"""How much of v2 is actually finished - one list, checked against the code.

The owner asks for this constantly, and the honest answer cannot be a memory of
what was promised: it has to be a list where every line points at something that
exists. So each feature carries a `check` - the thing that must be true in this
installation for it to count as done - and a `partial` sentence for the ones that
are built but waiting on something outside the code (a microphone, a key, a
phone). Three states, and no fourth:

* ``done``    - the code exists and does not need anything outside this machine.
* ``partial`` - the code exists and is honest about what it needs; it works the
                moment that thing is present (Whisper, a Piper voice, a bot token).
* ``missing`` - not built. Saying "done" here would be the lie that matters.

Nothing in this file performs an action. It reports, and `next_step()` names the
one thing that would move the number most.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

VERSION = "0.2"

#: (group, label, state, note, check) - `check` is a dotted path measured at run
#: time, so a feature cannot be marked done by editing this table alone.
FEATURES: List[Dict[str, Any]] = [
    # ---- the base ---------------------------------------------------- #
    {"group": "base", "label": "Encrypted vault, passphrase + TOTP",
     "state": "done", "check": "brain.memory.vault:Vault"},
    {"group": "base", "label": "Voice as a second factor (enrol, challenge, replay-proof)",
     "state": "done", "check": "brain.security.speaker:SpeakerLock"},
    {"group": "base", "label": "Speaker lock: only the owner's voice is acted on",
     "state": "done", "check": "brain.security.speaker:SpeakerLock"},
    {"group": "base", "label": "Permission broker: every host/file/app asks once",
     "state": "done", "check": "brain.security.permission:Broker"},
    {"group": "base", "label": "Strict mode and standing instructions",
     "state": "done", "check": "brain.security.strict:DEFAULT_ALLOWED"},
    {"group": "base", "label": "Audit journal (hash-chained)",
     "state": "done", "check": "brain.memory.vault:Vault.read_audit"},
    {"group": "base", "label": "Session expiry, idle lock, panic latch",
     "state": "done", "check": "brain.security.permission:Broker.panicked"},
    {"group": "base", "label": "Issue report you read before anything leaves",
     "state": "done", "check": "brain.issues_report:known_issues"},

    # ---- everyday use ------------------------------------------------ #
    {"group": "use", "label": "Reminders (English and Tamil, repeat, snooze)",
     "state": "done", "check": "brain.agent.reminders:parse_when"},
    {"group": "use", "label": "Reminders from the icon, and missed ones announced",
     "state": "done", "check": "brain.cli.main:_due_alerts"},
    {"group": "use", "label": "Calculator and everyday maths, offline",
     "state": "done", "check": "brain.agent.local_brain:LocalBrain"},
    {"group": "use", "label": "Calendar read (.ics, secret address masked)",
     "state": "done", "check": "brain.util.ics:masked_url"},
    {"group": "use", "label": "Memory: remember, recall, forget one",
     "state": "done", "check": "brain.memory.store:MemoryStore.search"},
    {"group": "use", "label": "Memory retention: 3-month report, purge only when told",
     "state": "done", "check": "brain.memory.retention:report"},
    {"group": "use", "label": "Chat, one-shot ask, local dashboard",
     "state": "done", "check": "brain.api.gateway:Gateway"},
    {"group": "use", "label": "Free connectors: weather, Wikipedia, YouTube, RSS",
     "state": "partial", "check": "brain.agent.web:weather",
     "note": "built; live calls were never made from here (this sandbox has no "
             "network). YouTube search needs a free Google API key."},
    {"group": "use", "label": "Speaks Tamil (espeak-ng, Piper or Windows SAPI)",
     "state": "partial", "check": "brain.util.speech:verify",
     "note": "three engines are wired and each is TESTED with a real Tamil sentence "
             "(`brain speak --engines`). One package (`brain fix voice`) or Windows "
             "SAPI is all that is missing."},
    {"group": "use", "label": "Hears you (Whisper / faster-whisper / vosk)",
     "state": "partial", "check": "brain.agent.ears:hear",
     "note": "the whole loop is wired and self-tested (`brain fix hearing`); the "
             "package and microphone install in one command, and the model downloads "
             "on its first use."},
    {"group": "use", "label": "One command that fixes and PROVES the missing pieces",
     "state": "done", "check": "brain.agent.fix:run",
     "note": "`brain fix` checks, installs into this exact Python, then runs the real "
             "path: a Tamil sentence spoken, a sentence transcribed."},

    # ---- files and the machine --------------------------------------- #
    {"group": "files", "label": "Reads any file: PDF, DOCX, XLSX, PPTX, code",
     "state": "done", "check": "brain.agent.media:read_document"},
    {"group": "files", "label": "Video: duration and edit commands without loading it",
     "state": "done", "check": "brain.agent.media:analyze_video"},
    {"group": "files", "label": "File search by name, and inside text files",
     "state": "done", "check": "brain.util.finder:search"},
    {"group": "files", "label": "Opens apps from a fixed list (never a shell)",
     "state": "done", "check": "brain.util.apps:open_app"},
    {"group": "files", "label": "Storage on D:, refuses instead of writing to C:",
     "state": "done", "check": "brain.config:StorageUnavailable"},
    {"group": "work", "label": "Business projects: idea, 9-block canvas, notes",
     "state": "done", "check": "brain.projects.store:ProjectStore"},
    {"group": "work", "label": "Guided interview, one question at a time",
     "state": "done", "check": "brain.projects.store:ProjectStore.interview"},
    {"group": "work", "label": "Milestone due days, and late ones put first",
     "state": "done", "check": "brain.projects.store:ProjectStore.late_milestones"},
    {"group": "work", "label": "Weekly summary, only when you ask",
     "state": "done", "check": "brain.projects.store:ProjectStore.weekly"},
    {"group": "work", "label": "Export a project summary to a .txt you choose",
     "state": "done", "check": "brain.projects.store:ProjectStore.export_summary"},
    {"group": "platform", "label": "Installable desktop app (Chrome \"Install as app\")",
     "state": "done", "note": "manifest + icon are served; the taskbar icon is "
                              "Chrome's, so it needs no tray code",
     "check": "brain.api.dashboard:ASSETS"},
    {"group": "platform", "label": "install-local-ai.bat: Ollama + a model, checked",
     "state": "done", "note": "installs nothing it cannot verify; without it Nila "
                              "answers from her own rules and says so",
     "check": "tools.install_local_ai:status"},
    {"group": "platform", "label": "move-to-d.bat: copy, verify, then switch",
     "state": "done", "note": "Windows run unverified here: the copy is checked "
                              "here, the .bat itself needs your PC",
     "check": "tools.move_storage:move"},
    {"group": "platform", "label": "backup-to-d.bat: dated backup + restore check",
     "state": "done", "note": "refuses a Google Drive/OneDrive folder on purpose",
     "check": "tools.backup_check:backup"},
    {"group": "files", "label": "Icons: app, reminders, press-to-talk",
     "state": "done", "check": "brain.util.launcher:push_to_talk_launchers"},

    # ---- telling the owner ------------------------------------------- #
    {"group": "reach", "label": "Telegram: Nila writes to your phone",
     "state": "partial", "check": "brain.agent.telegram:Telegram.send",
     "note": "built, with an encrypted outbox when the phone is off. Needs a bot "
             "token and your chat id - `brain telegram how`."},
    {"group": "reach", "label": "Telegram two-way (you message the bot, it answers)",
     "state": "partial", "check": "brain.agent.phone:PhoneBridge",
     "note": "the reader is built and tested both ways: one chat only, sealed in the "
             "vault, offset saved, /due /done /snooze /remind and free text through "
             "the same brain as the terminal. What is missing is YOUR bot token and "
             "chat id (`brain telegram how`), then `nila-phone.bat` keeps it open."},

    # ---- left out on purpose, or built in the safe half --------------- #
    {"group": "later", "label": 'Screen viewing (screenshot -> read it back)', "state": 'done', "check": 'brain.agent.screen:read',
     "note": 'built as the safe half: Nila never captures your screen. You press Win+Shift+S, give her the picture (`brain screen`), and she reads the words in it. What the picture *means* still needs a vision model.'},
    {"group": "later", "label": 'Wake word ("Nila"), inside a window you open', "state": 'done',
     "check": 'brain.agent.wake:WakeWindow',
     "note": 'say the name instead of pressing a key: `brain wake --window 120` opens the '
             'microphone for two minutes, hears "Nila" (or "hey Nila" / "நிலா"), and stops. '
             'The window is a capability asked once per opening, it closes itself, and the '
             'ceiling is 15 minutes - an always-on microphone is still refused, on purpose. '
             'What matched comes back with its audio so the speaker lock judges it first. '
             'No microphone? `brain wake --check file.wav` scores a recording. LIMITS says '
             'plainly that this is a shape matcher, not a neural spotter.'},
    {"group": "later", "label": 'Music player', "state": 'done', "check": 'brain.agent.music:library',
     "note": "plays the songs already on your PC (`brain music`): tags read from the files, the folder remembered once, and Windows' own player does the playing. No account, no download, no background daemon."},
    {"group": "later", "label": 'Gmail / Drive / Calendar write (OAuth)', "state": 'partial', "check": 'brain.util.ics:add_event',
     "note": 'calendar is WRITTEN (local .ics, stable UID, rising SEQUENCE, `brain calendar --add`), '
             'and Gmail + Drive now exist as declared, disabled integrations with single-account '
             'binding: an unbound account, or a call naming any other account, is refused before '
             'the network (`brain connectors integrations bind gmail you@gmail.com`). What is still '
             'missing is the Google OAuth handshake itself - the build will not pretend a token '
             'it does not have.'},
    {"group": "later", "label": 'Phone app (Android) and multi-user', "state": 'missing',
     "note": 'v3. Telegram already answers from the phone (`nila-phone.bat`), which is the part he asked for; a native app is a different project.'},
    {"group": "later", "label": 'Neural speaker model (ECAPA / x-vector)', "state": 'missing',
     "note": 'v3: would raise the ceiling above the current MFCC+pitch voiceprint, and it needs a neural model download, so it is not pretended here.'},
    {"group": "later", "label": 'OCR for scanned pages, and ffmpeg scene detect', "state": 'done', "check": 'brain.agent.ocr:read',
     "note": "both halves are here without a binary: scanned PDF pages are read by the built-in OCR (`brain ocr`, pure-Python PNG decode) and scene cuts are read from the video's own keyframe index (`brain media scenes`). What still wants a decoder: comparing every frame to the next, and finding silence - getffmpeg.bat installs ffmpeg, then --run cuts without re-encoding."},
    {"group": "later", "label": 'Two PCs, one memory (same Wi-Fi)', "state": 'done',
     "check": 'tools.sync_lan:join_code',
     "note": 'sync-host.bat on one PC prints a six-group join code, sync-join.bat on the other '
             'takes the address and the code, sync-now.bat catches up. Ciphertext end to end, '
             'nothing to the internet, same Wi-Fi only. This used to sit in the same row as the '
             'Tamil model, which made a finished feature look half-done.'},
    {"group": "later", "label": 'Fine-tuned Tamil model', "state": 'missing',
     "note": 'v3: a Tamil model tuned on your own speech needs a labelled training set and a GPU '
             'run. The Tamil that exists now is a pipeline (detect, answer, speak) with '
             'off-the-shelf parts - saying otherwise would be the lie that matters.'},
]

#: Fill these in and the `partial` rows above become `done`. Kept separate so the
#: owner can see that the remaining work is an install, not a rewrite.
EXTERNAL_NEEDS: List[Dict[str, str]] = [
    {"what": "speech to text", "run": "pip install faster-whisper",
     "then": "brain ears --file me.wav"},
    {"what": "microphone capture", "run": "pip install sounddevice numpy",
     "then": "brain ears --listen 5"},
    {"what": "a Tamil voice, on Windows", "run": "use Windows (SAPI is built in)",
     "then": "brain speak \"வணக்கம்\""},
    {"what": "a Tamil voice, offline", "run": "pip install piper-tts + a ta voice file",
     "then": "brain speak \"வணக்கம்\""},
    {"what": "phone notifications", "run": "brain telegram how",
     "then": "brain telegram test"},
]


def _exists(dotted: str) -> bool:
    """Does this part of Nila really exist here? Imported, then walked."""
    if not dotted:
        return False
    module_path, _, attr = dotted.partition(":")
    try:
        module = __import__(module_path, fromlist=["__name__"])
    except Exception:
        return False
    target: Any = module
    for piece in attr.split(".") if attr else []:
        target = getattr(target, piece, None)
        if target is None:
            return False
    return True


def report(probe: bool = True) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for row in FEATURES:
        item = dict(row)
        if probe and row.get("check") and not _exists(row["check"]):
            # A row whose code is missing cannot be done, whatever the table says.
            item["state"] = "missing"
            item["note"] = f"code not found: {row['check']}"
        rows.append(item)
    done = sum(1 for r in rows if r["state"] == "done")
    partial = sum(1 for r in rows if r["state"] == "partial")
    missing = sum(1 for r in rows if r["state"] == "missing")
    total = len(rows) or 1
    return {"ok": True, "version": VERSION, "features": rows,
            "total": len(rows), "done": done, "partial": partial, "missing": missing,
            "percent": round(100.0 * done / total),
            "percent_with_partial": round(100.0 * (done + partial) / total),
            "limits": [f"{n['what']}: {n['run']}" for n in EXTERNAL_NEEDS],
            "next": next_step(rows),
            "honest": ("`done` means the code is here and needs nothing outside this "
                       "machine. `partial` means the code is here and is waiting on "
                       "an install or a key. `missing` means not built.")}


def next_step(rows: Optional[List[Dict[str, Any]]] = None) -> str:
    """The single most useful next move, said as a sentence, not a plan."""
    rows = rows or FEATURES
    partial = [r for r in rows if r.get("state") == "partial"]
    if any("Hears you" in r["label"] for r in partial):
        return "run `brain fix` once - it installs the transcriber and proves it works"
    if any("phone" in r["label"].lower() for r in partial):
        return "give Nila a Telegram bot token so reminders reach your phone"
    missing = [r for r in rows if r.get("state") == "missing"
               and r.get("group") == "later"]
    if missing:
        return f"start v3 with: {missing[0]['label']}"
    return "v2 is complete on this machine - re-test it on yours"
