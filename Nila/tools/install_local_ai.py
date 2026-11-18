#!/usr/bin/env python3
"""Install a local AI (Ollama + a small model) for Nila - and say honestly what happened.

Called by ``install-local-ai.bat``. Steps, each one checked before the next:

1. Is Ollama already answering on this PC? Then nothing is downloaded.
2. Is ``winget`` available? If yes, install Ollama with it.
3. Is ``ollama`` runnable now? If not, print the one official download link and
   stop - a script that pretends it installed something is worse than one that
   says "open this page".
4. Pull a model that fits 4 GB of VRAM, and verify it answers a real prompt.

Nothing here is required for Nila to work: without a model she answers locally
from her own rules and says so. This only makes her a language model too.

    python tools/install_local_ai.py --check
    python tools/install_local_ai.py --install
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

OLLAMA = "http://127.0.0.1:11434"
MODEL = "llama3.2:3b"
WINGET_ID = "Ollama.Ollama"
DOWNLOAD = "https://ollama.com/download"


def _http_json(url: str, payload: Optional[Dict[str, Any]] = None,
               timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def status(url: str = OLLAMA) -> Dict[str, Any]:
    """Three states the dashboard has to tell apart, not two."""
    tags = _http_json(f"{url}/api/tags")
    if tags is None:
        return {"running": False, "models": [], "state": "no-ollama",
                "why": "nothing is answering on 127.0.0.1:11434",
                "next": f"run install-local-ai.bat, or download it from {DOWNLOAD}"}
    names = [str(m.get("name") or m.get("model") or "") for m in tags.get("models") or []]
    if not names:
        return {"running": True, "models": [], "state": "no-model",
                "why": "Ollama is running but has no model yet",
                "next": f"ollama pull {MODEL}"}
    return {"running": True, "models": names, "state": "ready",
            "why": "", "next": ""}


def _run(cmd: List[str], timeout: float = 1800) -> Dict[str, Any]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "code": 127, "out": "", "err": f"{cmd[0]} is not on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": 124, "out": "", "err": f"{cmd[0]} timed out"}
    except OSError as exc:
        return {"ok": False, "code": 1, "out": "", "err": str(exc)}
    return {"ok": done.returncode == 0, "code": done.returncode,
            "out": (done.stdout or "").strip(), "err": (done.stderr or "").strip()}


def install(dry_run: bool = False) -> int:
    here = status()
    print(f"step 1  Ollama now: {here['state']}" + (f" ({here['why']})" if here["why"] else ""))
    if here["state"] == "ready":
        print(f"        already working with: {', '.join(here['models'][:4])}")
        return 0
    if here["state"] == "no-model":
        return pull(dry_run)

    winget = shutil.which("winget")
    print(f"step 2  winget: {winget or 'not found'}")
    if dry_run:
        print(f"        would install {WINGET_ID} and pull {MODEL}")
        return 0
    if not winget:
        print(f"        open {DOWNLOAD} and install Ollama, then run this again.")
        return 3
    print(f"        installing {WINGET_ID} (this downloads ~700 MB)…")
    got = _run([winget, "install", "--id", WINGET_ID, "-e", "--accept-source-agreements",
                "--accept-package-agreements"])
    print(f"        winget exit {got['code']}" + (f": {got['err'][:200]}" if got["err"] else ""))
    if not got["ok"]:
        print(f"        that did not work. Open {DOWNLOAD} instead - it is two clicks.")
        print("        (If the package id is wrong on your Windows, the official page is "
              "always correct; the script will not invent one.)")
        return 4
    if status()["state"] == "no-ollama":
        print("        Ollama is installed but not answering yet. Start it from the Start "
              "menu once, then run this again.")
        return 5
    return pull(dry_run)


def pull(dry_run: bool = False) -> int:
    print(f"step 3  pulling {MODEL} (about 2 GB, fits a 4 GB GPU)")
    if dry_run:
        return 0
    ollama = shutil.which("ollama")
    if not ollama:
        print("        `ollama` is not on PATH yet. Close this window, open a NEW one "
              "(PATH changes only apply to new windows), and run this again.")
        return 3
    got = _run([ollama, "pull", MODEL])
    print(f"        pull exit {got['code']}")
    if not got["ok"]:
        print(f"        {got['err'][:300] or 'the download failed'}")
        return 4
    print("step 4  asking the model a real question")
    answer = _http_json(f"{OLLAMA}/api/generate",
                        {"model": MODEL, "prompt": "Say OK in one word.", "stream": False},
                        timeout=120)
    if not answer or not str(answer.get("response") or "").strip():
        print("        the model did not answer. Chromium-free check: `ollama run "
              f"{MODEL} hi` in a new window.")
        return 6
    print(f"        it answered: {str(answer['response']).strip()[:60]}")
    print("done    run `brain models` to see it, and `brain chat` to talk to it.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Install Ollama and a small model for Nila.")
    ap.add_argument("--check", action="store_true", help="only say what is here")
    ap.add_argument("--install", action="store_true", help="install it (default action)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.check:
        info = status()
        print(json.dumps(info, indent=2))
        return 0 if info["state"] == "ready" else 1
    return install(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
