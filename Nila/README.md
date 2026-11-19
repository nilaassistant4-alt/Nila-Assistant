# Nila — a local-first personal AI brain

> **Windows user? Start with [`START_HERE.md`](START_HERE.md)** (v0.2: the six `.bat` files,
> voice in/out, the phone window, Ollama and Gemini). See [`ROADMAP.md`](ROADMAP.md) and [`PROGRESS.md`](PROGRESS.md).
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
unzip NILA_V02_STAGE15.zip      # the file you downloaded
cd Nila

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

Every command that needs the vault asks for your passphrase **once per session**.
On Windows, one extra step makes it *once, ever* on that laptop:

```bat
nila-first-run.bat          :: setup (if needed) + remember this laptop + check
brain passphrase status     :: will it ask me again?  (a plain yes/no)
brain lock-check            :: the same, with the four reasons on one screen
brain hotkey --write        :: a desktop shortcut; set Ctrl+Alt+N on it once
nila-easy-start.bat         :: everyday start: shortcut, voice, then Nila
```

`remember` stores the **master key** in the OS secret store (Windows Credential
Locker, macOS Keychain, Linux Secret Service; on Windows, DPAPI per-user as a
fallback) — never the passphrase. It is scoped to **you and this laptop**: another
machine, or another Windows account, still needs the passphrase. Undo it with
`brain passphrase forget`; a per-command `--no-saved-key` ignores it. For scripts,
`--passphrase-env VAR` still works — but see "Passphrase handling" below.

The unlock flags (`--passphrase-env`, `--totp`, `--recovery`) work on **every**
subcommand and in **either** position: before the command, or after it.

```bash
python3 brain.py --passphrase-env PW --totp 123456 progress
python3 brain.py progress --passphrase-env PW --totp 123456
```

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
ocr             read words out of a picture or a scanned PDF (nothing installed)
screen          read the words in a screenshot YOU took (never captures)
music           play songs from your own folders (no account)
media           video: scene cuts, and cutting without re-encoding
search          find a memory by meaning, from a local word-vector file
calendar        read an .ics, or --add/--update/--remove a local one
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

## Reading, showing, playing, cutting (v0.2, stage 12)

    brain ocr --demo                      # writes a page, reads it back, compares
    brain ocr page.png                    # the words in a picture
    brain file scanned-invoice.pdf        # a scanned PDF is read, not refused
    brain ocr --engines                   # built-in reader vs Tesseract
    brain screen                          # what to press; then give it the picture
    brain music --add-folder "D:/Songs"   # once; then `brain music <words> --play`
    brain media scenes video.mp4          # scene starts, from the video's own index
    brain media video.mp4 --trim 90,135   # the command; --run actually writes it
    brain search --wordvec glove.txt --meaning "scooter needs fixing"

**What the built-in OCR can and cannot do, measured.** It decodes a PNG itself and
reads pages printed in *its own* font exactly, at any size, bit depth or noise - so a
screenshot of text, a fax-style scan and a scanned PDF work with nothing installed.
A page typeset in a real font (Sans, Serif, Mono were measured) is read badly; when
that happens the answer is marked unsure and points at Tesseract rather than handing
over wrong words at full confidence. `brain ocr --how` gives the two-line install
for photo-grade OCR and Tamil.

**Screen reading has no capture code.** `brain/agent/screen.py` cannot take a
screenshot: there is no call to any screen API in it, and a test reads the source to
keep it that way. You press Win+Shift+S, save, and hand Nila the file; every answer
says `watchful: false`. What the picture *means* needs a vision model; the words in
it are read here.

**Scene cuts are read, not guessed.** An MP4 lists the frames the encoder marked as
independent (`stss`), and those are the scene starts - exact, and no decode, so a
10 GB file takes milliseconds. A cut is planned first and copies the stream
(`-c copy`), so the picture is unchanged; `--run` is required to write anything.
Frame-by-frame comparison and silence detection do need a decoder: `getffmpeg.bat`
puts two `.exe` files in `tools\` (no installer, no admin rights) and proves they run.

## Two PCs, one memory (v0.2, stage 12)

    PC 1:  sync-host.bat      # prints an address, a port and a join code
    PC 2:  sync-join.bat      # type those two things, once
    either: sync-now.bat      # catch up whenever you like

The join code is six groups of four characters with no O/0, I/1, L/l, S/5 or B/8 in
it, so reading it over the phone is safe. The token is proved against the server
before anything is saved, and stored next to the vault, so no environment variable is
involved. **A public address is refused**: the two PCs must be on the same Wi-Fi, and
the data is ciphertext before it leaves either one. See `cloud/README.md` for running
the server elsewhere, and `tests/test_sync_over_http_acceptance.py` for the proof.

## Saying the name, and holding a number (v0.2, stage 13)

    brain wake --window 120               # the microphone is open for 2 minutes
    brain wake --check nila.wav           # score a recording (no microphone needed)
    brain temp hold 9845123789 --for "call amma" --use
    brain temp                            # what is held: ids and purposes, never values
    brain temp purge                      # forget all of it now
    brain permission mode command         # every action asks again, always
    brain permission mode standing        # back to a grant a human may make
    brain challenge --style tamil_digits  # the number to say, in Tamil
    brain connectors integrations         # which account each service speaks for
    brain connectors integrations bind gmail you@gmail.com
    brain device limit 3                  # how many PCs may hold this brain

**The wake word is not an always-on microphone.** `brain wake` opens a window, and
the window closes: by a timer, when the name is heard, or when you stop it. The
ceiling is 15 minutes, a longer window is refused, and opening one asks the
`mic.listen` capability and writes both the open and the close to the audit
journal. What matched comes back with its audio, so the speaker lock can judge the
voice before anything is acted on - a wake word is a doorbell, not a key. The
matcher is loudness + voicing + two-syllable shape, not a trained keyword spotter;
`brain/agent/wake.py: LIMITS` says exactly where it stops being reliable.

**A held value is never written down.** `brain temp hold` keeps the value in RAM
against a purpose. The record that may be written is a salted digest; `--use`
hands it over once and it is gone; it expires, or you purge it, or `brain lock`
purges it; it is not in `memory.db`, so it is not in a backup and not in a sync.
Because it lives in one process, a *later* command cannot see it - that is the
point, and the command says so instead of pretending. If you want something kept,
that is `brain remember`, a different act with a different audit line.

**"Strictly per command" is a mode.** In `command` mode no standing grant is
honoured - not by `check` and not by `peek` - a new one cannot be created, and
switching back restores whatever was there. The three dangerous capabilities
(`fs.write`, `shell.exec`, `data.export`) were never durable; this makes the rest
of them behave the same way when you ask for it.

## Passphrase handling

- Prefer the interactive hidden prompt.
- `--passphrase-env VAR` exists for scripting, but on Linux
  `/proc/<pid>/environ` is readable by other processes running as you. Use it for
  tests, not for daily driving.
- Never pass a passphrase as a literal command-line argument; it lands in shell
  history.
- **Forgot it, but this laptop still opens Nila?** You are not locked out: run
  `brain passphrase set` and choose a new one. The saved device key already proves
  it is your vault, so the old passphrase is not needed — and nothing is
  re-encrypted, because the passphrase only wraps the master key. The old one
  stops working afterwards, on every machine.
- **A recovery code opens the vault by itself** — no passphrase, no phone. That is
  what they are for, and it is why the sheet matters: `start-nila.bat recovery` (or
  `--recovery-stdin`) is the whole ceremony. Each code wraps the master key once and
  is deleted after it is used, so one code opens the vault exactly once and then the
  vault should be given a passphrase you remember (`brain passphrase set`).
- There is no recovery if you lose the passphrase **and** all recovery codes **and**
  every machine that remembered the key (e.g. a dead laptop with no backup). Write
  the recovery codes down somewhere offline during `init`, and keep one `brain
  backup` copy on a USB disk.
- "Remembered" means anyone at your unlocked desktop can open Nila. That is what a
  key in the OS store is; the journal records `unlock_via_device_key` so the
  difference between "he typed it" and "the laptop had it" is never lost.

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

## Reminders (v0.2, stage 2)

    brain remind "in 10 minutes to drink water"
    brain remind "tomorrow at 9 to take the tablet"
    brain remind "every day at 22:00 to take the tablet"
    brain remind list          # or just: remind me ...
    brain remind done --id 1   # snooze --id 1 --minutes 5 / cancel --id 1

A reminder is the owner's own sentence, stored in the encrypted memory store and
rendered by code - never paraphrased by a model, never sent anywhere. In the
chat the same sentences work with no model configured, and an alert that came due
is printed above the next answer, with how late it is if the PC was asleep.

## Everyday maths

   18% of 1250        1250-ல 18 சதவீதம்      இருநூறு ரூபாய்க்கு 15 சதவீதம்
   GST on 2499        GST inside 2950        10% discount on 899
   simple interest on 50000 at 8% for 3 years
   5 feet in cm       2 kg in lb             5 அடி எத்தனை சென்டிமீட்டர்

Every answer shows its steps. The arithmetic is plain Python with no `eval`;
amounts are rounded half-up the way a shop rounds.

## Languages and voice (v0.2, stage 3)

Nila **talks** in Tamil and English only. She **reads** Malayalam, Hindi, Telugu,
Kannada, Bengali, Marathi, Gujarati, Punjabi, Urdu and Sanskrit - by script, or by
common words when the text arrives in Latin letters - and explains what was said
in Tamil:

    brain languages                     # what she reads, what she answers in
    brain languages "എന്റെ പേര് അനു"     # detect, with a word-by-word hint

Reading another language needs a model. With Gemini configured and Online chosen,
one prompt translates and says who translated it; with nothing configured Nila
names the language, glosses the words she knows, and says plainly that a full
translation needs a model. She never invents a meaning.

    brain speak "வணக்கம்"               # SAPI on Windows, Piper, or espeak-ng
    brain speak --engines               # prove every engine with a real Tamil sentence
    brain speak --list-voices           # what Windows has installed
    brain speak --wav out.wav "..."     # write a file instead of speaking

With no engine, `spoken` is false, the reason and the exact fix are stated, and no
place-holder audio is passed off as speech - a silent no is better than a confident
lie. There is a section on all of this in stage 11, below.

## Calendar (v0.2, stage 3 read; stage 12 write)

    brain calendar --set ~/calendar.ics
    brain calendar --set "https://calendar.google.com/calendar/ical/.../basic.ics"
    brain calendar --when tomorrow
    brain calendar --add "Dentist" --start "2026-11-06 10:30" --where "Anna Nagar"
    brain calendar --list

Google publishes each calendar as a *secret address in iCal format*: no login, and
no way to write to **it**. Repeats (daily, weekly, by-day, monthly, count, until),
all-day events, timezones and folded lines are handled. A calendar address is
treated like a password - only its host is ever printed.

Writing goes to a local `.ics` (in the vault folder by default) that your phone or
Google Calendar imports once. The `UID` stays the same across edits and `SEQUENCE`
goes up, so importing an updated file **replaces** the event instead of adding a
second copy; moving an event keeps its length, and a removal leaves a `CANCELLED`
entry so the phone removes it too. Writing straight into Google still needs OAuth,
and Nila says that instead of offering a button that fails.

## Free connectors (v0.2, stage 5)

    brain web pack                          # what is in the pack and what it needs
    brain web weather Chennai               # Open-Meteo: no key, no account
    brain web wiki "drip irrigation"        # Wikipedia, with the source link
    brain web wiki "தமிழ்" --lang ta        # in Tamil
    brain web youtube "drip irrigation"     # links (needs a free Google API key)
    brain web transcript <youtube link>     # what is said in the video
    brain web rss https://example.com/feed  # headlines

Weather, Wikipedia, transcripts and RSS need **no account at all**; YouTube search
needs a free Google API key, kept in the sealed vault. Every one of them is a
plain GET to a named host, so every one is gated by the same `net.host` permission
as any other network call: one approval, one call, audited.

    brain dashboard                         # unlock once, and the app opens itself

`brain dashboard` replaces the old three steps (`unlock`, then `serve`, then copy
the printed token). It is what `start-nila.bat` runs: one passphrase, one
authenticator code, then the browser is in front of you. The token goes into the
address quietly (never into this window); `brain serve --show-token` prints it if
you need it by hand, and `brain start` is still the terminal chat.

    brain habit                             # days you really opened Nila, and the streak
    brain fix                               # voice / hearing / microphone / phone: what is missing

## One voice (v0.2, stage 6)

    brain voice status                  # is a voice enrolled, and the threshold
    brain voice enrol                   # record 3 seconds; run it 2-3 times
    brain voice enrol --file me.wav     # or use a recording
    brain voice check --file other.wav  # score a sample without acting on it

Only the enrolled voice may start Nila, approve anything, or be listened to. The
check combines the shape of the voice with its pitch, and the threshold is fitted
to the owner's own recordings once there are two or more. A stranger, a short
clip, or a noisy room is refused; an in-between match says "uncertain" and asks
again. Anything that matters also asks for a spoken challenge number, so a
recording of the owner cannot be replayed. `brain.security.speaker.LIMITS` says
plainly what this is not: a trained speaker model.

    brain launcher --plan               # what can start Nila on this machine
    brain launcher --write              # an icon (and a hotkey recipe) on the Desktop

A wake word is offered, but only inside a window you open: `brain wake --window 120`
listens for two minutes, hears "Nila", and stops. It lists all day only if you ask
for a window that long, and a window over 15 minutes is refused.

## Any file (v0.2, stage 6)

    brain file report.docx              # PDF, DOCX, XLSX, PPTX, text
    brain file code.py                  # a one-screen map of any code file
    brain file movie.mp4                # size, duration, and the edit commands

Documents are read with the standard library - no dependency, no upload - and a
scanned PDF is called scanned rather than shown as an empty page. A 10 GB video is
never loaded into memory: the container is read by seeking, and finding every cut
and silence is reported as the exact one-pass ffmpeg command, because that is a
decode job and pretending otherwise would be a lie about what was analysed.

## The archive on D (v0.2, stage 6)

    brain storage --set "D:/Nila Assistant Storage"
    brain storage                       # where it lives, size, free space
    brain storage --fallback-c on       # allow C: if the drive is missing

The encrypted store can live on any drive. If the drive is gone, Nila refuses
instead of quietly creating an empty archive on C: - that would make a full
history look deleted. Nothing is deleted on a schedule: `brain forget <id>`
removes one record, when the owner says so.

## Hearing you (v0.2, stage 7)

    brain ears --status                 # is there a transcriber and a microphone?
    brain ears --file me.wav            # transcribe a recording (no microphone needed)
    brain ears --listen 5               # record 5 s and transcribe
    brain talk                          # press once, say it, Nila does it
    brain talk --text "nila open files" --show   # try the loop without a microphone

`brain talk` is the whole loop: hear one sentence, put the same audio through the
voice lock, then act. If the voice is not the enrolled one, the words are thrown
away unused - they are never shown, forwarded or acted on.

Three transcribers are recognised, most preferred first: **faster-whisper**,
**openai-whisper** (best Tamil, slower on CPU) and **vosk** (50 MB, weakest Tamil).
**None of them is installed by Nila** - downloading a model is the owner's decision,
and one command does it and then proves it:

    brain fix hearing           # check; prints what is missing and the exact line
    brain fix hearing --apply   # install, then transcribe a test sentence for real

    pip install faster-whisper  # or install it yourself
    pip install vosk            # plus a ta model from alphacephei.com

`brain fix` separates the three honest outcomes: `missing` (nothing installed),
`installed-waiting` (the package is there; its model downloads on first use, so a
machine with no internet cannot prove it yet) and `working` (a sentence went in and
text came out). It never reports `working` without the second half.

    brain launcher --talk --write       # a press-to-talk icon (not an always-on mic)

One press listens once, for a bounded number of seconds, then closes. On Windows
the same file also goes into the Startup folder, so the Win key then typing "Nila"
reaches it. Press-to-talk needs no window at all; for saying the name instead, see
`brain wake` above. The microphone is never opened for the life of the machine.

## Speaking Tamil (v0.2, stage 11)

    brain speak "வணக்கம் நிலா"        # speak it
    brain speak --voice ta "காலை வணக்கம்"
    brain speak --engines            # speak a real Tamil sentence through EVERY engine
    brain speak --wav out.wav "வணக்கம்"   # write a file instead of using a speaker

Three engines, in this order: **Windows SAPI** (already on Windows), **Piper**
(a binary plus a `ta` voice file) and **espeak-ng** (a system package). `--engines`
does not ask "is it installed?" - it runs the real path per engine and reports how
many bytes of audio came out, or the reason none did. A missing Tamil voice now
*refuses* and prints the fix, instead of quietly speaking English.

    tools/install_voice.py --espeak    # install espeak-ng and then prove it
    tools/install_voice.py --check     # what is here, without installing anything
    brain fix voice --apply            # the same thing, from one place

## Finding a file (v0.2, stage 7)

    brain find invoice                  # by name, under your home folder
    brain find electricity --content    # look inside text files too
    brain find bill --in "D:/Papers" --since 30

Name search skips `.git`, `node_modules`, `AppData`, the recycle bin and similar
folders. Reading inside files is opt-in, capped at 2 MB per file, and only for
text-like extensions. The answer says how many files were actually checked, so
"0 matches" after 12 files and after 120,000 are different answers. Nothing is
opened and nothing is executed - it returns paths.

## Opening an app (v0.2, stage 7)

    brain open --list                   # every name Nila knows
    brain open notepad --show           # what would run, without running it
    brain open chrome

Only names from a fixed table work (chrome, edge, firefox, notepad, calculator,
explorer, files, word, excel, settings, paint, terminal); anything else is refused
with the nearest name it does know, so "open" can never become a shell. Each app is
started with `shell=False` and its own arguments. A name that is in the table but
not installed here is reported as exactly that. To add your own:

    {"agent": {"apps": {"studio": ["code", "--new-window"]}}}

A spoken sentence is matched against a small verb list before anything runs:
"Nila open notepad", "Nila find invoice", "Nila read D:/bill.pdf", "Nila stop",
in English or Tamil. A sentence that does not match is answered with "I do not
know how to ..." - never guessed into an action.

## The small problems, and the ones that were real bugs (v0.2, stage 8)

Found by using the product, not by reading it. Each one is written here as the
behaviour that was wrong, because that is what will be checked again.

| What was wrong | What happens now |
|---|---|
| A reminder 90 minutes away said `[in 0 min]` | minutes are rounded, not truncated: `[in 2 h]`, `[in 45 min]` |
| A reminder seconds away showed a clock with no seconds | `[in 3 s]`, and the clock shows `%H:%M:%S` inside two minutes |
| A reminder that just came due said `MISSED 0 min ago` | `MISSED just now` |
| Nothing announced a reminder that came due while Nila was closed | the app says it before opening the dashboard; the **Nila Reminders** icon checks it (`brain remind --due --say`); the dashboard checks `/v1/reminders/due` in its slow lane and shows a **Due now** box with a Done button |
| "Nila remind me in 10 minutes to drink water" was refused by the icon | it is a spoken action now, parsed by code, no model |
| A Gemini model name this key does not have failed with a raw 404 | the list is read from `/models` (chat models only), the configured name is kept if it exists, and a 404 is repaired once with a real name |
| "Three months of memory, then let it go" did not exist | `brain memory --older-than 90` reports; `--purge --yes` deletes. Reminders, credentials, instructions and anything marked important are never purged, and nothing is scheduled |
| The refusal hint `--scope C:/path with spaces` could not be pasted | the scope is quoted in the hint |

Re-testing the whole product from a fresh vault:

    python scripts/retest_everything.py        # writes NILA_RETEST_RESULTS.md

It drives the vault, memory, reminders, the calculator, a full agent turn, the
voice lock (owner, stranger, restart, replay), files, video, search, apps,
connectors, retention, the icons, the tool registry and its permission gate, the
local API, the dashboard, language reading and the ZIP - and says `SKIP` with the
exact missing piece for anything that needs a microphone, a voice, a model key or
the network. A `SKIP` is not a pass.

## Your phone (v0.2, stage 9)

    brain telegram how                  # the whole setup, in five steps
    brain telegram token 123456789:ABC… # the token BotFather gave you
    brain telegram whoami               # your chat id, read from your /start
    brain telegram chat 987654321
    brain telegram test                 # a real message should arrive
    brain telegram status               # configured? queued? which chat?
    brain telegram queue | flush | off

**About the number.** A Telegram account is tied to a phone number, but a bot
never needs it. Nila needs the **bot token** (from @BotFather) and your **chat
id** - a number like `987654321` that `whoami` reads after you press Start in the
bot's chat. Nila never asks for, sees or stores your phone number, your Telegram
login or your SIM. Telegram itself will not let a bot message first, which is why
pressing Start once is part of the setup.

Whatever may be pushed can be switched off one kind at a time:
`brain telegram notify updates off` (reminders · errors · updates · reports).

**The token is a password.** It is stored in the sealed vault - never in
`config.json` - it is shown only as `123456…wXyZ`, and it is only ever sent to
`api.telegram.org`. A request to any other host is refused before it leaves.

**The phone being off is not a lost message.** If Telegram cannot be reached the
message is queued, encrypted, in the vault (newest 50), and goes out on the next
successful send or `brain telegram flush`. Reminders that came due, a failed
action, a note when Nila starts and an issue report are the four kinds.

**Both ways, while the phone window is open.** `nila-phone.bat` (or
`brain telegram listen`) holds the connection and answers messages from your
phone: `/due`, `/list`, `/done 2`, `/snooze 2 10`, `/remind in 30 min …`,
`/status`, `/help`, and free text goes through the same brain the terminal uses.
Close the window and Telegram keeps the messages until you start it again - the
reader saves its position in the vault, so a restart replays rather than drops.

Only the one chat id in your vault is obeyed. A message from any other chat is
dropped, counted and written to the audit log - never answered, never run, and
never echoed back to you (an echo would dress a stranger's words up as Nila's).

## How much of v2 is really finished (v0.2, stage 11)

    brain progress                      # every feature, with its real state
    brain progress next                 # the single most useful next step

### One more thing from the same stage

`setup.bat` now finishes the whole setup: `brain init` links your authenticator
in the same step (the secret and the current code are printed once) and reminds
you about voice enrolment, so `start-nila.bat` works the **first** time. Before
this, a brand new vault had no second factor, so the first command a new owner
ever ran was refused until they typed `brain totp`. The second factor is still
required - it simply exists from the beginning now. `brain init --no-totp` skips
it if you want to link your own key later.

`done` means the code is here and needs nothing outside this machine. `partial`
means the code is here and is waiting on an install or a key - the row says which.
`missing` means not built. The percentage is computed from that list, and each row
carries the dotted path of the code that must exist for it to count, so the table
cannot claim a feature the installation does not have. The same numbers are on the
dashboard, refreshed every 15 seconds.

## Your business work (v0.2, stage 10)

One question at a time, so nothing has to be typed twice:

    brain project list                      # what is open, and what is LATE
    brain project new "Tea shop" - a stall near the bus stand
    brain project ask "Tea shop"            # the next single question
    brain project answer "Tea shop" students walking to the bus stand
    brain project milestone "Tea shop" buy the stove --due 2026-12-01
    brain project done "Tea shop" 1
    brain project late                      # unfinished, past its day, everywhere
    brain project weekly                    # what moved, what slipped - only when asked
    brain project export "Tea shop" --folder D:/Notes

Milestones take the day you would actually type (`2026-12-01`, `01/12/2026`,
`1 Dec 2026`, `today`, `tomorrow`). A milestone without a day is still a
milestone; it just never shows as late. Nothing is guessed: `next week` is
refused with a message instead of becoming some date.

The dashboard shows the same numbers, with late milestones at the top, and has
the interview as one box - the next question and an answer field.

## Install it as an app, and move it to D:

`brain serve` now serves a web manifest and an icon, so Chrome offers
**Install as app** (taskbar icon, own window) without any extra program.

Two Windows helpers, both safe by construction:

- **`move-to-d.bat`** - copies your vault to another drive, compares every file
  by SHA-256, and only then switches `BRAIN_HOME`. It refuses while Nila is
  running, refuses to merge two data folders, and keeps the old folder as
  `vault-old` until *you* delete it.
- **`backup-to-d.bat`** - writes a dated backup, then restores it into a
  temporary folder and compares record counts. A backup that has not been
  restored is a guess, so a mismatch is reported as **BACKUP FAILED**. It
  refuses a Google Drive / OneDrive / Dropbox folder on purpose.

Neither script deletes anything.

**`install-local-ai.bat`** installs a local AI - Ollama, then `llama3.2:3b` (about
2 GB, fits a 4 GB GPU) - and then *asks the model a question* before calling it a
success. It separates the three states honestly: no Ollama, Ollama with no model,
ready. If `winget` is missing or its package id is wrong on your Windows, it sends
you to the official download page instead of inventing a command. Nila works
without it; she answers from her own rules and says so.
