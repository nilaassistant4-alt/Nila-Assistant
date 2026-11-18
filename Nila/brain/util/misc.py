"""Small shared helpers: ids, time, hashing, safe file IO, optional imports."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

EPOCH = 1_700_000_000  # fixed epoch -> smaller varints, stable across installs


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str = "") -> str:
    raw = base64.urlsafe_b64encode(secrets.token_bytes(12)).decode().rstrip("=")
    return f"{prefix}_{raw}" if prefix else raw


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


def b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def loads(text: str | bytes) -> Any:
    return json.loads(text)


def ensure_dir(path: Path | str, mode: int = 0o700) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, mode)
    except OSError:
        pass
    return p


def write_private(path: Path | str, data: bytes) -> None:
    """Write atomically with 0600 perms."""
    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def read_bytes(path: Path | str) -> bytes:
    return Path(path).read_bytes()


def slugify(text: str, maxlen: int = 48) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    out = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:maxlen] or "item"


def mask(text: str, keep: int = 4) -> str:
    """Redact the middle of a secret for logging."""
    if len(text) <= keep:
        return "*" * len(text)
    return text[:keep] + "*" * 6 + text[-2:]


def try_import(name: str) -> Any | None:
    """Optional dependency loader - never raises."""
    try:
        import importlib

        return importlib.import_module(name)
    except Exception:
        return None


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def retry(fn: Callable[[], Any], attempts: int = 3, delay: float = 0.4) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - network dependent
            last = exc
            time.sleep(delay * (2 ** i))
    raise last if last else RuntimeError("retry failed")
