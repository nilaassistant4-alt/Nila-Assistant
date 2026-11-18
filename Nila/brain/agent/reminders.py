"""Reminders: "tell me at 4:30", kept on this PC and never sent anywhere.

The owner's first request, and the shape is deliberate:

* **Offline only.** A reminder is a time and a sentence. Nothing about it is
  worth a network call, so this module makes none.
* **Rendered by the code, never by a model.** The owner's own words are stored
  and shown back, untruncated and unparaphrased: an appointment that comes back
  as a summary is worse than no reminder at all.
* **Stored in the encrypted memory store**, as records of kind ``reminder``, so
  a reminder inherits the audit trail, the backup, the tier rules and the
  forget path that already exist. No second database, no second key.
* **Honest about time.** If the PC was asleep when a reminder was due, the
  next check says it was missed and when - it does not silently pretend the
  moment is now.

Time parsing is a small, explicit grammar rather than a model: it must give the
same answer every time, and it must refuse when unsure instead of guessing a
date. The supported forms are listed in ``HELP``.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

KIND = "reminder"
TAG = "reminder"
MAX_MINUTES_AHEAD = 60 * 24 * 366 * 5      # "five years" is the polite ceiling

HELP = """Reminders are read like this:

    remind me in 10 minutes to drink water
    remind me at 4:30 to call the shop
    remind me tomorrow at 9 to take the tablet
    remind me on 12/12 at 18:00 to pay the bill
    remind me every day at 22:00 to take the tablet
    remind me every monday at 8 to open the shop

Tamil words also work: "நிமிடம்" (minutes), "மணி" (hour/o'clock),
"நாளைக்கு" (tomorrow), "தினமும்" (every day), "நினைவூட்டு" (remind).
Then: `reminders` lists them, `done 2` closes one, `snooze 2 10` gives it ten
more minutes, `cancel 2` removes it.
"""

#: Words that mean "a reminder", in the two registers the owner actually uses.
_TRIGGER = re.compile(
    r"\b(remind|reminder|alert me|wake me)\b|"
    r"(நினைவூட்டு|நினைவு\s*படுத்து|ஞாபகப்படுத்து)", re.I)

_UNIT_MIN = {
    "minute": 1, "minutes": 1, "min": 1, "mins": 1, "m": 1,
    "நிமிடம்": 1, "நிமிஷம்": 1, "நிமிடத்தில்": 1,
    "hour": 60, "hours": 60, "hr": 60, "hrs": 60, "h": 60,
    "மணி": 60, "மணிநேரம்": 60, "மணி நேரம்": 60,
    "second": 1 / 60, "seconds": 1 / 60, "sec": 1 / 60, "s": 1 / 60,
    "நொடி": 1 / 60,
    "day": 1440, "days": 1440, "நாள்": 1440,
}

_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
             "forty five": 45, "half an": 0.5, "half": 0.5,
             "ஒரு": 1, "இரண்டு": 2, "மூன்று": 3, "நான்கு": 4, "ஐந்து": 5,
             "பத்து": 10, "பதினைந்து": 15, "இருபது": 20, "முப்பது": 30}

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
             "saturday": 5, "sunday": 6,
             "திங்கள்": 0, "செவ்வாய்": 1, "புதன்": 2, "வியாழன்": 3,
             "வெள்ளி": 4, "சனி": 5, "ஞாயிறு": 6}


class ReminderError(ValueError):
    """Bad input, said plainly. Never guessed around."""


@dataclass
class When:
    at_ms: int
    repeat: str = ""            # "" | "daily" | "weekly"
    weekday: Optional[int] = None
    text: str = ""
    matched: str = ""           # the words that produced this time
    next_ms: int = 0            # the first firing time AFTER at_ms (repeats)

    def to_dict(self) -> Dict[str, Any]:
        return {"at_ms": self.at_ms, "repeat": self.repeat, "weekday": self.weekday,
                "text": self.text, "matched": self.matched, "next_ms": self.next_ms}


# --------------------------------------------------------------------------- #
# time
# --------------------------------------------------------------------------- #
def _now_ms(now: Optional[_dt.datetime] = None) -> int:
    n = now or _dt.datetime.now()
    return int(n.timestamp() * 1000)


def now_ms() -> int:
    """Wall clock in milliseconds - the one definition this module uses."""
    return _now_ms()


def _ms(dt: _dt.datetime) -> int:
    return int(dt.timestamp() * 1000)


def _as_dt(ms: int) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(ms / 1000)


def _number(token: str) -> Optional[float]:
    token = token.strip().lower()
    if token in _WORD_NUM:
        return _WORD_NUM[token]
    try:
        return float(token)
    except ValueError:
        return None


def _parse_clock(hh: str, mm: str = "", meridiem: str = "") -> Optional[Tuple[int, int]]:
    try:
        hour = int(hh)
        minute = int(mm or 0)
    except (TypeError, ValueError):
        return None
    mer = (meridiem or "").lower().replace(".", "")
    if mer in ("pm", "p.m") and hour < 12:
        hour += 12
    if mer in ("am", "a.m") and hour == 12:
        hour = 0
    if not mer and 0 < hour < 7:
        # "at 4" in an evening conversation is 16:00 far more often than 04:00.
        # The rule is written down rather than hidden: before 07:00 means pm,
        # and a reminder that lands 12 hours early would be worse than useless.
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def parse_when(text: str, now: Optional[_dt.datetime] = None) -> When:
    """Turn "in 10 minutes", "tomorrow at 9", ... into a time.

    Raises ``ReminderError`` when the text holds no time at all, because a
    reminder with a guessed time is the one failure mode that cannot be
    noticed until it matters.
    """
    low = " ".join(str(text or "").lower().split())
    base = now or _dt.datetime.now()
    matched: List[str] = []
    repeat = ""
    weekday: Optional[int] = None

    # ---- every day / every monday ------------------------------------- #
    daily_m = re.search(r"\b(every ?day|daily|each day)\b|(தினமும்|தினந்தோறும்|ஒவ்வொரு நாளும்)", low)
    if daily_m:
        repeat = "daily"
        matched.append(daily_m.group(0))
    for word, idx in _WEEKDAYS.items():
        week_m = re.search(rf"\bevery\s+{word}\b|ஒவ்வொரு\s*{word}", low)
        if week_m:
            repeat, weekday = "weekly", idx
            matched.append(week_m.group(0))
            break

    # ---- in N minutes / N minutes later ------------------------------- #
    m = re.search(r"\bin\s+(half an?\s+|[a-z஀-௿]+\s+)?(\d+(?:\.\d+)?)?\s*"
                  r"(minutes?|mins?|hours?|hrs?|seconds?|secs?|days?)\b", low)
    if not m:
        m = re.search(r"(?<![\w.])(half an?\s+|a\s+|an\s+|one\s+)?"
                      r"(\d+(?:\.\d+)?)?\s*(minutes?|mins?|hours?|hrs?|seconds?|secs?)\s+"
                      r"(later|from now)\b", low)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(நிமிட\w*|நிமிஷ\w*|மணி\s*நேர\w*|மணிநேர\w*|நொடி\w*)", low)
    if m:
        prefix = (m.group(1) or "").strip() if m.lastindex and m.lastindex >= 3 else ""
        raw = m.group(2) if m.lastindex and m.lastindex >= 3 else m.group(1)
        unit = m.group(3) if m.lastindex and m.lastindex >= 3 else m.group(2)
        amount = _number(raw) if raw else _number(prefix.split()[0] if prefix else "")
        if amount is None:
            amount = 1.0
        mins = amount * _UNIT_MIN.get(str(unit).strip().lower(), 1)
        if mins < 0:
            raise ReminderError("that is in the past - say 'in 10 minutes'")
        if mins > MAX_MINUTES_AHEAD:
            raise ReminderError("that is too far away to be a reminder")
        matched.append(m.group(0))
        return When(at_ms=_ms(base + _dt.timedelta(minutes=mins)), repeat=repeat,
                    weekday=weekday, matched=" ".join(matched))

    # ---- a clock time (with optional day) ----------------------------- #
    clock = re.search(r"\b(?:at|by|@)?\s*(\d{1,2})[:.](\d{2})\s*(am|pm|a\.m\.?|p\.m\.?)?", low) \
        or re.search(r"\b(?:at|by|@)\s*(\d{1,2})\s*(am|pm|a\.m\.?|p\.m\.?)?\b", low) \
        or re.search(r"(\d{1,2})\s*(மணி|மணிக்கு)", low)
    if clock:
        g = clock.groups()
        if len(g) == 3:
            hh, mm, mer = g
        elif len(g) == 2:
            hh, mer = g
            mm = ""
        else:                                   # pragma: no cover - defensive
            hh, mm, mer = "", "", ""
        parts = _parse_clock(hh, mm, mer)
        if parts is None:
            raise ReminderError(f"'{clock.group(0).strip()}' is not a time I can read")
        hour, minute = parts
        day = base.date()
        # Which day? tomorrow / a date / the next occurrence of the hour.
        tomorrow = re.search(r"\b(tomorrow|நாளைக்கு|நாளை)\b", low)
        date_m = re.search(r"\b(?:on\s+)?(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\b", low)
        if tomorrow:
            day = day + _dt.timedelta(days=1)
            matched.append("tomorrow")
        elif date_m:
            d, mo = int(date_m.group(1)), int(date_m.group(2))
            yr = date_m.group(3)
            year = base.year if not yr else (int(yr) + (2000 if len(yr) == 2 else 0))
            try:
                day = _dt.date(year, mo, d)
            except ValueError as exc:
                raise ReminderError(f"'{date_m.group(0)}' is not a real date") from exc
            if not yr and day < base.date():
                day = _dt.date(year + 1, mo, d)   # next year's date, not last year's
            matched.append(date_m.group(0))
        if weekday is not None and repeat == "weekly":
            ahead = (weekday - day.weekday()) % 7
            day = day + _dt.timedelta(days=ahead)
        target = _dt.datetime.combine(day, _dt.time(hour, minute))
        if target <= base and not tomorrow and not date_m and repeat != "weekly":
            target += _dt.timedelta(days=1)       # a time that has passed means tomorrow
        matched.append(clock.group(0).strip())
        return When(at_ms=_ms(target), repeat=repeat, weekday=weekday,
                    matched=" ".join(x for x in matched if x))

    # ---- "tomorrow morning" with no time is refused, not guessed ------ #
    if re.search(r"\b(tomorrow|நாளைக்கு)\b", low):
        raise ReminderError("I have the day but not the time - say 'tomorrow at 9'")
    raise ReminderError("I could not find a time. Try 'in 10 minutes' or 'at 4:30'.")


def next_after(at_ms: int, repeat: str, weekday: Optional[int] = None,
               after_ms: Optional[int] = None) -> int:
    """The next firing time for a repeating reminder, strictly after `after_ms`."""
    after = after_ms if after_ms is not None else now_ms()
    if not repeat:
        return 0
    step = _dt.timedelta(days=7 if repeat == "weekly" else 1)
    nxt = _as_dt(at_ms)
    guard = 0
    while _ms(nxt) <= after and guard < 4000:
        nxt += step
        guard += 1
    return _ms(nxt)


# --------------------------------------------------------------------------- #
# text of the reminder
# --------------------------------------------------------------------------- #
#: Words that carry no meaning once the time has been taken out. Kept short and
#: explicit: a word removed here can never reappear in the reminder itself.
_FILLERS = {
    "remind", "reminder", "me", "to", "that", "in", "at", "on", "by", "from",
    "now", "later", "and", "please", "every", "each", "day",
    "நினைவூட்டு", "நினைவூட்டவும்", "எனக்கு", "நான்", "காலை", "மாலை", "இரவு",
    "நண்பகல்", "மணிக்கு", "நாளைக்கு", "நாளை", "தினமும்", "ஒவ்வொரு", "நாளும்",
    "நிமிடம்", "நிமிடத்தில்", "நிமிடங்களில்", "மணி", "மணிநேரம்",
}


def split_text(text: str, when: When) -> str:
    """The owner's own sentence, with the time words taken out.

    Token by token, never by regex on digits: "10 minutes" used to lose the
    "i" from "minutes" because a digit pattern matched inside the word. The
    owner's words are the reminder, so they are copied rather than rebuilt.
    """
    drop = {w.lower().strip(" ,.:;!?()[]")
            for w in re.split(r"\s+", when.matched or "") if w}
    kept: List[str] = []
    for word in re.findall(r"\S+", str(text or "")):
        core = word.strip(' ,.:;!?"\'()[]').lower()
        if not core or core in drop or core in _FILLERS:
            continue
        if re.fullmatch(r"[\d:./\-]+", core):
            continue
        kept.append(word.strip(" ,.:;"))
    body = " ".join(" ".join(kept).split())
    return body or str(text or "").strip()


def describe(rec: Any, now_ms: int) -> str:
    """One line for a person: number, when, the sentence, and any trouble."""
    meta = dict(getattr(rec, "meta", None) or {})
    at_ms = int(meta.get("at_ms") or 0)
    repeat = str(meta.get("repeat") or "")
    done = bool(meta.get("done"))
    cancelled = bool(meta.get("cancelled"))
    at = _as_dt(at_ms)
    # A wait measured in seconds gets seconds on the clock too: "remind me in 30
    # seconds" shown as the current minute reads as if it had already fired.
    stamp = at.strftime("%a %d %b, %H:%M:%S") if abs(at_ms - now_ms) < 120000 \
        else at.strftime("%a %d %b, %H:%M")
    tail = ""
    if repeat:
        tail = f"  (repeats {repeat})"
    if cancelled:
        status = "cancelled"
    elif done:
        status = "done"
    elif at_ms <= now_ms:
        late = int(round((now_ms - at_ms) / 60000.0))
        if late < 1:
            status = "MISSED just now"
        elif late < 1440:
            status = f"MISSED {late} min ago"
        else:
            status = "MISSED earlier"
    else:
        # Found in stage 8: a reminder set for 90 minutes away said "[in 0 min]"
        # because the leftover milliseconds were truncated, and a reminder three
        # seconds away was made funnier by "0 min" too. Sub-two-minute waits are
        # now counted in seconds, and the rest are rounded, never truncated.
        left_ms = at_ms - now_ms
        left_s = int(round(left_ms / 1000.0))
        if left_s < 120:
            status = f"in {max(1, left_s)} s"
        else:
            left = int(round(left_ms / 60000.0))
            if left < 60:
                status = f"in {left} min"
            elif left < 60 * 24:
                status = f"in {max(1, int(round(left / 60.0)))} h"
            else:
                status = f"in {max(1, int(round(left / 1440.0)))} days"
    return f"  {rec.id}  {stamp}{tail}  -  {rec.title}   [{status}]"


def due(records: List[Any], now_ms: int, grace_ms: int = 1000) -> List[Any]:
    """Records whose time has arrived and that are neither done nor cancelled."""
    out = []
    for rec in records:
        meta = dict(getattr(rec, "meta", None) or {})
        if meta.get("done") or meta.get("cancelled"):
            continue
        at_ms = int(meta.get("at_ms") or 0)
        if at_ms and at_ms + grace_ms <= now_ms:
            out.append(rec)
    return sorted(out, key=lambda r: int((r.meta or {}).get("at_ms") or 0))


# --------------------------------------------------------------------------- #
# store-backed reminder list
# --------------------------------------------------------------------------- #
class ReminderBook:
    """Reminders live in memory records; this is the reading of them.

    It adds no storage of its own. Everything it does goes through the encrypted
    ``MemoryStore``, so `brain forget`, the sync engine and the tier rules keep
    working without knowing that reminders exist.
    """

    def __init__(self, memory: Any):
        self.memory = memory

    # -- reading -------------------------------------------------------- #
    def all(self, include_closed: bool = False) -> List[Any]:
        rows = self.memory.list_records(kind=KIND, limit=500)
        if include_closed:
            return rows
        out = []
        for rec in rows:
            meta = dict(getattr(rec, "meta", None) or {})
            if meta.get("cancelled"):
                continue
            out.append(rec)
        return sorted(out, key=lambda r: int((r.meta or {}).get("at_ms") or 0))

    def open(self) -> List[Any]:
        return [r for r in self.all() if not (r.meta or {}).get("done")]

    def get(self, rid: str) -> Optional[Any]:
        for rec in self.all(include_closed=True):
            if rec.id == rid:
                return rec
        return None

    def by_number(self, number: Any) -> Optional[Any]:
        """`2` means the second item in `reminders`, not a magic id."""
        items = self.all()
        try:
            idx = int(number) - 1
        except (TypeError, ValueError):
            return None
        return items[idx] if 0 <= idx < len(items) else None

    # -- writing -------------------------------------------------------- #
    def add(self, text: str, when: When, original: str = "") -> Any:
        title = split_text(text, when) if text else (original or "reminder")
        meta: Dict[str, Any] = {
            "at_ms": when.at_ms, "repeat": when.repeat, "weekday": when.weekday,
            "done": False, "cancelled": False, "snoozed": 0,
            "created_iso": _as_dt(now_ms()).isoformat(timespec="seconds"),
        }
        if when.repeat:
            meta["next_ms"] = when.next_ms or next_after(when.at_ms, when.repeat,
                                                         when.weekday)
        return self.memory.remember(body=original or title, title=title, kind=KIND,
                                    tags=[TAG], meta=meta)

    def snooze(self, rec: Any, minutes: float) -> Any:
        meta = dict(rec.meta or {})
        new_at = now_ms() + int(minutes * 60000)
        meta["at_ms"] = new_at
        meta["snoozed"] = int(meta.get("snoozed") or 0) + 1
        meta["last_fired_ms"] = 0
        return self.memory.update(rec.id, meta=meta)

    def close(self, rec: Any, done: bool = True) -> Any:
        """Mark one firing finished; a repeater rolls forward instead of dying."""
        meta = dict(rec.meta or {})
        now = now_ms()
        meta["last_fired_ms"] = now
        if meta.get("repeat"):
            meta["at_ms"] = next_after(int(meta["at_ms"]), str(meta["repeat"]),
                                       meta.get("weekday"), after_ms=now)
            meta["done"] = False
        else:
            meta["done"] = bool(done)
        return self.memory.update(rec.id, meta=meta)

    def cancel(self, rec: Any) -> Any:
        meta = dict(rec.meta or {})
        meta["cancelled"] = True
        return self.memory.update(rec.id, meta=meta)

    def due_now(self) -> List[Any]:
        return due(self.all(), now_ms())

    def render(self, include_closed: bool = False) -> str:
        rows = self.all(include_closed=include_closed)
        if not rows:
            return ("No reminders yet. Try: `remind me in 10 minutes to drink water` "
                    "or `remind me tomorrow at 9 to take the tablet`.")
        now = now_ms()
        lines = ["Reminders:", *[describe(r, now) for r in rows]]
        return "\n".join(lines)
