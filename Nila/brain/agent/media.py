"""Reading what the owner hands over: documents, code, images and video.

"Whatever the file is, read it and explain it short and sweet - and however big
the code, tell me very briefly. And video: 1 GB, 10 GB, editing is why I do this,
so it has to take it in and analyse it."

The first half is real today. The second half - understanding a 10 GB video - is
where an honest answer matters more than a feature list, because "AI analyses
video" usually means "samples a few frames", and a video editor needs the opposite:
where every cut, every silence, every scene change is.

So this module does four things and says clearly what each one costs:

* **documents** - PDF, DOCX, XLSX, PPTX, HTML and plain text, read with the
  standard library wherever possible (a zip and an XML file are a DOCX; a PDF is
  a stream of objects). No dependency, no upload.
* **code** - a one-screen map of a file or a folder: what it is, how big, the
  shape of it, and the handful of places worth reading first.
* **images** - size, format, and whether a caption model is available.
* **video** - the truth: frame count, duration, expected edit points, and the
  one thing that needs ffmpeg. Large files are never loaded into memory; the
  container is read structurally and the plan reports the exact command.

A 10 GB file is never read into RAM. Everything here streams or seeks.
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

MAX_TEXT_CHARS = 60_000          # what a summary reads, not the whole book
MAX_FILE_BYTES = 2 * 1024 ** 3   # refuse to "read" anything larger as text

DOC_EXT = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".rst", ".csv",
           ".log", ".json", ".yaml", ".yml", ".html", ".htm", ".xml", ".ini"}
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp",
            ".cs", ".go", ".rs", ".rb", ".php", ".kt", ".swift", ".sql", ".sh",
            ".ps1", ".lua", ".dart", ".scala", ".m"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".tif",
             ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv",
             ".ts", ".mts", ".mpg", ".mpeg", ".3gp"}


class MediaError(RuntimeError):
    pass


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in DOC_EXT:
        return "document"
    if ext in CODE_EXT:
        return "code"
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return "unknown"


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #
def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.startswith("word/") and n.endswith(".xml")]
        main = "word/document.xml" if "word/document.xml" in z.namelist() else (
            names[0] if names else "")
        if not main:
            raise MediaError("this .docx has no document part")
        xml = z.read(main).decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return _unescape_xml(text)


def _xlsx_text(path: Path) -> str:
    rows: List[str] = []
    with zipfile.ZipFile(path) as z:
        shared: List[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            shared = [_unescape_xml(re.sub(r"<[^>]+>", "", m))
                      for m in re.findall(r"<si>(.*?)</si>", sx, flags=re.S)]
        sheets = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))
        for sheet in sheets[:4]:
            sx = z.read(sheet).decode("utf-8", "replace")
            for row in re.findall(r"<row[^>]*>(.*?)</row>", sx, flags=re.S)[:200]:
                cells: List[str] = []
                for attrs, body in re.findall(r"<c([^>]*)>(.*?)</c>", row, flags=re.S):
                    kind = re.search(r't="(\w+)"', attrs)
                    value = re.sub(r"<[^>]+>", "", body)
                    if kind and kind.group(1) == "s":
                        try:
                            cells.append(shared[int(value)])
                            continue
                        except (ValueError, IndexError):
                            pass
                    cells.append(value)
                line = " | ".join(c for c in cells if c)
                if line:
                    rows.append(line)
    return "\n".join(rows)


def _pptx_text(path: Path) -> str:
    out: List[str] = []
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist()
                         if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
                        key=lambda n: int(re.sub(r"\D", "", n) or 0))
        for i, slide in enumerate(slides, 1):
            xml = z.read(slide).decode("utf-8", "replace")
            texts = [_unescape_xml(re.sub(r"<[^>]+>", "", t))
                     for t in re.findall(r"<a:t>(.*?)</a:t>", xml, flags=re.S)]
            if texts:
                out.append(f"slide {i}: " + " ".join(texts))
    return "\n".join(out)


def _unescape_xml(text: str) -> str:
    return (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&apos;", "'").replace("&#10;", "\n"))


def _pdf_text(path: Path) -> str:
    """Text from a PDF, without a dependency.

    Real PDFs store text in one of two ways: uncompressed between ``BT``/``ET``,
    or inside a Flate stream. Both are handled here with ``zlib``. What is *not*
    handled is a scanned page (an image of words) - that needs OCR, and the caller
    is told so rather than shown an empty document.
    """
    import zlib

    raw = path.read_bytes()
    chunks: List[str] = []

    def pull(blob: bytes) -> None:
        for block in re.findall(rb"BT(.*?)ET", blob, flags=re.S):
            for text in re.findall(rb"\((.*?)\)\s*Tj|\[(.*?)\]\s*TJ", block, flags=re.S):
                piece = text[0] or text[1] or b""
                clean = re.sub(rb"\\[0-7]{1,3}", b" ", piece)
                clean = re.sub(rb"\\[()\\]", b" ", clean)
                if clean.strip():
                    chunks.append(clean.decode("latin-1", "replace"))

    pull(raw)
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw, flags=re.S):
        blob = match.group(1)
        for attempt in (blob, blob.strip()):
            try:
                pull(zlib.decompress(attempt))
                break
            except zlib.error:
                continue
    text = " ".join(chunks)
    return " ".join(text.split())


def _pdf_via_ocr(path: Path) -> str:
    """A scanned PDF: read its page images. Empty string when that is not possible."""
    try:
        from . import ocr
        out = ocr.read_scanned_pdf(path)
    except Exception:                 # a picture we cannot decode is "no text", not a crash
        return ""
    return str(out.get("text") or "")


def read_document(path: Path, limit: int = MAX_TEXT_CHARS) -> Dict[str, Any]:
    """The text of a document, plus what kind it was and whether it was scanned."""
    ext = path.suffix.lower()
    try:
        if ext == ".docx":
            text = _docx_text(path)
        elif ext == ".xlsx":
            text = _xlsx_text(path)
        elif ext == ".pptx":
            text = _pptx_text(path)
        elif ext == ".pdf":
            text = _pdf_text(path)
            if not text:
                text = _pdf_via_ocr(path)
        elif ext in (".html", ".htm", ".xml"):
            body = path.read_text(encoding="utf-8", errors="replace")
            text = _unescape_xml(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ",
                                        body, flags=re.S | re.I))
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise MediaError(f"could not read {path.name}: {exc}") from exc
    text = " ".join(text.split())
    # A PDF whose pages are images yields no text at all, so "short" is the wrong
    # test: a two-word invoice number is a successful read, not a scan. Only an
    # empty extraction means "there is no text layer here".
    scanned = ext == ".pdf" and not text
    ocr_text = ext == ".pdf" and bool(text) and not _pdf_text(path)
    return {"ok": True, "path": str(path), "kind": kind_of(path), "format": ext,
            "chars": len(text), "text": text[:limit],
            "truncated": len(text) > limit, "scanned": scanned,
            "note": (("this looks like a scanned PDF (pages are images), so there is "
                      "no text inside it, and the built-in OCR could not make words "
                      "out of the page images - a real scan usually needs "
                      "pytesseract (`brain ocr --how`)") if scanned
                     else ("no text layer - these words were read by the built-in "
                           "OCR from the page images (`brain ocr`)") if ocr_text
                     else "")}


# --------------------------------------------------------------------------- #
# code, briefly
# --------------------------------------------------------------------------- #
_DEF_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class|function|func|fn|public|private|"
                     r"protected|static|const|let|var)\s+([A-Za-z_][\w]*)", re.M)
_IMPORT_RE = re.compile(r"^\s*(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import|"
                        r"require\(['\"]([^'\"]+)|#include\s+[<\"]([^>\"]+))", re.M)


def summarize_code(path: Path, limit: int = 4000) -> Dict[str, Any]:
    """A one-screen map: size, language, what it defines, what it needs."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MediaError(f"could not read {path.name}: {exc}") from exc
    lines = text.splitlines()
    defs = [m.group(1) for m in _DEF_RE.finditer(text)][:40]
    imports = sorted({next(g for g in m.groups() if g) for m in _IMPORT_RE.finditer(text)})[:20]
    todos = [ln.strip()[:100] for ln in lines if "TODO" in ln or "FIXME" in ln][:8]
    blank = sum(1 for ln in lines if not ln.strip())
    return {"ok": True, "path": str(path), "language": path.suffix.lower().lstrip("."),
            "lines": len(lines), "blank_lines": blank, "bytes": len(text.encode()),
            "defines": defs, "imports": imports, "todos": todos,
            "head": "\n".join(lines[:40])[:limit],
            "rendered": (f"{path.name}: {len(lines)} lines "
                         f"({len(lines) - blank} real), {len(text.encode()):,} bytes, "
                         f"{len(defs)} definitions, {len(imports)} imports"
                         + (f", {len(todos)} TODO/FIXME" if todos else ""))}


def summarize_tree(root: Path, limit: int = 4000) -> Dict[str, Any]:
    """A folder's shape: how many files of each kind, and the biggest few."""
    if not root.exists() or not root.is_dir():
        raise MediaError(f"{root} is not a folder")
    counts: Dict[str, int] = {}
    total = 0
    biggest: List[Tuple[int, str]] = []
    scanned = 0
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__",
                                                ".venv", "venv", "dist", "build")]
        for name in files:
            scanned += 1
            if scanned > limit:
                break
            p = Path(base) / name
            try:
                size = p.stat().st_size
            except OSError:
                continue
            total += size
            counts[p.suffix.lower() or "(none)"] = counts.get(p.suffix.lower() or "(none)", 0) + 1
            biggest.append((size, str(p.relative_to(root))))
        if scanned > limit:
            break
    biggest.sort(reverse=True)
    return {"ok": True, "root": str(root), "files": scanned, "bytes": total,
            "by_kind": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:12]),
            "biggest": [{"path": p, "mb": round(s / 1048576, 2)} for s, p in biggest[:8]],
            "truncated": scanned > limit}


# --------------------------------------------------------------------------- #
# video
# --------------------------------------------------------------------------- #
def _read_mp4_boxes(path: Path, limit_bytes: int = 64 * 1024 * 1024) -> Dict[str, Any]:
    """Walk the MP4 box tree without loading the file.

    ``moov`` carries the durations and the sample tables. When it sits at the end
    (common after editing) reading the first 64 MB finds nothing, so the tail is
    read as well. Both are seeks - a 10 GB file is never opened into memory.
    """
    size = path.stat().st_size
    info: Dict[str, Any] = {"boxes": [], "duration_s": 0.0, "timescale": 0,
                            "handler": "", "has_moov": False}

    def walk(fh, end: int, depth: int = 0) -> None:
        while fh.tell() < end - 8:
            start = fh.tell()
            head = fh.read(8)
            if len(head) < 8:
                return
            box_size = int.from_bytes(head[:4], "big")
            box_type = head[4:8].decode("latin-1", "replace")
            if box_size == 1:
                large = fh.read(8)
                box_size = int.from_bytes(large, "big") if len(large) == 8 else 0
            if box_size < 8:
                return
            info["boxes"].append(box_type)
            if box_type in ("moov", "trak", "mdia") and depth < 4:
                walk(fh, min(start + box_size, end), depth + 1)
            elif box_type == "mvhd":
                body = fh.read(min(box_size - 8, 200))
                version = body[0]
                if version == 1:
                    timescale = int.from_bytes(body[20:24], "big")
                    duration = int.from_bytes(body[24:32], "big")
                else:
                    timescale = int.from_bytes(body[12:16], "big")
                    duration = int.from_bytes(body[16:20], "big")
                if timescale:
                    info.update(has_moov=True, timescale=timescale,
                                duration_s=round(duration / timescale, 3))
            elif box_type == "hdlr":
                body = fh.read(min(box_size - 8, 40))
                info["handler"] = body[8:12].decode("latin-1", "replace") if len(body) > 12 else ""
            else:
                fh.seek(start + box_size)
        return

    try:
        with open(path, "rb") as fh:
            walk(fh, min(size, limit_bytes))
            if not info["has_moov"]:
                # moov at the end: look in the last 32 MB.
                fh.seek(max(0, size - 32 * 1024 * 1024))
                walk(fh, size)
    except OSError as exc:
        raise MediaError(f"could not read {path.name}: {exc}") from exc
    return info


def _ffprobe(path: Path) -> Optional[Dict[str, Any]]:
    import shutil
    import subprocess

    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-v", "error", "-print_format", "json", "-show_format",
             "-show_streams", str(path)], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout or b"{}")
    except json.JSONDecodeError:
        return None


def analyze_video(path: Path) -> Dict[str, Any]:
    """What a video *is*, what it would take to cut it, and what needs ffmpeg."""
    if not path.exists():
        raise MediaError(f"no file at {path}")
    size = path.stat().st_size
    info: Dict[str, Any] = {"ok": True, "path": str(path), "bytes": size,
                            "mb": round(size / 1048576, 1),
                            "gb": round(size / 1073741824, 2)}
    probe = _ffprobe(path)
    if probe:
        fmt = probe.get("format") or {}
        duration = float(fmt.get("duration") or 0)
        streams = probe.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        fps = 0.0
        try:
            num, den = str(video.get("r_frame_rate") or "0/1").split("/")
            fps = float(num) / float(den) if float(den) else 0.0
        except (ValueError, ZeroDivisionError):
            fps = 0.0
        info.update({
            "source": "ffprobe", "duration_s": round(duration, 2),
            "width": video.get("width"), "height": video.get("height"),
            "codec": video.get("codec_name"), "fps": round(fps, 3),
            "audio": audio.get("codec_name") or "",
            "frames": int(duration * fps) if fps else 0,
        })
    else:
        if path.suffix.lower() not in (".mp4", ".mov", ".m4v", ".3gp"):
            info.update({"source": "none", "duration_s": 0,
                         "note": (f"{path.suffix} needs ffprobe to read; install "
                                  f"ffmpeg to analyse it")})
        else:
            mp4 = _read_mp4_boxes(path)
            info.update({"source": "mp4 boxes",
                         "duration_s": float(mp4.get("duration_s") or 0),
                         "width": None, "height": None, "codec": "",
                         "fps": 0.0, "frames": 0,
                         "note": ("read from the container without ffmpeg; frame "
                                  "rate and codec need ffprobe")})
    # The honest edit plan: exact cut points are a decode job, not a guess.
    info["edit_plan"] = {
        "needs_ffmpeg": True,
        "why": ("finding where every cut, scene change and silence sits means "
                "decoding the file; ffmpeg does that in one pass without "
                "re-encoding"),
        "scene_detect": ("ffmpeg -i input.mp4 -filter:v "
                         "\"select='gt(scene,0.4)',showinfo\" -f null -"),
        "silence_detect": ("ffmpeg -i input.mp4 -af silencedetect=noise=-35dB:d=0.4 "
                           "-f null -"),
        "thumbnail_sheet": ("ffmpeg -i input.mp4 -vf \"fps=1/10,scale=320:-1\" "
                            "shots-%03d.jpg"),
        "trim_without_reencode": ("ffmpeg -ss 00:01:30 -to 00:02:15 -i input.mp4 "
                                  "-c copy out.mp4"),
    }
    info["rendered"] = (
        f"{path.name}: {info['gb']} GB, {info.get('duration_s', 0)} s"
        + (f", {info.get('width')}x{info.get('height')}" if info.get("width") else "")
        + (f", {info.get('fps')} fps, {info.get('frames'):,} frames"
           if info.get("frames") else "")
        + (f"  ({info['note']})" if info.get("note") else ""))
    return info


def analyze_image(path: Path) -> Dict[str, Any]:
    """Dimensions and format from the header - no pixels decoded, no upload."""
    head = path.read_bytes()[:64 * 1024]
    width = height = 0
    fmt = path.suffix.lower().lstrip(".")
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")
        fmt = "png"
    elif head[:2] == b"\xff\xd8":
        fmt = "jpeg"
        i = 2
        while i < len(head) - 9:
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                height = int.from_bytes(head[i + 5:i + 7], "big")
                width = int.from_bytes(head[i + 7:i + 9], "big")
                break
            seg_len = int.from_bytes(head[i + 2:i + 4], "big")
            i += 2 + seg_len
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        fmt = "webp"
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        fmt = "gif"
        width = int.from_bytes(head[6:8], "little")
        height = int.from_bytes(head[8:10], "little")
    out = {"ok": True, "path": str(path), "format": fmt, "width": width,
           "height": height, "bytes": path.stat().st_size, "caption": "",
           "text": "", "note": ("understanding what is IN the picture needs a "
                                "vision model; this reports its shape only")}
    if fmt == "png":
        # A screenshot is a PNG, and the words in it can be read here. The
        # picture's *meaning* still needs a vision model - that part of the note
        # is true and stays true.
        try:
            from . import ocr
            read = ocr.read_image(path)
        except Exception:             # unsupported or corrupt picture: shape only
            read = {}
        if read.get("text"):
            out["text"] = read["text"]
            out["rendered"] = (f"{path.name}: {width}x{height} {fmt}, "
                               f"{len(read['text'].split())} words of text in it")
            out["note"] = (f"the words in the picture were read by the built-in "
                           f"reader (confidence {read.get('confidence')}); what the "
                           f"picture *means* still needs a vision model")
    return out


# --------------------------------------------------------------------------- #
# one entry point
# --------------------------------------------------------------------------- #
def read_anything(path: Path, limit: int = MAX_TEXT_CHARS) -> Dict[str, Any]:
    p = Path(path).expanduser()
    if not p.exists():
        raise MediaError(f"no file at {p}")
    kind = kind_of(p)
    if kind == "document":
        out = read_document(p, limit)
    elif kind == "code":
        out = summarize_code(p)
    elif kind == "image":
        out = analyze_image(p)
    elif kind == "video":
        out = analyze_video(p)
    else:
        size = p.stat().st_size
        out = {"ok": True, "path": str(p), "kind": "unknown", "bytes": size,
               "rendered": (f"{p.name}: {size:,} bytes, and I do not have a reader "
                            f"for a {p.suffix or 'file with no extension'}")}
    out.setdefault("rendered", "")
    return out
