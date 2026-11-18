"""Runtime configuration, paths and the key hierarchy.

Layout under ``BRAIN_HOME`` (default ``~/.personal-brain``)::

    identity.json   wrapped master key, salts, voice template, TOTP, recovery
    memory.db       encrypted-at-rest memory vault (SQLite + FTS5)
    audit.jsonl     append-only security/audit journal
    sync/           sync journal, cursors, conflict copies
    updates/        pinned update channels, downloaded+verified artifacts
    tmp/            scratch (never persisted, best-effort wiped)

Everything sensitive lives inside ``identity.json`` / ``memory.db`` as
ciphertext; the plaintext master key exists only in process memory while a
session is unlocked.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .util.misc import ensure_dir, read_bytes, write_private

APP_NAME = "personal-brain"
#: v3: the idle rule became 30 minutes warning / 60 minutes lock, so the two
#: retired BUILT-IN defaults (900 s and 7200 s) migrate. A hand-set value stays.
CONFIG_VERSION = 3
DEFAULT_HOME = Path(os.environ.get("BRAIN_HOME", str(Path.home() / f".{APP_NAME}")))

# KDF work factors. Lowered automatically in tests via Config(fast_kdf=True).
KDF_N = 2 ** 15
KDF_N_FAST = 2 ** 12
KDF_R = 8
KDF_P = 1

LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_BASE_SECONDS = 30
class StorageUnavailable(RuntimeError):
    """The folder the owner chose for the archive cannot be reached."""


LEGACY_SESSION_TIMEOUT_SECONDS = 15 * 60
#: Idle timeout. The owner's rule for v0.2: a session is not left open all
#: afternoon by accident. 30 minutes without an owner action warns, 60 minutes
#: locks. Auto-refresh (the dashboard poll, a heartbeat) is NOT an owner action
#: and never extends this - see ``Vault.touch_session``.
WARN_AFTER_SECONDS = 30 * 60
SESSION_TIMEOUT_SECONDS = 60 * 60
SESSION_ABSOLUTE_TIMEOUT_SECONDS = 12 * 60 * 60


@dataclass
class Config:
    home: Path = DEFAULT_HOME
    fast_kdf: bool = False
    session_timeout: int = SESSION_TIMEOUT_SECONDS
    session_absolute_timeout: int = SESSION_ABSOLUTE_TIMEOUT_SECONDS
    sync: Dict[str, Any] = field(default_factory=dict)
    updates: Dict[str, Any] = field(default_factory=dict)
    agent: Dict[str, Any] = field(default_factory=dict)
    identity: Dict[str, Any] = field(default_factory=dict)

    # -- paths ------------------------------------------------------------- #
    @property
    def identity_path(self) -> Path:
        return self.home / "identity.json"

    @property
    def memory_path(self) -> Path:
        """Where the encrypted store lives.

        The owner keeps the archive on D: - a 100 GB video drive - and asked Nila
        to point this at a folder of their own rather than back it up only on C:.
        Two settings decide it, in this order:

        * ``agent.storage_dir`` - an explicit folder. Used when it exists or when
          its drive exists; otherwise the default below is kept and the reason is
          reported by ``brain storage``, because silently writing to C: when the
          owner asked for D: hides a real problem.
        * ``agent.storage_fallback_to_c`` (default False) - if the folder or its
          drive is missing, fall back to ``home/memory.db`` instead of failing.

        A drive that is gone is a refusal, not a smaller database: recreating an
        empty store on C: would make the owner's whole history look deleted.
        """
        configured = str((self.agent or {}).get("storage_dir") or "").strip()
        if not configured:
            return self.home / "memory.db"
        target = Path(configured).expanduser()
        drive = target.drive or (target.parts[0] if target.parts else "")
        drive_ok = True
        if drive and len(drive) == 2 and drive[1] == ":":
            drive_ok = Path(drive + os.sep).exists()
        # Do NOT create anything here. This property is read by `brain storage`
        # and by the vault before it knows whether the owner wants a store there
        # yet; a getter that makes folders turns "show me the setting" into a
        # side effect. `ensure_layout` creates it when it is actually used.
        if drive_ok:
            return target / "memory.db"
        if (self.agent or {}).get("storage_fallback_to_c"):
            return self.home / "memory.db"
        raise StorageUnavailable(
            f"the storage folder {configured} is not reachable (is the drive "
            f"connected?). Nothing was created in its place - run "
            f"`brain storage --check` to see this, or `--fallback-c` to allow "
            f"falling back to {self.home / 'memory.db'}")

    @property
    def audit_path(self) -> Path:
        return self.home / "audit.jsonl"

    @property
    def sync_dir(self) -> Path:
        return self.home / "sync"

    @property
    def update_dir(self) -> Path:
        return self.home / "updates"

    @property
    def tmp_dir(self) -> Path:
        return self.home / "tmp"

    @property
    def config_path(self) -> Path:
        return self.home / "config.json"

    @property
    def kdf_n(self) -> int:
        return KDF_N_FAST if self.fast_kdf else KDF_N

    def ensure_layout(self) -> "Config":
        for d in (self.home, self.sync_dir, self.update_dir, self.tmp_dir):
            ensure_dir(d)
        return self

    # -- persistence ------------------------------------------------------- #
    def save(self) -> None:
        payload = {
            "config_version": CONFIG_VERSION,
            "fast_kdf": self.fast_kdf,
            "session_timeout": self.session_timeout,
            "session_absolute_timeout": self.session_absolute_timeout,
            "sync": self.sync,
            "updates": self.updates,
            "agent": self.agent,
            "identity": self.identity,
        }
        write_private(self.config_path, json.dumps(payload, indent=2).encode())

    @classmethod
    def load(cls, home: Optional[Path | str] = None) -> "Config":
        path = Path(home) if home else DEFAULT_HOME
        cfg = cls(home=path)
        cfg.ensure_layout()
        if cfg.config_path.exists():
            try:
                data = json.loads(read_bytes(cfg.config_path))
                cfg.fast_kdf = bool(data.get("fast_kdf", False))
                config_version = int(data.get("config_version", 1))
                needs_upgrade = config_version < CONFIG_VERSION
                stored_idle_timeout = data.get("session_timeout")
                if stored_idle_timeout is None:
                    cfg.session_timeout = SESSION_TIMEOUT_SECONDS
                else:
                    cfg.session_timeout = int(stored_idle_timeout)
                    # 900 s was the old built-in default, not a UI-selectable
                    # preference. Move those v1 configs to the newly confirmed
                    # 2 h idle default while preserving other custom values.
                    # 900 s (the pre-v0.2 built-in) and 7200 s (the previous
                    # build's built-in) are retired DEFAULTS, not preferences:
                    # move them to the confirmed 60-minute idle rule while
                    # preserving any other custom value.
                    if (needs_upgrade
                            and cfg.session_timeout in (LEGACY_SESSION_TIMEOUT_SECONDS,
                                                        2 * 60 * 60)):
                        cfg.session_timeout = SESSION_TIMEOUT_SECONDS
                cfg.session_absolute_timeout = int(data.get(
                    "session_absolute_timeout", SESSION_ABSOLUTE_TIMEOUT_SECONDS))
                cfg.sync = dict(data.get("sync") or {})
                cfg.updates = dict(data.get("updates") or {})
                cfg.agent = dict(data.get("agent") or {})
                cfg.identity = dict(data.get("identity") or {})
            except Exception:
                pass  # corrupt config -> fall back to defaults, never crash
        return cfg

    # -- typed views ------------------------------------------------------- #
    def provider_config(self, role: Optional[str] = None) -> Dict[str, Any]:
        """Provider settings. ``role`` is ``"local"`` or ``"online"`` (the router's
        two lanes); without it the legacy single-provider settings are returned."""
        if role:
            lane = dict((self.agent.get("providers") or {}).get(role) or {})
            lane.setdefault("temperature", float(self.agent.get("temperature", 0.2)))
            lane.setdefault("timeout", float(self.agent.get("timeout", 30 if role == "online" else 120)))
            lane.setdefault("tools_mode", self.agent.get("tools_mode", "auto"))
            return lane
        return {
            "type": self.agent.get("provider", "local"),
            "model": self.agent.get("model"),
            "api_key_env": self.agent.get("api_key_env"),
            "base_url": self.agent.get("base_url"),
            "temperature": float(self.agent.get("temperature", 0.2)),
            "timeout": float(self.agent.get("timeout", 30)),
            "tools_mode": self.agent.get("tools_mode", "auto"),
        }

    @property
    def warn_after(self) -> int:
        """Seconds without an owner action before the "still there?" banner."""
        configured = self.agent.get("session_warn_after")
        try:
            value = int(configured) if configured else WARN_AFTER_SECONDS
        except (TypeError, ValueError):
            value = WARN_AFTER_SECONDS
        # Never warn after the lock, and never warn so early it is noise.
        return max(60, min(value, max(60, int(self.session_timeout) - 60)))

    def voice_config(self) -> Dict[str, Any]:
        ident = self.identity
        return {
                        # None -> use the threshold measured by the shipped calibration model
            "threshold": ident.get("voice_threshold"),
            "backend": ident.get("voice_backend", "mfcc"),
            "require_liveness": bool(ident.get("require_liveness", True)),
            "challenge_len": int(ident.get("challenge_len", 6)),
            "challenge_seconds": float(ident.get("challenge_seconds", 4.0)),
            "min_duration": float(ident.get("min_duration", 1.2)),
            "max_duration": float(ident.get("max_duration", 12.0)),
            "replay_window": int(ident.get("replay_window", 48)),
            "max_attempts": int(ident.get("max_attempts", LOCKOUT_MAX_ATTEMPTS)),
            "lockout_base": int(ident.get("lockout_base", LOCKOUT_BASE_SECONDS)),
            "samples": int(ident.get("enroll_samples", 3)),
            "threshold_policy": ident.get("voice_threshold_policy", "secure"),
        }
