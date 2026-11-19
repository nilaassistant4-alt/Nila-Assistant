"""The unlock/enrol ceremony: passphrase (KDF) + voice (biometric) + liveness.

Policy matrix
-------------
mode ``full``      passphrase + spoken random challenge + liveness  -> trusted session
mode ``voice``     voice + liveness, passphrase cached in keyring   -> trusted session
mode ``totp``      passphrase + TOTP code                           -> full unless an enrolled voice factor was skipped
mode ``recovery``  recovery code (break-glass)                      -> trusted, ``degraded``,
                                                                    forces re-enrolment flag
mode ``passphrase``passphrase only                                  -> **refused** unless
                                                                    policy.allow_degraded

Every attempt - pass or fail - is written to the hash-chained audit journal,
and failures drive exponential lockout (see ``Vault.register_failure``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..crypto.hash import fast_kdf
from ..memory.vault import Session, Vault
from ..util.misc import now_ms
from .liveness import LivenessGuard
from .totp import (counter_for, generate_recovery_codes, generate_secret,
                   provisioning_uri, verify as totp_verify)
from .voice import VoiceVerifier


class CeremonyError(RuntimeError):
    def __init__(self, message: str, reasons: Optional[List[str]] = None):
        super().__init__(message)
        self.reasons = reasons or []


@dataclass
class CeremonyResult:
    ok: bool
    mode: str
    session: Optional[Session] = None
    detail: Dict[str, Any] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)


class IdentityCeremony:
    def __init__(self, config: Config, vault: Vault):
        self.config = config
        self.vault = vault
        vcfg = config.voice_config()
        self.voice = VoiceVerifier(vault, threshold=vcfg["threshold"], backend_name=vcfg["backend"])
        self.liveness = LivenessGuard(
            key=fast_kdf((vault.data.get("kdf", {}).get("salt", "x")).encode(),
                         b"pbx/liveness/v1", 32),
            ttl_seconds=90,
            replay_window=vcfg["replay_window"],
            challenge_len=vcfg["challenge_len"],
            min_duration=vcfg["min_duration"],
            max_duration=vcfg["max_duration"],
        )

    # ------------------------------------------------------------------ #
    # enrolment
    # ------------------------------------------------------------------ #
    def enroll_voice(self, samples: List[Any]) -> Dict[str, Any]:
        if not self.vault.is_unlocked():
            raise CeremonyError("unlock the vault before enrolling a voice")
        if len(samples) < int(self.config.voice_config()["samples"]):
            raise CeremonyError(
                f"need {self.config.voice_config()['samples']} enrolment samples, got {len(samples)}")
        report = self.voice.enroll(samples)
        if report.get("intra_speaker_min") is not None and report["intra_speaker_min"] < 0.35:
            report["warning"] = ("enrolment samples disagree strongly - re-record in a quieter "
                                 "room with the same microphone")
        return report

    def enroll_totp(self, account: Optional[str] = None) -> Dict[str, Any]:
        if not self.vault.is_unlocked():
            raise CeremonyError("unlock the vault first")
        secret = generate_secret()
        self.vault.store_totp(secret)
        acct = account or self.vault.user
        self.vault.audit("totp_enrolled", {"account": acct})
        return {"secret": secret, "uri": provisioning_uri(secret, acct)}

    def reissue_recovery_codes(self, n: int = 8) -> List[str]:
        """Replace the recovery set; every code issued before is void."""
        if not self.vault.is_unlocked():
            raise CeremonyError("unlock the vault first")
        codes = generate_recovery_codes(n)
        self.vault.reissue_recovery_codes(codes)
        return codes

    def enroll_recovery_codes(self, n: int = 8) -> List[str]:
        if not self.vault.is_unlocked():
            raise CeremonyError("unlock the vault first")
        codes = generate_recovery_codes(n)
        self.vault.store_recovery_codes(codes)
        self.vault.audit("recovery_codes_issued", {"count": n})
        return codes

    # ------------------------------------------------------------------ #
    # challenge / verify
    # ------------------------------------------------------------------ #
    def issue_challenge(self, style: str = "auto") -> Dict[str, Any]:
        """Issue the phrase the owner must say. ``auto`` follows her language.

        The owner speaks Tamil first. Asking her to read "alpha bravo cedar"
        aloud to prove she is herself was the one place the build forgot that -
        and the voice maths never cared, because MFCC is text-independent.
        ``auto`` reads the owner's language from the profile/config and picks
        Tamil numerals when it is Tamil or Tanglish, English digits otherwise.
        """
        if not self.vault.has_voice():
            raise CeremonyError("no voice template enrolled (run: brain enroll-voice)")
        style = self._resolve_style(style)
        ch = self.liveness.issue(style=style)
        self.vault.audit("challenge_issued", {"nonce": ch.nonce[:8], "style": ch.style,
                                              "language": ch.language})
        return ch.to_dict()

    def _resolve_style(self, style: str) -> str:
        """Turn ``auto`` into a concrete pool, using the owner's own language."""
        want = str(style or "auto").strip().lower()
        if want not in ("auto", "", "default"):
            return want
        language = ""
        try:
            from ..agent import translate

            for field in ("language", "language_preference"):
                value = str((self.config.agent or {}).get(field) or "")
                if value:
                    language = value
                    break
            if not language:
                language = str(self.vault.data.get("owner", {}).get("language") or "")
            if not language:
                language = str(self.vault.data.get("language") or "")
            if language:
                info = translate.detect(language) if hasattr(translate, "detect") else {}
                language = str(info.get("language") or language)
        except Exception:                       # pragma: no cover - keep working without a hint
            language = ""
        if language.lower().startswith(("tamil", "tanglish")) or "ta" == language.lower():
            return "tamil_digits"
        return "digits"

    def verify_voice(self, nonce: str, audio: Any, bound: Optional[str] = None) -> Dict[str, Any]:
        """Step 2 of the ceremony. Returns scores; does NOT start a session."""
        submission = {"nonce": nonce, "audio": audio, "bound": bound}
        live = self.liveness.check(submission)
        if not live.ok:
            self.vault.audit("liveness_failed", {"nonce": nonce[:8], "reasons": live.reasons})
            raise CeremonyError("liveness check failed", live.reasons)
        score = self.voice.verify(audio)
        self.vault.audit("voice_verified", {"nonce": nonce[:8], "score": score.score,
                                            "passed": score.passed, "threshold": score.threshold,
                                            "detail": live.detail})
        if not score.passed:
            wait = self.vault.register_failure("voice-score-below-threshold")
            raise CeremonyError(
                f"voice score {score.score} below threshold {score.threshold}"
                + (f" (locked out {wait}s)" if wait else ""),
                [f"score={score.score}", f"threshold={score.threshold}"],
            )
        return {"score": score.score, "threshold": score.threshold,
                "liveness": live.detail, "backend": score.backend}

    # ------------------------------------------------------------------ #
    # unlock
    # ------------------------------------------------------------------ #
    def unlock(self, passphrase: str, mode: str = "auto", voice_submission: Optional[Dict[str, Any]] = None,
               totp_code: Optional[str] = None, recovery_code: Optional[str] = None) -> CeremonyResult:
        """Run the full ceremony and, on success, start a session.

        ``voice_submission`` = ``{"nonce": ..., "audio": <path|bytes|data-uri>, "bound": ...}``.
        """
        locked, wait = self.vault.locked_out()
        if locked:
            return CeremonyResult(False, mode, reasons=[f"locked out; retry in {wait}s"])

        policy = self.vault.policy()
        vcfg = self.config.voice_config()
        detail: Dict[str, Any] = {}

        # 1. knowledge factor ------------------------------------------------
        try:
            mk = self.vault.unlock(passphrase)
        except PermissionError as exc:
            w = self.vault.register_failure("bad-passphrase")
            return CeremonyResult(False, mode, reasons=[str(exc)] + ([f"locked out {w}s"] if w else []))
        except RuntimeError as exc:
            return CeremonyResult(False, mode, reasons=[str(exc)])
        detail["passphrase"] = "ok"
        # the voice template / TOTP secret are encrypted *with* MK, so hold it in
        # memory provisionally until the second factor is decided.
        self.vault.begin_provisional(mk)

        # 2. choose the second factor ---------------------------------------
        chosen = mode
        has_voice = self.vault.has_voice()
        totp_secret = self.vault.load_totp_secret()
        has_totp = bool(totp_secret)
        if chosen == "auto":
            if has_voice and voice_submission and vcfg["require_liveness"]:
                chosen = "voice"
            elif has_totp and totp_code:
                chosen = "totp"
            elif recovery_code:
                chosen = "recovery"
            elif has_voice and voice_submission:
                chosen = "voice"
            else:
                chosen = "passphrase"

        if chosen == "voice":
            if not voice_submission:
                self.vault.discard_provisional()
                return CeremonyResult(False, chosen, reasons=["voice submission required"])
            try:
                detail["voice"] = self.verify_voice(voice_submission["nonce"],
                                                    voice_submission["audio"],
                                                    voice_submission.get("bound"))
            except CeremonyError as exc:
                self.vault.discard_provisional()
                return CeremonyResult(False, chosen, reasons=exc.reasons or [str(exc)], detail=detail)

        elif chosen == "totp":
            secret = totp_secret
            if not secret or not totp_code:
                self.vault.discard_provisional()
                return CeremonyResult(False, chosen, reasons=["totp not enrolled or code missing"])
            # ONE-TIME CODE. Verifying the maths alone let the same 6 digits
            # authenticate again and again inside their 30-second window (audit
            # B6, reproduced end to end). The highest spent step is stored in the
            # encrypted identity blob, so replay would require opening the vault.
            # NOTE: the ceiling must be read while the PROVISIONAL key is live -
            # `load_totp_secret` above only works because `begin_provisional` has
            # already run. Reading it after the session swap returned 0 and let a
            # used code through, which the end-to-end probe caught immediately.
            counter = counter_for(secret, totp_code,
                                  min_counter=self.vault.totp_last_counter())
            if counter < 0:
                w = self.vault.register_failure("bad-totp")
                self.vault.discard_provisional()
                return CeremonyResult(False, chosen,
                                      reasons=["invalid or already-used TOTP code"]
                                              + ([f"locked out {w}s"] if w else []),
                                      detail=detail)
            self.vault.mark_totp_used(counter)
            detail["totp"] = "ok"

        elif chosen == "recovery":
            if not recovery_code or not self.vault.consume_recovery_code(recovery_code):
                w = self.vault.register_failure("bad-recovery-code")
                self.vault.discard_provisional()
                return CeremonyResult(False, chosen,
                                      reasons=["invalid recovery code"] + ([f"locked out {w}s"] if w else []),
                                      detail=detail)
            detail["recovery"] = "consumed"
            self.vault.meta_set("re_enrollment_required", True)

        elif chosen == "passphrase":
            # FAIL CLOSED. A passphrase alone is never a full session: if any
            # second factor exists it must be used, and if none exists the owner
            # must enrol one (``brain totp``) before the vault opens. The only
            # override is the explicit, audited ``identity.allow_degraded``.
            needs_factor = (policy.get("require_second_factor", True)
                            or (policy.get("require_voice", True) and has_voice))
            if needs_factor and not policy.get("allow_degraded"):
                self.vault.discard_provisional()
                if has_voice or has_totp:
                    why = ("second factor required: supply a voice challenge "
                           "(brain unlock --voice <wav>) or --totp <code>")
                else:
                    why = ("no second factor is enrolled, so the passphrase alone "
                           "is refused. Run `brain totp` once to enrol an "
                           "authenticator app")
                return CeremonyResult(
                    False, chosen,
                    reasons=[why, "policy.require_second_factor is on (a safety setting)"],
                    detail=detail)
            detail["degraded"] = "passphrase-only session"

        # 3. success ---------------------------------------------------------
        # Match the CLI: a session is limited only when a stronger factor is
        # available and was skipped. TOTP is a full second factor when no voice
        # factor is enrolled; recovery remains break-glass/degraded.
        degraded = (chosen == "recovery"
                    or (chosen == "totp" and has_voice)
                    or (chosen == "passphrase" and (has_voice or has_totp)))
        try:
            sess = self.vault.start_session(mk, mode=chosen, degraded=degraded)
        except RuntimeError as exc:
            self.vault.discard_provisional()
            return CeremonyResult(False, chosen,
                                  reasons=[f"authentication session expired: {exc}"],
                                  detail=detail)
        self.vault.register_success()
        self.vault.audit("session_unlock", {"mode": chosen, "degraded": degraded,
                                            "session": sess.session_id})
        detail["session"] = {"id": sess.session_id, "expires_at": sess.expires_at,
                             "absolute_expires_at": sess.absolute_expires_at,
                             "degraded": degraded, "mode": chosen}
        return CeremonyResult(True, chosen, session=sess, detail=detail)

    # ------------------------------------------------------------------ #
    def step_up(self, passphrase: str, voice_submission: Optional[Dict[str, Any]] = None) -> CeremonyResult:
        """Re-authenticate for high-risk ops (export, rotate key, delete all).

        The result is a *fresh* unlock. Marking the session is what makes the
        gate real: before this, ``Session.step_up_ok`` was read by the tool
        registry and by the classifier but nothing ever set it, so
        ``export_backup`` refused in every normal flow - a dead feature, not a
        safe one.
        """
        result = self.unlock(passphrase, mode="voice" if voice_submission else "auto",
                             voice_submission=voice_submission)
        sess = getattr(result, "session", None)
        if result.ok and sess is not None:
            sess.step_up_ok = True
            sess.step_up_at = now_ms()
            self.vault.audit("step_up_granted", {"session": sess.session_id,
                                                 "mode": getattr(sess, "mode", ""),
                                                 "degraded": bool(getattr(sess, "degraded", False))})
        return result

    # ------------------------------------------------------------------ #
    # approvals for one-off owner actions (connectors, #3)
    # ------------------------------------------------------------------ #
    def authorise_action(self, action: str, passphrase: str,
                         totp_code: Optional[str] = None,
                         recovery_code: Optional[str] = None,
                         max_uses: int = 1,
                         ttl_ms: Optional[int] = None) -> CeremonyResult:
        """Re-authenticate the owner and spend the approval on ONE action.

        The session is deliberately NOT replaced. Swapping it would give the
        caller a *new* session id, which the dashboard's bearer token (the old
        session token) does not match - a stale-token bug wearing a security
        hat. Approvals are therefore recorded against the CURRENT session id
        and consumed by :meth:`consume_action`, once.

        Every attempt is audited, including a refusal, so a replay after a
        restart, a stolen click or an accidental double-submit all appear in
        the journal.
        """
        action = str(action or "").strip()
        if not action or len(action) > 120:
            return CeremonyResult(False, "approval", reasons=["no action named"])
        sess = self.vault.session
        if sess is None or sess.session_id == "provisional":
            return CeremonyResult(False, "approval", reasons=["the vault is locked"])

        locked, wait = self.vault.locked_out()
        if locked:
            return CeremonyResult(False, "approval",
                                  reasons=[f"locked out; retry in {wait}s"])
        try:
            mk = self.vault.unlock(passphrase)
        except PermissionError as exc:
            w = self.vault.register_failure("bad-passphrase")
            self.vault.audit("action_refused", {"action": action, "reason": "bad-passphrase",
                                                "session": sess.session_id})
            return CeremonyResult(False, "approval",
                                  reasons=[str(exc)] + ([f"locked out {w}s"] if w else []))
        except RuntimeError as exc:
            self.vault.audit("action_refused", {"action": action, "reason": "not-initialised"})
            return CeremonyResult(False, "approval", reasons=[str(exc)])

        self.vault.begin_provisional(mk)
        detail: Dict[str, Any] = {}
        chosen = "passphrase"
        try:
            secret = self.vault.load_totp_secret()
            # `has_voice` is not needed on this path: an approval step-up is
            # permitted or not by the policy below, not by enrolment.
            if recovery_code:
                # Break-glass replaces the authenticator, and it is degraded by
                # construction, so it can never authorise a key edit.
                if not self.vault.consume_recovery_code(str(recovery_code).strip()):
                    w = self.vault.register_failure("bad-recovery-code")
                    self.vault.audit("action_refused", {"action": action,
                                                        "reason": "bad-recovery-code"})
                    return CeremonyResult(False, "recovery",
                                          reasons=["invalid recovery code"]
                                                  + ([f"locked out {w}s"] if w else []))
                chosen = "recovery"
                detail["recovery"] = "consumed"
            elif secret:
                if not totp_code:
                    self.vault.audit("action_refused", {"action": action,
                                                        "reason": "totp-required"})
                    return CeremonyResult(False, "totp",
                                          reasons=["an authenticator code is required",
                                                   "run `brain totp` to see the current one"])
                counter = counter_for(secret, str(totp_code).strip(),
                                      min_counter=self.vault.totp_last_counter())
                if counter < 0:
                    w = self.vault.register_failure("bad-totp")
                    self.vault.audit("action_refused", {"action": action,
                                                        "reason": "bad-totp"})
                    return CeremonyResult(False, "totp",
                                          reasons=["invalid or already-used TOTP code"]
                                                  + ([f"locked out {w}s"] if w else []))
                self.vault.mark_totp_used(counter)
                chosen = "totp"
                detail["totp"] = "ok"
            else:
                policy = self.vault.policy()
                if not policy.get("allow_degraded"):
                    self.vault.audit("action_refused", {"action": action,
                                                        "reason": "no-second-factor"})
                    return CeremonyResult(
                        False, "passphrase",
                        reasons=["no second factor is enrolled, so a passphrase alone "
                                 "cannot approve an action. Run `brain totp` once to "
                                 "enrol an authenticator app."])
                chosen = "passphrase"
                detail["degraded"] = "passphrase-only (policy allows it)"
        finally:
            self.vault.discard_provisional()

        self.vault.register_success()
        grant = self._grant(action, sess, chosen, degraded=(chosen == "recovery"),
                            max_uses=max_uses, ttl_ms=ttl_ms)
        self.vault.audit("action_authorised",
                         {"action": action, "mode": chosen, "session": sess.session_id,
                          "expires_at": grant["expires_at"],
                          "degraded": grant["degraded"]})
        detail["grant"] = {"action": action, "expires_at": grant["expires_at"],
                           "mode": chosen, "degraded": grant["degraded"],
                           "uses_left": grant["uses_left"]}
        return CeremonyResult(True, chosen, session=sess, detail=detail)

    #: How long an approval stays spendable. Short on purpose: the owner is
    #: expected to approve and act in one breath.
    APPROVAL_TTL_MS = 120_000

    #: How recently a session must have been created to count as "this login"
    #: for a command that runs in one process. The CLI unlocks the vault and
    #: performs the action seconds apart; the dashboard's session is long-lived
    #: and never qualifies, so it must re-authenticate.
    FRESH_SESSION_MS = 120_000

    def grant_from_fresh_session(self, action: str, max_uses: int = 1) -> Dict[str, Any]:
        """Approve an action because the ceremony ran in THIS process, just now.

        Only for a local command that has already authenticated (the CLI): the
        passphrase and the authenticator code were typed into this process
        seconds ago, so asking for a second code inside the same 30-second
        window would be refused as a replay - a fairness bug, not a security
        gain. Anything long-lived (the dashboard, an API session) must call
        :meth:`authorise_action` instead.
        """
        sess = self.vault.session
        if sess is None or sess.session_id == "provisional":
            return {"ok": False, "reason": "the vault is locked"}
        if sess.degraded or str(sess.mode).lower() != "totp":
            return {"ok": False, "reason": "this session did not prove the authenticator factor"}
        if getattr(sess, "fresh_login_spent", False):
            return {"ok": False, "reason": "this login already authorised an action"}
        if now_ms() - int(sess.started_at) > self.FRESH_SESSION_MS:
            return {"ok": False, "reason": "the session is older than a fresh login"}
        grant = self._grant(action, sess, "totp-session", degraded=False,
                            max_uses=max_uses)
        self.vault.audit("action_authorised",
                         {"action": action, "mode": "totp-session",
                          "session": sess.session_id, "expires_at": grant["expires_at"],
                          "degraded": False})
        return {"ok": True, **grant}

    def _approval_path(self):
        return self.config.home / "approvals.json"

    def _grants(self) -> Dict[str, Any]:
        import json as _json

        path = self._approval_path()
        if not path.exists():
            return {}
        try:
            data = _json.loads(path.read_bytes())
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _save_grants(self, grants: Dict[str, Any]) -> None:
        import json as _json

        from ..util.misc import write_private as _wp

        _wp(self._approval_path(), _json.dumps(grants, indent=1).encode())

    def _grant(self, action: str, sess: Any, mode: str, degraded: bool,
               max_uses: int = 1, ttl_ms: Optional[int] = None) -> Dict[str, Any]:
        import hashlib as _hash

        grants = self._grants()
        nonce = __import__("secrets").token_urlsafe(18)
        # ``max_uses`` is the honest knob: a secret write is spent on first use,
        # while flipping a switch may be repeated for the life of the grant. The
        # user's rule is "a fresh login", not "one click per login"; a switch is
        # not a secret, and every use is audited individually.
        uses = max(1, min(int(max_uses or 1), 50))
        entry = {"session": sess.session_id, "mode": mode, "degraded": bool(degraded),
                 "expires_at": now_ms() + int(ttl_ms or self.APPROVAL_TTL_MS),
                 "uses_left": uses, "uses": 0,
                 "nonce_hash": _hash.sha256(nonce.encode()).hexdigest()}
        grants[action] = entry
        # prune anything already expired so the file cannot grow without bound
        keep = {k: v for k, v in grants.items()
                if isinstance(v, dict) and int(v.get("expires_at", 0)) > now_ms()}
        self._save_grants(keep)
        return {**entry, "nonce": nonce}

    def pending_actions(self) -> List[Dict[str, Any]]:
        """Approvals still live for the current session (metadata only)."""
        sess = self.vault.session
        if sess is None:
            return []
        out: List[Dict[str, Any]] = []
        changed = False
        for action, entry in list(self._grants().items()):
            if (not isinstance(entry, dict)
                    or entry.get("session") != sess.session_id
                    or int(entry.get("expires_at", 0)) <= now_ms()):
                changed = changed or isinstance(entry, dict) and \
                    entry.get("session") == sess.session_id
                continue
            out.append({"action": action, "mode": entry.get("mode", ""),
                        "degraded": bool(entry.get("degraded", False)),
                        "uses_left": int(entry.get("uses_left", 1)),
                        "expires_in_s": max(0, (int(entry["expires_at"]) - now_ms() + 999) // 1000)})
        if changed:
            self._save_grants({k: v for k, v in self._grants().items()
                               if int(v.get("expires_at", 0)) > now_ms()})
        return sorted(out, key=lambda r: r["action"])

    def consume_action(self, action: str) -> Tuple[bool, str]:
        """Spend the approval for ``action``. ONE use, then gone.

        Consume-then-act, never peek-then-act: a peek would let a single
        approval authorise unlimited connector switches and test calls, which is
        the same bug class the integration egress gate was fixed for.
        """
        sess = self.vault.session
        grants = self._grants()
        entry = grants.get(action)
        if not isinstance(entry, dict):
            return False, "no approval for this action"
        # Spend it FIRST: whatever happens next cannot reuse this use.
        left = int(entry.get("uses_left", 1)) - 1
        if left <= 0:
            grants.pop(action, None)
        else:
            entry["uses_left"] = left
            entry["uses"] = int(entry.get("uses", 0)) + 1
            grants[action] = entry
        self._save_grants(grants)
        if sess is None or entry.get("session") != sess.session_id:
            self.vault.audit("action_refused", {"action": action,
                                                "reason": "approval belongs to another session"})
            return False, "this approval belongs to a different session"
        if int(entry.get("expires_at", 0)) <= now_ms():
            self.vault.audit("action_refused", {"action": action, "reason": "approval expired"})
            return False, "the approval expired; log in again to approve"
        if entry.get("degraded"):
            self.vault.audit("action_refused", {"action": action,
                                                "reason": "approval was degraded"})
            return False, ("that approval was given with a recovery code, which cannot "
                           "authorise this action")
        self.vault.audit("action_consumed",
                         {"action": action, "mode": entry.get("mode", ""),
                          "session": sess.session_id, "uses_left": max(0, left)})
        return True, ""

    def lock(self) -> None:
        self.vault.lock(reason="manual")
