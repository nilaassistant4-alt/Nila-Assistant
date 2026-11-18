# Dashboard Phase 1–3 (`brain serve`)

Three-panel local dashboard served by the existing API gateway, plus one security
fix that was found while building it.

Run it:

```
brain serve                                  # prints: dashboard: http://127.0.0.1:<port>/#<token>
```

Open the printed link. The token travels in the URL **fragment**, so it is never
sent to the server and never written to a log; the page strips it immediately with
`history.replaceState`.

## Panels

| Panel | Source | Notes |
|---|---|---|
| Files (left) | `GET /v1/files` | allowlisted folders, **names only** — there is no read-content endpoint anywhere |
| Neural core (centre) | `GET /v1/metrics` | canvas animation driven by real CPU/load |
| System + Consent + Logs (right) | `/v1/status`, `/v1/metrics`, `/v1/consent`, `/v1/audit` | 2 s poll |

## New routes

| Route | Auth | Behaviour |
|---|---|---|
| `GET /`, `/dashboard.css`, `/dashboard.js` | none (contains no secrets) | same-origin static, strict CSP |
| `GET /v1/metrics` | token | works while locked, so the panel can say "vault locked" |
| `GET /v1/files` | token + **full session** | 403 in a degraded session |
| `GET /v1/consent` | token + unlocked | pending prompts, metadata only |
| `POST /v1/consent/affirm\|deny` | token + **full session** | **single-use only** |

`affirm` ignores any `remember` flag and can never create a durable grant — the
dashboard has no path to a standing permission. Every decision lands in the
hash-chained audit journal as `permission_affirmed`.

## Security posture

* **Loopback only.** `serve()` refuses any non-loopback host outright; there is no
  override flag. The old docstring claimed a `--force-public` flag existed — it never
  did. Corrected.
* **Host-header allowlist** on every request (DNS-rebinding guard) → 403.
* **Static assets are a fixed allowlist** (`dashboard.ASSETS`), so `/../brain/config.py`
  is a 404, not a traversal.
* **Headers:** `Content-Security-Policy: default-src 'none'; script-src 'self'; …`,
  `Permissions-Policy: microphone=(), camera=(), geolocation=(), payment=()`,
  `X-Frame-Options: DENY`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`. No inline script or style anywhere, so the CSP needs
  no `unsafe-inline`.
* **`Gateway.require_voice` was removed.** It was a dead parameter that shared a name
  with the live vault policy key `policy["require_voice"]`, which is what actually
  enforces voice (via `IdentityCeremony`). Setting it did nothing — a footgun.
* **Never-read list is shared, not duplicated.** The dashboard calls
  `host_tools.forbidden_reason`, the same 55-substring / 25-name / 13-suffix list the
  agent tools use. It is *not* a second, weaker copy.

## Files panel folders

Defaults: `~/Documents`, `~/Downloads`, `~/Pictures`. Override:

```
brain config set agent.dashboard_roots "C:\Users\<you>\OneDrive\Documents,C:\Users\<you>\Downloads"
```

On Windows 11 `~/Documents` is often redirected into OneDrive and the default then
points at an empty or missing folder. The panel now **explains** that instead of
showing a blank list: `/v1/files` returns a `roots` array with `exists` per configured
folder, and the UI prints `⚠ folder not found: …` plus the exact command to fix it.

Hidden files (dot-prefixed), never-read paths, and symlinks that resolve outside the
allowlist are all dropped. Listings are capped at 300 entries.

## Metrics

`psutil` if installed (recommended on Windows — it is the only reliable source there
for CPU, memory and battery). Otherwise stdlib fallbacks: `/proc/meminfo` and
`/sys/class/power_supply` on Linux, `GlobalMemoryStatusEx` / `GetSystemPowerStatus`
via ctypes on Windows (**never exercised on real Windows — treat as unverified**).
`shutil.disk_usage` works everywhere. When no CPU figure is available the bar is
labelled "Load avg" rather than pretending to be a percentage; on Windows without
`psutil` it disappears.

## Session lifetime

Sessions use a **60-minute idle timeout with a warning after 30 minutes**, and a
12-hour absolute maximum (`agent.session_timeout`, `agent.session_warn_after`). The
System panel shows live idle and absolute-maximum countdowns from `/v1/status`.

* Idle means **no owner action**: a message, a click, a CLI command. Background
  status polling never extends it (it used to - fixed in the 30/60 build).
* At 30 minutes a banner appears with one button, **"I am still here"**, which
  calls `POST /v1/session/touch`. Nothing else moves the window.
* At 60 minutes open project state is flushed, `session_idle_lock` is journalled
  with the checkpoint, and the vault locks; `/v1/files` and `/v1/consent` then
  return a clean 423.

`/v1/status` and `/v1/metrics` keep answering with the vault locked, and the
`session` object keeps its shape (`active: false`, plus `warn_after_s` and
`lock_after_s`) so the panel can still explain the rule.

**Unlocking again is a form in the dashboard** (`Unlock again`, passphrase +
authenticator code) - no restart. **Stop Nila** locks cleanly, which matters: a
ctrl-c leaves the session in the last identity snapshot, so the next start asks
for the passphrase.

Note that `/v1/files` and the consent buttons need a **full** session. Once a
voiceprint is enrolled, a passphrase-only session is degraded, so the Files panel
will 403 until you unlock by voice. Before enrollment a passphrase session is full
and the panel works. This is deliberate, not a bug.

## Security fix shipped alongside: lockout backoff

`Vault.register_failure` computed
`wait = base * 2 ** min(6, count - max_attempts)` and then immediately did
`att["count"] = 0`. The reset made the exponent **always 0**, so every lockout was a
flat 30 seconds regardless of history:

```
before:  5 guesses / 30 s, forever  =  10/min  =  ~14,400 guesses/day
after:   30, 60, 120, 240, 480, 960, then 1920 s per attempt  =  ~45 guesses/day
```

A 320× reduction. The counter is now cleared **only** by `register_success()`.
`docs/THREAT_MODEL.md` T12 had this marked ✅ closed and quoted a formula
(`base·2^(count-1)`) that never matched the code; both corrected.

No existing test covered the escalation, which is why it shipped.
`tests/test_lockout_backoff.py` (6 tests) now pins the sequence, the 2**6 cap, the
`locked_out()` report, the success reset, and the audited `lockout_seconds`.

## Also fixed: case-insensitive never-read matching

Substring and folder-name matching was case-sensitive, so on Windows (and default
macOS volumes) `…\appdata\local\google\chrome\…` slipped past
`/AppData/Local/Google/Chrome/`, and `AppData\Roaming\Microsoft\Credentials` — the
real casing of the DPAPI credential store — was readable while the lowercase spelling
was blocked. Only the extension check lowercased its input.

All three checks are now case-insensitive on every OS, and three Windows credential
locations were added: `AppData/Roaming/Microsoft/Credentials`,
`AppData/Roaming/Microsoft/Protect`, `AppData/Local/Microsoft/Vault`.
Fail-closed costs a little precision (a legitimate folder named exactly `Credentials`
is now refused); `tests/test_forbidden_case.py` (19 tests) pins both directions,
including that `Documents/credit-union-letter.txt` stays readable.

## Packaging guard

The dashboard is the first part of this project whose assets are not `.py` files, and
the ZIP builder had only ever been checked for Python modules — the exact blind spot
that once let `-x '*/updates/*'` silently swallow `brain/updates/`. The three static
files plus `dashboard.py` are now in `REQUIRED_FILES` (the build **deletes the archive
and exits 1** if any is missing), `api/static/*` is in `pyproject.toml` package-data,
and `tests/test_dashboard_packaging.py` asserts all of it by iterating
`dashboard.ASSETS`, so a future fourth asset is guarded automatically.

## Tests

386 passed with the archive present, 384 passed + 2 skipped in a clean room
(the two skips are the ZIP-integrity tests, which need an archive to inspect).

## Not done yet

* **Phase 4** — browser voice unlock and session recovery. Server side is already
  there (`/v1/auth/challenge`, `/v1/auth/verify` accept base64 WAV). Needs:
  `Permissions-Policy: microphone=(self)`, an AudioWorklet file in
  `brain/api/static/` added to both `ASSETS` and `REQUIRED_FILES`, `getUserMedia`
  with `echoCancellation`/`noiseSuppression`/`autoGainControl` all `false`, a
  clipping guard before 16-bit encoding, and an echo guard so the mic is suspended
  while Nila is speaking. **No downsampling is needed** — `extract_features` already
  calls `resample_linear` to 16 kHz, so a raw 48 kHz WAV scores identically.
* **Phase 5** — learning memory in the vault (preferences, corrections, custom
  instructions). Must pin the class to `internal` so a degraded session can still
  read Nila's own rules; otherwise the health/legal lexicon will escalate a rule like
  "never mention my diabetes" to `sensitive` and Nila will forget it.
* **TTS** — separate phase. Route it through the Web Audio API
  (`fetch → decodeAudioData → AnalyserNode`) so the CSP needs no `media-src` and the
  orb gets real amplitude instead of a fake animation.
* **Never verified:** Windows metrics, backslash/OneDrive paths, browser layout and
  animation, and the `psutil` path. Real-voice calibration is still outstanding —
  `voice_model.json` is fitted on a synthetic development population
  (`trained_on: synthetic-dev-population`), so 0.6199 is not a real-world EER.
  `scripts/calibrate_voice_model.py` exists and should be re-run against real voices.
