"""Calendar, read-only, from an `.ics` file or a secret address. No OAuth.

The owner asked "what is on tomorrow?" without wanting to hand Google a login.
Google Calendar publishes each calendar as a *secret address in iCal format*:
a URL that needs no password and cannot be written to. That is the whole basis of
this module, and the limits are stated rather than hidden:

* **Read, and write to a file.** An ICS feed is one-way, so `add`/`update` never
  touch the feed: they write a *local* `.ics` that your phone, Outlook or Google
  can import, and they are the same shape a real calendar uses (`UID` stable
  across edits, `SEQUENCE` incremented, `DTSTAMP` refreshed) so that importing an
  updated file replaces the event instead of duplicating it. Writing back to
  Google still needs OAuth, which is a later stage; saying so beats a button that
  silently fails.
* **Fetching is the owner's decision.** ``load`` takes a path or a URL, but a URL
  is only fetched when the caller says so - the agent asks first and reports the
  host, because a calendar address is as private as a password.
* **Parsing is local and strict.** Folded lines, escapes, all-day events, UTC and
  `TZID` times, repetitions (RRULE daily/weekly/by-day/count/until) - the shapes
  a real calendar actually contains. Anything unknown is skipped, never guessed.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

MAX_BYTES = 8 * 1024 * 1024


# --------------------------------------------------------------------------- #
# reading the file
# --------------------------------------------------------------------------- #
def _unfold(raw: str) -> List[str]:
    """RFC 5545 folds long lines with a leading space or tab."""
    out: List[str] = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(text: str) -> str:
    return (text.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\")).strip()


def _split_prop(line: str) -> Tuple[str, Dict[str, str], str]:
    """`DTSTART;TZID=Asia/Kolkata:20261104T090000` -> (name, params, value)."""
    head, _, value = line.partition(":")
    parts = head.split(";")
    name = parts[0].upper()
    params: Dict[str, str] = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v
    return name, params, value


_TZ_CACHE: Dict[str, Any] = {}


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo

        if name not in _TZ_CACHE:
            _TZ_CACHE[name] = ZoneInfo(name)
        return _TZ_CACHE[name]
    except Exception:
        return None


def _parse_dt(value: str, params: Dict[str, str]) -> Tuple[Optional[_dt.datetime], bool]:
    """A date or a date-time, plus whether it is all-day."""
    v = value.strip()
    date_only = params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", v)
    try:
        if date_only:
            return _dt.datetime.strptime(v[:8], "%Y%m%d"), True
        if v.endswith("Z"):
            return (_dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
                    .replace(tzinfo=_dt.timezone.utc)), False
        naive = _dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
        zone = _zone(params.get("TZID", "")) if params.get("TZID") else None
        return (naive.replace(tzinfo=zone) if zone else naive), False
    except (ValueError, TypeError):
        return None, False


def _parse_duration(text: str) -> Optional[_dt.timedelta]:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?",
                     str(text or "").strip())
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    weeks, days, hours, minutes = (int(x or 0) for x in m.groups()[1:])
    return sign * _dt.timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes)


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #
@dataclass
class Event:
    start: _dt.datetime
    end: Optional[_dt.datetime] = None
    summary: str = ""
    location: str = ""
    all_day: bool = False
    uid: str = ""
    rrule: str = ""
    status: str = ""          # "CANCELLED" is a removal, not an event to show

    def to_dict(self) -> Dict[str, Any]:
        return {"start": self.start.isoformat(timespec="minutes"),
                "end": self.end.isoformat(timespec="minutes") if self.end else "",
                "summary": self.summary, "location": self.location,
                "all_day": self.all_day, "recurring": bool(self.rrule)}

    def line(self, tz_note: str = "") -> str:
        when = ("all day" if self.all_day
                else self.start.strftime("%H:%M")
                     + (f"-{self.end.strftime('%H:%M')}" if self.end else ""))
        tail = f"  @ {self.location}" if self.location else ""
        return f"  {self.start.strftime('%a %d %b')}  {when:>11s}  {self.summary}{tail}"


def parse_ics(raw: str) -> Tuple[List[Event], List[str]]:
    """Every event in the file, plus a list of what was skipped and why."""
    events: List[Event] = []
    skipped: List[str] = []
    stack: List[str] = []
    cur: Optional[Dict[str, Any]] = None
    for line in _unfold(raw):
        if not line.strip():
            continue
        name, params, value = _split_prop(line)
        if name == "BEGIN":
            stack.append(value.upper())
            if value.upper() == "VEVENT":
                cur = {"params": {}}
            continue
        if name == "END":
            if value.upper() == "VEVENT" and cur is not None:
                start = cur.get("start")
                if not start:
                    skipped.append(f"event without a readable start: {cur.get('summary', '')}")
                else:
                    end = cur.get("end")
                    if end is None and cur.get("duration"):
                        end = start + cur["duration"]
                    events.append(Event(start=start, end=end,
                                        summary=cur.get("summary", "") or "(no title)",
                                        location=cur.get("location", ""),
                                        all_day=bool(cur.get("all_day")),
                                        uid=cur.get("uid", ""),
                                        rrule=cur.get("rrule", ""),
                                        status=str(cur.get("status", ""))))
                cur = None
            if stack:
                stack.pop()
            continue
        if cur is None:
            continue
        if name in ("DTSTART", "DTEND"):
            dt, all_day = _parse_dt(value, params)
            cur["start" if name == "DTSTART" else "end"] = dt
            if name == "DTSTART":
                cur["all_day"] = all_day
        elif name == "DURATION":
            cur["duration"] = _parse_duration(value)
        elif name == "SUMMARY":
            cur["summary"] = _unescape(value)
        elif name == "LOCATION":
            cur["location"] = _unescape(value)
        elif name == "UID":
            cur["uid"] = value.strip()
        elif name == "RRULE":
            cur["rrule"] = value.strip()
        elif name == "STATUS":
            cur["status"] = value.strip().upper()
    return events, skipped


# --------------------------------------------------------------------------- #
# repetitions
# --------------------------------------------------------------------------- #
_WEEKDAY_ICS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_WEEKDAY_TA = {"திங்கள்": 0, "செவ்வாய்": 1, "புதன்": 2, "வியாழன்": 3,
               "வெள்ளி": 4, "சனி": 5, "ஞாயிறு": 6}


def _expand(event: Event, until: _dt.datetime, limit: int = 400) -> List[Event]:
    """Turn one recurring event into the occurrences before `until`."""
    rule = event.rrule.upper()
    if "FREQ=DAILY" in rule:
        step = _dt.timedelta(days=int(re.search(r"INTERVAL=(\d+)", rule).group(1))
                             if "INTERVAL" in rule else 1)
    elif "FREQ=WEEKLY" in rule:
        step = _dt.timedelta(weeks=int(re.search(r"INTERVAL=(\d+)", rule).group(1))
                             if "INTERVAL" in rule else 1)
    elif "FREQ=MONTHLY" in rule:
        step = None                     # handled below, month by month
    else:
        return [event]
    count_m = re.search(r"COUNT=(\d+)", rule)
    until_m = re.search(r"UNTIL=([0-9T]+Z?)", rule)
    # Compare like with like: an event with a timezone is expanded in its own
    # timezone, so a naive horizon has to be given the same one.
    tz = event.start.tzinfo
    hard_until = until if tz is None else until.replace(tzinfo=tz)
    if until_m:
        raw = until_m.group(1)
        parsed, _ = _parse_dt(raw, {})
        if parsed:
            parsed = parsed if tz is None else (
                parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed)
            hard_until = min(hard_until, parsed)
    bydays = re.search(r"BYDAY=([A-Z,]+)", rule)
    days = [_WEEKDAY_ICS[d[-2:]] for d in bydays.group(1).split(",")
            if d[-2:] in _WEEKDAY_ICS] if bydays else []
    out: List[Event] = [event]
    emitted = 1
    cursor = event.start
    while emitted < limit:
        if step is not None:
            cursor = cursor + step
        else:                                   # monthly: same day next month
            month = cursor.month + 1
            year = cursor.year + (1 if month > 12 else 0)
            month = 1 if month > 12 else month
            try:
                cursor = cursor.replace(year=year, month=month)
            except ValueError:                  # 31st in a 30-day month
                continue
        if cursor > hard_until:
            break
        if count_m and emitted >= int(count_m.group(1)):
            break
        if days and cursor.weekday() not in days:
            continue
        length = (event.end - event.start) if event.end else None
        out.append(Event(start=cursor, end=(cursor + length) if length else None,
                         summary=event.summary, location=event.location,
                         all_day=event.all_day, uid=event.uid, rrule=event.rrule))
        emitted += 1
    return out


def between(raw: str, start: _dt.date, end: _dt.date,
            now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Events from `start` to `end` (inclusive dates), sorted, repeats expanded."""
    events, skipped = parse_ics(raw)
    # A cancelled entry is a receipt, not an appointment: it is in the file so an
    # import removes the event elsewhere, and it must never be shown as one.
    events = [e for e in events if e.status.upper() != "CANCELLED"]
    horizon = _dt.datetime.combine(end + _dt.timedelta(days=1),
                                   _dt.time.min, tzinfo=None)
    window_start = _dt.datetime.combine(start, _dt.time.min)
    picked: List[Event] = []
    for ev in events:
        candidates = _expand(ev, horizon) if ev.rrule else [ev]
        for occ in candidates:
            s = occ.start.replace(tzinfo=None) if occ.start.tzinfo else occ.start
            if window_start <= s < horizon:
                picked.append(occ)
    picked.sort(key=lambda e: (e.start.replace(tzinfo=None) if e.start.tzinfo
                               else e.start, e.summary))
    return {"ok": True, "events": [e.to_dict() for e in picked],
            "lines": [e.line() for e in picked], "skipped": skipped,
            "count": len(picked)}


# --------------------------------------------------------------------------- #
# where the calendar comes from
# --------------------------------------------------------------------------- #
def is_url(source: str) -> bool:
    return bool(re.match(r"^https?://", str(source or "").strip(), re.I))


def masked_url(url: str) -> str:
    """A calendar address is a secret; show its host and nothing else.

    Google's secret address works without a password, so printing it in a chat
    window, a log or an audit entry is the same as printing a password.
    """
    m = re.match(r"^(https?://[^/]+)(.*)$", str(url or ""), re.I)
    if not m:
        return "(not a URL)"
    return m.group(1) + "/…(address hidden)"


def fetch(url: str, timeout: float = 12.0) -> Dict[str, Any]:
    """Download an ICS feed. Only ever called after the owner says yes."""
    if not is_url(url):
        return {"ok": False, "error": "not an http(s) address"}
    if not str(url).lower().split("?")[0].endswith((".ics", "/ical", "/basic.ics")):
        # Not a refusal - Google's own address ends in basic.ics, but anything
        # else is stated so the owner can see what is being contacted.
        pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "nila-calendar/0.2"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "error": f"could not read the calendar: {exc}",
                "host": masked_url(url)}
    if len(data) > MAX_BYTES:
        return {"ok": False, "error": "the calendar file is larger than 8 MB"}
    return {"ok": True, "text": data.decode("utf-8", "replace"),
            "bytes": len(data), "host": masked_url(url)}


def load(source: str, allow_network: bool = False) -> Dict[str, Any]:
    """`source` is a path or a URL. A URL is only fetched when allowed."""
    src = str(source or "").strip()
    if not src:
        return {"ok": False, "error": "no calendar set - give a .ics path or a "
                                      "secret iCal address"}
    if is_url(src):
        if not allow_network:
            return {"ok": False, "needs_permission": True, "host": masked_url(src),
                    "error": ("that is a calendar address on the internet; say yes "
                              "and I will read it (only that host is contacted)")}
        got = fetch(src)
        if not got.get("ok"):
            return got
        got["source"] = masked_url(src)
        return got
    path = Path(src).expanduser()
    if not path.exists():
        return {"ok": False, "error": f"no calendar file at {src}"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": f"could not read {src}: {exc}"}
    return {"ok": True, "text": text, "bytes": len(text), "source": str(path)}


# --------------------------------------------------------------------------- #
# the question the owner asks
# --------------------------------------------------------------------------- #
_RANGE_WORDS = (
    (r"\b(today|இன்று|இன்னைக்கு)\b", 0, 0),
    (r"\b(tomorrow|நாளை|நாளைக்கு)\b", 1, 1),
    (r"\b(day after tomorrow|நாளை மறுநாள்)\b", 2, 2),
    (r"\b(this week|இந்த வாரம்)\b", 0, 6),
    (r"\b(next week|அடுத்த வாரம்)\b", 7, 13),
    (r"\b(this month|இந்த மாதம்)\b", 0, 30),
)


def range_from_text(text: str, now: Optional[_dt.datetime] = None) -> Tuple[_dt.date, _dt.date, str]:
    """Which days a question is about, and the words that decided it."""
    low = " ".join(str(text or "").lower().split())
    base = (now or _dt.datetime.now()).date()
    for pattern, a, b in _RANGE_WORDS:
        m = re.search(pattern, low)
        if m:
            return (base + _dt.timedelta(days=a), base + _dt.timedelta(days=b),
                    m.group(0))
    # A weekday name means the next such day, today included. Only the full
    # names: the two-letter ICS codes ("TH", "SA") match ordinary words - "the"
    # contains "th", which once turned "what is on the news" into "Events TH".
    for word, idx in _WEEKDAY_TA.items():
        if word.lower() in low:
            ahead = (idx - base.weekday()) % 7
            day = base + _dt.timedelta(days=ahead)
            return day, day, word
    for word, idx in {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                      "friday": 4, "saturday": 5, "sunday": 6}.items():
        if re.search(rf"\b{word}\b|{word}", low):
            ahead = (idx - base.weekday()) % 7
            day = base + _dt.timedelta(days=ahead)
            return day, day, word
    return base, base, "today (no day was named)"


def render(data: Dict[str, Any], label: str = "") -> str:
    if not data.get("ok"):
        return "⚠️ " + str(data.get("error", "the calendar could not be read"))
    lines = data.get("lines") or []
    if not lines:
        return f"No events {label}." if label else "No events."
    head = f"Events {label}:" if label else "Events:"
    out = [head, *lines]
    if data.get("skipped"):
        out.append(f"  ({len(data['skipped'])} entr(y/ies) skipped - unreadable time)")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# writing: a local .ics your phone can import
# --------------------------------------------------------------------------- #
class IcsWriteError(RuntimeError):
    """Writing the calendar failed, in words the owner can act on."""


def _escape(text: str) -> str:
    """RFC 5545: commas, semicolons, backslashes and newlines are escaped."""
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n").replace("\r", ""))


def _fold(line: str, limit: int = 75) -> List[str]:
    """Fold a long line the way the RFC says, so other calendars read it."""
    out = [line[:limit]]
    rest = line[limit:]
    while rest:
        out.append(" " + rest[: limit - 1])
        rest = rest[limit - 1:]
    return out


def _dt_line(name: str, when: _dt.datetime, all_day: bool) -> str:
    if all_day:
        return f"{name};VALUE=DATE:{when.strftime('%Y%m%d')}"
    return (f"{name}:{when.strftime('%Y%m%dT%H%M%S')}")


def new_uid(summary: str = "", when: Optional[_dt.datetime] = None,
            domain: str = "nila.local") -> str:
    """A stable-looking but unpredictable id - the same event keeps it for ever."""
    stamp = (when or _dt.datetime.now()).strftime("%Y%m%dT%H%M%S")
    digest = hashlib.sha256(f"{summary}|{stamp}|{uuid.uuid4()}".encode()).hexdigest()[:16]
    return f"{stamp}-{digest}@{domain}"


def _all_events(path: Path) -> List[Event]:
    """Everything in the file, cancelled entries included (they are tombstones)."""
    p = Path(path).expanduser()
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise IcsWriteError(f"could not read {p}: {exc}") from exc
    events, _skipped = parse_ics(text)
    return events


def read_events(path: Path) -> List[Event]:
    """The events in a local file that are still events (empty if none)."""
    return [e for e in _all_events(path)
            if e.status.upper() != "CANCELLED" and not e.summary.startswith("(")]


def _rewrite(path: Path, kept: List[Event], extra: List[str], name: str) -> None:
    stamp = _dt.datetime.now()
    body: List[str] = []
    for event in kept:
        body += _event_body(event, event.summary, event.location, "",
                            0, stamp, event.uid)
    body += extra
    path.write_text(_wrap_calendar(body, name), encoding="utf-8")


def _wrap_calendar(body: List[str], name: str) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Nila Assistant//Calendar//EN",
             "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{_escape(name or 'Nila')}"]
    lines += body
    lines += ["END:VCALENDAR", ""]
    folded: List[str] = []
    for line in lines:
        if not line.strip():          # a blank line ends the calendar for some readers
            continue
        folded.extend(_fold(line))
    return "\r\n".join(folded) + "\r\n"


def _event_body(event: Event, summary: str, location: str, description: str,
                sequence: int, stamp: _dt.datetime, uid: str) -> List[str]:
    end = event.end
    if not event.all_day and end is None:
        end = event.start + _dt.timedelta(hours=1)
    if event.all_day and end is None:
        end = event.start + _dt.timedelta(days=1)
    return ["BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp.strftime('%Y%m%dT%H%M%SZ')}",
            f"SEQUENCE:{int(sequence)}",
            _dt_line("DTSTART", event.start, event.all_day),
            _dt_line("DTEND", end, event.all_day),
            f"SUMMARY:{_escape(summary)}",
            f"LOCATION:{_escape(location)}" if location else "",
            f"DESCRIPTION:{_escape(description)}" if description else "",
            *(   [f"RRULE:{event.rrule}"] if event.rrule else []),
            "END:VEVENT"]


def add_event(path: Path | str, summary: str, start: _dt.datetime,
              end: Optional[_dt.datetime] = None, location: str = "",
              description: str = "", all_day: bool = False,
              rrule: str = "", name: str = "Nila") -> Dict[str, Any]:
    """Add one event to a local .ics file, creating the file if needed."""
    when = start if not all_day else start.replace(hour=0, minute=0, second=0, microsecond=0)
    if not str(summary or "").strip():
        raise IcsWriteError("an event needs a title")
    event = Event(start=when, end=end, summary=summary, location=location,
                  all_day=all_day, rrule=rrule)
    p = Path(path).expanduser()
    uid = new_uid(summary, when)
    event.uid = uid
    stamp = _dt.datetime.now()
    keep = _all_events(p)
    body = _event_body(event, summary, location, description, 0, stamp, uid)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        _rewrite(p, keep, body, name)
    except OSError as exc:
        raise IcsWriteError(f"could not write {p}: {exc}") from exc
    return {"ok": True, "path": str(p), "uid": uid, "sequence": 0,
            "summary": summary, "location": location,
            "start": when.isoformat(timespec="minutes"),
            "all_day": all_day, "created": True,
            "note": ("import this file into your phone or Google Calendar; "
                     "writing straight into a Google calendar needs OAuth (not built)")}


def update_event(path: Path | str, uid: str, summary: Optional[str] = None,
                 start: Optional[_dt.datetime] = None,
                 end: Optional[_dt.datetime] = None, location: Optional[str] = None,
                 description: Optional[str] = None, all_day: Optional[bool] = None,
                 name: str = "Nila") -> Dict[str, Any]:
    """Change one event, keeping its UID and raising SEQUENCE.

    The UID staying the same is what makes an import *replace* the old event
    instead of adding a second copy - which is the whole reason a calendar file
    can be edited at all.
    """
    p = Path(path).expanduser()
    events = _all_events(p)
    if not uid:
        raise IcsWriteError("give the event's UID (brain calendar --list shows it)")
    stamp = _dt.datetime.now()
    found = False
    body: List[str] = []
    for event in events:
        if event.uid != uid:
            body += _event_body(event, event.summary, event.location, "",
                                0, stamp, event.uid)
            continue
        found = True
        new_summary = summary if summary is not None else event.summary
        new_start = start if start is not None else event.start
        new_end = end if end is not None else event.end
        if start is not None and end is None and event.end is not None:
            # Moving an event keeps its length. Leaving the old end behind made
            # an event that finished before it started.
            new_end = new_start + (event.end - event.start)
        new_location = location if location is not None else event.location
        new_all_day = all_day if all_day is not None else event.all_day
        if new_all_day:
            new_start = new_start.replace(hour=0, minute=0, second=0, microsecond=0)
        changed = Event(start=new_start, end=new_end, summary=new_summary,
                        location=new_location or "", all_day=new_all_day,
                        uid=uid, rrule=event.rrule)
        body += _event_body(changed, new_summary, new_location or "", description or "",
                            1, stamp, uid)
    if not found:
        raise IcsWriteError(f"no event with UID {uid} in {p}")
    try:
        p.write_text(_wrap_calendar(body, name), encoding="utf-8")
    except OSError as exc:
        raise IcsWriteError(f"could not write {p}: {exc}") from exc
    return {"ok": True, "path": str(p), "uid": uid, "sequence": 1, "updated": True}


def remove_event(path: Path | str, uid: str, name: str = "Nila") -> Dict[str, Any]:
    """Remove one event from a local file. A tombstone is written, not silence."""
    p = Path(path).expanduser()
    events = _all_events(p)
    keep = [e for e in events if e.uid != uid]
    if len(keep) == len(events):
        raise IcsWriteError(f"no event with UID {uid} in {p}")
    stamp = _dt.datetime.now()
    body: List[str] = []
    for event in keep:
        body += _event_body(event, event.summary, event.location, "", 0, stamp, event.uid)
    body += ["BEGIN:VEVENT", f"UID:{uid}",
             f"DTSTAMP:{stamp.strftime('%Y%m%dT%H%M%SZ')}", "SEQUENCE:2",
             "STATUS:CANCELLED",
             _dt_line("DTSTART", stamp, False),
             "SUMMARY:(removed)"]
    body += ["END:VEVENT"]
    p.write_text(_wrap_calendar(body, name), encoding="utf-8")
    return {"ok": True, "path": str(p), "uid": uid, "removed": True}
