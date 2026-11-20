# Nila v0.2 — start here

Nila is your private assistant. It runs on **your PC**, keeps your ideas and files
**encrypted**, and shows you **where every answer was produced** (this PC or online).
This is v0.2: the v0.1 core plus the things you asked for first — **your voice in,
Tamil out, your phone both ways, a better dashboard, and `.bat` files you can trust**.

## 1. One-time setup (about 5 minutes)

1. Extract this ZIP to `C:\Nila` (right-click → **Extract All**). You do this **once**;
   after that, nothing needs extracting again. **Do not delete the old folder** — your
   memories are not in it (they live in `%LOCALAPPDATA%\Nila\vault`).
2. On your phone install **Google Authenticator** or **Microsoft Authenticator**.
3. **Double-click `nila-start.bat` — that one file is the whole start.** It says which
   vault folder it found and where the memories file is, says whether this laptop will
   ask for a password, sets the vault up **only if there is none**, remembers the key,
   and puts the **Nila icon on the Desktop** (one double-click from then on; Windows
   asks you for the key itself: right-click the icon → Properties → *Shortcut key*).
   It stops with a readable message instead of a black window that closes.
   **The passphrase is typed once here and never again on this laptop.**
   Write the passphrase and the recovery codes on paper anyway: they are what opens
   Nila on a **different** laptop, or after a reinstall of Windows.
   `nila-first-run.bat` is the same thing with more talking — it is safe to run it
   instead, and safe to run it twice.
4. If you prefer the old way, `setup.bat` still works — it just does not remember
   the laptop, so you would type the passphrase each time. `setup.bat` will:
   - check Python (3.10 or newer) and say so in one sentence if it is missing,
   - try to install the encryption helper (`cryptography`),
   - create your vault: choose a **long passphrase** and **write down the recovery codes**
     (keep them off this PC — **one code opens the vault on its own** if the passphrase
     is forgotten; each works once),
   - show a **setup key** — in the authenticator app choose *Enter a setup key*, account name
     `Nila`, time-based — then type the 6-digit code it shows.

Your data lives in `%LOCALAPPDATA%\Nila\vault`, **outside** this folder. Replacing this folder
with a newer version never touches your data.

### The memories on D: (the big drive) — the honest version

Two different things are called "moving to D:", and Nila does both, separately:

| What you want | Double-click | What it does |
|---|---|---|
| the **memories file** on D: | `move-memories-to-d.bat` | copies `memory.db` to the folder you type, compares **every** file by SHA-256, and only then leaves `moved-to.json` in the old folder. Every start reads that pointer. The old file is kept. |
| the **whole vault** on D: | `move-to-d.bat` → type `1` | the same, for the whole folder (vault + memories). Type `2` there and it runs the file above. |

Both ask **before** copying, both can be cancelled with no change, and neither deletes
anything. If the D: drive is unplugged later, Nila says **"your data folder is not
connected"** and changes nothing — it never quietly starts an empty vault on C:.
`brain storage --set D:/folder` is the one-command form of the same idea, and it now
says where the old memories stayed instead of opening an empty store.

### Forgot the passphrase AND you have no recovery code? (start a new vault)

This is the case the owner reported: *"I do not know the password, I do not know what
a recovery code is, and Nila will not start."* One double-click now answers it.

1. Double-click **`nila-start.bat`** as usual.
2. If it cannot open the vault it shows a screen with **three ways in**, and only three:
   1. **THE PASSPHRASE** — the word you chose. Nothing appears while you type (normal);
      it is on paper, nowhere in any file on this PC, and nobody can look it up.
   2. **A RECOVERY CODE** — 8 words like `abcde-fghij`, shown **once** at setup. That
      is the only place they exist. Type one at the prompt; each works once.
   3. **START A NEW VAULT** — if you have neither.
3. Type **`NEW`** and press Enter. Nila then:
   - copies your whole old vault to `vault-old-<date>` **beside** it (kept, verified),
   - asks you for a **new passphrase** (write it on paper — at least 6 characters),
   - prints **new recovery codes** and a **new authenticator secret** (once),
   - remembers this laptop, and opens Nila.
4. Nothing is deleted, and the old folder still opens with the old passphrase if you
   ever remember it.

From the command window the same thing is: `python brain.py new-vault`.

### Forgot the passphrase? You are not locked out (and a paper code is enough)
- **You have one recovery code** (from setup): `start-nila.bat recovery` → type the
  code. That opens the vault **without the passphrase and without the phone**. Each
  code works once. Right after it, run `brain passphrase set` and choose one you will
  remember, and `brain totp` to link the authenticator again.
- **This laptop still opens Nila** (the key is remembered): run
  `python brain.py passphrase set` in `nila-cmd.bat` and choose a new one. Every
  memory stays; the old passphrase stops working everywhere.
- **It does not open and you have no code**: only the paper passphrase or a backup can
  open it. `backup-to-d.bat` restores onto a new laptop.
- **Nothing at all**: nobody can open it, including me — that is what encryption
  means. The paper passphrase, one recovery code, or a `backup-to-d.bat` copy is the
  whole list of ways back in.

### Start over with a fresh vault, in one double-click — `reset-vault.bat`

This is the plan you wrote out yourself, made safe. It is the same thing as typing
**`NEW`** on the stuck screen, but it also works when Nila *does* still open and you
simply want to begin again.

1. Close every Nila window.
2. Double-click **`reset-vault.bat`**.
3. It prints the exact vault folder it is about to replace (usually
   `%LOCALAPPDATA%\Nila\vault`) and asks you to type **`START`**. Anything else
   cancels and changes nothing.
4. It copies the old vault to `vault-old-<date>` **beside** it and compares the copy
   before it does anything else. **It never renames and never deletes** — a rename
   would leave you with one single copy, and that is the one thing that could lose
   your memories.
5. It asks for a **new passphrase** (at least 6 characters), prints new recovery codes
   and a new authenticator secret **once**, and remembers this laptop.

Then write the passphrase on paper and put the paper somewhere that is not this laptop.

### One click, no passphrase — what is real today

You asked for Nila to open with the Windows PIN and to ask for the passphrase only
once a week. Here is the honest state of that, in three lines:

| What you asked for | Real today? | What actually happens |
|---|---|---|
| no passphrase prompt on this laptop | **yes, already** | the key is kept in Windows' own protected store; you type the passphrase **once per laptop**, not once a week |
| the passphrase typed again every 7 days | **no** | there is no weekly timer; if you want the prompt back sooner, `python brain.py passphrase forget` |
| a **Windows PIN / Hello** prompt from Nila | **no — not built** | that needs a small compiled Windows helper and a real Windows test run. Nila does **not** pretend: `python brain.py winmode` prints *"Windows Hello prompt: not in this build"* and then the two things that do work |

So the way in today is: **the Nila icon on the Desktop**, or **`Ctrl+Alt+N`**, or
`nila-start.bat`. All three open Nila without a passphrase on this laptop. The Windows
PIN is still worth setting — it is what stops someone who picks up the laptop from
using *any* of those three, and `python brain.py winmode` tells you so in one line.

## 2. Every day — the `.bat` files you double-click

| File | What it does | When |
|---|---|---|
| `nila-start.bat` | **the one to double-click**: finds the vault, sets it up only if there is none, remembers this laptop, writes the Desktop icon once, opens Nila | every day |
| `move-memories-to-d.bat` | copies the memories to D:, compares every file, leaves the pointer | once, or when you change drives |
| `nila-first-run.bat` | setup + remember this laptop + check, in one double-click | once |
| `nila-easy-start.bat` | shortcut, voice, check, then Nila | every day (the easy one) |
| `setup.bat` | one-time setup on its own, safe to run twice | once |
| `start-nila.bat` | starts Nila, opens the dashboard | every day |
| `nila-phone.bat` | Nila **answers Telegram messages** from your phone | while you want the phone to work |
| `nila-cmd.bat` | a command window with Nila's settings loaded | for `python brain.py …` |
| `reset-vault.bat` | starts a brand new vault (the old one is copied, never deleted); type `START` to confirm | once, only when you want to begin again |
| `report.bat` | writes a report (no secrets inside) | when something is wrong |
| `backup-to-d.bat` | dated backup **and** a real restore check | weekly |
| `sync-host.bat` | shares this PC's memory with your **other PC** on the same Wi-Fi | on the PC that has the memory |
| `sync-join.bat` | joins this PC to the other one (type the address and the join code) | once, on the second PC |
| `sync-now.bat` | syncs now, both ways | when you have added things |

Also present: `move-to-d.bat` (move the vault to another drive by copying and verifying,
never deleting) and `install-local-ai.bat` (Ollama + a small model).

### Two PCs, one memory

1. On the PC that has your memory: double-click **`sync-host.bat`**. It prints an
   **address** (`192.168.1.5`), a **port**, and a **join code** of six groups of four.
2. On the other PC: double-click **`sync-join.bat`** and type those two things.
3. On either PC: **`sync-now.bat`** whenever you want them to catch up.

Both PCs must be on the same Wi-Fi. Nothing goes to the internet, and what travels
is already encrypted: the sharing PC cannot read it either. A public address is
refused on purpose.

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

An always-on wake word (refused on purpose: an all-day microphone - `brain wake`
is a window instead), ZIP-project editing, a native Android app, the Google OAuth
handshake for Gmail/Drive, a neural speaker model, a fine-tuned Tamil model,
Claude/OpenAI providers. Screen reading, the music player and the two-PC sync are
built (stage 12-13), so they are no longer on this list.
`python brain.py progress` lists every feature with its real state — `done`, `partial` or
`missing` — computed from the code, and it will not round up to 100%.

## 11. If something breaks

Run `report.bat` and send me its text (it contains no private content), plus the exact error
message or a screenshot. `python brain.py fix` also prints, in one place, which of voice,
hearing, microphone and phone is missing and what to do about it.
