# Changelog

## Unreleased — stage 13: the three "not built" rows and the partials, finished (2026-11-04)

Every row below was reported as built while a part of it was not. The pattern is
the one the owner kept pointing at, so this stage is written the other way round:
each item says what was missing, then what now exists, then what still does not.

### Added
- **The wake word, without the always-on microphone — `brain wake`.**
  The old row said *not built* and gave the reason (an all-day microphone is a
  battery and a privacy cost). The reason stood; the missing half did not. A
  window is now explicit: `brain wake --window 120` opens the microphone for two
  minutes, hears "Nila" / "hey Nila" / "நிலா", and stops. The window is the
  `mic.listen` capability, asked once per opening and audited on open and close;
  the ceiling is 15 minutes and a longer one is refused in words. What matched
  comes back with its audio, because a wake word is a doorbell and the speaker
  lock is the key. `brain wake --check file.wav` scores a recording, so the
  feature is usable and provable on a machine with no microphone (the sandbox has
  none) — `brain/agent/wake.py`.
- **A use-once store that writes nothing down — `brain temp` and three tools.**
  Mandate 1 asks for a tier where a number used for a temporary task is never
  saved. There wasn't one: the only option was `remember`, and forgetting was a
  habit the model was asked to keep. `brain/agent/ephemeral.py` holds a value in
  RAM against a purpose; the record that may be written is a per-process salted
  digest and nothing else; `take()` hands it over once and then it is gone;
  expiry purges it, `brain lock` purges it, `brain temp --purge` purges it; it is
  never in `memory.db`, so it is never in a backup or a sync payload. The agent
  gets `hold_ephemeral` / `use_ephemeral` / `list_ephemeral`.
- **"Strictly per command" as a mode — `brain permission mode command`.**
  Mandate 5 wants activation strictly on demand; the build had standing grants
  that would not have satisfied a strict reading. The mode now exists: in
  `command` mode no durable grant is honoured (check AND peek, so a caller cannot
  skip the affirmation), a new standing grant is refused, and switching back
  restores what was there. `DURABLE_TTL_MS` also stopped claiming 7 days in its
  comment — it has been 24 h since stage 8.
- **Tamil liveness challenges — `brain challenge --style auto|tamil_digits|tamil_words`.**
  A Tamil-first owner was asked to read "alpha bravo cedar" aloud to prove she was
  herself. The voice maths never needed English (MFCC is text-independent); a word
  list was missing. Tamil words and Tamil numerals now ship, `auto` follows the
  owner's language, and an unknown style is refused instead of silently falling
  back to English.
- **Single-account binding for Gmail, Drive and Calendar — `brain connectors integrations`.**
  The integrations registry could describe a Gmail endpoint but nothing asserted
  *which* account, and nothing stopped a second one. Each integration may now name
  its one account; a call naming any other account, or a call on an unbound
  account integration, is refused before the network with the exact command that
  binds it. `gmail` and `drive` now ship as declared, disabled integrations with
  a `whoami` operation, so the binding can be checked against the token.
- **A device ceiling — `brain device limit N`.** Any number of devices could be
  enrolled from a bundle and none had to be the owner's. The ceiling is 3 by
  default, enforced on the side that knows the full list (the exporting vault),
  counted from the shared identity blob so it holds across hops PC1 → PC2 → PC3,
  and it can be raised on purpose. `revoke` now removes the device from both
  lists, so the slot is actually freed.

### Changed
- **The 44-row list is now 45 rows and 36 are done** (was 33): 6 partial, 3 not
  built, 80% — and the three that remain say why instead of being rounded away.
  "2 PCs, one memory" was split out of the row it shared with the Tamil model,
  because a finished feature sitting next to an unfinished one reads as half-done.
- **OCR for scanned pages and scene detection are `done`, not `partial`.**
  `--run` works the moment ffmpeg is there (`getffmpeg.bat`), and the built-in
  reader needs nothing; leaving it `partial` was the pessimistic side of the same
  mistake.
- `brain progress` no longer calls the wake word "refused by design"; it says what
  the window does and what its limits are.

### Fixed (small, and each one found by a check rather than by reading)
- The wake matcher counted three syllables in a four-syllable phrase, because a
  30 ms gap between words vanished inside a 20 ms analysis frame — a sentence
  scored as the name. Frames are 10 ms and a phrase of four syllables is refused.
- `EphemeralStore` had a slot ceiling but the CLI printed "nothing is being held"
  when a *different process* held the value. The CLI now says that plainly and
  offers `--use`, which holds and hands over in one command.
- `vault.revoke_device` removed the device from the local map only, so the shared
  count never went down and a revoked slot could never be reused.

### Still not built, and said so
- A native Android app and multi-user accounts (Telegram answers from the phone;
  a native app is a different project).
- A neural speaker model (ECAPA / x-vector) and a fine-tuned Tamil model: both
  need a model download or a labelled training set and a GPU run.
- The Google OAuth handshake itself for Gmail/Drive — the binding and the
  refusal are built, the token exchange is not.

## Unreleased — stage 12: the eight rows that said "not built", and five partials (2026-11-04)

### Added
- **OCR, without a binary — `brain ocr`.** A pure-Python PNG decoder (all five
  filters, colour types 0/2/3/4/6 at 1/2/4/8/16 bits, palette, alpha; interlaced
  refused in words), Otsu binarisation, line and word segmentation, and template
  matching that recovers case from a glyph's height against the page's capitals.
  Scanned PDF pages (Flate images) are read the same way, so `brain file scan.pdf`
  no longer answers "cannot read". `brain ocr --demo` writes a page with known
  words, reads it back and says whether they matched — the feature proves itself on
  the owner's PC. `tools/text_image.py` is the page writer. Tesseract is preferred
  automatically the moment it is installed; a page typeset in a real font is
  reported unsure rather than read confidently wrong (a test enforces that).
- **Two PCs, one memory — `sync-host.bat`, `sync-join.bat`, `sync-now.bat`.**
  `tools/sync_lan.py` makes a readable six-group join code (no O/0, I/1, L/l, S/5,
  B/8), prints the home-network address and port, proves the code against the
  running server, and saves the token next to the vault so no environment variable
  is needed. A public address is refused; the server binds 0.0.0.0 and says what
  that means. `tests/test_sync_over_http_acceptance.py` runs the real server and
  pushes a record from PC A to PC B through a socket.
- **Calendar write — `brain calendar --add/--update/--remove/--list`.** A local
  `.ics` with a stable `UID` and a rising `SEQUENCE`, so importing it again
  replaces an event instead of duplicating it; moving an event keeps its length; a
  removal leaves a `CANCELLED` tombstone so the phone removes it too. Google write
  is still refused, in words, because it needs OAuth.
- **Search by meaning — `brain search --meaning`.** Vectors are read from a local
  `.wordvec` text file (GloVe/word2vec format), so there is no download and no
  torch; coverage is reported so a query the file does not know says so instead of
  returning confident noise.
- **Screen reading — `brain screen`.** The safe half of the highest-risk row: there
  is no capture call anywhere in the module (a test reads the source to be sure),
  `Win+Shift+S` is printed, and every answer carries `watchful: false`. It reads
  the words in the picture; the meaning still needs a vision model and it says so.
- **Music player — `brain music`.** Tags read from the files (ID3v2.2/2.3/2.4,
  FLAC comments), the file name mined when there are none, the folder remembered
  once, and the PC's own player does the playing.
- **Scene cuts — `brain media scenes`, and `--trim` without re-encoding.** Cuts are
  read from the MP4 keyframe index (`stss`/`stts`) — exact scene starts with no
  decode, fast on a large file. `--trim 90,135` prints the `-c copy` command and
  writes nothing until `--run`. `getffmpeg.bat` (`tools/getffmpeg.py`) unpacks only
  `ffmpeg.exe` and `ffprobe.exe` for frame-by-frame comparison, installs nothing
  system-wide, and proves the binary runs.
- **Dashboard: "Today and next" and "Both PCs" panels**, answered by `/v1/calendar`
  and the new read-only `/v1/sync`, both executed in jsdom against the real server.
- **Eight new acceptance files**: OCR, sync over HTTP, calendar write, semantic
  search, two-PC joining, music, screen, video scenes — plus four new checks in
  `scripts/retest_everything.py`.

### Changed
- `progress`: **33 done · 8 partial · 3 not built** of 44 (was 31/5/8). The rows
  moved to `partial` say exactly what they are waiting for.
- `brain file`: a scanned PDF is read by the built-in OCR, and a PNG screenshot
  reports the words inside it while still saying its *meaning* needs a vision model.

## Unreleased — stage 11: voice in, Tamil out, phone both ways, dashboard, .bat (2026-11-04)

### Added
- **`brain.agent.phone` — Telegram, both ways.** A long-poll reader that answers
  messages from the owner's phone: `/help`, `/due`, `/list`, `/done 2`,
  `/snooze 2 10`, `/remind in 30 min …`, `/status`, and free text through the same
  `Brain.handle` the terminal uses. Only the chat id sealed in the vault is obeyed:
  a message from any other chat is dropped, counted and audited — never answered and
  never echoed back. The update offset is saved in the vault, so a restart continues
  instead of replaying; `brain telegram poll` does one round trip, `listen` loops.
- **`nila-phone.bat`** — the window that keeps the phone answered (same Vault
  ceremony as the CLI: passphrase, authenticator, recovery, idle lock).
- **Speech out, verified.** `speech.verify()` (behind `brain speak --engines`) speaks
  a real Tamil sentence through every engine and reports bytes or the reason; espeak-ng
  joins SAPI and Piper; `tools/install_voice.py` installs and proves one.
- **`brain fix [voice|hearing|mic|phone]`** — one command that checks, installs what
  can be installed, then PROVES it, with `installed-waiting` for a package whose
  model needs the network.
- **Start at sign-in** — `brain launcher --add-startup/--remove-startup` writes a
  shortcut in the owner's own Startup folder (`.vbs` on Windows, LaunchAgent on macOS,
  `~/.config/autostart` on Linux). Not a service, and `brain launcher --plan` shows it.
- **`nila-common-check.bat`** — one readable sentence when Python is missing, instead
  of a window that flashes and closes.
- **`tests/test_bat_hygiene.py`** (57 checks) — CRLF only, ASCII only, no BOM, quoted
  `%~dp0` paths, `pause` on every double-clickable script, one `NILA_PY`, one
  `BRAIN_HOME`.

- **One command to start: `brain dashboard`.** It asks for the passphrase and the
  authenticator code once, then opens the app; `start-nila.bat` runs it. Before
  this, starting Nila ended with a wall of JSON and a second command.
- **A habit streak that cannot be faked (`brain habit`, a dashboard panel).**
  Days are counted from the audit journal's real unlocks, so a background tab is
  not a day of use. Current and best streak, days in all, and the last seven days.
- **`brain config add-folder <path>`.** The dashboard's own hint used to be a
  `config set` with JSON quotes escaped for JavaScript; pasted, it stored a
  broken value. Found by pasting it.
- **`tests/test_dashboard_dom.py`** — the page is executed in a real DOM against
  the real server, and every panel must leave its loading state.

### Changed
- **The dashboard polls less and says more.** Five requests every 2 s became two
  clock panels every 5 s and the slow panels every 20 s; polling and the canvas
  animation stop while the tab is hidden; every panel shows a *loading* skeleton
  before its first answer; the system panel prints "updated HH:MM:SS", and a failed
  request says "showing data from N s ago" instead of freezing silently.
- **Static assets are revalidated, not re-downloaded** (`ETag` + `304`), and `HEAD`
  is answered with the same status as `GET` instead of `501`.
- **`ears.available()` always lists every engine** ("installed" or "install with …"),
  so an owner with one engine still learns which is best for Tamil.
- **Every `.bat`** now quotes `%~dp0`, uses `%NILA_PY%`, sets `chcp 65001`, and
  reports the failing step; `START_HERE.md` was rewritten for v0.2 with a table of
  the six files and a phone section.

### Fixed
- `brain telegram status` said the bot "does not read replies"; it now reports the
  reader, the last update id, and that only one chat is read.
- `brain launcher --plan` said Telegram could not carry commands in — it can now.
- **A refused canvas context broke the whole dashboard** (found by running the page
  in a DOM): the 2D animation threw at the top of `dashboard.js`, so every panel
  stayed on "loading". Decoration now fails alone.
- **Two panels never left "loading"** because their content was drawn *inside*
  the loading placeholder (connectors and the online-AI budget).
- **`brain launcher --talk` also wrote a copy into the Startup folder**, so
  `brain talk` - a microphone - would have opened at every sign-in. Sign-in start
  is now only the explicit `--add-startup`, and it starts the dashboard.
- **Linux shortcuts did not quote a path with a space**, and a relative `brain.py`
  was written when the home folder was relative.
- **`python -m brain.agent.__main_phone` refused `--totp`**, so the phone window
  could not get past the second factor it asks for.
- `brain status` now reports whether Nila starts at sign-in, and the days used.

## Unreleased — #4 session rule, and the docs the build disagreed with (2026-11-04)

### Added
- **30-minute warning / 60-minute idle lock** (the owner's session rule).
  `Config.warn_after` (30 min, configurable, always before the lock),
  `Session.last_activity` and `Vault.idle_seconds()`. The dashboard shows a
  banner with an "I am still here" button (`POST /v1/session/touch`); the REPL
  prints the same warning once; `brain session [--touch]` reports the rule.
- **A checkpoint before the idle lock.** When the idle deadline passes, open
  project state is flushed and the reason is audited (`session_idle_lock`)
  before the key is dropped.
- **`brain session`** and, on the API, `POST /v1/session/touch`.

### Fixed
- **A standing instruction never reached an offline answer.** With no model
  running (the normal first run) the intent engine answered from its own
  patterns, so "call me Anna" was stored, audited, shown in the panel - and
  ignored. The rule now reaches the offline engine too, and an online turn still
  gets none.
- **The 30/60 checkpoint never ran.** `Vault.session` locks and returns `None`
  as soon as the deadline passes, so the check saw "no session" and skipped the
  checkpoint; the guard also sat *after* the 423. It now runs on every
  authenticated request, reads the raw session, and is journalled with its
  checkpoint (`test_the_deadline_that_has_just_passed_still_gets_a_checkpoint`).
- **A locked session could only be recovered by restarting Nila.** The dashboard
  now has an `Unlock again` form, `POST /v1/auth/verify` refuses to swap a live
  session (409, so the tab's token survives), and `/v1/status` keeps the same
  `session` shape when locked instead of dropping the rule the screen needs.
- **The connector screen said "unlock with your authenticator" to someone who
  has none.** The refusal now says `brain totp` first, and the panel shows the
  whole `how` list instead of only the error line.
- **A ctrl-c'd session made the next start ask for the passphrase.** A `Stop
  Nila` button calls `/v1/lock` for a clean exit.
- **An auto-refresh counted as the owner being present.** The 2 s dashboard poll
  refreshed the idle deadline, so a laptop left open never locked. Only a
  message, an explicit click or a CLI action moves the window now.
- **The idle default was 2 hours**, set by an earlier build; 30/60 is the
  confirmed rule, so `CONFIG_VERSION` is 3 and the two retired *built-in*
  defaults (900 s / 7200 s) migrate to 60 minutes. A hand-set value is kept.
- **`docs/CAPABILITIES.md` documented an `agent.auto_online` switch that no
  code ever read.** Removed and replaced with the truth: Online is per message.
- **`.env.example` said to copy it to `.env`.** Nothing loads a `.env` file;
  the file now says so at the top, and names `BRAIN_HOME`'s real default.
- **The capability map called outside services BLOCKED even when one was on.**
  It now reads the connectors screen.
- **Stale counts**: `docs/ARCHITECTURE.md` said 20 tools / 25 subcommands
  (28 / 32 today) and `docs/THREAT_MODEL.md` still described a 2 h idle.
- **The "no online key" message named only an environment variable**; it now
  says the vault was checked too, and how to add a key.

## Unreleased — #3 connectors screen (2026-11-04)

One screen that answers "which AI services and outside services may Nila use,
and may they be used right now?", implemented against the owner's brief. The
acceptance tests are in `tests/test_connectors.py` (28).

### Added
- **`brain/agent/connectors.py`** — the single source of truth: the catalogue
  (built-in engine, Ollama, Gemini, an OpenAI-compatible endpoint, Anthropic),
  ON/OFF state, sealed key storage, today's usage, the fixed-prompt test call,
  and the fresh-login approval.
- **`/v1/connectors`** (GET) — the whole screen. Never returns a secret: only
  `has_key`, the last four characters and a non-reversible fingerprint.
- **`/v1/connectors/authorise`**, `/switch`, `/key` (POST/DELETE), `/test` —
  every write spends a **single-use approval** bound to this session and to one
  action.
- **`brain connectors list|on|off|key|remove-key|test|approve`**. A key is read
  from a hidden prompt (or `--key-env`) and never accepted as an argument:
  `argv` is visible in `ps` and shell history.
- **Dashboard "Connectors" panel** — state pill, toggle, Test, hosts, key
  existence and today's usage, plus an Approve form for the login check.
- **`IdentityCeremony.authorise_action` / `consume_action`** — re-authenticate
  once, spend once, audit both; grants are stored in `approvals.json` (no
  secrets; 120 s TTL), keyed by session id.
- **Sealed key storage** (`ConnectorVault`) — connector secrets live inside the
  encrypted identity blob, each sealed under a key derived from the master key.
  A locked or degraded session cannot release one.
- **Explicit OFF is honoured** by the router. An owner who never opened the
  screen keeps exactly the behaviour they had.
- **No payment, bank or card connectors.** The brief's "ChatGPT / Claude /
  Gemini" are AI services; the test asserts no such row exists.

### Fixed on the way
- `counter_for()` refused every step **after** the stored ceiling (`<=`), so
  approving an action inside the same 30-second window as the unlock failed -
  found by driving the real CLI. Equality is the correct rule; `verify()` keeps
  its own floor.
- The **CLI** accepted one authenticator code for unlimited commands: it used
  `verify()` without spending the step. It now marks the counter used, like the
  API ceremony always did (audit B6's fix never reached the main human route).
- A wrong passphrase or a stale code was refused with no trace in the journal;
  every approval attempt, success or failure, is now audited.

### Fixed after the first build
- **A key entered in the screen never reached the router.** The screen read the
  vault; the router built its provider from the environment only. An owner could
  add a Gemini key, see "key ending 7890", and still be told "no online key set".
  `ConnectorRegistry.key_material()` is now the single answer, `build_provider()`
  accepts a key from the config, and the router asks for it once per turn. A
  switched-off connector lends no key at all.
- The "no online key" message now says where to put one
  (`brain connectors key gemini`) instead of pointing only at an env variable.

### Behaviour to know
- One approval authorises exactly one action. ON and OFF are different actions,
  and the CLI's "this process just logged in" shortcut covers a single action,
  then asks for the password again.
- The **Test** button sends one fixed prompt (~120 characters, no personal
  data), counts it against the daily budget, and records a failure as usage too.

## Unreleased — #2 standing instruction memory (2026-11-04)

A new memory kind, `instruction`, for rules the owner states once and expects
applied every time ("call me Anna"). Implemented against the owner's brief; the
29 acceptance tests are in `tests/test_instruction_memory.py`.

### Added
- **`instruction` kind** (`brain/security/classify.py`, `brain/memory/store.py`).
  Applied to EVERY local turn, injected before the model sees the question -
  retrieval is relevance-ranked, so a rule used to appear only when the question
  happened to share words with it. Verified: "call me Anna" now reaches
  "what is the weather".
- **Owner-only creation.** `remember()` refuses `kind="instruction"` unless the
  caller passes `owner_instruction=True`, which only `brain instruct`, the
  dashboard panel and the `/v1/instructions` route do. The model's `remember`
  tool, a file, a web page and a tool result are all refused, and the refusal is
  returned as `{"ok": false}` rather than raised (an exception there killed the
  turn).
- **Safety filter** (`instruction_safety()`). A standing rule is applied to
  every turn, so it may not touch the permission machinery, the audit trail, the
  vault's factors or network egress. Matched on folded text (case, whitespace,
  leetspeak) so `n3ver ask for perm1ssion` is refused too. Length cap 280 chars,
  count cap 20.
- **Online isolation.** An Online turn receives NO instructions, and a degraded
  session only receives the classes its tier may read.
- **`brain instruct add|list|edit|remove`.**
- **`/v1/instructions`** (GET/POST/PATCH/DELETE) and a dashboard panel with
  Add / Edit / Remove.
- **Every change audited** (`instruction_added|edited|removed|refused`), and the
  chain still verifies.

### Fixed on the way
- The gateway had no `do_PATCH`/`do_PUT`, so `BaseHTTPRequestHandler` answered
  **501** for any PATCH before the handler was reached.
- The `remember` tool raised `InstructionError` out of the registry, killing the
  turn; it now returns a refusal dict.
- `"ignore your safety rules"` slipped past `"ignore your rules"`; the noun in
  the middle defeated the needle. Pattern set widened and covered by tests.

### Tests
- 696 passed, 3 skipped (pytest); 653 passed, 0 failed (stdlib fallback).

## Unreleased — audit remediation (2026-11-04)

Findings from an evidence-based audit (`NILA_AUDIT_REPORT_2026-11-04.md`) where every
item below was reproduced against the shipped code with a synthetic fixture before it
was fixed, and has a regression test in `tests/test_audit_*.py`.

### Security
- **Redirect credential leak (F4).** A 302 from an allow-listed integration, an
  OpenAI-compatible provider or the sync blob store carried `Authorization` /
  `x-api-key` to the new host. New `brain/util/http.py` refuses redirects on any
  credentialed request and re-checks policy on credential-free ones.
- **Path containment (F1a/F2).** `extract_to` in an update manifest could write
  outside the install root (`../../..`); the update server, sync server and installer
  used a string-prefix test that a sibling directory defeats. New
  `brain/util/safe_paths.py` is now the only containment check in the project.
- **Update authenticity (F1c).** An unsigned manifest was accepted in `tofu` mode and
  pinned nothing, so every later manifest was equally unauthenticated. Unsigned is now
  refused outside explicit `insecure` mode. An HMAC channel that pinned only a
  `key_id` (no shared secret) no longer verifies an empty key.
- **Install-time code execution (F1b).** The pre-swap import test ran package code for
  any feed. It now runs only when the signature was verified against a pinned key;
  otherwise it is skipped and reported.
- **Update publish server (F3).** Refuses to start without a token (`--open` serves
  read-only and disables publishing), requires a token on POST, and confines
  `release_dir` to `--publish-from`.
- **Never-read list (F5).** `.env`, `.env.*`, `api_keys.json`, `secrets.{json,yml}`,
  `credentials.{json,yml}`, `service-account.json`, `terraform.tfstate`, `.pgpass`,
  `*.pem`, `*.key`, `*.der`, `*.pkcs12` are now refused; templates (`.env.example`,
  `credentials.example.json`) stay readable.
- **Usage ledger (F6).** The per-minute window is persisted (a restart refunded the
  minute's budget) and a corrupt ledger fails **closed** instead of reading as zero usage.
- **Undeclared capability (F8).** `run_sync` and `check_updates` now declare `net.host`.

### Fixed
- **Step-up was unreachable (F7).** Nothing ever set `Session.step_up_ok`, so
  `export_backup` refused in every normal flow. `Ceremony.step_up()` and
  `brain unlock --step-up` now grant it, and the flag is consumed by one high-risk
  call rather than standing.
- **Wheel packaging (F9).** `packages = ["brain"]` shipped 4 modules; `nila`/`brain`
  scripts failed with `No module named 'brain.cli'`. Now `find` over `brain.*`
  (52 modules) and both entry points run from a clean install.
- **ZIP contamination (F10).** `build/` and `*.egg-info/` from a prior wheel build
  rode into the distributable. Excluded and asserted against.
- **False test failures (F11).** `scripts/run_tests.py` reported two hard failures
  instead of skips when no ZIP existed; it now honours `pytest.skip`.

### Security (v0.2 backlog batch — NILA_V02_PLAN_DRAFT_TA.md §1)
Every item below was reproduced first, then fixed, then re-probed; each has a
regression test in `tests/test_backlog_remediation.py`.

- **A2 — a degraded session opened a full store.** An API re-login that skipped the
  strong factor still built `MemoryStore(access_level="full")` with the same master
  key, so restricted rows were returned regardless of the tier the rest of the app
  believed it enforced. `MemoryStore` now takes the session and derives the level from
  it: a degraded session can never open `full`, and an expired session is refused.
- **A3 — the audit verifier verified nothing.** `verify_audit_chain()` only checked that
  each line *had* a `chain` field; it never recomputed one, so an edited payload, a
  deleted entry and a reordered journal all returned `ok=True`. The chain now commits to
  the entire entry (`_audit_body`) and the verifier recomputes every line, reporting
  `mismatched` and `problems`. Journals written before the fix are accepted via the old
  formula but counted as `legacy_unprotected`, never as tampering.
- **B6 — TOTP replay.** A used code authenticated twice. The counter is recorded on the
  vault and a code at or below it is refused.
- **B7 — malformed `Content-Length`.** `-1`/`abc` now answer `400` and an over-large
  value answers `413` immediately, instead of pinning the handler thread.
- **B8 — ledger corruption bypassed the cap.** `{"days": []}` parses as a dict, so the
  old `_corrupt()` accepted it and the counter read as zero; a wrong-typed count raised
  `ValueError` out of `usage()`. `_corrupt()` is now structural and fails **closed**.
- **B9 — the 200-record page.** A project list stopped at 200 and older projects became
  unreachable while `truncated` stayed silent. `SCAN_LIMIT` is 2000 and `listing()`
  reports truncation explicitly.
- **B10 — one approval, unlimited calls.** `IntegrationGateway` and the tool gate called
  `Broker.peek()`, which answers "would this be allowed?" and spends nothing, so an
  affirmed one-shot `net.host` grant authorised every later call. New
  `Broker.consume()` burns the approval atomically; a tool handler that owns its own
  `request()` keeps working because the gate spends any approval left over
  (`spend_unused`) in a `finally` block.
- **B11 — TOFU pinned a key that authenticated nothing.** On a symmetric (hmac-sha256)
  channel the first manifest supplied its own `key_id`, which was then "verified"
  against itself; a forged manifest was accepted forever. Trust-on-first-use now refuses
  to pin a symmetric key and tells you to supply it out of band.
- **B12 — sync lied.** `run_sync` imported `brain.cloud.sync`, a module that does not
  exist, so the tool raised `ModuleNotFoundError` on every call; `sync_status` had the
  same wrong import. An empty `sync.backend` crashed `build_store`; `status()` claimed a
  remote backend when only `sync.url` was set. Sync now reports `backend`/`remote`/
  `target`/`warnings`, `ok` follows the report's errors, and the CLI exits **1**.
- **A4 — WAL records were lost by a live backup.** `memory.db` was copied with a 4 KiB
  WAL pending, so a backup taken while the app ran restored **0** records. The export now
  uses SQLite's online `conn.backup()` into a temporary snapshot.
- **A5 — attachments were left out.** Sealed project files (`*.pbx`) were not in the
  archive, so a restored project's files were `file not found`. Format 2 bundles them
  with a `attachments_sha256` integrity claim; `brain restore` verifies both hashes and
  restores the blobs.
- **Credential rotation (backlog "கூடுதல்" items).** `brain recovery` shows the remaining
  break-glass codes and reissues a set (every old code dies); `brain change-passphrase`
  exposes `rotate_passphrase`, which had no caller at all; a non-interactive `brain totp`
  no longer silently replaces an enrolled factor (`--replace` is required).

### Tests
- 664 passed, 3 skipped (the skips require a built distributable ZIP or a crypto extra).
- `scripts/run_tests.py` (no pytest installed): 621 passed, 0 failed, 46 skipped.

## 0.1.0 (first Windows build of the new core)

### Security (Phase 0)
- Passphrase alone no longer opens the vault. An authenticator code is required; a vault with no
  second factor refuses everything except `brain totp` / `brain enroll-voice`.
- `brain totp` confirms your 6-digit code before saving the secret (a typo cannot lock you out).
- Recovery codes work as a one-time break-glass login (`--recovery`).
- Access token compared in constant time and no longer printed (browser receives it privately).
- Standing grants last 24 hours (was 7 days); network access can no longer be a standing grant.
- `run_command`: rejects chaining/redirection, runs in a private workspace folder, secret-looking
  environment variables removed.
- Taint rule: after untrusted content (file, photo, network, command output) enters a reply,
  high-risk tools are held for the rest of that reply.
- Provider addresses validated: the Gemini key is only sent to Google (or loopback); local AI must
  be on this PC.

### Features (Phase 1)
- Chat panel in the dashboard with a label showing where each answer ran.
- Router with three lanes (this PC / online / built-in engine); online only on request, only
  for non-private messages, only within budget.
- Usage ledger with daily cap, per-minute cap, stop-at-percent, 429 cool-down, failures counted.
- Gemini provider. 
- Projects: ideas, 9-block business model, milestones, notes, status, next steps, resume,
  encrypted file attachments. Works offline.
- Capability map (possible / asks first / needs setup / blocked / planned) and health report.
- Windows scripts: `setup.bat`, `start-nila.bat`, `report.bat`, `nila-cmd.bat`.

### Tests
- 42 new tests (`tests/test_v01.py`). Existing suite kept green; one test updated because network
  access is intentionally no longer a standing grant.
