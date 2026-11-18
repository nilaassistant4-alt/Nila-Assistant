"""Usage ledger: counts cloud requests per provider per day and enforces limits.

Why this exists: free API tiers have small daily and per-minute limits, and some
providers count FAILED calls against them. The ledger counts every attempt
(success or failure), stops BEFORE the limit is reached, honours rate-limit
cool-downs, and never stores any message text - only counters.

FAIL CLOSED: if the owner has not set a limit, a conservative default applies
instead of "unlimited". A ledger file that cannot be parsed is treated as
"already at the cap", never as "zero usage" - a corrupted counter must not hand
back a fresh budget.

The per-minute window is persisted with the counters. It used to live only in
``self._recent``, so restarting the process reset it and the same minute's
budget could be spent again; daily totals persisted and the per-window budget
did not.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Tuple

DEFAULT_DAILY_CAP = 20      # used only when the owner has not set daily_requests
DEFAULT_RPM = 5
DEFAULT_STOP_PCT = 80
KEEP_DAYS = 14


class UsageLedger:
    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.RLock()
        self._recent: Dict[str, Deque[float]] = {}

    # ------------------------------------------------------------------ #
    def _today(self) -> str:
        return _dt.datetime.fromtimestamp(self.clock()).strftime("%Y-%m-%d")

    #: a count is a real, non-negative integer - not a bool, not a string, not a
    #: float. `True` is an int in Python and must not pass as a request count.
    @staticmethod
    def _is_count(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def _corrupt(self) -> bool:
        """True when the file exists but is not a ledger we can trust.

        Structural, not just syntactic. ``{"days": []}`` parses as a dict yet
        carries no usage at all, and the old check accepted it: the counter reset
        to zero and a capped provider was allowed again (audit B8, reproduced).
        A value of the wrong type had the same effect the other way round - it
        raised ``ValueError`` out of ``usage()`` instead of failing closed.
        """
        if not self.path.exists():
            return False
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return True
        if not isinstance(data, dict):
            return True
        days = data.get("days", {})
        if not isinstance(days, dict):
            return True
        for day, rows in days.items():
            if not isinstance(day, str) or not isinstance(rows, dict):
                return True
            for provider, counts in rows.items():
                if not isinstance(provider, str) or not isinstance(counts, dict):
                    return True
                for field in ("requests", "failures", "tokens"):
                    if field in counts and not self._is_count(counts[field]):
                        return True
        for field in ("recent", "cooldown"):
            if field in data and not isinstance(data[field], dict):
                return True
        return False

    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if isinstance(data, dict):
                data.setdefault("days", {})
                data.setdefault("cooldown", {})
                if not isinstance(data.get("recent"), dict):
                    data["recent"] = {}
                if not isinstance(data.get("days"), dict):
                    data["days"] = {}
                if not isinstance(data.get("cooldown"), dict):
                    data["cooldown"] = {}
                return data
        except (OSError, ValueError):
            pass
        return {"days": {}, "cooldown": {}, "recent": {}}

    def _save(self, data: Dict[str, Any]) -> None:
        days = sorted(data["days"])
        for d in days[:-KEEP_DAYS]:
            data["days"].pop(d, None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), "utf-8")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ #
    @staticmethod
    def limits(cfg: Dict[str, Any]) -> Dict[str, Any]:
        cap = int(cfg.get("daily_requests") or 0)
        return {
            "cap": cap if cap > 0 else DEFAULT_DAILY_CAP,
            "cap_is_default": cap <= 0,
            "rpm": int(cfg.get("rpm") or DEFAULT_RPM),
            "stop_pct": min(100, max(10, int(cfg.get("stop_at_pct") or DEFAULT_STOP_PCT))),
        }

    def usage(self, provider: str) -> Dict[str, int]:
        with self._lock:
            day = self._load()["days"].get(self._today(), {})
            u = day.get(provider, {}) if isinstance(day.get(provider, {}), dict) else {}
            return {"requests": self._count(u.get("requests")),
                    "failures": self._count(u.get("failures")),
                    "tokens": self._count(u.get("tokens"))}

    @classmethod
    def _count(cls, value: Any) -> int:
        return value if cls._is_count(value) else 0

    def cooldown_left(self, provider: str) -> int:
        with self._lock:
            until = float(self._load()["cooldown"].get(provider, 0))
            return max(0, int(until - self.clock()))

    def check(self, provider: str, cfg: Dict[str, Any]) -> Tuple[bool, str]:
        lim = self.limits(cfg)
        if self._corrupt():
            # FAIL CLOSED: a counter we cannot read is not a counter that says 0.
            return False, (f"the usage ledger at {self.path} is unreadable or corrupt; "
                           f"requests to {provider} are refused until it is repaired "
                           f"or removed deliberately")
        left = self.cooldown_left(provider)
        if left > 0:
            return False, (f"{provider} asked us to slow down; paused for another {left}s")
        used = self.usage(provider)["requests"]
        allowed = max(1, lim["cap"] * lim["stop_pct"] // 100)
        if used >= allowed:
            return False, (f"daily limit reached for {provider} ({used} of {allowed} allowed, "
                           f"cap {lim['cap']}); it resets tomorrow")
        with self._lock:
            if len(self._recent_window(provider)) >= lim["rpm"]:
                return False, f"too many {provider} requests this minute (limit {lim['rpm']})"
        return True, ""

    def _recent_window(self, provider: str) -> list:
        """Timestamps of successful requests in the last 60s, from DISK.

        The in-memory deque is a cache in front of the same data, not the source
        of truth: a restart must not refund a minute's budget.
        """
        now = self.clock()
        q = self._recent.setdefault(provider, deque())
        while q and now - q[0] > 60:
            q.popleft()
        stored = self._load().get("recent", {}).get(provider) or []
        # A deque cannot hold two identical floats, and two requests inside the
        # same clock tick ARE two requests: count them separately.
        from_disk = [float(t) for t in stored if now - float(t) <= 60]
        merged = list(from_disk)
        for t in q:
            if now - float(t) > 60:
                continue
            try:
                merged.remove(float(t))
            except ValueError:
                pass
        merged.extend(float(t) for t in q if now - float(t) <= 60)
        q.clear()
        q.extend(from_disk)
        return merged

    def record(self, provider: str, ok: bool, tokens: int = 0) -> None:
        with self._lock:
            data = self._load()
            day = data["days"].setdefault(self._today(), {})
            u = day.setdefault(provider, {"requests": 0, "failures": 0, "tokens": 0})
            u["requests"] += 1          # failed calls count: some providers charge them too
            if not ok:
                u["failures"] += 1
            u["tokens"] += max(0, int(tokens or 0))
            now = self.clock()
            recent = data["recent"].get(provider) or []
            recent = [float(t) for t in recent if now - float(t) <= 60]
            recent.append(now)
            data["recent"][provider] = recent[-500:]
            self._save(data)
            self._recent.setdefault(provider, deque()).append(now)

    def set_cooldown(self, provider: str, seconds: int) -> None:
        with self._lock:
            data = self._load()
            data["cooldown"][provider] = self.clock() + max(1, min(int(seconds), 3600))
            self._save(data)

    def snapshot(self, provider: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        lim = self.limits(cfg)
        u = self.usage(provider)
        allowed = max(1, lim["cap"] * lim["stop_pct"] // 100)
        return {"provider": provider, "used": u["requests"], "failures": u["failures"],
                "tokens": u["tokens"], "cap": lim["cap"], "allowed": allowed,
                "remaining": max(0, allowed - u["requests"]),
                "pct": min(100, round(100 * u["requests"] / allowed)),
                "cap_is_default": lim["cap_is_default"],
                "cooldown_s": self.cooldown_left(provider)}
