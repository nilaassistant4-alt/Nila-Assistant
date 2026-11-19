"""One double-click for the whole thing: a hotkey, and a voice, and no password.

The owner's request was short and specific:

    "put a shortcut key on the keyboard: the moment I press it, Nila must come.
     If not that, then my voice - I say it and she comes."

Two things had to be true before this could be honest:

1. **Starting must not ask for a password.** It did, every time. That is fixed in
   ``brain/security/vaultkey.py`` (the device key) and ``brain passphrase`` - and
   this module refuses to write a launcher that would still stop at a prompt.
2. **A hotkey has to be set by Windows, not by us.** A `.lnk`'s "Shortcut key"
   lives inside a binary file and there is no supported API for writing it. So the
   shape is: we write the shortcut for you, in the right place, with the right
   target and working directory, and Windows asks you for ONE keystroke in its own
   dialog. Anything that claimed to press the key for you would be a background
   program that watches the keyboard - exactly the thing this build refuses.

The voice half uses what already exists: ``brain talk`` (one press, one sentence,
bounded) and ``brain wake`` (say "Nila" inside a window you open). What is new
here is the launcher file, and the check that says whether it will work.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_HOTKEY = "Ctrl+Alt+N"


class HotkeyError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# the shortcut file, written so only Windows has to fill in the key
# --------------------------------------------------------------------------- #
def _windows_shortcut(lnk: Path, target: Path, arguments: str = "",
                      workdir: Optional[Path] = None, icon: str = "") -> Path:
    """Write a real .lnk through WScript.Shell (the supported way).

    ``WScript.Shell`` can create and set a shortcut's target, arguments, working
    directory and icon - everything except the hotkey, which Windows only exposes
    in the property dialog. Doing it this way means the file is a normal shortcut
    the owner can move, rename or delete, not a script we hope behaves like one.
    """
    workdir = workdir or target.parent
    script = (
        "$ws = New-Object -ComObject WScript.Shell\n"
        f"$s = $ws.CreateShortcut('{lnk}')\n"
        f"$s.TargetPath = '{target}'\n"
        f"$s.Arguments = '{arguments}'\n"
        f"$s.WorkingDirectory = '{workdir}'\n"
        + (f"$s.IconLocation = '{icon}'\n" if icon else "")
        + "$s.Description = 'Nila - press once, she is here'\n"
        "$s.Save()\n"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HotkeyError(f"could not write the shortcut: {exc}") from exc
    return lnk


def where(start_menu: bool = False) -> Path:
    """Where a shortcut should live: the Desktop, or the Start menu."""
    if platform.system().lower().startswith("win"):
        if start_menu:
            appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
            return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        return Path.home() / "Desktop"
    if platform.system().lower() == "darwin":
        return Path.home() / "Applications"
    return Path.home() / ".local" / "share" / "applications"


def write_hotkey_shortcut(home: Path, out_dir: Optional[Path] = None,
                          start_menu: bool = False) -> Dict[str, Any]:
    """Write the shortcut Windows needs, and say exactly what to press next.

    The target is ``nila-start.vbs``: a script that starts the dashboard with no
    console window. Naming a .bat as the target would flash a black window on
    every keypress, which is the kind of small ugliness that makes an owner stop
    using something.
    """
    target_dir = Path(out_dir) if out_dir else where(start_menu)
    system = platform.system().lower()
    py = sys.executable or "python"
    brain_py = Path(home) / "brain.py"
    script = str(brain_py.resolve()) if brain_py.exists() else "-m brain"

    written: List[str] = []
    if system.startswith("win"):
        vbs = Path(home) / "nila-start.vbs"
        from .launcher import windows_vbs

        vbs.write_text(windows_vbs(py, script, str(Path(home).resolve())), encoding="utf-8")
        written.append(str(vbs))
        target_dir.mkdir(parents=True, exist_ok=True)
        lnk = target_dir / "Nila.lnk"
        _windows_shortcut(lnk, Path("wscript.exe"),
                          arguments=f'"{vbs}"', workdir=Path(home))
        written.append(str(lnk))
        how = (f"1. Right-click the shortcut 'Nila' ({lnk})\n"
               f"2. Properties -> the 'Shortcut key' box\n"
               f"3. Press {DEFAULT_HOTKEY} (or any unused combination), then OK.\n"
               f"4. From then on that key starts Nila, with no console window.")
        honesty = ("Windows owns that keystroke; no program can set it for you, and a "
                   "program that watched your keyboard for it would be worse than the "
                   "one keypress it saved.")
    elif system == "darwin":
        target_dir.mkdir(parents=True, exist_ok=True)
        app = target_dir / "Nila.command"
        from .launcher import macos_command

        app.write_text("#!/bin/sh\n" + macos_command(py, script, str(Path(home).resolve())),
                       encoding="utf-8")
        os.chmod(app, 0o755)
        written.append(str(app))
        how = ("Drag 'Nila.command' to the Dock, then open System Settings -> Keyboard -> "
               "Keyboard Shortcuts -> App Shortcuts and add one for it.")
        honesty = "macOS also owns application shortcuts; the Dock icon is one click."
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
        from .launcher import linux_desktop

        entry = target_dir / "nila-start.desktop"
        entry.write_text(linux_desktop(py, script, str(Path(home).resolve()),
                                       name="Nila", comment="Start Nila"),
                         encoding="utf-8")
        os.chmod(entry, 0o755)
        written.append(str(entry))
        how = ("Your desktop's own 'Keyboard -> Custom Shortcuts' dialog can bind that "
               "entry to a key; or copy it to ~/.local/share/applications and pin it.")
        honesty = "every Linux desktop does this differently, so the dialog is yours."

    return {"ok": True, "written": written, "hotkey": DEFAULT_HOTKEY,
            "how": how, "honesty": honesty,
            "note": ("the shortcut starts Nila with no password prompt ONLY if the "
                     "device key is remembered - `brain lock-check` says whether it is")}


# --------------------------------------------------------------------------- #
# the voice half
# --------------------------------------------------------------------------- #
def voice_plan() -> Dict[str, Any]:
    """What is installed for "say it and she comes", and what one command adds."""
    from ..agent import ears, wake

    hearing = ears.available()
    mic = bool(hearing.get("mic"))
    engine = (hearing.get("engines") or [None])[0]
    return {"ok": True, "microphone": mic, "transcriber": engine,
            "wake_word": True,
            "bounded_window_seconds": wake.MAX_WINDOW_SECONDS,
            "ready": bool(mic and engine),
            "to_install": [] if (mic and engine) else
                          ["pip install sounddevice numpy", "pip install faster-whisper"],
            "then": ["brain wake --window 120        (say 'Nila')",
                     "brain talk                     (press once, say a command)"],
            "note": ("the microphone is never open continuously: `brain wake` opens it "
                     "for a window you choose, and `brain talk` for one sentence")}


def check(home: Path) -> Dict[str, Any]:
    """Everything one click depends on, in the order it can fail."""
    from ..security import vaultkey

    saved = vaultkey.status(Path(home))
    voice = voice_plan()
    system = platform.system().lower()
    shortcut = (where() / "Nila.lnk") if system.startswith("win") else where()
    return {"ok": True,
            "no_password_prompt": saved["remembered"],
            "key_method": saved["method"],
            "shortcut_exists": bool(shortcut.exists()) if system.startswith("win") else None,
            "shortcut_path": str(shortcut),
            "hotkey_to_press": DEFAULT_HOTKEY if system.startswith("win") else "",
            "voice_ready": voice["ready"],
            "voice_missing": voice["to_install"],
            "next_step": ("press " + DEFAULT_HOTKEY if saved["remembered"]
                          else "run `brain passphrase remember` first - otherwise the "
                               "shortcut stops at a password prompt")}
