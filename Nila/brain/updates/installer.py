"""Artifact installation: staged, verified, atomic, reversible.

Layout under ``$BRAIN_HOME/updates``::

    channels.json          pinned channels
    staging/<version>/     downloaded artifacts (hash-verified)
    installed/<version>/   unpacked, import-tested payloads
    current -> installed/<version>   (atomic symlink swap)
    previous -> installed/<old>      (rollback target)
    history.jsonl          append-only install/rollback log

Nothing touches ``current`` until every artifact has been downloaded, hashed,
size-checked and (for packages) import-tested. A failed install leaves the
previous version running.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util.misc import ensure_dir, now_ms, sha256_hex
from ..util.safe_paths import UnsafePath, resolve_within
from .manifest import Artifact, Manifest, verify_artifact


class InstallError(RuntimeError):
    pass


@dataclass
class InstallReport:
    version: str
    staged: List[str]
    installed_to: str
    rolled_back: bool = False
    notes: List[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"version": self.version, "staged": self.staged, "installed_to": self.installed_to,
                "rolled_back": self.rolled_back, "notes": self.notes or []}


class Installer:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.staging = ensure_dir(self.root / "staging")
        self.installed = ensure_dir(self.root / "installed")
        self.current = self.root / "current"
        self.previous = self.root / "previous"
        self.history = self.root / "history.jsonl"

    # ------------------------------------------------------------------ #
    def log(self, event: str, data: Dict[str, Any]) -> None:
        entry = {"ts": now_ms(), "event": event, **data}
        with open(self.history, "ab") as fh:
            fh.write(json.dumps(entry, sort_keys=True).encode() + b"\n")

    def current_version(self) -> Optional[str]:
        if self.current.is_symlink() or self.current.exists():
            return Path(os.readlink(self.current) if self.current.is_symlink()
                        else self.current).name
        return None

    # ------------------------------------------------------------------ #
    def stage(self, manifest: Manifest, blobs: Dict[str, bytes]) -> Dict[str, Any]:
        """Verify every artifact's hash/size before anything is unpacked."""
        stage_dir = ensure_dir(self.staging / manifest.version)
        report: Dict[str, Any] = {"version": manifest.version, "artifacts": [], "ok": True}
        for art in manifest.artifacts:
            data = blobs.get(art.name)
            if data is None:
                report["ok"] = False
                report["artifacts"].append({"name": art.name, "ok": False, "error": "not downloaded"})
                continue
            check = verify_artifact(data, art)
            dest = stage_dir / Path(art.path).name
            if check["ok"]:
                dest.write_bytes(data)
            report["artifacts"].append({"name": art.name, "path": str(dest), **check})
            if not check["ok"]:
                report["ok"] = False
        if not report["ok"]:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise InstallError(f"artifact verification failed: {report['artifacts']}")
        self.log("staged", {"version": manifest.version,
                            "artifacts": [a["name"] for a in report["artifacts"]]})
        return report

    # ------------------------------------------------------------------ #
    def install(self, manifest: Manifest, blobs: Dict[str, bytes],
                import_test: bool = True) -> InstallReport:
        self.stage(manifest, blobs)
        stage_dir = self.staging / manifest.version
        target = self.installed / manifest.version
        # ``installed/<version>`` must be a real directory we create. A symlink
        # planted there would point the whole install (and every containment
        # check made relative to it) somewhere else.
        if target.is_symlink():
            raise InstallError(f"refusing to install into a symlink: {target}")
        if target.exists():
            shutil.rmtree(target)
        ensure_dir(target)

        notes: List[str] = []
        staged: List[str] = []
        for art in manifest.artifacts:
            src = stage_dir / Path(art.path).name
            staged.append(src.name)
            # `extract_to` is attacker-controlled manifest data. It was already
            # validated by Manifest.parse(); this second check runs on the real
            # filesystem so a symlink planted between parse and install cannot
            # redirect the write either.
            dest_root = self._dest_root(target, art)
            ensure_dir(dest_root)
            if src.suffix == ".zip":
                self._safe_unzip(src, dest_root)
                notes.append(f"unpacked {src.name} -> {dest_root.relative_to(target).as_posix()}")
            else:
                shutil.copy2(src, dest_root / src.name)
                notes.append(f"copied {src.name}")

        if import_test:
            self._import_test(target, manifest, notes, import_test=True)
        else:
            skip = [a.name for a in manifest.artifacts if a.kind == "package"]
            if skip:
                notes.append(f"import test skipped for {', '.join(skip)} (unverified signature)")

        # atomic-ish pointer swap
        old = self.current_version()
        tmp_link = self.root / "current.tmp"
        if tmp_link.is_symlink() or tmp_link.exists():
            tmp_link.unlink()
        tmp_link.symlink_to(target, target_is_directory=True)
        os.replace(tmp_link, self.current)
        if old and old != manifest.version:
            prev = self.installed / old
            if prev.exists():
                tmp_prev = self.root / "previous.tmp"
                if tmp_prev.is_symlink() or tmp_prev.exists():
                    tmp_prev.unlink()
                tmp_prev.symlink_to(prev, target_is_directory=True)
                os.replace(tmp_prev, self.previous)

        report = InstallReport(version=manifest.version, staged=staged,
                              installed_to=str(target), notes=notes)
        self.log("installed", {"version": manifest.version, "from": old, "staged": staged})
        shutil.rmtree(stage_dir, ignore_errors=True)
        return report

    # ------------------------------------------------------------------ #
    @staticmethod
    def _dest_root(target: Path, art: Artifact) -> Path:
        """The directory one artifact unpacks into, proven to be inside ``target``."""
        rel = art.extract_to or art.kind
        try:
            dest = resolve_within(target, rel, what=f"artifact {art.name} extract_to")
        except UnsafePath as exc:
            shutil.rmtree(target, ignore_errors=True)
            raise InstallError(str(exc)) from exc
        return dest

    @staticmethod
    def _safe_unzip(src: Path, dest: Path) -> None:
        """Unzip with zip-slip, symlink and containment protection.

        Entry names are validated by :mod:`brain.util.safe_paths` (which rejects
        ``..`` behind either separator, absolute paths and drive letters) and
        every resolved target is proven to be below ``dest`` on the real
        filesystem - a string-prefix test would accept ``/x/y-private``.
        """
        dest = dest.resolve()
        try:
            with zipfile.ZipFile(src) as zf:
                for info in zf.infolist():
                    name = info.filename
                    if info.external_attr >> 16 & 0o170000 == 0o120000:
                        raise InstallError(f"symlink entries are refused: {name}")
                    if name.endswith("/"):
                        resolve_within(dest, name.rstrip("/"), what="zip entry")
                        continue
                    target = resolve_within(dest, name, what="zip entry")
                    if target.exists() and target.is_symlink():
                        raise InstallError(f"refusing to write through a symlink: {name}")
                zf.extractall(dest)
        except UnsafePath as exc:
            raise InstallError(f"unsafe path in zip: {exc}") from exc
        except zipfile.BadZipFile as exc:
            raise InstallError(f"not a valid zip: {src.name}: {exc}") from exc

    def _import_test(self, target: Path, manifest: Manifest, notes: List[str],
                     import_test: bool = True) -> None:
        """Fail the install if a delivered package cannot even be imported.

        SECURITY: importing a module *executes* its top-level code. That is
        acceptable only when the manifest's signature was actually verified
        against a pinned key - otherwise this step turns "fetch an update" into
        "run someone else's code", and it ran *before* the pointer swap, so a
        failed install had already executed the payload.

        Callers therefore pass ``authenticated=False`` for insecure mode and for
        a channel that pinned nothing (an unsigned manifest). In that case the
        test is skipped and the reason is recorded in the report, so
        ``requires_restart`` still tells the truth about what happened.
        """
        import sys

        for art in manifest.artifacts:
            if art.kind != "package":
                continue
            pkg_root = target / (art.extract_to or art.kind)
            if not pkg_root.exists():
                continue
            marker = art.meta.get("import_module")
            if not marker:
                notes.append(f"no import_module declared for {art.name}; skipped import test")
                continue
            if not import_test:
                notes.append(f"import test skipped for {art.name} (unverified signature)")
                continue
            saved = list(sys.path)
            try:
                sys.path.insert(0, str(pkg_root))
                mod = importlib.import_module(marker)
                importlib.reload(mod)
                notes.append(f"import test ok: {marker} from {pkg_root.name}")
            except Exception as exc:
                shutil.rmtree(target, ignore_errors=True)
                raise InstallError(
                    f"import test failed for {art.name} ({marker}): {type(exc).__name__}: {exc}"
                ) from exc
            finally:
                sys.path[:] = saved

    # ------------------------------------------------------------------ #
    def rollback(self) -> Dict[str, Any]:
        if not self.previous.exists():
            return {"ok": False, "error": "no previous version to roll back to"}
        target = Path(os.readlink(self.previous)) if self.previous.is_symlink() else self.previous
        tmp = self.root / "current.tmp"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(target, target_is_directory=True)
        os.replace(tmp, self.current)
        self.log("rolled_back", {"version": target.name})
        return {"ok": True, "version": target.name, "path": str(target)}

    def prune(self, keep: int = 3) -> List[str]:
        versions = sorted((p.name for p in self.installed.iterdir() if p.is_dir()), reverse=True)
        current = self.current_version()
        removed = []
        for v in versions[keep:]:
            if v == current:
                continue
            shutil.rmtree(self.installed / v, ignore_errors=True)
            removed.append(v)
        return removed
