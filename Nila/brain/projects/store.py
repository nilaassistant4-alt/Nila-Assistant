"""Project records on top of the encrypted memory store."""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util.misc import now_ms

KIND = "project"
STATUSES = ("idea", "validating", "planning", "building", "launched", "paused", "done")
UNFINISHED = ("idea", "validating", "planning", "building", "launched")
MAX_NOTES = 500
MAX_NOTE_CHARS = 4000
MAX_MILESTONES = 100
MAX_TITLE = 120

#: the nine blocks of a lean business canvas, in the order they are usually filled
CANVAS: List[Dict[str, str]] = [
    {"key": "problem", "label": "Problem",
     "ask": "What problem does this solve, and who feels it most?"},
    {"key": "customers", "label": "Customers",
     "ask": "Who exactly is the first customer? Be specific (job, place, size)."},
    {"key": "value_prop", "label": "Value proposition",
     "ask": "In one sentence, why would they choose this over what they do today?"},
    {"key": "solution", "label": "Solution",
     "ask": "What is the smallest version that solves the problem?"},
    {"key": "channels", "label": "Channels",
     "ask": "How will the first 10 customers find out about it?"},
    {"key": "revenue", "label": "Revenue",
     "ask": "How does money come in, and at what price?"},
    {"key": "costs", "label": "Costs",
     "ask": "What are the main costs to start and to run for a month?"},
    {"key": "metrics", "label": "Key metric",
     "ask": "Which single number tells you it is working?"},
    {"key": "advantage", "label": "Advantage",
     "ask": "What is hard for others to copy?"},
]
_CANVAS_KEYS = [c["key"] for c in CANVAS]


def _local_date() -> "date":
    """Today, on this PC's calendar. The owner's day, not UTC's."""
    from datetime import date as _date
    return _date.today()


def _parse_due(value: Any):
    """`2026-12-01` -> a date; anything else -> None (never a guess)."""
    from datetime import date as _date

    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _date.fromisoformat(text[:10])
    except ValueError:
        return None


def _is_late(due: Any) -> bool:
    parsed = _parse_due(due)
    return bool(parsed is not None and (_local_date() - parsed).days > 0)


def _clean_due(due: str) -> str:
    """Accept the shapes an owner actually types, store ISO, refuse nonsense."""
    from datetime import date as _date, datetime as _dt

    text = str(due or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            return _dt.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    if text.lower() in ("today", "tomorrow"):
        today = _local_date()
        return (today if text.lower() == "today" else
                _date.fromordinal(today.toordinal() + 1)).isoformat()
    raise ProjectError("the due date is not a date - write it as 2026-12-01")

_STAGE_STEP = {
    "idea": "Talk to 3 people who might have this problem and write down their exact words.",
    "validating": "Run one cheap test (a simple page, a pre-order or a demo) and record the result.",
    "planning": "Define the smallest first version and a 4-week plan with weekly goals.",
    "building": "Get the smallest working version in front of 5 real users this week.",
    "launched": "Pick one number to track weekly and review it every Sunday.",
    "paused": "Decide: what single thing would unblock this, or should it be closed?",
    "done": "Write down what worked and what you would do differently next time.",
}


class ProjectError(ValueError):
    pass


def _ago(ms: int) -> str:
    days = max(0, int((now_ms() - ms) / 86_400_000))
    return "today" if days == 0 else ("1 day ago" if days == 1 else f"{days} days ago")


#: how many project records we will scan for a lookup. Not a storage limit:
#: reaching it is reported, never silently applied (audit B9 - `list_records`
#: defaulted to 200 records, so the 201st project vanished from `list()` and could
#: not be found by name at all).
SCAN_LIMIT = 2000


class ProjectStore:
    def __init__(self, memory: Any, files: Any = None):
        self.memory = memory
        self.files = files
        #: set by _records() when the scan hit SCAN_LIMIT; callers surface it
        self.truncated = False

    # ------------------------------------------------------------------ #
    def _records(self) -> List[Any]:
        rows = self.memory.list_records(kind=KIND, limit=SCAN_LIMIT + 1)
        self.truncated = len(rows) > SCAN_LIMIT
        return rows[:SCAN_LIMIT]

    @staticmethod
    def _data(rec: Any) -> Dict[str, Any]:
        return rec.meta.get("project") or {}

    def _resolve(self, ref: str) -> Any:
        ref = (ref or "").strip()
        if not ref:
            raise ProjectError("which project? give its name or id")
        recs = self._records()
        for r in recs:
            if r.id == ref:
                return r
        low = ref.lower()
        exact = [r for r in recs if r.title.lower() == low]
        found = exact or [r for r in recs if low in r.title.lower()]
        if len(found) == 1:
            return found[0]
        if not found:
            raise ProjectError(f"no project matches '{ref}'")
        names = ", ".join(f"'{r.title}'" for r in found[:5])
        raise ProjectError(f"'{ref}' matches several projects: {names}. Be more specific.")

    @staticmethod
    def _render(title: str, p: Dict[str, Any]) -> str:
        lines = [p.get("idea", "").strip()]
        for c in CANVAS:
            v = (p.get("canvas") or {}).get(c["key"])
            if v:
                lines.append(f"{c['label']}: {v}")
        for m in p.get("milestones", []):
            lines.append(("[x] " if m.get("done") else "[ ] ") + m.get("text", ""))
        for n in p.get("notes", []):
            lines.append(n.get("text", ""))
        return "\n".join(x for x in lines if x)[:200_000]

    def _save(self, rec: Any, p: Dict[str, Any]) -> Any:
        p["updated"] = now_ms()
        return self.memory.update(rec.id, body=self._render(rec.title, p), meta={"project": p})

    # ------------------------------------------------------------------ #
    def create(self, title: str, idea: str = "") -> Dict[str, Any]:
        title = " ".join((title or "").split())[:MAX_TITLE]
        if not title:
            raise ProjectError("a project needs a title")
        if any(r.title.lower() == title.lower() for r in self._records()):
            raise ProjectError(f"a project called '{title}' already exists")
        p = {"status": "idea", "idea": (idea or "").strip()[:MAX_NOTE_CHARS], "canvas": {},
             "notes": [], "milestones": [], "files": [], "created": now_ms(),
             "updated": now_ms()}
        rec = self.memory.remember(self._render(title, p) or title, title=title, kind=KIND,
                                   tags=["project"], meta={"project": p})
        return self._summary(rec)

    def add_note(self, ref: str, text: str) -> Dict[str, Any]:
        rec = self._resolve(ref)
        text = (text or "").strip()
        if not text:
            raise ProjectError("the note is empty")
        p = self._data(rec)
        notes = p.setdefault("notes", [])
        if len(notes) >= MAX_NOTES:
            raise ProjectError(f"this project already has {MAX_NOTES} notes")
        notes.append({"ts": now_ms(), "text": text[:MAX_NOTE_CHARS]})
        return self._summary(self._save(rec, p))

    def set_status(self, ref: str, status: str) -> Dict[str, Any]:
        status = (status or "").strip().lower()
        if status not in STATUSES:
            raise ProjectError(f"status must be one of: {', '.join(STATUSES)}")
        rec = self._resolve(ref)
        p = self._data(rec)
        p["status"] = status
        return self._summary(self._save(rec, p))

    def set_canvas(self, ref: str, block: str, text: str) -> Dict[str, Any]:
        block = (block or "").strip().lower().replace(" ", "_").replace("-", "_")
        if block not in _CANVAS_KEYS:
            raise ProjectError(f"block must be one of: {', '.join(_CANVAS_KEYS)}")
        rec = self._resolve(ref)
        p = self._data(rec)
        p.setdefault("canvas", {})[block] = (text or "").strip()[:MAX_NOTE_CHARS]
        if not p["canvas"][block]:
            p["canvas"].pop(block)
        return self._summary(self._save(rec, p))

    def add_milestone(self, ref: str, text: str, due: str = "") -> Dict[str, Any]:
        """A milestone, optionally with the day it is due (YYYY-MM-DD).

        A date is kept as text and validated on the way in, because the owner
        types `2026-12-01`; interpreting that string as local midnight later is
        the only place it becomes a moment. A milestone without a date is still
        a milestone - it simply never shows as late.
        """
        rec = self._resolve(ref)
        p = self._data(rec)
        ms = p.setdefault("milestones", [])
        if len(ms) >= MAX_MILESTONES:
            raise ProjectError("too many milestones")
        if not (text or "").strip():
            raise ProjectError("the milestone is empty")
        entry = {"text": text.strip()[:300], "done": False, "ts": now_ms()}
        if (due or "").strip():
            entry["due"] = _clean_due(due)
        ms.append(entry)
        return self._summary(self._save(rec, p))

    def complete_milestone(self, ref: str, index: int) -> Dict[str, Any]:
        rec = self._resolve(ref)
        p = self._data(rec)
        ms = p.get("milestones", [])
        if not (1 <= index <= len(ms)):
            raise ProjectError(f"milestone number must be 1 to {len(ms)}")
        ms[index - 1]["done"] = True
        return self._summary(self._save(rec, p))

    def attach_file(self, ref: str, filename: str, data: bytes) -> Dict[str, Any]:
        if self.files is None:
            raise ProjectError("file storage is not available in this session")
        rec = self._resolve(ref)
        p = self._data(rec)
        meta = self.files.put(filename, data)
        p.setdefault("files", []).append(meta)
        self._save(rec, p)
        return meta

    def read_file(self, ref: str, fid: str) -> Dict[str, Any]:
        rec = self._resolve(ref)
        meta = next((f for f in self._data(rec).get("files", []) if f["id"] == fid), None)
        if meta is None or self.files is None:
            raise ProjectError("no such file in this project")
        return {"meta": meta, "data": self.files.get(fid)}

    # ------------------------------------------------------------------ #
    def late_milestones(self) -> List[Dict[str, Any]]:
        """Every unfinished milestone whose day has passed, across all projects.

        This is what the dashboard puts on top and what a reminder can be made
        from: the owner's rule was that late items must be *foregrounded*, not
        found by scrolling. Nothing here writes - it only looks.
        """
        today = _local_date()
        out: List[Dict[str, Any]] = []
        for s in self.list():
            for i, m in enumerate(self.get(s["id"]).get("milestones") or []):
                if m.get("done") or not m.get("due"):
                    continue
                due = _parse_due(m["due"])
                if due is None:
                    continue
                days = (today - due).days
                if days > 0:
                    out.append({"project": s["title"], "id": s["id"], "index": i + 1,
                                "text": m["text"], "due": m["due"], "days_late": days})
        return sorted(out, key=lambda r: -r["days_late"])

    def _summary(self, rec: Any) -> Dict[str, Any]:
        p = self._data(rec)
        canvas = p.get("canvas") or {}
        ms = p.get("milestones") or []
        return {"id": rec.id, "title": rec.title, "status": p.get("status", "idea"),
                "updated": p.get("updated", rec.updated_at), "updated_ago": _ago(p.get("updated", rec.updated_at)),
                "canvas_filled": len(canvas), "canvas_total": len(CANVAS),
                "milestones_done": sum(1 for m in ms if m.get("done")),
                "milestones_total": len(ms),
                "milestones_late": sum(1 for m in ms if not m.get("done")
                                       and _is_late(m.get("due"))),
                "notes": len(p.get("notes", [])),
                "files": len(p.get("files", []))}

    def list(self) -> List[Dict[str, Any]]:
        out = [self._summary(r) for r in self._records()]
        return sorted(out, key=lambda s: s["updated"], reverse=True)

    def listing(self) -> Dict[str, Any]:
        """`list()` plus the truth about whether it is complete."""
        items = self.list()
        return {"projects": items, "count": len(items),
                "truncated": self.truncated, "scan_limit": SCAN_LIMIT,
                "note": (f"more than {SCAN_LIMIT} projects exist; only the {SCAN_LIMIT} "
                         f"most recently updated were scanned"
                         if self.truncated else "")}

    def get(self, ref: str) -> Dict[str, Any]:
        rec = self._resolve(ref)
        p = self._data(rec)
        return {**self._summary(rec), "idea": p.get("idea", ""), "canvas": p.get("canvas", {}),
                "notes": p.get("notes", []), "milestones": p.get("milestones", []),
                "files": p.get("files", [])}

    def next_steps(self, ref: str) -> List[str]:
        d = self.get(ref)
        steps: List[str] = []
        if d["status"] in ("done",):
            return [_STAGE_STEP["done"]]
        if d["status"] == "paused":
            return [_STAGE_STEP["paused"]]
        open_ms = [m["text"] for m in d["milestones"] if not m.get("done")]
        steps += [f"Milestone: {t}" for t in open_ms[:2]]
        missing = [c for c in CANVAS if c["key"] not in d["canvas"]]
        if missing:
            c = missing[0]
            steps.append(f"Fill in '{c['label']}': {c['ask']}")
        steps.append(_STAGE_STEP.get(d["status"], _STAGE_STEP["idea"]))
        age_days = int((now_ms() - d["updated"]) / 86_400_000)
        if age_days >= 14:
            steps.insert(0, f"It has been {age_days} days since this was touched - "
                            "pick the smallest possible next action and do it today.")
        return steps[:5]

    # ------------------------------------------------------------------ #
    # the guided interview: one question at a time, in the owner's order
    # ------------------------------------------------------------------ #
    def interview(self, ref: str) -> Dict[str, Any]:
        """The next single question, or a plain "the model is complete".

        The owner's confirmed v0.2 scope: *one question at a time*, answers kept
        in the nine canvas blocks. The store does not conduct a conversation -
        it answers "what should I ask next", so the CLI, the dashboard and a
        future voice loop all ask the identical question in the identical order.
        """
        d = self.get(ref)
        for block in CANVAS:
            if not (d["canvas"].get(block["key"]) or "").strip():
                return {"done": False, "block": block["key"], "label": block["label"],
                        "question": block["ask"], "filled": d["canvas_filled"],
                        "total": d["canvas_total"],
                        "ask_as": f'brain ask "note to {d["title"]}: {block["label"]} - '
                                  f'<your answer>"'}
        return {"done": True, "block": "", "label": "", "question": "",
                "filled": d["canvas_filled"], "total": d["canvas_total"],
                "message": "The nine blocks are filled. Add milestones, then a status."}

    def export_summary(self, ref: str, folder: str = "") -> Dict[str, Any]:
        """Write the project summary to a plain `.txt` file the owner chooses.

        Deliberately plain text, written where the owner names, never inside the
        vault and never automatically. The text is the same `report()` the owner
        already reads, so an export cannot say something the screen does not.
        """
        rep = self.report(ref)
        d = self.get(ref)
        lines = [rep["summary"], ""]
        for block in CANVAS:
            val = (d["canvas"].get(block["key"]) or "").strip()
            if val:
                lines.append(f"{block['label']}: {val}")
        if d["milestones"]:
            lines.append("")
            lines.append("Milestones:")
            for m in d["milestones"]:
                mark = "done" if m.get("done") else ("late" if _is_late(m.get("due")) else "open")
                due = f" (due {m['due']})" if m.get("due") else ""
                lines.append(f"  [{mark}] {m['text']}{due}")
        if rep["next_steps"]:
            lines.append("")
            lines.append("Next steps:")
            lines.extend(f"  - {s}" for s in rep["next_steps"])
        text = "\n".join(lines) + "\n"
        target = Path(folder).expanduser() if folder else Path.home() / "Documents"
        target.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if (ch.isalnum() or ch in " -_") else "_" for ch in d["title"]).strip()
        out = target / f"{safe or 'project'}-summary.txt"
        out.write_text(text, encoding="utf-8")
        try:
            os.chmod(out, 0o600)
        except OSError:
            pass
        return {"path": str(out), "bytes": len(text.encode("utf-8")), "chars": len(text)}

    def report(self, ref: str) -> Dict[str, Any]:
        d = self.get(ref)
        missing = [c["label"] for c in CANVAS if c["key"] not in d["canvas"]]
        latest = d["notes"][-1]["text"][:160] if d["notes"] else ""
        text = (f"{d['title']} - status: {d['status']}, last updated {d['updated_ago']}.\n"
                f"Business model: {d['canvas_filled']} of {d['canvas_total']} blocks filled"
                + (f" (missing: {', '.join(missing[:5])}{'…' if len(missing) > 5 else ''})." if missing else ".")
                + f"\nMilestones: {d['milestones_done']} of {d['milestones_total']} done."
                + f"\nNotes: {len(d['notes'])}"
                + (f" (latest: {latest})" if latest else "")
                + f". Files: {len(d['files'])}.")
        return {"project": {k: d[k] for k in ("id", "title", "status", "updated_ago")},
                "summary": text, "next_steps": self.next_steps(ref), "missing_blocks": missing}

    def weekly(self, days: int = 7) -> Dict[str, Any]:
        """What moved, what slipped, what is next - only when the owner asks.

        The confirmed v0.2 scope says the weekly summary is produced *on request*
        and never on its own. So this is a pure read: it counts notes written in
        the window, milestones completed, milestones that are late, and the next
        step for each open project. No mail, no calendar, no network.
        """
        from datetime import timedelta

        cutoff = now_ms() - int(days) * 86_400_000
        touched: List[Dict[str, Any]] = []
        done_rows: List[Dict[str, Any]] = []
        for summary in self.list():
            d = self.get(summary["id"])
            notes = [n for n in d.get("notes") or [] if int(n.get("ts") or 0) >= cutoff]
            finished = [m for m in d.get("milestones") or []
                        if m.get("done") and int(m.get("ts") or 0) >= cutoff]
            if notes or finished or int(d.get("updated") or 0) >= cutoff:
                touched.append({"project": d["title"], "id": d["id"], "status": d["status"],
                                "notes_this_week": len(notes), "finished_this_week": len(finished),
                                "next": (self.next_steps(d["id"]) or [""])[0]})
            done_rows.extend({"project": d["title"], "text": m["text"]} for m in finished)
        late = self.late_milestones()
        return {"window_days": int(days), "projects_touched": len(touched),
                "milestones_finished": len(done_rows), "late": late,
                "projects": touched, "finished": done_rows,
                "line": (f"{len(touched)} project(s) moved in the last {days} days, "
                         f"{len(done_rows)} milestone(s) finished, {len(late)} late.")}

    def resume(self) -> Optional[Dict[str, Any]]:
        """The most recently touched project that is still in progress.

        ``report()`` sorts notes OLDEST first (it prints "latest: ..." from the
        last row), so handing it straight to the caller made "Notes: N (latest:
        <the first note you ever wrote>)" - wrong in the one place the owner
        reads first. Present newest first here, without changing an edit order
        that note editing relies on. (Found in the end-to-end run.)
        """
        for s in self.list():
            if s["status"] in UNFINISHED:
                rep = self.report(s["id"])
                d = self.get(s["id"])
                rep["notes"] = list(reversed(d.get("notes") or []))
                rep["latest_note"] = rep["notes"][0]["text"] if rep["notes"] else ""
                return rep
        return None
