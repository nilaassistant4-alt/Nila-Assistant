"""Acceptance tests: stage 12, gap 7 - the screen, only when he shows it.

"Screen viewing (screenshot -> read it back)" was marked *not built*, and the note
said it was the highest-risk feature. The risk is not the reading - it is a
program that looks whenever it likes. So the feature that exists here is
deliberately the safe half, and the tests hold it to that:

* there is **no capture call anywhere** in the module - not a timer, not a
  hotkey, not an optional one. `read(path)` needs a file the owner made.
* the answer says `watchful: False` in the data, not only in the documentation,
  so a caller cannot display it as "I am watching".
* what it reads is the *words*; every answer says the meaning still needs a
  vision model.
* `--how` prints the exact key combination and then the exact command to run,
  so the hint can be followed without knowing anything about terminals.

Run:  python -m pytest tests/test_screen_view_acceptance.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from brain.agent import screen

ROOT = Path(__file__).resolve().parents[1]


def page_module():
    path = ROOT / "tools" / "text_image.py"
    spec = importlib.util.spec_from_file_location("nila_screen_page", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def screenshot(tmp_path: Path, text: str) -> Path:
    page = page_module()
    width, height, grey = page.render_grey(text, scale=2)
    return page.write_png(tmp_path / "screen.png", width, height, grey)


# --------------------------------------------------------------------------- #
# it must never look on its own
# --------------------------------------------------------------------------- #
def test_the_module_contains_no_capture_call_at_all():
    """The strongest statement this feature can make: there is no code that looks."""
    source = (ROOT / "brain" / "agent" / "screen.py").read_text()
    for forbidden in ("ImageGrab", "screenshot(", "mss(", "pyautogui", "grab(",
                      "import mss", "PIL.ImageGrab", "xwd", "scrot"):
        assert forbidden not in source, f"screen.py can capture: {forbidden}"
    # and no timer that could call read() on a schedule
    for forbidden in ("Thread(", "schedule", "while True", "asyncio"):
        assert forbidden not in source, forbidden


def test_every_answer_says_it_was_not_watching(tmp_path):
    out = screen.read(screenshot(tmp_path, "ERROR 42"))
    assert out["watchful"] is False
    assert "did NOT capture your screen" in out["note"]
    assert "vision model" in out["note"]


def test_how_says_the_shortcut_and_the_command_in_order():
    info = screen.how()
    assert info["always_watching"] is False
    assert "Win+Shift+S" in info["capture"] or "Cmd+Shift+4" in info["capture"]
    assert info["then"].startswith("brain screen ")
    assert "vision model" in info["cannot"]
    assert "bank" in info["why_not_always_on"]          # the reason, concretely


def test_the_printed_shortcut_is_a_command_he_can_follow(tmp_path, capsys):
    from brain.cli.main import UI, build_parser, cmd_screen

    args = build_parser().parse_args(["screen"])
    assert cmd_screen(args, UI()) == 0
    printed = capsys.readouterr().out
    assert "does not watch your screen" in printed
    assert "brain screen" in printed or "then:" in printed


# --------------------------------------------------------------------------- #
# what it does read
# --------------------------------------------------------------------------- #
def test_a_screenshot_is_read_back_word_for_word(tmp_path):
    out = screen.read(screenshot(tmp_path, "Total: 1250"))
    assert out["ok"] is True and out["text"] == "Total: 1250"
    assert out["lines"] == 1 and out["confidence"] >= 0.9


def test_a_picture_with_no_words_says_so_rather_than_inventing_them(tmp_path):
    page = page_module()
    width, height, grey = page.render_grey(" ")          # a blank page
    path = page.write_png(tmp_path / "blank.png", width, height, grey)
    out = screen.read(path)
    assert out["ok"] is False and out["text"] == ""
    assert "nothing readable" in out["note"]


def test_a_missing_file_is_named_not_crashed_on(tmp_path):
    with pytest.raises(screen.ScreenError, match="no picture at"):
        screen.read(tmp_path / "not-there.png")


def test_a_jpeg_screenshot_is_refused_with_the_fix_in_the_sentence(tmp_path):
    path = tmp_path / "shot.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 60)
    with pytest.raises(screen.ScreenError) as caught:
        screen.read(path)
    assert "JPEG" in str(caught.value) and "pytesseract" in str(caught.value)


def test_the_command_reads_a_picture_and_returns_one_on_failure(tmp_path, capsys):
    from brain.cli.main import UI, build_parser, cmd_screen

    good = screenshot(tmp_path, "INVOICE 7")
    args = build_parser().parse_args(["screen", str(good)])
    assert cmd_screen(args, UI()) == 0
    assert "INVOICE 7" in capsys.readouterr().out

    args = build_parser().parse_args(["screen", str(tmp_path / "nope.png")])
    assert cmd_screen(args, UI()) == 2
    assert "no picture at" in capsys.readouterr().err
