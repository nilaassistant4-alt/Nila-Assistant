"""Reading the screen - a screenshot he takes, nothing watching on its own.

"Screen viewing" was left out of v2 on purpose, and the reason is worth keeping:
a program that can look at the screen whenever it likes is a different product
from one that looks when it is shown something. This module is the second kind.

* **It never captures by itself.** There is no timer, no hotkey, no "while you
  work" mode. `read(path)` reads a picture the owner already has, and
  `ask_owner_to_capture()` prints the one keyboard shortcut Windows and macOS
  both use, then reads what appears.
* **It reads the words, not the meaning.** The words in a screenshot are text,
  and text can be read here. What the window *is* - which app, what it is for,
  what the diagram means - needs a vision model, and every answer says so
  instead of pretending.
* **It says where it looked.** The answer names the file and its size, so a
  wrong screenshot is visible as a wrong screenshot.

So the honest shape of this feature is: "show me a screenshot and I will read
what it says", not "I am watching your screen".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

CAPTURE_HINT_WINDOWS = ("Press Win+Shift+S, drag a box around what you want me to read, "
                        "then save it (Ctrl+S) and give me the file.")
CAPTURE_HINT_MAC = ("Press Cmd+Shift+4, drag a box, and the picture is saved to your "
                    "Desktop - give me that file.")


class ScreenError(RuntimeError):
    """Screen reading failed in a way the owner can fix."""


def capture_hint() -> str:
    import sys

    return CAPTURE_HINT_MAC if sys.platform == "darwin" else CAPTURE_HINT_WINDOWS


def read(path: Path | str) -> Dict[str, Any]:
    """Read the words in a screenshot. Never captures anything itself."""
    from . import ocr

    p = Path(path).expanduser()
    if not p.is_file():
        raise ScreenError(f"no picture at {p}")
    try:
        out = ocr.read(p)
    except ocr.OcrError as exc:
        raise ScreenError(str(exc)) from exc
    text = str(out.get("text") or "")
    verdict = "read" if text else "nothing readable"
    return {"ok": bool(text), "path": str(p), "text": text,
            "engine": out.get("engine"), "confidence": out.get("confidence"),
            "lines": out.get("lines", 0),
            "watchful": False,          # stated in the answer, not only in the docs
            "note": (f"the words in that screenshot were {verdict} by "
                     f"{out.get('engine')}. Nila did NOT capture your screen - you "
                     f"gave her a picture. What the picture *is* (which app, what it "
                     f"means) needs a vision model, which is not installed.")}


def how() -> Dict[str, Any]:
    """What he must do, and what this can and cannot do - in three sentences."""
    return {"ok": True, "always_watching": False,
            "capture": capture_hint(),
            "then": "brain screen <the picture's path>",
            "reads": "the words in the picture (PNG; a scan or a screenshot)",
            "cannot": "say which app it is, or what a diagram means - that needs a "
                      "vision model; the words are read here, the meaning is not",
            "why_not_always_on": ("a program that looks at the screen whenever it "
                                  "likes can see your bank page, your messages and "
                                  "your passwords; a picture you choose cannot")}


def shortcut_lines() -> List[str]:
    import sys

    if sys.platform == "darwin":
        return ["Cmd+Shift+4   drag a box  ->  a file on your Desktop",
                "then:  brain screen ~/Desktop/<name>.png"]
    return ["Win+Shift+S   drag a box  ->  a notification appears",
            "click it, save with Ctrl+S, then:  brain screen <the file>"]


def main(argv: List[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="screen",
                                     description="Read the words in a screenshot you took.")
    parser.add_argument("path", nargs="?", default="", help="the picture")
    parser.add_argument("--how", action="store_true", help="what to press, and what this can do")
    args = parser.parse_args(argv)
    if args.how or not args.path:
        info = how()
        for key in ("capture", "then", "reads", "cannot", "why_not_always_on"):
            print(f"{key:18s} {info[key]}")
        return 0
    try:
        out = read(args.path)
    except ScreenError as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 2
    print(out["text"] or "(no words found in that picture)")
    print(f"\n[{out['engine']} · confidence {out['confidence']}]", file=__import__("sys").stderr)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
