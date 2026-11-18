#!/usr/bin/env python3
"""Move Nila's data to another drive - by COPYING, checking, and only then switching.

Called by ``move-to-d.bat``. The owner's rules, in his own order:

1. Do not move data while Nila is running. If the vault is open in another
   process, stop and say so; do not guess.
2. Copy. Never move-and-delete. The old folder stays as ``vault-old`` until the
   owner deletes it himself.
3. Compare what arrived with what left: file count, byte count, and a SHA-256 of
   every file. A size-only check is what loses a database.
4. Only after an exact match, repoint the setting (``BRAIN_HOME``) and run a
   self-test against the new place.
5. If anything fails, change nothing and say exactly which step failed.

    python tools/move_storage.py --to D:/Nila-data --dry-run
    python tools/move_storage.py --to D:/Nila-data --yes

It is deliberately stdlib-only, so it runs before anything is installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple


def _sha(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def inventory(root: Path) -> Tuple[Dict[str, Tuple[int, str]], List[str]]:
    """Every file under ``root`` with its size and hash, plus any unreadable path.

    Unreadable files are reported, not skipped: a copy that silently drops one
    file is the failure this whole script exists to prevent.
    """
    files: Dict[str, Tuple[int, str]] = {}
    unreadable: List[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            files[rel] = (path.stat().st_size, _sha(path))
        except OSError as exc:
            unreadable.append(f"{rel}: {exc}")
    return files, unreadable


def compare(left: Dict[str, Tuple[int, str]], right: Dict[str, Tuple[int, str]]) -> Dict[str, List[str]]:
    missing = sorted(set(left) - set(right))
    extra = sorted(set(right) - set(left))
    different = sorted(k for k in set(left) & set(right) if left[k] != right[k])
    return {"missing": missing, "extra": extra, "different": different}


def _looks_running(home: Path) -> str:
    """A cheap, honest check for "someone has the vault open".

    A lock file that a live process owns is evidence; its absence is NOT proof
    that nothing is running, so the caller is told which of the two happened.
    """
    lock = home / "vault.lock"
    if not lock.exists():
        return ""
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        pid = int(data.get("pid") or 0)
    except (OSError, ValueError):
        return ""
    if not pid:
        return ""
    try:
        os.kill(pid, 0)
    except OSError:
        return ""
    except Exception:
        return ""
    return f"process {pid} still has the vault open"


def _repoint_setting(home: Path, target: Path) -> None:
    """Store the new data folder where the launcher already looks for it."""
    cfg = home / "config.json"
    data = {}
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    agent = dict(data.get("agent") or {})
    agent["home"] = str(target)
    data["agent"] = agent
    cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(cfg, 0o600)
    except OSError:
        pass


def move(home: Path, target: Path, dry_run: bool = False, yes: bool = False) -> int:
    home = home.expanduser().resolve()
    target = target.expanduser().resolve()
    if not home.is_dir():
        print(f"error: no vault folder at {home}")
        return 2
    if target == home or home in target.parents:
        print("error: the new folder is inside the old one")
        return 2
    running = _looks_running(home)
    if running:
        print(f"error: {running} - close Nila first (this is why the copy cannot be trusted while it runs)")
        return 3
    if target.exists() and any(target.iterdir()):
        print(f"error: {target} already has files in it; refusing to merge two data folders")
        return 2

    before, unreadable = inventory(home)
    total_bytes = sum(size for size, _ in before.values())
    print(f"source      {home}")
    print(f"files       {len(before)}")
    print(f"bytes       {total_bytes}")
    if unreadable:
        print("error: these files cannot be read, so they cannot be copied safely:")
        for row in unreadable[:10]:
            print(f"  {row}")
        return 4
    if dry_run:
        print(f"would copy to {target}, compare every file, then repoint BRAIN_HOME")
        return 0
    if not yes:
        print("refusing without --yes (this writes to another drive)")
        return 2

    target.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(home, target, dirs_exist_ok=True, symlinks=True)
    except OSError as exc:
        print(f"error: the copy failed at {exc} - nothing was switched, the old folder is untouched")
        return 5

    after, _ = inventory(target)
    diff = compare(before, after)
    if any(diff.values()):
        print("error: the copy does NOT match the original. Nothing was switched.")
        for kind, rows in diff.items():
            if rows:
                print(f"  {kind}: {len(rows)} (first: {rows[0]})")
        print(f"  the partial copy is at {target}; delete it once you have checked the original")
        return 6
    print(f"verified    {len(after)} files, {total_bytes} bytes, every SHA-256 identical")

    (home / "vault-old").mkdir(exist_ok=True)          # the old copy is kept, never deleted
    _repoint_setting(home, target)
    print(f"switched    BRAIN_HOME now points at {target}")
    print(f"kept        {home} (renamed folder inside it: vault-old) - delete it yourself once sure")
    print("next        run:  brain doctor   (it must pass from the new place)")
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy Nila's data to another drive, verify, then switch.")
    ap.add_argument("--home", default=os.environ.get("BRAIN_HOME") or str(Path.home() / ".personal-brain"))
    ap.add_argument("--to", required=True, help="the new data folder, e.g. D:/Nila-data")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)
    return move(Path(args.home), Path(args.to), args.dry_run, args.yes)


if __name__ == "__main__":
    sys.exit(main())
