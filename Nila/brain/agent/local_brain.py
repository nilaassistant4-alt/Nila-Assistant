"""Offline brain: a deterministic intent engine so the agent works with no
network, no API key and no downloads.

It is deliberately simple and *transparent* - every action it takes is a real
tool call against the encrypted memory, and it says so. When a network provider
is configured, this class becomes the fallback used whenever the provider is
unreachable, which keeps the brain useful offline (a hard requirement).
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Optional

from .providers import Completion, LLMProvider


#: Fixed, model-independent answers. These are deliberately literal: identity
#: and destructive-action refusals should read the same whatever provider is
#: configured, and should never be improvised.
IDENTITY_REPLY = (
    "I'm Nila - your private, local-first personal assistant. I'm software "
    "running on this machine, not a person, and I'm not conscious.\n"
    "Your memories are encrypted at rest and only readable inside this unlocked "
    "session. I can remember things for you, recall them, and use local tools - "
    "but I can only *ask* to touch your files, photos, shell or network. Only "
    "you can grant that.\n"
    "Right now I'm answering from the offline rule-based engine, so I work from "
    "intent patterns rather than a language model. Type `help` for commands, or "
    "set `agent.provider` to connect a model for free-form conversation."
)

BULK_DELETE_REPLY = (
    "That would delete memories permanently, so I won't do it from a phrase "
    "alone - the offline engine has no way to confirm you meant it.\n"
    "Nothing was deleted.\n"
    "To delete one record:  `brain forget <id>`  (find ids with `brain list`)\n"
    "To delete everything:  `brain forget --all --yes`\n"
    "Say `forget <something specific>` and I'll show you the matching records "
    "first."
)


class LocalBrain(LLMProvider):
    name = "local"
    needs_network = False

    def __init__(self) -> None:
        #: the owner's standing instructions for THIS turn. The intent engine has
        #: no prompt, so a rule like "call me Anna" could not reach an offline
        #: reply at all before: three tests and a live turn proved it (#2).
        self.instructions: List[str] = []

    def available(self) -> bool:
        return True

    def complete(self, system: str, messages: List[Dict[str, str]],
                 tools: Optional[List[Dict[str, Any]]] = None,
                 tools_mode: str = "auto") -> Completion:
        text = messages[-1]["content"] if messages else ""
        call = self.plan(text)
        if call:
            # A plan may be either a tool call or a direct reply. Direct replies
            # exist for two cases that must not depend on a language model being
            # configured: who Nila is, and refusing to mass-delete on a phrase.
            if call.get("reply"):
                return Completion(text=call["reply"], model="local-intent-v1",
                                  provider=self.name)
            return Completion(text="", tool_calls=[call], model="local-intent-v1",
                              provider=self.name)
        return Completion(text=self.smalltalk(text), model="local-intent-v1",
                          provider=self.name)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _strip(text: str, prefix: str) -> str:
        return text[len(prefix):].strip(" .:!?,;") if text.lower().startswith(prefix) else ""

    def plan(self, text: str) -> Optional[Dict[str, Any]]:
        t = (text or "").strip()
        low = t.lower()

        # --- identity ----------------------------------------------------- #
        # Answered from fixed text on purpose. Her identity must not depend on a
        # language model being configured, or on a model being well-behaved: an
        # offline vault should still say who is answering. The same wording
        # refuses sentience/human claims, so the answer cannot be talked into
        # overstating what it is.
        # Romanized Tamil (Tanglish) and Tamil-script forms are matched too:
        # "neenga yaaru?", "un peyar enna?", "யார் நீங்க", "உன் பெயர்".
        # The owner's stated register is Tamil/Tanglish, and an English-only
        # identity rule meant the model-independent guarantee did not hold in
        # the language she is actually addressed in.
        if re.search(r"\b(who|what) (are|r) (you|u)\b|\byour name\b|"
                     r"\b(neenga|neengal|nee|nii)\s+(yaaru|yaru|who)\b|"
                     r"\b(yaaru|yaru)\s+(neenga|nee)\b|"
                     r"\b(un|unga|umatu|unoda)\s+(peyar|peru|paer|name)\b|"
                     r"\b(peyar|peru)\s+(enna|en|what)\b|"
                     r"(யார்|யாரு)\s*(நீ|நீங்க|நீங்கள்)|"
                     r"(உன்|உங்கள்)\s*(பெயர்|பேரு)|"
                     r"\bwho am i (talking|speaking) to\b|\bintroduce yourself\b|"
                     r"\bare you (a )?(human|person|conscious|alive|real|sentient|ai|"
                     r"robot|chatbot|llm)\b", low):
            return {"id": "", "name": None, "reply": IDENTITY_REPLY}

        # --- projects ------------------------------------------------------ #
        # Matched before the generic "status" / question patterns below, which
        # would otherwise swallow them. These only touch the owner's own encrypted
        # project records.
        if re.search(r"\b(resume|continue|pick up)\b.*\b(project|work|idea)s?\b|"
                     r"\bwhat (was|were) i (working|doing)\b|\bwhere (did|was) i (leave|stop)", low):
            return {"id": "", "name": "project_resume", "args": {}}
        m = re.search(r"\b(?:status|progress|summary|summari[sz]e|next steps?)\s+(?:of|for|on)\s+"
                      r"(?:my\s+|the\s+)?(.+?)(?:\s+project)?\s*[?.!]*$", t, flags=re.I) \
            or re.search(r"\bhow(?:'s| is)\s+(?:my\s+|the\s+)?(.+?)(?:\s+project)?\s+going\b", t, flags=re.I)
        if m:
            return {"id": "", "name": "project_status", "args": {"project": m.group(1).strip()}}
        if re.search(r"\b(list|show|what are)\b.*\b(my )?projects\b|^\s*projects\s*[?.!]*$", low):
            return {"id": "", "name": "project_list", "args": {}}
        m = re.match(r"\s*(?:please\s+)?(?:save|add|start|create|new)\s+(?:a\s+|an\s+)?(?:new\s+)?"
                     r"(?:project|idea|business idea)\s*(?:called|named|:|-)?\s*(.+)$", t, flags=re.I)
        if m:
            rest = m.group(1).strip()
            parts = re.split(r"\s*(?::|\s-\s)\s*", rest, maxsplit=1)
            title = parts[0].strip()[:80] if len(parts) == 2 else " ".join(rest.split()[:6])
            return {"id": "", "name": "project_create",
                    "args": {"title": title, "idea": parts[1].strip() if len(parts) == 2 else rest}}
        m = re.match(r"\s*(?:add\s+)?note\s+(?:to|on|for)\s+(.+?)\s*:\s*(.+)$", t, flags=re.I)
        if m:
            return {"id": "", "name": "project_note",
                    "args": {"project": m.group(1).strip(), "text": m.group(2).strip()}}
        m = re.match(r"\s*(?:add\s+)?milestone\s+(?:to|for|on)\s+(.+?)\s*:\s*(.+)$", t, flags=re.I)
        if m:
            return {"id": "", "name": "project_milestone",
                    "args": {"project": m.group(1).strip(), "text": m.group(2).strip()}}

        # explicit commands (also used by the CLI slash router)
        for verb in ("remember that ", "remember ", "note that ", "note ", "save ", "store "):
            body = self._strip(low, verb)
            if body:
                cls = _classify(body)
                # keep the user's own wording after the verb, but drop a leading
                # "the todo:"-style label that only served to trigger the intent
                payload = re.sub(r"^(the )?(todo|task|note|fact|reminder)s?\s*[:\-]?\s*", "",
                                 t[len(verb):].strip(), flags=re.I)
                return {"id": "", "name": "remember",
                        "args": {"text": payload or t, "kind": cls["kind"], "tags": cls["tags"]}}

        for verb in ("what do you know about ", "what do i know about ", "recall ",
                     "search ", "find ", "look up ", "do you remember "):
            q = self._strip(low, verb)
            if q:
                return {"id": "", "name": "recall", "args": {"query": q.rstrip("?"), "limit": 5}}

        if re.fullmatch(r"(list|show) (my )?(memories|notes|tasks|facts|preferences)\??", low):
            kind = {"tasks": "task", "facts": "fact", "preferences": "preference"}.get(
                low.split()[-1].rstrip("?"), None)
            return {"id": "", "name": "list_memories", "args": {"limit": 20, "kind": kind}}

        # Tamil destructive phrases are matched explicitly. Without this a
        # Tamil-speaking owner saying "delete everything" fell through to the
        # generic offline fallback: nothing was deleted, but only because the
        # engine did not understand - safety by ignorance. That is not a guard,
        # and it would not survive a connected model that DOES understand.
        tamil_destructive = bool(re.search(
            r"நீக்கு|நீக்கவும்|அழி|அழிக்கவும்|அழிச்சுடு|அழித்துவிடு|"
            r"மறந்துவிடு|மறந்துவிடுங்கள்|மறந்துடு|மறக்கவும்|டெலிட்", low))
        tamil_bulk_scope = bool(re.search(
            r"அனைத்தையும்|அனைத்தும்|எல்லாவற்றையும்|எல்லாம்|"
            r"அத்தனையும்|முழுவதும்|ஒவ்வொன்றையும்", low))

        if tamil_destructive and tamil_bulk_scope:
            return {"id": "", "name": None, "reply": BULK_DELETE_REPLY}

        if low.startswith("forget ") or low.startswith("delete ") or \
                re.search(r"\b(delete|remove|erase|wipe|forget)\b.*\b"
                          r"(all|everything|every|entire|whole)\b.*\b"
                          r"(memor(y|ies)|notes|records|data)\b", low):
            # Bulk deletion is destructive and irreversible. It is NEVER planned
            # as a tool call from a phrase: the offline engine has no way to
            # confirm intent, so it answers with the exact command the user must
            # run instead. (Previously this path passed an `_intent` argument to
            # `recall`, which does not accept one, raising TypeError and leaving
            # the confirmation flow unreachable.)
            if re.search(r"\b(all|everything|every|entire|whole)\b", low):
                return {"id": "", "name": None, "reply": BULK_DELETE_REPLY}
            rest = t.split(None, 1)[1].strip() if len(t.split(None, 1)) > 1 else ""
            if rest:
                # Find candidates first; deletion still needs an explicit id.
                return {"id": "", "name": "recall",
                        "args": {"query": rest, "limit": 3}}

        if re.search(r"\b(what|when|who|where|how)\b.*(my|about)\b", low) or low.endswith("?"):
            q = re.sub(r"^(what|when|who|where|why|how)\s+(do|does|did|is|are|was|were)\s+", "", low)
            q = re.sub(r"[?]+$", "", q).strip()
            if q:
                return {"id": "", "name": "recall", "args": {"query": q, "limit": 5}}

        m = re.fullmatch(r"\s*(calculate|compute|calc)?\s*([-+*/%().\d eE]+)\s*=?\s*", t)
        if m and re.search(r"\d", t) and re.search(r"[-+*/%]", t):
            return {"id": "", "name": "calculate", "args": {"expression": m.group(2).strip()}}

        # Money and measurement questions reach the calculator offline, with no
        # model: the answer is arithmetic and the steps are shown ("18% of 1250",
        # "GST on 2499", "5 feet in cm"). Recognised only when the maths engine
        # understands the sentence - otherwise the turn carries on as before.
        from ..util.money import solve as _money_solve

        if re.search(r"%|சதவீத|percent|gst|ஜிஎஸ்டி|discount|தள்ளுபடி|வட்டி|interest|"
                     r"\b(in|to|மாற்று|எத்தனை)\b", low) and _money_solve(t) is not None:
            return {"id": "", "name": "calculate", "args": {"expression": t}}

        if re.search(r"\bwhat('?s| is) (the )?(time|date|day)\b|நேரம் என்ன|தேதி என்ன", low):
            return {"id": "", "name": "current_time", "args": {}}

        if re.search(r"\b(status|health|stats|how (many|much))\b", low):
            return {"id": "", "name": "memory_stats", "args": {}}

        if re.search(r"\b(session|unlock(ed)?|am i locked)\b", low):
            return {"id": "", "name": "session_status", "args": {}}

        if re.search(r"\bsync\b", low):
            return {"id": "", "name": "run_sync", "args": {"dry_run": "dry" in low}}

        if re.search(r"\bupdates?\b", low):
            return {"id": "", "name": "check_updates", "args": {}}

        if re.search(r"\bintegrations?\b", low):
            return {"id": "", "name": "list_integrations", "args": {}}

        # ---- host-device intents -------------------------------------
        # These are matched BEFORE the generic "what ... my ...?" question
        # pattern would swallow them. Every one of them is permission-gated:
        # the tool registry refuses the call until a human affirms it, so
        # recognising the intent here cannot by itself touch the filesystem.
        m = re.search(r"\b(?:read|open|show|cat|print)\s+(?:the\s+)?file\s+(\S+)", low) \
            or re.search(r"\b(?:read|open)\s+(\S*[/~]\S+)", low)
        if m:
            return {"id": "", "name": "read_file",
                    "args": {"path": m.group(1).strip().rstrip(".,;"),
                             "purpose": "you asked me to read this file"}}

        # skip a preposition so "list the files in ~/notes" yields "~/notes",
        # not "in".
        m = re.search(r"\b(?:list|show)\s+(?:the\s+)?(?:files|directory|dir|folder)"
                      r"(?:\s+(?:in|from|at|under|inside))?\s+(\S+)", low) \
            or re.search(r"\b(?:list|show)\s+(?:what(?:'s| is)\s+)?in\s+(\S*[/~]\S+)", low) \
            or re.search(r"\bwhat(?:'s| is)\s+in\s+(\S*[/~]\S+)", low)
        if m:
            return {"id": "", "name": "list_directory",
                    "args": {"path": m.group(1).strip().rstrip(".,;?"),
                             "purpose": "you asked me to list this folder"}}

        m = re.search(r"\b(?:run|execute)\s+(?:the\s+)?command\s+(.+)$", t, flags=re.I)
        if m:
            return {"id": "", "name": "run_command",
                    "args": {"command": m.group(1).strip(),
                             "purpose": "you asked me to run this command"}}

        m = re.search(r"\b(?:show|view|look at|find|search)\s+(?:my\s+|the\s+)?"
                      r"(?:photo|photos|picture|pictures|image|images|gallery)\b", low)
        if m:
            pm = re.search(r"\bin\s+(\S*[/~]\S+)", low)
            return {"id": "", "name": "search_photos",
                    "args": {"path": (pm.group(1).rstrip(".,;") if pm else ""),
                             "limit": 15,
                             "purpose": "you asked me to look in your photo gallery"}}

        return None

    # ------------------------------------------------------------------ #
    def _name_from_instructions(self) -> str:
        """The name the owner's own instruction asked for, if it said one."""
        import re as _re

        for line in self.instructions:
            m = _re.search(r"\b(?:call|address|refer to)\s+me\s+(?:as\s+)?([A-Za-z][\w'-]{0,30})",
                           line, _re.I)
            if m:
                return m.group(1)
        return ""

    def _call_them(self) -> str:
        name = self._name_from_instructions()
        return f", {name}" if name else ""

    def smalltalk(self, text: str) -> str:
        low = (text or "").lower().strip()
        if not low:
            return "I'm listening."
        if re.match(r"^(hi|hello|hey|namaste|vanakkam)\b", low):
            greeting = ("Hello. I'm Nila, your offline brain - I can remember things, recall "
                        "them, and run local tools. Try `help` for commands.")
            name = self._name_from_instructions()
            if not name:
                return greeting
            # after the "Hello" sentence, not stapled to the end of a paragraph
            return f"Hello, {name}. " + greeting[len("Hello. "):]
        if re.search(r"\bwhat do you (call|address) me\b|\bwhat(?:'s| is) my name\b", low):
            name = self._name_from_instructions()
            if name:
                return f"You asked me to call you {name}."
            return ("You have not told me a name yet. Add one with: "
                    "brain instruct add \"call me <name>\" - it is stored in your vault, "
                    "not on the wire.")
        if "help" in low:
            return ("Type `help` for the command list. Offline I can: remember/recall "
                    "memories, list, calculate, show status, run sync and check updates.")
        if re.search(r"\b(who|what) (are|r) (you|u)\b|\byour name\b", low):
            return IDENTITY_REPLY
        if "thank" in low:
            return "Anytime" + (self._call_them() or "") + "."
        return ("I'm running the offline engine, so I answered from intent patterns rather "
                "than a language model. I can still store and retrieve your memories - "
                "or configure a provider (`brain config set agent.provider ollama`) for "
                "free-form conversation.")


def _classify(body: str) -> Dict[str, Any]:
    low = body.lower()
    tags: List[str] = []
    kind = "note"
    if re.search(r"\b(prefers|likes|loves|hates|dislikes|favorite|favourite|allerg)\b", low):
        kind = "preference"
        tags.append("preference")
    if re.search(r"\b(todo|task|reminder|follow ?up|by (monday|friday|tomorrow)|deadline)\b", low):
        kind = "task"
        tags.append("task")
    if re.search(r"\b(dr\.|doctor|dentist|lawyer|manager|colleague|friend|brother|sister|amma|appa)\b", low):
        kind = "contact"
        tags.append("people")
    if re.search(r"\b(password|passphrase|pin|otp|secret|key|credential|token)\b", low):
        kind = "credential_ref"
        tags.append("sensitive")
    if re.search(r"\b(meeting|appointment|call|flight|train|birthday|anniversary)\b", low):
        kind = "event"
        tags.append("calendar")
    if re.search(r"\b(decided|decision|chose|agreed)\b", low):
        kind = "decision"
        tags.append("decision")
    if re.search(r"\b(work|project|repo|client|team)\b", low):
        tags.append("work")
    if re.search(r"\b(health|doctor|medicine|gym|diet)\b", low):
        tags.append("health")
    if re.search(r"\b(home|wifi|router|car|bank)\b", low):
        tags.append("home")
    return {"kind": kind, "tags": tags or ["inbox"]}
