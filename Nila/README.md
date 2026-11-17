# Nila — a local-first personal AI brain

> **Windows user? Start with [`START_HERE.md`](START_HERE.md)** (v0.1 setup scripts, Ollama and
> Gemini instructions). See [`ROADMAP.md`](ROADMAP.md) and [`PROGRESS.md`](PROGRESS.md).
> Note: since v0.1 the vault requires an authenticator code (`brain totp` once after `brain init`);
> the passphrase alone is refused.


Nila is a personal agent that remembers things about you, answers from that
memory, and is *careful* about what it touches and what it sends anywhere.

It runs on one machine. Your memories are encrypted at rest. A cloud server is
used only for encrypted backup and for signed package updates — it never sees
plaintext, and it is optional.

```
you ──► Nila ──► encrypted memory vault (SQLite, sealed per record)
              ├──► tools ──► permission broker ──► asks you first
              └──► model ──► cloud LLM only with data that may leave
```

---

## Quick start

Requires **Python 3.10+**. No third-party packages are mandatory.

```bash
unzip personal-brain-mvp.zip
cd personal-brain

# 1. create the vault (passphrase must be at least 8 characters)
python3 brain.py init --label mylaptop

# 2. put something in it
python3 brain.py remember "Rent is 18500 due on the 5th of every month"
python3 brain.py remember "Dentist appointment with Dr Mehta, Tuesday 10am" --tag health

# 3. get it back
python3 brain.py recall "when is rent due"
python3 brain.py ask "what appointments do I have this week"

# 4. talk to it
python3 brain.py chat
```

Every command that needs the vault will prompt for your passphrase. For scripts,
use `--passphrase-env VAR` and put the passphrase in that variable — but see
"Passphrase handling" below before relying on it.

If `init` is slow, add the global `--fast-kdf` flag *before* the subcommand:

```bash
python3 brain.py --fast-kdf init --label mylaptop
```

Note the position: `--fast-kdf`, `--home`, `--json` and `--quiet` are **global**
flags and must come before the subcommand.

### Verify your install

```bash
python3 brain.py doctor      # runs a self-test of crypto, voice, vault, sync
python3 -m pytest tests/ -q          # 633 tests, 2 skipped without a built ZIP
python3 scripts/run_tests.py         # same suite with NO pytest installed
```

---

## What it does

**Encrypted memory.** Titles, bodies, metadata and tags are sealed per record
with a key derived from your passphrase. Search still works while encrypted, via
an HMAC blind index — the server and the database file never see your search
terms in plaintext.

**Voice + passphrase unlock.** A four-gate ceremony: a liveness
challenge-response nonce, speaker verification against your enrolled voiceprint,
your passphrase (or TOTP, or a single-use recovery code), then a session that
expires.

> **Read this: two factors only exist once you enrol a second one.** The ceremony
> is voice **and** passphrase — but on a vault where no voiceprint and no TOTP
> are enrolled, the passphrase is the only factor there is, so unlocking with it
> gives a **full-tier** session that can recall sensitive and restricted
> memories. Nila warns on every such session, but nothing blocks it. Run
> `brain enroll-voice` and `brain totp` before storing anything you care about.
>
> The alternative — treating a no-voice vault as permanently *degraded* — was
> tried and reverted: it made every health and financial memory unrecallable by
> its own owner, a functional dead end rather than a security property. A
> degraded session is now one where a stronger factor *exists and was skipped*;
> in that case sensitive memories are correctly withheld and the CLI says so.

**Permissions.** Nila cannot read your files, look at your photos, run a command
or reach the network unless you affirm it. The model can *request*; only you can
*grant*, from the CLI or the local API. Some capabilities can be granted for 7
days; `fs.write`, `shell.exec` and `data.export` ask every single time and can
never be made standing.

**Secret detection.** When you save a note, Nila scans it. A card number, PAN,
Aadhaar, password or API key in an otherwise ordinary note is detected and the
record is escalated so it can never be sent to a cloud model — even if you
forgot to tag it.

**Egress control.** Memories are classified `public` / `internal` / `sensitive` /
`restricted`. Restricted data has **no** configuration flag that permits it to a
cloud provider. The absence of the flag is the control.

**End-to-end encrypted sync.** Optional. The server stores opaque blobs keyed by
an HMAC of the record id. It cannot read your memories or link them to you.

**Signed updates.** Manifests are channel-pinned and signature-verified before
anything is unpacked; zip-slip and symlink entries are refused; installs are
staged, import-tested and atomically swapped, keeping the previous version for
rollback.

**Panic switch.** If you suspect compromise:

```bash
python3 brain.py permission panic --reason "suspected compromise"
```

This revokes every grant, drops pending prompts, and latches — *including
blocking new grants* — until you deliberately clear it. Unlike `revoke`, a
compromised session cannot simply ask for access again.

---

## Optional extras

Everything above works with the standard library alone. Extras improve quality:

```bash
pip install -r requirements.txt          # numpy, soundfile -> real voice features
pip install -r requirements-optional.txt # semantic embedder, API server, argon2
```

| Extra | What it enables | Without it |
|---|---|---|
| `numpy`, `soundfile` | MFCC voice features from real WAV files | synthetic voice backend; ceremony still enforced |
| `sentence-transformers` | neural semantic search | deterministic hash embedder (bag-of-words-ish) |
| `fastapi`, `uvicorn` | the localhost JSON API | stdlib `ThreadingHTTPServer` gateway |
| `argon2-cffi` | Argon2id key derivation | scrypt n=2^15 (weaker; see threat model T3) |

---

## Cloud sync (optional)

Sync is end-to-end encrypted: the server stores opaque blobs keyed by an HMAC of
the record id and cannot read your memories or link them to you.

**Run the server** (a reference implementation is included):

```bash
export BRAIN_SYNC_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
python3 cloud/sync_server/app.py --host 127.0.0.1 --port 8790 --data /var/lib/brain-sync
```

The server refuses to start with an empty token unless you pass `--open`, which
disables auth and is for local development only. Bind to `127.0.0.1` unless you
have TLS in front of it — binding elsewhere prints a warning for exactly that
reason.

**Point the vault at it.** All three keys are required:

```bash
python3 brain.py config set sync.backend http
python3 brain.py config set sync.url http://127.0.0.1:8790
python3 brain.py config set sync.token_env BRAIN_SYNC_TOKEN
python3 brain.py sync
```

> **Trap worth knowing about.** `sync.backend` defaults to `local`, which writes
> blobs to a directory on disk (`<vault>/sync/remote/`) instead of contacting any
> server. If you set only `sync.url` and `sync.token_env`, `brain sync` reports
> success and marks your records clean while **nothing is uploaded** — you would
> believe you had an off-machine backup and not have one. Always set
> `sync.backend http`.

**Verify it actually reached the server:**

```bash
curl -H "Authorization: Bearer $BRAIN_SYNC_TOKEN" http://127.0.0.1:8790/health
# {"ok": true, "service": "brain-sync", "keys": 2}     <- keys must be non-zero
python3 brain.py sync --status
```

The server never sees plaintext. You can confirm it:

```bash
grep -rl "some phrase from your notes" /var/lib/brain-sync   # should find nothing
```

---

## Commands

```
init            create the vault            remember   add a memory
status          vault/config/memory state   recall     search memories
unlock / lock   run the ceremony            list       browse records
enroll-voice    enrol your voiceprint       forget     delete a record
voice-test      test a recording            chat       interactive session
totp            enrol/show TOTP             ask        one-shot question
challenge       issue a liveness nonce      audit      inspect the ledger
permission      manage tool access          doctor     self-test
sync            push/pull encrypted backup  serve      localhost JSON API
backup/restore  portable vault copy         config     read/write settings
update          apply signed releases       keygen     generate a signing key
device          enrol/list/import devices
instruct        standing rules (owner only) connectors AI + outside services, keys
session         idle rule; --touch = "still here"
```

Two commands deserve a note:

* **`brain instruct`** writes a *standing rule* ("call me Anna") that Nila applies to
  every local turn and never sends to an Online model. It is owner-only on purpose: the
  model's own `remember` tool is refused, and a rule that would switch off permission
  checks, the audit trail or egress is rejected.
* **`brain connectors`** shows which AI services and outside services are on, whether a
  key exists, which hosts each may reach and what it has used today. A key is entered with
  a hidden prompt and stored sealed in the vault; it is never displayed again, only its last
  four characters. Switching a connector on or off asks for a fresh login, and a key can only
  be written from a session that proved your authenticator.

`brain permission --help` and `docs/PERMISSIONS.md` cover the permission model.

---

## Passphrase handling

- Prefer the interactive hidden prompt.
- `--passphrase-env VAR` exists for scripting, but on Linux
  `/proc/<pid>/environ` is readable by other processes running as you. Use it for
  tests, not for daily driving.
- Never pass a passphrase as a literal command-line argument; it lands in shell
  history.
- There is no recovery if you lose the passphrase **and** all recovery codes.
  Write the recovery codes down somewhere offline during `init`.

---

## Verifying it yourself

`python3 scripts/run_tests.py` runs the whole suite with the standard library
alone. It was added because a reviewer with no pytest installed could not check a
single claim in this file, which makes the README untestable rather than the
reviewer under-equipped. It builds the fixtures by hand and says plainly which
tests it cannot, instead of reporting them as passes.

`python3 scripts/build_package.py` builds the ZIP and then verifies it: every
source module and required file must be present, and no cache, vault or secret
file may ride along. It deletes the archive and exits non-zero otherwise. This
exists because a packaging exclusion once swallowed `brain/updates/` and the
suite still passed - nothing imported it.

## Read this before trusting it

`docs/THREAT_MODEL.md` is the honest document in this repository. It lists what
is mitigated, what is partial, and what is open. The most important open items:

1. **A single master key** covers all record bodies, so one unwrap exposes the
   whole history. (Vector embeddings are now separately class-keyed; bodies are
   not yet.)
2. **Voice anti-spoofing is limited.** Liveness and nonce replay are enforced;
   a live high-quality synthesis is not defended against. There is no hardware
   possession factor on the highest-risk actions yet.
3. **The never-read list is incomplete.** `~/.ssh`, `~/.aws`, `~/.gnupg` and
   similar are blocked even under a standing grant, but browser profiles, OS
   keychains and password-store directories are not.
4. **Secret detection is regex-based, with no NER.** A card number or PAN in a
   note is caught; a health detail phrased naturally ("the biopsy came back
   benign") is not, and still classifies as ordinary.
5. **If you enable sync, set `sync.backend http` explicitly.** The default is
   `local`, which writes to a directory on the same disk and reports success
   while uploading nothing (T43).

Recently closed, so you know what *is* handled: semantic vectors are sealed under
class-scoped keys with per-record salts and never touch disk in plaintext,
restricted content is not embedded at all, and the voiceprint is device-local and
stored as a cancelable transform rather than raw (see `docs/THREAT_MODEL.md` §12).

This is an MVP. It is a serious attempt at the right architecture, and it is not
yet something to hold your only copy of sensitive data in.

---

## Layout

```
brain.py              entry point
brain/
  cli/                commands and the REPL
  crypto/             PBX1 envelope, KDF, HKDF subkeys, signing
  memory/             vault (keys) + store (sealed records, search, sync)
  identity/           voice model, liveness, ceremony, TOTP, tiers
  agent/              Brain, providers, tool registry, offline engine
  security/           permission broker, classifier, redactor
  api/                localhost JSON gateway
  cloud/, updates/    e2ee sync client, signed update installer
cloud/                reference sync + update servers
tests/                633 tests (2 need a built ZIP)
docs/                 ARCHITECTURE, THREAT_MODEL, PERMISSIONS, PII_SCHEMA, adr/
scripts/              release tooling
config/               default policy
```

See `docs/ARCHITECTURE.md` for the design and `docs/adr/` for the decisions
behind it.

---

## License and status

Provided as-is for evaluation. Not audited. No warranty. Read the threat model.


## Reporting a problem (v0.2)

    brain issue "voice unlock fails"

Nila runs its own checks, decides which of them matter for your sentence, and
prints a report. **Nothing is written and nothing is sent** until you read the
report and re-run with `--write --yes`; the file goes to `<home>/reports/` and
you decide where it goes next. It never contains API keys, your memories,
e-mail addresses, phone numbers or file paths with your account name - every
string is scrubbed, and the preview you read is byte-for-byte the file written.

    brain profile set callme "Anna"     # facts about you, applied to every local turn
    brain profile show
    brain strict on                     # tools run only if you named them, or said yes
    brain strict deny remember
    brain strict auto remember ask

## The model pack (v0.2)

    brain models                  # what is here, what to add, and what does what
    brain models use qwen2.5:3b   # the default small brain (best Tamil at 2 GB)

Thirteen brains are described in `brain/agent/models.py`: the built-in engine and
Ollama models (Qwen2.5 3B/7B, Llama 3.2 3B, Phi-3.5 mini), speech in and out
(Whisper small, Piper Tamil), and the cloud ones (Gemini Flash/Pro,
OpenAI-compatible, Claude). Each row carries its weight, its RAM load, how good
its Tamil is, and the exact command to install it.

Nila never downloads a model. It reports what is **really** present - Ollama
answering on 127.0.0.1 with the model pulled - and a model that is not present is
never chosen. Small questions are answered by the cheapest brain that can do the
job (usually the built-in engine, 0 bytes), real work goes to the big one, and
every reply says which brain answered and whether it was free.
