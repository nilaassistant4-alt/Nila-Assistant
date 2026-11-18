# Nila v0.2 — start here

Nila is your private assistant. It runs on **your PC**, keeps your ideas and files
**encrypted**, and shows you **where every answer was produced** (this PC or online).
This is v0.2: the v0.1 core plus the things you asked for first — **your voice in,
Tamil out, your phone both ways, a better dashboard, and `.bat` files you can trust**.

## 1. One-time setup (about 5 minutes)

1. Extract this ZIP to `C:\Nila` (right-click → **Extract All**).
2. On your phone install **Google Authenticator** or **Microsoft Authenticator**.
3. Double-click **`setup.bat`**. It will:
   - check Python (3.10 or newer) and say so in one sentence if it is missing,
   - try to install the encryption helper (`cryptography`),
   - create your vault: choose a **long passphrase** and **write down the recovery codes**
     (keep them off this PC; if you lose the passphrase the data cannot be recovered),
   - show a **setup key** — in the authenticator app choose *Enter a setup key*, account name
     `Nila`, time-based — then type the 6-digit code it shows.

Your data lives in `%LOCALAPPDATA%\Nila\vault`, **outside** this folder. Replacing this folder
with a newer version never touches your data.

## 2. Every day — the six files you double-click

| File | What it does | When |
|---|---|---|
| `setup.bat` | one-time setup, safe to run twice | once |
| `start-nila.bat` | starts Nila, opens the dashboard | every day |
| `nila-phone.bat` | Nila **answers Telegram messages** from your phone | while you want the phone to work |
| `nila-cmd.bat` | a command window with Nila's settings loaded | for `python brain.py …` |
| `report.bat` | writes a report (no secrets inside) | when something is wrong |
| `backup-to-d.bat` | dated backup **and** a real restore check | weekly |

Also present: `move-to-d.bat` (move the vault to another drive by copying and verifying,
never deleting) and `install-local-ai.bat` (Ollama + a small model).

Every one of them: sets UTF-8, finds the right Python (`py -3` or `python`), says plainly if
Python is missing, keeps the window open, and quotes paths so `C:\Users\Some Name\` works.
`tests/test_bat_hygiene.py` checks all of that on every test run.

Lost your phone? Run `start-nila.bat recovery` and use one recovery code (each works once;
set up the authenticator again afterwards).

## 3. Talk to Nila, and hear Nila

Check what this PC can do, then fix what it cannot:

```
python brain.py fix            # voice, hearing, microphone, phone - what is missing
python brain.py fix --apply    # install what can be installed, then PROVE it works
```

- **Hearing you:** `python brain.py ears` shows the transcriber and the microphone;
  `python brain.py ears --listen 6` listens for six seconds and prints what it heard.
  Needs `faster-whisper` (installed by `brain fix`); the first listen downloads a small
  model, so the first run needs internet.
- **Tamil out loud:** `python brain.py speak --voice ta "வணக்கம்"` speaks Tamil.
  Nila uses **espeak-ng**, **Piper** or **Windows SAPI**, whichever is installed;
  `python brain.py speak --engines` **speaks a real Tamil sentence** through each engine
  in turn and reports, per engine, whether audio came out (and how many bytes) or why not.
  `tools/install_voice.py --espeak` installs espeak-ng for you.
- **The honest limit:** a missing voice or transcriber is reported as missing, never
  silently replaced by English or by a guess.

## 4. Your phone, both ways

One-time, with Telegram (free, 5 minutes):

1. In Telegram search **@BotFather**, send `/newbot`, pick a name. It answers with a token.
2. `python brain.py telegram token 123456789:ABC…` (stored **sealed** in the vault, shown only
   as `123456…wXyZ`, and only ever sent to `api.telegram.org`).
3. Open your bot and press **Start**, then `python brain.py telegram whoami` prints your chat id,
   then `python brain.py telegram chat <that number>`.
4. `python brain.py telegram test` — a message arrives on your phone.

Then double-click **`nila-phone.bat`** to let Nila answer. From your phone you can send:

```
/help                      what it can do
/due                       what is due now
/list                      open reminders, numbered
/done 2                    close reminder 2
/snooze 2 10               reminder 2, ten minutes later
/remind in 30 min take the tablet
/status                    vault unlocked? anything waiting?
```

Anything else is a question, answered by the same brain the terminal uses.

**Only your one chat is obeyed.** A message from any other chat is dropped, counted and
written to the audit log — never answered, never run, and never echoed back to you. Close the
phone window and Telegram keeps your messages; the reader saves its place in the vault, so
restarting replays instead of losing them. Nila never asks for your phone number.

## 5. If something is missing

One command answers "why does Nila not hear me / not speak Tamil / not answer my phone":

```
python brain.py fix              # voice, hearing, microphone, phone - what is missing
python brain.py fix --apply      # install what can be installed, then PROVE it works
python brain.py habit            # days you opened Nila, and your streak
python brain.py progress         # how much of v2 is done, measured from the code
```

`brain fix` prints three honest states: `missing`, `installed-waiting` (the package
is there, its model still has to download once) and `working`. It never says
`working` without having actually run something.

## 6. The dashboard

`start-nila.bat` opens it. What changed in v0.2:

- every panel says *loading* before its first answer, instead of an empty box,
- the clock panels refresh every 5 s and the slow ones every 20 s (it was five requests
  every 2 s, which made the page jump),
- polling **pauses while the tab is hidden** and the animation stops with it,
- the page and script are revalidated with a hash instead of re-downloaded on every visit,
- "updated 14:22:05" under the system panel, so a frozen number is visible as frozen,
- a failed request says *showing data from 40 s ago* instead of quietly leaving old numbers.

## 7. Local AI (free, private, offline) — Ollama

1. Install Ollama from **ollama.com** and keep it running.
2. Double-click **`nila-cmd.bat`** and run: `ollama pull llama3.2:3b` (about 2 GB).
3. Restart Nila. The chat label will now say *this PC — llama3.2:3b*.

Your laptop (4 GB graphics memory, 12 GB RAM) suits this 3B model. Its Tamil is weak: use it
for ideas and drafts, and use `speak --voice ta` for Tamil speech.

## 8. Online AI (Gemini) — optional, free tier, with limits

1. Sign in at **Google AI Studio** (aistudio.google.com) and create an API key.
2. Give the key to Nila **without** putting it in a file or an environment variable:
   ```
   python brain.py connectors key gemini
   ```
   It is asked for with a *hidden* prompt, stored sealed, and shown as its last four characters.
   Prove it: `python brain.py connectors test gemini`.
3. In `nila-cmd.bat`, set your real limits from AI Studio's rate-limit page:
   ```
   python brain.py config set agent.online_daily_requests 200
   python brain.py config set agent.online_rpm 10
   ```
4. Restart Nila and choose **Online (Gemini)** under the chat box.

**Privacy rules Nila enforces:** anything that looks like private data (card or ID numbers,
passwords…) is never sent online; your saved notes and project texts are not attached to online
requests; free-tier content may be used by Google to improve its products, so use Online only for
public or low-sensitivity questions. If the online model fails or the budget is gone, Nila
answers on this PC and says so.

## 9. Safety rules in this version

- The vault needs **passphrase + authenticator code**. A passphrase alone is refused.
- Anything that touches your files, photos, a command, or the network **asks you first**
  (Approvals box). Standing permission lasts 24 hours at most and is never available for
  commands, writes, exports or network.
- After Nila reads a file or gets a command's output, risky actions are **paused for the rest
  of that reply** (protects against instructions hidden in files).
- Commands: one plain command only (no `&`, `|`, `;`, `>`, `<`), run in a private workspace
  folder with secret-looking environment variables removed. This is hardening, **not** a sandbox.
- The dashboard link carries a private token that is not printed in the window. The dashboard
  only ever answers `127.0.0.1`/`localhost` on its own port.
- The phone bridge reads only the chat id sealed in your vault, and needs the vault unlocked.

## 10. Not built yet (do not expect these)

Always-on wake word (refused on purpose: an all-day microphone), screen viewing,
ZIP-project editing, music player, a phone app, Claude/OpenAI providers.
`python brain.py progress` lists every feature with its real state — `done`, `partial` or
`missing` — computed from the code, and it will not round up to 100%.

## 11. If something breaks

Run `report.bat` and send me its text (it contains no private content), plus the exact error
message or a screenshot. `python brain.py fix` also prints, in one place, which of voice,
hearing, microphone and phone is missing and what to do about it.
