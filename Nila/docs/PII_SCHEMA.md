# Nila — PII Data Schema & Disclosure Matrix

Normative companion to `ARCHITECTURE.md` §2. This is the authoritative list of
what personal data Nila holds, how each field is protected, and who can ever see
it.

---

## 1. Storage-level protection map

For every column in `memory.db`, its protection state. "Clear" means readable by
anyone holding the file; "Sealed" means encrypted under a per-record key;
"Blind" means replaced by a keyed one-way token.

| Column | Table | State | Rationale |
|---|---|---|---|
| `id` | records | **Clear** | Primary lookup key. Format `<kind[:3]>_<random>`. Carries no content information. See ADR-0002 |
| `kind` | records | **Clear** | One of 8 coarse classes. Needed to filter while locked-adjacent code paths run. Reveals *that* a credential reference exists, not what it is |
| `created_at`, `updated_at` | records | **Clear** | `updated_at` is the LWW clock and must be comparable during sync without decryption |
| `version`, `deleted` | records | **Clear** | Sync bookkeeping |
| `sync_state` | records | **Clear** | `dirty` / `clean` |
| `title_enc` | records | **Sealed** | `HKDF(MK,"rec",id)` domain `title` |
| `body_enc` | records | **Sealed** | domain `body` |
| `meta_enc` | records | **Sealed** | domain `meta` |
| `tags_enc` | records | **Sealed** | domain `tags` — was plaintext before schema v5; migrated and the old column dropped |
| `tag_index` | records | **Blind** | `HMAC(Kidx(id), "tag:"+tag)` per tag, space-joined. Supports equality filtering only |
| `rid`, `tokens`, `tags` | records_fts | **Blind** | Folded then HMAC'd. No plaintext vocabulary exists in the index |
| `vec` | vectors | **Sealed by construction** | 512 floats from a hashing embedder over sealed content. Inverting a signed-hashing TF vector to text is not practical, but it is *not* a cryptographic guarantee — treat as sensitive |
| `rid`, `dim`, `backend` | vectors | **Clear** | Metadata |
| `rid`, `op`, `at`, `pushed` | sync_journal | **Clear** | An observer learns *that* record X changed at time T, never what changed |
| `rid`, `base_version`, `remote_version`, `last_synced` | sync_state | **Clear** | Sync cursors |
| `k`, `v` | meta | **Clear** | Schema version, embedder name |

**Verified:** a 23-token probe set (personal names, card numbers, SSIDs,
passphrases, tag words, medical terms, place names) against `memory.db` and
`identity.json` returns **zero** matches.

---

## 2. Identity store protection map

| Field | File | State | Notes |
|---|---|---|---|
| `user` | identity.json | **Clear** | A label you chose, e.g. "owner". Not your legal name unless you make it so |
| `device_id` | identity.json | **Clear** | `dev-<HMAC(MK,label)[:12]>` — pseudonymous, not traceable to you without MK |
| `devices[id].kek_wrap` | identity.json | **Sealed** | MK wrapped under that device's KEK |
| `devices[id].label` | identity.json | **Clear** | e.g. "laptop" |
| `identity_blob` | identity.json | **Sealed** | Contains voice template, TOTP secret, recovery codes, hardware wrap, sessions |
| `verifier` | identity.json | **Clear** | `HMAC(MK, SESSION_TAG)`. Proves a passphrase without storing MK. Offline brute-force target — protected by scrypt cost (n=2^15, r=8, p=1) |
| `policy` | identity.json | **Clear** | Booleans and integers only. Must be enforceable while locked, therefore cannot be sealed. See ADR-0006 |
| `attempts` | identity.json | **Clear** | `{count, locked_until, last_failure}` |
| `kdf` | identity.json | **Clear** | scrypt parameters — public by definition |

### What is inside `identity_blob`

| Sub-field | Sensitivity | Consequence of disclosure |
|---|---|---|
| `voice.template` | **Critical** | Speaker model. Enables targeted replay attacks against G2 |
| `voice.meta` | High | Backend name, threshold, enrolment quality |
| `totp.secret` | **Critical** | Full second-factor bypass |
| `recovery.codes` | **Critical** | Single-use, but each is a full bypass until consumed |
| `hardware_wrap` | High | Hardware-key wrapped material |
| `policy` (copy) | Low | Legacy location; superseded by the clear copy |
| `sessions` | Low | Historical session metadata |
| `meta.devices` | Low | Enrolment history |

Protected by rule **P-8**: unreadable while locked; `load_voice_template()`
returns `None` after `vault.lock()`.

---

## 3. Content classes and their PII exposure

| Class | Typical PII contained | Disclosure if `memory.db` stolen | Disclosure if cloud bucket stolen |
|---|---|---|---|
| `note` | Anything | **Nothing** | Nothing |
| `instruction` | Owner's stated standing rule | `instruction` | **Applied to every local turn** (never Online). Not a stored fact but an active rule: refused if it would disable permissions, the audit trail, the vault's factors or egress; created only by the owner, never by a tool or model output. |
| `preference` | Dietary, religious, political preferences | Class only (`preference` visible via `kind`) | Nothing |
| `task` | Employer, projects, deadlines | Class + timing | Nothing |
| `contact` | **Names, relationships, phone, email** | Class + timing | Nothing |
| `event` | Appointments, travel, medical visits | Class + timing | Nothing |
| `decision` | Sensitive reasoning | Class + timing | Nothing |
| `credential_ref` | *Where* secrets live (never the secrets — rule P-3) | Class + timing | Nothing |
| `health` (tag) | **Diagnoses, medication, therapy** | Nothing beyond class | Nothing |

The residual disclosure from a stolen `memory.db` is therefore: **how many
records you have, their coarse classes, when they changed, and how often.**
That is a real leak — it can reveal, for example, that you created seven
`contact` records and two `credential_ref` records last Tuesday — and it is
accepted deliberately, because eliminating it would break sync's LWW clock and
every lookup path. Recorded as T2 residual risk in `docs/THREAT_MODEL.md`.

It is also **not the whole leak.** The `vectors` column is stored as plaintext
JSON and the default embedder is a public deterministic hash, so a stolen
`memory.db` additionally permits a dictionary attack that attributes guessed
phrases to specific records without the passphrase — measured at cos 0.901 for a
health-related guess in `docs/THREAT_MODEL.md` §6.1, T1. That is the more serious
of the two and is currently unmitigated.

---

## 4. Audit journal schema

`audit.jsonl` — append-only, hash-chained (`chain integrity: {ok, entries,
unparsable}` verified by `brain audit`).

```jsonc
{
  "seq": 15,
  "at": 1792485080788,
  "event": "auth_failure",
  "data": { "reason": "voice_score", "score_band": "low" },
  "prev": "<sha256 of previous entry>",
  "hash": "<sha256 of this entry>"
}
```

| Event | Recorded | **Never** recorded |
|---|---|---|
| `vault_initialized`, `device_enrolled` | device id, label | passphrase, KEK, MK |
| `auth_success` / `auth_failure` | mode, degraded, reason | passphrase, audio, score beyond a band |
| `challenge_issued`, `liveness_failed` | nonce id, failure reason | the spoken phrase, the audio bytes |
| `session_lock` | reason | session key material |
| `tool:<name>` | **risk tier**, sorted **argument key names** | argument *values* (rule P-6) |
| `integration_call` | name, operation, host, status, **redacted secret key names + sha256** | secret values (rule P-7) |
| `sync_start` / `sync_done` | backend, device, counts | record ids, content |
| `bundle_exported` | label, target | the bundle itself |
| `policy_changed` | **keys** that changed | — |

---

## 5. Retention and deletion

| Action | Effect | Propagates? |
|---|---|---|
| `forget <id>` (soft, default) | `deleted=1`, title→`[deleted]`, **body/tags cleared**, version bumped, vector removed | Yes — tombstone syncs to every device |
| `forget <id> --hard` | Row deleted from `records`, `records_fts`, `vectors`, `sync_state`, `sync_journal` | No — local only, by design |
| `purge_tombstones(older_than_ms)` | Removes tombstones older than 30 days by default | Yes |
| `device revoke <id>` | Removes that device's `kek_wrap`; it can no longer unwrap MK | Requires each remaining device to re-sync |
| `device rotate-key` | Re-wraps MK under a new passphrase across all devices | Yes |

**Caveat, stated plainly:** soft delete clears content but SQLite does not scrub
freed pages. A forensic examiner with the file *and* the master key could
recover prior page content. `--hard` plus `VACUUM` is the correct answer for
data that must truly disappear; this is not yet automated (roadmap item).

---

## 6. Egress matrix — where your data can go

| Path | Content leaves the device? | Protection |
|---|---|---|
| Local retrieval (`recall`) | **No** | Sealed until decrypted in-process |
| LLM provider `local` / `ollama` | **No** (loopback) | No network egress at all |
| LLM provider `openai_compatible` / `anthropic` | **Yes** — the prompt, which includes up to 6 retrieved memories | Your choice, configured explicitly; ADR-0007 covers the disclosure |
| `call_integration` | **Yes** — only declared params to declared hosts | 7 gateway controls (§3.2 of ARCHITECTURE) |
| `run_sync` | **Yes** — re-wrapped ciphertext + opaque ids | Server cannot decrypt; E2EE |
| `check_updates` | **Yes** — client version + channel only | No memory content in the request |
| `export_backup` | **Yes**, if you move the file | Ciphertext + integrity tag; requires **T4 step-up** |
| `/v1/*` API | Loopback only | Bearer token; gateway refuses non-loopback bind |

The single largest disclosure surface is the **LLM prompt**: sending a question
to a hosted provider necessarily sends the retrieved memories that give it
context. This is why `local` is the default provider and why the system prompt
forbids claiming data was sent when no tool sent it.
