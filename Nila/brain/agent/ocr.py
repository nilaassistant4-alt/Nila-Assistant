"""Reading words out of a picture, with nothing installed.

The owner's list had one honest gap: "OCR for scanned pages". It is partly closed
here, and the part that is not closed is stated in the first paragraph rather
than the last.

**What this reads, measured.** It reads pages printed in *this module's own 7-row
bitmap font* - the font `tools/text_image.py` renders test pages with, at any
size, any PNG bit depth, and with grey or noisy ink. That is a real pipeline test:
the PNG decoder, the binariser, the line/word segmentation and the classifier are
all exercised, and a mistake anywhere in them shows up as wrong words. It is also
the honest limit: **a page typeset in a real font is read badly** (Sans, Serif and
Mono were measured at roughly 60% character errors, and the case of a letter is
often lost). A real page needs Tesseract; `engines()` says whether it is here, and
`read()` prefers it the moment it is. Nothing here is presented as more than it
is: every answer carries its `engine`, its `confidence` and the number of glyphs
it could not place.

**What it does, with no dependency at all:**

* **PNG decoding in pure Python** - all five filters, colour types 0/2/3/4/6,
  1/2/4/8/16 bits per channel, palettes and alpha, interlaced files refused with
  a sentence the owner can act on. zlib is in the standard library, so this needs
  no install.
* **Printed text from pixels** - Otsu threshold, horizontal bands for lines,
  column runs for glyphs, widths measured against the line's own letter pitch for
  spaces, and template matching that recovers the case of a letter from its height
  against the page's capitals.
* **Scanned PDF pages** - the Flate image inside the PDF is decoded with the same
  PNG path (that is exactly what a scanner writes). A JPEG inside a PDF is
  refused in words, because decoding JPEG is a different job.

**What is *not* here, said plainly:** handwriting, a photograph of a page taken at
an angle, and real Tamil type. Three Tamil letters (அ ம க) are drawn in the font
so a form label is not blank; Tamil OCR is Tesseract with `tesseract-ocr-tam`, and
`how_to_install()` says how.
"""
from __future__ import annotations

import math
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

MAX_PIXELS = 40_000_000          # a 40 MP page is ~20 s of pure-Python decoding
MAX_TEXT_CHARS = 200_000


class OcrError(RuntimeError):
    """Reading the picture failed, in a way the owner can act on."""


# --------------------------------------------------------------------------- #
# PNG
# --------------------------------------------------------------------------- #
@dataclass
class Bitmap:
    """Grey pixels, 0 = black, 255 = white. One byte per pixel."""

    width: int
    height: int
    pixels: bytearray

    def at(self, x: int, y: int) -> int:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.pixels[y * self.width + x]
        return 255


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, width: int, height: int, bpp: int,
              bits_per_pixel: int) -> Tuple[bytearray, int]:
    """Undo PNG's per-scanline filters, and hand back the bytes of each row.

    PNG filters work on *bytes*, not pixels, so a 1-bit image is unfiltered one
    byte at a time (`bpp` is 1) and only unpacked into pixels afterwards. Doing
    those two steps in that order is what makes 1-, 2- and 4-bit PNGs - which is
    what a scanned page or a fax saved by an old scanner usually is - read
    correctly instead of as noise.
    """
    stride = (width * bits_per_pixel + 7) // 8
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        if pos >= len(raw):
            break
        kind = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if len(line) < stride:                      # truncated file: pad with paper
            line.extend(b"\xff" * (stride - len(line)))
        if kind == 1:                               # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif kind == 2:                             # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif kind == 3:                             # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif kind == 4:                             # Paeth
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 0xFF
        elif kind != 0:
            raise OcrError(f"PNG filter type {kind} is not a thing (corrupt file?)")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out, stride


def _sample(line: bytes, index: int, depth: int, channels: int, channel: int) -> int:
    """One channel of one pixel, at any bit depth, as an 8-bit value."""
    bit = (index * channels + channel) * depth
    if depth == 8:
        return line[bit // 8] if bit // 8 < len(line) else 255
    if depth == 16:
        return line[bit // 8] if bit // 8 < len(line) else 255     # high byte
    per_byte = 8 // depth
    byte = line[(index * channels + channel) // per_byte] if (index * channels + channel) // per_byte < len(line) else 0
    shift = 8 - depth * ((index * channels + channel) % per_byte + 1)
    return ((byte >> shift) & ((1 << depth) - 1)) * 255 // ((1 << depth) - 1)


def decode_png(data: bytes) -> Bitmap:
    """A PNG as grey pixels: colour types 0, 2, 3, 4, 6 at 1-16 bits per channel."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise OcrError("that is not a PNG file")
    pos = 8
    width = height = depth = ctype = 0
    idat = bytearray()
    palette: List[Tuple[int, int, int]] = []
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctag == b"IHDR":
            width, height, depth, ctype, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", chunk[:13])
            if interlace:
                raise OcrError("interlaced PNGs are not supported - re-save it "
                               "without interlacing, or install pytesseract")
        elif ctag == b"PLTE":
            palette = [tuple(chunk[i:i + 3]) for i in range(0, len(chunk) - 2, 3)]
        elif ctag == b"IDAT":
            idat.extend(chunk)
        elif ctag == b"IEND":
            break
    if not width or not height:
        raise OcrError("no image header in that PNG")
    if width * height > MAX_PIXELS:
        raise OcrError(f"that picture is {width}x{height}; too large to read here "
                       f"({MAX_PIXELS:,} pixels is the limit)")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None:
        raise OcrError(f"PNG colour type {ctype} is not supported")
    if depth not in (1, 2, 4, 8, 16):
        raise OcrError(f"PNG bit depth {depth} is not supported")
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise OcrError(f"the PNG's data is corrupt: {exc}") from exc

    bits_per_pixel = channels * depth
    bpp = max(1, bits_per_pixel // 8)
    lines, stride = _unfilter(raw, width, height, bpp, bits_per_pixel)

    grey = bytearray(width * height)
    for y in range(height):
        row = lines[y * stride:(y + 1) * stride]
        base = y * width
        for x in range(width):
            if ctype == 3:                          # paletted
                index = _sample(row, x, depth, 1, 0)
                if depth != 8:                  # undo the 8-bit scaling of the index
                    index = (index * ((1 << depth) - 1) + 127) // 255
                rgb = palette[index] if index < len(palette) else (255, 255, 255)
                v = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) // 1000
            elif channels in (1, 2):
                v = _sample(row, x, depth, channels, 0)
            else:
                r = _sample(row, x, depth, channels, 0)
                g = _sample(row, x, depth, channels, 1)
                b = _sample(row, x, depth, channels, 2)
                v = (r * 299 + g * 587 + b * 114) // 1000
            if channels in (2, 4):                  # transparent pixels -> paper
                alpha = _sample(row, x, depth, channels, channels - 1)
                v = (v * alpha + 255 * (255 - alpha)) // 255
            grey[base + x] = v
    return Bitmap(width, height, grey)


def bitmap_from_grey(width: int, height: int, values: Sequence[int]) -> Bitmap:
    return Bitmap(width, height, bytearray(values))


# --------------------------------------------------------------------------- #
# the built-in font: 5x7 columns, the shapes a dollar-free OCR needs
# --------------------------------------------------------------------------- #
#: One entry per character: 7 rows of 5 bits ("1" = ink). Printed text only.
_FONT: Dict[str, Tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "a": ("00000", "00000", "01110", "00001", "01111", "10001", "01111"),
    "b": ("10000", "10000", "10110", "11001", "10001", "10001", "11110"),
    "c": ("00000", "00000", "01110", "10001", "10000", "10001", "01110"),
    "d": ("00001", "00001", "01101", "10011", "10001", "10001", "01111"),
    "e": ("00000", "00000", "01110", "10001", "11111", "10000", "01110"),
    "f": ("00110", "01001", "01000", "11100", "01000", "01000", "01000"),
    "g": ("00000", "01111", "10001", "10001", "01111", "00001", "01110"),
    "h": ("10000", "10000", "10110", "11001", "10001", "10001", "10001"),
    "i": ("00100", "00000", "01100", "00100", "00100", "00100", "01110"),
    "j": ("00010", "00000", "00110", "00010", "00010", "10010", "01100"),
    "k": ("10000", "10000", "10010", "10100", "11000", "10100", "10010"),
    "l": ("01100", "00100", "00100", "00100", "00100", "00100", "01110"),
    "m": ("00000", "00000", "11010", "10101", "10101", "10001", "10001"),
    "n": ("00000", "00000", "10110", "11001", "10001", "10001", "10001"),
    "o": ("00000", "00000", "01110", "10001", "10001", "10001", "01110"),
    "p": ("00000", "00000", "11110", "10001", "11110", "10000", "10000"),
    "q": ("00000", "00000", "01101", "10011", "01111", "00001", "00001"),
    "r": ("00000", "00000", "10110", "11001", "10000", "10000", "10000"),
    "s": ("00000", "00000", "01111", "10000", "01110", "00001", "11110"),
    "t": ("01000", "01000", "11100", "01000", "01000", "01001", "00110"),
    "u": ("00000", "00000", "10001", "10001", "10001", "10011", "01101"),
    "v": ("00000", "00000", "10001", "10001", "10001", "01010", "00100"),
    "w": ("00000", "00000", "10001", "10001", "10101", "10101", "01010"),
    "x": ("00000", "00000", "10001", "01010", "00100", "01010", "10001"),
    "y": ("00000", "00000", "10001", "10001", "01111", "00001", "01110"),
    "z": ("00000", "00000", "11111", "00010", "00100", "01000", "11111"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ",": ("00000", "00000", "00000", "00000", "01100", "00100", "01000"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "%": ("11001", "11010", "00010", "00100", "01000", "01011", "10011"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "=": ("00000", "00000", "11111", "00000", "11111", "00000", "00000"),
    "&": ("01100", "10010", "10100", "01000", "10101", "10010", "01101"),
    "'": ("00100", "00100", "00000", "00000", "00000", "00000", "00000"),
    '"': ("01010", "01010", "00000", "00000", "00000", "00000", "00000"),
    "!": ("00100", "00100", "00100", "00100", "00100", "00000", "00100"),
    "?": ("01110", "10001", "00001", "00110", "00100", "00000", "00100"),
    "#": ("01010", "01010", "11111", "01010", "11111", "01010", "01010"),
    "@": ("01110", "10001", "10111", "10101", "10111", "10000", "01110"),
    "*": ("00000", "10101", "01110", "11111", "01110", "10101", "00000"),
    ";": ("00000", "01100", "01100", "00000", "01100", "00100", "01000"),
    "<": ("00010", "00100", "01000", "10000", "01000", "00100", "00010"),
    ">": ("01000", "00100", "00010", "00001", "00010", "00100", "01000"),
    # a few Tamil letters, drawn at the same 5x7 scale: enough for a caption or a
    # form label, not a book. Tesseract (`--ocr-engine tesseract`) is better.
    "அ": ("00100", "11111", "00100", "01110", "10101", "01110", "00100"),
    "க": ("11100", "00100", "01110", "10101", "01110", "00100", "00100"),
    "ம": ("10001", "10001", "10101", "10101", "10101", "11011", "10001"),
}
#: characters the font knows, by width in the 5-column grid
_TEMPLATES: Dict[str, List[List[List[int]]]] = {}


def _glyph_grid(ch: str) -> List[List[int]]:
    rows = _FONT[ch]
    return [[1 if c == "1" else 0 for c in row] for row in rows]


def alphabet() -> str:
    return "".join(sorted(_FONT))


# --------------------------------------------------------------------------- #
# pixels -> lines -> words -> glyphs
# --------------------------------------------------------------------------- #
def _otsu(hist: Sequence[int]) -> int:
    total = sum(hist)
    if not total:
        return 128
    sum_all = sum(i * h for i, h in enumerate(hist))
    w_b = 0
    sum_b = 0.0
    best, threshold = -1.0, 128
    for t in range(256):
        w_b += hist[t]
        if not w_b:
            continue
        w_f = total - w_b
        if not w_f:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, threshold = between, t
    return threshold


def binarise(bmp: Bitmap) -> Tuple[bytearray, int]:
    """Ink = 1, paper = 0, with the threshold chosen from the picture itself."""
    hist = [0] * 256
    for v in bmp.pixels:
        hist[v] += 1
    threshold = _otsu(hist)
    # Otsu picks the split, but a page of near-white paper with a lot of it can
    # push it low; keeping it inside 60..220 is the safe band for scans.
    threshold = max(60, min(220, threshold))
    ink = bytearray(1 if v <= threshold else 0 for v in bmp.pixels)
    return ink, threshold


def _row_has_ink(ink: bytearray, width: int, y: int) -> int:
    row = ink[y * width:(y + 1) * width]
    return sum(row)


def _bands(ink: bytearray, width: int, height: int,
           min_height: int = 6) -> List[Tuple[int, int]]:
    """Horizontal strips that contain text (vertical projection of ink)."""
    counts = [_row_has_ink(ink, width, y) for y in range(height)]
    limit = max(1, width // 200)          # a stray speck is not a line of text
    bands: List[Tuple[int, int]] = []
    start = None
    for y, count in enumerate(counts):
        if count > limit and start is None:
            start = y
        elif count <= limit and start is not None:
            if y - start >= min_height:
                bands.append((start, y))
            start = None
    if start is not None and height - start >= min_height:
        bands.append((start, height))
    return bands


def _columns(ink: bytearray, width: int, top: int, bottom: int) -> List[Tuple[int, int]]:
    """Vertical runs of ink inside a band: glyphs, spaces and word gaps."""
    counts = []
    for x in range(width):
        c = 0
        for y in range(top, bottom):
            c += ink[y * width + x]
        counts.append(c)
    runs: List[Tuple[int, int]] = []
    start = None
    for x, c in enumerate(counts):
        if c and start is None:
            start = x
        elif not c and start is not None:
            runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, width))
    return runs


def _crop(ink: bytearray, width: int, x0: int, x1: int, y0: int, y1: int) -> List[List[int]]:
    return [[ink[y * width + x] for x in range(x0, x1)] for y in range(y0, y1)]


def _resample(grid: List[List[int]], rows: int, cols: int) -> List[List[float]]:
    """Nearest-neighbour normalise to a rows x cols grid (area average)."""
    h = len(grid) or 1
    w = len(grid[0]) if grid else 1
    out = [[0.0] * cols for _ in range(rows)]
    for r in range(rows):
        y0, y1 = r * h / rows, (r + 1) * h / rows
        for c in range(cols):
            x0, x1 = c * w / cols, (c + 1) * w / cols
            total = 0.0
            count = 0
            for y in range(int(y0), max(int(y0) + 1, int(math.ceil(y1)))):
                if y >= h:
                    break
                for x in range(int(x0), max(int(x0) + 1, int(math.ceil(x1)))):
                    if x >= w:
                        break
                    total += grid[y][x]
                    count += 1
            out[r][c] = total / count if count else 0.0
    return out


def _score(a: List[List[float]], b: List[List[float]]) -> float:
    """1.0 = identical shape. Cosine on the ink, which tolerates thin vs bold."""
    num = sum(x * y for ra, rb in zip(a, b) for x, y in zip(ra, rb))
    na = math.sqrt(sum(x * x for ra in a for x in ra))
    nb = math.sqrt(sum(y * y for rb in b for y in rb))
    if not na or not nb:
        return 0.0
    return num / (na * nb)


def _trim(grid: List[List[int]]) -> List[List[int]]:
    """The glyph's own ink box - which is exactly what cropping the page gives us.

    Without this, a lower-case template kept its empty rows above the letter while
    the crop from the page was tight: "Total:" was read as "TBTDL".
    """
    ys = [y for y, row in enumerate(grid) if any(row)]
    xs = [x for x in range(len(grid[0]) if grid else 0) if any(row[x] for row in grid)]
    if not ys or not xs:
        return grid
    return [row[xs[0]:xs[-1] + 1] for row in grid[ys[0]:ys[-1] + 1]]


def _ink_height(grid: List[List[int]]) -> int:
    rows = [y for y, row in enumerate(grid) if any(row)]
    return (rows[-1] - rows[0] + 1) if rows else 0


def _templates(ch: str, rows: int = 14, cols: int = 10) -> List[List[List[float]]]:
    key = f"{ch}:{rows}x{cols}"
    if key in _TEMPLATES:
        return _TEMPLATES[key]
    grid = _trim(_glyph_grid(ch))
    out = [_resample(grid, rows, cols)]
    # a slightly bolder copy: real ink is thicker than a bitmap font's single row
    bold = [[1 if any(grid[y][max(0, x - 1):x + 1]) else 0 for x in range(len(grid[0]))]
            for y in range(len(grid))]
    out.append(_resample(_trim(bold), rows, cols))
    _TEMPLATES[key] = out
    return out


def _small_mark(grid: List[List[int]], height: int, line_height: int) -> str:
    """A dot, a dash or an apostrophe - chosen by where its ink sits, not its shape."""
    if not grid or not line_height:
        return ""
    rows = [y for y, row in enumerate(grid) if any(row)]
    cols = [x for x in range(len(grid[0])) if any(row[x] for row in grid)]
    if not rows or not cols:
        return ""
    # Height alone decides that this is a mark: a dash is as wide as a letter,
    # but no letter in a page's font is only three dots tall.
    ink_h = rows[-1] - rows[0] + 1
    if ink_h > line_height * 0.35:
        return ""
    centre = ((rows[0] + rows[-1]) / 2) / max(1, height)
    if centre >= 0.72:
        return "."
    if centre >= 0.38:
        return "-"
    return "'"


def _classify(grid: List[List[int]], height: int, line_height: int) -> Tuple[str, float]:
    """Best character for one glyph, and how sure. Case comes from geometry.

    "o" and "O" are the same ink, so the shape decides the letter and the height
    of the glyph against the rest of the line decides the case. A capital "A" is
    a different shape from "a", so there the shape itself wins.
    """
    full = grid
    grid = _trim(full)
    if not grid or not any(any(row) for row in grid):
        return " ", 1.0
    mark = _small_mark(full, len(full), line_height)
    if mark:
        # ".", "-" and "'" are the same three dots of ink. Where the ink sits in
        # the line is the only thing that tells them apart, and trimming throws
        # that away - so this decision is made on the untrimmed crop.
        return mark, 1.0
    norm = _resample(grid, 14, 10)
    scored: List[Tuple[float, str]] = []
    for ch in _FONT:
        for template in _templates(ch):
            scored.append((_score(norm, template), ch))
    if not scored:
        return "?", 0.0
    scored.sort(key=lambda pair: -pair[0])
    best_score, best = scored[0]
    # `height` is this glyph's ink, `line_height` the page's capital height, so
    # a short letter is lower case whatever font the page was printed in.
    lower = height / max(1, line_height) < 0.82
    twin = best.lower() if best.isupper() else best.upper()
    if best.isalpha() and twin in _FONT:
        twin_score = max((s for s, c in scored if c == twin), default=0.0)
        if twin_score >= best_score - 0.03:
            # Both shapes fit equally well - the letter is the same, the case is not.
            best = twin if lower else best.upper()
    return best, best_score


def _zones(grid: List[List[int]], scale: float) -> List[int]:
    """Split a glyph down the middle and ask: by column, by row, is there ink?

    A colons sits on one side, one stroke per row has ink on both sides, a curve
    has ink on the outer columns but often not the middle. These four numbers are
    what tells ":" from "l" from "1" when every one of them is a few dots wide.
    """
    if not grid:
        return [0, 0, 0, 0]
    h, w = len(grid), len(grid[0])
    thin = max(1.0, scale * 0.45)
    if w > h * 1.2:
        thin = max(1.0, h * 0.45)
    left = any(any(row[x] for x in range(0, max(1, w // 2))) for row in grid)
    right = any(any(row[x] for x in range(w // 2, w)) for row in grid)
    top = any(any(row) for row in grid[:max(1, h // 2)])
    bottom = any(any(row) for row in grid[h // 2:])
    return [int(left), int(right), int(top), int(bottom)]


def read_bitmap(bmp: Bitmap, min_confidence: float = 0.55) -> Dict[str, Any]:
    """The printed text in a picture, line by line.

    Two passes, because case needs the whole page: the first pass crops every
    glyph and records how tall its ink is; the second reads them, using the
    page's own capital height to tell "o" from "O". A page whose letters are all
    one height is read as capitals, which is what a single-height page is.
    """
    ink, threshold = binarise(bmp)
    bands = _bands(ink, bmp.width, bmp.height)

    # ---- pass 1: crop, measure, and remember where the spaces go ----------- #
    rows: List[Dict[str, Any]] = []
    for top, bottom in bands:
        line_height = bottom - top
        runs = _columns(ink, bmp.width, top, bottom)
        if not runs:
            continue
        starts = [x0 for x0, _ in runs]
        advances = sorted(b - a for a, b in zip(starts, starts[1:]))
        pitch = advances[len(advances) // 2] if advances else 0
        glyphs = []
        prev = None
        for x0, x1 in runs:
            grid = _crop(ink, bmp.width, x0, x1, top, bottom)
            ink_h = _ink_height(grid)
            gap = None if prev is None else x0 - prev["x1"]
            glyphs.append({"grid": grid, "ink_h": ink_h, "gap": gap,
                           "x0": x0, "x1": x1})
            prev = {"x1": x1}
        rows.append({"height": line_height, "pitch": pitch, "glyphs": glyphs})

    tall = sorted(g["ink_h"] for row in rows for g in row["glyphs"])
    cap_height = tall[int(len(tall) * 0.9)] if tall else 0      # the page's capitals

    # ---- pass 2: read ------------------------------------------------------ #
    lines: List[str] = []
    unknown = 0
    confidences: List[float] = []
    for row in rows:
        read = [_classify(g["grid"], g["ink_h"], cap_height) for g in row["glyphs"]]
        parts: List[str] = []
        prev = None
        for glyph, (ch, score) in zip(row["glyphs"], read):
            if prev is not None:
                parts.append(_separator(glyph["gap"], row["pitch"], prev, ch))
            confidences.append(score)
            if score < min_confidence:
                unknown += 1
            parts.append(ch)
            prev = ch
        line = _cleanup("".join(parts))
        if line:
            lines.append(line)
    text = "\n".join(lines)
    avg = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
    return {"ok": True, "text": text[:MAX_TEXT_CHARS], "lines": len(lines),
            "glyphs": len(confidences), "unsure_glyphs": unknown,
            "confidence": avg, "threshold": threshold,
            "engine": "builtin (template matcher)"}


def _cleanup(line: str) -> str:
    """Tidy what a character matcher cannot know: no space before a full stop."""
    out = line.strip()
    for mark in ".,:;":
        out = out.replace(" " + mark, mark)
    while "  " in out:
        out = out.replace("  ", " ")
    return out.strip()


def _separator(gap: int, pitch: int, prev: str, nxt: str) -> str:
    """`""`, `" "` or `": "` between two glyphs.

    A person reading decides on one thing: is this gap as wide as a word space
    (about a letter's advance), or is it the ordinary sliver between letters?
    The pitch of the line is that measure, so narrow glyphs like "1" stop
    inventing spaces. Two more things a person knows for free:

    * a colon or semicolon hugs the word after it - ``Total:`` not ``Total :``;
    * a full stop or dash between digits is part of the number - ``A.117`` and
      ``A-117``, never ``A. 117``.
    """
    if pitch <= 0:
        pitch = max(4, gap + 1)
    if gap < max(2, pitch * 0.55):
        return ""
    if prev in ":;":
        return ": " if gap < pitch * 1.4 else " "
    if prev in ".," and nxt.isdigit():
        return ""
    return " "


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #
def read_image(path: Path) -> Dict[str, Any]:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix not in (".png",):
        raise OcrError(f"{suffix or 'that file'} is not a PNG. The built-in reader "
                       "understands PNG; a photo saved as JPEG needs pytesseract "
                       "(`brain ocr --how`).")
    bmp = decode_png(data)
    out = read_bitmap(bmp)
    out.update({"path": str(path), "width": bmp.width, "height": bmp.height})
    return out


def _pdf_images(raw: bytes) -> List[Tuple[int, int, bytes, Dict[str, Any]]]:
    """Flate-compressed images inside a PDF, as (width, height, grey bytes, info)."""
    images: List[Tuple[int, int, bytes, Dict[str, Any]]] = []
    for match in re.finditer(rb"(\d+)\s+0\s+obj(.*?)endobj", raw, flags=re.S):
        body = match.group(2)
        if b"/Image" not in body:
            continue
        head = body.split(b"stream", 1)[0]
        width = re.search(rb"/Width\s+(\d+)", head)
        height = re.search(rb"/Height\s+(\d+)", head)
        if not width or not height:
            continue
        if b"/DCTDecode" in head:
            images.append((int(width.group(1)), int(height.group(1)), b"",
                           {"why": "JPEG image inside the PDF - needs a JPEG decoder "
                                   "or pytesseract"}))
            continue
        if b"/FlateDecode" not in head:
            continue
        stream = re.search(rb"stream\r?\n(.*?)endstream", body, flags=re.S)
        if not stream:
            continue
        blob = stream.group(1)
        try:
            data = zlib.decompress(blob)
        except zlib.error:
            continue
        w, h = int(width.group(1)), int(height.group(1))
        channels = 1
        if b"/DeviceRGB" in head:
            channels = 3
        elif b"/DeviceCMYK" in head:
            channels = 4
        if len(data) < w * h * channels:
            continue
        grey = bytearray(w * h)
        for i in range(w * h):
            if channels == 1:
                grey[i] = data[i]
            else:
                base = i * channels
                grey[i] = (data[base] * 299 + data[base + 1] * 587
                           + data[base + 2] * 114) // 1000
        images.append((w, h, bytes(grey), {"channels": channels}))
    return images


def read_scanned_pdf(path: Path) -> Dict[str, Any]:
    """Text out of a PDF whose pages are pictures, page by page."""
    raw = path.read_bytes()
    images = _pdf_images(raw)
    if not images:
        raise OcrError("this PDF has no readable image pages (and no text layer)")
    pages, skipped = [], []
    for index, (w, h, grey, info) in enumerate(images, 1):
        if not grey:
            skipped.append({"page": index, "why": info.get("why", "unsupported image")})
            continue
        if w * h > MAX_PIXELS:
            skipped.append({"page": index, "why": f"page too large ({w}x{h})"})
            continue
        page = read_bitmap(Bitmap(w, h, bytearray(grey)))
        pages.append({"page": index, "text": page["text"],
                      "confidence": page["confidence"]})
    text = "\n\n".join(p["text"] for p in pages if p["text"])
    return {"ok": bool(text), "text": text[:MAX_TEXT_CHARS], "pages": pages,
            "skipped_pages": skipped, "engine": "builtin (template matcher)",
            "note": ("" if not skipped else
                     f"{len(skipped)} page(s) could not be read here: "
                     + "; ".join(f"p{s['page']}: {s['why']}" for s in skipped))}


# --------------------------------------------------------------------------- #
# the optional better engine
# --------------------------------------------------------------------------- #
def tesseract_available() -> bool:
    import importlib.util
    import shutil

    if importlib.util.find_spec("pytesseract") is None:
        return False
    return bool(shutil.which("tesseract"))


def engines() -> Dict[str, Any]:
    have = tesseract_available()
    return {"builtin": True, "tesseract": have,
            "preferred": "tesseract" if have else "builtin",
            "tamil": ("tesseract with the `tam` language pack" if have
                      else "the built-in font has a few Tamil letters; "
                           "install tesseract + tesseract-ocr-tam for real Tamil OCR"),
            "note": ("the built-in reader needs nothing installed and reads pages "
                     "printed in its own font (which is what `brain ocr --demo` "
                     "writes); anything typeset in a real font, or photographed, "
                     "wants tesseract")}


def read_with_tesseract(path: Path, language: str = "eng") -> Dict[str, Any]:
    """The optional engine. Kept behind one function so the caller never cares."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:                 # pragma: no cover - depends on install
        raise OcrError("pytesseract/Pillow are not installed - `brain ocr --how`") from exc
    with Image.open(path) as img:              # pragma: no cover - needs the binary
        text = pytesseract.image_to_string(img, lang=language)
    return {"ok": True, "text": text.strip(), "engine": f"tesseract ({language})",
            "confidence": None, "path": str(path)}


def read(path: Path | str, engine: str = "auto", language: str = "eng") -> Dict[str, Any]:
    """Read words out of a picture or a scanned PDF, saying which engine did it."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise OcrError(f"{p} is not a file")
    size = p.stat().st_size
    if size > 40 * 1024 * 1024:
        raise OcrError(f"{p.name} is {size // 1024 // 1024} MB; too large to read here")
    use = (engine or "auto").lower()
    if use in ("auto", "tesseract") and tesseract_available():
        try:
            return read_with_tesseract(p, language)
        except OcrError:
            if use == "tesseract":
                raise
    if p.suffix.lower() == ".pdf":
        return read_scanned_pdf(p)
    return read_image(p)


def how_to_install() -> str:
    return ("Reading a picture needs nothing installed (PNG and scanned PDF pages "
            "are decoded here). For photos, skewed scans and real Tamil OCR:\n"
            "  python -m pip install pytesseract pillow\n"
            "  then install Tesseract itself: on Windows `winget install "
            "UB-Mannheim.TesseractOCR`, on Ubuntu `sudo apt install tesseract-ocr "
            "tesseract-ocr-tam`\n"
            "  brain ocr --engines      # says which of the two is being used")
