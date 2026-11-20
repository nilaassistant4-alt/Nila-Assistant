# Nila — System Architecture & Configuration Specification

**A personalized, context-aware AI agent with strict identity boundaries and
high-fidelity long-term memory, shipped as a portable ZIP runtime.**

| | |
|---|---|
| **Agent name** | Nila |
| **Status** | MVP implemented and tested — **118** automated tests passing, **11,833** LOC Python |
| **Deployment model** | Local-first, single machine. Cloud is *backup + updates only* |
| **Mandatory dependencies** | **Zero.** Python 3.10+ stdlib. `numpy`/`soundfile` optional |
| **Identity** | Voiceprint + second factor + liveness challenge-response |
| **Data at rest** | AES-class envelope encryption; blind indexes; no plaintext content |

> Every number, path, constant and protocol in this document is taken from the
> code in this repository, not from a design intention. Where the
> implementation makes a trade-off, the trade-off is named.

---

## 0. Design invariants

These five invariants drive every decision below. When a future change would
violate one, it is rejected regardless of how convenient it is.

1. **The cloud never sees plaintext.** Not content, not titles, not tags, not
   record ids, not your vocabulary. The server stores opaque HMAC identifiers
   and re-wrapped ciphertext it cannot read.
2. **A locked brain leaks nothing actionable.** Metadata endpoints stay up
   (tool list, audit tail) but no memory content is reachable without a session.
3. **Nothing is silently lost.** Concurrent edits converge to one canonical
   record *and* preserve the loser as a retrievable conflict copy.
4. **Updates cannot reset state.** Code, models and prompts update in place;
   `$BRAIN_HOME` is never touched by the updater. Rollback is one command.
5. **The agent cannot invent capabilities.** No fabricated tool names, no
   fabricated URLs, no literal secrets. Every external action is pre-declared.

---

# 1. Modular ZIP Runtime Architecture & Sync

## 1.1 Package layout

The ZIP is the *program*. `$BRAIN_HOME` is the *state*. They never mix — this
separation is what makes continuous updates possible without data loss.

```
personal-brain.zip
│
├── brain.py                       # executable entry point (chmod +x)
├── brain/
│   ├── __main__.py                # `python -m brain` → cli.main.main
│   │
│   ├── crypto/                    ── ISOLATION: nothing above may hand-roll crypto
│   │   ├── envelope.py            #    PBX1 container, suite 1 (HMAC-SHA256-CTR)
│   │   │                          #    and suite 2 (AES-256-GCM, optional dep)
│   │   └── hash.py                #    HKDF, scrypt, mac, constant_time_eq,
│   │                              #    ed25519 verify (hmac-sha256 fallback)
│   │
│   ├── memory/                    ── ISOLATION: the only code that touches keys
│   │   ├── vault.py               #    key hierarchy, sessions, device enrolment,
│   │   │                          #    bundles, rotation, hash-chained audit log
│   │   └── store.py               #    SQLite schema, blind index, hybrid retrieval
│   │
│   ├── security/                  ── ISOLATION: policy, separate from primitives
│   │   ├── permission.py          #    capability broker: 7 capabilities, one-shot
│   │   │                          #    and durable grants, never-readable list
│   │   ├── redact.py              #    27-pattern secret detector, runs on every write
│   │   └── classify.py            #    data classes + tier/egress gates
│   │
│   ├── identity/                  ── ISOLATION: authentication only, never authorisation
│   │   ├── voice.py               #    MFCC backend + calibrated scorer
│   │   ├── voice_model.json       #    shipped dual-space calibration (EER 0.18%)
│   │   ├── liveness.py            #    challenge-response, nonce + replay guard
│   │   ├── totp.py                #    RFC 6238 second factor
│   │   └── ceremony.py            #    orchestrates gates G1–G4
│   │
│   ├── cloud/
│   │   └── protocol.py            #    SyncEngine, LocalBlobStore, HttpBlobStore
│   │
│   ├── updates/                   ── ISOLATION: may write code, may NOT write $BRAIN_HOME
│   │   ├── manifest.py            #    schema-1 manifest, canonical(), signature check
│   │   ├── channels.py            #    channel registry + trust modes
│   │   ├── installer.py           #    stage → verify → atomic swap → prune
│   │   └── manager.py             #    check / download / apply / rollback
│   │
│   ├── agent/
│   │   ├── agent.py               #    Nila: governed context, tool loop, fallback
│   │   ├── tools.py               #    28 tools, risk tiers, capability gate, step-up
│   │   ├── host_tools.py          #    files, directories, photos, shell (all brokered)
│   │   ├── providers.py           #    OpenAI-compatible / Anthropic / Ollama / local
│   │   ├── local_brain.py         #    zero-dependency offline engine
│   │   ├── integrations.py        #    sandboxed plugin gateway (allow-lists)
│   │   ├── connectors.py          #    #3: on/off, sealed keys, usage, test call
│   │   └── repl.py                #    interactive text session
│   │
│   ├── api/gateway.py             #    localhost HTTP surface (127.0.0.1 only)
│   ├── cli/main.py                #    32 subcommands
│   ├── util/{misc,audio}.py       #    hashing, paths, WAV I/O, private writes
│   └── config.py                  #    layered config resolution
│
├── cloud/                         ── reference server, NOT required to run Nila
│   ├── sync_server/app.py         #    stdlib E2EE blob store (If-Match CAS)
│   ├── update_server/app.py       #    signed release distribution
│   ├── docker-compose.yml
│   └── Dockerfile
│
├── scripts/
│   ├── make_release.py            #    publisher: build + sign a release
│   ├── calibrate_voice_model.py   #    retrain voice_model.json on a real corpus
│   └── synth_voice.py             #    deterministic test speakers (no mic needed)
│
├── tests/                         #    68 tests: crypto, memory, voice, sync
├── config/                        #    shipped defaults + integrations.example.json
└── docs/                          #    this file, THREAT_MODEL, ADRs
```

### Dependency direction (strictly acyclic)

```
cli ──┬──► api ──► agent ──► integrations
      │                │
      └────────────────┼──► memory.store ──► memory.vault ──► crypto
                       ├──► cloud.protocol ─────────────────►┘
                       ├──► identity ─────────────────────────►┘
                       └──► updates ──────────────────────────►┘
```

`crypto` imports nothing from Nila. `memory` imports only `crypto` and `util`.
`identity` may call into `memory.vault` (to store a template) but never the
reverse. `updates` is deliberately unable to import `memory.store` — the updater
has no path to your data, which is invariant 4 enforced by structure rather than
by discipline.

## 1.2 Runtime state (`$BRAIN_HOME`, default `~/.personal-brain/`)

| File | Contents | Plaintext? |
|---|---|---|
| `config.json` | Layered settings, device id, provider choice, `agent.storage_dir` | Config only — no personal data |
| `moved-to.json` | One pointer, written only after a byte-verified copy when the data folder moves to another drive (`move-to-d.bat`, `move-memories-to-d.bat`). `Config.load` follows it, so a moved vault opens from the same `BRAIN_HOME`; if the drive is not connected the old folder stays in use and `moved_to_missing` names it. Paths only, never data | Paths only |
| `identity.json` | Device registry, KEK wraps, encrypted identity blob, verifier, **policy**, lockout counters | Policy + counters are clear (must be enforceable while locked); everything else ciphertext |
| `memory.db` | SQLite: `records`, `records_fts` (blind), `vectors`, `sync_journal`, `sync_state`, `meta` | Ids, kinds, timestamps, blind tokens. **No content, no tags** |
| `audit.jsonl` | Hash-chained event journal | Event names + metadata keys, never values |
| `integrations.json` | Plugin registry (optional) | Hosts and operations; secrets are `$env`/`$vault` references only |
| `approvals.json` | One-time owner approvals (action name, session id, expiry, use count) | No secrets and no personal data; readable only by the same user |
| `updates/` | `channels.json`, `installed/<version>/`, `current`, `previous`, `history.jsonl` | Code artifacts |
| `backups/` | Encrypted exports | Ciphertext + integrity tag |

`memory.db` was verified against a 23-token probe set (names, card numbers,
SSIDs, tag words, medical terms): **zero leaks**. See `tests/test_memory.py::
test_ciphertext_at_rest` and `tests/test_sync.py::test_cloud_never_sees_plaintext`.

## 1.3 Continuous sync — state-preserving by construction

### Record representation on the wire

```
remote_id  = b64url(HMAC(Ko, local_id))            # opaque, unlinkable
blob       = {
  oid      : remote_id,
  body     : b64url(Encrypt(Ke, record_json, aad=oid)),   # re-wrapped per push
  hmac     : b64url(HMAC(Ki, oid | v | body)),            # binds version to content
  v        : record version,
  ts       : updated_at (ms),
  tomb     : 0 | 1,
  client   : originating device id,
  version  : store-assigned CAS version,
  server_ts: server receive time
}
```

`Ko`, `Ke`, `Ki` are HKDF subkeys of the master key under distinct domains
(`pbx/sync/{opaque-id,envelope,integrity}/v1`). Because records are **re-wrapped
on every push**, per-record keys never leave the device, and the server cannot
correlate a record across versions by anything except the opaque id it was given.

### The six phases

| # | Phase | Purpose |
|---|---|---|
| 1 | Blob-missing create | Push local records with no remote counterpart, `expect_version=0` ("must not exist") |
| 2 | Authenticate | `_unwrap(blob)` — verify HMAC under `Ki`, then decrypt under `Ke` with `aad=oid` |
| 3 | Local-dirty reconcile | `same_data` → no-op · `fast-forward` → adopt remote · **conflict** → deterministic LWW + preserve loser |
| 4 | Local-clean pull | `remote_v > base_remote` → apply |
| 5 | Publish new | Records created *during* this sync (conflict copies included) |
| 6 | Remote-only bootstrap | `list_keys("")` → adopt anything unknown. Lets a **brand-new device** populate from an empty vault with no prior id knowledge |

### Conflict resolution: deterministic LWW with loss preservation

```
winner  = greater updated_at
          tie → lexicographically greater origin device id
loser   → stashed as "<canonical_id>-conflict-<loser.updated_at>"
          tags   += ["conflict"]
          title   = "[conflict] " + title
          meta    = {conflict_of, conflict_reason: "concurrent-edit",
                     resolved_by: "lww+preserve"}
```

The conflict id derives from the **loser's** timestamp, so every device
independently generates the *same* conflict record and the fleet converges to an
identical canonical + conflict set. An earlier design used "local always wins,
stash remote" — it ping-ponged forever and was rejected.

Because sync is *eventually* consistent, a device only learns about records
pushed after its own last sync. Convergence takes ~3 alternating rounds, then
the system reaches a fixed point where every report is all-zero.
`tests/test_sync.py::sync_to_fixed_point` encodes this as the real invariant.

### Verified sync behaviour

| Property | Result |
|---|---|
| Two devices share one master key via bundle import | ✓ |
| Cloud stores only the 9 declared blob fields | ✓ |
| Opaque ids, not local ids, name the files | ✓ |
| Concurrent edit converges; both sides hold identical conflict copies | ✓ |
| Idempotent — 3 further rounds are completely inert | ✓ |
| Tombstones propagate | ✓ |
| 8 offline records from both devices all arrive on both | ✓ |
| Foreign vault (different passphrase) pulls **nothing** | ✓ `pulled: 0`, reported as `refused` |
| Malicious server advancing `version` with corrupted content | ✓ refused, not applied, sync continues |
| HTTP round-trip against `cloud/sync_server` | ✓ `pushed:2 pulled:0 ms:20` |

A refusal and an error are now **distinct report fields**: another vault's data
in a shared bucket is expected (`refused`), our own data failing integrity is
an incident (`errors`).

### Transport backends

| Backend | Use case |
|---|---|
| `LocalBlobStore(dir)` | Tests, air-gapped sync over Syncthing/rsync/NAS/S3-mount |
| `HttpBlobStore(url, token)` | The reference server, or any store speaking `GET/PUT/DELETE /v1/blobs/<key>` with `If-Match` CAS |

The server enforces compare-and-swap: `If-Match: 0` on an existing key returns
**412**, which is how two devices racing to create the same record are detected
rather than silently overwriting each other.

## 1.4 Continuous updates without resetting state

```
manifest.json  ──►  canonical() = sorted-keys JSON, signature field removed
                    │
   ┌────────────────┴─────────────────────────────────────┐
   │ 1. schema + channel pin      2. signature            │
   │    strict │ tofu │ insecure   ed25519, hmac fallback  │
   │ 3. min_client_version        4. per-artifact sha256 + size │
   └────────────────┬─────────────────────────────────────┘
                    ▼
   stage → hash-verify ALL artifacts (wipe staging on any failure)
        → unpack to updates/installed/<version>/
             (zip-slip and symlink entries refused)
        → optional import test
        → atomic swap: current.tmp → current, old current → previous
        → append history.jsonl, prune keeping 3
```

**Why state survives:** artifacts land in `updates/installed/<version>/`. The
updater has no import path to `memory.store` and no write access to
`$BRAIN_HOME/{memory.db,identity.json,audit.jsonl}`. Only three artifact types
hot-apply without a restart — `voice_model`, `prompt`, `config` (merged, never
clobbered), `integration` — because they are data, not code. Everything else sets
`requires_restart: true` rather than mutating a running process.

**Trust modes:** `strict` (pinned public key required), `tofu` (first manifest
pins the key — verify its fingerprint out of band), `insecure` (explicit, warned,
for a LAN you control end to end). Verified: TOFU pinned
`alg=hmac-sha256 key_id=ddf7ff6cf768df38`; 0.2.0 applied with
`hot_applied=['voice_model','prompt','config']`; **all three tamper variants
rejected**; 0.2.1 applied; `rollback` restored 0.2.0.

---

# 2. Identity & Access Control Policies

## 2.1 PII data schema

Nila has exactly **eight content classes**. Everything the user says is mapped
onto one of them at write time; the class drives retention, risk tier and
retrieval behaviour.

```jsonc
// Logical record. Only `id`, `kind`, timestamps and blind tokens exist in the clear.
{
  "id":         "cre_Bumj7TLEBok_Eoep",   // prefix from kind[:3]; plaintext lookup key
  "kind":       "credential_ref",         // one of the 8 classes below
  "title":      "HDFC netbanking",        // SEALED
  "body":       "user 8821xxx, pass in KeePass vault 'bank'",  // SEALED
  "tags":       ["finance", "sensitive"], // SEALED (blind index kept for filtering)
  "meta":       { "source": "chat", "confidence": 0.9 },       // SEALED
  "created_at": 1792485080788,            // clear
  "updated_at": 1792485080788,            // clear — LWW clock
  "version":    1,                        // clear
  "deleted":    false,                    // clear (tombstone)
  "score":      0.7268                    // transient, set by retrieval only
}
```

| Class | Auto-detected by | Tags added | Sensitivity | Special handling |
|---|---|---|---|---|
| `note` | default | `inbox` | medium | — |
| `preference` | prefers / likes / hates / favorite / allergy | `preference` | low | Fed into context first; shapes tone |
| `task` | todo / reminder / follow up / deadline | `task` | low | Listed by `list tasks` |
| `contact` | Dr. / doctor / manager / amma / appa / friend | `people` | **high** | Name + relationship are PII |
| `event` | meeting / appointment / flight / birthday | `calendar` | medium | Time-normalised on write |
| `decision` | decided / chose / agreed | `decision` | medium | Immutable-by-convention; edit creates a new version |
| `credential_ref` | password / pin / otp / secret / token | `sensitive` | **critical** | Reference only — see rule P-3 |
| `health` | (tag) health / doctor / medicine / diet | `health` | **critical** | Highest tier; never leaves the device |
| `instruction` | "from now on…", "always…", "call me…" — **owner routes only** | `instruction` | medium | A standing RULE, not a memory: injected into every local turn (never an Online one), capped at 20 rules / 280 chars, and refused if it tries to disable permissions, the audit trail, the vault's factors or egress. Only `brain instruct`, the dashboard panel and `/v1/instructions` may create one — the `remember` tool cannot. |

### Codified PII rules

| # | Rule | Enforcement point |
|---|---|---|
| **P-1** | Content, title, tags and metadata are sealed with a **per-record key** `HKDF(MK, "rec", record_id)` | `store._enc` / `seal` |
| **P-2** | Record **ids, kinds and timestamps are plaintext** by design — they are lookup keys and the LWW clock. They carry no content information. This is documented, not accidental | `docs/adr/0002` |
| **P-3** | `credential_ref` stores a **reference**, never the secret. Nila records *where* a password lives ("in KeePass vault 'bank'"), not the password | System prompt rule 4 + tool docs |
| **P-4** | Keywords are indexed as **blind tokens** `HMAC(Kidx(record_id), token)`. No global vocabulary exists, so no cross-record frequency analysis is possible | `store._blind` |
| **P-5** | Tags are **sealed**; only a blinded `tag_index` supports filtering. A stolen DB must not reveal that you keep `health` records | `store.blind_tag_index` |
| **P-6** | The audit journal records **event names and metadata key names**, never values | `vault.audit` |
| **P-7** | Integration calls log secrets as `sha256` fingerprints with the key names redacted | `integrations.py` |
| **P-8** | Voice templates are stored inside the encrypted identity blob and are **unreadable while locked** | `vault.load_voice_template` |
| **P-9** | A voice template carries its backend name; loading a template produced by a different backend raises rather than silently scoring garbage | `ceremony.voice.templates()` |
| **P-10** | The agent's system prompt forbids revealing the passphrase, keys, tokens or the audit chain | `SYSTEM_PREAMBLE` rule 7 |

## 2.2 The identity boundary

**One brain, one human, many devices.** There is no multi-user model, and this
is deliberate: a shared master key would make per-user access control
meaningless. Sharing happens by exporting a *record*, not by admitting a person.

```
passphrase ──scrypt(n=2^15,r=8,p=1)──► KEK_device   (never stored anywhere)
                                          │
                                          ▼ unwrap
                                       MK (32 B, random, shared across devices)
                                          │
                    ┌─────────────┬───────┴──────┬──────────────┬────────────┐
                    ▼             ▼              ▼              ▼            ▼
              HKDF "rec"    HKDF "sync"     HKDF "audit"  HKDF "sess"   verifier
              per-record    → Ko, Ke, Ki    chain key     session tags  HMAC(MK,
              content keys                                            SESSION_TAG)
```

| Component | Property |
|---|---|
| **KEK** | Derived per device from the passphrase. Never written to disk, never synced |
| **MK** | Random 32 B, generated once at `init`. Wrapped by each device's KEK in `devices[id].kek_wrap` |
| **Subkeys** | HKDF-SHA256 under distinct domain strings; no subkey can be used to recover another |
| **Verifier** | `HMAC(MK, b"pbx/session-tag/v1")` — proves a passphrase produced the right MK *without* storing MK |
| **Session** | `{session_id, mk, mode, degraded, started_at, expires_at, absolute_expires_at}`; 2 h idle timeout, 12 h absolute maximum, `touch()` on user activity |

### Enrolment bundle (`PBBUNDLE1`, ~1,162 bytes)

```
{ magic, version, user, kdf, kek_wrap, identity_blob, verifier,
  policy, label, exported_from, exported_at, bundle_mac }
```

Ciphertext-only, so it can travel by cloud, QR code or USB. The receiving device
derives its **own** KEK from the passphrase, unwraps MK, then:

1. Verifies `HMAC(MK, SESSION_TAG) == verifier` — proves the passphrase was right.
2. Recomputes `bundle_mac` over the canonical form — proves nothing was tampered with in transit.
3. Test-decrypts `identity_blob` — proves the key actually works.
4. Registers a **fresh** device id `dev-<HMAC(MK, label)[:12]>` with its own `kek_wrap`.

Three distinct failure modes are separately reported: `incorrect passphrase for
this bundle`, `bundle verifier mismatch`, `bundle integrity check failed
(tampered in transit)`.

### Device lifecycle

`device list` · `device export` (produce a bundle) · `device import [--force]`
(works on a fresh machine — it does **not** require an existing vault) ·
`device revoke` (refuses to revoke the device you are on) · `device rotate-key`
(re-wraps MK under a new passphrase across all devices).

## 2.3 Layered voiceprint authentication

Four gates, evaluated in order. Failing any gate ends the attempt; the
provisional key material is discarded, never retained.

```
┌─ G1  LIVENESS ─────────────────────────────────────────────────────────┐
│  issue: 6-word numeric phrase + nonce + bound=HMAC(Ksess, nonce|phrase) │
│  check:  duration ≥ min_duration · energy present · spectral variety    │
│          · nonce not previously consumed · audio fingerprint unseen     │
│          · bound matches (tamper-evident)                               │
│  fails:  expired │ replay │ nonce reuse │ binding │ too short │ silence │
└─────────────────────────────┬───────────────────────────────────────────┘
                              ▼
┌─ G2  SPEAKER VERIFICATION ──────────────────────────────────────────────┐
│  embed candidate utterance → 86-dim feature vector                      │
│  score = 0.75·blend(z-space) + 0.25·blend(w-space)                      │
│  blend = 0.75·cos(candidate, centroid) + 0.25·max cos(candidate, tmpl)  │
│  accept iff score ≥ threshold (0.6199 as shipped)                       │
└─────────────────────────────┬───────────────────────────────────────────┘
                              ▼
┌─ G3  KNOWLEDGE FACTOR ──────────────────────────────────────────────────┐
│  passphrase → KEK → unwrap MK → verify SESSION_TAG                      │
│  (or TOTP RFC 6238, or a single-use recovery code — both DEGRADED)      │
└─────────────────────────────┬───────────────────────────────────────────┘
                              ▼
┌─ G4  SESSION GRANT ─────────────────────────────────────────────────────┐
│  start_session(mk, mode, degraded) → 2 h idle / 12 h absolute maximum   │
│  audit: auth_success / auth_failure with reason                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### The shipped voice model

| Property | Value |
|---|---|
| Backend | `mfcc-cal-v1` (MFCC 1–19 mean+std, Δ-MFCC, log-F0 percentiles, voiced ratio, centroid, rolloff, energy DR, duration, ZCR std) |
| Raw dim → kept | 86 → 40 |
| Whitening | **Shrunk**: `1/sqrt(max(λ_c, 0.1·λ_max))`, ridge 1e-6 |
| Dual-space blend | `alpha = 0.75` |
| Threshold | **0.6199** |
| Explained variance | 0.974 |
| Genuine mean / min | 0.8618 / 0.6285 |
| Impostor mean / max | 0.0257 / 0.7574 (n = 1656) |
| **EER** | **0.18%** |
| `secure` preset | 0.5742 → FAR 1.03%, FRR 0% |
| `convenient` preset | 0.4425 → FAR 5.01% |

> **Honest limitation, stated in the model file itself:** these figures come
> from *synthetic* speakers generated by `scripts/synth_voice.py` (36 dev + 24
> eval × 6 utterances, 3 for enrolment). They prove the pipeline, the
> calibration method and the relative separation. **They are not a real-world
> EER claim.** Before trusting this with real voices, run
> `scripts/calibrate_voice_model.py` against a genuine corpus.

Two earlier approaches were tried and **rejected**: plain L2-normalised
MFCC mean/std cosine scored 0.959–0.987 *across different speakers* (useless),
and full PCA whitening on a rank-deficient covariance amplified ridge noise
~1000×, collapsing impostor scores. Shrunk whitening is the fix.

### Lockout and degraded modes

```
attempts.count++ on failure → wait = lockout_base_seconds · 2^(count-1)
                              ("retry in 29s" observed at the boundary)
register_success() → counters reset
```

| Mode | Factors | `degraded` | Permitted when |
|---|---|---|---|
| `passphrase+voice` | G1+G2+G3 | false | Default |
| `totp` | passphrase + TOTP | **true** | Voice unavailable (hoarse, noisy room) |
| `recovery` | passphrase + single-use code | **true** | Lost voice enrolment |
| `passphrase` only | G3 | **true** | Only if `identity.allow_degraded=true` |

With voice enrolled, passphrase-only unlock is **refused by default**:
`second factor required by policy.require_voice=true`. The override exists and
is explicit, because the person who needs it is the person who is locked out.

Policy lives in the **clear** in `identity.json`, not in the encrypted blob.
This is required: rate limiting and the second-factor requirement are precisely
the controls that must run *before* authentication succeeds. It holds only
booleans and integers, so it discloses nothing personal.

## 2.4 Multi-level access gates

| Tier | Name | Requirement | Reachable surface |
|---|---|---|---|
| **T0** | Public | none | `/health`, `brain --version`, `brain doctor` |
| **T1** | Metadata | bearer token | `/v1/status`, `/v1/tools`, `/v1/audit` (tail), `brain challenge` |
| **T2** | Session | unlocked (voice **or** degraded) | `recall`, `remember`, `list`, `search`, `chat`, `/v1/memory*`, `/v1/chat`, `run_sync` *(medium)* |
| **T3** | Confirmed | T2 + explicit user confirmation in-turn | `forget` *(high)*, `call_integration` *(high)* |
| **T4** | Step-up | T3 + **fresh re-authentication** (`session.step_up_ok`) | `export_backup` *(high, step_up=True)* |

```python
if spec.step_up and not getattr(ctx.session, "step_up_ok", False):
    raise PermissionError(...)          # tools.py:101
ctx.audit(f"tool:{name}", {"risk": spec.risk, "args_keys": sorted(args)})
```

Note what the audit line records: the **risk tier** and the sorted **names** of
the arguments. Never their values (rule P-6).

Risk distribution across the 15 tools: 10 `low`, 2 `medium` (`update_memory`,
`run_sync`), 3 `high` (`forget`, `call_integration`, `export_backup`), of which
1 requires step-up.

The API gateway additionally refuses to bind to any **non-loopback** address,
because it serves plain HTTP — the token is a bearer credential and must not
cross a network interface. `serve()` prints a red refusal rather than starting.

---

# 3. Memory Model & Tool Integration

## 3.1 Hybrid retrieval

```
                    query
                      │
        ┌─────────────┴──────────────┐
        ▼                            ▼
  LEXICAL (0.45)               SEMANTIC (0.55)
  fold → blind → match         HashingEmbedder → 512-d → cosine
  hits/|query_terms|           absolute, NOT renormalised
  min-max within hit set              │
        │                             │
        └─────────────┬───────────────┘
                      ▼
        positional damping  1/(1 + i·0.05)
                      ▼
        min_relevance floor  0.12   ← drops SEMANTIC-ONLY noise; records
                                      with proven token overlap are kept
                      ▼
                  ranked records
```

### Why the floor matters

Before it existed, `"who is my heart doctor"` returned the **wifi** record
first: a `"who"*` prefix coincidence against `"wifi"` was normalised to a
perfect 1.0. Two changes fixed it:

1. **Cosine is used absolutely.** It is already bounded in `[-1, 1]` and
   meaningful. Renormalising by the best candidate turns pure noise into a
   confident-looking 1.0 exactly when nothing matches.
2. **Lexical hits must prove genuine token overlap** with the document.
3. **The floor exempts records that proved lexical overlap.** It gates
   *semantic-only* candidates, which is where pure noise lives — a 0.02 cosine
   to everything in the store. A record sharing a real folded token with the
   query has already demonstrated relevance, so it is returned regardless of
   score, and the agent is separately told when the score is weak
   (`< 0.25` → "treat it as a hint, not a fact").

Without rule 3, `"who is my heart doctor"` was silently dropped: it shares the
token `doctor` with a record saying "Dr Meera", but scored 0.118 because the
lexical embedder has no concept of cardiology — Nila answered *"no memory
matched confidently"* about something she did know. Regression test:
`tests/test_memory.py::test_lexical_overlap_survives_the_relevance_floor`.

Result: a brain that says *"no memory matched confidently"* only when nothing
genuinely matches. Measured over **13 ranking cases — 13 correct**: 0.45–0.73
for true matches; empty result set for `zebraquartz nonexistent topic`,
`quantum baking recipes`, `kubernetes deployment yaml`, `passport expiry` (when
no passport is stored).

### Suffix folding (and why it is ours)

SQLite's FTS5 `porter` tokenizer is **not compiled into every build** — verified
in this environment: `"running"*` and `"dr"*` matched nothing while `"dr"`
matched exactly. Nila therefore folds tokens itself, at index *and* query time:

```python
_SUFFIXES = ("ization","isation","ations","ingly","edly","ings","ies","ing",
             "ment","ness","ers","er","ed","ly","es","s")
# conservative: never folds below 3 characters; "appli" → "apply"
```

`doctors` now finds `doctor`; `meetings` finds `meeting`.

Folding also expands a table of **common abbreviations** to their canonical
form, which matters because the blind index supports equality only:

```python
ABBREVIATIONS = {"dr": "doctor", "pwd": "password", "appt": "appointment",
                 "meds": "medicine", "acct": "account", "bday": "birthday", ...}
```

So `"dr meera"` and `"doctor meera"` retrieve the same record, and
`"netbanking pwd"` finds a note that says "password". Nothing is folded below
three characters, so short real words survive intact.

### The blind index — search without disclosure

```
token  ──fold──►  HMAC( fast_kdf(MK, "pbx/blind-index/v1|" + record_id), token )
                  ──► base64[:24]
```

Indexed forms per token: the raw fold **and** its folded variant, so singular
and plural queries both hit. Consequences, stated plainly:

| Gain | Cost |
|---|---|
| No plaintext vocabulary on disk | No prefix matching (`"pass"*` won't find `"passport"`) |
| No cross-record term frequency to mine | No stemming beyond `fold()` |
| Per-record keys → a leaked token reveals nothing about other records | Query is **O(records)**: each candidate needs its own per-record HMAC |

The O(n) query cost is the price of not maintaining a global blind vocabulary.
For a personal brain — thousands of records, not millions — it measures in
single-digit milliseconds. If that ever stops being true, the escape hatch is a
**global** blind index keyed by `HKDF(MK, "global-blind")`, which restores O(1)
lookup at the cost of leaking document frequency. That trade is not taken today.

### Embedder

`HashingEmbedder` (`hashing-tfidf-v1`): 512 dimensions, word uni/bigrams plus
character 3- and 4-grams, signed hashing, sublinear TF, L2-normalised.
**Deterministic, offline, no model download.** It captures lexical and
sub-word overlap — it does *not* know that "cardiologist" means "heart doctor".
That is an honest limitation of a zero-dependency MVP, and it is why the
semantic lane is weighted 0.55 rather than treated as ground truth.

The interface is pluggable: `SentenceTransformerBackend` (`all-MiniLM-L6-v2`)
drops in when the optional dependency is present, and `search_index_tokens`
supports `none │ tags │ keywords │ full` to trade index size against recall.

### Consistency guarantees

- **Per-record sealing** — `HKDF(MK, "rec", record_id)`, so one record's key
  cannot decrypt another.
- **Undecryptable rows never crash.** A record sealed under a different master
  key (e.g. restored from another device) surfaces as `meta.locked = true` and
  can still be *synced*, just not read.
- **Thread safety** — `sqlite3.connect(..., check_same_thread=False)` plus an
  `RLock` around all 11 public methods, because the API gateway is a
  `ThreadingHTTPServer`. Verified: 6 threads × 5 records, no errors.
- **Tombstones** — soft delete clears title/body/tags and bumps version so the
  deletion propagates; `purge_tombstones(older_than_ms)` reclaims later.
- **Backups** — `export_encrypted_backup()` emits DB bytes (already ciphertext)
  with a JSON header (`schema`, `sha256`, `bytes`, `at`, `embedder`) and an
  integrity tag derived from MK.

## 3.2 Tool integration & sandboxed plugins

### The 20 built-in tools

See **`docs/PERMISSIONS.md`** for the permission model that gates the five host
tools — the capability ledger, one-shot affirmation, the never-readable list,
and the two deny-by-default egress gates.

| Tool | Risk | Tier | Notes |
|---|---|---|---|
| `remember` | low | T2 | Auto-classifies kind + tags |
| `recall` | low | T2 | Hybrid search |
| `list_memories` | low | T2 | Filter by kind/tag |
| `memory_stats` | low | T2 | Counts only |
| `session_status` | low | T1/T2 | Mode, degraded, expiry |
| `current_time` | low | T2 | |
| `calculate` | low | T2 | No `eval` of arbitrary code |
| `list_integrations` | low | T2 | Names + operations, never secrets |
| `sync_status` | low | T2 | |
| `check_updates` | low | T2 | Read-only |
| `update_memory` | **medium** | T2 | Mutates content |
| `run_sync` | **medium** | T2 | Network egress |
| `forget` | **high** | T3 | Destructive; confirmation required |
| `call_integration` | **high** | T3 | Network egress to a declared host |
| `export_backup` | **high + step-up** | T4 | Fresh re-authentication + `data.export` (never durable) |
| `read_file` | medium | **affirm** | `fs.read`; 512 KB cap; never-readable list applies |
| `list_directory` | low | **affirm** | `fs.list`; names and sizes only, never contents |
| `view_photo` | **high** | **affirm** | `media.photo`; metadata only, pixels never returned |
| `search_photos` | **high** | **affirm** | `media.photo`; paths and metadata only |
| `run_command` | **high** | **affirm** | `shell.exec`; 20 s timeout; **never** grantable standing |

### Plugin gateway: seven enforced controls

The model cannot reach the network except through this gateway, and the gateway
only knows about what *you* declared.

| # | Control | Effect |
|---|---|---|
| 1 | **Declared operations only** | The model may call `name.operation` pairs present in `integrations.json`. It cannot invent a URL, a path or an integration |
| 2 | **Secrets by reference** | `{"$env": "VAR"}` or `{"$vault": "key-name"}`. A `$literal` secret is **rejected at load and at call time** |
| 3 | **Host allow-list** | `allow_hosts` enforced per integration |
| 4 | **TLS by default** | Plain `http` permitted **only** for loopback |
| 5 | **Method allow-list** | Per operation |
| 6 | **Rate bucket** | `rate_limit_per_minute`, per integration |
| 7 | **Response cap + timeout** | 2 MB body cap, per-integration timeout |

Plus: `{{param.NAME}}` templating restricted to declared placeholders,
`dry_run` mode, and audit entries carrying **redacted key names + sha256 of
secret values** (rule P-7). All seven controls were verified individually.

### Connecting free backends

**A local model (free, private, no egress):**

```bash
brain config set agent.provider ollama
brain config set agent.model llama3.1:8b
brain config set agent.base_url http://127.0.0.1:11434
```

**A keyless weather API** — Open-Meteo needs no account and no secret, so the
sandbox config is minimal and control 2 is trivially satisfied:

```jsonc
{
  "name": "weather",
  "description": "Open-Meteo — no API key required.",
  "base_url": "https://api.open-meteo.com",
  "allow_hosts": ["api.open-meteo.com"],
  "enabled": true,
  "timeout": 10,
  "rate_limit_per_minute": 20,
  "auth": { "type": "none" },
  "operations": [
    {
      "name": "current",
      "method": "GET",
      "path": "/v1/forecast",
      "description": "current conditions for a lat/lon",
      "params": {
        "query": {
          "latitude":  "{{param.lat}}",
          "longitude": "{{param.lon}}",
          "current":   "temperature_2m,relative_humidity_2m,wind_speed_10m"
        }
      }
    }
  ]
}
```

Nila can now answer *"how hot is it in Chennai?"* by calling
`weather.current` — and it can only ever call that, against that host, at that
rate, with those parameters.

### Provider layer

| Provider | Cost | Egress | Notes |
|---|---|---|---|
| `local` (`LocalBrain`) | free | **none** | Intent patterns + tools. Always available; no model download |
| `ollama` | free | none (loopback) | Real free-form conversation on your own hardware |
| `openai_compatible` | paid/free-tier | yes | Any endpoint speaking the OpenAI schema |
| `anthropic` | paid | yes | |

`tools_mode ∈ auto │ native │ prompt`. On any provider failure, Nila falls back
to `LocalBrain` and tags the turn `local(fallback)` — a degraded answer that is
honest about being degraded beats a silent failure.

**Tool loop bounds:** `MAX_TOOL_ROUNDS = 5`, at most 4 calls per round, 6000-char
cap on any single tool result. Context assembly takes the top 6 memories plus
session mode plus vault stats.

Local-provider detection uses `getattr(provider, "needs_network", True) is False`
rather than object identity — an earlier `provider is self.local` check silently
failed because `build_provider()` returns a *second* `LocalBrain` instance, so
tool results were never rendered offline.

---

## 4. Verification status

| Suite | Tests | Result |
|---|---|---|
| `test_crypto.py` | 11 | pass — envelope roundtrip, tamper detection, AAD binding, HKDF separation |
| `test_memory.py` | 21 | pass — at-rest ciphertext, blind index, folding, abbreviations, relevance floor **and its exemption**, ordering |
| `test_voice.py` | 27 | pass — calibration, text independence, replay/nonce/binding/silence, ceremony, lockout, TOTP |
| `test_sync.py` | 12 | pass — convergence, opacity, no-plaintext, foreign vault, malicious server |
| `test_permission_egress.py` | 47 | pass — default-deny, one-shot consumption, never-durable capabilities, SSH keys unreachable, photos metadata-only, classification matrix, egress gates, tier filtering, cloud-prompt leakage |
| **Total** | **118** | **passing** |

End-to-end smoke runs additionally verified: full CLI lifecycle in a temp home,
offline agent answering with tools, HTTP sync against `cloud/sync_server`,
second-device bootstrap from an 1162-byte bundle, and a 17-check localhost API
run including genuine voice authentication over HTTP (score 0.7914) with
impostor rejection (0.1102 vs threshold 0.6199) and replay detection.

## 5. Known limitations

> Two gaps from the original analysis are now **closed**: retrieval is filtered
> by session tier, and restricted memories never reach a network-connected
> model. See `docs/PERMISSIONS.md` §3. The remaining limitations are below.

Named deliberately, because an architecture document that hides its gaps is
worse than no document.

1. **Voice EER is measured on synthetic speakers.** Not a real-world claim.
   Recalibrate before trusting real voices.
2. **The default embedder is lexical, not conceptual.** "heart doctor" will not
   find "cardiologist". Plug in `SentenceTransformerBackend` to close this.
3. **Blind-index queries are O(records).** Fine at personal scale; see §3.1 for
   the escape hatch and what it costs.
4. **No prefix keyword search.** A direct consequence of blinding (P-4).
5. **The API gateway serves plain HTTP.** Loopback-only by construction, but
   anything on the host can reach it. A UNIX-socket transport with peer-cred
   checks is the right next step.
6. **Package updates require a restart.** Only data-like artifacts hot-apply.
   Runtime code replacement was deliberately not implemented.
7. **No multi-user model.** By design (§2.2).
8. **Global CLI flags must precede the subcommand** — `brain --quiet init`, not
   `brain init --quiet` (argparse subparsers shadow global dests).

## 6. Related documents

- **`docs/PERMISSIONS.md`** — capability ledger, host access, egress gates,
  advantages and disadvantages
- `docs/PII_SCHEMA.md` — data classes, retention and disclosure matrix
- `docs/THREAT_MODEL.md` — adversaries, capabilities, mitigations, residual risk
- `docs/adr/0001-zero-mandatory-dependencies.md`
- `docs/adr/0002-plaintext-ids-blind-content.md`
- `docs/adr/0003-deterministic-lww-conflict-copies.md`
- `docs/adr/0004-shrunk-whitening-voiceprint.md`
- `docs/adr/0005-no-runtime-code-replacement.md`
