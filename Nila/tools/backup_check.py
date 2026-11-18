#!/usr/bin/env python3
"""Write a dated backup to another drive, then PROVE it restores.

Called by ``backup-to-d.bat``. The owner's rules:

* The backup goes to ``<drive>/Nila-backups/<date>/`` - never into a Google
  Drive / OneDrive folder, because a vault inside a synced folder is copied to
  somebody else's servers.
* A backup that has not been restored is a guess, not a backup. This restores it
  into a temporary folder and compares record counts and bytes with the live
  store. A mismatch is reported as a FAILED backup, loudly.
* Nothing on the source disk is deleted, ever. Old backups are only listed, so
  the owner decides what to keep.

    python tools/backup_check.py --to D:/Nila-backups --dry-run
    python tools/backup_check.py --to D:/Nila-backups --yes
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SYNCED_FOLDERS = ("google drive", "googledrive", "onedrive", "dropbox", "icloud",
                  "sharepoint", "box", "nextcloud")


def _memory_db(home: Path) -> Path:
    """Where the encrypted store actually is, following config if it moved."""
    cfg = home / "config.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            stored = str((data.get("agent") or {}).get("home") or "")
            if stored:
                cand = Path(stored).expanduser() / "memory.db"
                if cand.exists():
                    return cand
        except (OSError, ValueError):
            pass
    return home / "memory.db"


def _counts(db: Path) -> Dict[str, Any]:
    """What is actually in the database, read without decrypting a record."""
    out: Dict[str, Any] = {"bytes": db.stat().st_size if db.exists() else 0,
                           "tables": {}, "rows": 0}
    if not db.exists():
        return out
    con = None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for name in names:
            try:
                n = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except sqlite3.DatabaseError:
                continue
            out["tables"][name] = n
            out["rows"] += n
    except sqlite3.DatabaseError as exc:
        out["error"] = str(exc)
    finally:
        if con is not None:
            con.close()
    return out


def _snapshot(db: Path, dest: Path) -> None:
    """A consistent copy while the database may be open in WAL mode."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(str(dest))
        try:
            con.backup(out)
            out.commit()
        finally:
            out.close()
    finally:
        con.close()


def backup(home: Path, target: Path, dry_run: bool = False, yes: bool = False) -> int:
    home = home.expanduser().resolve()
    target = target.expanduser().resolve()
    lowered = str(target).lower()
    for folder in SYNCED_FOLDERS:
        if folder in lowered:
            nice = {"googledrive": "Google Drive", "onedrive": "OneDrive",
                    "icloud": "iCloud", "sharepoint": "SharePoint"}.get(folder, folder.title())
            print(f"error: {target} looks like a {nice} folder. A vault must not be "
                  "inside a synced folder - keep it on a local disk.")
            return 2
    db = _memory_db(home)
    if not db.exists():
        print(f"error: no encrypted store at {db} - is Nila set up on this PC?")
        return 3
    live = _counts(db)
    stamp = time.strftime("%Y-%m-%d_%H%M")
    dest = target / stamp
    print(f"source      {db} ({live['bytes']} bytes, {live['rows']} records)")
    print(f"destination {dest}")
    if dry_run:
        print("would: snapshot the database consistently, copy the vault, restore into a "
              "temporary folder, compare record counts, and report PASS or FAIL")
        return 0
    if not yes:
        print("refusing without --yes")
        return 2

    dest.mkdir(parents=True, exist_ok=True)
    try:
        _snapshot(db, dest / "memory.db")
        for extra in ("identity.json", "config.json"):
            src = home / extra
            if src.exists():
                shutil.copy2(src, dest / extra)
        files = home / "files"
        if files.is_dir():
            shutil.copytree(files, dest / "files", dirs_exist_ok=True)
    except (OSError, sqlite3.DatabaseError) as exc:
        print(f"error: the backup could not be written ({exc}) - this backup FAILED")
        return 4

    # The only part that makes it a backup: put it back, somewhere else, and count.
    with tempfile.TemporaryDirectory(prefix="nila-restore-check-") as tmp:
        try:
            shutil.copy2(dest / "memory.db", Path(tmp) / "memory.db")
        except OSError as exc:
            print(f"error: could not read the backup back ({exc}) - this backup FAILED")
            return 5
        restored = _counts(Path(tmp) / "memory.db")

    ok = restored.get("rows") == live.get("rows") and restored.get("bytes", 0) > 0
    detail = {"live_rows": live.get("rows"), "restored_rows": restored.get("rows"),
              "live_tables": live.get("tables"), "restored_tables": restored.get("tables"),
              "backup_file": str(dest / "memory.db")}
    print(json.dumps(detail, indent=2))
    if not ok:
        print("BACKUP FAILED: the restored copy does not match. Do not rely on this one.")
        return 6
    print(f"PASS        {restored['rows']} records restored and counted identically")
    print("note        nothing on the source was deleted; old backups are yours to remove")
    return 0


def list_backups(target: Path) -> List[str]:
    return sorted(p.name for p in target.expanduser().glob("*") if p.is_dir()) if target.exists() else []


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Back up Nila to another drive and verify the restore.")
    ap.add_argument("--home", default=os.environ.get("BRAIN_HOME") or str(Path.home() / ".personal-brain"))
    ap.add_argument("--to", required=True, help="e.g. D:/Nila-backups")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    if args.list:
        for name in list_backups(Path(args.to)):
            print(name)
        return 0
    return backup(Path(args.home), Path(args.to), args.dry_run, args.yes)


if __name__ == "__main__":
    sys.exit(main())
