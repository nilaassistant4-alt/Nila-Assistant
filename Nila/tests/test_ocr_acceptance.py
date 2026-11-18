"""Acceptance tests: stage 12, gap 1 - reading words out of a picture.

The owner's row said "OCR for scanned pages" and it was marked *not built*
because reading a picture looked like it needed a binary he does not have. The
answer here is narrower than "OCR" and stronger than "not built":

* it decodes a PNG itself (every filter, every allowed bit depth), so a
  screenshot needs nothing installed;
* it reads words printed in its own 7-row font - which is exactly what
  ``brain ocr --demo`` writes - at any scale, with grey ink, with noise, and
  with strokes one pixel thick;
* it reads a *scanned PDF* whose pages are Flate images, which is what a scanner
  actually writes;
* when the page is typeset in a real font it does not pretend: the engine name,
  the confidence and the count of glyphs it could not place travel with every
  answer, and Tesseract is preferred the moment it exists.

The last test is the important one for honesty: it renders a real DejaVu page
with ImageMagick (when that is on the machine) and asserts the built-in reader
either reads it or says it is unsure - never confident nonsense.

Run:  python -m pytest tests/test_ocr_acceptance.py -q
"""
from __future__ import annotations

import importlib.util
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from brain.agent import ocr

ROOT = Path(__file__).resolve().parents[1]
FONT = ocr._FONT


def page_module():
    """`tools/text_image.py` - the page writer the CLI's --demo uses."""
    path = ROOT / "tools" / "text_image.py"
    spec = importlib.util.spec_from_file_location("nila_text_image_page", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# 1. the words come back - the pipeline, end to end
# --------------------------------------------------------------------------- #
def test_a_page_written_and_read_back_gives_the_same_words(tmp_path):
    page = page_module()
    text = "INVOICE 42\nTotal: 1250 rupees"
    width, height, grey = page.render_grey(text)
    path = page.write_png(tmp_path / "page.png", width, height, grey)
    out = ocr.read(path)
    assert out["ok"] and out["engine"] == "builtin (template matcher)"
    assert out["text"] == text                       # exactly, not "close enough"
    assert out["confidence"] >= 0.9 and out["unsure_glyphs"] == 0
    assert (out["width"], out["height"]) == (width, height)


def test_case_is_recovered_from_height_not_guessed(tmp_path):
    """A real invoice mixes "INVOICE" and "rupees" on the same page."""
    page = page_module()
    text = "GST 18%\nBill no. A-117 (paid)\ndon't pay"
    width, height, grey = page.render_grey(text)
    out = ocr.read(page.write_png(tmp_path / "p.png", width, height, grey))
    assert out["text"] == text


def test_punctuation_that_is_only_a_few_dots_is_told_apart(tmp_path):
    """".", "-" and "'" are the same three dots of ink; only position differs."""
    page = page_module()
    for text in ("A-117", "A.117", "don't", "Total: 1250"):
        width, height, grey = page.render_grey(text)
        out = ocr.read(page.write_png(tmp_path / "q.png", width, height, grey))
        assert out["text"] == text, f"{text!r} came back as {out['text']!r}"


@pytest.mark.parametrize("scale", [1, 2, 3, 6])
def test_the_size_on_the_page_does_not_matter(tmp_path, scale):
    page = page_module()
    width, height, grey = page.render_grey("Nila 2026", scale=scale)
    out = ocr.read(page.write_png(tmp_path / f"s{scale}.png", width, height, grey))
    assert out["text"] == "Nila 2026"


@pytest.mark.parametrize("filter_type", [0, 1, 2, 3, 4])
def test_every_png_filter_is_undone(tmp_path, filter_type):
    """Sub, Up, Average and Paeth are what a real screenshot uses."""
    page = page_module()
    width, height, grey = page.render_grey("Total 1250")
    path = page.write_png(tmp_path / f"f{filter_type}.png", width, height, grey,
                          filter_type=filter_type)
    assert ocr.read(path)["text"] == "Total 1250"


def test_grey_ink_on_grey_paper_with_noise_still_reads(tmp_path):
    page = page_module()
    width, height, grey = page.render_grey("Hello 42")
    noisy = bytearray(232 if v > 127 else 38 for v in grey)
    for i in range(0, len(noisy), 17):
        noisy[i] = min(255, max(0, noisy[i] + (7 if i % 2 else -9)))
    path = page.write_png(tmp_path / "n.png", width, height, bytes(noisy))
    assert ocr.read(path)["text"] == "Hello 42"


def test_strokes_one_pixel_thick_are_read(tmp_path):
    """A thin font is not the same shape as the filled one: the matcher must cope.

    Every glyph is redrawn as its outline, so no pixel is identical to a
    template - if the reader compared bitmaps instead of shapes this would fail.
    """
    def thin(ch: str):
        grid = [[1 if c == "1" else 0 for c in row] for row in FONT[ch]]
        out = [[0] * 5 for _ in range(7)]
        for y in range(7):
            for x in range(5):
                if not grid[y][x]:
                    continue
                neighbours = sum(1 for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1))
                                 if 0 <= y + dy < 7 and 0 <= x + dx < 5
                                 and grid[y + dy][x + dx])
                if neighbours <= 2:
                    out[y][x] = 1
        return out

    text = "Total: 1250"
    width, height = len(text) * 6 + 10, 9
    pixels = bytearray(b"\xff" * (width * height))
    for i, ch in enumerate(text):
        if ch == " ":
            continue
        for y in range(7):
            for x in range(5):
                if thin(ch)[y][x]:
                    pixels[(y + 1) * width + 3 + i * 6 + x] = 0
    out = ocr.read_bitmap(ocr.Bitmap(width, height, pixels))
    assert out["text"] == text


# --------------------------------------------------------------------------- #
# 2. the formats a scanner and a screenshot really produce
# --------------------------------------------------------------------------- #
def _png(width: int, height: int, rows: bytes, depth: int, ctype: int,
         palette: bytes = b"") -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    out = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, depth, ctype, 0, 0, 0))
    if palette:
        out += chunk(b"PLTE", palette)
    return out + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def test_a_one_bit_page_reads_even_though_filters_work_on_bytes(tmp_path):
    """A fax, and a scan saved by an old scanner, is 1 bit per pixel."""
    page = page_module()
    width, height, grey = page.render_grey("Total 42", scale=2)
    rows = bytearray()
    for y in range(height):
        line = grey[y * width:(y + 1) * width]
        bits = 0
        count = 0
        for v in line:
            bits = (bits << 1) | (1 if v > 127 else 0)
            count += 1
            if count == 8:
                rows.append(bits)
                bits, count = 0, 0
        if count:
            rows.append(bits << (8 - count))
    # a filter byte in front of each packed row, which is the PNG layout
    fixed = bytearray()
    for y in range(height):
        fixed.append(0)
        fixed.extend(rows[y * ((width + 7) // 8):(y + 1) * ((width + 7) // 8)])
    path = tmp_path / "onebit.png"
    path.write_bytes(_png(width, height, bytes(fixed), 1, 0))
    assert ocr.read(path)["text"] == "Total 42"


def test_a_paletted_png_reads(tmp_path):
    page = page_module()
    width, height, grey = page.render_grey("PAY 250", scale=2)
    rows = bytearray()
    for y in range(height):
        line = grey[y * width:(y + 1) * width]
        rows.append(0)                       # filter: none
        for v in line:
            rows.append(0 if v < 128 else 1)
    path = tmp_path / "pal.png"
    path.write_bytes(_png(width, height, bytes(rows), 8, 3,
                          palette=b"\x00\x00\x00\xff\xff\xff"))
    assert ocr.read(path)["text"] == "PAY 250"


def test_an_interlaced_png_is_refused_in_words_not_crashed_on(tmp_path):
    data = bytearray(_png(4, 4, b"\x00" * 4, 8, 0))
    data[28] = 1                              # interlace method in IHDR
    path = tmp_path / "adam7.png"
    path.write_bytes(bytes(data))
    with pytest.raises(ocr.OcrError, match="interlaced"):
        ocr.read(path)


def test_something_that_is_not_a_png_is_refused_by_name(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 100)
    with pytest.raises(ocr.OcrError, match="JPEG"):
        ocr.read(path)


def test_a_scanned_pdf_page_is_read(tmp_path):
    """A PDF whose only content is an image of the words - that is a scan."""
    page = page_module()
    path = page.write_scanned_pdf(tmp_path / "scan.pdf", "Invoice 42\nTotal: 1250")
    out = ocr.read(path)
    assert out["ok"] and out["pages"][0]["text"] == "Invoice 42\nTotal: 1250"
    assert out["engine"].startswith("builtin")


def test_a_pdf_of_jpeg_pages_says_what_is_missing(tmp_path):
    """A scan saved as JPEG pages must be named, not returned as empty text."""
    body = (b"%PDF-1.4\n1 0 obj\n<< /Type /XObject /Subtype /Image /Width 4 "
            b"/Height 4 /Filter /DCTDecode >>\nstream\n\xff\xd8\xff\nendstream\n"
            b"endobj\n%%EOF")
    path = tmp_path / "jpeg.pdf"
    path.write_bytes(body)
    out = ocr.read(path)
    assert out["ok"] is False and out["text"] == ""
    assert "JPEG" in out["skipped_pages"][0]["why"]
    assert "could not be read here" in out["note"]


# --------------------------------------------------------------------------- #
# 3. the rest of the product
# --------------------------------------------------------------------------- #
def test_brain_file_reads_a_scanned_pdf_instead_of_saying_it_cannot(tmp_path):
    from brain.agent import media

    page = page_module()
    path = page.write_scanned_pdf(tmp_path / "scan.pdf", "Invoice 42")
    out = media.read_document(path)
    assert out["text"] == "Invoice 42"
    assert out["scanned"] is False
    assert "OCR" in out["note"] and "page images" in out["note"]


def test_brain_file_still_calls_an_unreadable_scan_unreadable(tmp_path):
    from brain.agent import media

    path = tmp_path / "blank.pdf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj<<>>stream\nendstream\nendobj\n%%EOF")
    out = media.read_document(path)
    assert out["text"] == "" and out["scanned"] is True
    assert "pytesseract" in out["note"]


def test_a_screenshot_reports_the_words_in_it_and_still_admits_it_cannot_see(tmp_path):
    from brain.agent import media

    page = page_module()
    width, height, grey = page.render_grey("ERROR 5")
    path = page.write_png(tmp_path / "shot.png", width, height, grey)
    out = media.read_anything(path)
    assert out["text"] == "ERROR 5"
    assert "words in the picture were read" in out["note"]
    assert "vision model" in out["note"]


def test_the_cli_demo_proves_itself_and_says_so(tmp_path, capsys):
    from brain.cli.main import UI, build_parser, cmd_ocr

    args = build_parser().parse_args(["ocr", "--demo", "--out", str(tmp_path / "d.png")])
    assert cmd_ocr(args, UI()) == 0
    printed = capsys.readouterr().out
    assert "read it back" in printed and "came back exactly" in printed
    assert (tmp_path / "d.png").is_file()


def test_the_cli_says_which_engine_would_be_used(capsys):
    from brain.cli.main import UI, build_parser, cmd_ocr

    args = build_parser().parse_args(["ocr", "--engines"])
    assert cmd_ocr(args, UI()) == 0
    printed = capsys.readouterr().out
    assert "builtin" in printed and "preferred" in printed


def test_the_install_hint_names_a_command_that_works():
    text = ocr.how_to_install()
    assert "pip install pytesseract pillow" in text
    assert "tesseract-ocr-tam" in text          # Tamil OCR, not just English


def test_tesseract_is_preferred_when_it_is_there(monkeypatch, tmp_path):
    """The better engine must win automatically - the caller never chooses."""
    calls = {}

    def fake_read(path, language="eng"):
        calls["ran"] = True
        return {"ok": True, "text": "read by tesseract", "engine": "tesseract (eng)"}

    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    monkeypatch.setattr(ocr, "read_with_tesseract", fake_read)
    page = page_module()
    width, height, grey = page.render_grey("x")
    path = page.write_png(tmp_path / "t.png", width, height, grey)
    assert ocr.read(path)["text"] == "read by tesseract"
    assert calls["ran"] is True
    # ...and asking for the built-in one explicitly still gets the built-in one
    assert ocr.read(path, engine="builtin")["engine"].startswith("builtin")


# --------------------------------------------------------------------------- #
# 4. the honesty test: a page in a real font
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("convert") is None,
                    reason="needs ImageMagick to render a real-font page")
def test_a_real_font_page_is_never_reported_as_confident_nonsense(tmp_path):
    """This is the gap that is *not* closed, written down as a test.

    Sans, Serif and Mono type at 22 points are read badly by a template matcher;
    that is why Tesseract is preferred wherever it exists. What the reader must
    never do is hand over wrong words at full confidence. So: either the words
    come back, or the answer is marked unsure.
    """
    line = "Total: 1250"
    page = tmp_path / "real.png"
    try:
        subprocess.run(["convert", "-font", "DejaVu-Sans", "-pointsize", "22",
                        "label:" + line, "-bordercolor", "white", "-border", "8",
                        "-type", "Grayscale", str(page)],
                       check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("ImageMagick here has no DejaVu font")
    out = ocr.read(page)
    if out["text"] != line:
        # It failed - and that is allowed, but only out loud.
        assert out["unsure_glyphs"] > 0 or out["confidence"] < 0.9, (
            f"wrong words at full confidence: {out['text']!r} "
            f"(conf {out['confidence']})")
        assert "tesseract" in ocr.engines()["note"]
