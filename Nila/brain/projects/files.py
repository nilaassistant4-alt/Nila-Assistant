"""Encrypted file attachments for projects.

Files are sealed with a key derived from the vault master key and written as
opaque ``.pbx`` blobs; the original name, size and hash live only inside the
encrypted project record. Nothing here ever executes or opens a file, and
nothing is read from anywhere except the bytes the caller hands in.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict

from ..crypto.envelope import open_sealed, seal
from ..util.misc import new_id, now_ms

MAX_FILE_BYTES = 8 * 1024 * 1024
DOMAIN = "projfile"
_ID_RE = re.compile(r"^fil_[A-Za-z0-9_-]{4,40}$")


class FileStoreError(ValueError):
    pass


def safe_name(name: str) -> str:
    name = (name or "file").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*').strip(" .")
    return (name or "file")[:120]


class FileStore:
    def __init__(self, root: Path | str, mk: Callable[[], bytes]):
        self.root = Path(root)
        self._mk = mk

    def _path(self, fid: str) -> Path:
        if not _ID_RE.match(fid or ""):
            raise FileStoreError("invalid file id")
        return self.root / f"{fid}.pbx"

    def put(self, filename: str, data: bytes) -> Dict[str, Any]:
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise FileStoreError("the file is empty")
        if len(data) > MAX_FILE_BYTES:
            raise FileStoreError(f"file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
        fid = new_id("fil")
        blob = seal(bytes(data), self._mk(), DOMAIN, fid)
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(fid).with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, self._path(fid))
        return {"id": fid, "name": safe_name(filename), "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "ts": now_ms()}

    def get(self, fid: str) -> bytes:
        p = self._path(fid)
        if not p.exists():
            raise FileStoreError("file not found")
        return open_sealed(p.read_bytes(), self._mk(), DOMAIN, fid)

    def delete(self, fid: str) -> bool:
        p = self._path(fid)
        if p.exists():
            p.unlink()
            return True
        return False
