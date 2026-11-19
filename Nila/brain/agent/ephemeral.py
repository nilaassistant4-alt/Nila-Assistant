"""A value you can use once, that is never written down.

The owner's mandate says it plainly: *a number used for a temporary task must
not be saved unless I explicitly tell you to save it, and what is unnecessary is
purged.* Until now that was only a habit the model was asked to keep - it could
choose to call ``remember`` and there was no tier that made forgetting the
default. This module is that missing tier.

Three rules, and they are enforced by what the code cannot do:

1. **Nothing verbatim is stored.** A value enters as text. What is kept is a
   *slot*: an id, what it is for, when it expires, and a SHA-256 of the value.
   There is no column and no attribute that holds the words. ``vault_slot()``
   writes only a hash, and the hash is of a per-process random salt plus the
   value, so even the digest cannot be looked up in a table of phone numbers.
2. **It lives in RAM, and it dies.** The value is in a dictionary held by the
   process. ``purge()`` empties it, expiry empties it, ``brain lock`` empties it,
   and the process ending empties it. A restart does not restore it, and the
   memory database is never touched, so sync never carries it and a backup never
   contains it.
3. **Using it is the only way to see it.** ``take()`` returns the value *once*
   and then forgets it, the way a one-time code works. A second ``take`` says
   "that value was used and is gone" instead of handing it over again -
   because a value that can be read twice is a value that was stored.

What this is NOT: a secret manager. If the owner wants a number kept, that is
``brain remember`` - durable, encrypted, recalled on purpose - and it is a
different act with a different audit line. Having both, and being able to tell
them apart, is the point.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

#: A slot with no stated life is short on purpose. Long enough to finish the
#: task it was made for, short enough that it is not a place things accumulate.
DEFAULT_TTL_SECONDS = 60 * 15
MAX_SLOTS = 32


class EphemeralError(RuntimeError):
    pass


def _now() -> float:
    return time.time()


@dataclass
class Slot:
    """The *record* of a value, deliberately without the value in it."""

    id: str
    purpose: str
    kind: str
    digest: str
    created_at: float
    expires_at: float
    used_at: float = 0.0
    #: repr() is what shows up in a traceback or a debug log, so it is written
    #: by hand: the default dataclass repr would print every field, and the day
    #: somebody adds a ``value`` field the default repr would print that too.
    def __repr__(self) -> str:                      # pragma: no cover - debug only
        return (f"Slot(id={self.id!r}, purpose={self.purpose!r}, kind={self.kind!r}, "
                f"expires_in={max(0, int(self.expires_at - _now()))}s)")

    def describe(self) -> Dict[str, Any]:
        left = max(0, int(self.expires_at - _now()))
        return {"id": self.id, "purpose": self.purpose, "kind": self.kind,
                "expires_in": left, "used": bool(self.used_at),
                "has_value": not self.used_at and left > 0,
                "note": "the value itself is not on disk and not in this record"}


class EphemeralStore:
    """Values in memory only, one process, one use each."""

    def __init__(self, salt: Optional[bytes] = None, clock=None):
        self._salt = salt or secrets.token_bytes(16)
        self._values: Dict[str, str] = {}
        self._slots: Dict[str, Slot] = {}
        self._clock = clock or _now
        #: Filled by the callers that keep an audit journal, so a purge can be
        #: recorded without this module importing the vault.
        self.journal: Optional[Any] = None

    # -- the digest ----------------------------------------------------- #
    def digest(self, value: str, purpose: str = "") -> str:
        """A per-process-salted digest. Not reversible, not comparable across runs."""
        h = hashlib.sha256()
        h.update(self._salt)
        h.update(b"\x00")
        h.update(str(purpose).encode())
        h.update(b"\x00")
        h.update(str(value).encode())
        return h.hexdigest()[:32]

    # -- putting a value in --------------------------------------------- #
    def keep(self, value: str, purpose: str = "", kind: str = "value",
             ttl: Optional[float] = None) -> Slot:
        """Hold a value in RAM against a named purpose, and return the slot."""
        text = str(value or "")
        if not text.strip():
            raise EphemeralError("there is nothing to hold")
        purpose = (purpose or "a temporary task").strip()[:120]
        ttl = float(DEFAULT_TTL_SECONDS if ttl is None else ttl)
        if ttl <= 0:
            raise EphemeralError("a slot must live for some time - use the value instead")
        self._prune()
        live = [s for s in self._slots.values() if not s.used_at and s.expires_at > self._clock()]
        if len(live) >= MAX_SLOTS:
            raise EphemeralError(
                f"{len(live)} values are already being held; finish or purge them first "
                f"(`brain temp --purge`). A use-once store that grows without a limit is "
                f"just a database with worse guarantees.")
        sid = "tmp_" + secrets.token_urlsafe(9)
        slot = Slot(id=sid, purpose=purpose, kind=str(kind or "value"),
                    digest=self.digest(text, purpose),
                    created_at=self._clock(), expires_at=self._clock() + ttl)
        self._slots[sid] = slot
        self._values[sid] = text
        self._audit("temp_held", {"id": sid, "purpose": purpose, "kind": slot.kind,
                                  "digest": slot.digest, "ttl": int(ttl)})
        return slot

    # -- taking it out again -------------------------------------------- #
    def take(self, sid: str) -> Dict[str, Any]:
        """Hand the value over ONCE, then forget it."""
        self._prune()
        slot = self._slots.get(str(sid))
        if slot is None:
            raise EphemeralError(
                f"no value called '{sid}' - it was used, it expired, or it was never here")
        if slot.used_at:
            raise EphemeralError(
                f"'{sid}' was already used once and is gone; that is what use-once means")
        if slot.expires_at <= self._clock():
            self._drop(sid)
            raise EphemeralError(f"'{sid}' expired and was purged")
        value = self._values.pop(sid, "")
        slot.used_at = self._clock()
        self._audit("temp_used", {"id": sid, "purpose": slot.purpose, "digest": slot.digest})
        return {"ok": True, "id": sid, "purpose": slot.purpose, "value": value,
                "note": "this was the only copy in Nila's hands; it is gone now"}

    def peek_purpose(self, sid: str) -> str:
        """What a slot is for - never the value. Safe to print anywhere."""
        slot = self._slots.get(str(sid))
        return slot.purpose if slot else ""

    def find(self, purpose_words: str) -> List[Slot]:
        """Live slots whose purpose contains these words. Values are untouched."""
        words = [w for w in str(purpose_words or "").lower().split() if w]
        self._prune()
        out = [s for s in self._slots.values()
               if not s.used_at and s.expires_at > self._clock()
               and all(w in s.purpose.lower() for w in words)]
        return sorted(out, key=lambda s: s.created_at)

    # -- the two ways it dies -------------------------------------------- #
    def _drop(self, sid: str) -> None:
        self._slots.pop(sid, None)
        self._values.pop(sid, None)

    def _prune(self) -> int:
        now = self._clock()
        dead = [s.id for s in self._slots.values() if s.expires_at <= now]
        for sid in dead:
            self._drop(sid)
        if dead:
            self._audit("temp_expired", {"count": len(dead)})
        return len(dead)

    def purge(self, reason: str = "asked") -> Dict[str, Any]:
        """Forget everything, now. Called by `brain temp --purge` and by lock."""
        count = len(self._values)
        self._values.clear()
        for slot in self._slots.values():
            slot.used_at = slot.used_at or self._clock()
        self._slots.clear()
        self._audit("temp_purged", {"count": count, "reason": str(reason)[:60]})
        return {"ok": True, "purged": count, "reason": reason,
                "note": "nothing was ever written down, so there is nothing else to erase"}

    def slots(self) -> List[Dict[str, Any]]:
        self._prune()
        return [s.describe() for s in sorted(self._slots.values(),
                                            key=lambda s: s.created_at)]

    def report(self) -> Dict[str, Any]:
        live = [s for s in self._slots.values() if not s.used_at]
        return {"ok": True, "held": len(live), "used": sum(1 for s in self._slots.values()
                                                           if s.used_at),
                "slots": self.slots(),
                "stored_on_disk": False, "synced": False, "in_backup": False,
                "note": ("values live in this process's RAM only. There is no table, no "
                         "column and no file: a restart, a lock or an expiry removes them, "
                         "and they never reach sync or a backup.")}

    # -- the one thing that MAY be written down -------------------------- #
    def vault_slot(self, sid: str) -> Dict[str, Any]:
        """A slot as it is allowed to appear in the sealed vault: a HASH only."""
        slot = self._slots.get(str(sid))
        if slot is None:
            raise EphemeralError(f"no value called '{sid}'")
        return {"id": slot.id, "purpose": slot.purpose, "kind": slot.kind,
                "digest": slot.digest, "created_at": int(slot.created_at),
                "expires_at": int(slot.expires_at),
                "value_recorded": False}

    def _audit(self, event: str, detail: Dict[str, Any]) -> None:
        if self.journal is None:
            return
        try:
            self.journal(event, detail)
        except Exception:                      # pragma: no cover - journal is best effort
            pass


#: One store per process. `brain temp` and the agent share it, which is what
#: makes "hold this number, then use it" work across two commands in one run.
_SHARED: Optional[EphemeralStore] = None


def shared() -> EphemeralStore:
    global _SHARED
    if _SHARED is None:
        _SHARED = EphemeralStore()
    return _SHARED


def purge_shared(reason: str = "lock") -> Dict[str, Any]:
    return shared().purge(reason=reason)
