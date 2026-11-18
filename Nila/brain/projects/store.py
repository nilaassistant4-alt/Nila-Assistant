"""Project records on top of the encrypted memory store."""
from __future__ import annotations

import datetime as _dt
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

    def add_milestone(self, ref: str, text: str) -> Dict[str, Any]:
        rec = self._resolve(ref)
        p = self._data(rec)
        ms = p.setdefault("milestones", [])
        if len(ms) >= MAX_MILESTONES:
            raise ProjectError("too many milestones")
        if not (text or "").strip():
            raise ProjectError("the milestone is empty")
        ms.append({"text": text.strip()[:300], "done": False, "ts": now_ms()})
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
    def _summary(self, rec: Any) -> Dict[str, Any]:
        p = self._data(rec)
        canvas = p.get("canvas") or {}
        ms = p.get("milestones") or []
        return {"id": rec.id, "title": rec.title, "status": p.get("status", "idea"),
                "updated": p.get("updated", rec.updated_at), "updated_ago": _ago(p.get("updated", rec.updated_at)),
                "canvas_filled": len(canvas), "canvas_total": len(CANVAS),
                "milestones_done": sum(1 for m in ms if m.get("done")),
                "milestones_total": len(ms), "notes": len(p.get("notes", [])),
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
