"""Mutable, shared session-key buffer with best-effort lock invalidation.

Python and crypto backends can create short-lived immutable copies, so this is
not a defence against a process-memory scraper. It does avoid retaining a
separate long-lived master-key copy in each MemoryStore and overwrites the
shared buffer when a session is locked.
"""
from __future__ import annotations

import threading
import weakref
from typing import Any


class MasterKey(bytearray):
    """A mutable 32-byte key shared by one Vault session and its stores."""

    def __new__(cls, value: bytes | bytearray | "MasterKey" = b""):
        return super().__new__(cls)

    def __init__(self, value: bytes | bytearray | "MasterKey" = b""):
        super().__init__(value)
        self._active = True
        self._lock = threading.RLock()
        self._stores: weakref.WeakSet[Any] = weakref.WeakSet()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def require_active(self) -> None:
        if not self.active:
            raise RuntimeError("master key is locked")

    def register_store(self, store: Any) -> None:
        with self._lock:
            if not self._active:
                raise RuntimeError("master key is locked")
            self._stores.add(store)

    def unregister_store(self, store: Any) -> None:
        with self._lock:
            self._stores.discard(store)

    def zeroize(self) -> None:
        """Overwrite this shared key buffer, then invalidate dependent stores."""
        with self._lock:
            if not self._active:
                return
            self[:] = b"\x00" * len(self)
            self._active = False
            stores = list(self._stores)
            self._stores.clear()
        # Do not hold the key lock while taking a store lock: store operations
        # may acquire locks in the opposite order while reading the key.
        for store in stores:
            try:
                store._on_master_key_lock()
            except Exception:
                # The shared buffer is already zeroed. A broken store callback
                # must never prevent Vault.lock() from clearing the session.
                pass

    def __repr__(self) -> str:
        state = "active" if self.active else "zeroized"
        return f"<MasterKey length={len(self)} {state}>"

    __str__ = __repr__
