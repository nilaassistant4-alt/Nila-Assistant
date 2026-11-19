"""Starting Nila without typing three commands, and without typing at all.

The owner's complaint, twice: "why do I have to open a terminal and type
`brain start` every time? It should come when I call it." This file is the honest
answer to that, and the honesty is the important part:

* **A wake word cannot be always-on here.** A microphone that listens 24/7 costs
  battery, needs an always-running process, and would be a privacy problem the
  owner would have to audit. So the modes offered are *triggered* ones:
  a **hotkey**, a **launcher icon**, a **hotkey on a phone** later.
* **Speaking a phrase needs to know whose voice it was.** "Nila, start" can only
  start the app if the speaker check passes - otherwise anyone who learns the
  words starts it. The voice gate is built (`brain.security.speaker`); the phrase
  is matched here, and the speaker is verified only if a voiceprint exists, which
  is reported rather than assumed.

What is generated is a small, readable file per platform:

* **Windows** - a `.vbs` that starts Nila with no console window, a `.lnk`-style
  shortcut instruction, and an optional AutoHotkey line for a hotkey.
* **Linux/macOS** - a `.desktop` entry and a shell script.

Nothing is installed silently: the file is written where the owner can read it,
and `brain launcher` prints exactly what to click and what it will run.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

#: The phrase that starts Nila when a voice trigger is used. Two words before the
#: command, so "stop" alone can never be mistaken for it.
WAKE_PHRASE = "nila start"
STOP_PHRASE = "nila stop"

#: Which word starts a command, and what it means. Kept tiny on purpose: a long
#: list of spoken commands is a long list of things a recording could trigger.
COMMANDS = {
    "nila start": "start",
    "nila stop": "stop",
    "nila lock": "lock",
    "நிலா தொடங்கு": "start",
    "நிலா நிறுத்து": "stop",
}
#: What may follow "nila" once Nila is already listening (stage 7). Deliberately
#: small and verb-first: a spoken sentence is matched against these *words*, and
#: anything that does not match is answered, never guessed at.
SPOKEN = {
    "open": ("open", "திற", "திறக்க"),
    "find": ("find", "search", "where is", "தேடு"),
    "read": ("read", "படி"),
    "calendar": ("calendar", "appointment", "நாள்காட்டி"),
    "remind": ("remind", "நினைவூட்டு"),
    "stop": ("stop", "wait", "நிறுத்து"),
}


class LauncherError(RuntimeError):
    pass


def entry_command(home: Optional[Path] = None, python: Optional[str] = None) -> List[str]:
    """The command a launcher should run, as a list, never a shell string."""
    exe = python or sys.executable or "python"
    base = [exe, "-m", "brain", "start"]
    if home:
        base[2:3] = [str(Path(home) / "brain.py")] if (Path(home) / "brain.py").exists() else base[2:3]
    # `-m brain` is the packaged entry; a checkout is started through its own
    # brain.py. Both are listed so the generated file works either way.
    return base


def plan(home: Optional[Path] = None) -> Dict[str, Any]:
    """What Nila can actually be started by, on this machine, right now."""
    system = platform.system().lower()
    py = sys.executable or "python"
    has_brain_py = bool(home and (Path(home) / "brain.py").exists())
    script = str(Path(home) / "brain.py") if has_brain_py else ""
    return {
        "ok": True,
        "platform": platform.system(),
        "python": py,
        "script": script,
        "modes": [
            {"id": "icon", "label": "Double-click an icon",
             "works": system.startswith("win") or system in ("linux", "darwin"),
             "how": "a .vbs on Windows, a .desktop entry elsewhere"},
            {"id": "hotkey", "label": "A keyboard shortcut",
             "works": system.startswith("win"),
             "how": "a .lnk with a Shortcut key, or AutoHotkey if installed"},
            {"id": "voice_phrase", "label": "Say 'Nila start'",
             "works": True,
             "how": ("needs the microphone open, which this build does not do in "
                     "the background; the phrase is recognised as soon as Nila is "
                     "given audio, and the speaker is checked when a voiceprint "
                     "exists")},
            {"id": "always_on", "label": "Always listening for the wake word",
             "works": False,
             "how": "not offered: a microphone open all day is a battery and "
                    "privacy cost the owner would have to audit"},
            {"id": "phone", "label": "A message on your phone (Telegram)",
             "works": True,
             "how": ("one bot token and your chat id: `brain telegram how`. "
                     "Reminders, failed actions and a start note go there; the "
                     "outbox is encrypted and waits when the phone is off. With "
                     "`nila-phone.bat` open, the bot also ANSWERS questions and "
                     "/due, /done, /snooze from your phone")},
        ],
        "limits": ("Nila cannot start herself before she is running. The phrase "
                   "'Nila start' works once she has audio; the icon and the hotkey "
                   "are what actually bring her up from cold. Telegram works "
                   "both ways while the phone window (`nila-phone.bat`) is open: "
                   "close that window and messages wait in Telegram instead of "
                   "being answered."),
    }


# --------------------------------------------------------------------------- #
# start with Windows: one setting, nothing resident while it is off
# --------------------------------------------------------------------------- #
STARTUP_NAME = "Nila"


def startup_path(name: str = STARTUP_NAME) -> Optional[Path]:
    """Where this OS keeps "run when I sign in", or None when it has no such place.

    A shortcut in the per-user Startup folder is the smallest honest answer to
    "I want Nila to come up with the computer": it is one file the owner can see
    and delete, it needs no registry edit and no service, and while Nila is not
    running nothing of hers is running either.
    """
    system = platform.system().lower()
    if system.startswith("win"):
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{name}.vbs"
    if system == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"com.nila.assistant.{name}.plist"
    if system == "linux":
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        return Path(base) / "autostart" / f"nila.desktop"
    return None


def write_startup(home: Path, name: str = STARTUP_NAME,
                  arguments: str = "start", want_voice: bool = False) -> Dict[str, Any]:
    """Create (or refresh) the sign-in launcher. Returns the path and what it runs.

    ``arguments`` is what the shortcut runs, and it is a parameter because the
    owner's two requests are different: "start at sign-in" wants the dashboard up
    quietly, while "the voice brings her" wants the microphone opened on purpose,
    by hand, from an icon. Opening a microphone at every sign-in without anyone
    asking is exactly what this build refuses, so the voice is never the default.
    """
    if want_voice and arguments == "start":
        raise LauncherError(
            "a sign-in shortcut must not open the microphone: that is an always-on "
            "listener arriving through the Startup folder. Use the talk icon, or "
            "`brain wake --window N`, which asks first and stops by itself.")
    target = startup_path(name)
    if target is None:
        raise LauncherError(f"this system ({platform.system()}) has no known start-at-sign-in place")
    py = sys.executable or "python"
    brain_py = Path(home) / "brain.py"
    system = platform.system().lower()
    target.parent.mkdir(parents=True, exist_ok=True)
    if system.startswith("win"):
        # Absolute paths: the Startup folder runs with the program folder as the
        # working directory, so a relative brain.py is the classic "it worked when
        # I double-clicked it" bug.
        script = str(brain_py.resolve()) if brain_py.exists() else "-m brain"
        target.write_text(windows_vbs(py, script, str(Path(home).resolve()),
                                      extra_args=[arguments]), encoding="utf-8")
    elif system == "darwin":
        script = str(brain_py.resolve()) if brain_py.exists() else "-m brain"
        args = [f"<string>{py}</string>"]
        args += (["<string>-m</string>", "<string>brain</string>"] if script == "-m brain"
                 else [f"<string>{script}</string>"])
        args.append("<string>start</string>")
        target.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>'
            '<key>Label</key><string>com.nila.assistant</string>'
            '<key>ProgramArguments</key><array>' + "".join(args) + "</array>"
            '<key>RunAtLoad</key><true/></dict></plist>\n', encoding="utf-8")
    else:
        script = str(brain_py.resolve()) if brain_py.exists() else "-m brain"
        target.write_text(linux_desktop(py, script, str(Path(home).resolve()), name="Nila",
                                        comment="Nila, started at sign-in"), encoding="utf-8")
        os.chmod(target, 0o755)
    return {"ok": True, "written": str(target), "remove": f"delete {target}",
            "note": ("This starts Nila when YOU sign in - it is a shortcut in your own "
                     "startup folder, not a service. Delete the file to stop it.")}


def remove_startup(name: str = STARTUP_NAME) -> Dict[str, Any]:
    target = startup_path(name)
    if target is None or not target.exists():
        return {"ok": True, "removed": False, "where": str(target or "n/a"),
                "note": "nothing was set to start at sign-in"}
    try:
        target.unlink()
    except OSError as exc:
        raise LauncherError(f"could not remove {target}: {exc}") from exc
    return {"ok": True, "removed": True, "where": str(target)}


def startup_status(name: str = STARTUP_NAME) -> Dict[str, Any]:
    target = startup_path(name)
    return {"ok": True, "supported": target is not None,
            "where": str(target) if target else "",
            "enabled": bool(target and target.exists())}


# --------------------------------------------------------------------------- #
# what to write
# --------------------------------------------------------------------------- #
def windows_vbs(python: str, script: str, home: str = "",
                extra_args: Optional[List[str]] = None) -> str:
    """A launcher with no console window, and no shell quoting to get wrong."""
    args = [python, script, *(["start"] if not extra_args else extra_args)]
    if home:
        args += ["--home", home]
    joined = " ".join(f'""{a}""' for a in args)
    return ("' Nila - start without a console window.\n"
            "' Generated by `brain launcher`; edit or delete it freely.\n"
            "Set sh = CreateObject(\"WScript.Shell\")\n"
            f"sh.Run \"{joined}\", 0, False\n")


def windows_shortcut_help(target_vbs: Path) -> str:
    return (
        "1. Right-click the desktop -> New -> Shortcut\n"
        f"2. Target:  wscript.exe \"{target_vbs}\"\n"
        "3. Right-click the shortcut -> Properties -> Shortcut key -> press\n"
        "   Ctrl+Alt+N (any unused combination). That is the hotkey.\n"
        "4. Optional: set Run = Minimized, and change the icon."
    )


def linux_desktop(python: str, script: str, home: str = "",
                  extra_args: Optional[List[str]] = None,
                  name: str = "Nila",
                  comment: str = "Start Nila (unlock, then open the app)",
                  terminal: bool = True) -> str:
    args = [python, script, *(["start"] if not extra_args else extra_args)]
    if home:
        args += ["--home", home]
    # The desktop-entry spec has its own quoting: a value with spaces goes inside
    # double quotes, and a literal double quote inside it is escaped. A raw shell
    # split written here makes the entry silently fail to launch.
    quoted = []
    for a in args:
        a = str(a)
        if any(c in a for c in ' "\\'):
            a = '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"'
        quoted.append(a)
    quoted = " ".join(quoted)
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={name}\n"
            f"Comment={comment}\n"
            f"Exec={quoted}\n"
            f"Terminal={'true' if terminal else 'false'}\n"
            "Categories=Utility;\n")


def macos_command(python: str, script: str, home: str = "",
                  extra_args: Optional[List[str]] = None) -> str:
    args = [python, script, *(["start"] if not extra_args else extra_args)]
    if home:
        args += ["--home", home]
    return " && ".join([f'"{a}"' for a in args])


def push_to_talk_launchers(home: Path, out_dir: Optional[Path] = None,
                            max_seconds: float = 8.0) -> Dict[str, Any]:
    """An icon for one spoken command - pressed, never resident.

    The owner's rule is no terminal typing, so hearing him has to be an icon too.
    What the icon runs is a *bounded* listen: one press, `max_seconds` of audio,
    then it closes. That is the difference between a push-to-talk and an
    always-on microphone, and only the first one is offered - which is also why
    nothing is put in Startup by this function: a `brain talk` there would open
    the microphone at sign-in, without anyone asking. Sign-in start is its own
    opt-in (`brain launcher --add-startup`), and it starts the dashboard.
    """
    system = platform.system().lower()
    py = sys.executable or "python"
    script = str(Path(home) / "brain.py")
    if not Path(script).exists():
        script = "-m brain"
    target = Path(out_dir) if out_dir else (Path.home() / "Desktop")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LauncherError(f"could not write to {target}: {exc}") from exc
    args = [py, script] if script != "-m brain" else [py, "-m", "brain"]
    args += ["talk", "--seconds", str(float(max_seconds))]
    written: Dict[str, str] = {}
    if system.startswith("win"):
        vbs = target / "Nila Talk.vbs"
        vbs.write_text(windows_vbs(py, script, str(home),
                                   extra_args=["talk", "--seconds", str(float(max_seconds))]),
                       encoding="utf-8")
        written["talk"] = str(vbs)
        # Nothing is placed in Startup here. A copy of this .vbs there would run
        # `brain talk` at every sign-in: a microphone opening before the owner
        # asked for anything, which is the one thing this build refuses. The
        # honest answer is the icon plus a Windows Shortcut key, both of which
        # only do something when pressed.
        note = ("Press the icon, say 'Nila open notepad', and it acts. For a real "
                "hotkey: right-click the icon -> Properties -> 'Shortcut key' -> "
                "press Ctrl+Alt+N. That part is Windows' own dialog, so it cannot "
                "be set from code - and nothing is put in your Startup folder, "
                "because one of those would listen at every sign-in. If you DO "
                "want Nila up at sign-in, use: brain launcher --add-startup")
    elif system == "darwin":
        sh = target / "Nila Talk.command"
        sh.write_text("#!/bin/sh\n" + macos_command(
            py, script, str(home),
            extra_args=["talk", "--seconds", str(float(max_seconds))]) + "\n")
        os.chmod(sh, 0o755)
        written["talk"] = str(sh)
        note = "Double-click Nila Talk.command. Drag it to the Dock for one click."
    else:
        script = str(Path(home).resolve() / "brain.py") if Path(script).exists() else "-m brain"
        desktop = target / "nila-talk.desktop"
        desktop.write_text(linux_desktop(
            py, script, str(Path(home).resolve()),
            extra_args=["talk", "--seconds", str(float(max_seconds))],
            name="Nila Talk", comment="Press once, say it, Nila does it"))
        os.chmod(desktop, 0o755)
        written["talk"] = str(desktop)
        note = "Double-click nila-talk.desktop, or put it in the application menu."
    return {"ok": True, "written": written, "runs": " ".join(args), "note": note,
            "bounded": True, "seconds": float(max_seconds)}


def write_launchers(home: Path, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Write the files for this platform and say where they are.

    Two icons are written, not one: the app, and a *reminder check* (`--due`)
    that runs in a few seconds and closes. That second icon is the honest answer
    to "a reminder that fires while Nila is closed" - nothing can wake a program
    that is not running, so the check runs when the owner does start something,
    says what was missed and how late, and the app itself does the same on start.
    """
    system = platform.system().lower()
    py = sys.executable or "python"
    script = str(Path(home) / "brain.py")
    if not Path(script).exists():
        script = "-m brain"
    target = Path(out_dir) if out_dir else (Path.home() / "Desktop")
    written: List[str] = []
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LauncherError(f"could not write to {target}: {exc}") from exc
    due_args = ["remind", "--due", "--say"] if script == "-m brain" else ["remind", "--due"]
    if system.startswith("win"):
        vbs = target / "Nila.vbs"
        vbs.write_text(windows_vbs(py, script, str(home)), encoding="utf-8")
        written.append(str(vbs))
        bath = target / "Nila.bat"
        bath.write_text(f'@echo off\r\n"{py}" "{script}" start\r\n', encoding="utf-8")
        written.append(str(bath))
        due = target / "Nila Reminders.vbs"
        due.write_text(windows_vbs(py, script, str(home), extra_args=due_args),
                       encoding="utf-8")
        written.append(str(due))
        note = windows_shortcut_help(vbs)
    elif system == "darwin":
        sh = target / "Nila.command"
        sh.write_text("#!/bin/sh\n" + macos_command(py, script, str(home)) + "\n")
        os.chmod(sh, 0o755)
        written.append(str(sh))
        note = "Double-click Nila.command, or add it to the Dock."
    else:
        script = str(Path(home).resolve() / "brain.py") if Path(script).exists() else "-m brain"
        desktop = target / "nila.desktop"
        desktop.write_text(linux_desktop(py, script, str(Path(home).resolve())))
        os.chmod(desktop, 0o755)
        written.append(str(desktop))
        due = target / "nila-reminders.desktop"
        due.write_text(linux_desktop(
            py, script, str(home), extra_args=due_args, name="Nila Reminders",
            comment="What came due while Nila was closed"))
        os.chmod(due, 0o755)
        written.append(str(due))
        note = ("Double-click it, or copy it to ~/.local/share/applications/ to "
                "find Nila in the application menu.")
    return {"ok": True, "written": written, "note": note,
            "runs": f'"{py}" "{script}" start'}


# --------------------------------------------------------------------------- #
# the spoken phrase
# --------------------------------------------------------------------------- #
def match_command(text: str) -> Optional[str]:
    """'nila start' -> 'start'. Nothing else is accepted, and case is ignored."""
    low = " ".join(str(text or "").lower().replace(",", " ").split())
    for phrase, action in sorted(COMMANDS.items(), key=lambda kv: -len(kv[0])):
        if low == phrase or low.startswith(phrase + " "):
            return action
    return None


def spoken_trigger(text: str, speaker_ok: bool, has_voiceprint: bool) -> Dict[str, Any]:
    """Decide what a spoken phrase may do, given whose voice it is.

    Three outcomes, and the middle one is the honest one:

    * no phrase -> nothing happens;
    * the phrase with a verified owner voice -> the action;
    * the phrase without verification -> refused, unless no voiceprint exists at
      all, in which case it is passed on with a warning instead of silently
      pretending the gate was satisfied.
    """
    action = match_command(text)
    if action is None:
        return {"ok": False, "action": None, "reason": "not a Nila command"}
    if has_voiceprint and not speaker_ok:
        return {"ok": False, "action": None,
                "reason": ("that is not the enrolled voice, so the command was "
                           "ignored - only the owner's voice may start or stop Nila")}
    return {"ok": True, "action": action,
            "warning": "" if has_voiceprint else
                       ("no voiceprint is enrolled, so I cannot tell whether this "
                        "was your voice - run `brain voice enrol`"),
            "reason": "recognised"}


def spoken_action(text: str) -> Dict[str, Any]:
    """Split one spoken sentence into an action and its argument.

    ``"nila open notepad"`` -> ``{"action": "open", "argument": "notepad"}``.
    A bare ``"nila start"`` stays the launcher phrase. Everything unmatched comes
    back as ``{"action": "", ...}`` so the caller can say "I did not catch that"
    instead of inventing an intent.
    """
    # A file name is not a sentence: "D:/bill.pdf" must keep its dot and slash,
    # so only commas become spaces here (end-of-sentence dots are stripped below
    # from the tail of an argument, never from the middle of a path).
    low = " ".join(str(text or "").lower().replace(",", " ").split())
    if not low:
        return {"action": "", "argument": "", "text": "", "reason": "nothing was said"}
    action = match_command(low)
    if action:
        return {"action": action, "argument": "", "text": low, "reason": ""}
    for word in ("nila", "நிலா"):
        if low == word:
            return {"action": "", "argument": "", "text": low,
                    "reason": "you said my name but not what to do"}
        if low.startswith(word + " "):
            low = low[len(word):].strip()
            break
    else:
        return {"action": "", "argument": low, "text": low,
                "reason": "the sentence did not start with my name"}
    for name, triggers in sorted(SPOKEN.items(), key=lambda kv: -max(len(t) for t in kv[1])):
        for trigger in sorted(triggers, key=len, reverse=True):
            if low == trigger:
                return {"action": name, "argument": "", "text": low, "reason": ""}
            if low.startswith(trigger + " "):
                argument = low[len(trigger):].strip().rstrip("?.!")
                return {"action": name, "argument": argument, "text": low, "reason": ""}
    return {"action": "", "argument": low, "text": low,
            "reason": f"I do not know how to '{low.split()[0]}' yet"}
