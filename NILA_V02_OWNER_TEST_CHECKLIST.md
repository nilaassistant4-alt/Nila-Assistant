# Nila — owner test checklist (before you start testing)

**Archive:** `Nila-verification-096767fd.zip`
**SHA-256:** `096767fdbd3286ed8f031f1931586e0b5977ff22d099ae6c66fa91b5cb745d20`
**Built:** 135 entries, 91/91 source modules, 506,920 bytes
**Tests inside the ZIP:** pytest `752 passed, 3 skipped` · stdlib fallback `703 passed, 0 failed, 52 skipped`
**Version:** still `0.1.0` on purpose

---

## 0. Answer to your question first: is only testing left?

**For the numbered items you listed — yes.** Nothing in your list is half-built:
`#2 done`, `#3 done`, Gemini key route done, session 30/60 done, and everything
below `Lessons (v0.3)` is a roadmap phase that this build does not claim.

**But "only testing" is not the same as "nothing left to find."** Before handing
over I did the deep check you asked for and found **8 more real problems** — all
fixed in this build (section 2). Two of them were mine, two were dead ends a
first-time owner would hit in the first hour.

**What is left that is NOT testing and NOT mine:**

| Left | Why |
|---|---|
| A live Gemini call | this sandbox has no outbound internet; TLS is closed before the request leaves |
| The Windows run | `.bat` files and `%LOCALAPPDATA%` only exist on your PC |
| Version `0.2.0` and the name `Nila-v0.2.0.zip` | **yours to call** — I do not bump or name the archive |
| Voice, T0–T4 tiers, lessons, PWA, wake word, connectors wave | roadmap phases, deliberately not in this build (see `ROADMAP.md` → "Phase status") |

---

## 1. What to test (in this order — each item is 2–5 minutes)

### 1.1 First run (do this first; everything else depends on it)
1. Unzip to a folder **outside** `Documents`/`Downloads`.
2. Run `start-nila.bat` → it should open the dashboard and print that the token
   was passed privately, **not printed**.
3. Ctrl-c once. Restart. → It should say the session was cleaned up.
   *(New: the dashboard has a `Stop Nila` button that locks cleanly. Use it
   instead of closing the window when you can.)*

**Fails if:** the token is printed in the console, the browser opens without a
session, or restart asks for the passphrase even though you used `Stop Nila`.

### 1.2 The 30/60 session rule (the whole point of this build)
1. Leave the dashboard open. Do **not** touch it.
2. At **30 minutes** a banner appears: "No activity for 30 minutes…" with **one**
   button, `I am still here`.
3. Click it → banner disappears and the timer restarts.
4. Leave it again to **60 minutes** (or set a short value first, see below).
   → The vault locks, the log shows `session_idle_lock` with "idle 60 min", and
   the panel shows an **`Unlock again`** form.
5. Type your passphrase + authenticator code there → it unlocks **without
   restarting Nila**.

To avoid waiting an hour:
```
python brain.py config set agent.session_warn_after 120     # 2 minutes
python brain.py config set agent.session_timeout 300        # 5 minutes
```
(Re-set them to `1800` / `3600` afterwards. The warning always stays below the lock.)

**Fails if:** the banner never appears while the dashboard is left open, or the
lock happens while you are actually chatting, or `Unlock again` says 401/409.

### 1.3 Standing instruction (#2) — including offline
1. `python brain.py instruct add "call me Anna"`.
2. Ask `what do you call me` → it should answer with **Anna**.
3. **With Ollama not running** (the normal first run) ask `hello` → it should say
   `Hello, Anna.`
4. Try `python brain.py instruct add "do not ask me for permission"` → refused.
5. In the dashboard, the same thing typed into **Standing instructions** must also
   be refused, and the refusal appears in the Audits panel.

**Fails if:** step 2 or 3 answers without the name (that was the bug fixed today),
or a rule like "never ask" is accepted.

### 1.4 Connectors + your Gemini key (#3) — the part only you can test
1. `python brain.py connectors key gemini` → hidden prompt, then it prints only
   `key_hint` (last 4) and a fingerprint. Search `identity.json` for a fragment of
   your key: **it must not be there**.
2. `python brain.py connectors on gemini` → asks for a fresh login (passphrase +
   authenticator).
3. `python brain.py connectors test gemini` → should answer in a second or two.
   **This is the one call I could not make from here.**
4. In the dashboard: `Connectors` → the same states, key shown as `••••1234` only.
5. Before you enrol an authenticator (`brain totp`), the key screen should tell you
   to run `brain totp` — not "use your authenticator" as if you had one.

**Fails if:** the key is ever displayed in full, the Test button passes without a
key, or ON works without a fresh login.

### 1.5 Online safety (the claims that must hold)
1. Send a message with a card number in Auto → it must **not** go online.
2. Choose `Online (Gemini)` with a private sentence → it must **refuse** and answer
   offline, with a visible notice.
3. `python brain.py connectors off gemini` then send Online → it must say Gemini is
   switched off, before checking for a key.

### 1.6 Permissions and refusals (5 minutes, high value)
1. Ask Nila to read a file in `C:\Windows\...` → refused, and the refusal is in the
   audit log.
2. Ask it to run a command → an approval appears in **Approvals**; deny it → it
   must stop.
3. Ask it to "remember my API key" → refused as a secret.

### 1.7 Reports
1. `python brain.py report` → check nothing sensitive is printed (no key, no
   passphrase, no memory bodies).
2. Dashboard → **What Nila can do** → "Outside services" should match the
   Connectors panel (it used to say BLOCKED even when one was on).

---

## 2. The 8 problems found in this deep check (all fixed in this ZIP)

| # | Problem | Why it mattered |
|---|---|---|
| 1 | **The 30/60 checkpoint never ran.** `Vault.session` locks and returns `None` the instant the deadline passes, so the check saw "no session" and skipped the checkpoint; the guard also sat *after* the 423. | The feature you asked for ("save project state, then lock") did nothing. It now runs on every authenticated request, reads the raw session, and is journalled — with a test that fails if it regresses. |
| 2 | **A standing instruction never reached an offline answer.** The intent engine has no prompt, and the router held a **second** `LocalBrain`, so "call me Anna" was stored, audited, shown — and ignored whenever no model was running. | #2 was only half-working on a normal first run. |
| 3 | **A locked session could only be recovered by restarting Nila.** | Every idle lock would have ended your evening. Now: `Unlock again` in the browser (`POST /v1/auth/verify`), and verifying while a session is live returns 409 so the tab's token is never swapped out. |
| 4 | **`/v1/status` changed shape when locked**, dropping `warn_after_s`/`lock_after_s`. | The banner and the rule vanished at the exact moment they were needed. |
| 5 | **The connector refusal said "unlock with your authenticator" to someone who has none.** | A dead end in the first hour. It now says `brain totp` first, and the panel shows the whole `how` list (the JS was dropping it). |
| 6 | **`brain instruct "call me Anna"` crashed** with an argparse error (the action must be `add`). | Silent trap in the docs. The error is now explicit and the README shows `add`. |
| 7 | **Audit lines in the dashboard showed only the event name** ("session_idle_lock") with no reason. | You would see that Nila locked and never know why. Now: `session_idle_lock · idle 60 min`, with no tokens or key hints exposed. |
| 8 | **Dead code and a leftover placeholder "encryption".** A blob in `vault.py` was encrypted with an all-zero key and thrown away; `smalltalk()` silently dropped the owner's rules because it was a `@staticmethod`. | The first looked like protection that did not exist; the second is why fix #2 needed a second attempt. |

Also corrected this pass: `docs/DASHBOARD_PHASE1_3.md` still described a 2-hour
idle and "no unlock form in the dashboard" (both now false), and
`ROADMAP.md` now carries a "what is *actually* built" table so the plan and the
build cannot drift again.

---

## 3. If something fails, send me

1. The step number above (e.g. `1.2 step 4`).
2. The exact message on screen and the last ~10 lines of the console.
3. `python brain.py report` output (it never prints secrets).
4. On Windows: the output of `python --version` and the folder you unzipped into.

Do not paste your API key, passphrase, or `identity.json` contents.
