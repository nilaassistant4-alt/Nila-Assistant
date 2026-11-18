# Nila - full re-test, 2026-11-18 11:59

**42 passed · 7 skipped (needs something not on this machine) · 0 failed**

| # | check | result | evidence |
|---|---|---|---|
| 1 | vault: initialise, unlock, session | ✅ | unlocked=True, recent audit entries=1 |
| 2 | vault: a wrong passphrase is refused | ✅ | refused: PermissionError |
| 3 | memory: remember, then recall | ✅ | stored not_qqCLQa9et9yGm-Px, 1 hit(s) on recall |
| 4 | memory: list by kind | ✅ | 2 records, kinds=['note'] |
| 5 | reminder: 90 minutes shows hours, not '0 min' | ✅ | rem_RwZlwMqUB1w3pGJH  Wed 18 Nov, 13:29  -  drink water   [in 2 h] |
| 6 | reminder: due detection | ✅ | 1 reminder(s), 1 would be due in 4 h |
| 7 | reminder: English + repeat phrasing | ✅ | 4/4 parsed |
| 8 | reminder: Tamil phrasing | ✅ | parsed '9 மணி' at 1795078800000 |
| 9 | calculator: percentages and sums, offline | ✅ | 3/3 answered; sample=[{'id': '', 'name': 'calculate', 'args': {'expression': 'what is 17% of 2400'}}] |
| 10 | agent: a full turn with reminders and alerts | ✅ | Set - rem_DbFk_H6E1yW3bNdP  Wed 18 Nov, 13:29  -  call amma   [in 2 h] |
| 11 | voice: only the enrolled voice is accepted | ✅ | owner=0.935 (owner), stranger=0.510 (other) |
| 12 | voice: the voiceprint survives a restart | ✅ | enrolled after reload=True, score=0.935 |
| 13 | voice: a challenge number cannot be replayed | ✅ | first=owner, replay=other |
| 14 | voice out: speak Tamil | ⏭️ *(needs a voice engine (Windows SAPI or Piper))* | no voice on this machine (piper=, sapi=False): no speech engine on this machine - install Piper with a Tamil voice, or r |
| 15 | voice in: speech to text | ⏭️ *(needs pip install faster-whisper (or vosk))* | no transcriber installed: microphone capture needs `pip install sounddevice numpy` |
| 16 | voice in: a WAV file (no microphone) | ⏭️ *(needs pip install faster-whisper (or vosk))* | no engine to transcribe with: no speech-to-text engine is installed; whisper: pip install openai-whisper; faster_whisper |
| 17 | file: a text/document read | ✅ | .txt, 90 chars, summary=None |
| 18 | file: a code file in one screen | ✅ | {'ok': True, 'path': '/tmp/nila-retest-uwyp72ub/sample.py', 'language': 'py', 'lines': 5, 'blank_lines': 1, 'b |
| 19 | file: a video, without loading it | ✅ | duration=60.0, plan=yes |
| 20 | search: by name and inside files | ✅ | name=1 content=2 checked=10 |
| 21 | apps: a fixed list, a preview, and a refusal | ✅ | 12 names; 'rm -rf /' refused=True; files would run ['xdg-open', '.'] |
| 22 | apps: make a folder | ✅ | {'ok': True, 'created': True, 'path': '/tmp/nila-retest-uwyp72ub/retest-folder'} |
| 23 | connectors: weather / wikipedia live | ⏭️ *(needs internet access)* | no network in this sandbox: weather=ConnectorError, wikipedia=ConnectorError |
| 24 | connectors: a missing key is said, not faked | ✅ | no key -> YouTube search needs a free Google API key: create one in Google Cloud... |
| 25 | memory: the 3-month report (nothing deleted) | ✅ | 0 record(s) older than 90 days can be removed; 0 kept because they are protected; 4 newer than the window. |
| 26 | icons: app, reminders, and press-to-talk | ✅ | nila.desktop, nila-reminders.desktop, nila-talk.desktop |
| 27 | voice commands: what may be said | ✅ | 6/6 sentences understood |
| 28 | storage: asking for D: creates nothing | ✅ | path=/tmp/nila-retest-uwyp72ub/D-drive/Nila Assistant Storage/memory.db (nothing created just by asking: True) |
| 29 | permissions: the capabilities a stranger must be asked for | ✅ | 8 capabilities, mic.listen=yes |
| 30 | strict mode: the module is present | ✅ | Any, DEFAULT_ALLOWED, Dict, Iterable, List, NEVER |
| 31 | tools: everything registered, with its capability | ✅ | 46 tools |
| 32 | permissions: a tool cannot read without a grant | ✅ | refused: permission required (needs fs.read) |
| 33 | API: status and the new 'due now' route | ✅ | status=200 unlocked=True, due=200 rows=0 |
| 34 | dashboard: it shows what is due | ✅ | html=2862B js=30204B |
| 35 | languages: detect, and explain in Tamil | ✅ | 5/5 scripts detected: {'Tamil': 'Tamil', 'Hindi': 'Hindi', 'Telugu': 'Telugu', 'Malayalam': 'Malayalam', 'Bengali': 'Ben |
| 36 | models: Gemini names come from the API | ⏭️ *(needs a Gemini key and internet)* | no key and no network here (asked for the list: 0 names) |
| 37 | models: the local model pack status | ✅ | {'ok': True, 'ollama_running': False, 'present': [{'id': 'builtin', 'label': 'Built-in engine (this device)',  |
| 38 | packaging: the ZIP carries the current code | ⏭️ | no ZIP next to this checkout (ztE) |
| 39 | packaging: version and interpreter | ✅ | version 0.1.0, python 3.11.2 |
| 40 | audit: an action leaves a trace | ✅ | 3 recent entries, marker found=True |
| 41 | telegram: one chat, queued when offline, token never printed | ✅ | sent=1, offline queued=1, token leaked=False |
| 42 | telegram: settings can be removed again | ✅ | cleared; configured=False |
| 43 | v2: the progress number is measured, not remembered | ✅ | 30/43 done (70%), 4 partial, 9 not built; next: install a transcriber (pip install faster-whisper), then spe |
| 44 | projects: one question at a time, late milestones, weekly, no guessing | ✅ | one question at a time (Problem -> Customers), 1 late, weekly=1 project(s), nonsense date refused=True |
| 45 | projects: a summary exported to a .txt you choose | ✅ | 393 chars, plain text, folder chosen by the owner |
| 46 | platform: Chrome 'Install as app' has a manifest and an icon | ✅ | manifest display=standalone, icon image/svg+xml, page links the manifest |
| 47 | storage: move-to-d copies, verifies, then switches | ✅ | exit=0, every file compared by SHA-256, old folder kept |
| 48 | backup: dated, restored, counted - and never inside Google Drive | ✅ | restored 5 records and counted them; a synced folder refused (2) |
| 49 | strict: mode can be switched | ⏭️ | module helpers: ['Any', 'DEFAULT_ALLOWED', 'Dict', 'Iterable', 'List', 'NEVER'] |

Skipped means the code path is built and says honestly what is missing; it is not a pass.
