#!/usr/bin/env python3
"""Render printed text to a PNG with nothing installed - and read it back.

Two uses, and both are honest about what they are:

* **A check you can run yourself.** `--write "invoice 42"` produces a page whose
  words are known, then `brain ocr` reads it. If the words come back wrong, the
  reader is wrong; there is no "it looked fine" in the middle.
* **A picture to test a scanned PDF.** `--pdf report.pdf` wraps the same page as a
  Flate image inside a PDF, which is what a scanner produces (minus the skew),
  so the scanned-PDF path can be exercised without a scanner.
* **A page a person can look at.** `--png` writes the same words as an ordinary
  grey-on-white snippet, and `--read` runs the reader on it, so "it works" can be
  seen rather than taken on trust. That pair is why the answer below is a picture
  in the chat and not a paragraph claiming one.

This renders with the same 5x7 font the reader knows, so it tests the *pipeline*
(PNG decode, filters, line and word segmentation, classification) rather than a
different typeface. `tests/test_ocr_acceptance.py` adds hand-built bitmaps whose
strokes are one pixel thick - a shape the filled font never draws - so the
matcher is not only compared with itself.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.agent.ocr import _FONT  # noqa: E402  (the same shapes the reader knows)

GLYPH_W, GLYPH_H = 5, 7


def render_rows(text: str, scale: int = 3, spacing: int = 1) -> list[str]:
    """The page as rows of "1"/"0": one glyph per character, a blank column between."""
    lines = text.split("\n")
    rows: list[str] = []
    for line_index, line in enumerate(lines):
        if line_index:
            rows.extend(["0" * 1] * (GLYPH_H * scale // 2))       # gap between lines
        columns = len(line) * (GLYPH_W + spacing) * scale or 1
        grid = [["0"] * columns for _ in range(GLYPH_H * scale)]
        for i, ch in enumerate(line):
            if ch == " ":
                continue                      # a space is blank columns, not a glyph
            glyph = _FONT.get(ch) or _FONT.get(ch.upper()) or _FONT["?"]
            for y in range(GLYPH_H):
                for x in range(GLYPH_W):
                    if glyph[y][x] != "1":
                        continue
                    for dy in range(scale):
                        for dx in range(scale):
                            gx = i * (GLYPH_W + spacing) * scale + x * scale + dx
                            gy = y * scale + dy
                            if gy < len(grid) and gx < columns:
                                grid[gy][gx] = "1"
        rows.extend("".join(r) for r in grid)
    return rows


def render_grey(text: str, scale: int = 3, margin: int = 8) -> tuple[int, int, bytearray]:
    rows = render_rows(text or " ", scale=scale)
    height = len(rows) + margin * 2
    width = max(len(r) for r in rows) + margin * 2
    pixels = bytearray(b"\xff" * (width * height))
    for y, row in enumerate(rows):
        base = (y + margin) * width + margin
        for x, c in enumerate(row):
            if c == "1":
                pixels[base + x] = 0
    return width, height, pixels


def write_png(path: Path, width: int, height: int, grey: bytes,
              filter_type: int = 0) -> Path:
    """A valid greyscale PNG. `filter_type` lets a test exercise each filter."""
    raw = bytearray()
    prev = bytearray(width)
    for y in range(height):
        line = bytearray(grey[y * width:(y + 1) * width])
        raw.append(filter_type)
        if filter_type == 0:
            raw.extend(line)
        elif filter_type == 1:                       # Sub
            out = bytearray(line)
            for i in range(width - 1, 0, -1):
                out[i] = (line[i] - line[i - 1]) & 0xFF
            raw.extend(out)
        elif filter_type == 2:                       # Up
            raw.extend(bytes((line[i] - prev[i]) & 0xFF for i in range(width)))
        elif filter_type == 3:                       # Average
            out = bytearray()
            for i in range(width):
                left = line[i - 1] if i else 0
                out.append((line[i] - ((left + prev[i]) >> 1)) & 0xFF)
            raw.extend(out)
        elif filter_type == 4:                       # Paeth
            out = bytearray()
            for i in range(width):
                left = line[i - 1] if i else 0
                ul = prev[i - 1] if i else 0
                out.append((line[i] - _paeth(left, prev[i], ul)) & 0xFF)
            raw.extend(out)
        prev = line

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    payload = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
               + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    path.write_bytes(payload)
    return path


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def write_scanned_pdf(path: Path, text: str, scale: int = 3) -> Path:
    """A one-page PDF whose only content is a Flate image of the words - a scan."""
    width, height, grey = render_grey(text, scale=scale)
    image = zlib.compress(bytes(grey), 9)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"),
        (f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
         f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
         f"/Length {len(image)} >>\nstream\n").encode() + image + b"\nendstream",
    ]
    draw = f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode()
    objects.append((f"<< /Length {len(draw)} >>\nstream\n").encode() + draw + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{start}\n%%EOF\n").encode()
    path.write_bytes(bytes(out))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render text to a PNG (or a scanned PDF).")
    parser.add_argument("--write", default="", metavar="TEXT",
                        help="the words to draw (\\n for a new line)")
    parser.add_argument("--out", default="", help="where to write it (default ./page.png)")
    parser.add_argument("--scale", type=int, default=3, help="pixel size per font dot")
    parser.add_argument("--pdf", default="", metavar="FILE",
                        help="write a one-page scanned-style PDF here")
    parser.add_argument("--read", default="", metavar="FILE",
                        help="read a file back with the built-in OCR and print the text")
    args = parser.parse_args(argv)

    if args.read:
        from brain.agent.ocr import OcrError, read
        try:
            out = read(Path(args.read))
        except OcrError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(out.get("text", ""))
        print(f"\n[{out.get('engine')} · confidence {out.get('confidence')}]",
              file=sys.stderr)
        return 0
    text = args.write.replace("\\n", "\n")
    if not text:
        parser.print_help()
        return 2
    if args.pdf:
        path = write_scanned_pdf(Path(args.pdf), text, scale=args.scale)
        print(f"wrote {path}")
        return 0
    out = Path(args.out or "page.png")
    width, height, grey = render_grey(text, scale=args.scale)
    write_png(out, width, height, grey)
    print(f"wrote {out} ({width}x{height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
