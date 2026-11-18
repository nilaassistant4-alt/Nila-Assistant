# Nila — Threat Model

**Status:** describes the MVP as shipped, not the target design. Every control
named here exists in the tree and is exercised by `tests/`. Every gap named here
is real and currently unmitigated.

**Last reviewed:** 2026-10-20 · **Suite:** 207 passing · **Scope:** local-first
single machine, one honest user, cloud used for backup and updates only.

---

## 1. How to read this document

A threat model that only lists mitigations is marketing. This one is organised
around what an attacker *gets*, and the register in §5 marks each entry as
**mitigated**, **partial**, or **open**. §9 ranks the open and partial items by
the product of likelihood and consequence, and the top entry is a genuine
plaintext content leak that shipped and was not previously documented as such.

Three conventions:

- **"Same-user code execution" is treated as total compromise.** Any attacker who
  can run code as the vault owner while a session is unlocked owns the master
  key, because it is live in process memory. No software-only design changes
  that. Controls here are about *shrinking the window* and *raising the cost*,
  never about defeating that attacker.
- **Controls are described by where they are enforced,** because an unenforced
  policy is not a control.
- **Residual risk is stated even when a control works,** since most mitigations
  here are depth-limited rather than absolute.

---

## 2. Out of scope

Explicitly not defended against, by design and by dependency budget:

| Excluded | Why |
|---|---|
| Nation-state / targeted adversary with a zero-day in the OS or Python runtime | Out of reach for any single-user local tool |
| Malicious `pip` dependency at install time | The MVP has **zero mandatory third-party dependencies**; optional extras (`numpy`, `soundfile`) are the user's choice |
| Coerced disclosure of the passphrase | Duress codes are not implemented |
| Hardware attacks: cold boot, DMA, bus probing, evil-maid with physical implant | No hardware root of trust is wired (see G-5) |
| Compromise of the cloud model provider's own infrastructure | We control what we *send*, not what they do with it |
| Denial of service | Availability is not a security property here; the vault can always be rebuilt from backup |
| Side channels: timing beyond constant-time comparisons, power, EM, acoustic | Only `constant_time_eq` on verifiers and MACs is claimed |

---

## 3. Assets

Ordered by what losing them costs the user.

| # | Asset | Where | Sensitivity | Replaceable? |
|---|---|---|---|---|
| A1 | **Master key (MK)** | process memory while unlocked; wrapped by per-device KEK in `vault.bundle` | catastrophic | no — loses all history |
| A2 | **Passphrase** | never stored; scrypt → KEK | catastrophic | no |
| A3 | **Voice template** | inside encrypted `identity_blob`, **syncs to cloud** | high — biometric | **no** |
| A4 | **Memory records** (title, body, meta, tags) | sealed per-record in SQLite | high | from backup only |
| A5 | **Semantic vectors** | `vectors.vec`, **plaintext JSON** | high — see T1 | regenerable from A4 |
| A6 | **Credentials referenced in notes** | inside A4, escalated to `restricted` | high | no |
| A7 | **Recovery codes** | SHA-256 hashes inside `identity_blob`; plaintext shown once | high — bypasses 2FA | regenerable |
| A8 | **TOTP seed** | inside `identity_blob` | high | re-enrol |
| A9 | **Host files, photos, shell** | outside the vault, reached via broker | high | varies |
| A10 | **Audit ledger** | hash-chained JSONL | medium — integrity, not secrecy | no |
| A11 | **Permission ledger + panic latch** | `permissions.json`, `.PANIC`, **cleartext by design** | medium | re-derivable |
| A12 | **Signing key for updates** | `brain keygen` output, held by the releaser | high — code execution | rotate |

A11 is cleartext deliberately: `brain permission list` must work while the vault
is locked, otherwise the user cannot inspect or revoke access during an
incident. This is the correct trade and is called out because it looks like a
bug.

---

## 4. Trust boundaries

| ID | Boundary | Crosses | Enforced by |
|---|---|---|---|
| B1 | user ↔ agent | natural-language requests | session tier + broker; the model may **request** but never **grant** |
| B2 | agent ↔ host filesystem | reads, listings, photos, shell | `Broker.peek()` centrally (fails closed with no broker) + handler `request()` on the precise scope + never-read list |
| B3 | vault ↔ disk | sealed records | per-record `Enc(rec_subkey, aad=record_id)`; MK never touches disk |
| B4 | device ↔ cloud sync | encrypted blobs | E2EE: `remote_id = HMAC(Ko, local_id)`; body under `Ke`; integrity under `Ki`; server sees only opaque bytes |
| B5 | device ↔ update server | signed artifacts | channel pin → signature → `min_client` → per-artifact sha256+size, in that order |
| B6 | agent ↔ third-party API | HTTPS calls | declarative gateway: allowlisted operation, host, method; rate bucket; timeout; 2 MB cap |
| B7 | agent ↔ cloud LLM | prompt context | `may_egress()`: `restricted` never leaves, no override flag exists |
| B8 | process ↔ API client | localhost JSON | `serve()` refuses non-loopback bind; bearer token |
| B9 | plugin/tool ↔ prompt text | justification strings | `render_prompt()` — broker authors all wording; tool supplies scope and a truncated purpose only |

B7 and B9 are the two boundaries added most recently and are the ones most often
misunderstood. B7 has **no configuration flag** that permits restricted data to a
cloud provider — the absence of the flag is the control. B9 exists because a
requester that writes its own permission prompt can argue for its own approval.

---

## 5. Attacker profiles

| ID | Profile | Capability | Realistic? |
|---|---|---|---|
| P1 | **Opportunistic remote** | Phishing, credential stuffing, no device access | high |
| P2 | **Device thief / lost laptop** | Full disk, no passphrase, device powered off | high |
| P3 | **Household / shared device** | Local account, shoulder-surfing, guessable passphrase, may have heard the enrolment phrase | high |
| P4 | **Malware as same user** | Arbitrary code execution while logged in; may wait for an unlocked session | medium |
| P5 | **Malicious or compromised cloud sync server** | Sees all uploaded blobs, controls responses, can replay or reorder | medium |
| P6 | **Malicious update / dependency** | Code execution via the update channel or an optional pip extra | medium |
| P7 | **Malicious tool, integration, or prompt-injected model** | Runs inside the agent loop with tool access; controls text the user sees | medium-high |
| P8 | **Coercive legal / border authority** | Compels passphrase disclosure, seizes device | jurisdiction-dependent |
| P9 | **State-level** | See §2 — out of scope | low |

P7 is the profile the permission broker exists for, and it is the one most
likely to be underestimated: it does not require any breach, only that the user
install a plugin or paste untrusted text into a conversation.

---

## 6. Threat register

Status: ✅ mitigated · ⚠️ partial · ❌ open.

### 6.1 Offline disk access (P2, P3)

| ID | Threat | Status | Control / residual risk |
|---|---|---|---|
| T1 | **Semantic vectors leak content in plaintext** | ✅ **(closed 2026-10-20)** | `vectors.vec` stores `json.dumps([round(v,6) …])` — an unencrypted BLOB, and the default embedder is a **public** deterministic function (`sha256("emb1|"+token) % dim`, signed, log-compressed). An attacker needs no key to reimplement it. **Demonstrated:** against a 3-record vault, with the sealed bodies never opened, guessed phrases attributed to the right record at cos **0.901** ("therapy session anxiety sleep") and **0.684** ("dentist appointment tuesday morning"). Note which classes scored highest — health and medical, precisely the data `classify()` works hardest to keep local. A card number ranked first for its record but only at 0.394, since long digit strings tokenize poorly; the leak is strongest for natural-language content, which is most of a memory vault. Titles, kinds, timestamps, tags and sync state are also plaintext by design (ADR-0002). Highest-severity open item. |
| T2 | Record bodies, metadata, tags at rest | ✅ | Sealed per record with an HKDF subkey bound to `record_id` as AAD. Tags additionally blind-indexed via `HMAC(HKDF(mk,"pbx/blind-index/v1|"+rid), token)`; a 23-probe leak audit found no plaintext token or tag leakage. Residual: record *existence*, kind and timestamps are visible. |
| T3 | Offline passphrase cracking | ⚠️ | scrypt `n=2^15, r=8, p=1` (~32 MB) — memory-hard but **not Argon2id**, and weaker than the target spec. Residual: a short passphrase is crackable offline given the bundle; strength is entirely the user's choice and is not enforced. |
| T4 | Wrong-passphrase oracle | ✅ | Three distinct failure paths (bad verifier, bad unwrap, bad bundle MAC) but all produce refusal; `constant_time_eq` on every MAC comparison. Residual: the three messages differ, which is a minor information leak accepted for usability. |
| T5 | Recovery-code brute force | ⚠️ | Codes are ~52-bit (`xxxxx-xxxxx`, 36-char alphabet) hashed with **unsalted SHA-256**, no stretching. Contained only because the hashes live inside the passphrase-encrypted `identity_blob` — obtaining them already requires A2. Residual: if the blob is ever exposed, hashes fall to GPU attack quickly. Should be scrypt or argon2 per code. |
| T6 | Vault copy to another machine | ✅ | Per-device KEK wraps a shared MK; a bundle from a foreign vault yields `pulled: 0`. Residual: **single MK across all records** (G-3) — one unwrap exposes the entire history with no per-class compartmentalisation. |
| T7 | Tampering with sealed records | ✅ | Encrypt-then-MAC with AAD `domain|context`; any mutation fails the tag before decryption is attempted. |
| T8 | SQLite file read while locked | ✅ | Policy is deliberately readable while locked; everything else is ciphertext. `mk_verifier` permits offline passphrase checking but does not reveal MK. |

### 6.2 Authentication and session (P1, P3)

| ID | Threat | Status | Control / residual risk |
|---|---|---|---|
| T9 | Voice spoofing / replay | ⚠️ | G1 liveness challenge-response with a 90 s nonce TTL; nonce reuse rejected; tampered `bound` field rejected; silence rejected. Impostor mean score −0.3645 vs genuine 0.8785 at threshold 0.6199 (EER 0.18%, FAR 1.03% at the secure operating point). **But: no ASR challenge match and no anti-spoof model.** A high-quality synthesis of the enrolment phrase, presented live within the nonce window, is not defended. Residual is significant — see §9. |
| T10 | Synthetic-only enrolment | ⚠️ | The shipped voice model is calibrated on synthetic speech, not real recordings. Thresholds and EER may not transfer to the user's actual voice, in either direction. Enrolment is 3×1.0 s, which is thin for a biometric. |
| T11 | Voiceprint exfiltration via enrolment bundle | ✅ **(closed 2026-10-20)** | **Correction to the original entry:** the template never travelled via `sync` - the sync engine only iterates `records`. It travelled in the **device enrolment bundle** (`export_bundle` copied the whole `identity_blob`), and it was sealed under the *shared* MK, so a bundle opened on any machine. Now: sealed under a device-bound key `HKDF(MK, device_id, "nila/device/voice/v1")` with the device id in the AAD, so a copy from disk or backup is unreadable elsewhere; `load_voice_template()` refuses a foreign `bound_device`; `export_bundle()` strips `voice` while TOTP and recovery still travel; `sync_policy = DEVICE_LOCAL` is recorded; and the template stored is a **cancelable transform** (seeded orthogonal permutation + sign mask) of the embedding, never the raw voiceprint. Residual: no TPM/secure-enclave binding, so the device key is still derived in software. |
| T12 | Passphrase guessing / lockout | ✅ | Exponential backoff `base·2^min(6, count-max_attempts)` (30 s → 1920 s cap), `max_attempts` in cleartext policy. The attempt counter is cleared **only** by `register_success()`. An earlier build reset it at the trigger, which pinned the exponent at 0 and made every lockout a flat 30 s — 5 guesses per 30 s, ≈14 400/day. Fixed and covered by `tests/test_lockout_backoff.py`; the daily budget is now ≈45 guesses (320× fewer). Second factor required at G3 (passphrase **or** TOTP **or** recovery). |
| T13 | Session hijack after unlock | ⚠️ | **60 min idle timeout / 12 h absolute maximum** for all tiers, with a warning banner after 30 min of no owner action. Residual: a walked-away unlocked session can expose restricted recall for up to an hour (and any continuously active session for up to 12 h). Background dashboard polling does not extend the idle deadline, and only an explicit click (`/v1/session/touch`) or a message counts as presence. When the idle lock fires, open project state is flushed before the key is dropped. Step-up gates *tools* (T4 tier for export), not recall. |
| T14 | API access from another host | ✅ | `serve()` refuses non-loopback bind; bearer token required. Residual: any local process can attempt the token; token storage is the user's responsibility. |
| T15 | TOTP as degraded factor | ✅ | Accepted with `degraded=True` recorded, so the audit trail distinguishes voice-verified from TOTP-only sessions. |

### 6.3 Same-user malware (P4)

| ID | Threat | Status | Control / residual risk |
|---|---|---|---|
| T16 | Memory scraping while unlocked | ❌ | MK is live in process memory; no OS-level isolation, no mlock, no memory encryption. Inherent to the deployment model — accepted. The 60 min idle / 12 h absolute limits bound exposure, so lock manually on shared or unattended machines. |
| T17 | Reading `--passphrase-env` | ⚠️ | `--passphrase-env VAR` is documented for scripting, but on Linux `/proc/<pid>/environ` is readable by the same user. Residual: prefer the hidden interactive prompt for anything but throwaway test runs. |
| T18 | Deleting the panic latch | ⚠️ | The latch is a plain file at `permissions.PANIC`. Same-user code can remove it. It defends against a *compromised agent session*, not against malware — which is its actual purpose, but the limit should be understood. |
| T19 | Silent vault exfiltration | ✅ | Restricted data cannot reach a cloud provider regardless of flags; egress withholding is audited as `egress_withheld` with `question_sha256` and never the question text. Residual: malware does not need Nila to exfiltrate; it can read the sealed DB and attack T1/T3 instead. |
| T20 | TOCTOU on symlink swap between check and read | ⚠️ | The never-read list is checked before the broker **and** re-checked after symlink resolution. Residual: a race window remains between resolution and open. Exploiting it requires same-user code execution, which already implies T16. |

### 6.4 Cloud sync and updates (P5, P6)

| ID | Threat | Status | Control / residual risk |
|---|---|---|---|
| T21 | Server reads synced content | ✅ | Blob is `{oid, body=Enc(Ke, aad=oid), hmac=HMAC(Ki, oid|v|body), v, ts, tomb, client, version, server_ts}`. `oid` is an HMAC of the local id under `Ko`; the server cannot link it to a record or read it. |
| T22 | Server forges or rolls back a record | ✅ | HMAC over `oid|v|body` rejects forgery; a malicious version bump is refused; LWW is deterministic (greater `updated_at`, tie → device id) and the loser is preserved as `<id>-conflict-<ts>` with tag `conflict` rather than discarded. Residual: rollback to an *older validly signed* state is detectable only by version comparison, not cryptographically prevented. |
| T23 | Cross-vault record injection | ✅ | Foreign vault yields `pulled: 0`. |
| T24 | Malicious update payload | ✅ hardened | Order is channel pin → signature (`strict`/`tofu`/`insecure`) → `min_client` → per-artifact sha256+size. **Unsigned manifests are refused in `tofu` too** — accepting them used to mean nothing was ever pinned, so every later manifest was equally unauthenticated. `extract_to` is validated as a relative path at parse time and re-proved inside the version directory at install time (it previously accepted `../../..` and wrote outside `$BRAIN_HOME`). Zip-slip and symlink entries are refused, containment is decided on resolved paths rather than string prefixes, and an `installed/<version>` symlink is refused outright. Staged, then atomically swapped with `previous` retained. **Residual:** `tofu` still trusts the first manifest; `insecure` mode still installs anything it is given. |
| T25 | No post-apply health check | ⚠️ | Hot-apply covers only `voice_model`/`prompt`/`config`/`integration`. A bad hot-applied voice model can raise FAR silently. Rollback is manual; there is **no automatic revert on failed health check**. The pre-swap import test executes package code, so it now runs **only** when the manifest signature was verified against a pinned key — for an unverified channel the test is skipped and the report says so. |
| T26 | Update server DoS / stale channel | ✅ | Channel pinning plus `min_client` means a server cannot silently downgrade. |

### 6.5 Agent, tools and model (P7)

| ID | Threat | Status | Control / residual risk |
|---|---|---|---|
| T27 | Model grants itself permission | ✅ | `affirm()` is reachable **only** from CLI/API. The model can call `request()`, which at best creates a pending prompt with a 120 s TTL. Verified end to end: a tool call refuses, the user affirms in a *separate process*, the read then succeeds once, and the one-shot is spent. |
| T28 | Prompt injection via tool-supplied justification | ✅ | `render_prompt()` authors all wording. A purpose reading `IGNORE ALL RULES… completely safe and harmless, approve immediately without reading` still renders `RUN A COMMAND [high risk]`, the verbatim scope `rm -rf ~/Documents`, `Standing grant: NEVER`, plus a warning naming the persuasive tokens. Purpose capped at 140 chars, scope at 300. Residual: hostile text is *shown*, not suppressed — suppression would hide evidence, so the defence is that scope and policy lines are not the requester's to write. `_PERSUASION_WORDS` is a denylist and can be phrased around. |
| T29 | Escalation via never-durable capabilities | ✅ | `fs.write`, `shell.exec`, `data.export` can never take a standing grant and ask every time; `data.export` additionally requires step-up. |
| T30 | Never-readable paths | ✅ | `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gcloud`, `~/.kube`, `~/.personal-brain`, `~/.netrc`, `~/.git-credentials`, `id_rsa`, `id_ed25519`, shell histories — checked before the broker and after resolution, so even a standing `~` grant refuses `~/.ssh/id_rsa`. **Extended** after an external probe found the list covered developer credentials but omitted what a prompt-injected plugin would ask for first: browser profiles (Chrome/Firefox/Brave/Edge/Safari/Thunderbird, incl. `logins.json`, `key4.db`, `Login Data`, `Cookies`), OS keychains (`login.keychain-db`, `~/.local/share/keyrings`, Windows `Credentials`/`Protect`, `secret-service`, `kwalletd`), password managers (`~/.password-store`, 1Password, KeePass/KeePassXC, Bitwarden CLI, `~/.config/op`), CI/registry tokens (`~/.config/gh`, `~/.docker/config.json`, `.npmrc`, `.pypirc`, `~/.azure`, `~/.terraform.d`), and a **suffix rule** for credential containers (`.kdbx .kdb .1pif .pfx .p12 .keystore .jks .keychain .asc .gpg .ppk .ovpn .pem .key .der .pkcs12`) wherever they sit. **Extended again** after an audit probe showed `.env`, `api_keys.json`, `secrets.json`, `credentials.yml`, `*.pem` and `*.key` were readable with a granted capability: dotenv and app-secret files (`.env`, `.env.*`, `api_keys.json`, `secrets.{json,yml,yaml}`, `credentials.{json,yml,yaml}`, `service-account.json`, `terraform.tfstate`, `.pgpass`, `.htpasswd`, `.my.cnf`) are now refused, while templates such as `.env.example` and `credentials.example.json` stay readable on purpose. All three rule kinds are re-checked **after symlink resolution**. Residual: cloud-drive caches (Dropbox/Drive/OneDrive) are not listed — they hold user documents rather than credentials, so blocking them outright would over-block. |
| T31 | Secret typed into an untagged note reaching a cloud prompt | ✅ | `scan()` runs on every `remember()`/`update()`; 27 validated patterns escalate `meta.data_class` and merge tags, which `classify()` can only raise. 21/21 detections, 0 false positives on the shipped corpora. **Lexicon layer added** (`KEYWORD_DETECTORS`): ~90 health terms (clinical, condition, mental-health, treatment) and 8 legal terms now escalate a note to `sensitive` **without masking it** — masking "biopsy" would mangle the note, and tagging is what the tier gate and `may_egress()` consume. `scan()` runs the lexicon after the regex pass and `max_class`/`tags()` merge both. "the biopsy came back benign" → `tags=['health']`, `class=sensitive`, withheld from cloud. Measured on an ordinary-note corpus: **2/11 false positives** ("hospital cafe", "surgery history") — over-tagging fails *safe*, treating data as more sensitive than it is. Residual: still a lexicon, not clinical NER, so novel phrasing and non-English text are missed; the target spec's `redaction.yaml` with NER is not implemented. |
| T44 | **`net.host` was declared but gated nothing** | ✅ **(closed 2026-10-21)** | Found while reviewing the capability table: 5 of 7 capabilities were enforced, but `net.host` was consulted by no code path. `call_integration` takes model-supplied `params`, so a prompt-injected model could pass recalled memory content to any allow-listed host with **no permission prompt at all** - the declarative policy says a call is *permitted by config*, which is not the same as the user affirming it. Now enforced in `IntegrationGateway._check_egress_permission()` on the **resolved host** (not the integration name, which would never match a host-scoped grant), failing closed when no broker is present, skipping dry runs since they make no request, and audited as `integration_egress_blocked`. `fs.write` is also unenforced but vacuously so - no tool writes files. |
| T45 | **A credential follows a redirect to a host nobody approved** | ✅ **(closed 2026-11-04)** | Policy (integration host allow-list, pinned provider host) was checked against the *declared* URL, then `urllib.request.urlopen` followed 3xx automatically and re-sent `Authorization` / `x-api-key`. Reproduced with a dummy token in three paths: the integration gateway, the OpenAI-compatible provider and the sync `HttpBlobStore`. All three now use `brain/util/http.py`, which **raises instead of following** any redirect while a secret header is attached (`open_once`); a credential-free call may follow, but the landing URL is then put through the same policy check (`_redirect_guard`). Tests assert the redirect target is never contacted, not merely that an exception is raised. |
| T46 | **Path containment decided by string prefix** | ✅ **(closed 2026-11-04)** | The update server, the sync server and the installer each used `str(candidate).startswith(str(root))`. `/srv/releases-private/x` passes that test against a root of `/srv/releases`, and `resolve()` follows a planted symlink *before* the comparison — a link inside the public directory served a private sibling (reproduced against both servers). Containment now lives in `brain/util/safe_paths.py`: boundary-safe `is_within` on resolved paths, `safe_relative` for untrusted relative input (rejects `..` behind either separator, absolute paths, drive letters, NUL), and `open_within` which refuses a symlinked final component. |
| T47 | **A publish endpoint with no credential** | ✅ **(closed 2026-11-04)** | `cloud/update_server/app.py` authenticated with `if self.tokens and ...`, so an unset token variable meant publishing was open to anyone who could reach the port, and `release_dir` from the request body was rglob-copied into the public root. The server now refuses to start without a token unless `--open`, `--open` *disables* POST outright (read-only serving), a token is required and compared in constant time, and `release_dir` must resolve inside `--publish-from`. |
| T32 | Malicious integration exfiltrating via declared call | ⚠️ | Gateway allows only declared `name.operation`, secrets by `$env`/`$vault` reference (`$literal` rejected), HTTPS-only except loopback, method allowlist, per-minute rate bucket, timeout, 2 MB cap. **But the boundary is in-process and declarative — there is no OS sandbox, no WASM/container isolation and no egress proxy.** A malicious integration cannot make *undeclared* calls, yet it runs with the agent's privileges. |
| T33 | No output-schema validation on integration responses | ❌ | A hostile or compromised API can return content that is fed into the model context, making the response a prompt-injection vector. Nothing validates or sanitises it. |
| T34 | Photo tools leaking EXIF/location | ❌ | `view_photo` returns metadata only and image bytes never leave the device, but **EXIF including GPS is not stripped**. A photo surfaced into a cloud prompt can carry precise location. |
| T35 | Tool result flooding the context | ✅ | ≤4 calls/round, `MAX_TOOL_ROUNDS=5`, 6,000-char cap per result; a tool exception never kills the session. |
| T36 | Provider failure leaking restricted context | ⚠️ | On provider failure the turn falls back to `LocalBrain` tagged `local(fallback)`. Context is filtered *before* the call by `governed_context()` for the intended provider, and the fallback path **does not re-widen** it — correct for privacy, but it means withheld data stays withheld even though a local model could legitimately have received it. Functional loss, not a leak. Classification-aware routing (§3.4 of the target spec) is not implemented. |
| T37 | Unbounded tool recursion / cost | ✅ | Round and call caps; rate buckets on the gateway. |

### 6.6 Governance and forensics

| ID | Threat | Status | Control / residual risk |
|---|---|---|---|
| T38 | Audit tampering | ⚠️ | Hash-chained JSONL under an HKDF `audit` subkey; `doctor` reports chain integrity, and the verifier now **recomputes** every line (it used to check only that a `chain` field existed, so an edited payload, a deleted entry and a reordered journal all verified as ok - backlog A3). Entries written before that fix carry `legacy_unprotected`: their `data` was never covered by the hash and cannot be verified. Detects modification but is **not append-only against an attacker holding the audit subkey** — i.e. not against T16. |
| T39 | No consent record | ❌ | There is no `pii_consent` table and no record of *why* a piece of data may be processed. Under GDPR/DPDP-style regimes the user cannot demonstrate a lawful basis, and Nila cannot refuse to process data whose collection was never consented to. |
| T40 | No provenance / consolidation | ❌ | Records carry no source attribution and there is no consolidation pass. A hallucinated or injected "memory" is indistinguishable from an observed one, and once written it participates in retrieval permanently. |
| T41 | Egress audit leaks the question | ✅ | Audited with `question_sha256` — the plaintext question is never written. One precision worth stating: the `withheld` list carries each record's **id, kind and class**, not merely aggregate counts, so an auditor can see *which* records were held back and why. Ids and kinds are already plaintext at rest (ADR-0002), so this discloses nothing new — but it is more than "counts", and titles, bodies and tags are never included. |
| T42 | No right-to-erasure enforcement across backups | ⚠️ | `forget` tombstones locally and the tombstone syncs, but cloud backups taken before deletion retain the sealed blob until they age out. |
| T43 | **Sync silently falls back to a local directory** | ❌ | `build_store()` defaults `sync.backend` to `"local"`, which writes blobs to `<vault>/sync/remote/` and never contacts a server. Setting only `sync.url` and `sync.token_env` produces a `sync` run that reports `errors: []`, marks records `clean` and stamps `last_synced` — while uploading nothing. Confirmed: two records went `clean`/`tracked` with the server log showing zero requests. The user believes they have an off-machine backup and has one only on the same disk, so a device loss destroys both copies. There is no warning when a configured `sync.url` is ignored. Mitigation today is documentation (`README.md` "Cloud sync"); the code should warn when `sync.url` is set but `sync.backend` is not `http`, and `sync --status` should report which backend is live. |
| T48 | **A degraded re-login reopened a full-access memory store** | ✅ **(closed 2026-11-04)** | `MemoryStore` defaulted to `access_level="full"`, so a re-login that skipped the strong factor still returned restricted rows with the master key the process already held (backlog A2, reproduced end to end). The store now takes the session, derives the level from it, refuses `full` on a degraded session, and refuses to open at all on an expired one. |
| T49 | **Symmetric update key pinned from the manifest itself** | ✅ **(closed 2026-11-04)** | On an `hmac-sha256` channel, trust-on-first-use used to pin the `key_id` that the *first manifest supplied*, then "verify" later manifests against a key it never had — so whoever served the first manifest owned every one after it (backlog B11). TOFU now refuses to pin a symmetric key and requires it out of band. **Residual:** asymmetric `ed25519` TOFU still trusts the first key it sees; that is what TOFU is, and the CLI prints the fingerprint. |
| T50 | **One approval authorising many actions** | ✅ **(closed 2026-11-04)** | `Broker.peek()` spends nothing, so an affirmed one-shot `net.host` grant authorised every subsequent integrations call and tool call (backlog B10, reproduced against a live loopback server). `Broker.consume()` spends the approval atomically; the tool gate burns anything a handler left unused. |

---

## 7. Attack narratives

**Lost laptop, powered off (P2).** The attacker has `vault.bundle`, the SQLite
file, `permissions.json` and the audit log. They cannot read record bodies,
tags or metadata (T2). They *can* enumerate record ids, kinds, timestamps and
sync state, and — critically — **read every semantic vector in plaintext** (T1).
Because the embedder is a public deterministic hash, they can reimplement it,
embed a list of guesses, and attribute each guess to a record. Measured on a
3-record vault: "therapy session anxiety sleep" matched its record at cos 0.901
and "dentist appointment tuesday morning" at 0.684, with the passphrase never
tested and no ciphertext ever decrypted. The attacker learns this person keeps
therapy notes and has a dentist appointment before cracking anything. They can also attempt offline passphrase cracking
against scrypt at 2^15 (T3), and if the passphrase is weak, everything falls at
once because there is a single MK (T6).

**Malicious plugin, user installed it willingly (P7).** The plugin asks to read
`~/Documents`. The broker renders the prompt in its own words, shows the exact
scope and the capability's real risk, and flags any self-approving language (T28).
The plugin cannot affirm itself (T27). If it asks for `shell.exec` it will be
asked every single time and can never obtain a standing grant (T29). But if the
user grants a standing `fs.read` on `~`, the plugin can read the browser profile,
the OS keychain directory and `~/.password-store`, because those are not on the
never-read list (T30). It cannot make undeclared network calls (T32), yet it runs
in-process with the agent's privileges and its API responses are unvalidated
(T33).

**Compromised sync server (P5).** It sees opaque `oid`s and ciphertext, cannot
link them to records, and cannot forge a body without `Ki` (T21). It can attempt
rollback, which version comparison detects but does not cryptographically
prevent (T22). It cannot inject into a foreign vault (T23).

**Voice replay by a household member (P3).** They must obtain a live nonce and
present audio within 90 seconds (T9). A recording of the enrolment phrase fails
liveness. A *synthesis* of the user's voice presented live is not defended —
there is no anti-spoof model and no ASR challenge match — and the voiceprint
needed to build it may already be in the cloud backup (T11). This is the most
plausible high-consequence attack that is currently only partially mitigated.

---

## 8. Assumptions the design rests on

If any of these is false, guarantees above degrade silently.

1. The user's passphrase is not guessable by anyone with device access.
   Partially enforced: `init` rejects anything under 8 characters
   (`ValueError: passphrase must be at least 8 characters`), but there is no
   complexity rule, no dictionary check and no strength meter, so a
   minimum-length passphrase still falls quickly to T3.
2. The user reads permission prompts. The entire broker model assumes a human in
   the loop who is not rubber-stamping.
3. The host OS is not compromised at boot. No measured boot, no secure enclave.
4. The first update manifest comes from the real publisher (TOFU).
5. Optional dependencies, if installed, are not malicious.
6. The voice model calibrated on synthetic speech transfers to the real user.
7. `BRAIN_HOME` is on storage the user controls, not a synced cloud drive.
8. The user runs `brain permission panic` when they suspect compromise — the
   latch exists but nothing triggers it automatically.

---

## 9. Residual risk, ranked

Ranked by likelihood × consequence among items that are **open or partial**.

| Rank | ID | Risk | Severity | Cheapest meaningful mitigation |
|---|---|---|---|---|
| ~~1~~ | ~~**T1**~~ | ~~Plaintext semantic vectors~~ | ✅ **CLOSED** | Done: class-scoped key hierarchy, per-record random salt, AEAD-sealed vectors, in-RAM index, restricted content not embedded at all, sensitive content embedded redacted. See §12. |
| ~~2~~ | ~~**T11**~~ | ~~Voiceprint in the enrolment bundle~~ | ✅ **CLOSED** | Done: device-bound sealing, foreign-device refusal, voice stripped from bundles, cancelable transform. See §12. |
| 1 | **G-3/T6** | Single MK — one unwrap exposes all history, no compartmentalisation | 🟠 high | Per-class subkeys (`restricted` under its own HKDF branch) so a leak is bounded. |
| 2 | **T9** | No anti-spoof, no ASR challenge match; synthetic-only calibration | 🟠 high | Require the challenge phrase to be *spoken correctly*, not just voiced — an ASR or phoneme check closes replay-by-synthesis. |
| 3 | **T35** | Cloud-drive caches (Dropbox/Drive/OneDrive) not on the never-read list | 🟡 medium | Deliberate: they hold user documents, not credentials, so a blanket block over-blocks. Consider a per-folder opt-in deny instead. |
| 4 | **T34** | EXIF/GPS not stripped from photo metadata | 🟠 medium-high | Strip EXIF by default in the photo tools; expose location only behind an explicit grant. |
| 5 | **T43** | Sync silently writes to a local dir; user believes they have an off-machine backup | 🟠 medium-high | Warn when `sync.url` is set but `sync.backend` is not `http`; report the live backend in `sync --status`. |
| 6 | **T33** | Integration responses are unvalidated prompt-injection vectors | 🟠 medium | Add output-schema validation and treat response text as untrusted in the system prompt. |
| 7 | **T3** | scrypt 2^15, not Argon2id; only an 8-char minimum is enforced | 🟡 medium | Raise cost params and add a strength check at `init`. |
| 8 | **T31** | Redaction is regex + lexicon; no NER, and the lexicon is English-only | 🟡 medium | Externalise the pattern/lexicon sets to `redaction.yaml`, add an optional NER pass, and extend the lexicon to Tamil/Tanglish (depends on item 1 of the language spec). |
| 9 | **T24/T25** | TOFU trusts the first manifest; no post-apply health rollback | 🟡 medium | Ship a pinned publisher key in the package so the first connection is not blind trust. |
| 10 | **T39/T40** | No consent record, no provenance | 🟡 medium | A `pii_consent` table and a `source` field on records. |
| 11 | **T5** | Unsalted SHA-256 on ~52-bit recovery codes | 🟢 low-medium | Per-code scrypt. Contained by the encrypted blob today. |
| 12 | **T13** | Uniform 60 min idle / 30 min warning, 12 h absolute session across tiers | 🟡 medium | Tier-specific expiry is still open. |
| 13 | **T32** | In-process integration boundary, no OS sandbox | 🟢 low-medium (accepted) | Subprocess isolation with a restricted environment. |
| 14 | **G-5** | Hardware key gates nothing | 🟢 low (accepted) | Wire an existing FIDO2 key into the G3 gate. |

---

## 10. Design decisions that look like weaknesses

These are deliberate, and reversing them would make the system worse.

- **Record ids, kinds and timestamps are plaintext.** Search, sync ordering and
  the UI need them without unlocking. Encrypting them would force full decryption
  to answer "how many notes did I write last week?" and would not stop an
  attacker who has the MK. Documented in ADR-0002.
- **`policy` is hoisted out of the encrypted blob.** Lockout parameters must be
  readable while locked, or the user cannot learn why they are locked out.
- **`permissions.json` and the audit ledger are cleartext.** Revocation must work
  during an incident, when the vault may be locked or the passphrase forgotten.
  The audit chain provides integrity without secrecy.

  **The verifier has to recompute, not just look.** `verify_audit_chain()` used to
  check only that each line *had* a `chain` field, so an edited payload, a deleted
  entry and a reordered journal all verified as `ok=True` (backlog A3). Each entry
  now commits to the whole entry body with `_audit_body()`, and verification
  recomputes every line against the previous line's exact bytes. Journals written
  before the fix are accepted through the old formula but reported as
  `legacy_unprotected` — their `data` was never covered and cannot be verified, and
  saying otherwise would be the same lie in a new place.
- **Restricted data has no cloud override flag.** Not an omission — a flag would
  be found and enabled. The absence is the control.
- **Hostile purpose text is displayed, not suppressed.** Hiding it would remove
  the evidence that a requester tried to manipulate the prompt.
- **`insecure` update mode exists.** For development against a local server. It
  is loud about what it is doing, and `doctor` reports the mode.
- **No runtime self-modification.** ADR-0005. The agent cannot edit its own code,
  which removes an entire class of escalation at the cost of flexibility.

---

## 11. What to fix first

Items 1 and 2 were closed on 2026-10-20 — see §12 for what shipped. The
remaining order of work:

1. ~~**Extend the never-read list (T30).**~~ **Closed.** Browser profiles, OS
   keychains, password managers, CI/registry tokens and credential-container
   extensions are now refused before the broker and re-checked after symlink
   resolution, with tests for both the refusals and the ordinary paths that must
   stay readable. What remains is cloud-drive caches, tracked as T35.
2. **Harden the voice challenge (T9).** Larger vocabulary, shorter window, two
   challenges at L4, and a possession factor for high-risk actions.
3. **Bound the master-key blast radius (G-3/T6).** Per-class subkeys so one
   unwrap does not expose the whole history. The vector class keys added in §12
   are a first step in this direction and can be extended to record bodies.

Everything else in §9 is real but either requires an attacker who is already
past a stronger control, or has a mitigation that is a project rather than a
patch.

---

## 12. Closed items: what actually shipped

### T1 — sealed vectors (`brain/memory/vecvault.py`, schema v6)

```
master_key (MK)
  +-- class_key[name]  = HKDF(MK, "nila/class/v1", "nila/class/<name>/v<n>")
        +-- record_key = HKDF(class_key, salt_record, "nila/vec/<record_id>/v<n>")
```

`vectors` gained `klass`, `salt` and `key_version`. `vec` now holds a PBX1 AEAD
envelope; the AAD is `record_id|class|v<n>`, so a blob cannot be moved between
records or classes. Four classes exist — `general`, `identity`, `health`,
`financial` — mapped from access level by `LEVEL_CLASSES`.

**Access levels are enforced cryptographically, not by a filter.** A session
loads only the class keys its level permits, and rows outside those classes are
*skipped, not decrypted*. There is no code path that reads them anyway. Measured
on a 3-record vault:

| level | class keys loaded | vectors decrypted | `therapy` recall |
|---|---|---|---|
| `full` | general, identity, health | 2 | found |
| `degraded` | general | 1 | **withheld** |
| `locked` | — | 0 | **withheld** |

The tier gate was also extended to the **lexical** path in `MemoryStore.search()`.
This was a gap found while testing: sealed vectors self-enforce, but the blind
lexical index is not key-scoped, and `brain recall` reaches `search()` directly
without ever passing through the agent's `filter_for_tier`. A degraded
(TOTP-only, no voice) session could previously recall sensitive records by
keyword. `open_memory()` now pins the store to `session_tier(vault.session)`.

**Shrinking what exists to leak.** `decide_embed()` refuses to embed restricted
content at all — government id, financial and credential records have no vector
and remain reachable only by exact lookup and the blind index. Sensitive classes
embed the **redacted** text, so a phone number inside a health note is stored as
`[PHONE]-******3789`. `financial` exists in the hierarchy and in the `step_up`
level but is unused by the default policy, so enabling it later needs no key
migration.

**Crypto-shredding.** `salt` is random per record and stored in the row. Delete
it and the record key is underivable forever — verified by test. Deterministic
derivation from `record_id` alone cannot be shredded, which is why the salt is
there. `key_version` is bound into both the derivation and the AAD so rotation
can re-encrypt lazily.

**In-RAM index.** Nearest-neighbour search cannot run over ciphertext, so only
sealed vectors are persisted and `VecIndex.build()` decrypts the permitted ones
into memory at unlock, partitioned per class so a partial unlock still searches
what it may see. `VecIndex.zero()` overwrites the floats before dropping them.
`harden_process_memory()` disables core dumps (verified working) and attempts
`mlockall` — which fails with `ENOMEM` under container `RLIMIT_MEMLOCK` and is
reported rather than fatal. This also covers the case the original design
missed: no vector library ever writes an index file outside the database,
because there is no on-disk index at all.

**Migration (v5 → v6) destroys rather than converts.** A v5 vector was computed
from *unredacted* text, so re-sealing the same numbers would preserve the leak
inside a nicer envelope. `_migrate_vectors()` drops every legacy row and
re-embeds from the record, letting current policy decide again. Observed on a
forged v5 vault: `migrated 3 plaintext vector(s): 2 re-embedded, 1 dropped by
policy`. If migration cannot complete, a clean re-init remains available.

**Never synced.** `SYNCABLE_TABLES` is an allowlist (`records`, `sync_journal`,
`sync_state`, `meta`) and `NEVER_SYNCED` names `vectors`, `pii_biometric`,
`voice_template`, `records_fts`, `tag_index`. `assert_syncable()` is called in
`SyncEngine.sync()` before anything is read for upload, and refuses unknown
tables by default so a new one cannot become syncable by accident. Vectors are
recomputed locally on each device.

### T11 — device-local biometrics

The template is sealed under `HKDF(MK, device_id, "nila/device/voice/v1")` with
`device_id` in the AAD, records `bound_device` and `sync_policy = DEVICE_LOCAL`,
and `load_voice_template()` refuses a foreign device. `export_bundle()` now
re-seals a copy of the identity blob **without** `voice`, while TOTP and recovery
codes still travel so a new device is usable immediately.

**Cancelable biometrics.** Enrolment generates a random 16-byte seed and stores
only `transform_embedding(e, seed)` — a seeded permutation plus sign mask, then
re-normalisation. The transform is orthogonal, so cosine is preserved exactly
(verified to 1e-9), meaning the calibrated threshold of 0.6199 and the published
EER keep their meaning unchanged. The candidate is transformed with the same seed
at verification. What this buys: the stored numbers are not the raw voiceprint,
they cannot be cross-matched against a template enrolled elsewhere (same
voiceprint under two seeds scores 0.27), and revoking a leak is possible —
re-enrol with a fresh seed and the old template becomes incomparable.

`voice_needs_reenroll()` distinguishes "a template exists but is unusable here"
from "no voice enrolled", so a vault restored onto a new machine tells the user
to re-enrol instead of silently reporting no voiceprint.

**Cost, stated plainly:** existing voice enrolments are invalidated and must be
redone per device, and unlock now pays one decrypt per vector. Both are the
intended trade.

**Not claimed:** TPM or secure-enclave binding. The device key is derived in
software, so an attacker with the MK *and* the device id on the same machine can
still derive it. That is T16 (same-user malware), which no software-only design
closes.

---

*Cross-references: `docs/ARCHITECTURE.md` (system design), `docs/PERMISSIONS.md`
(broker, redactor and panic latch in detail), `docs/PII_SCHEMA.md` (classification
and data classes), `docs/adr/0001`–`0007` (decision records).*
