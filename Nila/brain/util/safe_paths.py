"""Containment helpers: the ONE way this project decides a path is "inside".

Why a module and not an inline check
------------------------------------
Three separate places used ``str(candidate).startswith(str(root))`` to test
containment. That test is wrong: ``/srv/releases-private/x`` starts with
``/srv/releases``, so a symlink or a sibling directory with a matching prefix
passes. It was reproducible against the update server, the sync server and the
installer's zip extraction.

Two rules now live here and nowhere else:

1. **Containment is decided on resolved paths** (``Path.resolve()`` follows
   symlinks), with ``os.path.commonpath``-style boundary logic - never string
   prefixes. ``/a/bc`` is not inside ``/a/b``.
2. **A caller who builds a destination from untrusted input calls
   ``safe_relative()``** *before* touching the disk. It rejects absolute paths,
   drive letters, ``..`` segments, NUL bytes and anything that does not survive
   being joined onto the root.

Both raise :class:`UnsafePath` so callers can turn the failure into a clear
refusal instead of an ``OSError`` mid-write.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]

#: longest relative path we will accept; well above any real layout
MAX_RELATIVE_CHARS = 400


class UnsafePath(ValueError):
    """A path escaped (or tried to escape) the directory it belongs in."""


def _resolved(path: PathLike) -> Path:
    """Resolve without requiring existence (``strict=False`` on every OS)."""
    try:
        return Path(path).expanduser().resolve(strict=False)
    except OSError as exc:                      # pragma: no cover - exotic FS
        raise UnsafePath(f"cannot resolve {path!r}: {exc}") from exc


def is_within(child: PathLike, root: PathLike) -> bool:
    """True when ``child`` is ``root`` itself or genuinely below it.

    Boundary-safe: ``/a/bc`` is NOT within ``/a/b``. Symlinks are followed
    first, so a link pointing outside the root is outside.
    """
    c, r = _resolved(child), _resolved(root)
    if c == r:
        return True
    try:
        c.relative_to(r)
        return True
    except ValueError:
        return False


def safe_relative(rel: str, *, what: str = "path") -> Path:
    """Validate an untrusted *relative* path and return it normalised.

    Refuses, with :class:`UnsafePath`:

    * empty values, NUL bytes, absurd lengths
    * absolute paths (``/etc/passwd``, ``C:\\Windows``)
    * any ``..`` segment, including ones that would normalise away
    * Windows-style separators smuggled into a POSIX path (``a\\..\\b``)
    * a value that normalises to nothing (``.``)
    """
    raw = str(rel or "").strip()
    if not raw:
        raise UnsafePath(f"{what} is empty")
    if "\x00" in raw:
        raise UnsafePath(f"{what} contains a NUL byte")
    if len(raw) > MAX_RELATIVE_CHARS:
        raise UnsafePath(f"{what} is longer than {MAX_RELATIVE_CHARS} characters")
    # A backslash is a separator on Windows; treat it as one everywhere so a
    # manifest cannot hide ".." behind it on the platform that matters.
    unified = raw.replace("\\", "/")
    if unified.startswith("/") or (len(unified) > 1 and unified[1] == ":"):
        raise UnsafePath(f"{what} must be relative, got {rel!r}")
    parts = [p for p in unified.split("/") if p not in ("", ".")]
    if not parts:
        raise UnsafePath(f"{what} normalises to nothing: {rel!r}")
    if any(p == ".." for p in parts):
        raise UnsafePath(f"{what} contains '..': {rel!r}")
    if any(p in (".", "") for p in parts):
        raise UnsafePath(f"{what} is malformed: {rel!r}")
    return Path(*parts)


def resolve_within(root: PathLike, rel: str, *, what: str = "path") -> Path:
    """Join a validated relative path onto ``root`` and prove it stays inside.

    Raises :class:`UnsafePath` if the result resolves outside ``root`` - which
    also catches a symlink *inside* the root that points somewhere else.
    """
    rel_path = safe_relative(rel, what=what)
    root_p = Path(root)
    target = root_p / rel_path
    if not is_within(target, root_p):
        raise UnsafePath(f"{what} escapes {root_p}: {rel!r}")
    return target


def open_within(root: PathLike, rel: str, *, what: str = "path") -> Path:
    """Return a resolved path inside ``root`` that is safe to *read*.

    Stricter than :func:`resolve_within`: the final component must not be a
    symlink at all. Used by servers that hand bytes back to a client, where
    following a planted link is the whole attack.
    """
    target = resolve_within(root, rel, what=what)
    if target.is_symlink():
        raise UnsafePath(f"{what} is a symlink, which is refused: {rel!r}")
    if not target.is_file():
        raise UnsafePath(f"{what} is not a regular file: {rel!r}")
    return target


__all__ = ["UnsafePath", "is_within", "safe_relative", "resolve_within", "open_within",
           "MAX_RELATIVE_CHARS"]
