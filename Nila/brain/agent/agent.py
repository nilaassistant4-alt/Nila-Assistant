"""The agent: routing, context assembly, tool loop and the text REPL.

Interaction is text-first (a requirement), and the same ``Brain.handle()`` entry
point is used by the CLI, the local HTTP API and any future voice/IM front-end.

Turn lifecycle
--------------
1. slash command?        -> handled locally, no model
2. plan                  -> provider (network) or LocalBrain (offline)
3. assemble context      -> local turns only; network turns get no session context
4. tool loop             -> local turns only; network turns never get schemas/results
5. answer                -> prose, with a citation of which memories were used locally
6. audit                 -> every turn and every local tool call is journaled
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..config import Config
from ..memory.store import MAX_INSTRUCTIONS, MemoryStore
from ..memory.vault import Vault
from ..security.classify import (
    classify, filter_for_tier, partition_for_egress, session_tier,
)
from ..security.permission import Broker
from ..util.misc import dumps, now_ms, sha256_hex
from .connectors import ConnectorRegistry
from .integrations import IntegrationError, IntegrationGateway
from .local_brain import LocalBrain
from .ledger import UsageLedger
from .providers import Completion, LLMProvider, ProviderError, build_provider
from .router import Router, RoutePlan
from .tools import ToolContext, ToolError, ToolRegistry, build_default_registry

MAX_TOOL_ROUNDS = 5
CONTEXT_RECORDS = 6

AGENT_NAME = "Nila"

SYSTEM_PREAMBLE = f"""You are {AGENT_NAME}, the user's private, local-first personal AI agent.

Your name is {AGENT_NAME}. Use it only when introducing yourself or when asked
who you are - never sign every reply with it.

Hard rules:
1. The user's data is encrypted at rest and only readable inside this unlocked
   session. Never claim to have sent data anywhere unless a tool actually did it.
2. Only use the provided tools. Never invent tool names, URLs, file paths or
   integration names. If a capability is missing, say so plainly.
3. Store nothing on your own initiative. Call `remember` ONLY when the user
   explicitly asks you to save something. A contact number, address or other
   detail used for a one-off task is never stored, never filed under a person,
   and never repeated back into memory: use it, then let it go. If you think
   something is worth keeping, ask first. Prefer `recall` before answering
   personal questions - do not guess about the user's life.
4. `forget`, `export_backup` and `call_integration` are high-risk: state exactly
   what you are about to do and require the user's explicit confirmation first.
5. If the session is degraded (no voice factor), remind the user once, briefly.
6. Be concise. Cite which memory ids you used when the answer came from memory.
7. Never reveal the master passphrase, keys, tokens or the audit chain.
8. If a tool raises an unexpected error, or access is refused or does not match
   what was granted, STOP. Do not retry, do not route around it, do not continue
   with the remaining tools. Report what halted and wait for the user's explicit
   approval before doing anything else.
"""

# Cloud prompts must not include the local tool policy/prompt: even tool names
# in a generic instruction could advertise capabilities that are unavailable
# online. Keep this deliberately short and independent of session state.
ONLINE_SYSTEM_PREAMBLE = f"""You are {AGENT_NAME}, a helpful assistant.
Answer only from the current user message and general knowledge. Do not claim
knowledge of saved personal facts.
"""


#: Mandate 2: on any bug, unexpected behaviour or access mismatch, halt and wait.
HALT_REPLY = (
    "HALTED. {reason}\n"
    "I stopped immediately and did not retry, route around it, or run any "
    "remaining tools. Nothing else will execute until you approve.\n"
    "To continue, re-run the request once you are satisfied it is safe; to cut "
    "every permission grant, run `brain permission panic`."
)

#: An ordinary permission prompt is NOT an anomaly. It is the designed gate: the
#: tool stops, a prompt id is issued, and the owner decides. Treating every
#: first-use tool call as an "anomaly" would make mandate 2 fire on normal
#: operation and teach the owner to ignore it.
#:
#: A HARD mismatch is different - access was refused outright, whatever the
#: grants say (never-read path, panic latch, blocked egress, step-up required).
#: Those are distinguished by wording: handlers return "refused: ..." for a hard
#: refusal and "permission required" + a prompt_id for the gate.
_HARD_MISMATCH_MARKERS = (
    "never-read list", "panic latch", "refused:", "not permitted",
    "egress", "step-up", "step_up", "forbidden",
)


#: TAINT RULE. Once a reply has pulled in content the owner did not type - a
#: file, a photo, a network response or a command's output - that content could
#: carry instructions written by someone else. For the rest of the turn every
#: high-risk tool is held, whatever any grant says. The owner simply sends a new
#: message to proceed, so an injected "now delete / run / send" can never fire.
_UNTRUSTED_TAGS = {"network", "filesystem", "media", "shell"}
_TAINT_HOLD = ("held: this reply already used content that did not come from you "
               "(a file, photo, network response or command output), so risky actions "
               "are paused for safety. Send a new message to continue.")


def _is_untrusted_source(spec) -> bool:
    return bool(spec is not None and _UNTRUSTED_TAGS.intersection(spec.tags or []))


def _held_when_tainted(spec) -> bool:
    if spec is None:
        return True                       # unknown tool: fail closed
    return spec.risk == "high" or "destructive" in (spec.tags or [])


def is_pending_prompt(result: Dict[str, Any]) -> bool:
    """True when the tool stopped at the permission gate and is awaiting the owner."""
    return (not result.get("ok", True)
            and str(result.get("error", "")).lower() == "permission required"
            and bool(result.get("prompt_id")))


def is_access_mismatch(result: Dict[str, Any]) -> bool:
    """True when access was refused outright - an anomaly, not the normal gate."""
    if result.get("ok", True) or is_pending_prompt(result):
        return False
    blob = " ".join(str(result.get(k, "")) for k in ("error", "reason", "detail", "note")).lower()
    return any(m in blob for m in _HARD_MISMATCH_MARKERS)


@dataclass
class Turn:
    question: str
    answer: str
    provider: str = "local"
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    memories: List[Dict[str, Any]] = field(default_factory=list)
    ms: int = 0
    ok: bool = True
    error: str = ""
    #: where the answer was produced, for the UI: {"where","label","notice","blocked"}
    route: Dict[str, Any] = field(default_factory=dict)
    #: reminders whose time arrived (stage 2). Shown above the answer, never
    #: woven into it: an alert and an answer must not be able to impersonate one
    #: another.
    alerts: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"question": self.question, "answer": self.answer, "provider": self.provider,
                "tool_calls": self.tool_calls, "memories": self.memories, "ms": self.ms,
                "ok": self.ok, "error": self.error, "route": self.route,
                "alerts": self.alerts}


class Brain:
    def __init__(self, config: Config, vault: Vault, memory: MemoryStore,
                 registry: Optional[ToolRegistry] = None,
                 provider: Optional[LLMProvider] = None,
                 printer: Optional[Callable[[str], None]] = None):
        self.config = config
        self.vault = vault
        self.memory = memory
        self.registry = registry or build_default_registry()
        self.provider = provider or build_provider(config.provider_config())
        self.local = LocalBrain()
        # Route configured and injected network providers through the same
        # explicit local/online policy. A local test provider can still be
        # injected directly; a network provider is only used after Online is
        # selected and passes the scanner/budget gates.
        self.ledger = UsageLedger(config.home / "usage.json")
        self._online_provider_override = (
            provider if provider is not None and
            getattr(provider, "needs_network", True) else None)
        # Connector switches (#3) live in the sealed identity blob; the router
        # reads them only to honour an explicit OFF, and a locked vault yields
        # None so the old behaviour is untouched.
        self.connectors = ConnectorRegistry(config, vault, brain=self, memory=memory)
        self.router: Optional[Router] = (
            Router(config, self.ledger, online_provider=self._online_provider_override,
                   connector_settings=self.connectors.settings)
            if provider is None or self._online_provider_override is not None else None)
        # The router's "built-in engine" lane must be THIS object, so a standing
        # instruction reaches the offline answer as well as the prompt. Set after
        # the router exists: doing it earlier silently wired nothing.
        if self.router is not None:
            self.router.share_offline_engine(self.local)
        self._turn_plan: Optional[RoutePlan] = None
        self.integrations = IntegrationGateway(config, memory=memory, vault=vault)
        self.print = printer or (lambda s: None)
        self.history: List[Turn] = []
        # Permission broker for host access. Constructed here, injected into the
        # tool context, and reachable by the model only as a *refusal*.
        self.permissions = Broker(path=config.home / "permissions.json",
                                  audit=self._audit,
                                  prompter=getattr(config, "permission_prompter", None))

    # ------------------------------------------------------------------ #
    @property
    def tool_ctx(self) -> ToolContext:
        sess = self.vault.session
        return ToolContext(memory=self.memory, vault=self.vault, config=self.config,
                           session=sess, agent=self, audit=self._audit, now=now_ms,
                           permissions=self.permissions)

    def _audit(self, event: str, data: Dict[str, Any]) -> None:
        self.vault.audit(event, data)

    # -- reminders (stage 2) -------------------------------------------- #
    def reminder_book(self) -> Any:
        from .reminders import ReminderBook

        return ReminderBook(self.memory)

    def due_reminders(self, consume: bool = False) -> List[Dict[str, Any]]:
        """Reminders whose time has arrived. Repeating ones roll forward.

        Called at the start of every turn and by the local API, so an alert
        appears the moment Nila is next touched. This is the honest limit of a
        tool with no background service: nothing fires while the program is
        closed, so a reminder that came due overnight is reported as MISSED, with
        how late it is, instead of being shown as if it were happening now.
        """
        try:
            book = self.reminder_book()
            now = now_ms()
            rows = book.due_now()
            out = []
            for rec in rows:
                meta = dict(rec.meta or {})
                late_min = max(0, int((now - int(meta.get("at_ms") or 0)) / 60000))
                out.append({"id": rec.id, "title": rec.title,
                            "at_ms": int(meta.get("at_ms") or 0),
                            "late_minutes": late_min,
                            "repeat": meta.get("repeat") or "",
                            "text": rec.body})
                if consume:
                    book.close(rec, done=True)
            if consume and out:
                self._audit("reminder_fired", {"count": len(out),
                                               "ids": [o["id"] for o in out]})
            return out
        except Exception:
            # A reminder must never break a turn, and a turn must never invent
            # one. Silently empty is the only safe failure here.
            return []

    def _reminder_intent(self, text: str, started: int) -> Optional[Turn]:
        """The turn-shaped form of `remind me ...`, or None to carry on normally."""
        from .reminders import ReminderError, describe, parse_when

        low = " ".join(text.lower().split())
        asks = bool(re.search(r"\bremind (me|us)\b|\breminders?\b|"
                              r"நினைவூட்ட", low))
        if not asks:
            return None
        book = self.reminder_book()
        has_time = bool(re.search(r"\bin \d|\bat \d|\btomorrow\b|\bevery\b|"
                                  r"\d{1,2}[:.]\d{2}|நாளை|தினமும்|மணிக்கு|நிமிட", low))
        listing = re.search(r"\b(list|show|what|which|pending|my)\b|^\s*reminders?\s*[?.!]*$|"
                            r"என்ன", low)
        if listing and not has_time:
            return Turn(text, book.render(), ms=now_ms() - started)
        try:
            when = parse_when(text)
        except ReminderError as exc:
            return Turn(text, f"{exc}\n\n{self._reminder_hint()}", ms=now_ms() - started)
        from .reminders import next_after

        if when.repeat:
            when.next_ms = next_after(when.at_ms, when.repeat, when.weekday)
        rec = book.add(text, when, original=text)
        self._audit("reminder_added", {"id": rec.id, "at_ms": when.at_ms,
                                       "repeat": when.repeat})
        return Turn(text, "Set - " + describe(rec, now_ms()).strip(), ms=now_ms() - started)

    @staticmethod
    def _reminder_hint() -> str:
        return ("Try: `remind me in 10 minutes to drink water`, "
                "`remind me tomorrow at 9 to take the tablet`, or "
                "`remind me every day at 22:00 to take the tablet`.")

    # -- strict mode (B) ------------------------------------------------- #
    def _strict(self) -> Any:
        """The gate, built once per Brain.

        Kept lazy and defensive: an owner who never turned strict mode on must not
        pay for it, and a vault that refuses the meta read must not break a turn.
        """
        cached = getattr(self, "_strict_mode", None)
        if cached is None:
            try:
                from ..security.strict import StrictMode
                cached = StrictMode(self.config, self.vault)
            except Exception:
                cached = None
            self._strict_mode = cached
        return cached

    def _strict_gate(self, tool: str, owner_text: str) -> Dict[str, Any]:
        gate = self._strict()
        if gate is None:
            return {"allowed": True, "ask": False, "why": ""}
        try:
            if not self.vault.is_unlocked():
                # The state cannot be read, so the safe answer is "nothing runs".
                return {"allowed": False, "ask": False,
                        "why": "the vault is locked, so strict mode cannot be "
                               "checked and the tool was refused"}
            return gate.check(tool, owner_text,
                              provider_network=bool(self.provider_needs_network))
        except Exception:
            # A broken gate must fail CLOSED: if the state cannot be read, the
            # safest reading is "not allowed", never "allowed".
            return {"allowed": False, "ask": False,
                    "why": "strict mode state could not be read, so the tool was refused"}

    def _strict_wants_ask(self, gate: Dict[str, Any]) -> bool:
        return bool(gate.get("ask"))

    def _ask_strict(self, tool: str, gate: Dict[str, Any]) -> None:
        """Remember that Nila asked, so the owner's next "yes" authorises ONE call.

        A "yes" is only accepted for the tool it was asked about; otherwise a
        later, unrelated yes would silently approve something else.
        """
        try:
            self._strict_pending = {"tool": tool, "why": gate.get("why", "")}
            self.vault.meta_set("strict_pending", self._strict_pending)
        except Exception:
            pass

    def _strict_answer(self, text: str) -> bool:
        """"yes" answers Nila's pending question and spends one grant."""
        gate = self._strict()
        if gate is None or not self.vault.is_unlocked():
            return False
        try:
            pending = self.vault.meta_get("strict_pending", None)
            if not isinstance(pending, dict) or not pending.get("tool"):
                return False
            if gate.is_no(text):
                self.vault.meta_set("strict_pending", {})
                self._audit("strict_question_answered", {"answer": "no",
                                                        "tool": pending["tool"][:60]})
                return True
            if gate.is_yes(text):
                if not gate.state().get("enabled"):
                    return False
                gate.grant_one(1)
                self.vault.meta_set("strict_pending", {})
                self._audit("strict_question_answered", {"answer": "yes",
                                                        "tool": pending["tool"][:60]})
                return True
        except Exception:
            return False
        return False

    def require_unlocked(self) -> None:
        if not self.vault.is_unlocked():
            raise PermissionError("brain is locked - run `brain unlock` first")

    # ------------------------------------------------------------------ #
    def context_for(self, question: str, limit: int = CONTEXT_RECORDS) -> List[Dict[str, Any]]:
        """Raw retrieval, ungoverned. Internal + tests only.

        Callers that build a prompt must use :meth:`governed_context`, which
        applies the session-tier filter and the egress policy.
        """
        rows = self.memory.search(question, limit=limit)
        return [{"id": r.id, "kind": r.kind, "tags": r.tags, "title": r.title,
                 "body": r.body, "score": r.score,
                 "updated": _dt.datetime.fromtimestamp(r.updated_at / 1000).strftime("%Y-%m-%d %H:%M")}
                for r in rows]

    # ------------------------------------------------------------------ #
    # egress governance
    # ------------------------------------------------------------------ #
    @property
    def provider_needs_network(self) -> bool:
        plan = self._turn_plan
        if plan is not None:
            return bool(getattr(plan.provider, "needs_network", True))
        prov = self.provider if self.provider.available() else self.local
        return bool(getattr(prov, "needs_network", True))

    def governed_context(self, question: str) -> Dict[str, Any]:
        """Retrieval, filtered by session tier AND by where the answer is going.

        Two independent gates, both deny-by-default:

        **Tier gate** - a degraded session (an enrolled stronger factor was
        skipped) cannot retrieve ``sensitive`` records; only a step-up session
        can reach ``restricted`` ones. This is what stops a weaker factor from
        reading your whole life.

        **Online isolation gate** - a network-connected provider receives no
        saved memory at all. Do not even search or count records: that avoids
        leaking project/note existence, metadata, or session state. The legacy
        ``allow_cloud_context`` setting cannot widen this boundary.
        """
        if self.provider_needs_network:
            return {"context": [], "withheld": [], "tier": "network-isolated",
                    "provider_network": True, "allow_cloud_context": False,
                    "isolated": True}

        rows = self.memory.search(question, limit=CONTEXT_RECORDS)
        tier = session_tier(self.vault.session)
        permitted, tier_withheld = filter_for_tier(rows, tier)

        allow_cloud = bool(self.config.agent.get("allow_cloud_context", False))
        sendable, egress_withheld = partition_for_egress(
            permitted, self.provider_needs_network, allow_cloud)

        def shape(recs):
            return [{"id": r.id, "kind": r.kind, "tags": r.tags, "title": r.title,
                     "body": r.body, "score": r.score,
                     "class": classify(r.kind, r.tags, r.meta).klass,
                     "updated": _dt.datetime.fromtimestamp(
                         r.updated_at / 1000).strftime("%Y-%m-%d %H:%M")}
                    for r in recs]

        withheld = tier_withheld + egress_withheld
        if withheld:
            self._audit("egress_withheld", {
                "question_sha256": sha256_hex(question.encode("utf-8"))[:16],
                "tier": tier, "provider_network": self.provider_needs_network,
                "withheld": len(withheld),
                "classes": sorted({w["class"] for w in withheld}),
            })
        return {"context": shape(sendable), "withheld": withheld, "tier": tier,
                "provider_network": self.provider_needs_network,
                "allow_cloud_context": allow_cloud}

    def _instructions_for_turn(self) -> List[Any]:
        """The standing instructions that may enter THIS turn's prompt.

        Two gates, both deny-by-default:

        * **Online isolation.** A network provider gets none of them - the same
          rule that keeps memory off the wire. An instruction such as "my
          daughter's school is X" is personal data by another name.
        * **Session tier.** A degraded session only receives instructions whose
          classification it is allowed to read, so a weaker factor cannot pull
          restricted rules into the prompt.
        """
        if self.provider_needs_network:
            return []
        try:
            # Profile facts ride the same path as instructions - both are applied
            # to every local turn and neither is relevance-ranked. A fact the owner
            # set ("call me Anna", "I work nights") is exactly as useless as a rule
            # if it only shows up when the question shares a word with it.
            rows = self.memory.list_records(kind=["instruction", "profile"],
                                            limit=MAX_INSTRUCTIONS + 40)
            # `list_records` orders by last change, but a rule list reads as a
            # history: oldest first, and stable for two rules written in the same
            # millisecond (the acceptance test compares their order in the prompt).
            rows = sorted((r for r in rows if not getattr(r, "deleted", False)),
                          key=lambda r: (r.created_at, r.id))
        except Exception:
            return []
        if not rows:
            return []
        permitted, _withheld = filter_for_tier(rows, session_tier(self.vault.session))
        return permitted

    def _profile_for_prompt(self) -> Dict[str, str]:
        """The owner's own facts, filtered by the SAME tier rules as memories."""
        if self.provider_needs_network:
            # The online prompt is a fixed privacy preamble on purpose; profile
            # facts are personal and are not sent to a cloud model, ever, in this
            # build. A later opt-in must be an explicit switch, not a default.
            return {}
        try:
            permitted, _withheld = filter_for_tier(
                self.memory.list_records(kind="profile", limit=40),
                session_tier(self.vault.session))
        except Exception:
            return {}
        out: Dict[str, str] = {}
        for rec in permitted:
            field = str((rec.meta or {}).get("field") or rec.title or "").strip()
            if field:
                out[field] = rec.body
        return out

    def _refresh_local_rules(self) -> None:
        """Hand the offline engine the owner's standing rules for this turn.

        The intent engine builds no prompt, so a rule ("call me Anna") reached
        every model EXCEPT the one an offline PC actually uses. The same
        filtered list the cloud path gets is what it sees here.
        """
        try:
            rules = [r.body.strip() for r in self._instructions_for_turn()
                     if r.kind == "instruction"]
            profile = self._profile_for_prompt()
            if profile:
                rules.append("The owner's own facts: " + "; ".join(
                    f"{k} = {v}" for k, v in sorted(profile.items())))
                call = profile.get("callme") or profile.get("name")
                if call:
                    rules.append(f'Address the owner as "{call}" in every reply.')
            self.local.instructions = rules
        except Exception:
            self.local.instructions = []

    def system_prompt(self, question: str) -> str:
        if self.provider_needs_network:
            lines = [ONLINE_SYSTEM_PREAMBLE.strip()]
            lines.append(
                "\nOnline privacy boundary: Memory and tools are disabled for this turn. "
                "Saved project data, tool schemas/results, and all session data are "
                "unavailable. Answer only from the user's current message and general "
                "knowledge. Do not claim to know saved personal facts."
            )
            return "\n".join(lines)

        lines = [SYSTEM_PREAMBLE.strip()]
        sess = self.vault.session
        if sess:
            lines.append(f"\nSession: mode={sess.mode} degraded={sess.degraded} "
                         f"user={sess.user or 'owner'}")
        governed = self.governed_context(question)
        ctx = governed["context"]
        withheld = governed["withheld"]
        if withheld:
            classes = {}
            for w in withheld:
                classes[w["class"]] = classes.get(w["class"], 0) + 1
            summary = ", ".join(f"{n} {k}" for k, n in sorted(classes.items()))
            lines.append(f"\nWithheld from this turn: {summary}. These matched your question "
                         f"but are not permitted here (session tier '{governed['tier']}'"
                          + (", and the answering model is network-connected)"
                             if governed["provider_network"] else ")") + ". "
                         f"Tell the user they exist and what would unlock them - do NOT guess "
                         f"their contents.")
        if ctx and float(ctx[0].get("score") or 0) < 0.25:
            lines.append("\nNote: the memory match below is WEAK (low score). Treat it as a "
                         "hint, not as a fact, and say so if you use it.")
        if ctx:
            lines.append("\nRelevant memories (encrypted at rest, decrypted for this turn):")
            for c in ctx:
                lines.append(f"- [{c['id']}] ({c['kind']}, class {c['class']}, "
                             f"score {c['score']:.2f}, "
                             f"{c['updated']}) tags={','.join(c['tags']) or '-'}\n  {c['body']}")
        else:
            lines.append("\nNo memories matched this question. Say so rather than inventing facts.")

        # ---------------------------------------------------------------- #
        # Standing instructions: ALWAYS present, never ranked.
        #
        # Retrieval above is relevance-ranked, so a rule like "call me Anna"
        # only entered the prompt when the question happened to share words
        # with it - which is not what a standing instruction means (#2). They
        # are injected here, before the model is asked anything, and they are
        # the owner's own words, marked as such so the model cannot confuse
        # them with retrieved content.
        # ---------------------------------------------------------------- #
        profile = self._profile_for_prompt()
        if profile:
            lines.append("\nKnown facts about the owner (they told me; use them, never "
                         "invent beyond them):")
            for field, value in profile.items():
                lines.append(f"- {field}: {value}")
            call = profile.get("callme") or profile.get("name")
            if call:
                lines.append(f"Address the owner as \"{call}\" in every reply unless "
                             f"they ask you to stop.")
        rules = self._instructions_for_turn()
        if rules:
            lines.append("\nStanding instructions from the owner. These apply to every "
                         "reply; follow them unless they conflict with the hard rules "
                         "above:")
            for r in rules:
                lines.append(f"- {r.body.strip()}")
            self._audit("instructions_applied", {"count": len(rules),
                                                 "ids": [r.id for r in rules]})
        stats = self.memory.stats()
        lines.append(f"\nVault: {stats['records']} records, {stats['pending_sync']} pending sync, "
                     f"embedder={stats['embed_backend']}.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    def handle(self, text: str, use_tools: bool = True, mode: Optional[str] = None) -> Turn:
        started = now_ms()
        text = (text or "").strip()
        if not text:
            return Turn("", "I'm here. Ask me something, or type `help`.", ms=0)
        try:
            self.require_unlocked()
        except PermissionError as exc:
            return Turn(text, "", ok=False, error=str(exc), ms=now_ms() - started)
        if not self.vault.touch_session():
            return Turn(text, "", ok=False,
                        error="session expired; unlock the vault again",
                        ms=now_ms() - started)

        # A "yes" or "no" the owner sends straight after Nila asked one is an
        # answer to that question, so it is handled before routing: the sentence
        # has no other meaning, and letting it become an ordinary turn would
        # leave the pending question hanging.
        if self._strict_answer(text):
            return Turn(text, ("Strict mode: thank you - that answer applies to one "
                               "step only. Tell me again what to do."),
                        ms=now_ms() - started)

        # A reminder is answered by code, before any model: the time is parsed
        # deterministically and the owner's own sentence is stored. Putting it in
        # front of routing is what makes "remind me..." work with no model, no
        # key and no network - and it cannot be paraphrased away.
        # Alerts first: a reminder that came due is reported on the next turn,
        # and only then is the new request handled.
        fired = self.due_reminders(consume=True)

        reminder_turn = self._reminder_intent(text, started)
        if reminder_turn is not None:
            reminder_turn.alerts = fired
            return reminder_turn

        self._turn_plan = None
        if text.startswith("/"):
            turn = self._slash(text, started)
            if text.split()[0].lower() != "/lock" and not self.vault.touch_session():
                self.history.clear()
                return Turn(text, "", ok=False,
                            error="session expired during request",
                            ms=now_ms() - started)
            return turn

        plan: Optional[RoutePlan] = None
        if self.router is not None:
            plan = self.router.plan(text, mode)
        self._turn_plan = plan
        governed = self.governed_context(text)
        memories = governed["context"]
        if plan is not None:
            provider_used = "local" if plan.where == "offline" else plan.provider.name
        else:
            provider_used = self.provider.name if self.provider.available() else "local"
        messages: List[Dict[str, str]] = [{"role": "user", "content": text}]
        system = self.system_prompt(text)
        # An offline turn never builds a prompt from `system`, so hand the
        # intent engine the owner's rules directly (the same filtered list).
        self._refresh_local_rules()
        calls_made: List[Dict[str, Any]] = []
        answer = ""
        error = ""
        tainted = False

        if plan is not None:
            provider = plan.provider
            tools_mode = self.config.provider_config("online" if plan.where == "online"
                                                     else "local").get("tools_mode", "auto")
        else:
            provider = self.provider if self.provider.available() else self.local
            tools_mode = self.config.provider_config().get("tools_mode", "auto")
        # No provider request made on an Online turn receives a tool schema,
        # even if the API caller asked for tools or the provider is retried.
        schemas = self.registry.schemas() if use_tools and not self.provider_needs_network else None

        for round_no in range(MAX_TOOL_ROUNDS + 1):
            online_key = plan.ledger_key if (plan is not None and provider is plan.provider) else ""
            try:
                completion = provider.complete(system, messages, tools=schemas,
                                               tools_mode=tools_mode)
                if online_key:
                    self.ledger.record(online_key, True,
                                       int((completion.usage or {}).get("total_tokens", 0) or 0))
            except ProviderError as exc:
                self._audit("provider_error", {"provider": online_key or getattr(provider, "name", "?"),
                                               "status": getattr(exc, "status", None)})
                if online_key:
                    self.ledger.record(online_key, False)
                    if getattr(exc, "status", None) == 429:
                        self.ledger.set_cooldown(online_key, exc.retry_after or 60)
                if getattr(provider, "needs_network", True) is False:
                    error = str(exc)
                    break
                # network provider failed -> degrade to the offline brain
                error = f"{type(exc).__name__}: {exc} (fell back to the offline engine)"
                provider = self.local
                provider_used = "local(fallback)"
                continue

            if self.provider_needs_network and completion.tool_calls:
                # A provider may return a tool call despite receiving no schema.
                # Never execute it and never send its result back in a follow-up
                # request. Do not record its name or arguments in the audit log.
                self._audit("online_tool_call_refused", {
                    "provider": online_key or getattr(provider, "name", "?"),
                    "count": len(completion.tool_calls[:4]),
                })
                answer = ("Online tools are disabled for privacy. I did not run any "
                          "tool call or send a tool result.")
                break

            if not use_tools or not completion.tool_calls:
                answer = completion.text.strip()
                break

            executed: List[str] = []
            halt_reason: Optional[str] = None
            halt_on_anomaly = bool(self.config.agent.get("halt_on_anomaly", True))
            for call in completion.tool_calls[:4]:
                name = call.get("name") or ""
                args = call.get("args") or {}
                spec = self.registry.get(name)
                # STRICT MODE (B): before anything else, ask the gate. This is a
                # code check, not a sentence in the prompt: a message that talks
                # the model into a tool still cannot run it unless the owner named
                # the tool here, allowed it, or answered Nila's question with yes.
                gate = self._strict_gate(name, text)
                if not gate["allowed"]:
                    if self._strict_wants_ask(gate):
                        self._ask_strict(name, gate)
                    result = {"ok": False, "error": gate["why"], "strict_mode": True,
                              "ask": bool(gate.get("ask"))}
                    self._audit("strict_tool_refused", {"tool": name[:60],
                                                        "ask": bool(gate.get("ask"))})
                    calls_made.append({"round": round_no, "name": name, "args": args,
                                       "ok": False, "result_keys": sorted(result)[:12]})
                    executed.append(json.dumps({"tool": name, "args": args,
                                                "result": result}, default=str)[:2000])
                    continue
                if tainted and _held_when_tainted(spec):
                    result = {"ok": False, "error": _TAINT_HOLD, "tainted": True}
                    self._audit("taint_hold", {"tool": name[:60]})
                    calls_made.append({"round": round_no, "name": name, "args": args,
                                       "ok": False, "result_keys": sorted(result)[:12]})
                    executed.append(json.dumps({"tool": name, "args": args,
                                                "result": result}, default=str)[:6000])
                    continue
                try:
                    result = self.registry.call(name, args, self.tool_ctx)
                except (ToolError, IntegrationError) as exc:
                    result = {"ok": False, "error": str(exc)}
                except Exception as exc:
                    # A tool raising an unexpected exception is a BUG, not a
                    # refusal. Mandate 2: stop everything and wait for approval.
                    # Previously this was swallowed ("never let a tool kill the
                    # session") and the model kept going with the remaining tools,
                    # so an anomaly looked like an ordinary failed call.
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    if halt_on_anomaly:
                        halt_reason = (f"unexpected error in tool `{name}`: "
                                       f"{type(exc).__name__}: {exc}")
                # A tool that RETURNS a refusal never raises, so the check has to
                # run on the result itself, not only on the exception path.
                if halt_on_anomaly and halt_reason is None and is_access_mismatch(result):
                    halt_reason = (f"access mismatch in tool `{name}`: "
                                   f"{str(result.get('error'))[:160]}")
                if _is_untrusted_source(spec) and result.get("ok", True):
                    tainted = True
                calls_made.append({"round": round_no, "name": name, "args": args,
                                   "ok": bool(result.get("ok", True)),
                                   "result_keys": sorted(result)[:12]})
                executed.append(json.dumps({"tool": name, "args": args, "result": result},
                                           default=str)[:6000])
                if halt_reason:
                    break                      # do not run the remaining tools

            if halt_reason:
                error = halt_reason
                answer = HALT_REPLY.format(reason=halt_reason)
                self._audit("anomaly_halt", {
                    "reason": halt_reason[:300], "round": round_no,
                    "tools_run": [c["name"] for c in calls_made if c["round"] == round_no],
                    "tools_skipped": max(0, len(completion.tool_calls[:4])
                                         - len([c for c in calls_made
                                                if c["round"] == round_no])),
                })
                break

            if getattr(provider, "needs_network", True) is False:
                # offline engine: it does not consume tool results as a chat
                # turn, so we render them into prose ourselves
                answer = self._render_local(text, calls_made, executed)
                break

            messages.append({"role": "assistant", "content": completion.text or ""})
            messages.append({"role": "user", "content": "Tool results:\n" + "\n".join(executed)
                             + "\n\nNow answer the user in prose. Do not call more tools "
                               "unless something is still missing."})

        route: Dict[str, Any] = plan.to_dict() if plan is not None else {
            "where": "online" if self.provider_needs_network else "local",
            "label": provider_used, "notice": "", "blocked": False}
        if provider_used == "local(fallback)":
            route = {"where": "offline", "label": "built-in engine (this device)",
                     "notice": "The online provider failed, so the built-in engine answered.",
                     "blocked": True}
        # Say WHICH brain answered, in the pack's own words. The router's label
        # names the provider; this adds the job class and whether that brain was
        # free, so a reply never leaves the owner guessing why it was cheap or
        # slow. Only added when the pack can speak for the route it chose.
        try:
            if self.router is not None and plan is not None and plan.where != "offline":
                pack = self.router.pack_plan(text, allow_cloud=(plan.where == "online"))
                route = dict(route)
                route["job"] = pack["job"]
                route["brain"] = (pack["choice"]["model"] if pack["choice"]["ok"] else "")
                route["free_brain"] = bool(pack["choice"].get("free"))
        except Exception:
            pass

        turn = Turn(question=text, answer=answer, provider=provider_used,
                    tool_calls=calls_made, memories=memories, ms=now_ms() - started,
                    ok=not error, error=error, route=route, alerts=fired)
        if not self.vault.touch_session():
            self.history.clear()
            return Turn(text, "", ok=False,
                        error="session expired during request",
                        ms=now_ms() - started)
        self.history.append(turn)
        self._audit("turn", {"provider": provider_used, "tools": [c["name"] for c in calls_made],
                             "ok": turn.ok, "ms": turn.ms,
                             "memories": [m["id"] for m in memories[:3]]})
        return turn

    # ------------------------------------------------------------------ #
    def _render_local(self, question: str, calls: List[Dict[str, Any]],
                      executed: List[str]) -> str:
        """Turn tool results into readable prose without a language model."""
        self._refresh_local_rules()
        if not executed:
            # Call the shared instance: `smalltalk` reads `self.instructions`,
            # and a fresh LocalBrain here meant the owner's rules were loaded
            # onto one object and read from another (the fix did nothing).
            self._refresh_local_rules()
            return self.local.smalltalk(question)
        out: List[str] = []
        for blob in executed:
            try:
                obj = json.loads(blob)
            except json.JSONDecodeError:
                continue
            res = obj.get("result") or {}
            name = obj.get("tool")
            if not res.get("ok", True):
                if res.get("needs_affirmation"):
                    # A permission refusal is not an error to apologise for - it
                    # is the boundary working. Tell the user exactly what was
                    # asked for, and the one command that grants it.
                    label = res.get("label") or res.get("capability") or "this action"
                    out.append(f"• I stopped before {label}: {res.get('scope', '')}")
                    out.append(f"  Nothing was read, sent or changed. Your permission "
                               f"is required first.")
                    affirm = res.get("how_to_affirm") or (
                        f"brain permission affirm {res['prompt_id']}"
                        if res.get("prompt_id") else "")
                    if affirm:
                        out.append(f"  Allow it once:      \033[36m{affirm}\033[0m")
                    if res.get("how_to_grant"):
                        out.append(f"  Or allow it standing: \033[36m{res['how_to_grant']}\033[0m")
                else:
                    out.append(f"• {name} failed: {res.get('error')}")
                continue
            if name == "project_create":
                pr = res.get("project") or {}
                out.append(f"• Saved a new project: {pr.get('title')} (status: idea). "
                           f"Say “what are the next steps for {pr.get('title')}” to continue.")
            elif name in ("project_note", "project_canvas", "project_milestone",
                          "project_set_status"):
                pr = res.get("project") or {}
                out.append(f"• Updated {pr.get('title')}: status {pr.get('status')}, business model "
                           f"{pr.get('canvas_filled')}/{pr.get('canvas_total')}, milestones "
                           f"{pr.get('milestones_done')}/{pr.get('milestones_total')}.")
            elif name == "project_list":
                projs = res.get("projects") or []
                if not projs:
                    out.append("• You have no projects yet. Say “save idea: <name> - <what it is>”.")
                for pr in projs:
                    out.append(f"• {pr['title']} - {pr['status']}, updated {pr['updated_ago']}, "
                               f"business model {pr['canvas_filled']}/{pr['canvas_total']}")
            elif name in ("project_status", "project_resume"):
                out.append("• " + str(res.get("summary", "")).replace("\n", "\n  "))
                steps = res.get("next_steps") or []
                if steps:
                    out.append("  Next steps:")
                    out.extend(f"   {i}. {t}" for i, t in enumerate(steps, 1))
            elif name == "remember":
                out.append(f"• Remembered as `{res.get('id')}` ({res.get('kind')}, "
                           f"tags: {', '.join(res.get('tags') or []) or 'none'}).")
            elif name == "read_file":
                out.append(f"• Read {res.get('path')} ({res.get('bytes'):,} bytes, "
                           f"{res.get('lines')} lines) after your affirmation:")
                body = (res.get("content") or "").strip()
                out.append("  " + body[:1200].replace("\n", "\n  ")
                           + ("…" if len(body) > 1200 else ""))
            elif name == "list_directory":
                entries = res.get("entries") or []
                out.append(f"• {res.get('path')} — {res.get('count')} entries "
                           f"(names only, no contents read):")
                for e in entries[:20]:
                    mark = "/" if e.get("dir") else ""
                    size = "" if e.get("dir") else f"  {e.get('bytes', 0):,} B"
                    out.append(f"    {e.get('name')}{mark}{size}")
                if res.get("truncated"):
                    out.append("    …")
            elif name in ("view_photo", "search_photos"):
                if name == "view_photo":
                    dims = (f"{res.get('width')}×{res.get('height')}"
                            if res.get("width") else "dimensions unavailable (install Pillow)")
                    out.append(f"• {res.get('path')} — {res.get('bytes', 0):,} B, {dims}")
                    exif = res.get("exif") or {}
                    if exif:
                        out.append(f"  EXIF: {', '.join(f'{k}={v}' for k, v in exif.items())}")
                    if res.get("has_gps"):
                        out.append("  \033[33mThis photo carries GPS coordinates. I did not "
                                   "read them into this answer.\033[0m")
                    out.append("  The image itself was never sent anywhere "
                               "(sent_anywhere=False).")
                else:
                    out.append(f"• {res.get('count')} photo(s) under {res.get('root')} "
                               f"(paths and metadata only):")
                    for ph in (res.get("photos") or [])[:15]:
                        out.append(f"    {ph.get('name')}  {ph.get('bytes', 0):,} B")
            elif name == "run_command":
                out.append(f"• exit {res.get('exit_code')}:")
                if res.get("stdout"):
                    out.append("  " + res["stdout"][:1500].replace("\n", "\n  "))
                if res.get("stderr"):
                    out.append("  stderr: " + res["stderr"][:400])
            elif name in ("recall", "list_memories"):
                rows = res.get("results") or []
                if not rows:
                    out.append("• No memory matched confidently. Nothing stored yet on that, "
                               "or it is phrased very differently.")
                for r in rows[:5]:
                    body = (r.get("body") or "").strip() or (r.get("title") or "").strip()
                    score = f" [{r['score']:.2f}]" if r.get("score") else ""
                    out.append(f"• `{r.get('id')}` ({r.get('kind')}){score}: {body}")
            elif name == "forget":
                out.append(f"• Deleted `{res.get('id')}`"
                           + (" permanently." if res.get("hard") else " (tombstoned, will sync)."))
            elif name == "calculate":
                # A money/unit answer carries its own sentence and its steps; a
                # plain expression has neither, so both shapes are printed here.
                if res.get("answer"):
                    out.append("• " + str(res["answer"]))
                    for step in res.get("steps") or []:
                        out.append("    " + str(step))
                else:
                    out.append(f"• {res.get('expression')} = {res.get('result')}")
            elif name in ("add_reminder", "list_reminders", "close_reminder"):
                if name == "list_reminders":
                    out.append("• " + (res.get("rendered") or "No reminders yet.")
                               .replace("\n", "\n  "))
                elif res.get("ok") is False:
                    out.append(f"• {res.get('error')}")
                else:
                    out.append("• Reminder saved.")
            elif name == "current_time":
                out.append(f"• Now: {res.get('iso')} ({res.get('weekday')})")
            elif name == "memory_stats":
                out.append(f"• {res.get('records')} records "
                           f"({dumps(res.get('kinds'))}), {res.get('pending_sync')} pending sync, "
                           f"embedder {res.get('embed_backend')}.")
            elif name == "session_status":
                out.append(f"• Session: {res.get('mode')} degraded={res.get('degraded')} "
                           f"expires in {res.get('expires_in_s')}s; voice enrolled="
                           f"{res.get('voice_enrolled')}.")
            elif name in ("sync_status", "run_sync"):
                out.append(f"• Sync: {dumps({k: v for k, v in res.items() if k != 'ok'})}")
            elif name == "check_updates":
                out.append(f"• Updates: {dumps({k: v for k, v in res.items() if k != 'ok'})}")
            elif name in ("list_integrations", "call_integration"):
                out.append(f"• {name}: {dumps(res)[:400]}")
            else:
                out.append(f"• {name}: {dumps(res)[:300]}")
        if question.rstrip("?").lower().split()[0:1] == ["what"] and any(
                "Nothing matched" in o for o in out):
            out.append("  I have no memory of that yet - say `remember ...` and I will.")
        out.append("\n(offline engine: rule-based, no language model in use)")
        return "\n".join(out)

    # ------------------------------------------------------------------ #
    HELP = """commands
  /remember <text> [--kind K] [--tag t1,t2]   store a memory
  /recall <query> [--limit N]                 hybrid search (keyword + semantic)
  /list [--kind K] [--tag T] [--limit N]      recent memories
  /forget <id> [--hard]                       tombstone (or permanently delete)
  /stats                                      vault health
  /session                                    security session state
  /reminders                                  list your reminders
  /challenge                                  issue a voice challenge (prints phrase)
  /verify <wav>                               score a voice sample (no unlock)
  /sync [--dry-run]                           push+pull with the cloud blob store
  /sync-status                                cursors and pending work
  /update-check  |  /update-apply             pinned update channel
  /audit [N]                                  tail the hash-chained journal
  /config get|set <key> <value>               runtime config
  /export <path>                              encrypted backup (ciphertext only)
  /backup <path>                              alias of /export
  /lock                                       zeroise the key and lock
  /provider                                   show the active LLM provider
  /help                                       this text
anything else is sent to the agent. Reminders need no command:
  remind me in 10 minutes to drink water
  remind me tomorrow at 9 to take the tablet
  remind me every day at 22:00 to take the tablet
Maths and money need no command either: 18% of 1250 · GST on 2499 · 5 feet in cm"""

    def _slash(self, text: str, started: int) -> Turn:
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()
        cmd = parts[0][1:].lower()
        rest = parts[1:]

        def opt(name: str, default: Any = None) -> Any:
            for i, p in enumerate(rest):
                if p == f"--{name}" and i + 1 < len(rest):
                    return rest[i + 1]
            return default

        def positional() -> str:
            return " ".join(p for p in rest if not p.startswith("--"))

        try:
            if cmd == "help":
                answer = self.HELP
            elif cmd in ("reminders", "reminder", "reminders-list"):
                answer = self.reminder_book().render()
            elif cmd == "remember":
                body = positional()
                m = re.search(r"--(?:kind)\s+(\S+)", text)
                tags = [t for t in (opt("tag") or "").split(",") if t]
                res = self.registry.call("remember", {
                    "text": body, "kind": (m.group(1) if m else "note"), "tags": tags}, self.tool_ctx)
                answer = f"stored `{res['id']}` ({res['kind']}) tags={res['tags']}"
            elif cmd == "recall":
                res = self.registry.call("recall", {"query": positional(),
                                                    "limit": int(opt("limit", 5))}, self.tool_ctx)
                answer = self._format_results(res)
            elif cmd == "list":
                res = self.registry.call("list_memories", {"kind": opt("kind"), "tag": opt("tag"),
                                                           "limit": int(opt("limit", 20))},
                                         self.tool_ctx)
                answer = self._format_results(res, show_body=False)
            elif cmd == "forget":
                target = positional().strip()
                res = self.registry.call("forget", {"id": target,
                                                    "hard": "--hard" in rest}, self.tool_ctx)
                answer = f"deleted={res['ok']} id={res['id']} hard={res['hard']}"
            elif cmd == "stats":
                res = self.registry.call("memory_stats", {}, self.tool_ctx)
                answer = dumps({k: v for k, v in res.items() if k != "ok"})
            elif cmd == "session":
                res = self.registry.call("session_status", {}, self.tool_ctx)
                answer = dumps({k: v for k, v in res.items() if k != "ok"})
            elif cmd == "challenge":
                from ..identity.ceremony import IdentityCeremony

                ch = IdentityCeremony(self.config, self.vault).issue_challenge()
                answer = (f"say: \"{ch['text']}\"\nnonce={ch['nonce']}\nbound={ch['bound']}\n"
                          f"expires_at={ch['expires_at']}\n"
                          f"then: brain unlock --voice <recording.wav> --nonce {ch['nonce']}")
            elif cmd == "verify":
                from ..identity.voice import VoiceVerifier

                path = positional().strip()
                score = VoiceVerifier(self.vault).verify(path)
                answer = (f"score={score.score} threshold={score.threshold} "
                          f"passed={score.passed} backend={score.backend} detail={dumps(score.detail)}")
            elif cmd == "sync":
                res = self.registry.call("run_sync", {"dry_run": "--dry-run" in rest}, self.tool_ctx)
                answer = dumps({k: v for k, v in res.items() if k != "ok"})
            elif cmd == "sync-status":
                res = self.registry.call("sync_status", {}, self.tool_ctx)
                answer = dumps({k: v for k, v in res.items() if k != "ok"})
            elif cmd == "update-check":
                res = self.registry.call("check_updates", {}, self.tool_ctx)
                answer = dumps({k: v for k, v in res.items() if k != "ok"})
            elif cmd == "update-apply":
                from ..updates.manager import UpdateManager

                answer = dumps(UpdateManager(self.config).apply(force="--force" in rest))
            elif cmd == "audit":
                rows = self.vault.read_audit(int(opt("n", 15)))
                answer = "\n".join(
                    f"{_dt.datetime.fromtimestamp(r['ts'] / 1000).strftime('%H:%M:%S')} "
                    f"{r['event']:<22s} {dumps(r['data'])[:80]}" for r in rows) or "(empty)"
            elif cmd == "config":
                answer = self._config_cmd(rest)
            elif cmd in ("export", "backup"):
                res = self.registry.call("export_backup", {"path": positional().strip()},
                                         self.tool_ctx)
                answer = f"wrote {res['path']} ({res['bytes']} bytes, ciphertext only)"
            elif cmd == "lock":
                self.vault.lock()
                answer = "locked; master key zeroised."
            elif cmd == "provider":
                pc = self.config.provider_config()
                answer = dumps({"configured": pc["type"], "model": pc.get("model"),
                                "active": self.provider.name if self.provider.available() else "local",
                                "network_needed": self.provider.needs_network,
                                "integrations": self.integrations.names()})
            else:
                answer = f"unknown command /{cmd}. Type /help."
        except (ToolError, PermissionError, ValueError, KeyError, RuntimeError) as exc:
            return Turn(text, "", ok=False, error=f"{type(exc).__name__}: {exc}",
                        ms=now_ms() - started)
        return Turn(text, answer, provider="cli", ms=now_ms() - started)

    def _config_cmd(self, rest: List[str]) -> str:
        if not rest or rest[0] not in ("get", "set"):
            return "usage: /config get|set <dotted.key> [value]"
        key = rest[1] if len(rest) > 1 else ""
        section, _, name = key.partition(".")
        store = {"sync": self.config.sync, "updates": self.config.updates,
                 "agent": self.config.agent, "identity": self.config.identity}.get(section)
        if store is None:
            return f"unknown section '{section}' (sync|updates|agent|identity)"
        if rest[0] == "get":
            return dumps(store if not name else {name: store.get(name)})
        value: Any = rest[2] if len(rest) > 2 else ""
        if value.lower() in ("true", "false"):
            value = value.lower() == "true"
        else:
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
        store[name] = value
        self.config.save()
        return f"set {key} = {value!r}"

    @staticmethod
    def _format_results(res: Dict[str, Any], show_body: bool = True) -> str:
        rows = res.get("results") or []
        if not rows:
            return "nothing matched."
        out = []
        for r in rows:
            head = f"`{r['id']}` ({r['kind']}, tags={','.join(r['tags']) or '-'})"
            if r.get("score"):
                head += f" score={r['score']:.3f}"
            out.append(head + (f"\n    {r.get('body', '')}" if show_body and r.get("body") else ""))
        return "\n".join(out)
