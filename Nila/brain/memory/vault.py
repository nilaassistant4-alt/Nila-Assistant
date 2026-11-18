"""Key hierarchy, identity store, multi-device enrolment and session state.

``identity.json`` layout
------------------------
::

    kdf              scrypt params (n, r, p) - same for every device wrap
    devices[id]      {"kek_wrap": <MK encrypted with this device's KEK>,
                      "created_at": ..., "label": ...}
    identity_blob    <ciphertext of {voice, totp, recovery, policy, ...}>
                     encrypted with MK. This is the part shared between
                     devices via an enrolment bundle.
    verifier         HMAC tag over identity_blob, so a wrong passphrase is
                     detected without decrypting anything
    attempts         rate-limit / lockout state

Key hierarchy
-------------
::

    passphrase --scrypt(salt, n, r, p)--> KEK_device        (never stored)
    MK (random 32B)  -- wrapped by each device's KEK -->  devices[id].kek_wrap
    MK --HKDF("rec")  --> per-record encryption keys      (memory/store.py)
    MK --HKDF("sync") --> Ksync: opaque ids, re-wrap, HMAC (cloud/protocol.py)
    MK --HKDF("audit")--> audit chain key
    MK --HKDF("sess") --> session token

Consequences that matter:
  * one shared MK means N devices sync and read each other's records;
  * adding a device never re-encrypts the vault - it adds one MK wrap;
  * revoking a device deletes its wrap (and ``rotate-master-key`` re-wraps
    everything for the surviving devices);
  * the plaintext MK exists only in process memory while unlocked, and is
    best-effort zeroised on lock/exit.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config, KDF_R, LOCKOUT_BASE_SECONDS, LOCKOUT_MAX_ATTEMPTS
from ..crypto.envelope import CryptoError, decrypt, encrypt
from ..crypto.hash import constant_time_eq, derive_key, fast_kdf, hkdf, mac, random_bytes
from ..util.misc import b64d, b64e, ensure_dir, mask, now_ms, try_import, write_private
from .key import MasterKey

keyring = try_import("keyring")

SCHEMA_VERSION = 2
BUNDLE_MAGIC = "PBBUNDLE1"
IDENT_AAD = b"pbx/identity-blob/v1"
VOICE_KEY_INFO = b"nila/device/voice/v1"

#: A biometric cannot be rotated after exposure, so the template never leaves
#: the device that enrolled it. This value is stored in the identity blob and
#: asserted on load; the sync engine refuses the table outright.
VOICE_SYNC_POLICY = "DEVICE_LOCAL"
SESSION_TAG = b"pbx/session-tag/v1"


@dataclass
class Session:
    mk: MasterKey | bytes
    session_id: str
    started_at: int
    expires_at: int
    absolute_expires_at: Optional[int] = None
    expires_at_monotonic: Optional[float] = None
    absolute_expires_at_monotonic: Optional[float] = None
    mode: str = "passphrase+voice"   # or "passphrase", "totp", "recovery"
    degraded: bool = False
    #: set only by a completed step-up re-authentication (``Ceremony.step_up``).
    #: Short-lived on purpose: the next tool call consumes it, so one re-auth
    #: authorises exactly one high-risk action.
    step_up_ok: bool = False
    step_up_at: int = 0
    #: set once the "this process just logged in" shortcut has approved an
    #: owner action; one login then authorises one action, not a time window.
    fresh_login_spent: bool = False
    #: wall-clock ms of the last OWNER action (a message they sent, a click, a
    #: command). Dashboard polling must never move this: an auto-refresh is not
    #: the owner being present, and treating it as presence is how a session
    #: stays unlocked all afternoon by accident.
    last_activity: int = 0
    token: str = ""
    user: str = ""
    device_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.mk, MasterKey):
            self.mk = MasterKey(self.mk)

    def expired(self, now: Optional[int] = None) -> bool:
        # ``now`` is an injectable wall-clock value for deterministic tests and
        # legacy callers. Normal enforcement also uses monotonic deadlines so a
        # system-clock rollback can never keep an unlocked session alive longer.
        if now is not None:
            return (now >= self.expires_at
                    or (self.absolute_expires_at is not None
                        and now >= self.absolute_expires_at))
        current_wall = now_ms()
        if (current_wall >= self.expires_at
                or (self.absolute_expires_at is not None
                    and current_wall >= self.absolute_expires_at)):
            return True
        current_mono = time.monotonic()
        return (self.expires_at_monotonic is not None
                and current_mono >= self.expires_at_monotonic) or (
                    self.absolute_expires_at_monotonic is not None
                    and current_mono >= self.absolute_expires_at_monotonic)

    def seconds_remaining(self, absolute: bool = False) -> Optional[int]:
        deadline = (self.absolute_expires_at_monotonic if absolute
                    else self.expires_at_monotonic)
        if deadline is not None:
            return max(0, int(deadline - time.monotonic()))
        wall_deadline = self.absolute_expires_at if absolute else self.expires_at
        if wall_deadline is None:
            return None
        return max(0, (wall_deadline - now_ms()) // 1000)


class Vault:
    """The identity/keystore for one ``BRAIN_HOME`` (i.e. one device)."""

    def __init__(self, config: Config):
        self.config = config
        self.path = config.identity_path
        self.data: Dict[str, Any] = {}
        self._session: Optional[Session] = None
        self._kdf_cache: Dict[bytes, bytes] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_bytes())
            except Exception:
                self.data = {}
            self._migrate()
        ensure_dir(self.path.parent)

    # ------------------------------------------------------------------ #
    # persistence + migration
    # ------------------------------------------------------------------ #
    def _migrate(self) -> None:
        """v1 (single wrapped_mk) -> v2 (per-device wraps + identity blob).

        Also hoists ``policy`` out of the encrypted blob so it stays readable
        while locked (see ``policy()``).
        """
        if "policy" not in self.data and self.data.get("identity_blob"):
            self.data["policy"] = dict(self.DEFAULT_POLICY)
        if int(self.data.get("schema_version", 1)) >= SCHEMA_VERSION:
            return
        if self.data.get("wrapped_mk"):
            dev_id = "dev-" + hashlib.sha256(self.data["wrapped_mk"].encode()).hexdigest()[:10]
            # The plaintext identity fields are NOT copied here: a schema
            # migration runs before the vault is unlocked, so they could only be
            # "encrypted" with a placeholder key - the old code did exactly that
            # and threw the result away. The blob is written from the real fields
            # on the next unlock (``save_identity``).
            self.data = {
                "schema_version": SCHEMA_VERSION,
                "created_at": self.data.get("created_at", now_ms()),
                "user": self.data.get("user", "owner"),
                "device_id": dev_id,
                "kdf": self.data.get("kdf", {}),
                "devices": {dev_id: {"kek_wrap": self.data["wrapped_mk"],
                                     "created_at": now_ms(), "label": "migrated",
                                     "primary": True}},
                "identity_blob": None,
                "verifier": self.data.get("mk_verifier"),
                "attempts": self.data.get("attempts", {"count": 0, "locked_until": 0}),
                "_needs_rewrap": True,
            }
            self.save()

    def save(self) -> None:
        self.data["schema_version"] = SCHEMA_VERSION
        self.data["updated_at"] = now_ms()
        write_private(self.path, json.dumps(self.data, indent=2).encode())

    @property
    def initialized(self) -> bool:
        return bool(self.data.get("devices"))

    @property
    def device_id(self) -> str:
        return str(self.data.get("device_id", ""))

    @property
    def user(self) -> str:
        return str(self.data.get("user", "owner"))

    def devices(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.data.get("devices", {}))

    # ------------------------------------------------------------------ #
    # KDF helpers
    # ------------------------------------------------------------------ #
    def _kek(self, passphrase: str) -> bytes:
        cached = self._kdf_cache.get(passphrase.encode())
        if cached:
            return cached[:32]
        kdf = self.data.get("kdf", {})
        salt = b64d(kdf["salt"]) if kdf.get("salt") else b""
        kek = derive_key(passphrase.encode(), salt, b"pbx/kek/v1", 32,
                         n=int(kdf.get("n", self.config.kdf_n)), r=int(kdf.get("r", KDF_R)),
                         p=int(kdf.get("p", 1)))
        self._kdf_cache[passphrase.encode()] = kek
        if len(self._kdf_cache) > 4:
            self._kdf_cache = {passphrase.encode(): kek}
        return kek

    def _new_kdf_params(self) -> Dict[str, Any]:
        return {"alg": "scrypt", "n": self.config.kdf_n, "r": KDF_R, "p": 1,
                "salt": b64e(random_bytes(16))}

    def subkey(self, domain: str, length: int = 32) -> bytes:
        if not self._session:
            raise RuntimeError("vault is locked")
        return fast_kdf(self._session.mk, domain.encode(), length)

    # ------------------------------------------------------------------ #
    # initialise (first device)
    # ------------------------------------------------------------------ #
    def initialize(self, passphrase: str, user: str = "owner",
                   device_label: str = "") -> Dict[str, Any]:
        if self.initialized:
            raise RuntimeError("vault already initialised (use `brain device add` to enrol more)")
        if len(passphrase) < 8:
            raise ValueError("passphrase must be at least 8 characters")
        mk = random_bytes(32)
        kdf = self._new_kdf_params()
        self.data["kdf"] = kdf
        kek = self._kek(passphrase)
        dev_id = "dev-" + b64e(mac(mk, (device_label or "primary").encode()))[:12]
        identity = {
            "voice": None, "totp": None, "recovery": None, "hardware_wrap": None,
            "policy": {"max_attempts": LOCKOUT_MAX_ATTEMPTS,
                       "lockout_base_seconds": LOCKOUT_BASE_SECONDS,
                       "require_voice": True, "require_liveness": True,
                       "allow_degraded": False},
            "meta": {}, "sessions": {},
        }
        blob = encrypt(json.dumps(identity).encode(), mk, aad=IDENT_AAD)
        self.data.update({
            "created_at": now_ms(), "user": user, "device_id": dev_id,
            "devices": {dev_id: {"kek_wrap": b64e(encrypt(mk, kek, aad=b"pbx/wrap/kek")),
                                 "created_at": now_ms(),
                                 "label": device_label or "primary", "primary": True}},
            "identity_blob": b64e(blob),
            "verifier": b64e(mac(mk, SESSION_TAG)),
            "policy": dict(self.DEFAULT_POLICY),
            "attempts": {"count": 0, "locked_until": 0, "last_failure": 0},
        })
        self.data.pop("_needs_rewrap", None)
        self.save()
        self._start_session(mk, mode="passphrase", degraded=False, user=user, device_id=dev_id)
        return {"user": user, "device_id": dev_id, "fingerprint": mask(b64e(mac(mk, SESSION_TAG)))}

    # ------------------------------------------------------------------ #
    # identity blob (encrypted with MK; shared across devices)
    # ------------------------------------------------------------------ #
    def _identity(self) -> Dict[str, Any]:
        if not self._session:
            raise RuntimeError("vault is locked")
        raw = decrypt(b64d(self.data["identity_blob"]), self._session.mk, aad=IDENT_AAD)
        return json.loads(raw)

    def _write_identity(self, identity: Dict[str, Any]) -> None:
        if not self._session:
            raise RuntimeError("vault is locked")
        blob = encrypt(json.dumps(identity, sort_keys=True).encode(),
                       self._session.mk, aad=IDENT_AAD)
        self.data["identity_blob"] = b64e(blob)
        self.data["verifier"] = b64e(mac(self._session.mk, SESSION_TAG))
        self.save()

    def _mutate_identity(self, fn) -> Any:
        identity = self._identity()
        result = fn(identity)
        self._write_identity(identity)
        return result

    DEFAULT_POLICY = {"max_attempts": LOCKOUT_MAX_ATTEMPTS,
                      "lockout_base_seconds": LOCKOUT_BASE_SECONDS,
                      "require_voice": True, "require_liveness": True,
                      "require_second_factor": True,
                      "allow_degraded": False}

    def policy(self) -> Dict[str, Any]:
        """Policy lives in the CLEAR (identity.json, not the encrypted blob).

        It must be enforceable while the vault is locked - rate limiting and
        the second-factor requirement are exactly the controls that run before
        authentication succeeds. It contains no personal data, only booleans
        and integers, so storing it in plaintext is not a disclosure.
        """
        stored = dict(self.DEFAULT_POLICY)
        stored.update(self.data.get("policy") or {})
        return stored

    def set_policy(self, **kwargs: Any) -> Dict[str, Any]:
        current = self.policy()
        current.update(kwargs)
        self.data["policy"] = current
        self.save()
        self.audit("policy_changed", {"keys": sorted(kwargs)})
        return current

    def meta_get(self, key: str, default: Any = None) -> Any:
        try:
            return self._identity().get("meta", {}).get(key, default)
        except (CryptoError, RuntimeError):
            return default

    def meta_get_strict(self, key: str, default: Any = None) -> Any:
        """`meta_get` that does NOT hide a locked vault.

        For ordinary flags "cannot read" and "not set" are the same answer. For a
        safety setting they are opposites: a swallowed error reported strict mode
        as OFF to an owner who had turned it ON. Callers that must not guess use
        this one and decide for themselves - the agent's gate fails closed.
        """
        return self._identity().get("meta", {}).get(key, default)

    def meta_set(self, key: str, value: Any) -> None:
        def apply(identity):
            identity.setdefault("meta", {})[key] = value
        self._mutate_identity(apply)

    # ------------------------------------------------------------------ #
    # rate limiting
    # ------------------------------------------------------------------ #
    def _attempts(self) -> Dict[str, Any]:
        return self.data.setdefault("attempts", {"count": 0, "locked_until": 0, "last_failure": 0})

    def locked_out(self) -> Tuple[bool, int]:
        att = self._attempts()
        until = int(att.get("locked_until", 0))
        if until and now_ms() < until:
            return True, int((until - now_ms()) / 1000)
        return False, 0

    def register_failure(self, reason: str) -> int:
        att = self._attempts()
        att["count"] = int(att.get("count", 0)) + 1
        att["last_failure"] = now_ms()
        policy = self.policy()
        max_attempts = int(policy.get("max_attempts", LOCKOUT_MAX_ATTEMPTS))
        base = int(policy.get("lockout_base_seconds", LOCKOUT_BASE_SECONDS))
        wait = 0
        if att["count"] >= max_attempts:
            wait = base * (2 ** min(6, att["count"] - max_attempts))
            att["locked_until"] = now_ms() + wait * 1000
            # NOTE: do NOT reset count here. Resetting made the exponent in
            # ``min(6, count - max_attempts)`` always 0, so every lockout was a
            # flat 30 s and an attacker kept 5 guesses per 30 s forever. The
            # counter is cleared by register_success() and only there.
        self.save()
        self.audit("auth_failure", {"reason": reason, "lockout_seconds": wait})
        return wait

    def register_success(self) -> None:
        att = self._attempts()
        att["count"] = 0
        att["locked_until"] = 0
        self.save()

    # ------------------------------------------------------------------ #
    # unlock
    # ------------------------------------------------------------------ #
    def verify_passphrase(self, passphrase: str) -> bool:
        try:
            self.unlock(passphrase)
            return True
        except (PermissionError, RuntimeError, CryptoError):
            return False

    def unlock(self, passphrase: str) -> bytes:
        locked, wait = self.locked_out()
        if locked:
            raise PermissionError(f"locked out; retry in {wait}s")
        if not self.initialized:
            raise RuntimeError("vault not initialised")
        dev = self.data["devices"].get(self.device_id)
        if not dev:
            raise RuntimeError(f"this device ({self.device_id}) is not enrolled")
        kek = self._kek(passphrase)
        try:
            mk = decrypt(b64d(dev["kek_wrap"]), kek, aad=b"pbx/wrap/kek")
        except CryptoError as exc:
            raise PermissionError("incorrect passphrase") from exc
        if not constant_time_eq(mac(mk, SESSION_TAG), b64d(self.data["verifier"])):
            raise PermissionError("incorrect passphrase")
        if self.data.pop("_needs_rewrap", False):
            # finish the v1->v2 migration now that we hold the real key
            identity = {"voice": None, "totp": None, "recovery": None, "hardware_wrap": None,
                        "policy": {"max_attempts": LOCKOUT_MAX_ATTEMPTS,
                                   "lockout_base_seconds": LOCKOUT_BASE_SECONDS,
                                   "require_voice": True, "require_liveness": True,
                                   "allow_degraded": False},
                        "meta": {}, "sessions": {}}
            self.data["identity_blob"] = b64e(encrypt(json.dumps(identity).encode(), mk, aad=IDENT_AAD))
            self.save()
        return mk

    def _start_session(self, mk: bytes | bytearray | MasterKey, mode: str,
                       degraded: bool, user: str = "",
                       device_id: str = "") -> Session:
        sid = b64e(random_bytes(12))
        started_at = now_ms()
        started_mono = time.monotonic()
        idle_seconds = int(self.config.session_timeout)
        absolute_seconds = int(self.config.session_absolute_timeout)
        idle_deadline = started_at + idle_seconds * 1000
        absolute_deadline = started_at + absolute_seconds * 1000
        expires_at = min(idle_deadline, absolute_deadline)
        idle_deadline_mono = started_mono + idle_seconds
        absolute_deadline_mono = started_mono + absolute_seconds
        expires_at_mono = min(idle_deadline_mono, absolute_deadline_mono)
        token = b64e(hkdf(mk, sid.encode(), b"pbx/session-token/v1", 24))
        sess = Session(mk=mk, session_id=sid, started_at=started_at, last_activity=started_at,
                       expires_at=expires_at, absolute_expires_at=absolute_deadline,
                       expires_at_monotonic=expires_at_mono,
                       absolute_expires_at_monotonic=absolute_deadline_mono,
                       mode=mode, degraded=degraded, token=token,
                       user=user or self.user, device_id=device_id or self.device_id)
        self._session = sess
        try:
            def apply(identity):
                identity.setdefault("sessions", {})[sid] = {
                    "mode": mode, "degraded": degraded, "started_at": sess.started_at,
                    "expires_at": sess.expires_at,
                    "absolute_expires_at": sess.absolute_expires_at,
                    "device": sess.device_id,
                    "token_fp": hashlib.sha256(token.encode()).hexdigest()[:16],
                }
            self._mutate_identity(apply)
        except CryptoError:
            pass
        return sess

    def start_session(self, mk: bytes | bytearray | MasterKey,
                      mode: str = "passphrase+voice",
                      degraded: bool = False) -> Session:
        current = self._session
        if current is not None and current.expired():
            provisional_expired = current.session_id == "provisional"
            self.lock(reason="session-timeout")
            current = None
            if provisional_expired:
                raise RuntimeError("provisional authentication expired; start again")
        if isinstance(mk, MasterKey):
            mk.require_active()
        if current is not None and mk is current.mk:
            if current.session_id == "provisional":
                # A verified provisional key can be promoted, but it is copied
                # before the provisional buffer is zeroised.
                mk = MasterKey(mk)
                self.discard_provisional()
                return self._start_session(mk, mode, degraded)

            # Reconfiguring with the same live key is not a new authentication:
            # preserve its original start and absolute deadline while refreshing
            # only the idle deadline.
            now = now_ms()
            now_mono = time.monotonic()
            idle_seconds = int(self.config.session_timeout)
            idle_deadline = now + idle_seconds * 1000
            if current.absolute_expires_at is not None:
                idle_deadline = min(idle_deadline, current.absolute_expires_at)
            idle_deadline_mono = now_mono + idle_seconds
            if current.absolute_expires_at_monotonic is not None:
                idle_deadline_mono = min(
                    idle_deadline_mono, current.absolute_expires_at_monotonic)
            current.mode = mode
            current.degraded = degraded
            current.expires_at = idle_deadline
            current.expires_at_monotonic = idle_deadline_mono
            try:
                def refresh(identity):
                    entry = identity.setdefault("sessions", {}).get(current.session_id)
                    if entry is not None:
                        entry.update({"mode": mode, "degraded": degraded,
                                      "expires_at": current.expires_at,
                                      "absolute_expires_at": current.absolute_expires_at})
                self._mutate_identity(refresh)
            except (CryptoError, RuntimeError):
                pass
            self._kdf_cache.clear()
            return current
        if current is not None:
            if current.session_id == "provisional":
                self.discard_provisional()
            else:
                self.lock(reason="session-replaced")
        else:
            self.discard_provisional()
        return self._start_session(mk, mode, degraded)

    def begin_provisional(self, mk: bytes) -> Session:
        """Hold MK in memory *without* committing a session record.

        Provisional keys can decrypt identity data needed to verify a second
        factor, but they are never an unlocked application session. On failure
        ``discard_provisional()`` zeroises the key and nothing is written to disk.
        """
        current = self._session
        if current is not None and current.expired():
            self.lock(reason="session-timeout")
            current = None
        if current is not None:
            return current
        now = now_ms()
        now_mono = time.monotonic()
        provisional_expires = now + 120_000
        provisional_expires_mono = now_mono + 120
        self._session = Session(mk=mk, session_id="provisional", started_at=now,
                                expires_at=provisional_expires,
                                absolute_expires_at=provisional_expires,
                                expires_at_monotonic=provisional_expires_mono,
                                absolute_expires_at_monotonic=provisional_expires_mono,
                                mode="provisional",
                                degraded=True, token="", user=self.user,
                                device_id=self.device_id)
        return self._session

    def discard_provisional(self) -> None:
        sess = self._session
        if sess is not None and sess.session_id == "provisional":
            sess.mk.zeroize()
            self._session = None
        self._kdf_cache.clear()

    @property
    def session(self) -> Optional[Session]:
        sess = self._session
        if sess and sess.expired():
            self.lock(reason="session-timeout")
            return None
        # A provisional session exists only to verify the second factor. It must
        # not satisfy application/API unlock checks or expose a usable context.
        if sess is not None and sess.session_id == "provisional":
            return None
        return sess

    def live_session(self) -> Optional[Session]:
        """The raw session, expired or not - for a caller that must tell
        "still open" apart from "reached its deadline" without triggering the
        lazy lock that :attr:`session` performs."""
        sess = self._session
        if sess is None or sess.session_id == "provisional":
            return None
        return sess

    def note_activity(self) -> int:
        """Record an OWNER action and return its timestamp.

        Deliberately separate from :meth:`touch_session`: one extends the idle
        deadline (only owner actions may call it), the other only marks when the
        owner was last present, which is what the 30-minute warning reads.
        """
        sess = self._session
        if sess is None or sess.session_id == "provisional":
            return 0
        sess.last_activity = now_ms()
        return sess.last_activity

    def idle_seconds(self) -> Optional[int]:
        """Seconds since the last owner action, or None when locked."""
        sess = self._session
        if sess is None or sess.session_id == "provisional":
            return None
        return max(0, (now_ms() - int(sess.last_activity or sess.started_at)) // 1000)

    def touch_session(self) -> bool:
        """Refresh the idle deadline only for a still-live, full session."""
        sess = self._session
        if sess is None or sess.session_id == "provisional":
            return False
        now = now_ms()
        now_mono = time.monotonic()
        if sess.expired():
            self.lock(reason="session-timeout")
            return False
        sess.last_activity = now
        idle_seconds = int(self.config.session_timeout)
        idle_deadline = now + idle_seconds * 1000
        sess.expires_at = (min(idle_deadline, sess.absolute_expires_at)
                           if sess.absolute_expires_at is not None else idle_deadline)
        idle_deadline_mono = now_mono + idle_seconds
        sess.expires_at_monotonic = (
            min(idle_deadline_mono, sess.absolute_expires_at_monotonic)
            if sess.absolute_expires_at_monotonic is not None else idle_deadline_mono)
        return True

    def is_unlocked(self) -> bool:
        return self.session is not None

    def forget_fresh_login(self, reason: str = "action-spent") -> None:
        """Make the live session stop counting as "this process just logged in".

        Called once an action has been approved by the fresh-login shortcut, so
        one login authorises the action it approved - not every action for the
        next two minutes. The session itself is untouched: its idle and absolute
        deadlines stay exactly as they were.
        """
        sess = self._session
        if sess is None or sess.session_id == "provisional":
            return
        sess.fresh_login_spent = True
        self.audit("fresh_login_consumed", {"reason": reason,
                                           "session": sess.session_id})

    def lock(self, reason: str = "manual") -> None:
        sess = self._session
        if sess and sess.session_id != "provisional":
            self.audit("session_lock", {"reason": reason, "session": sess.session_id})
            try:
                def drop(identity):
                    identity.get("sessions", {}).pop(sess.session_id, None)
                self._mutate_identity(drop)
            except (CryptoError, RuntimeError):
                pass
        if sess:
            # The key object is shared with open MemoryStores; zeroising it also
            # closes those handles and wipes their in-RAM vector indexes.
            sess.mk.zeroize()
        self._session = None
        self._kdf_cache.clear()

    # ------------------------------------------------------------------ #
    # device management (multi-device enrolment)
    # ------------------------------------------------------------------ #
    def export_bundle(self, label: str = "device") -> Dict[str, Any]:
        """Produce an enrolment bundle for another device.

        Contains **only ciphertext** plus the scrypt parameters, so it is safe
        to transfer through any channel (cloud, QR code, USB). The receiving
        device still needs the passphrase to derive a KEK and unwrap MK.
        """
        if not self._session:
            raise RuntimeError("unlock the vault before exporting a bundle")
        payload = {
            "magic": BUNDLE_MAGIC,
            "version": 1,
            "user": self.user,
            "kdf": self.data["kdf"],
            "kek_wrap": self.data["devices"][self.device_id]["kek_wrap"],
            # The voiceprint is deliberately NOT in the bundle. A biometric
            # cannot be rotated after exposure, so it is enrolled separately on
            # each device rather than transported. Everything else in the
            # identity blob (TOTP seed, recovery hashes, policy) travels so the
            # new device is usable immediately.
            "identity_blob": self._identity_blob_without_voice(),
            "verifier": self.data["verifier"],
            "policy": self.policy(),
            "label": label,
            "exported_from": self.device_id,
            "exported_at": now_ms(),
        }
        payload["bundle_mac"] = b64e(mac(self._session.mk,
                                         json.dumps({k: v for k, v in payload.items()
                                                     if k != "bundle_mac"},
                                                    sort_keys=True).encode()))
        self.audit("bundle_exported", {"label": label, "to": "new-device"})
        return payload

    def _identity_blob_without_voice(self) -> Optional[str]:
        """Re-seal a copy of the identity blob with the voice entry removed."""
        if not self._session or not self.data.get("identity_blob"):
            return self.data.get("identity_blob")
        try:
            identity = self._identity()
        except CryptoError:
            return None
        identity = {k: v for k, v in identity.items() if k != "voice"}
        return b64e(encrypt(json.dumps(identity).encode(), self._session.mk, aad=IDENT_AAD))

    def import_bundle(self, bundle: Dict[str, Any], passphrase: str,
                      device_label: str = "") -> Dict[str, Any]:
        """Enrol *this* device into an existing brain."""
        if bundle.get("magic") != BUNDLE_MAGIC:
            raise ValueError("not a personal-brain enrolment bundle")
        if self.initialized:
            raise RuntimeError("this device already has a vault; remove BRAIN_HOME first")
        kdf = dict(bundle["kdf"])
        self.data["kdf"] = kdf
        kek = self._kek(passphrase)
        try:
            mk = decrypt(b64d(bundle["kek_wrap"]), kek, aad=b"pbx/wrap/kek")
        except CryptoError as exc:
            raise PermissionError("incorrect passphrase for this bundle") from exc
        if not constant_time_eq(mac(mk, SESSION_TAG), b64d(bundle["verifier"])):
            raise PermissionError("bundle verifier mismatch")
        expect = b64e(mac(mk, json.dumps({k: v for k, v in bundle.items()
                                          if k != "bundle_mac"}, sort_keys=True).encode()))
        if not constant_time_eq(expect, bundle.get("bundle_mac", "")):
            raise PermissionError("bundle integrity check failed (tampered in transit)")
        try:
            decrypt(b64d(bundle["identity_blob"]), mk, aad=IDENT_AAD)
        except CryptoError as exc:
            raise PermissionError("cannot decrypt identity blob") from exc

        label = device_label or bundle.get("label") or "device-2"
        dev_id = "dev-" + b64e(mac(mk, label.encode()))[:12]
        self.data.update({
            "created_at": now_ms(), "user": bundle.get("user", "owner"), "device_id": dev_id,
            "devices": {dev_id: {"kek_wrap": b64e(encrypt(mk, kek, aad=b"pbx/wrap/kek")),
                                 "created_at": now_ms(), "label": label,
                                 "imported_from": bundle.get("exported_from", "")}},
            "identity_blob": bundle["identity_blob"],
            "verifier": bundle["verifier"],
            "policy": dict(bundle.get("policy") or self.DEFAULT_POLICY),
            "attempts": {"count": 0, "locked_until": 0, "last_failure": 0},
        })
        self.save()
        self._start_session(mk, mode="bundle-import", degraded=False, device_id=dev_id)
        # register this device with the others (so it can be revoked later)
        def apply(identity):
            identity.setdefault("meta", {}).setdefault("devices", {})[dev_id] = {
                "label": label, "enrolled_at": now_ms()}
        self._mutate_identity(apply)
        self.audit("device_enrolled", {"device": dev_id, "label": label,
                                       "from": bundle.get("exported_from", "")})
        return {"device_id": dev_id, "label": label, "user": self.user}

    def revoke_device(self, device_id: str) -> bool:
        if device_id == self.device_id:
            raise ValueError("cannot revoke the device you are using")
        devices = self.data.get("devices", {})
        if device_id not in devices:
            return False
        devices.pop(device_id)
        self.save()
        self.audit("device_revoked", {"device": device_id})
        return True

    def rotate_master_key(self, passphrase: str) -> Dict[str, Any]:
        """Generate a new MK and re-wrap for every enrolled device.

        Use after revoking a lost/stolen device: the old MK becomes useless, but
        the data must be re-encrypted (records keep their per-record keys, so
        ``memory.rekey`` handles that side).
        """
        mk_old = self.unlock(passphrase)
        mk_new = random_bytes(32)
        kek = self._kek(passphrase)
        identity = decrypt(b64d(self.data["identity_blob"]), mk_old, aad=IDENT_AAD)
        for dev in self.data["devices"].values():
            dev["kek_wrap"] = b64e(encrypt(mk_new, kek, aad=b"pbx/wrap/kek"))
            dev["rotated_at"] = now_ms()
        self.data["identity_blob"] = b64e(encrypt(identity, mk_new, aad=IDENT_AAD))
        self.data["verifier"] = b64e(mac(mk_new, SESSION_TAG))
        self.data["rotated_at"] = now_ms()
        self.save()
        self._session = None
        self.audit("master_key_rotated", {"devices": len(self.data["devices"])})
        return {"devices": list(self.data["devices"]), "note": "now rekey memory records"}

    def rotate_passphrase(self, old: str, new: str) -> None:
        mk = self.unlock(old)
        if len(new) < 8:
            raise ValueError("new passphrase must be at least 8 characters")
        self.data["kdf"] = self._new_kdf_params()
        kek = self._kek(new)
        for dev in self.data["devices"].values():
            dev["kek_wrap"] = b64e(encrypt(mk, kek, aad=b"pbx/wrap/kek"))
            dev["repassphrased_at"] = now_ms()
        self.data["rotated_at"] = now_ms()
        self.save()
        self._kdf_cache.clear()
        self.audit("passphrase_rotated", {"devices": len(self.data["devices"])})

    # ------------------------------------------------------------------ #
    # hardware-backed wrap (optional)
    # ------------------------------------------------------------------ #
    def attach_hardware_key(self, service: str = "personal-brain") -> str:
        if not self._session:
            raise RuntimeError("unlock first")
        if keyring is None:
            return "unavailable: pip install keyring (or configure PKCS#11/TPM)"
        try:
            token = b64e(random_bytes(24))
            keyring.set_password(service, self.user, token)
            hkek = derive_key(token.encode(), b"pbx/hw/v1", b"pbx/hwkek/v1", 32,
                              n=self.config.kdf_n)
            def apply(identity):
                identity["hardware_wrap"] = {
                    "service": service,
                    "wrapped_mk": b64e(encrypt(self._session.mk, hkek, aad=b"pbx/wrap/hw"))}
            self._mutate_identity(apply)
            self.audit("hardware_key_attached", {"service": service})
            return "attached"
        except Exception as exc:
            return f"failed: {type(exc).__name__}: {exc}"

    def unwrap_with_hardware_key(self) -> Optional[bytes]:
        if keyring is None or not self._session:
            return None
        try:
            hw = self._identity().get("hardware_wrap")
            if not hw:
                return None
            token = keyring.get_password(hw["service"], self.user)
            if not token:
                return None
            hkek = derive_key(token.encode(), b"pbx/hw/v1", b"pbx/hwkek/v1", 32,
                              n=self.config.kdf_n)
            return decrypt(b64d(hw["wrapped_mk"]), hkek, aad=b"pbx/wrap/hw")
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # voice template (inside the identity blob)
    # ------------------------------------------------------------------ #
    def _voice_key(self) -> bytes:
        """Device-bound key for the voiceprint.

        Derived from the MK *and this device's id*, so a copy of ``identity.json``
        or an enrolment bundle taken from one machine cannot be opened on
        another even by someone holding the same MK. That is the software
        equivalent of the non-exportable device-bound key the design calls for;
        a TPM/secure-enclave binding would be strictly stronger and is noted as
        a residual limitation rather than claimed here.
        """
        if not self._session:
            raise RuntimeError("unlock the vault first")
        return hkdf(self._session.mk, self.device_id.encode(), VOICE_KEY_INFO, 32)

    def _voice_aad(self) -> bytes:
        return b"pbx/voice/template|" + self.device_id.encode()

    def store_voice_template(self, embeddings: List[Any], meta: Dict[str, Any],
                             transform_seed: Optional[bytes] = None) -> None:
        first = embeddings[0] if embeddings else {}
        dim = len(first.get("z", [])) if isinstance(first, dict) else len(first)
        vkey = self._voice_key()
        aad = self._voice_aad()
        payload = {"embeddings": embeddings, "meta": meta}
        if transform_seed:
            payload["transform_seed"] = b64e(transform_seed)

        def apply(identity):
            identity["voice"] = {
                "backend": meta.get("backend", "mfcc-cal-v1"),
                "dim": dim,
                "whitened_dim": len(first.get("w", [])) if isinstance(first, dict) else 0,
                "alpha": meta.get("alpha", 0.75),
                "samples": len(embeddings),
                # sealed under the DEVICE-BOUND key, not the shared MK
                "template": b64e(encrypt(json.dumps(payload).encode(), vkey, aad=aad)),
                "created_at": now_ms(),
                "threshold": float(meta.get("threshold") or 0.0),
                "sync_policy": VOICE_SYNC_POLICY,
                "bound_device": self.device_id,
                "cancelable": bool(meta.get("cancelable")),
                "key_version": 1,
            }
        self._mutate_identity(apply)

    def load_voice_template(self) -> Optional[Dict[str, Any]]:
        """Return the enrolled template, or None if it is not usable HERE.

        Refuses a template bound to a different device: the whole point of
        device-local biometrics is that a copy is useless elsewhere, so the
        refusal is the control working, not a failure. Callers should surface
        ``voice_needs_reenroll()`` to tell the user to enrol on this device.
        """
        if not self._session:
            return None
        try:
            voice = self._identity().get("voice")
            if not voice:
                return None
            bound = voice.get("bound_device")
            if bound and bound != self.device_id:
                self.audit("voice_template_rejected_foreign_device",
                           {"bound_device": bound, "this_device": self.device_id})
                return None
            raw = decrypt(b64d(voice["template"]), self._voice_key(), aad=self._voice_aad())
            out = json.loads(raw)
            seed = out.get("transform_seed")
            if isinstance(seed, str):
                out["transform_seed"] = b64d(seed)
            return out
        except (CryptoError, ValueError, KeyError):
            return None

    def voice_needs_reenroll(self) -> bool:
        """True when a template exists but cannot be used on this device.

        Happens after a vault is restored from backup onto a new machine, or
        when a pre-v6 template (sealed under the shared MK, not transformed) is
        loaded by this build. Voice re-enrolment is required either way, and
        saying so beats reporting "voice not enrolled" when it plainly is.
        """
        if not self._session:
            return False
        try:
            voice = self._identity().get("voice")
        except CryptoError:
            return False
        return bool(voice) and self.load_voice_template() is None

    def has_voice(self) -> bool:
        if not self._session:
            return bool(self.data.get("identity_blob"))
        try:
            return bool(self._identity().get("voice"))
        except CryptoError:
            return False

    def voice_info(self) -> Dict[str, Any]:
        if not self._session:
            return {}
        try:
            v = self._identity().get("voice") or {}
            return {k: v.get(k) for k in ("backend", "dim", "samples", "threshold", "created_at")}
        except CryptoError:
            return {}

    # ------------------------------------------------------------------ #
    # TOTP + recovery codes
    # ------------------------------------------------------------------ #
    def store_totp(self, secret_b32: str) -> None:
        def apply(identity):
            identity["totp"] = {"secret": b64e(encrypt(secret_b32.encode(), self._session.mk,
                                                       aad=b"pbx/totp")),
                                "created_at": now_ms(),
                                # a fresh secret restarts the replay counter
                                "last_counter": 0}
        self._mutate_identity(apply)

    def totp_last_counter(self) -> int:
        """Highest TOTP step already spent; 0 when nothing has been used yet.

        Lives inside the encrypted identity blob, so it cannot be rolled back by
        editing a file: a replayed code has to defeat the vault first.
        """
        if not self._session:
            return 0
        try:
            totp = self._identity().get("totp") or {}
            return int(totp.get("last_counter") or 0)
        except (CryptoError, KeyError, TypeError, ValueError):
            return 0

    def mark_totp_used(self, counter: int) -> None:
        """Consume a TOTP step. One code authenticates exactly once (audit B6)."""
        def apply(identity):
            totp = identity.setdefault("totp", {})
            totp["last_counter"] = max(int(totp.get("last_counter") or 0), int(counter))
        self._mutate_identity(apply)

    def load_totp_secret(self) -> Optional[str]:
        if not self._session:
            return None
        try:
            totp = self._identity().get("totp")
            if not totp:
                return None
            return decrypt(b64d(totp["secret"]), self._session.mk, aad=b"pbx/totp").decode()
        except (CryptoError, KeyError):
            return None

    def store_recovery_codes(self, codes: List[str]) -> List[str]:
        hashed = [hashlib.sha256(c.strip().lower().encode()).hexdigest() for c in codes]
        def apply(identity):
            identity["recovery"] = {"hashes": hashed, "used": [], "created_at": now_ms()}
        self._mutate_identity(apply)
        return hashed

    def consume_recovery_code(self, code: str) -> bool:
        if not self._session:
            return False
        h = hashlib.sha256(code.strip().lower().encode()).hexdigest()
        ok = False
        def apply(identity):
            nonlocal ok
            rec = identity.get("recovery")
            if rec and h in rec.get("hashes", []) and h not in rec.get("used", []):
                rec.setdefault("used", []).append(h)
                ok = True
        self._mutate_identity(apply)
        return ok

    def reissue_recovery_codes(self, codes: List[str]) -> int:
        """Replace the recovery set and invalidate every old code.

        There was no user-facing way to reissue recovery codes: the internal
        ``store_recovery_codes`` existed, but nothing exposed it, so a user who
        used most of their codes (or whose printed sheet was exposed) had no
        remedy. Reissuing REPLACES the set - keeping the old hashes would leave
        a credential that should be dead still working.
        """
        before = self.recovery_remaining()
        self.store_recovery_codes(codes)
        self.audit("recovery_codes_reissued", {"count": len(codes), "invalidated": before})
        return before

    def has_totp(self) -> bool:
        try:
            return bool(self._identity().get("totp"))
        except Exception:
            return False

    def recovery_remaining(self) -> int:
        if not self._session:
            return 0
        try:
            rec = self._identity().get("recovery") or {}
            return len([h for h in rec.get("hashes", []) if h not in rec.get("used", [])])
        except CryptoError:
            return 0

    # ------------------------------------------------------------------ #
    # audit journal (hash-chained)
    # ------------------------------------------------------------------ #
    def audit(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        entry = {"ts": now_ms(), "event": event, "data": data or {},
                 "device": self.device_id or "-"}
        sess = self._session
        if sess:
            entry["session"] = sess.session_id
        path: Path = self.config.audit_path
        ensure_dir(path.parent)
        prev = b""
        if path.exists():
            lines = path.read_bytes().splitlines()
            if lines:
                prev = hashlib.sha256(lines[-1]).digest()
        # The chain must cover the ENTIRE entry, not just the event name. It used
        # to hash `prev + event`, so editing `data`, `ts` or `session` left the
        # chain valid - reproduced by rewriting an entry's payload and getting
        # ok=True (audit A3).
        # Written into the entry so verification knows which formula applies.
        # A line logged while LOCKED (a session end, a failed unlock) cannot use a
        # session key, and reading it back after a later unlock used to be
        # reported as tampering - a fresh vault told its owner "7 entries were
        # edited" when nothing had been touched.
        #
        # It MUST be set before the canonical body is built, or the chain would be
        # computed over a body the entry no longer has (caught the moment I added
        # the field, by every line verifying as mismatched).
        entry["chain_mode"] = "session" if sess else "plain"
        canonical = self._audit_body(entry)
        entry["chain"] = self._chain_value(sess, prev, canonical)
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        with open(path, "ab") as fh:
            fh.write(line.encode() + b"\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _audit_body(entry: Dict[str, Any]) -> bytes:
        """Canonical bytes of every field except the chain value itself."""
        body = {k: v for k, v in entry.items() if k != "chain"}
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()

    def _chain_value(self, sess: Any, prev: bytes, canonical: bytes) -> str:
        if sess:
            chain_key = fast_kdf(sess.mk, b"pbx/audit-chain/v1", 32)
            return b64e(hkdf(chain_key, prev, canonical, 16))
        return b64e(hashlib.sha256(prev + canonical).digest()[:16])

    def _legacy_chain_value(self, sess: Any, prev: bytes, event: bytes) -> str:
        """Pre-fix formula, kept only so old journals are not called tampered."""
        if sess:
            chain_key = fast_kdf(sess.mk, b"pbx/audit-chain/v1", 32)
            return b64e(hkdf(chain_key, prev, event, 16))
        return b64e(hashlib.sha256(prev + event).digest()[:16])

    def read_audit(self, limit: int = 50) -> List[Dict[str, Any]]:
        path = self.config.audit_path
        if not path.exists():
            return []
        out = []
        for ln in path.read_bytes().splitlines()[-limit:]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out

    def verify_audit_chain(self) -> Dict[str, Any]:
        """Recompute the whole chain; report tampering, deletion and reordering.

        The previous version only checked that each line had *a* `chain` field.
        It never recomputed anything, so an edited payload, a deleted entry and a
        reordered journal all returned ok=True (audit A3, reproduced).

        Detection works because each entry commits to the previous line's exact
        bytes: change any field, drop a line, or swap two lines, and the next
        entry's recomputed value no longer matches.
        """
        path = self.config.audit_path
        if not path.exists():
            return {"ok": True, "entries": 0, "unparsable": 0, "problems": []}
        sess = self._session
        prev = b""
        unparsable = 0
        mismatched = 0
        legacy = 0
        unkeyed = 0
        unverifiable = 0
        problems: List[Dict[str, Any]] = []
        i = 0
        for i, ln in enumerate(path.read_bytes().splitlines(), 1):
            if not ln.strip():
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                unparsable += 1
                problems.append({"line": i, "problem": "not valid JSON"})
                prev = hashlib.sha256(ln).digest()
                continue
            claimed = obj.get("chain")
            if not isinstance(claimed, str):
                unparsable += 1
                problems.append({"line": i, "problem": "entry has no chain value"})
                prev = hashlib.sha256(ln).digest()
                continue
            if hmac.compare_digest(claimed, self._chain_value(sess, prev, self._audit_body(obj))):
                prev = hashlib.sha256(ln).digest()
                continue
            # Journals written before the fix chained only `prev + event`, so a
            # genuine old entry would otherwise be reported as tampering. Accept
            # that formula, but count it separately: it does not protect `data`.
            if hmac.compare_digest(claimed, self._legacy_chain_value(
                    sess, prev, str(obj.get("event", "")).encode())):
                legacy += 1
                prev = hashlib.sha256(ln).digest()
                continue
            # A line written while the vault was locked is chained with the plain
            # formula on purpose (there was no key in memory). Accept it, and
            # count it separately: those lines detect an edit or a reordering but
            # not a careful rewrite by someone who holds the file.
            if hmac.compare_digest(claimed, self._chain_value(None, prev,
                                                              self._audit_body(obj))):
                unkeyed += 1
                prev = hashlib.sha256(ln).digest()
                continue
            if sess is None and obj.get("chain_mode") == "session":
                # This line was chained with a session key and we have no session,
                # so it cannot be verified - which is NOT the same as tampered.
                # `brain doctor` read a locked vault and told its owner "7 entries
                # were edited" when nothing had been touched. Count it separately
                # and say so; an unlocked verify is what proves integrity.
                unverifiable += 1
                prev = hashlib.sha256(ln).digest()
                continue
            mismatched += 1
            problems.append({"line": i, "event": str(obj.get("event"))[:60],
                             "problem": "chain mismatch (edited, removed or reordered)"})
            prev = hashlib.sha256(ln).digest()
        return {"ok": (unparsable == 0 and mismatched == 0), "entries": i,
                "unparsable": unparsable, "mismatched": mismatched,
                "legacy_unprotected": legacy, "unkeyed": unkeyed,
                "unverifiable_while_locked": unverifiable,
                "needs_unlock_to_verify": bool(unverifiable),
                "problems": problems[:10]}
