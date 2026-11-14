# #5 — the deep check before your testing round

**Archive:** `Nila-verification-096767fd.zip`
**SHA-256:** `096767fdbd3286ed8f031f1931586e0b5977ff22d099ae6c66fa91b5cb745d20`
**Built by:** `scripts/build_package.py` → 135 entries, 91/91 modules, 506,920 bytes
**Your checklist:** `NILA_V02_OWNER_TEST_CHECKLIST.md`

## The question you asked

*Is only testing left, or does a deep check still find unfinished work?*

**Your list: only testing is left.** Nothing in it is half-built.

**The deep check: 8 real problems, all fixed here.** Two were mine, three were
dead ends a first-time owner would hit in the first hour, one was a real
regression of the exact feature this build exists to deliver.

## The most important one

The 30/60 checkpoint **never ran**. `Vault.session` locks the vault lazily and
returns `None` at the moment the deadline passes, so `_idle_lock_check()` saw
"there is no session" and returned - and even when it did run, it was called
*after* the 423 guard. The journal would then only have had `session_lock`, so
"save the open state, then lock" was a claim, not a behaviour.

Fixed by reading the raw session (`Vault.live_session()`), running the check at
the top of every authenticated route (the status poll is the clock that notices),
and journalling `session_idle_lock` **with its checkpoint**
(`{memory: flushed, projects_open: N}`). Two tests fail if this regresses.

## The other seven

1. **A standing instruction never reached an offline answer.** The intent engine
   builds no prompt, and the router held a *second* `LocalBrain`; the rule was
   loaded onto one object and read from the other. Now the router shares the
   Brain's engine, and `LocalBrain` holds the turn's (tier-filtered) rules.
   Online turns still get none - asserted.
2. **A locked session needed a restart.** Added the dashboard `Unlock again`
   form; `POST /v1/auth/verify` refuses to swap a live session (409) so the tab's
   bearer token survives a self-lock.
3. **`/v1/status` changed shape when locked** and dropped the rule the screen
   prints. One shape now, `active: false` plus `warn_after_s`/`lock_after_s`.
4. **"Unlock with your authenticator"** to an owner who has none enrolled. The
   refusal now leads with `brain totp`, and the panel shows the full `how` list
   (the JS was discarding everything but the error string).
5. **`brain instruct "call me Anna"` crashed in argparse.** Action is required.
6. **Audit lines in the dashboard showed no reason.** Now `event · idle 60 min`,
   from an allowlist - no session token, no key hint.
7. **Dead/incorrect code:** an all-zero-key "encryption" left in `vault.py`,
   `smalltalk` as a `@staticmethod` (so it silently ignored `self.instructions`),
   a dead `reasons` list, an unused local in the ceremony and the key generator.
   A `Stop Nila` button was added because ctrl-c leaves the session in the last
   identity snapshot, which makes the *next* start ask for the passphrase.

## Docs corrected

`docs/DASHBOARD_PHASE1_3.md` (said 2 h idle and "no unlock form"),
`docs/CAPABILITIES.md` (added the two new escape hatches), `ROADMAP.md` (a
"what is actually built" table), `CHANGELOG.md`. `docs/THREAT_MODEL.md` and
`docs/ARCHITECTURE.md` were corrected in the previous build.

## Evidence from this build

| Check | Result |
|---|---|
| pytest (3.11.2) in the repo | 752 passed, 3 skipped |
| stdlib fallback in the repo | 703 passed, **0 failed**, 52 skipped |
| pytest inside the extracted archive | 752 passed, 3 skipped |
| fallback inside the extracted archive | 703 passed, 0 failed |
| live server **started from the extracted archive** | `/`, `/dashboard.css`, `/dashboard.js` 200; `/v1/status` active/warn/lock correct |
| lock → status → unlock again | 200, `active:false` keeps the rule, re-unlock with a TOTP code → 200 `mode:totp`, chat 200, touch 200 |
| new tests | session rule (11), connectors refusal path (2), dashboard escape hatches (2), offline rule (4) |

Not verified here, on purpose: a live Gemini call (no outbound TLS in this
sandbox), the Windows `.bat` run, and the browser's visual layout.

## Still not built (roadmap, not a regression)

Lessons loop, T0–T4 tiers, voice/push-to-talk, passkeys + T3 vault, phone PWA,
wake word, connector wave, native Android, `0.2.0`. `ROADMAP.md` lists them with
status so the plan cannot quietly disagree with the build.
