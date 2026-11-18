"""Tool registry: the only way the agent can touch data or the outside world.

Every tool is declarative (JSON-schema-ish), allow-listed, audited and runs
inside a policy envelope. The LLM never gets raw filesystem/network access -
it can only *request* one of these named operations, and the gateway decides
whether the current session is allowed to run it.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..security.permission import (
    CAPABILITIES, CAP_EXPORT, CAP_FILE_LIST, CAP_FILE_READ, CAP_NET, CAP_PHOTO, CAP_SHELL,
)
from ..util.misc import now_ms, sha256_hex

RISK_LEVELS = ("low", "medium", "high")


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Dict[str, Any]]
    risk: str = "low"
    requires_unlock: bool = True
    step_up: bool = False          # needs a fresh re-authentication
    offline_ok: bool = True
    #: host capability this tool must have affirmed before it runs. Enforced
    #: centrally in ``ToolRegistry.call`` as well as inside the handler, so a
    #: handler that forgets to broker cannot become a hole.
    capability: str = ""
    tags: List[str] = field(default_factory=list)

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def openai_tool(self) -> Dict[str, Any]:
        return self.schema()


@dataclass
class ToolContext:
    """Everything a handler may need. Injected by the agent, never by the model."""
    memory: Any
    vault: Any
    config: Any
    session: Any = None
    agent: Any = None
    audit: Callable[[str, Dict[str, Any]], None] = lambda event, data: None
    http: Any = None
    now: Callable[[], int] = now_ms
    #: permission broker. Injected by the agent; the model can never set it.
    permissions: Any = None


class ToolError(RuntimeError):
    pass


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        self._tools[spec.name] = spec
        return spec

    def tool(self, name: str, description: str, parameters: Dict[str, Any], risk: str = "low",
             step_up: bool = False, capability: str = "", tags: Optional[List[str]] = None):
        def deco(fn: Callable[..., Dict[str, Any]]):
            self.register(ToolSpec(name=name, description=description, parameters=parameters,
                                   handler=fn, risk=risk, step_up=step_up,
                                   capability=capability, tags=tags or []))
            return fn
        return deco

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def specs(self, only_offline: bool = False) -> List[ToolSpec]:
        out = list(self._tools.values())
        if only_offline:
            out = [s for s in out if s.offline_ok]
        return sorted(out, key=lambda s: s.name)

    def schemas(self, only_offline: bool = False) -> List[Dict[str, Any]]:
        return [s.schema() for s in self.specs(only_offline)]

    def call(self, name: str, args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
        spec = self._tools.get(name)
        if spec is None:
            raise ToolError(f"unknown tool: {name}")
        if spec.requires_unlock and not (ctx.vault and ctx.vault.is_unlocked()):
            raise ToolError(f"tool '{name}' requires an unlocked session")
        if spec.step_up and not getattr(ctx.session, "step_up_ok", False):
            raise ToolError(
                f"tool '{name}' requires step-up re-authentication: run "
                f"`brain unlock --step-up` (or `brain step-up`) and retry")
        args = args or {}

        # Central capability gate - a NON-CONSUMING peek.
        #
        # Two rules make this correct:
        #   * If no broker exists, fail closed with an exception. There is no
        #     session in which host access is merely "unconfigured".
        #   * If the capability is not yet permitted, return a refusal DICT
        #     rather than raising, so the agent loop can tell the user exactly
        #     what to affirm. It must not *consume* a one-shot here, or the
        #     handler's own more precise check would find nothing left.
        if spec.capability:
            broker = getattr(ctx, "permissions", None)
            if broker is None:
                ctx.audit(f"tool:{name}", {"risk": spec.risk, "args_keys": sorted(args),
                                           "blocked": "no permission broker"})
                raise ToolError(f"tool '{name}' needs the '{spec.capability}' permission, "
                                f"but no permission broker is available in this session")
            scope = str(args.get("path") or args.get("command") or "*")

            # The never-read list is ABSOLUTE, so do not offer a prompt for a
            # path that can never be read. Previously the gate prompted first and
            # the handler refused only after the owner had affirmed - which trains
            # the owner to approve requests that cannot succeed, and leaves an
            # affirmation for a forbidden path in the audit trail. The handler
            # still re-checks; this is the same rule applied one layer earlier.
            if args.get("path"):
                from pathlib import Path as _P
                from .host_tools import _forbidden
                try:
                    blocked = _forbidden(_P(scope).expanduser())
                except Exception:
                    blocked = None
                if blocked:
                    ctx.audit(f"tool:{name}", {
                        "risk": spec.risk, "blocked": "never-read",
                        "capability": spec.capability,
                        "scope_sha256": sha256_hex(scope.encode())[:16]})
                    return {"ok": False, "error": f"refused: {blocked}",
                            "note": "this path is never readable, whatever "
                                    "permissions exist - no prompt is offered"}

            # PEEK here, because the handler underneath calls request() for the
            # same capability, and that call is what spends the approval. Doing
            # both would consume it twice and refuse an action the user had just
            # approved. The at-most-once guarantee is enforced afterwards (see
            # `spend_unused` below), so a handler that forgets cannot leave an
            # approval live - the replay hole found in audit B10.
            peek = broker.peek(spec.capability, scope)
            if not peek.allowed:
                decision = broker.request(spec.capability, scope,
                                          purpose=f"tool '{name}'", ask=False)
                ctx.audit(f"tool:{name}", {"risk": spec.risk, "args_keys": sorted(args),
                                           "blocked": "permission",
                                           "capability": spec.capability,
                                           "scope_sha256": sha256_hex(scope.encode())[:16],
                                           "reason": decision.reason})
                meta = CAPABILITIES.get(spec.capability, {})
                return {"ok": False, "error": "permission required",
                        "capability": spec.capability,
                        "label": meta.get("label", spec.capability),
                        "scope": scope, "reason": decision.reason,
                        "prompt_id": decision.prompt_id,
                        "needs_affirmation": True,
                        "how_to_affirm": (f"brain permission affirm {decision.prompt_id}"
                                          if decision.prompt_id else ""),
                        "how_to_grant": f"brain permission grant {spec.capability} "
                                        f"--scope {scope} --purpose '<why>'"}

        # ONE re-auth, ONE high-risk action - consumed here, at the point the
        # call is certain to execute. Consuming it earlier threw the grant away
        # whenever the capability gate returned a refusal dict, so the user had to
        # re-authenticate again for a permission they had not granted yet.
        if spec.step_up:
            try:
                ctx.session.step_up_ok = False
            except (AttributeError, TypeError):      # pragma: no cover - exotic ctx
                pass
        ctx.audit(f"tool:{name}", {"risk": spec.risk, "args_keys": sorted(args),
                                   "capability": spec.capability or None})
        try:
            return spec.handler(ctx, **args)
        finally:
            # At most once, whatever the handler did or forgot: an approval this
            # call could have used is dead once the call returns.
            if spec.capability and getattr(ctx, "permissions", None) is not None:
                try:
                    ctx.permissions.spend_unused(spec.capability, scope)
                except AttributeError:
                    pass


# --------------------------------------------------------------------------- #
# built-in tools
# --------------------------------------------------------------------------- #
def build_default_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(
        name="remember",
        description="Store a durable memory. Use for facts, preferences, decisions, "
                    "people, credentials locations, appointments.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "the memory content"},
                "kind": {"type": "string", "enum": ["note", "fact", "preference", "task",
                                                     "contact", "event", "decision", "credential_ref"],
                         "description": "memory type"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "title": {"type": "string"},
            },
            "required": ["text"],
        },
        risk="low", tags=["memory"],
    )
    def remember(ctx: ToolContext, text: str, kind: str = "note", tags: Optional[List[str]] = None,
                 title: str = "") -> Dict[str, Any]:
        # `instruction` is a legal enum value only so the model gets a clear
        # refusal instead of a schema error. The store enforces owner-only, and
        # that refusal must be RETURNED: an exception here escaped into the agent
        # loop and killed the turn (found while testing #2).
        from ..memory.store import InstructionError

        if str(kind).strip().lower() == "instruction":
            ctx.audit("instruction_refused", {"source": "tool", "args_keys": sorted(
                {"text": text, "title": title})})
            return {"ok": False,
                    "error": "standing instructions can only be created by the owner",
                    "how": "the owner adds or edits them in the dashboard's Instructions "
                           "panel, or with `brain instruct`"}
        try:
            rec = ctx.memory.remember(text, title=title, kind=kind, tags=tags)
        except InstructionError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "id": rec.id, "title": rec.title, "kind": rec.kind, "tags": rec.tags}

    @reg.tool(
        name="recall",
        description="Search memory (hybrid keyword + semantic). Returns the most relevant memories.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                "kind": {"type": "string"},
                "tag": {"type": "string"},
            },
            "required": ["query"],
        },
        risk="low", tags=["memory"],
    )
    def recall(ctx: ToolContext, query: str, limit: int = 5, kind: Optional[str] = None,
               tag: Optional[str] = None) -> Dict[str, Any]:
        rows = ctx.memory.search(query, limit=int(limit), kind=kind, tag=tag)
        return {"ok": True, "count": len(rows), "results": [
            {"id": r.id, "kind": r.kind, "title": r.title, "tags": r.tags,
             "body": r.body, "score": r.score, "updated_at": r.updated_at} for r in rows]}

    @reg.tool(
        name="update_memory",
        description="Edit an existing memory by id.",
        parameters={"type": "object",
                    "properties": {"id": {"type": "string"}, "text": {"type": "string"},
                                   "title": {"type": "string"},
                                   "tags": {"type": "array", "items": {"type": "string"}}},
                    "required": ["id"]},
        risk="medium", tags=["memory"],
    )
    def update_memory(ctx: ToolContext, id: str, text: Optional[str] = None,
                      title: Optional[str] = None, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        rec = ctx.memory.update(id, body=text, title=title, tags=tags)
        return {"ok": True, "id": rec.id, "version": rec.version, "title": rec.title}

    @reg.tool(
        name="forget",
        description="Delete a memory by id (tombstoned, so the deletion syncs).",
        parameters={"type": "object", "properties": {"id": {"type": "string"},
                                                    "hard": {"type": "boolean"}},
                    "required": ["id"]},
        risk="high", tags=["memory", "destructive"],
    )
    def forget(ctx: ToolContext, id: str, hard: bool = False) -> Dict[str, Any]:
        ok = ctx.memory.delete(id, hard=bool(hard))
        return {"ok": ok, "id": id, "hard": bool(hard)}

    @reg.tool(
        name="list_memories",
        description="List recent memories, optionally filtered by kind or tag.",
        parameters={"type": "object",
                    "properties": {"kind": {"type": "string"}, "tag": {"type": "string"},
                                   "limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
        risk="low", tags=["memory"],
    )
    def list_memories(ctx: ToolContext, kind: Optional[str] = None, tag: Optional[str] = None,
                      limit: int = 20) -> Dict[str, Any]:
        rows = ctx.memory.list_records(kind=kind, tag=tag, limit=int(limit))
        return {"ok": True, "count": len(rows), "results": [
            {"id": r.id, "kind": r.kind, "title": r.title, "tags": r.tags,
             "updated_at": r.updated_at} for r in rows]}

    @reg.tool(
        name="memory_stats",
        description="Counts and health of the memory vault.",
        parameters={"type": "object", "properties": {}},
        risk="low", tags=["memory", "ops"],
    )
    def memory_stats(ctx: ToolContext) -> Dict[str, Any]:
        return {"ok": True, **ctx.memory.stats()}

    @reg.tool(
        name="session_status",
        description="Report the current security session: mode, degraded flag, expiry.",
        parameters={"type": "object", "properties": {}},
        risk="low", tags=["identity", "ops"],
    )
    def session_status(ctx: ToolContext) -> Dict[str, Any]:
        sess = ctx.vault.session
        if not sess:
            return {"ok": True, "unlocked": False}
        return {"ok": True, "unlocked": True, "mode": sess.mode, "degraded": sess.degraded,
                "expires_in_s": sess.seconds_remaining(),
                "absolute_expires_in_s": sess.seconds_remaining(absolute=True),
                "user": sess.user, "voice_enrolled": ctx.vault.has_voice(),
                "totp_enrolled": bool(ctx.vault.load_totp_secret())}

    @reg.tool(
        name="current_time",
        description="Current local date and time (ISO 8601).",
        parameters={"type": "object", "properties": {}},
        risk="low", tags=["utility"],
    )
    def current_time(ctx: ToolContext) -> Dict[str, Any]:
        now = _dt.datetime.now().astimezone()
        return {"ok": True, "iso": now.isoformat(timespec="seconds"),
                "date": now.date().isoformat(), "time": now.strftime("%H:%M"),
                "weekday": now.strftime("%A"), "epoch_ms": ctx.now()}

    @reg.tool(
        name="calculate",
        description="Work out arithmetic or everyday money maths. Handles "
                    "expressions (+ - * / // % ** and parentheses) and questions "
                    "like '18% of 1250', 'GST on 2499', 'GST inside 2950', "
                    "'10% discount on 899', 'simple interest on 50000 at 8% for 3 "
                    "years', '5 feet in cm', '2 kg in lb' - in English or Tamil.",
        parameters={"type": "object", "properties": {"expression": {"type": "string"}},
                    "required": ["expression"]},
        risk="low", tags=["utility"],
    )
    def calculate(ctx: ToolContext, expression: str) -> Dict[str, Any]:
        expr = (expression or "").strip()
        # Everyday money/measurement questions first ("18% of 1250", "GST on
        # 2499", "5 feet in cm"). The plain expression path below only ever sees
        # digits, so a question that reached it became "only digits and ... are
        # allowed" - a refusal for something Nila could work out exactly.
        from ..util.money import solve as _money_solve

        solved = _money_solve(expr)
        if solved is not None:
            return solved
        if not re.fullmatch(r"[0-9+\-*/%(). eE\t]{1,200}", expr):
            raise ToolError("only digits and + - * / % ( ) . are allowed - for a "
                            "money or unit question try '18% of 1250' or '5 feet in cm'")
        if not re.search(r"\d", expr):
            raise ToolError("expression has no numbers")
        try:
            value = eval(expr, {"__builtins__": {}}, {})   # noqa: S307 - allow-listed charset
        except Exception as exc:
            raise ToolError(f"cannot evaluate: {exc}") from exc
        return {"ok": True, "expression": expr, "result": value}

    # -- calendar, read-only (stage 3) --------------------------------- #
    @reg.tool(
        name="calendar",
        description="Read the owner's calendar (an .ics file or a secret iCal "
                    "address) for a day range. Read-only: creating an event needs "
                    "OAuth, which this tool does not do.",
        parameters={"type": "object", "properties": {
            "when": {"type": "string",
                     "description": "today | tomorrow | week | a weekday | a date"}}},
        risk="low", tags=["calendar", "offline"],
    )
    def calendar(ctx: ToolContext, when: str = "today") -> Dict[str, Any]:
        from ..util import ics

        source = str((ctx.config.agent or {}).get("calendar_ics") or "")
        if not source:
            return {"ok": False, "error": "no calendar set",
                    "how": "run `brain calendar --set <path-or-ics-url>`"}
        allow = bool((ctx.config.agent or {}).get("calendar_allow_network", False))
        got = ics.load(source, allow_network=allow)
        if not got.get("ok"):
            return got
        start, end, label = ics.range_from_text(when or "today")
        data = ics.between(got["text"], start, end)
        data["label"] = label
        data["rendered"] = ics.render(data, label)
        data["source"] = got.get("source", "")
        return data

    @reg.tool(
        name="speak",
        description="Say something out loud on this device (SAPI on Windows, Piper "
                    "if a voice file is installed). Reports honestly when no "
                    "speech engine exists.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string"}}},
        risk="low", tags=["voice", "offline"],
    )
    def speak(ctx: ToolContext, text: str = "") -> Dict[str, Any]:
        from ..util import speech

        return speech.speak_text(text)

    # -- reminders (stage 2) ------------------------------------------- #
    # Rendered by code, never by a model: the owner's own sentence is what the
    # reminder says. No network, no capability, no new storage - a reminder is a
    # memory record of kind `reminder`.
    @reg.tool(
        name="add_reminder",
        description="Set a reminder for a time. Use when the user says 'remind me "
                    "in 10 minutes', 'remind me tomorrow at 9'. The time must be in "
                    "the request; if it is missing, ask for it instead of guessing.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string", "description": "what to remind about"},
            "when": {"type": "string",
                     "description": "the time words, e.g. 'in 10 minutes', "
                                    "'tomorrow at 9', 'every day at 22:00'"}},
            "required": ["text"]},
        risk="low", tags=["reminder", "offline"],
    )
    def add_reminder(ctx: ToolContext, text: str, when: str = "") -> Dict[str, Any]:
        from .reminders import ReminderBook, ReminderError, parse_when

        phrase = " ".join(x for x in (when or "", text or "") if x).strip() or text
        try:
            w = parse_when(phrase)
        except ReminderError as exc:
            return {"ok": False, "error": str(exc), "need_time": True,
                    "hint": "say the time, e.g. 'in 10 minutes' or 'at 4:30'"}
        from .reminders import next_after
        if w.repeat:
            w.next_ms = next_after(w.at_ms, w.repeat, w.weekday)
        book = ReminderBook(ctx.memory)
        rec = book.add(text or w.text, w, original=phrase)
        return {"ok": True, "id": rec.id, "title": rec.title,
                "at": rec.meta.get("at_ms"), "repeat": rec.meta.get("repeat") or "",
                "said": f"Set: {rec.title} at {rec.meta.get('at_ms')}"}

    @reg.tool(
        name="list_reminders",
        description="List pending reminders, soonest first.",
        parameters={"type": "object", "properties": {
            "include_done": {"type": "boolean"}}},
        risk="low", tags=["reminder", "offline"],
    )
    def list_reminders(ctx: ToolContext, include_done: bool = False) -> Dict[str, Any]:
        from .reminders import ReminderBook, describe
        from ..util.misc import now_ms as _now

        book = ReminderBook(ctx.memory)
        rows = book.all(include_closed=include_done)
        now = _now()
        return {"ok": True, "count": len(rows),
                "reminders": [{"id": r.id, "title": r.title, **dict(r.meta or {})}
                              for r in rows],
                "rendered": "\n".join([describe(r, now) for r in rows])}

    @reg.tool(
        name="close_reminder",
        description="Mark a reminder done, cancel it, or snooze it by a number of minutes.",
        parameters={"type": "object", "properties": {
            "id_or_number": {"type": "string"},
            "action": {"type": "string", "enum": ["done", "cancel", "snooze"]},
            "minutes": {"type": "number"}},
            "required": ["id_or_number", "action"]},
        risk="low", tags=["reminder", "offline"],
    )
    def close_reminder(ctx: ToolContext, id_or_number: str, action: str = "done",
                       minutes: float = 10) -> Dict[str, Any]:
        from .reminders import ReminderBook
        book = ReminderBook(ctx.memory)
        rec = book.get(str(id_or_number)) or book.by_number(id_or_number)
        if rec is None:
            return {"ok": False, "error": f"no reminder '{id_or_number}' - run `reminders` "
                                          f"to see the list"}
        if action == "cancel":
            book.cancel(rec)
            return {"ok": True, "id": rec.id, "cancelled": True}
        if action == "snooze":
            book.snooze(rec, float(minutes))
            return {"ok": True, "id": rec.id, "snoozed_minutes": float(minutes)}
        book.close(rec, done=True)
        return {"ok": True, "id": rec.id, "done": True}

    @reg.tool(
        name="call_integration",
        description="Call a *pre-registered* external API integration by name. "
                    "Only integrations configured by the owner are reachable; the model "
                    "cannot invent URLs.",
        parameters={"type": "object",
                    "properties": {"name": {"type": "string"}, "operation": {"type": "string"},
                                   "params": {"type": "object"}},
                    "required": ["name", "operation"]},
        risk="high", tags=["integrations", "network"],
    )
    def call_integration(ctx: ToolContext, name: str, operation: str,
                         params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        gateway = getattr(ctx.agent, "integrations", None)
        if gateway is None:
            raise ToolError("integration gateway not available")
        return gateway.call(name, operation, params or {}, ctx=ctx)

    @reg.tool(
        name="list_integrations",
        description="List configured external integrations and their operations.",
        parameters={"type": "object", "properties": {}},
        risk="low", tags=["integrations", "ops"],
    )
    def list_integrations(ctx: ToolContext) -> Dict[str, Any]:
        gateway = getattr(ctx.agent, "integrations", None)
        if gateway is None:
            return {"ok": True, "integrations": []}
        return {"ok": True, "integrations": gateway.describe()}

    @reg.tool(
        name="sync_status",
        description="Show pending/last sync state.",
        parameters={"type": "object", "properties": {}},
        risk="low", tags=["cloud", "ops"],
    )
    def sync_status(ctx: ToolContext) -> Dict[str, Any]:
        # Same wrong module path as `run_sync` (`brain.cloud.sync` does not
        # exist), so this tool raised ModuleNotFoundError on every call too.
        from ..cloud import build_sync_engine
        from ..cloud.protocol import SyncError

        try:
            engine = build_sync_engine(ctx.config, ctx.memory, ctx.vault)
        except SyncError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:      # a broken config must not kill the turn
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        status = engine.status()
        # The status carries warnings (e.g. "sync.url is set but the backend is
        # local, so nothing is uploaded"). Those must not be swallowed here.
        return {"ok": not status.get("warnings"), **status}

    @reg.tool(
        name="run_sync",
        description="Push local changes to the cloud and pull remote changes (end-to-end encrypted).",
        parameters={"type": "object", "properties": {"dry_run": {"type": "boolean"}}},
        # `net.host` was missing, so the central gate had nothing to enforce for a
        # tool that uploads encrypted blobs to a remote endpoint. The handler
        # still resolves the real host itself; this declaration makes the
        # boundary visible to the registry (and to the model).
        risk="medium", capability=CAP_NET, tags=["cloud", "network"],
    )
    def run_sync(ctx: ToolContext, dry_run: bool = False) -> Dict[str, Any]:
        # `brain.cloud.sync` does not exist: this raised ModuleNotFoundError on
        # every call, which the agent loop reported as an anomaly and halted on.
        # `build_sync_engine` lives on the package itself.
        from ..cloud import build_sync_engine
        from ..cloud.protocol import SyncError

        try:
            engine = build_sync_engine(ctx.config, ctx.memory, ctx.vault)
        except SyncError as exc:                 # bad config, not a crash
            return {"ok": False, "error": str(exc)}
        except Exception as exc:                 # nor may a surprise kill the turn
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            report = engine.sync(dry_run=bool(dry_run))
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        # A report with errors is NOT a success. `ok` used to be hard-coded True
        # and prefixed before the report, so a failed upload looked fine.
        return {**report, "ok": not report.get("errors"),
                "backend": engine.status()["backend"]}

    @reg.tool(
        name="check_updates",
        description="Check the pinned update channel for new agent/model/config bundles.",
        parameters={"type": "object", "properties": {}},
        risk="low", capability=CAP_NET, tags=["cloud", "updates", "network"],
    )
    def check_updates(ctx: ToolContext) -> Dict[str, Any]:
        from ..updates.manager import UpdateManager

        return {"ok": True, **UpdateManager(ctx.config).check()}

    @reg.tool(
        name="export_backup",
        description="Write an encrypted backup of the whole memory vault to a file. "
                    "Ciphertext only - it cannot be read without the master passphrase.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}},
                    "required": ["path"]},
        risk="high", step_up=True, capability=CAP_EXPORT, tags=["ops", "destructive"],
    )
    def export_backup(ctx: ToolContext, path: str) -> Dict[str, Any]:
        from pathlib import Path

        data = ctx.memory.export_encrypted_backup()
        out = Path(path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return {"ok": True, "path": str(out), "bytes": len(data)}

    # ------------------------------------------------------------------ #
    # host-device tools (files, directories, photos, shell)
    #
    # Each declares a capability, so the central gate in ``ToolRegistry.call``
    # refuses it until a human affirms - and each handler *also* brokers its own
    # precise scope, so the audit trail records the specific path involved.
    # ------------------------------------------------------------------ #
    from . import host_tools

    @reg.tool(
        name="read_file",
        description="Read a text file from this device. Requires the user to affirm "
                    "the specific file (or a standing grant for its folder).",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string", "description": "file path or ~/path"},
                                   "purpose": {"type": "string",
                                               "description": "one line shown to the user in the "
                                                              "permission prompt"}},
                    "required": ["path"]},
        risk="medium", capability=CAP_FILE_READ, tags=["host", "filesystem"],
    )
    def read_file(ctx: ToolContext, path: str, purpose: str = "") -> Dict[str, Any]:
        return host_tools.read_file(ctx, path=path, purpose=purpose)

    @reg.tool(
        name="list_directory",
        description="List file and folder NAMES in a directory. Returns no file contents.",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "pattern": {"type": "string",
                                               "description": "optional substring filter on names"},
                                   "purpose": {"type": "string"}},
                    "required": ["path"]},
        risk="low", capability=CAP_FILE_LIST, tags=["host", "filesystem"],
    )
    def list_directory(ctx: ToolContext, path: str, pattern: str = "",
                       purpose: str = "") -> Dict[str, Any]:
        return host_tools.list_directory(ctx, path=path, pattern=pattern, purpose=purpose)

    @reg.tool(
        name="view_photo",
        description="Inspect one image from the photo gallery. Returns metadata (size, "
                    "dimensions, EXIF make/model/date, whether GPS is present) - the image "
                    "itself is never read into the answer and never leaves the device.",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "purpose": {"type": "string"}},
                    "required": ["path"]},
        risk="high", capability=CAP_PHOTO, tags=["host", "media"],
    )
    def view_photo(ctx: ToolContext, path: str, purpose: str = "") -> Dict[str, Any]:
        return host_tools.view_photo(ctx, path=path, purpose=purpose)

    @reg.tool(
        name="search_photos",
        description="Find images in a gallery folder. Returns paths and metadata only.",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string",
                                            "description": "gallery root, default ~/Pictures"},
                                   "limit": {"type": "integer"},
                                   "purpose": {"type": "string"}}},
        risk="high", capability=CAP_PHOTO, tags=["host", "media"],
    )
    def search_photos(ctx: ToolContext, path: str = "", limit: int = 25,
                      purpose: str = "") -> Dict[str, Any]:
        return host_tools.search_photos(ctx, path=path, limit=limit, purpose=purpose)

    @reg.tool(
        name="run_command",
        description="Run a shell command on this device. ALWAYS requires fresh affirmation - "
                    "shell access can never be granted standing. Prefer a specific tool.",
        parameters={"type": "object",
                    "properties": {"command": {"type": "string"},
                                   "purpose": {"type": "string"}},
                    "required": ["command"]},
        risk="high", capability=CAP_SHELL, tags=["host", "shell"],
    )
    def run_command(ctx: ToolContext, command: str, purpose: str = "") -> Dict[str, Any]:
        return host_tools.run_command(ctx, command=command, purpose=purpose)

    from ..projects.tools import register as _register_projects
    _register_projects(reg)

    return reg


def render_tool_prompt(reg: ToolRegistry) -> str:
    lines = ["You may call these tools. Reply with JSON only when calling one.", "",
             "```json", json.dumps({"tool": "<name>", "args": {}}), "```", "", "Available tools:"]
    for spec in reg.specs():
        props = spec.parameters.get("properties", {})
        req = set(spec.parameters.get("required", []))
        sig = ", ".join(f"{k}{'*' if k in req else ''}" for k in props)
        lines.append(f"- {spec.name}({sig}) [risk={spec.risk}]: {spec.description}")
    lines.append("\nIf no tool is needed, answer in plain prose.")
    return "\n".join(lines)
