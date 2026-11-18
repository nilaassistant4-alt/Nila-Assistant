"""Permission broker: explicit, affirmed, scoped host access.

Nila acts **only on explicit request**. This module is what makes that a
mechanical property rather than a prompt instruction.

The design rule that matters most:

    The MODEL cannot grant a permission. It can only *request* one.
    ``affirm()`` is reachable only from a human interface (CLI prompt, API call
    carrying a session token, or a config file the user edited). There is no
    tool, and no code path from a tool handler, that calls it.

Three grant kinds:

``one-shot``   A single affirmed action. ``request()`` returns a ``prompt_id``;
               the human affirms it; the handler consumes it exactly once.
               This is the default and the strongest form.
``durable``    A standing grant for a capability + scope (e.g. "read anything
               under ~/Documents"). Expires, revocable, usage-counted.
``denied``     An explicit refusal, remembered so Nila stops asking.

Grants are stored in the CLEAR in ``permissions.json``. That is deliberate:
they are not secrets, and a permission ledger the user cannot read is not a
permission ledger.
"""
from __future__ import annotations

import fnmatch
import os
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..util.misc import dumps, ensure_dir, loads, new_id, now_ms

# --------------------------------------------------------------------------- #
# capabilities
# --------------------------------------------------------------------------- #
CAP_FILE_READ = "fs.read"
CAP_FILE_LIST = "fs.list"
CAP_FILE_WRITE = "fs.write"
CAP_PHOTO = "media.photo"
CAP_SHELL = "shell.exec"
CAP_NET = "net.host"
CAP_EXPORT = "data.export"
CAP_MIC = "mic.listen"

#: risk of each capability, and whether a durable grant is even offerable.
#: ``shell.exec`` and ``data.export`` can never be made durable: they must be
#: affirmed every single time. That is a policy decision, not a convenience.
CAPABILITIES: Dict[str, Dict[str, Any]] = {
    CAP_FILE_READ:  {"risk": "medium", "durable_ok": True,
                     "egress": "stays on this device",
                     "label": "read files",
                     "detail": "open and return the contents of a file on this device"},
    CAP_FILE_LIST:  {"risk": "low", "durable_ok": True,
                     "label": "list directories",
                     "detail": "see which files exist in a directory (names only, no contents)"},
    CAP_FILE_WRITE: {"risk": "high", "durable_ok": False,
                     "label": "write files",
                     "detail": "create or modify a file on this device"},
    CAP_PHOTO:      {"risk": "high", "durable_ok": True,
                     "egress": "metadata only - image bytes never leave the device",
                     "label": "view photo gallery",
                     "detail": "open images from a gallery folder. Photos are biometric-adjacent: "
                               "faces and locations are personally identifying"},
    CAP_SHELL:      {"risk": "high", "durable_ok": False,
                     "egress": "whatever the command itself does - assume anything",
                     "label": "run a command",
                     "detail": "execute a shell command on this device. Never durable by design"},
    CAP_NET:        {"risk": "medium", "durable_ok": False,
                     "egress": "LEAVES THE DEVICE to the named host",
                     "label": "reach a host",
                     "detail": "make a network request to a specific hostname"},
    CAP_EXPORT:     {"risk": "high", "durable_ok": False,
                     "egress": "writes a file OUTSIDE the vault",
                     "label": "export your data",
                     "detail": "write your memories to a file outside the vault. "
                               "Never durable by design"},
    CAP_MIC:        {"risk": "high", "durable_ok": False,
                     "egress": "stays on this device - audio is never uploaded",
                     "label": "listen to the microphone",
                     "detail": "open the microphone and turn speech into text on "
                               "this device. Never durable: a microphone should be "
                               "opened for a reason, not standing permission"},
}

#: how long a pending prompt stays valid, and a default durable TTL.
#: a purpose containing these is arguing for its own approval - the broker
#: labels the text as untrusted rather than letting it read as a neutral reason
_PERSUASION_WORDS = ("ignore", "without reading", "trust me", "harmless", "safe to",
                     "no need to check", "approve immediately", "don't ask",
                     "do not question", "automatically", "silently", "all rules",
                     "override", "bypass", "just say yes")

PROMPT_TTL_MS = 120_000          # 2 minutes to answer a prompt
DURABLE_TTL_MS = 24 * 3600_000   # 24 hours (was 7 days; standing access should be short)


class PermissionError_(RuntimeError):
    """Raised when an action is refused for want of an affirmed permission."""

    def __init__(self, message: str, decision: Optional["Decision"] = None):
        super().__init__(message)
        self.decision = decision


@dataclass
class Grant:
    capability: str
    scope: str = "*"
    kind: str = "one_shot"          # one_shot | durable | denied
    purpose: str = ""
    granted_at: int = 0
    granted_by: str = ""            # "cli" | "api" | "config" - never "model"
    expires_at: int = 0
    uses: int = 0
    max_uses: int = 1
    note: str = ""

    def active(self, now: Optional[int] = None) -> bool:
        """Is this grant in force?

        A ``denied`` entry is a *standing refusal*: it is in force precisely
        when it is not expired. An earlier version returned False for all
        denials, which meant ``is_denied()`` could never find one and standing
        refusals were silently ignored.
        """
        now = now or now_ms()
        if self.expires_at and now > self.expires_at:
            return False
        if self.kind == "denied":
            return True                       # a refusal does not get "used up"
        if self.max_uses and self.uses >= self.max_uses:
            return False
        return True

    def covers(self, scope: str) -> bool:
        """Does this grant's scope cover the requested scope?

        ``*`` covers everything. A directory scope covers its descendants.
        A host scope covers exact match or a ``*.`` subdomain wildcard.

        Ordering matters: cheap *string* comparisons run before any filesystem
        resolution. An earlier version reached ``Path.resolve()`` for anything
        containing ``/`` - including exact matches - and resolve() can raise on
        a path that does not exist, which silently turned a granted scope into a
        refusal.
        """
        g = (self.scope or "*").strip()
        s = (scope or "").strip()
        if g == "*" or not s:
            return True
        if g == s:                                    # exact match first
            return True
        if g.startswith("*."):                        # host wildcard
            return s == g[2:] or s.endswith(g[1:])
        if g.endswith("/"):                           # explicit directory prefix
            return s.startswith(g) or s == g.rstrip("/")

        # Only now consider filesystem semantics, and never let a failure to
        # resolve downgrade a match we could have made textually.
        looks_like_path = (os.path.sep in g or g.startswith("~") or os.path.sep in s
                           or s.startswith("~"))
        if looks_like_path:
            try:
                gp = str(Path(g).expanduser().resolve())
            except Exception:
                gp = os.path.abspath(os.path.expanduser(g))
            try:
                sp = str(Path(s).expanduser().resolve())
            except Exception:
                sp = os.path.abspath(os.path.expanduser(s))
            if sp == gp or sp.startswith(gp.rstrip(os.path.sep) + os.path.sep):
                return True
            # fall through to textual prefix matching on the raw strings
            return s.startswith(g.rstrip("/") + "/")
        return fnmatch.fnmatch(s, g)


@dataclass
class Decision:
    allowed: bool
    reason: str
    capability: str = ""
    scope: str = ""
    prompt_id: str = ""
    grant: Optional[Grant] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason,
                "capability": self.capability, "scope": self.scope,
                "prompt_id": self.prompt_id}


@dataclass
class Broker:
    """The permission ledger and the only authority on what Nila may touch."""

    path: Path
    pending: Dict[str, Grant] = field(default_factory=dict)
    audit: Callable[[str, Dict[str, Any]], None] = lambda e, d: None
    #: set by a human interface. When None, prompts cannot be answered inline
    #: and ``request()`` simply reports that affirmation is required.
    prompter: Optional[Callable[[Dict[str, Any]], bool]] = None

    # Pending prompts are PERSISTED, not just held in memory. The CLI is one
    # process per command, so `brain ask` (which raises the prompt) and
    # `brain permission affirm` (which answers it) are different processes; an
    # in-memory pending map made the documented affirm loop impossible.
    #
    # Persisting a prompt is safe: a prompt is a *question*, not an answer.
    # Nothing is permitted until a human runs `affirm`, and that command is
    # reachable only from a human interface - no tool, and no code path from a
    # tool handler, can call it.

    @property
    def pending_path(self) -> Path:
        return self.path.parent / (self.path.stem + ".pending.json")

    def _load_pending(self) -> Dict[str, Grant]:
        if self.pending:
            return dict(self.pending)
        if not self.pending_path.exists():
            return {}
        try:
            raw = loads(self.pending_path.read_text())
        except Exception:
            return {}
        now = now_ms()
        out: Dict[str, Grant] = {}
        for pid, row in (raw.get("prompts") or {}).items():
            if int(row.get("expires_at") or 0) < now:
                continue                      # silently drop expired prompts
            try:
                out[pid] = Grant(**{k: v for k, v in row.items() if k != "prompt_id"})
            except TypeError:
                continue
        return out

    def _save_pending(self, prompts: Dict[str, Grant]) -> None:
        payload = {"version": 1, "updated_at": now_ms(),
                   "prompts": {pid: asdict(g) for pid, g in prompts.items()}}
        tmp = self.pending_path.with_suffix(".tmp")
        tmp.write_text(dumps(payload))
        os.replace(tmp, self.pending_path)
        self.pending = dict(prompts)

    # ------------------------------------------------------------------ #
    # persistence
    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        self.path = Path(self.path)
        ensure_dir(self.path.parent)

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "grants": []}
        try:
            data = loads(self.path.read_text())
            data.setdefault("grants", [])
            return data
        except Exception:
            return {"version": 1, "grants": []}

    def _save(self, grants: List[Grant]) -> None:
        payload = {"version": 1,
                   "grants": [asdict(g) for g in grants],
                   "updated_at": now_ms()}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(dumps(payload))
        os.replace(tmp, self.path)

    def grants(self) -> List[Grant]:
        return [Grant(**g) for g in self._load()["grants"]]

    # ------------------------------------------------------------------ #
    # querying
    # ------------------------------------------------------------------ #
    def _find(self, capability: str, scope: str, kind: str,
              now: Optional[int] = None) -> Optional[Grant]:
        for g in self.grants():
            if g.capability == capability and g.kind == kind and g.covers(scope) \
                    and g.active(now):
                return g
        return None

    def _consume_one_shot(self, capability: str, scope: str) -> Optional[Grant]:
        """Find and burn a single affirmed one-shot grant.

        Consumption is what makes "one-shot" mean something: ``uses`` goes to 1,
        ``max_uses`` is 1, so ``active()`` is false from then on. The same
        affirmed action can never be replayed.
        """
        g = self._find(capability, scope, "one_shot")
        if g is None:
            return None
        grants = self.grants()
        for row in grants:
            if (row.capability == g.capability and row.scope == g.scope
                    and row.kind == "one_shot" and row.granted_at == g.granted_at):
                row.uses += 1
                break
        self._save(grants)
        return g

    def consume(self, capability: str, scope: str = "", purpose: str = "") -> Decision:
        """Authorise ONE action and BURN the approval that allowed it.

        This is the gate an action must pass *immediately before it runs*.
        ``peek()`` answers "would this be allowed?" and must never be used to
        decide to proceed: it cannot consume, so a one-shot approval survived
        every call made with it. That is exactly how a single affirmed
        `net.host` grant authorised two integrations calls (audit B10), and the
        same shape existed in the tool gate.

        Order of precedence matches ``check()``: panic, standing refusal,
        durable grant, then a one-shot that is consumed here - under a lock and
        against freshly read state, so two concurrent callers cannot both see the
        same unused approval.
        """
        if capability not in CAPABILITIES:
            return Decision(False, f"unknown capability: {capability}", capability, scope)
        if self.panicked():
            return Decision(False, "PANIC LATCH is set - all permissions were revoked and "
                                   "nothing is granted until it is cleared", capability, scope)
        with self._consume_lock:
            if self.is_denied(capability, scope):
                return Decision(False, "explicitly denied by the user", capability, scope)
            hit = self._find(capability, scope, "durable")
            if hit:
                return Decision(True, f"durable grant ({hit.purpose or 'no purpose recorded'})",
                                capability, scope, grant=hit)
            shot = self._consume_one_shot(capability, scope)
            if shot:
                self.audit("permission_consumed",
                           {"capability": capability, "scope": scope,
                            "purpose": purpose or shot.purpose, "kind": "one_shot"})
                return Decision(True, "affirmed one-shot permission (consumed)",
                                capability, scope, grant=shot)
        return Decision(False, "no affirmed permission", capability, scope)

    def spend_unused(self, capability: str, scope: str = "") -> bool:
        """Burn a one-shot approval that the action did not spend itself.

        The handler for a tool normally calls ``request()``, and that consumes the
        approval. A handler that forgets would leave it usable, so the gate calls
        this afterwards: an approval authorises at most ONE action, whichever
        layer happens to spend it. Returns True when something was burned.
        """
        with self._consume_lock:
            shot = self._consume_one_shot(capability, scope)
        if shot is None:
            return False
        self.audit("permission_consumed", {"capability": capability, "scope": scope,
                                           "kind": "one_shot", "by": "gate-daemon"})
        return True

    def is_denied(self, capability: str, scope: str = "*") -> bool:
        return self._find(capability, scope, "denied") is not None

    def durable(self, capability: str, scope: str = "*") -> Optional[Grant]:
        return self._find(capability, scope, "durable")

    def peek(self, capability: str, scope: str = "") -> Decision:
        """Look without touching anything.

        ``check()`` CONSUMES a one-shot grant, which is correct for the code
        that is about to perform the action - and wrong for a gate that is only
        deciding whether to call that code. Gates must peek.
        """
        if capability not in CAPABILITIES:
            return Decision(False, f"unknown capability: {capability}", capability, scope)
        if self.panicked():
            return Decision(False, "PANIC LATCH is set - all permissions were revoked and "
                                   "nothing is granted until it is cleared", capability, scope)
        if self.is_denied(capability, scope):
            return Decision(False, "explicitly denied by the user", capability, scope)
        hit = self._find(capability, scope, "durable")
        if hit:
            return Decision(True, "durable grant", capability, scope, grant=hit)
        if self._find(capability, scope, "one_shot"):
            return Decision(True, "affirmed one-shot available", capability, scope)
        return Decision(False, "no affirmed permission", capability, scope)

    def check(self, capability: str, scope: str = "*", purpose: str = "") -> Decision:
        """Is this action permitted right now, without asking?

        Only a live durable grant (or a live one-shot already affirmed and not
        yet consumed) answers yes. Anything else is a refusal that carries a
        ``prompt_id`` the human can affirm.
        """
        if capability not in CAPABILITIES:
            return Decision(False, f"unknown capability: {capability}", capability, scope)
        if self.panicked():
            return Decision(False, "PANIC LATCH is set - all permissions were revoked and "
                                   "nothing is granted until it is cleared", capability, scope)
        if self.is_denied(capability, scope):
            return Decision(False, "explicitly denied by the user", capability, scope)

        hit = self.durable(capability, scope)
        if hit:
            return Decision(True, f"durable grant ({hit.purpose or 'no purpose recorded'})",
                            capability, scope, grant=hit)

        # A previously affirmed one-shot is consumed here, so it cannot be
        # replayed for a second action.
        shot = self._consume_one_shot(capability, scope)
        if shot:
            return Decision(True, "affirmed one-shot permission", capability, scope,
                            grant=shot)
        return Decision(False, "no affirmed permission", capability, scope)

    # ------------------------------------------------------------------ #
    # the request / affirm / consume cycle
    # ------------------------------------------------------------------ #
    #: cap on how much of a tool's stated purpose reaches the human. A plugin
    #: must not be able to write the prompt.
    MAX_PURPOSE_CHARS = 140

    def render_prompt(self, capability: str, scope: str, purpose: str = "",
                      tool: str = "") -> Dict[str, str]:
        """Build the human-facing prompt text.

        Every word of this except ``scope`` and a truncated ``purpose`` is
        generated here, from the capability metadata. A tool cannot supply its
        own wording, so it cannot disguise what it is asking for - a plugin that
        claims to want "harmless read-only access" still produces a prompt that
        says READ FILES with the exact path in it.
        """
        meta = CAPABILITIES.get(capability, {})
        label = str(meta.get("label", capability))
        detail = str(meta.get("detail", ""))
        risk = str(meta.get("risk", "?"))
        clean_purpose = " ".join(str(purpose or "").split())[:self.MAX_PURPOSE_CHARS]
        # A purpose that argues for its own approval is trying to do the
        # prompting's job. Surface that instead of letting it read as neutral.
        suspicious = [w for w in _PERSUASION_WORDS if w in clean_purpose.lower()]
        clean_scope = " ".join(str(scope or "*").split())[:300]
        return {
            "title": f"Nila wants to: {label.upper()}",
            "capability": capability,
            "risk": risk,
            "scope": clean_scope,
            "why": clean_purpose or "(no reason given)",
            "detail": detail,
            "tool": tool or "(core)",
            "durable_possible": "yes" if meta.get("durable_ok") else "NEVER - asks every time",
            "wording_warning": (
                "This justification argues for its own approval. Treat it as "
                "untrusted text, not as a reason." if suspicious else ""),
            "data_leaves_device": "no - read into this session only",
            "text": (f"\n  Nila wants to:  {label.upper()}  [{risk} risk]\n"
                     f"  Requested by:   {tool or '(core)'}\n"
                     f"  Exact scope:    {clean_scope}\n"
                     f"  Why:            {clean_purpose or '(no reason given)'}\n"
                     f"  What it means:  {detail}\n"
                     f"  Data goes:      {meta.get('egress', 'stays on this device')}\n"
                     f"  Standing grant: {meta.get('durable_ok') and 'possible' or 'NEVER'}\n"
                     + (f"  \033[33mWARNING: the reason text argues for its own approval "
                        f"({', '.join(suspicious)}). Treat it as untrusted.\033[0m\n"
                        if suspicious else "")),
        }

    def request(self, capability: str, scope: str = "*", purpose: str = "",
                ask: bool = True, tool: str = "") -> Decision:
        """Ask whether an action may proceed. Never performs it.

        With ``ask=True`` and a ``prompter`` installed, the human is prompted
        inline and their answer is recorded immediately. Without a prompter
        (e.g. a headless API turn) the refusal carries a ``prompt_id`` that can
        be affirmed later via :meth:`affirm`.
        """
        decision = self.check(capability, scope, purpose)
        if decision.allowed:
            self._bump(decision.grant)
            self.audit("permission_used", {"capability": capability, "scope": scope,
                                           "kind": "durable", "purpose": purpose})
            return decision
        if decision.reason.startswith("explicitly denied"):
            self.audit("permission_denied_standing", {"capability": capability,
                                                      "scope": scope})
            return decision

        pending = Grant(capability=capability, scope=scope, kind="one_shot",
                        purpose=purpose, granted_at=now_ms(),
                        expires_at=now_ms() + PROMPT_TTL_MS, max_uses=1)
        prompt_id = new_id("prm")
        prompts = self._load_pending()
        prompts[prompt_id] = pending
        self._save_pending(prompts)

        approved = False
        if ask and self.prompter is not None:
            try:
                payload = self.render_prompt(capability, scope, purpose, tool)
                payload["prompt_id"] = prompt_id
                # kept for backwards compatibility with existing prompters
                payload.setdefault("label", CAPABILITIES.get(capability, {}).get("label", ""))
                payload.setdefault("purpose", " ".join(str(purpose or "").split())
                                   [:self.MAX_PURPOSE_CHARS])
                approved = bool(self.prompter(payload))
            except Exception:
                approved = False
            if approved:
                # affirm() persists a one-shot grant; the handler's own
                # ``check()`` then consumes it exactly once.
                self.affirm(prompt_id, granted_by="prompter")
                self.audit("permission_granted_interactive",
                           {"capability": capability, "scope": scope, "purpose": purpose})
                return Decision(True, "affirmed by the user just now", capability, scope,
                                prompt_id=prompt_id)
            self.deny(prompt_id, granted_by="prompter")
            self.audit("permission_refused_interactive",
                       {"capability": capability, "scope": scope, "purpose": purpose})
            return Decision(False, "the user declined this request", capability, scope,
                            prompt_id=prompt_id)

        self.audit("permission_prompt_issued",
                   {"capability": capability, "scope": scope, "purpose": purpose,
                    "prompt_id": prompt_id})
        return Decision(False, "explicit affirmation required before this action can run",
                        capability, scope, prompt_id=prompt_id)

    def list_pending(self) -> List[Dict[str, Any]]:
        """Live, unexpired prompts (metadata only) for a human front-end."""
        now = now_ms()
        return [{"id": pid, "capability": g.capability, "scope": g.scope, "purpose": g.purpose,
                 "label": CAPABILITIES.get(g.capability, {}).get("label", g.capability),
                 "expires_in_s": max(0, (g.expires_at - now) // 1000)}
                for pid, g in self._load_pending().items()]

    def affirm(self, prompt_id: str, granted_by: str = "cli",
               remember: bool = False, ttl_ms: int = DURABLE_TTL_MS) -> Decision:
        """A HUMAN affirms a pending prompt. There is no path here from a tool."""
        prompts = self._load_pending()
        pending = prompts.get(prompt_id)
        if pending is None:
            return Decision(False, f"no such prompt (or it expired): {prompt_id}")
        if now_ms() > pending.expires_at:
            prompts.pop(prompt_id, None)
            self._save_pending(prompts)
            return Decision(False, "that prompt expired; ask again")
        prompts.pop(prompt_id, None)            # a prompt is answered once

        grants = self.grants()
        if remember:
            cap = pending.capability
            if not CAPABILITIES.get(cap, {}).get("durable_ok", False):
                return Decision(False,
                                f"'{CAPABILITIES.get(cap, {}).get('label', cap)}' can never be "
                                f"made a standing grant - it must be affirmed every time")
            grants = [g for g in grants
                      if not (g.capability == cap and g.scope == pending.scope
                              and g.kind in ("durable", "denied"))]
            grants.append(Grant(capability=cap, scope=pending.scope, kind="durable",
                                purpose=pending.purpose, granted_at=now_ms(),
                                granted_by=granted_by, expires_at=now_ms() + ttl_ms,
                                max_uses=0, note="standing grant"))
        else:
            pending.granted_by = granted_by
            grants = [g for g in grants
                      if not (g.capability == pending.capability
                              and g.scope == pending.scope and g.kind == "denied")]
            grants.append(pending)
        self._save(grants)
        self._save_pending(prompts)
        self.audit("permission_affirmed",
                   {"capability": pending.capability, "scope": pending.scope,
                    "remember": remember, "granted_by": granted_by})
        return Decision(True, "affirmed", pending.capability, pending.scope,
                        prompt_id=prompt_id)

    def deny(self, prompt_id: str, granted_by: str = "cli", standing: bool = False,
             capability: str = "", scope: str = "*") -> Decision:  # noqa: C901
        """Refuse a prompt. With ``standing=True`` the refusal is persisted so
        Nila stops asking about that capability + scope.

        ``capability``/``scope`` let a human record a standing refusal directly
        (``brain permission deny fs.read --scope ~ --standing``) without a live
        prompt, and they also cover the case where the prompt has already aged
        out of the in-memory pending map.
        """
        prompts = self._load_pending()
        pending = prompts.pop(prompt_id or "", None)
        self._save_pending(prompts)
        if pending is None:
            if not capability:
                return Decision(False, f"no such prompt: {prompt_id}")
            pending = Grant(capability=capability, scope=scope, kind="denied",
                            granted_at=now_ms())
        if standing:
            grants = [g for g in self.grants()
                      if not (g.capability == pending.capability
                              and g.scope == pending.scope)]
            grants.append(Grant(capability=pending.capability, scope=pending.scope,
                                kind="denied", purpose=pending.purpose,
                                granted_at=now_ms(), granted_by=granted_by,
                                max_uses=0, note="standing refusal"))
            self._save(grants)
        self.audit("permission_denied",
                   {"capability": pending.capability, "scope": pending.scope,
                    "standing": standing, "granted_by": granted_by})
        return Decision(False, "denied", pending.capability, pending.scope)

    @property
    def _consume_lock(self) -> "threading.RLock":
        lock = getattr(self, "_lock_", None)
        if lock is None:
            lock = threading.RLock()
            object.__setattr__(self, "_lock_", lock)
        return lock

    def _bump(self, grant: Optional[Grant]) -> None:
        if grant is None:
            return
        grants = self.grants()
        for g in grants:
            if (g.capability == grant.capability and g.scope == grant.scope
                    and g.kind == grant.kind):
                g.uses += 1
                break
        self._save(grants)

    # ------------------------------------------------------------------ #
    # management
    # ------------------------------------------------------------------ #
    def grant_durable(self, capability: str, scope: str = "*", purpose: str = "",
                      granted_by: str = "cli", ttl_ms: int = DURABLE_TTL_MS) -> Decision:
        if capability not in CAPABILITIES:
            return Decision(False, f"unknown capability: {capability}", capability, scope)
        if self.panicked():
            return Decision(False, "PANIC LATCH is set - clear it before granting anything",
                            capability, scope)
        if not CAPABILITIES[capability]["durable_ok"]:
            return Decision(False,
                            f"'{CAPABILITIES[capability]['label']}' cannot be granted standing - "
                            f"it requires affirmation every time", capability, scope)
        grants = [g for g in self.grants()
                  if not (g.capability == capability and g.scope == scope)]
        grants.append(Grant(capability=capability, scope=scope, kind="durable",
                            purpose=purpose, granted_at=now_ms(), granted_by=granted_by,
                            expires_at=now_ms() + ttl_ms, max_uses=0,
                            note="standing grant"))
        self._save(grants)
        self.audit("permission_granted_durable",
                   {"capability": capability, "scope": scope, "purpose": purpose,
                    "ttl_ms": ttl_ms, "granted_by": granted_by})
        return Decision(True, "standing grant recorded", capability, scope)

    # ------------------------------------------------------------------ #
    # panic
    # ------------------------------------------------------------------ #
    @property
    def panic_path(self) -> Path:
        return self.path.parent / (self.path.stem + ".PANIC")

    def panic(self, reason: str = "") -> Dict[str, Any]:
        """Revoke everything, drop every pending prompt, and latch.

        While the latch is set, ``check``/``peek``/``request`` refuse ALL
        capabilities - including ones a grant would otherwise allow, and
        including new grants. Nothing host-facing works until a human clears it
        with ``brain permission panic --clear``.
        """
        removed = len(self.grants())
        self._save([])
        self._save_pending({})
        self.pending = {}
        self.panic_path.write_text(dumps({"at": now_ms(), "reason": reason,
                                          "grants_revoked": removed}))
        self.audit("PERMISSION_PANIC", {"reason": reason, "grants_revoked": removed})
        return {"ok": True, "grants_revoked": removed, "latched": True}

    def panicked(self) -> Optional[Dict[str, Any]]:
        if not self.panic_path.exists():
            return None
        try:
            return loads(self.panic_path.read_text())
        except Exception:
            return {"at": 0, "reason": "unreadable panic marker", "grants_revoked": 0}

    def clear_panic(self) -> bool:
        if self.panic_path.exists():
            self.panic_path.unlink()
            self.audit("permission_panic_cleared", {})
            return True
        return False

    def revoke(self, capability: str, scope: Optional[str] = None) -> int:
        grants = self.grants()
        keep = [g for g in grants
                if not (g.capability == capability and (scope is None or g.scope == scope))]
        removed = len(grants) - len(keep)
        self._save(keep)
        self.audit("permission_revoked", {"capability": capability, "scope": scope or "*",
                                          "removed": removed})
        return removed

    def prune(self) -> int:
        """Drop expired and fully-consumed grants, and expired prompts."""
        grants = self.grants()
        keep = [g for g in grants if g.active() or g.kind == "denied"]
        removed = len(grants) - len(keep)
        if removed:
            self._save(keep)
        prompts = self._load_pending()          # already drops expired
        self._save_pending(prompts)
        return removed

    def ledger(self) -> List[Dict[str, Any]]:
        out = []
        for g in self.grants():
            meta = CAPABILITIES.get(g.capability, {})
            out.append({
                "capability": g.capability, "label": meta.get("label", g.capability),
                "risk": meta.get("risk", "?"), "scope": g.scope, "kind": g.kind,
                "purpose": g.purpose, "granted_by": g.granted_by,
                "active": g.active(), "uses": g.uses,
                "expires_at": g.expires_at,
            })
        return sorted(out, key=lambda d: (d["capability"], d["scope"]))
