"""Opening an app, from a fixed list - never from a command string.

The owner asked for "I say it, and it happens on my PC: open Chrome, open Word".
The obvious implementation (`subprocess.run(user_text, shell=True)`) is the single
most dangerous line that could be added to this codebase: it turns any phrase -
including one that arrived inside a web page or a memory - into a shell command.

So this module does the opposite:

* **A table, not a command.** Each app is a name the owner can say, mapped to one
  exact executable and an argument list. Say "notepad" and Notepad opens; there is
  no path from that word to `rm -rf`.
* **Only the listed apps.** An unknown word returns the list, not a guess. Adding
  an app is an edit to a JSON file the owner controls, not a spontaneous action.
* **The write paths are checked.** "new folder" and "create note" are separate,
  narrow operations under a folder the owner chooses, and they refuse to escape it.
* **Nothing runs in a shell.** `shell=False`, no string interpolation of user text,
  and the executable is resolved to an absolute path first.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

#: The default table. Keys are what may be *said*; values are what is run.
#:
#: A platform maps to a list of *candidates*, tried in order (a Linux machine may
#: have google-chrome or chromium). A candidate is either a program name or a
#: whole `argv` list, so an opener that needs an argument says so explicitly:
#: `[["xdg-open", "."]]` means run xdg-open *with* a dot - never "try xdg-open,
#: then try the dot".
DEFAULT_APPS: Dict[str, Dict[str, Any]] = {
    "chrome": {"win": ["chrome.exe"], "mac": [["open", "-a", "Google Chrome"]],
               "linux": ["google-chrome", "chromium", "chromium-browser"]},
    "edge": {"win": ["msedge.exe"], "mac": [["open", "-a", "Microsoft Edge"]],
             "linux": ["microsoft-edge"]},
    "firefox": {"win": ["firefox.exe"], "mac": [["open", "-a", "Firefox"]],
                "linux": ["firefox"]},
    "notepad": {"win": ["notepad.exe"], "mac": [["open", "-a", "TextEdit"]],
                "linux": ["gedit", "kate", "mousepad", "xed", "pluma"]},
    "calculator": {"win": ["calc.exe"], "mac": [["open", "-a", "Calculator"]],
                   "linux": ["gnome-calculator", "kcalc", "galculator"]},
    "explorer": {"win": ["explorer.exe"], "mac": [["open", "."]],
                 "linux": [["xdg-open", "."]]},
    "files": {"win": ["explorer.exe"], "mac": [["open", "."]],
              "linux": [["xdg-open", "."], ["nautilus", "."]]},
    "word": {"win": ["winword.exe"], "mac": [["open", "-a", "Microsoft Word"]],
             "linux": []},
    "excel": {"win": ["excel.exe"], "mac": [["open", "-a", "Microsoft Excel"]],
              "linux": []},
    "settings": {"win": ["ms-settings:"], "mac": [["open", "-b", "com.apple.systempreferences"]],
                 "linux": ["gnome-control-center", "systemsettings"]},
    "paint": {"win": ["mspaint.exe"], "mac": [["open", "-a", "Preview"]],
              "linux": ["gimp"]},
    "terminal": {"win": ["wt.exe", "cmd.exe"], "mac": [["open", "-a", "Terminal"]],
                 "linux": ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]},
}


class AppError(RuntimeError):
    pass


def hint_ok(close: List[str]) -> bool:
    """A 'did you mean' is only offered when it is close enough to be useful."""
    return 0 < len(close) <= 3


def _platform_key() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "win"
    if system == "darwin":
        return "mac"
    return "linux"


def load_table(config: Any = None) -> Dict[str, Dict[str, Any]]:
    """The default table, plus anything the owner added in agent.apps.

    The owner writes a *command*: `{"studio": ["code", "--new-window"]}` means run
    `code --new-window` on this machine. An entry may also be per-platform, with
    the same candidate rules as the defaults.
    """
    table = {k: dict(v) for k, v in DEFAULT_APPS.items()}
    extra = ((getattr(config, "agent", {}) or {}).get("apps") or {}) if config else {}
    for name, spec in extra.items():
        key = str(name).strip().lower()
        if isinstance(spec, str):
            table[key] = {_platform_key(): [spec]}
        elif isinstance(spec, list):
            # One argv for every platform: [program, *args]
            table[key] = {_platform_key(): [[str(x) for x in spec]]}
        elif isinstance(spec, dict):
            table[key] = {str(k): ([v] if isinstance(v, str) else list(v))
                          for k, v in spec.items()}
    return table


def _candidates(raw: Any) -> List[List[str]]:
    """Normalise one platform entry into a list of argv candidates."""
    if isinstance(raw, str):
        return [[raw]] if raw else []
    out: List[List[str]] = []
    for item in raw or []:
        if isinstance(item, str):
            if item:
                out.append([item])
        elif isinstance(item, (list, tuple)) and item:
            out.append([str(x) for x in item])
    return out


def resolve(name: str, config: Any = None) -> Dict[str, Any]:
    """Which executable 'open chrome' means here, and whether it exists."""
    key = " ".join(str(name or "").lower().split())
    if not key:
        raise AppError("which app? say its name, e.g. 'chrome' or 'notepad'")
    table = load_table(config)
    if key not in table:
        close = [k for k in table if key in k or k in key][:5]
        hint = f" Did you mean: {', '.join(close)}?" if hint_ok(close) else ""
        raise AppError(f"I do not have '{key}' in my list.{hint} "
                       f"Known: {', '.join(sorted(table))}")
    candidates = _candidates(table[key].get(_platform_key()) or [])
    if not candidates:
        raise AppError(f"'{key}' is in my list but it is not available on "
                       f"{platform.system()} - it is a Windows/macOS app")
    for argv in candidates:
        exe = argv[0]
        if exe.endswith(":"):                     # settings: protocol URI
            return {"ok": True, "name": key, "command": exe, "args": argv[1:],
                    "found": True, "uri": True}
        found = shutil.which(exe)
        if not found and platform.system().lower().startswith("win"):
            # Windows registers apps in App Paths, where `which` cannot see them.
            found = shutil.which(str(Path(exe).with_suffix(".exe")))
        if found:
            return {"ok": True, "name": key, "command": exe, "args": argv[1:],
                    "found": True, "uri": False}
    first = candidates[0]
    return {"ok": True, "name": key, "command": first[0], "args": first[1:],
            "found": False, "uri": False}


def open_app(name: str, config: Any = None, extra_args: Optional[List[str]] = None,
             dry_run: bool = False) -> Dict[str, Any]:
    """Launch one listed app, or say why it cannot be launched."""
    plan = resolve(name, config)
    if not plan["found"]:
        if dry_run:
            # A preview is allowed to describe something that is not installed:
            # that is how the owner finds out before asking for it.
            return {"ok": False, "dry_run": True, "name": plan["name"],
                    "would_run": [plan["command"], *plan["args"]], "installed": False,
                    "error": f"'{name}' is in my list but it is not installed here "
                             f"(looked for {plan['command']})"}
        raise AppError(f"'{name}' is in my list but it is not installed here "
                       f"(looked for {plan['command']})")
    args = [plan["command"], *(extra_args or plan["args"])]
    if dry_run:
        return {"ok": True, "dry_run": True, "name": plan["name"], "would_run": args,
                "installed": True}
    try:
        proc = subprocess.Popen(args, shell=False, close_fds=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError) as exc:
        raise AppError(f"could not start {name}: {exc}") from exc
    return {"ok": True, "name": plan["name"], "pid": proc.pid,
            "command": plan["command"], "note": "started; I cannot see its window"}


def new_folder(parent: str | os.PathLike, name: str) -> Dict[str, Any]:
    """Create one folder, inside the folder it was given, and nowhere else."""
    safe = " ".join(str(name or "").split())
    if not safe or any(sep in safe for sep in ("/", "\\", "..")):
        raise AppError("a folder name cannot contain /, \\ or ..")
    root = Path(parent).expanduser()
    if not root.exists() or not root.is_dir():
        raise AppError(f"{root} is not a folder")
    target = root / safe
    if target.exists():
        return {"ok": True, "already_there": True, "path": str(target)}
    try:
        target.mkdir(parents=False)
    except OSError as exc:
        raise AppError(f"could not create {target}: {exc}") from exc
    return {"ok": True, "created": True, "path": str(target)}


def note(path: str | os.PathLike, text: str, append: bool = False) -> Dict[str, Any]:
    """Write a short note to a file. Refuses anything that is not a file path."""
    p = Path(path).expanduser()
    if p.is_dir():
        raise AppError(f"{p} is a folder")
    p.parent.mkdir(parents=True, exist_ok=True)
    clean = str(text or "").strip()
    if not clean:
        raise AppError("there is nothing to write")
    with open(p, "a" if append else "w", encoding="utf-8") as fh:
        fh.write(clean + "\n")
    return {"ok": True, "path": str(p), "bytes": p.stat().st_size,
            "appended": append}


def list_apps(config: Any = None) -> List[Dict[str, Any]]:
    table = load_table(config)
    out = []
    for name in sorted(table):
        try:
            plan = resolve(name, config)
            out.append({"name": name, "command": plan["command"],
                        "installed": plan["found"]})
        except AppError:
            out.append({"name": name, "command": "", "installed": False})
    return out
