"""Finding a file on this PC: by name, and (optionally) by what is inside it.

The owner asked for the everyday version of search - "where is that bill?" - and
the everyday version is the hard one, because a naive `os.walk` of a home folder
reads a million files and reports nothing useful. So:

* **Name search is fast and bounded.** A depth limit and a result cap, with the
  folders that never belong in a personal search (`.git`, `node_modules`,
  `AppData\\Local\\Temp`, the recycle bin) skipped by name.
* **Content search is opt-in and small.** Only text-ish files, only under a size
  cap, and only when the caller asks: reading every document to answer "what is
  this called" would be a privacy event, not a search.
* **The result says how much it looked at.** "12 matches" after 200 files and
  "12 matches" after 200,000 are different answers, and the report says which.
* **Nothing is executed and nothing is opened.** It returns paths.
"""
from __future__ import annotations

import fnmatch
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "AppData", "Windows", "$Recycle.Bin", "System Volume Information",
    "Library", ".cache", ".npm", ".gradle", "site-packages", "dist", "build",
    ".next", ".mypy_cache", ".pytest_cache", "target",
}
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".xml",
            ".log", ".yaml", ".yml", ".ini", ".rst", ".sql", ".sh"}
MAX_BYTES_FOR_CONTENT = 2 * 1024 * 1024


@dataclass
class Hit:
    path: str
    name: str
    size: int
    modified: str
    matched: str = "name"          # "name" | "content" | "both"
    snippet: str = ""

    def line(self) -> str:
        mb = self.size / 1048576
        size = f"{mb:.1f} MB" if mb >= 1 else f"{self.size / 1024:.0f} KB"
        extra = f"   … {self.snippet}" if self.snippet else ""
        return f"  {self.name:<38s} {size:>9s}  {self.modified}  {self.path}{extra}"

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "name": self.name, "size": self.size,
                "modified": self.modified, "matched": self.matched,
                "snippet": self.snippet}


def _stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def search(root: str | os.PathLike, query: str, *, limit: int = 20,
           max_depth: int = 8, max_files: int = 200_000, content: bool = False,
           ext: str = "", newer_than_days: int = 0) -> Dict[str, Any]:
    """Files under `root` whose name matches `query` (glob or plain words)."""
    base = Path(root).expanduser()
    if not base.exists() or not base.is_dir():
        return {"ok": False, "error": f"{base} is not a folder I can search"}
    q = str(query or "").strip()
    if not q:
        return {"ok": False, "error": "what should I look for?"}
    phrase = q.lower()
    patterns = [p for p in re.split(r"\s+", phrase) if p]
    glob = any(ch in q for ch in "*?[")
    cutoff = (time.time() - newer_than_days * 86400) if newer_than_days else 0
    hits: List[Hit] = []
    scanned = 0
    skipped_dirs = 0
    started = time.time()

    for dirpath, dirnames, filenames in os.walk(base):
        depth = dirpath[len(str(base)):].count(os.sep)
        if depth > max_depth:
            dirnames[:] = []
            continue
        kept: List[str] = []
        for d in dirnames:
            if d in SKIP_DIRS or d.startswith("."):
                skipped_dirs += 1
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in filenames:
            scanned += 1
            if scanned > max_files:
                break
            p = Path(dirpath) / name
            if ext and p.suffix.lower() != ("." + ext.lower().lstrip(".")):
                continue
            name_low = name.lower()
            name_match = (fnmatch.fnmatch(name_low, phrase) if glob
                          else all(part in name_low for part in patterns))
            if cutoff:
                try:
                    if p.stat().st_mtime < cutoff:
                        continue
                except OSError:
                    continue
            snippet = ""
            content_match = False
            if not name_match and content and p.suffix.lower() in TEXT_EXT:
                try:
                    if p.stat().st_size <= MAX_BYTES_FOR_CONTENT:
                        text = p.read_text(encoding="utf-8", errors="ignore").lower()
                        content_match = all(part in text for part in patterns)
                        if content_match:
                            idx = text.find(patterns[0])
                            snippet = " ".join(
                                text[max(0, idx - 40):idx + 80].split())[:120]
                except OSError:
                    content_match = False
            if not (name_match or content_match):
                continue
            try:
                stat = p.stat()
                size, mtime = stat.st_size, stat.st_mtime
            except OSError:
                size, mtime = 0, time.time()
            hits.append(Hit(path=str(p), name=name, size=size, modified=_stamp(mtime),
                            matched=("both" if (name_match and content_match)
                                     else "content" if content_match else "name"),
                            snippet=snippet))
            if len(hits) >= limit:
                break
        if len(hits) >= limit or scanned > max_files:
            break

    elapsed = round(time.time() - started, 2)
    return {"ok": True, "root": str(base), "query": q, "hits": [h.to_dict() for h in hits],
            "count": len(hits), "scanned": scanned, "skipped_dirs": skipped_dirs,
            "content_search": bool(content), "seconds": elapsed,
            "complete": scanned <= max_files,
            "rendered": ("\n".join([f"Found {len(hits)} file(s) under {base} "
                                    f"({scanned:,} checked in {elapsed}s"
                                    + ("" if scanned <= max_files else ", capped") + "):"]
                                   + [h.line() for h in hits])
                         if hits else
                         f"No file matched '{q}' under {base} "
                         f"({scanned:,} checked).")}


def recent(root: str | os.PathLike, days: int = 7, limit: int = 15) -> Dict[str, Any]:
    """The files that changed most recently - the "what was I working on" view."""
    base = Path(root).expanduser()
    found: List[Hit] = []
    cutoff = time.time() - days * 86400
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                stat = p.stat()
            except OSError:
                continue
            if stat.st_mtime >= cutoff:
                found.append(Hit(str(p), name, stat.st_size, _stamp(stat.st_mtime)))
    found.sort(key=lambda h: h.modified, reverse=True)
    return {"ok": True, "days": days, "count": len(found), "hits": [h.to_dict() for h in found[:limit]],
            "rendered": "\n".join([f"Changed in the last {days} day(s):"]
                                  + [h.line() for h in found[:limit]])}
