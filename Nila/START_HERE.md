# Nila v0.1 — start here

Nila is your private assistant. It runs on **your PC**, keeps your ideas and files
**encrypted**, and shows you **where every answer was produced** (this PC or online).
This is version **0.1**: a working core for PC. Voice, image understanding, the phone
app and ZIP-project work come in later versions (see `ROADMAP.md`).

## 1. One-time setup (about 5 minutes)

1. Extract this ZIP to `C:\Nila` (right-click → **Extract All**).
2. On your phone install **Google Authenticator** or **Microsoft Authenticator**.
3. Double-click **`setup.bat`**. It will:
   - check Python (3.10 or newer; yours is fine),
   - try to install the encryption helper (`cryptography`),
   - create your vault: choose a **long passphrase** and **write down the recovery codes**
     (keep them off this PC; if you lose the passphrase the data cannot be recovered),
   - show a **setup key** — in the authenticator app choose *Enter a setup key*, account name
     `Nila`, time-based — then type the 6-digit code it shows. Nothing is saved until the code
     matches, so a typo cannot lock you out.

Your data lives in `%LOCALAPPDATA%\Nila\vault`, **outside** this folder. Replacing this folder
with a newer version never touches your data.

Windows may warn about running `.bat` files. Do not turn off Defender; if a warning appears,
send me its exact text.

## 2. Every day

Double-click **`start-nila.bat`**. Enter your passphrase, then the 6-digit code. Your browser opens
the Nila dashboard. Close the window to stop Nila and lock the vault.
Lost your phone? Run `start-nila.bat recovery` and use one recovery code (each works once;
set up the authenticator again afterwards).

## 3. What you can do now

- **Chat** in the middle panel. Under every answer a label says *stayed on this PC* or
  *went online*.
- **Save an idea:** type `save idea: Tea Shop - sell filter coffee online`.
- **Resume later:** `resume my project`, `what is the status of Tea Shop`,
  `add milestone to Tea Shop: pick 3 suppliers`, `note for Tea Shop: talked to two cafes`.
- **Projects panel (left):** fill the 9-block business model, set status, add milestones and
  notes, attach files (up to 8 MB each, encrypted).
- **Right side:** system health, online-AI budget, approvals, what Nila can/can't do, audit log.
- **Reports:** double-click `report.bat`, or press *Full report* in the dashboard.

Without Ollama or Gemini, Nila uses a built-in **basic** engine: it handles projects, memory
and simple commands, but it is not a language model and cannot hold a free conversation.

## 4. Local AI (free, private, offline) — Ollama

1. Install Ollama from **ollama.com** (official site) and keep it running.
2. Double-click **`nila-cmd.bat`** and run: `ollama pull llama3.2:3b` (about 2 GB).
3. Restart Nila. The chat label will now say *this PC — llama3.2:3b*.

Your laptop (4 GB graphics memory, 12 GB RAM) suits this 3B model. Close heavy browser tabs
while it runs. Its Tamil is weak: use it for ideas and drafts, not polished Tamil text.

## 5. Online AI (Gemini) — optional, free tier, with limits

1. Use the email you made for Nila and sign in at **Google AI Studio** (aistudio.google.com).
2. Create an API key. Open AI Studio's **rate limits** page and note the *requests per minute*
   and *requests per day* shown for the model you use.
3. Give the key to Nila **without** putting it in a file, an environment variable or a
   browser form:
   ```
   python brain.py connectors key gemini
   ```
   Nila asks for the key with a *hidden* prompt (nothing appears as you type, nothing is
   kept in your shell history), stores it sealed inside your encrypted vault, and shows only
   the last four characters afterwards. Prove it works with:
   ```
   python brain.py connectors test gemini
   ```
   (That sends one small fixed sentence and counts it against today's limit.)
4. Open `nila-cmd.bat` and set your real limits (numbers from AI Studio):
   ```
   python brain.py config set agent.online_daily_requests 200
   python brain.py config set agent.online_rpm 10
   ```
   Until you do, Nila uses a safe default of 20 requests/day, and always stops at 80% of the
   limit you set.
5. Restart Nila. Choose **Online (Gemini)** under the chat box when you want it.

Model names change often. If you see a "model not found" problem, copy a current Flash model
name from AI Studio and run: `python brain.py config set agent.online_model <name>`.

**Privacy rules Nila enforces:** a message that looks like private data (card or ID numbers,
passwords…) is never sent online; your saved notes and project texts are not attached to online
requests; free-tier content may be used by Google to improve its products, so use Online only for
public or low-sensitivity questions. If the online model fails or you run out of budget, Nila
answers on this PC instead and tells you.

Claude and OpenAI APIs are separate paid products (not your ChatGPT or Claude.ai app plans);
Nila v0.1 does not include them.

## 6. Safety rules in this version

- The vault needs **passphrase + authenticator code**. A passphrase alone is refused.
- Anything that touches your files, photos, a command, or the network **asks you first**
  (Approvals box). Standing permission lasts 24 hours at most and is never available for
  commands, writes, exports or network.
- After Nila reads a file/photo or gets a command's output, risky actions are **paused for the
  rest of that reply** (protects against instructions hidden in files). Send a new message to
  continue.
- Commands: one plain command only (no `&`, `|`, `;`, `>`, `<`), run in a private workspace
  folder with secret-looking environment variables removed. This is hardening, **not** a sandbox.
- The dashboard link carries a private token that is not printed in the window.

## 7. Not in v0.1 (do not expect these yet)

Voice and wake phrase, image understanding, phone access, working on ZIP projects, video
helpers, business research tools, Claude/OpenAI providers, scheduled daily/weekly reports.

## 8. If something breaks

Run `report.bat` and send me its text (it contains no private content), plus the exact error
message or a screenshot. I will fix it in v0.2.
