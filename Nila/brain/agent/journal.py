"""How it is actually going: a streak, and what you got done.

Two kinds of owner open Nila. One uses it every morning for a month and then
stops for a month; the other uses it twice and then forgets it exists. A single
number that answers "am I still keeping this up?" - and that only ever counts
**days Nila was really opened** - is the difference between a tool that is used
and one that is uninstalled.

The rules here are deliberately narrow, because a flattering number is worse than
no number:

* A day counts only when the vault was unlocked that day (a real login, recorded
  in the hash-chained audit journal). Not a dashboard poll, not a background
  timer - the owner showing up.
* The streak counts **backwards from today**; a day with no login ends it. Today
  with no login yet does not break yesterday's streak (it is still in progress),
  but yesterday without one does.
* Best streak, total days and the last 7 days are computed from the same journal,
  so no separate store can drift from the truth.

Nothing here writes anything. `activity()` reads the audit journal that already
exists, so it cannot invent a day, and it says `days_in_app: 0` on a fresh vault
rather than a cheerful 1.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

#: Audit events that mean "the owner opened Nila". A dashboard poll is not here
#: on purpose: a tab left open all week must not look like seven days of use.
LOGIN_EVENTS = ("session_unlock",)
UNLOCK_WORD = "unlock"
#: How much of the journal to read. 10 000 entries covers months of normal use.
READ_LIMIT = 10_000


def _day(ts: Any) -> Optional[_dt.date]:
    """The local calendar day of an audit timestamp."""
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            # audit `ts` is epoch milliseconds
            return _dt.datetime.fromtimestamp(float(ts) / 1000.0).date()
        text = str(ts).strip().replace("Z", "+00:00")
        parsed = _dt.datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _login_days(vault: Any) -> List[_dt.date]:
    days = set()
    try:
        entries = vault.read_audit(READ_LIMIT)
    except Exception:
        return []
    for entry in entries or []:
        try:
            event = str(entry.get("event") or "")
            if event not in LOGIN_EVENTS and UNLOCK_WORD not in event:
                continue
            day = _day(entry.get("ts", entry.get("time", entry.get("at"))))
        except AttributeError:
            continue
        if day:
            days.add(day)
    return sorted(days)


def streak(days: List[_dt.date], today: Optional[_dt.date] = None) -> Dict[str, Any]:
    """Current streak, best streak, and whether today already counted."""
    today = today or _dt.date.today()
    have = set(days)
    current = 0
    cursor = today
    if today not in have:
        # Today is not over: yesterday's streak is still alive until midnight.
        cursor = today - _dt.timedelta(days=1)
    while cursor in have:
        current += 1
        cursor -= _dt.timedelta(days=1)
    best, run, previous = 0, 0, None
    for day in sorted(have):
        run = run + 1 if previous and (day - previous).days == 1 else 1
        best = max(best, run)
        previous = day
    return {"current": current, "best": max(best, current),
            "today": today in have, "last_day": (max(have).isoformat() if have else "")}


def last_seven(days: List[_dt.date], today: Optional[_dt.date] = None) -> List[Dict[str, Any]]:
    """Which of the last seven days were used - the honest little bar chart."""
    today = today or _dt.date.today()
    have = set(days)
    out = []
    for back in range(6, -1, -1):
        day = today - _dt.timedelta(days=back)
        out.append({"date": day.isoformat(),
                    "weekday": day.strftime("%a"),
                    "used": day in have})
    return out


def activity(vault: Any = None, memory: Any = None,
             today: Optional[_dt.date] = None) -> Dict[str, Any]:
    """The whole picture, safe to call when the vault is locked or empty."""
    days = _login_days(vault) if vault is not None else []
    today = today or _dt.date.today()
    state = streak(days, today)
    payload: Dict[str, Any] = {
        "ok": True,
        "days_in_app": len(days),
        "current_streak": state["current"],
        "best_streak": state["best"],
        "today": state["today"],
        "last_day": state["last_day"],
        "week": last_seven(days, today),
        "used_today": state["today"],
        "note": ("A day counts when the vault is unlocked - the dashboard polling "
                 "in the background does not count."),
    }
    if memory is not None and payload:
        # A memory store pinned to a sealed session: asked for counts only, and a
        # failure is not worth an error, because the streak is still true without it.
        try:
            payload["records"] = int(memory.stats().get("records") or 0)
        except Exception:
            pass
    if not days:
        payload["note"] = ("No day counted yet. The first time you open Nila, "
                           "today becomes day one.")
    elif not state["today"]:
        payload["nudge"] = ("Today has not counted yet - open the dashboard once "
                            "and the streak continues.")
    elif state["current"] >= 2:
        payload["nudge"] = f"{state['current']} days in a row."
    return payload


def line(payload: Dict[str, Any]) -> str:
    """One sentence for the terminal, never longer than it needs to be."""
    if not payload.get("days_in_app"):
        return "Day one starts the first time you open Nila."
    bits = [f"{payload['current_streak']} day"
            + ("" if payload["current_streak"] == 1 else "s") + " in a row"]
    bits.append(f"{payload['days_in_app']} day"
                + ("" if payload['days_in_app'] == 1 else "s") + " used in all")
    if payload.get("best_streak", 0) > payload.get("current_streak", 0):
        bits.append(f"best {payload['best_streak']}")
    if not payload.get("today"):
        bits.append("today not counted yet")
    return " · ".join(bits)
