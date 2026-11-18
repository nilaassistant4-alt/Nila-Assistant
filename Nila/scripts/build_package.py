#!/usr/bin/env python3
"""Build the distributable ZIP and verify it before declaring success.

Replaces an earlier bash version. That one excluded ``*/updates/*`` to keep
runtime update data out of the archive, which also silently swallowed
``brain/updates/`` — the entire signed-update source package. The archive built,
extracted, and passed the full test suite, because no test imported the module.
An external security probe caught it by reading the file list.

Two lessons are encoded here:

1. Exclusions are matched against **relative paths inside the project**, with
   explicit prefixes for runtime data. There is no bare ``*/name/*`` pattern,
   because that matches source packages too.
2. The build **verifies its own output** and refuses to leave a bad archive
   behind: every source module must be present, every required file must be
   present, and no cache or secret file may ride along.

Usage::

    python3 scripts/build_package.py [name] [output_dir]
"""

from __future__ import annotations

import fnmatch
import hashlib
import pathlib
import shutil
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: directories that are never part of a distribution
EXCLUDED_DIRS = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git",
    ".venv", "venv", ".idea", ".vscode", ".data", ".personal-brain",
    "brain-sync-data", "node_modules", ".tox", ".nox",
    # Build output from `pip wheel` / `python -m build`. Without these the
    # distributable silently absorbed Nila/build/lib/brain/... and
    # Nila/personal_brain.egg-info/* (14 extra entries), so two builds of the
    # same source tree produced different archives.
    "build", "dist", "*.egg-info",
}

#: directory-name patterns, matched as a whole component
EXCLUDED_DIR_GLOBS = ("*.egg-info",)

#: runtime/secret file names that must never ship, wherever they appear
EXCLUDED_NAMES = {
    "memory.db", "memory.db-wal", "memory.db-shm", "identity.json",
    "vault.bundle", "permissions.json", "permissions.pending.json",
    "permissions.PANIC", ".env", "usage.json",
}

EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".pyd", ".zip", ".log")

#: prefix-matched runtime paths relative to the project root
EXCLUDED_PREFIXES = ("audit", "sync/remote/", "tmp/", "updates/installed/",
                     "updates/staging/", "updates/history")

#: files that must be in the archive or the build fails
REQUIRED_FILES = [
    "README.md", "pyproject.toml", "requirements.txt", "requirements-optional.txt",
    ".env.example", ".gitignore", "brain.py",
    "docs/ARCHITECTURE.md", "docs/THREAT_MODEL.md", "docs/PERMISSIONS.md",
    "docs/PII_SCHEMA.md", "docs/MANDATE_COMPLIANCE.md",
    "brain/identity/voice_model.json", "config/integrations.example.json",
    "scripts/build_package.py", "scripts/run_tests.py",
    "scripts/make_release.py", "scripts/nila_mandate_probe.py",
    "nila_mandate_tests.py",
    "brain/api/dashboard.py", "brain/api/static/dashboard.html",
    "brain/api/static/dashboard.css", "brain/api/static/dashboard.js",
    # Chrome's "Install as app" needs both, and a missing icon fails silently
    # (the prompt simply never appears), so the builder must require them.
    "brain/api/static/manifest.json", "brain/api/static/icon.svg",
    # v0.1 (Windows owner kit)
    "START_HERE.md", "ROADMAP.md", "PROGRESS.md", "CHANGELOG.md", "docs/CAPABILITIES.md",
    "setup.bat", "start-nila.bat", "report.bat", "nila-env.bat", "nila-cmd.bat",
    "brain/projects/store.py", "brain/projects/files.py", "brain/projects/tools.py",
    "brain/agent/router.py", "brain/agent/ledger.py", "brain/diagnostics.py",
    "tests/test_v01.py",
    # v0.2 - the D-drive helpers the owner specified
    "move-to-d.bat", "backup-to-d.bat", "install-local-ai.bat",
    "tools/move_storage.py", "tools/backup_check.py", "tools/install_local_ai.py",
    # stage 11 - the phone answers, and the .bat files are checked by a test
    "nila-phone.bat", "nila-common-check.bat",
    "brain/agent/phone.py", "brain/agent/fix.py", "brain/agent/__main_phone.py",
    "tools/install_voice.py", "tests/test_bat_hygiene.py",
    "tests/test_phone_two_way_acceptance.py",
    # stage 11 polish: the streak, the one-command start, and the page actually run
    "brain/agent/journal.py", "tests/test_habit_and_start_acceptance.py",
    "tests/test_dashboard_dom.py", "tests/_dom_check.js",
]

#: source trees whose .py files must ALL be present
SOURCE_TREES = ("brain", "cloud", "tests", "scripts")


def is_excluded(rel: str, name: str) -> bool:
    parts = pathlib.PurePosixPath(rel).parts
    if any(p in EXCLUDED_DIRS or any(fnmatch.fnmatch(p, g) for g in EXCLUDED_DIR_GLOBS)
           for p in parts):
        return True
    if name in EXCLUDED_NAMES:
        return True
    if name.endswith(EXCLUDED_SUFFIXES):
        return True
    return any(rel.startswith(p) or fnmatch.fnmatch(rel, p + "*")
               for p in EXCLUDED_PREFIXES)


def collect() -> list[pathlib.Path]:
    out = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if is_excluded(rel, path.name):
            continue
        out.append(path)
    return out


def main(argv: list[str]) -> int:
    name = argv[1] if len(argv) > 1 else "personal-brain-mvp"
    out_dir = pathlib.Path(argv[2] if len(argv) > 2 else ROOT.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{name}.zip"
    prefix = "Nila/"

    if zip_path.exists():
        zip_path.unlink()

    files = collect()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, prefix + path.relative_to(ROOT).as_posix())

    # ------------------------------------------------------------------ #
    # verify what was actually written - never trust the build step alone
    # ------------------------------------------------------------------ #
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        bad = zf.testzip()
    stripped = {n[len(prefix):] if n.startswith(prefix) else n for n in names}

    failures: list[str] = []
    if bad:
        failures.append(f"corrupt entry in archive: {bad}")

    # 1. every source module must be present
    expected_src = set()
    for tree in SOURCE_TREES:
        base = ROOT / tree
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if any(part in EXCLUDED_DIRS for part in p.parts):
                continue
            expected_src.add(p.relative_to(ROOT).as_posix())
    missing_src = sorted(expected_src - stripped)
    if missing_src:
        failures.append(f"missing source files ({len(missing_src)}): "
                        + ", ".join(missing_src[:12]))

    # 2. required non-python files
    missing_req = [f for f in REQUIRED_FILES if f not in stripped]
    if missing_req:
        failures.append("missing required files: " + ", ".join(missing_req))

    # 3. nothing sensitive or generated
    leaks = []
    for n in sorted(stripped):
        parts = pathlib.PurePosixPath(n).parts
        if any(p in EXCLUDED_DIRS or any(fnmatch.fnmatch(p, g) for g in EXCLUDED_DIR_GLOBS)
               for p in parts):
            leaks.append(n)
        elif pathlib.PurePosixPath(n).name in EXCLUDED_NAMES:
            leaks.append(n)
        elif n.endswith((".pyc", ".pyo")):
            leaks.append(n)
    if leaks:
        failures.append(f"cache/secret files present ({len(leaks)}): "
                        + ", ".join(leaks[:12]))

    if failures:
        print("BUILD FAILED - archive removed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        zip_path.unlink(missing_ok=True)
        return 1

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"built {zip_path}")
    print(f"  source modules : {len(expected_src)}/{len(expected_src)} verified present")
    print(f"  total entries  : {len([n for n in names if not n.endswith('/')])}")
    print(f"  size           : {zip_path.stat().st_size:,} bytes")
    print(f"  sha256         : {digest}")
    print("  integrity      : archive verified, no caches or secrets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
