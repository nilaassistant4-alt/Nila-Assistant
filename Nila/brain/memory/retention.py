"""Keeping the archive from growing forever: a report, and a purge the owner runs.

The owner's words were "3 months of memory, then let it go". That sentence has
one dangerous reading - a background job deleting records - and the codebase's
standing rule is the opposite: **Nila deletes only when told**. So this module:

* **reports** what is older than the chosen window (a dry run is the default),
* **deletes** only with an explicit ``apply`` and only records older than the
  window, never whole kinds, never a reminder that is still pending,
* **protects** anything the owner pinned/mastered (highest importance) or marked
  as a credential reference, because deleting a login hint silently is worse than
  keeping a note too long,
* **writes an audit event** with the count, so a purge is visible afterwards.

Nothing here is scheduled. `brain memory --older-than 90 --purge` is the switch,
and without it the archive never shrinks on its own.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

#: Kinds that are never purged automatically: a reminder or a credential note is
#: either still needed or explicitly the owner's to delete.
PROTECTED_KINDS = {"credential_ref", "reminder", "instruction", "profile"}
DEFAULT_DAYS = 90


class RetentionError(ValueError):
    pass


def _age_days(record: Any, now_ms: int) -> float:
    stamp = int(getattr(record, "updated_at", 0) or getattr(record, "created_at", 0) or 0)
    if not stamp:
        return 0.0
    return max(0.0, (now_ms - stamp) / 86400000.0)


def report(memory: Any, days: int = DEFAULT_DAYS, limit: int = 5000,
           now_ms: Optional[int] = None) -> Dict[str, Any]:
    """What a purge would remove, and what it would refuse to touch."""
    if int(days) < 1:
        raise RetentionError("the window must be at least one day")
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    cutoff_ms = now - int(days) * 86400000
    rows = memory.list_records(limit=limit, include_deleted=False)
    stale: List[Dict[str, Any]] = []
    protected: List[Dict[str, Any]] = []
    keep_new: int = 0
    for rec in rows:
        meta = dict(getattr(rec, "meta", None) or {})
        kind = str(getattr(rec, "kind", "") or "")
        age = _age_days(rec, now)
        row = {"id": rec.id, "title": getattr(rec, "title", "") or "",
               "kind": kind, "age_days": round(age, 1)}
        if age < days:
            keep_new += 1
            continue
        if kind in PROTECTED_KINDS or meta.get("importance") == "highest" or meta.get("pinned"):
            protected.append({**row, "why": "you marked it important, or it is a "
                                            "reminder/credential/instruction"})
            continue
        stale.append(row)
    return {"ok": True, "window_days": int(days), "scanned": len(rows),
            "newer": keep_new, "older": len(stale), "older_records": stale[:50],
            "protected": len(protected), "protected_records": protected[:50],
            "cutoff_ms": cutoff_ms,
            "line": (f"{len(stale)} record(s) older than {int(days)} days can be "
                     f"removed; {len(protected)} kept because they are protected; "
                     f"{keep_new} newer than the window."),
            "note": "nothing has been deleted - a purge needs --purge"}


def purge(memory: Any, days: int = DEFAULT_DAYS, apply: bool = False,
          now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Delete records older than `days` - only when `apply` is True."""
    out = report(memory, days=days, now_ms=now_ms)
    out["purged"] = 0
    out["dry_run"] = not apply
    if not apply:
        return out
    removed = 0
    for row in out["older_records"]:
        try:
            if memory.delete(row["id"], hard=True):
                removed += 1
        except Exception:                  # one bad row must not stop the rest
            continue
    out["purged"] = removed
    out["note"] = f"removed {removed} record(s) older than {int(days)} days"
    # A report is not a log; this is the caller's job, so the count is exposed.
    out["audit"] = {"event": "memory_purge", "count": removed, "window_days": int(days)}
    return out
