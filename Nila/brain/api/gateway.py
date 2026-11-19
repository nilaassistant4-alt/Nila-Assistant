"""Localhost JSON API - the bridge a voice/phone/GUI front-end plugs into.

The MVP interaction is text-first, but the *same* ceremony and tool surface are
exposed over HTTP so a voice client can drive it:

    POST /v1/auth/challenge          -> {nonce, text, bound, expires_at}
    POST /v1/auth/verify             -> {nonce, audio(base64 wav), bound, passphrase}
    GET  /v1/status
    POST /v1/chat                    {message}
    GET  /v1/memory/search?q=...
    POST /v1/memory                  {text, kind, tags}
    GET  /v1/instructions            standing rules (owner only, #2)
    GET  /v1/connectors              AI + outside services, never a secret (#3)
    POST /v1/connectors/authorise    one fresh login for ONE action (#3)
    POST /v1/sync
    POST /v1/lock

Security posture
----------------
* Binds to **loopback only**. serve() refuses any other host outright (there is no
  override flag) because the transport is plain HTTP and the token would cross the
  network; put a TLS reverse proxy in front if you ever need remote access.
* ``GET /`` serves the same-origin dashboard (no inline script/style, strict CSP); a
  request whose Host header is not loopback is refused (DNS-rebinding guard).
* Dashboard routes: GET /v1/metrics, GET /v1/files (allowlisted folder NAMES only),
  GET /v1/consent and POST /v1/consent/affirm|deny (single-use, full session only),
  GET /v1/connectors plus the connector writes (each needs a single-use approval).
* Every request needs ``Authorization: Bearer <token>`` where the token is
  either generated at startup (printed once) or the unlocked session token.
* ``/v1/auth/verify`` is the only endpoint that accepts a passphrase, and it is
  rate limited by the vault's lockout policy like every other ceremony.
* Nothing is logged except the audit journal, which stores no audio and no
  plaintext bodies.
* The server holds the unlocked master key in memory only; ``/v1/lock``
  zeroises it.
"""
from __future__ import annotations

import base64
import json
import hmac
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from ..agent.agent import Brain
from ..agent.host_tools import forbidden_reason as _forbidden
from . import dashboard
from .. import diagnostics
from pathlib import Path

from ..config import Config
from ..agent.connectors import AuthLevel
from ..identity.ceremony import CeremonyError, IdentityCeremony
from ..memory.store import MemoryStore
from ..memory.vault import Vault
from ..util.misc import now_ms

MAX_BODY = 12 * 1024 * 1024     # 12 MB: enough for a WAV challenge


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


_ANSI = __import__("re").compile(r"\x1b\[[0-9;]*m")


def _web_text(text: str) -> str:
    """The brain writes for a terminal (colours, ``brain permission affirm ...``).
    In the browser the same step is a button, so say that instead."""
    text = _ANSI.sub("", text or "")
    out, hinted = [], False
    for line in text.split("\n"):
        st = line.strip()
        if st.startswith(("Allow it once:", "Or allow it standing:")):
            if not hinted:
                out.append("  → Press “Allow once” in the Approvals box on the right.")
                hinted = True
            continue
        out.append(line)
    return "\n".join(out)


class Gateway:
    def __init__(self, config: Config, vault: Vault, memory: MemoryStore,
                 token: Optional[str] = None):
        self.config = config
        self.vault = vault
        self.memory = memory
        self.brain = Brain(config, vault, memory)
        self.ceremony = IdentityCeremony(config, vault)
        self.token = token or secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.chat_lock = threading.Lock()      # one conversation turn at a time
        self.verify_attempts: list = []

    # ------------------------------------------------------------------ #
    def _rate_limit_verify(self) -> None:
        cutoff = now_ms() - 60_000
        with self.lock:
            self.verify_attempts = [t for t in self.verify_attempts if t > cutoff]
            if len(self.verify_attempts) >= 12:
                raise ApiError(429, "too many verify attempts; wait a minute")
            self.verify_attempts.append(now_ms())

    #: How long a session may sit at the 30-minute warning before the 60-minute
    #: lock, expressed as the config values rather than a second constant.
    LOCK_MARGIN_SECONDS = 60

    def _idle_lock_check(self) -> None:
        """Lock an idle session, and say why.

        ``Vault.session`` already refuses an EXPIRED session, so this is not the
        safety net for expiry - it is the visible rule: 30 minutes without an
        owner action warns, and once the idle deadline passes the vault is
        locked and the reason is in the journal, not just in a 423 response.
        """
        # NOT ``self.vault.session``: that property locks and returns None the
        # moment the deadline passes, so by the time it answered "expired" the
        # checkpoint below had already been skipped - the feature did nothing.
        sess = self.vault.live_session()
        if sess is None:
            return
        idle = self.vault.idle_seconds() or 0
        if idle >= int(self.config.session_timeout):
            # "Save project state, then lock": flush what is open before the key
            # goes away, so nothing typed in the last hour is lost to the lock.
            checkpoint = self._checkpoint()
            self.vault.audit("session_idle_lock", {"idle_s": idle,
                                                   "timeout_s": int(self.config.session_timeout),
                                                   "checkpoint": checkpoint})
            self.vault.lock(reason="idle-timeout")

    def _checkpoint(self) -> Dict[str, Any]:
        """Flush what is open, before an idle lock drops the keys.

        Projects and memories already commit per write, so this is a belt-and-
        braces flush plus a note of what was open. It must never raise: a lock
        that fails is worse than a checkpoint that fails.
        """
        out: Dict[str, Any] = {}
        try:
            conn = getattr(self.memory, "conn", None)
            if conn is not None:
                conn.commit()
            out["memory"] = "flushed"
        except Exception as exc:                      # pragma: no cover - defensive
            out["memory"] = f"failed: {type(exc).__name__}"
        try:
            from ..projects.store import ProjectStore

            open_rows = ProjectStore(self.memory).list()
            out["projects_open"] = len(open_rows)
        except Exception:
            out["projects_open"] = 0
        return out

    def _touch_user_activity(self):
        if not self.vault.touch_session():
            raise ApiError(423, "session expired; authenticate again")
        sess = self.vault.session
        if sess is None:
            raise ApiError(423, "session expired; authenticate again")
        return sess

    def _reopen_memory_for_session(self) -> None:
        """Bind a fresh store/index to the newly authenticated session key."""
        from ..security.classify import session_tier

        sess = self.vault.session
        if sess is None:
            raise RuntimeError("authentication session expired before memory reopened")
        old = self.memory
        old.close()
        fresh = MemoryStore(self.config, sess.mk, access_level=session_tier(sess),
                            session=sess)
        self.memory = fresh
        self.brain.memory = fresh
        self.brain.integrations.memory = fresh
        # Do not carry decrypted conversation context across session boundaries.
        self.brain.history.clear()

    # ------------------------------------------------------------------ #
    def _projects(self, method: str, path: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        import base64
        import binascii
        from urllib.parse import unquote

        from ..projects.files import FileStore, FileStoreError
        from ..projects.store import ProjectError, ProjectStore

        self._touch_user_activity()
        files = FileStore(self.config.home / "files", lambda: self.vault.session.mk)
        st = ProjectStore(self.memory, files)
        parts = [unquote(p) for p in path[len("/v1/projects"):].split("/") if p]
        try:
            if not parts:
                if method == "GET":
                    return 200, {"ok": True, "projects": st.list()}
                if method == "POST":
                    return 201, {"ok": True, "project": st.create(str(body.get("title", "")),
                                                                  str(body.get("idea", "")))}
            elif parts == ["resume"] and method == "GET":
                return 200, {"ok": True, "resume": st.resume()}
            elif parts == ["late"] and method == "GET":
                # late milestones first: the owner asked for these to be put in
                # front of him, not found by scrolling
                late = st.late_milestones()
                return 200, {"ok": True, "late": late, "count": len(late)}
            else:
                ref = parts[0]
                action = parts[1] if len(parts) > 1 else ""
                if not action and method == "GET":
                    return 200, {"ok": True, "project": st.get(ref), "report": st.report(ref)}
                if method == "POST":
                    if action == "note":
                        return 200, {"ok": True, "project": st.add_note(ref, str(body.get("text", "")))}
                    if action == "status":
                        return 200, {"ok": True, "project": st.set_status(ref, str(body.get("status", "")))}
                    if action == "canvas":
                        return 200, {"ok": True, "project": st.set_canvas(
                            ref, str(body.get("block", "")), str(body.get("text", "")))}
                    if action == "milestone":
                        return 200, {"ok": True, "project": st.add_milestone(
                            ref, str(body.get("text", "")), str(body.get("due", "")))}
                    if action == "interview":
                        return 200, {"ok": True, "question": st.interview(ref)}
                    if action == "export":
                        self.vault.audit("project_exported", {"project": ref[:40]})
                        return 200, {"ok": True, "file": st.export_summary(
                            ref, str(body.get("folder", "")))}
                    if action == "complete":
                        return 200, {"ok": True, "project": st.complete_milestone(
                            ref, int(body.get("index", 0)))}
                    if action == "files":
                        try:
                            raw = base64.b64decode(str(body.get("data_b64", "")), validate=True)
                        except (binascii.Error, ValueError) as exc:
                            raise ApiError(400, "data_b64 is not valid base64") from exc
                        meta = st.attach_file(ref, str(body.get("name", "file")), raw)
                        self.vault.audit("project_file_added", {"size": meta["size"]})
                        return 201, {"ok": True, "file": meta}
                if method == "GET" and action == "files" and len(parts) == 3:
                    got = st.read_file(ref, parts[2])
                    return 200, {"ok": True, "name": got["meta"]["name"],
                                 "data_b64": base64.b64encode(got["data"]).decode()}
        except (ProjectError, FileStoreError) as exc:
            raise ApiError(400, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise ApiError(400, f"bad request: {exc}") from exc
        raise ApiError(404, "unknown project route")

    # ------------------------------------------------------------------ #
    # connectors (#3)
    #
    # Read first: GET /v1/connectors answers the whole screen. It never returns
    # a secret - only has_key, the last four characters and a fingerprint.
    #
    # Two-step write: POST /v1/connectors/authorise asks for the passphrase (and
    # the authenticator code), and the approval it creates is bound to this
    # session and to ONE action. The next call spends it. A dashboard session is
    # long-lived, so it can never skip this by "just having logged in".
    # ------------------------------------------------------------------ #
    def _connectors(self, method: str, path: str, body: Dict[str, Any]
                    ) -> Tuple[int, Dict[str, Any]]:
        from ..agent.connectors import ConnectorError, ConnectorRegistry, StepUpRequired

        self._touch_user_activity()
        reg = ConnectorRegistry(self.config, self.vault, self.brain, self.memory)
        tail = path[len("/v1/connectors"):].strip("/")

        def refusal(exc: Exception) -> Tuple[int, Dict[str, Any]]:
            if isinstance(exc, StepUpRequired):
                self.vault.audit("connector_refused",
                                 {"route": tail or "list", "needs": exc.needs,
                                  "action": exc.action})
                how = list(exc.actions)
                # "unlock again with your authenticator" is useless advice to
                # someone who never enrolled one: say what to do instead.
                if exc.needs == "authenticator" and not self.vault.has_totp():
                    how = ["you have no authenticator enrolled - run `brain totp` once "
                           "(adds a 6-digit code app), then unlock with that code",
                           "or unlock with your recovery code and set the key in that session"]
                return 403, {"ok": False, "error": str(exc), "needs": exc.needs,
                             "action": exc.action, "how": how,
                             "has_totp": self.vault.has_totp()}
            self.vault.audit("connector_refused", {"route": tail or "list",
                                                   "reason": str(exc)[:120]})
            if "unknown connector" in str(exc):
                return 404, {"ok": False, "error": str(exc)}
            if "locked" in str(exc).lower():
                return 423, {"ok": False, "error": str(exc)}
            return 400, {"ok": False, "error": str(exc)}

        try:
            if not tail and method == "GET":
                out = reg.status()
                out["has_totp"] = self.vault.has_totp()
                return 200, out
            if tail == "authorise" and method == "POST":
                out = reg.authorise(str(body.get("action", "")),
                                    str(body.get("passphrase", "")),
                                    str(body.get("totp", "") or body.get("totp_code", "")))
                return 200, out
            if tail.endswith("/enable") or tail.endswith("/disable"):
                cid = tail.rsplit("/", 1)[0]
                enabled = tail.endswith("/enable")
                if method != "POST":
                    raise ApiError(405, "use POST")
                row = reg.set_enabled(cid, enabled)
                return 200, {"ok": True, "connector": row}
            if tail == "switch" and method == "POST":
                return 200, reg.set_on(str(body.get("id", "")), bool(body.get("on")),
                                       approved=True)
            if tail == "key" and method == "POST":
                cid = str(body.get("id", ""))
                if not AuthLevel.at_least(reg.status()["auth_level"], AuthLevel.TOTP):
                    raise StepUpRequired("a key needs an authenticator-verified session",
                                         ["unlock again with your authenticator code"],
                                         action=f"connector:{cid}:key:set",
                                         needs="authenticator")
                self.vault.audit("connector_key_attempt", {"connector": cid})
                return 200, reg.set_key(cid, str(body.get("key", "")), approved=True)
            if tail == "key" and method == "DELETE":
                return 200, reg.remove_key(str(body.get("id", "")), approved=True)
            if tail == "test" and method == "POST":
                return 200, reg.test(str(body.get("id", "")), approved=True)
        except StepUpRequired as exc:
            return refusal(exc)
        except ConnectorError as exc:
            return refusal(exc)
        raise ApiError(404, "unknown connector route")

    # ------------------------------------------------------------------ #
    def route(self, method: str, path: str, query: Dict[str, Any],
              body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        # Every authenticated request, /v1/status included: the dashboard poll is
        # the clock that notices the deadline, so the checkpoint and the journal
        # entry must happen here and not only on a later chat message.
        self._idle_lock_check()

        if path == "/health":
            return 200, {"ok": True, "version": __import__("brain").__version__,
                         "unlocked": self.vault.is_unlocked()}

        if path == "/v1/status":
            payload = {"initialized": self.vault.initialized, "unlocked": self.vault.is_unlocked(),
                       "user": self.vault.user, "device_id": self.vault.device_id,
                       "voice_enrolled": bool((self.vault.data.get("voice") or {}).get("template")),
                       "provider": self.brain.provider.name if self.brain.provider.available() else "local"}
            sess = self.vault.session
            # A locked vault still reports the rule (warn/lock minutes) so the
            # screen can explain what happened; `active` says whether a session
            # is really there. Two different shapes here made the banner vanish
            # at the exact moment it mattered.
            payload["session"] = {
                "active": False, "mode": None, "degraded": False,
                "idle_s": None, "warn_after_s": self.config.warn_after,
                "lock_after_s": int(self.config.session_timeout),
                "idle_warning": False, "expires_in_s": None,
                "absolute_expires_in_s": None,
            }
            if sess is not None:
                payload["session"].update({
                    "active": True,
                    "mode": sess.mode, "degraded": sess.degraded,
                    "idle_s": self.vault.idle_seconds(),
                    "warn_after_s": self.config.warn_after,
                    "idle_warning": (self.vault.idle_seconds() or 0) >= self.config.warn_after,
                    "expires_at": sess.expires_at,
                    "absolute_expires_at": sess.absolute_expires_at,
                    "expires_in_s": sess.seconds_remaining(),
                    "absolute_expires_in_s": sess.seconds_remaining(absolute=True),
                })
                payload["memory"] = self.memory.stats()
            return 200, payload

        if path == "/v1/auth/challenge" and method == "POST":
            try:
                ch = self.ceremony.issue_challenge(style=str(body.get("style", "auto")))
            except CeremonyError as exc:
                raise ApiError(409, str(exc)) from exc
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
            return 200, ch

        if path == "/v1/auth/verify" and method == "POST":
            self._rate_limit_verify()
            passphrase = str(body.get("passphrase", ""))
            if not passphrase:
                raise ApiError(400, "passphrase required")
            submission = None
            if body.get("audio"):
                audio = body["audio"]
                if isinstance(audio, str) and not audio.startswith("data:"):
                    audio = "data:audio/wav;base64," + audio
                submission = {"nonce": str(body.get("nonce", "")), "audio": audio,
                              "bound": body.get("bound")}
            mode = "voice" if submission else ("totp" if body.get("totp") else "auto")
            # A session that is still open must be locked (not replaced) first:
            # swapping it would invalidate the dashboard's bearer token mid-request.
            if self.vault.session is not None:
                raise ApiError(409, "already unlocked; POST /v1/lock before unlocking again")
            # The browser must keep a still-open session's token; a lock request
            # from the same tab needs it after the vault has locked itself.
            with self.chat_lock:
                result = self.ceremony.unlock(passphrase, mode=mode, voice_submission=submission,
                                              totp_code=body.get("totp"),
                                              recovery_code=body.get("recovery"))
                if not result.ok:
                    raise ApiError(401, "; ".join(result.reasons) or "authentication failed")
                try:
                    self._reopen_memory_for_session()
                except Exception as exc:
                    self.vault.lock(reason="api-memory-reopen-failed")
                    raise ApiError(500, "could not open memory for the new session") from exc
            sess = result.session
            return 200, {"ok": True, "mode": result.mode, "degraded": sess.degraded,
                         "session_token": sess.token, "expires_at": sess.expires_at,
                         "absolute_expires_at": sess.absolute_expires_at,
                         "detail": {k: v for k, v in result.detail.items() if k != "voice"} |
                                   {"voice_score": result.detail.get("voice", {}).get("score")}}

        if path == "/v1/session/touch" and method == "POST":
            # Only an explicit owner click may extend the idle window. The
            # dashboard's 2 s poll never calls this: an auto-refresh is not
            # somebody sitting at the keyboard.
            if not self.vault.touch_session():
                raise ApiError(423, "session expired; authenticate again")
            self.vault.audit("session_touched", {"via": "dashboard-button"})
            return 200, {"ok": True, "idle_s": self.vault.idle_seconds(),
                         "expires_in_s": self.vault.session.seconds_remaining()
                         if self.vault.session else 0}

        if path == "/v1/lock" and method == "POST":
            with self.chat_lock:
                self.vault.lock(reason="api")
                self.brain.history.clear()
            return 200, {"ok": True, "locked": True}

        # metadata-only routes: they expose no plaintext, so a locked vault is fine
        if path == "/v1/tools" and method == "GET":
            return 200, {"ok": True, "unlocked": self.vault.is_unlocked(), "tools": [
                {"name": s.name, "risk": s.risk, "description": s.description,
                 "parameters": s.parameters} for s in self.brain.registry.specs()]}

        if path == "/v1/audit" and method == "GET":
            return 200, {"ok": True, "integrity": self.vault.verify_audit_chain(),
                         "entries": self.vault.read_audit(int(query.get("limit", 25)))}

        if path == "/v1/metrics" and method == "GET":
            return 200, {"ok": True, "metrics": dashboard.metrics()}

        # Everything below requires an unlocked session.
        if not self.vault.is_unlocked():
            raise ApiError(423, "vault is locked; POST /v1/auth/verify first")

        if path == "/v1/chat" and method == "POST":
            message = str(body.get("message", "")).strip()
            if not message:
                raise ApiError(400, "message required")
            mode = str(body.get("mode") or "").strip().lower() or None
            if len(message) > 8000:
                raise ApiError(400, "message is longer than 8000 characters")
            with self.chat_lock:
                turn = self.brain.handle(message, mode=mode)
            out = turn.to_dict()
            out["answer"] = _web_text(out.get("answer", ""))
            return 200, out

        if path == "/v1/capabilities" and method == "GET":
            return 200, {"ok": True, "capabilities": diagnostics.capabilities(self.brain)}

        if path == "/v1/diagnostics" and method == "GET":
            rep = diagnostics.report(self.brain)
            if query.get("format") == "text":
                return 200, {"ok": True, "text": diagnostics.report_text(rep)}
            return 200, {"ok": True, **rep}

        if path == "/v1/budget" and method == "GET":
            router = self.brain.router
            return 200, {"ok": True, "budgets": diagnostics.budgets(self.brain),
                         "route_default": router.default_mode() if router else "auto"}

        if path == "/v1/connectors" or path.startswith("/v1/connectors/"):
            return self._connectors(method, path, body)

        if path == "/v1/projects" or path.startswith("/v1/projects/"):
            return self._projects(method, path, body)

        if path == "/v1/memory" and method == "POST":
            text = str(body.get("text", "")).strip()
            if not text:
                raise ApiError(400, "text required")
            self._touch_user_activity()
            rec = self.memory.remember(text, title=str(body.get("title", "")),
                                       kind=str(body.get("kind", "note")),
                                       tags=body.get("tags") or [])
            return 201, {"ok": True, "id": rec.id, "kind": rec.kind, "tags": rec.tags,
                         "title": rec.title}

        if path == "/v1/memory" and method == "DELETE":
            rid = str(body.get("id", ""))
            if not rid:
                raise ApiError(400, "id required")
            self._touch_user_activity()
            ok = self.memory.delete(rid, hard=bool(body.get("hard")))
            return 200, {"ok": ok, "id": rid}

        # ---------------------------------------------------------------- #
        # standing instructions (#2) - owner only, so these require an
        # unlocked session AND are audited on every mutation. The model has no
        # route here: tools run in-process and never speak HTTP to the gateway.
        # ---------------------------------------------------------------- #
        # ------------------------------------------------------------ #
        # owner profile: facts, not orders (B)
        # ------------------------------------------------------------ #
        # ------------------------------------------------------------ #
        # languages and voice (stage 3)
        # ------------------------------------------------------------ #
        if path == "/v1/languages" and method == "GET":
            from ..agent import translate as tr
            from ..util import speech

            return 200, {"ok": True, "speaks": list(tr.SPOKEN),
                         "reads": list(tr.SCRIPT_LANG.values()),
                         "capability": tr.CAPABILITY, "voice": speech.engines()}
        if path == "/v1/languages/detect" and method == "POST":
            from ..agent import translate as tr

            text = str(body.get("text") or "")
            info = tr.detect(text)
            return 200, {"ok": True, **info, "gloss": tr.gloss(text, info["language"]),
                         "explanation": tr.explain(text, info) if not info["spoken"] else ""}
        if path == "/v1/telegram" and method == "GET":
            # Read-only view of the phone bridge. The token is never in it: the
            # bridge masks it at the source, so this route cannot leak it by
            # forgetting to.
            from ..agent.telegram import Telegram

            return 200, Telegram(vault=self.vault).status()
        if path == "/v1/telegram/test" and method == "POST":
            from ..agent.telegram import Telegram, TelegramError

            try:
                out = Telegram(vault=self.vault).send(
                    "Nila: a test from the dashboard.")
            except TelegramError as exc:
                return 400, {"ok": False, "error": str(exc)}
            return 200, out
        if path == "/v1/habit" and method == "GET":
            # Days really opened (from the audit journal), never from a poll.
            from ..agent import journal

            return 200, journal.activity(self.vault, self.memory)
        if path == "/v1/progress" and method == "GET":
            # How much of v2 is really finished, measured against the code.
            from ..agent import progress

            return 200, progress.report()
        if path == "/v1/sync" and method == "GET":
            # Read-only: where this PC syncs to, and what the last sync did. No
            # token, no address of a server it has not already been told about.
            from ..cloud.protocol import SyncEngine, SyncError

            cfg = dict(self.config.sync or {})
            backend = str(cfg.get("backend") or "local")
            try:
                # The engine's own status: pending records, when it last synced,
                # unresolved conflicts. Building it needs the unlocked vault,
                # which the dashboard already has.
                state = SyncEngine(self.config, self.memory, self.vault).status()
            except SyncError as exc:
                state = {"error": str(exc), "pending": 0, "last_synced": 0}
            out = {"ok": True, "configured": backend not in ("", "local"), "backend": backend,
                   "url": "", "path": "",
                   "pending": state.get("pending", 0),
                   "last_synced": state.get("last_synced", 0),
                   "conflicts": state.get("unresolved_conflicts", 0),
                   "device": state.get("device_id", ""),
                   "last": {}}
            if backend in ("http", "https", "cloud", "s3"):
                url = str(cfg.get("url") or "")
                local = url.startswith(("http://127.", "http://192.168.",
                                        "http://10.", "http://localhost"))
                out["url"] = url if local else ""
                out["masked"] = bool(url) and not local
            elif backend in ("local", "dir", "folder", "nas"):
                out["path"] = str(cfg.get("path") or "")
            return 200, out
        if path == "/v1/speak" and method == "POST":
            from ..util import speech

            text = str(body.get("text") or "").strip()
            if not text:
                return 400, {"ok": False, "error": "text required"}
            return 200, speech.speak_text(text, voice=str(body.get("voice") or ""),
                                          out_wav=body.get("wav") or None)

        # ------------------------------------------------------------ #
        # calendar, read-only (stage 3)
        # ------------------------------------------------------------ #
        if path == "/v1/calendar" and method == "GET":
            from ..util import ics

            source = self.brain.calendar_source()
            if not source:
                return 200, {"ok": True, "configured": False,
                             "how": "brain calendar --set <path-or-ics-url>"}
            allow = bool((self.config.agent or {}).get("calendar_allow_network", False))
            got = ics.load(source, allow_network=allow)
            if not got.get("ok"):
                return 200, {"ok": True, "configured": True,
                             "source": ics.masked_url(source), "error": got.get("error")}
            start, end, label = ics.range_from_text(str(query.get("when") or "today"))
            data = ics.between(got["text"], start, end)
            return 200, {"ok": True, "configured": True, "label": label,
                         "source": ics.masked_url(source) if ics.is_url(source) else source,
                         **data, "rendered": ics.render(data, label)}
        if path == "/v1/calendar" and method == "POST":
            from ..util import ics

            source = str(body.get("source") or "").strip()
            if not source:
                return 400, {"ok": False, "error": "source required (.ics path or address)"}
            if ics.is_url(source) and not body.get("allow_network"):
                return 400, {"ok": False, "needs_permission": True,
                             "host": ics.masked_url(source),
                             "error": "a calendar address on the internet needs allow_network: true"}
            agent = dict(self.config.agent or {})
            agent["calendar_ics"] = source
            agent["calendar_allow_network"] = bool(body.get("allow_network")) and ics.is_url(source)
            self.config.agent = agent
            self.config.save()
            return 200, {"ok": True,
                         "calendar": ics.masked_url(source) if ics.is_url(source) else source,
                         "note": "read-only; creating events needs OAuth"}

        # ------------------------------------------------------------ #
        # reminders (stage 2) - offline, encrypted, rendered by code
        # ------------------------------------------------------------ #
        if path in ("/v1/reminders", "/v1/reminders/due") and method == "GET":
            book = self.brain.reminder_book()
            if path.endswith("/due"):
                return 200, {"ok": True, "due": self.brain.due_reminders(consume=False)}
            return 200, {"ok": True, "count": len(book.all()),
                         "line": book.render(),
                         "reminders": [{"id": r.id, "title": r.title,
                                        **dict(r.meta or {})} for r in book.all()]}
        if path == "/v1/reminders" and method == "POST":
            from ..agent.reminders import next_after, parse_when, ReminderError

            phrase = str(body.get("text") or body.get("when") or "").strip()
            if not phrase:
                return 400, {"ok": False, "error": "say the time: 'in 10 minutes to ...'"}
            try:
                when = parse_when(phrase)
            except ReminderError as exc:
                return 400, {"ok": False, "error": str(exc)}
            if when.repeat:
                when.next_ms = next_after(when.at_ms, when.repeat, when.weekday)
            rec = self.brain.reminder_book().add(phrase, when, original=phrase)
            return 200, {"ok": True, "id": rec.id, "title": rec.title,
                         "at_ms": when.at_ms, "repeat": when.repeat}
        if path == "/v1/reminders/close" and method == "POST":
            book = self.brain.reminder_book()
            target = str(body.get("id") or body.get("number") or "")
            rec = book.get(target) or book.by_number(target)
            if rec is None:
                return 404, {"ok": False, "error": f"no reminder '{target}'"}
            action = str(body.get("action") or "done").lower()
            if action == "cancel":
                book.cancel(rec)
            elif action == "snooze":
                book.snooze(rec, float(body.get("minutes") or 10))
            else:
                book.close(rec, done=True)
            return 200, {"ok": True, "id": rec.id, "action": action,
                         "line": book.render()}

        # ------------------------------------------------------------ #
        # the model pack (small brain / big brain workflow)
        # ------------------------------------------------------------ #
        if path == "/v1/models" and method == "GET":
            from ..agent import models as pack
            ollama = str((self.config.agent or {}).get("ollama_url")
                         or "http://127.0.0.1:11434")
            machine = pack.installed(ollama_url=ollama)
            return 200, pack.pack_status(machine, ollama_url=ollama)
        if path == "/v1/models/choose" and method == "POST":
            from ..agent import models as pack
            job = str(body.get("job", "")).strip().lower()
            allow = bool(body.get("allow_cloud", True))
            free_only = bool(body.get("free_only", False))
            machine = pack.installed()
            return 200, {"ok": True, "job": job,
                         **pack.choose(job, machine["present"], allow_cloud=allow,
                                       free_only=free_only)}

        if path == "/v1/profile" and method == "GET":
            self._touch_user_activity()
            from ..security.classify import MAX_INSTRUCTION_CHARS as _MAX_CHARS

            rows = self.memory.profile()
            return 200, {"ok": True, "count": len(rows),
                         "profile": [{"field": k, "value": v}
                                     for k, v in sorted(rows.items())],
                         "limits": {"max_fields": self.memory.MAX_PROFILE_FIELDS,
                                    "max_chars": _MAX_CHARS},
                         "sent_online": False}
        if path == "/v1/profile" and method == "POST":
            from ..memory.store import InstructionError
            field = str(body.get("field", "")).strip()
            value = str(body.get("value", ""))
            self._touch_user_activity()
            try:
                rec = self.memory.set_profile(field, value)
            except InstructionError as exc:
                self.vault.audit("profile_refused", {"source": "api",
                                                     "reason": str(exc)[:120]})
                raise ApiError(400, str(exc)) from exc
            self.vault.audit("profile_set", {"source": "api", "field": field.lower(),
                                             "id": rec.id})
            return 201, {"ok": True, "field": field.lower(), "value": rec.body,
                         "profile": {"field": field.lower(), "value": rec.body}}
        if path.startswith("/v1/profile/") and method in ("POST", "DELETE"):
            f = path[len("/v1/profile/"):].strip("/")
            if f.endswith("/delete"):
                f = f[:-len("/delete")].strip("/")
            self._touch_user_activity()
            gone = self.memory.forget_profile(f)
            self.vault.audit("profile_forgotten", {"source": "api", "field": f,
                                                  "removed": gone})
            if not gone:
                raise ApiError(404, "no such profile fact")
            return 200, {"ok": True, "field": f, "removed": gone}

        # ------------------------------------------------------------ #
        # strict mode (B)
        # ------------------------------------------------------------ #
        if path == "/v1/strict" and method == "GET":
            from ..security.strict import StrictMode
            return 200, {"ok": True, **StrictMode(self.config, self.vault).state()}
        if path == "/v1/strict" and method == "POST":
            from ..security.strict import StrictMode
            action = str(body.get("action", "")).strip().lower()
            st = StrictMode(self.config, self.vault)
            if action == "on":
                state = st.enable()
            elif action == "off":
                state = st.disable()
            elif action in ("allow", "deny"):
                tool = str(body.get("tool", "")).strip()
                if not tool:
                    raise ApiError(400, "tool required")
                state = st.allow(tool) if action == "allow" else st.deny(tool)
            elif action == "ask":
                tool = str(body.get("tool", "")).strip()
                if not tool:
                    raise ApiError(400, "tool required")
                state = st.set_auto(tool, body.get("decision"))
            else:
                raise ApiError(400, "action must be on, off, allow, deny or ask")
            self.vault.audit("strict_mode_api", {"action": action})
            return 200, {"ok": True, **state}

        # ------------------------------------------------------------ #
        # bug capture: preview only. Writing a file stays a CLI action,
        # because it needs the owner to read the preview first (A).
        # ------------------------------------------------------------ #
        if path == "/v1/issues/preview" and method == "POST":
            from .. import issues_report as ir
            from ..diagnostics import run_checks  # the same checks `brain doctor` runs
            problem = str(body.get("problem", "")).strip()
            self._touch_user_activity()
            bundle = ir.build_bundle(problem, run_checks(self.config),
                                     home=Path(self.config.home),
                                     events=[])
            show = ir.preview(bundle)
            show["writes_file"] = False
            show["sends_anything"] = False
            self.vault.audit("issue_previewed",
                             {"category": bundle["triage"]["category"],
                              "fingerprint": bundle["fingerprint"]})
            return 200, show

        if path == "/v1/instructions" and method == "GET":
            self._touch_user_activity()
            rows = self.memory.instructions()
            from ..memory.store import MAX_INSTRUCTIONS
            from ..security.classify import MAX_INSTRUCTION_CHARS

            return 200, {"ok": True, "count": len(rows), "instructions": [
                {"id": r.id, "text": r.body, "created": r.created_at,
                 "created_at": r.created_at, "updated_at": r.updated_at}
                for r in rows],
                "limits": {"max_count": MAX_INSTRUCTIONS,
                           "max_chars": MAX_INSTRUCTION_CHARS}}

        if path == "/v1/instructions" and method == "POST":
            from ..memory.store import InstructionError

            text = str(body.get("text", ""))
            self._touch_user_activity()
            if not text.strip():
                self.vault.audit("instruction_refused", {"source": "api",
                                                         "reason": "empty"})
                raise ApiError(400, "an instruction cannot be empty")
            try:
                rec = self.memory.add_instruction(text)
            except InstructionError as exc:
                self.vault.audit("instruction_refused", {"source": "api",
                                                         "reason": str(exc)})
                raise ApiError(400, str(exc)) from exc
            self.vault.audit("instruction_added", {"source": "api", "id": rec.id})
            # Both shapes: `instruction` for the contract, the flat fields for
            # the dashboard that was written against them.
            return 201, {"ok": True, "instruction": {"id": rec.id, "text": rec.body,
                                                     "created": rec.created_at},
                         "id": rec.id, "text": rec.body}

        if (path.startswith("/v1/instructions/") and method == "POST"
                and path.endswith("/delete")):
            rid = path[len("/v1/instructions/"):-len("/delete")].strip("/")
            self._touch_user_activity()
            ok = self.memory.remove_instruction(rid)
            self.vault.audit("instruction_removed", {"source": "api", "id": rid})
            if not ok:
                raise ApiError(404, "no such instruction")
            return 200, {"ok": True, "id": rid}

        if path.startswith("/v1/instructions/") and method in ("POST", "PATCH", "PUT",
                                                              "DELETE"):
            from ..memory.store import InstructionError

            rid = path.rsplit("/", 1)[-1]
            self._touch_user_activity()
            if method == "DELETE":
                ok = self.memory.remove_instruction(rid)
                self.vault.audit("instruction_removed", {"source": "api", "id": rid})
                if not ok:
                    raise ApiError(404, "no such instruction")
                return 200, {"ok": True, "id": rid}
            try:
                rec = self.memory.edit_instruction(rid, str(body.get("text", "")))
            except InstructionError as exc:
                self.vault.audit("instruction_refused", {"source": "api",
                                                         "reason": str(exc)})
                raise ApiError(400, str(exc)) from exc
            self.vault.audit("instruction_edited", {"source": "api", "id": rec.id})
            return 200, {"ok": True, "id": rec.id, "text": rec.body}

        if path == "/v1/memory/search" and method == "GET":
            self._touch_user_activity()
            q = str(query.get("q", ""))
            rows = self.memory.search(q, limit=int(query.get("limit", 5)),
                                      kind=query.get("kind"), tag=query.get("tag"))
            return 200, {"ok": True, "count": len(rows), "results": [
                {"id": r.id, "kind": r.kind, "title": r.title, "tags": r.tags,
                 "body": r.body, "score": r.score} for r in rows]}

        if path == "/v1/memory/list" and method == "GET":
            self._touch_user_activity()
            rows = self.memory.list_records(kind=query.get("kind"), tag=query.get("tag"),
                                            limit=int(query.get("limit", 25)))
            return 200, {"ok": True, "count": len(rows), "results": [
                {"id": r.id, "kind": r.kind, "title": r.title, "tags": r.tags,
                 "updated_at": r.updated_at} for r in rows]}

        if path == "/v1/sync" and method == "POST":
            from ..cloud.protocol import SyncEngine, SyncError

            self._touch_user_activity()
            try:
                engine = SyncEngine(self.config, self.memory, self.vault)
            except SyncError as exc:
                raise ApiError(409, str(exc)) from exc
            return 200, engine.sync(dry_run=bool(body.get("dry_run")))

        if path == "/v1/files" and method == "GET":
            # The dashboard auto-fetches the root directory; only explicit
            # navigation to a selected path counts as user activity.
            sess = (self._touch_user_activity() if query.get("path")
                    else self.vault.session)
            if sess is None:
                raise ApiError(423, "session expired; authenticate again")
            if sess.degraded:
                raise ApiError(403, "a full (voice-verified) session is required to browse files")
            try:
                out = dashboard.list_names(dashboard.roots(self.config),
                                           str(query.get("path", "")), _forbidden)
                if not out["path"]:
                    out["roots"] = dashboard.root_status(self.config)
                return 200, {"ok": True, **out}
            except PermissionError as exc:
                self.vault.audit("dashboard_refused", {"route": "files", "reason": str(exc)[:60]})
                raise ApiError(403, "refused") from exc

        if path == "/v1/consent" and method == "GET":
            return 200, {"ok": True, "pending": self.brain.permissions.list_pending()}

        if path in ("/v1/consent/affirm", "/v1/consent/deny") and method == "POST":
            sess = self._touch_user_activity()
            if sess.degraded:
                raise ApiError(403, "a full (voice-verified) session is required to answer consent prompts")
            pid, broker = str(body.get("id", "")), self.brain.permissions
            # single-use only: the dashboard can never create a standing grant
            d = (broker.affirm(pid, granted_by="api", remember=False) if path.endswith("affirm")
                 else broker.deny(pid, granted_by="api", standing=False))
            return 200, {"ok": True, "decision": d.to_dict()}

        raise ApiError(404, f"no route for {method} {path}")


# --------------------------------------------------------------------------- #
def _etag_match(header: str, tag: str) -> bool:
    """True when the client already holds this exact asset (`*` or a list)."""
    if not header:
        return False
    if header.strip() == "*":
        return True
    want = tag.strip('"')
    return any(part.strip().strip('"') == want for part in header.split(","))


class _Handler(BaseHTTPRequestHandler):
    gateway: Gateway = None          # set by serve()
    server_version = "personal-brain/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:   # keep stdout clean
        pass

    def _send(self, status: int, payload: Dict[str, Any],
              head_only: bool = False) -> None:
        data = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "0" if head_only else str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def _authorized(self) -> bool:
        gw = self.gateway
        header = self.headers.get("Authorization", "")
        if hmac.compare_digest(header.encode(), f"Bearer {gw.token}".encode()):
            return True
        sess = gw.vault.session
        return bool(sess and hmac.compare_digest(header.encode(),
                                                 f"Bearer {sess.token}".encode()))

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

    def _send_static(self, data: bytes, ctype: str, head_only: bool = False) -> None:
        tag = dashboard.etag(data)
        if head_only:
            self.send_response(200)
            for k, v in (("Content-Type", ctype), ("Content-Length", str(len(data))),
                         ("ETag", tag), ("Cache-Control", "no-cache"),
                         ("X-Content-Type-Options", "nosniff")):
                self.send_header(k, v)
            self.end_headers()
            return
        if _etag_match(self.headers.get("If-None-Match", ""), tag):
            # A repeat visit costs one revalidation and no body: the page and the
            # 30 KB script used to be re-downloaded on every open.
            self.send_response(304)
            for k, v in (("ETag", tag), ("Cache-Control", "no-cache"),
                         ("Content-Length", "0")):
                self.send_header(k, v)
            self.end_headers()
            return
        self.send_response(200)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(data))),
                     ("ETag", tag), ("Cache-Control", "no-cache"),
                     ("X-Content-Type-Options", "nosniff"),
                     ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
                     ("Content-Security-Policy", dashboard.CSP),
                     ("Permissions-Policy", dashboard.PERMISSIONS)):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method: str) -> None:
        gw = self.gateway
        parsed = urlparse(self.path)
        if not self._host_ok():
            self._send(403, {"ok": False, "error": "bad Host header"})
            return
        # HEAD was answered 501 by BaseHTTPRequestHandler, which made plain
        # reachability checks (and some browser preflights) look like an outage.
        head = method == "HEAD"
        page = dashboard.asset(parsed.path) if method in ("GET", "HEAD") else None
        if page:
            self._send_static(*page, head_only=head)
            return
        if head:
            self._head_api(parsed.path)
            return
        if parsed.path not in ("/health",) and not self._authorized():
            self._send(401, {"ok": False, "error": "missing or invalid bearer token"})
            return
        # Content-Length is attacker-controlled. `int()` raised on "-1" was not
        # the only problem: the negative value reached `rfile.read(-1)`, which
        # blocks until the peer closes, so one bad header pinned a handler thread
        # and the request never got an answer (audit B7, reproduced).
        raw_length = (self.headers.get("Content-Length") or "0").strip()
        if not raw_length.isdigit():
            self._send(400, {"ok": False, "error": "Content-Length must be a "
                                                  "non-negative integer"})
            return
        length = int(raw_length)
        if length > MAX_BODY:
            self._send(413, {"ok": False, "error": f"body larger than {MAX_BODY} bytes"})
            return
        raw = self.rfile.read(length) if length else b""
        body: Dict[str, Any] = {}
        if raw:
            try:
                body = json.loads(raw)
                if not isinstance(body, dict):
                    body = {"_raw": body}
            except json.JSONDecodeError:
                self._send(400, {"ok": False, "error": "body must be JSON"})
                return
        query = {k: (v[0] if len(v) == 1 else v) for k, v in parse_qs(parsed.query).items()}
        try:
            status, payload = gw.route(method, parsed.path, query, body)
        except ApiError as exc:
            status, payload = exc.status, {"ok": False, "error": exc.message}
        except PermissionError as exc:
            status, payload = 423, {"ok": False, "error": str(exc)}
        except Exception as exc:
            gw.vault.audit("api_error", {"path": parsed.path, "error": type(exc).__name__})
            status, payload = 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self._send(status, payload)

    def _head_api(self, path: str) -> None:
        """Answer HEAD with the same status the GET would give, and no body."""
        gw = self.gateway
        if path not in ("/health",) and not self._authorized():
            self._send(401, {"ok": False, "error": "missing or invalid bearer token"},
                       head_only=True)
            return
        try:
            status, _payload = gw.route("GET", path, {}, {})
        except ApiError as exc:
            status = exc.status
        except PermissionError:
            status = 423
        except Exception:
            status = 500
        self._send(status, {}, head_only=True)

    def do_HEAD(self) -> None:    # noqa: N802
        self._handle("HEAD")

    def do_GET(self) -> None:     # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:    # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_PATCH(self) -> None:   # noqa: N802
        # Missing until #2: the gateway routed PATCH internally but
        # BaseHTTPRequestHandler answers 501 for any verb without a do_ method,
        # so editing an instruction over HTTP failed before it reached the
        # handler.
        self._handle("PATCH")

    def do_PUT(self) -> None:     # noqa: N802
        self._handle("PUT")


def serve(config: Config, vault: Vault, memory: MemoryStore, host: str = "127.0.0.1",
          port: int = 8765, token: str = "", open_browser: bool = False,
          show_token: bool = False, quiet_banner: bool = False) -> int:
    gateway = Gateway(config, vault, memory, token=token or None)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"\033[31mrefusing to bind {host}: the API speaks plain HTTP and the bearer "
              f"token would cross the network.\033[0m\n"
              f"Put a TLS reverse proxy (caddy/nginx) in front and keep this on loopback.",
              flush=True)
        memory.close()
        vault.lock()
        return 1
    _Handler.gateway = gateway
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    actual_port = httpd.server_address[1]
    base = f"http://{host}:{actual_port}"
    if not quiet_banner:
        print(f"Nila is listening on {base}  (this computer only)", flush=True)
    # The token is a credential. It is NOT printed by default: it would land in
    # terminal scrollback and any log, so the browser receives it in the URL
    # *fragment*, which is never sent to the server (see `not printed` below).
    if show_token:
        # flush: stdout is a pipe whenever a .bat or a log captures this window,
        # and a token sitting in a 4 KB buffer is a token the owner cannot see
        # when he needs it (found by starting the app from a script).
        print(f"  dashboard: {base}/#{gateway.token}", flush=True)
        print(f"  bearer token: {gateway.token}", flush=True)
    elif open_browser:
        import threading
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(f"{base}/#{gateway.token}")).start()
        print(f"Nila is open at {base}  (this computer only)", flush=True)
        print("  your browser is opening; the access token is passed privately, "
              "never printed")
    else:
        print(f"Nila is listening on {base}  (this computer only)",
              flush=True)
        print("  add --open to put the dashboard in your browser", flush=True)
    print("  ctrl-c (or closing this window) stops Nila and locks the vault", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down…")
    finally:
        httpd.shutdown()
        httpd.server_close()
        memory.close()
        vault.lock()
    return 0
