"""Strict mode: nothing runs unless the owner named it, or said yes (B, part 3).

The owner's words:

    "I give an instruction that it should behave only the way I say. Listen to
     what I say, and only when I say OK should the steps I say happen."

That is not a prompt. A model that "tries to follow" a prompt can be argued out
of it by a clever message; a check that runs *before* the tool executes cannot.
So this module lives under `brain/security/`, next to the scanner and the
classifier, and the agent calls it in the same place it authorises a tool call.

How a call is judged, in order:

1. ``blocked`` -> never, whatever the message says.
2. ``allowed`` -> runs without a question (the owner put it there).
3. the owner named it in THIS message -> runs.
4. the owner answered Nila's own question with yes -> runs, and the answer is
   spent (one answer authorises one call).
5. otherwise -> nothing runs; the agent asks, in words the owner can answer.

The default state is OFF. Turning it on does not silently change any of the
owner's existing permissions; it only adds this gate.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

#: Tools that can change something. Strict mode's default posture is "ask before
#: every one of these", because they are exactly the ones a wrong sentence could
#: mis-fire. A read-only tool (search, list, recall) is not in this set: gating a
#: search would make Nila unusable and protect nothing.
WRITE_TOOLS = frozenset({
    "remember", "forget", "delete", "update_memory", "run_command",
    "write_file", "open_app", "send", "send_message", "add_event",
    "create_event", "reminder_add", "reminder_delete", "permission_affirm",
    "backup", "restore", "sync", "update", "connector_switch", "set_key",
})

#: Never allowed while strict mode is on, even if the owner names them in a
#: message. These are the tools whose whole purpose is to remove the owner from
#: the loop; "spell it out" is not enough for them.
NEVER = frozenset({"auto_approve", "grant_standing", "empty_trash_all", "wipe"})

#: Default auto-answers per tool, so the owner can say "always ask" for one tool
#: and "always go ahead" for another without turning the whole mode off.
DEFAULT_ALLOWED: List[str] = []

_YES = re.compile(r"^(yes|y|ok|okay|sure|go ahead|do it|yes do it|சரி|ஆம்|ஓகே)\b",
                  re.IGNORECASE)
_NO = re.compile(r"^(no|n|stop|don'?t|cancel|வேண்டாம்|இல்லை)\b", re.IGNORECASE)


class StrictMode:
    """Durable state, in the encrypted half of the identity blob."""

    KEY = "strict_mode"

    def __init__(self, config: Any, vault: Any):
        self.config = config
        self.vault = vault

    # -- state ---------------------------------------------------------- #
    def _load(self) -> Dict[str, Any]:
        # A locked vault raises out of `meta_get`. Swallowing that would report
        # "strict mode: off" to an owner who turned it ON - a silent downgrade of
        # a safety setting. The caller decides what to do with the error; this
        # function never invents a default when it could not read the truth.
        raw = self.vault.meta_get_strict(self.KEY, None)
        base = {"enabled": False, "allowed": list(DEFAULT_ALLOWED),
                "blocked": [], "ask": {}, "grant": None}
        if isinstance(raw, dict):
            base.update({k: v for k, v in raw.items() if k in base})
        return base

    def _save(self, state: Dict[str, Any]) -> None:
        self.vault.meta_set(self.KEY, state)
        self.vault.audit("strict_mode_changed",
                         {"enabled": bool(state.get("enabled")),
                          "allowed": list(state.get("allowed") or []),
                          "blocked": list(state.get("blocked") or [])})

    def state(self) -> Dict[str, Any]:
        """The current state, or an exception if it cannot be read (locked vault)."""
        st = self._load()
        grant = st.get("grant") or {}
        return {"enabled": bool(st["enabled"]),
                "allowed": sorted(st.get("allowed") or []),
                "blocked": sorted(st.get("blocked") or []),
                "ask": dict(st.get("ask") or {}),
                "turns_left": int(grant.get("left", 0)) if grant else 0,
                "every_write_needs_a_yes": bool(st["enabled"])}

    def enable(self, turns: Optional[int] = None) -> Dict[str, Any]:
        st = self._load()
        st["enabled"] = True
        self._save(st)
        return self.state()

    def disable(self) -> Dict[str, Any]:
        st = self._load()
        st["enabled"] = False
        st["grant"] = None
        self._save(st)
        return self.state()

    def allow(self, tool: str) -> Dict[str, Any]:
        st = self._load()
        name = str(tool or "").strip()
        if not name:
            raise ValueError("name a tool to allow")
        if name not in st["allowed"]:
            st["allowed"].append(name)
        st["blocked"] = [t for t in st.get("blocked", []) if t != name]
        self._save(st)
        return self.state()

    def deny(self, tool: str) -> Dict[str, Any]:
        st = self._load()
        name = str(tool or "").strip()
        if not name:
            raise ValueError("name a tool to deny")
        if name not in st["blocked"]:
            st["blocked"] = list(st.get("blocked", [])) + [name]
        st["allowed"] = [t for t in st.get("allowed", []) if t != name]
        self._save(st)
        return self.state()

    def set_auto(self, tool: str, decision: Optional[str]) -> Dict[str, Any]:
        """`ask`, `allow` or `block` for one tool. `None` clears the entry."""
        st = self._load()
        name = str(tool or "").strip()
        ask = dict(st.get("ask") or {})
        decision = (decision or "").strip().lower() or None
        if decision not in (None, "ask", "allow", "block"):
            raise ValueError("decision must be ask, allow or block")
        if decision in (None, "ask"):
            ask.pop(name, None)
            # "ask me" is a decision too: it must beat an older allow-list entry,
            # otherwise `auto <tool> ask` would silently keep allowing it.
            st["allowed"] = [t for t in st.get("allowed", []) if t != name]
            st["blocked"] = [t for t in st.get("blocked", []) if t != name]
        else:
            ask[name] = decision
        st["ask"] = ask
        self._save(st)
        return self.state()

    # -- the one answer that authorises one call ------------------------- #
    def grant_one(self, calls: int = 1) -> None:
        st = self._load()
        st["grant"] = {"left": int(calls)}
        self._save(st)

    def _spend_one(self) -> bool:
        st = self._load()
        grant = st.get("grant") or {}
        if int(grant.get("left") or 0) <= 0:
            return False
        grant["left"] = int(grant["left"]) - 1
        st["grant"] = grant
        self._save_no_audit(st)
        return True

    def _save_no_audit(self, state: Dict[str, Any]) -> None:
        self.vault.meta_set(self.KEY, state)

    # -- the gate ------------------------------------------------------- #
    def check(self, tool: str, owner_text: str = "",
              provider_network: bool = False) -> Dict[str, Any]:
        """`{"allowed": bool, "why": str, "ask": bool}` for one tool call.

        Called before execution. It never raises for a normal refusal - the agent
        needs a sentence to speak, not an exception to catch.

        ``provider_network`` is about the SOURCE of the call. A model reached over
        the network must not be able to drive a tool with the owner's standing
        permissions (the online lane already refuses its tool calls, but this gate
        does not rely on that one check being present); a local model is a program
        on this PC, and the owner's allow-list applies to it.
        """
        name = str(tool or "").strip()
        st = self._load()
        if name in NEVER:
            return {"allowed": False, "ask": False,
                    "why": f"'{name}' removes your supervision, so strict mode never "
                           f"runs it. Turn strict mode off first, deliberately."}
        if not st.get("enabled"):
            return {"allowed": True, "ask": False, "why": ""}
        if name in (st.get("blocked") or []):
            return {"allowed": False, "ask": False,
                    "why": f"you blocked '{name}' for strict mode"}
        auto = (st.get("ask") or {}).get(name)
        if auto == "block":
            return {"allowed": False, "ask": False,
                    "why": f"you set '{name}' to block in strict mode"}
        if provider_network:
            return {"allowed": False, "ask": True,
                    "why": (f"strict mode: the model answering this turn arrived over "
                            f"the network, so '{name}' waits for your yes")}
        if auto == "allow" or name in (st.get("allowed") or []):
            return {"allowed": True, "ask": False, "why": f"you allowed '{name}'"}
        if self._named(owner_text, name):
            return {"allowed": True, "ask": False,
                    "why": f"you named '{name}' in this message"}
        if name not in WRITE_TOOLS:
            # A tool that changes nothing (search, recall, list, status) runs. Asking
            # about a search would make strict mode unusable and protect nothing -
            # and the earlier version DID ask, because the default `auto` is None.
            return {"allowed": True, "ask": False,
                    "why": f"'{name}' only reads, so strict mode allows it"}
        if self._spend_one():
            return {"allowed": True, "ask": False,
                    "why": "you answered yes to Nila's question"}
        return {"allowed": False, "ask": True,
                "why": (f"strict mode is on: '{name}' changes something, and you "
                        f"did not ask for it by name. Say yes, or name it.")}

    #: Phrases that count as naming a tool, so the owner does not have to learn
    #: tool names. Order matters: the first match wins.
    NAMES = {
        "remember": ("remember", "save this", "note this down", "நினைவு வை"),
        "forget": ("forget", "delete the memory", "மறந்துவிடு"),
        "run_command": ("run ", "execute ", "terminal", "command", "இயக்கு"),
        "write_file": ("write a file", "save a file", "create a file", "எழுது"),
        "add_event": ("add to calendar", "calendar event", "நாட்காட்டி"),
        "create_event": ("add to calendar", "calendar event", "நாட்காட்டி"),
        "reminder_add": ("remind me", "set a reminder", "நினைவூட்டு"),
        "send": ("send it", "send this", "அனுப்பு"),
    }

    def _named(self, text: str, tool: str) -> bool:
        low = " ".join(str(text or "").lower().split())
        if not low:
            return False
        if re.search(rf"\b{re.escape(tool.lower())}\b", low):
            return True
        return any(p in low for p in self.NAMES.get(tool, ()))

    # -- reading the owner's answer ------------------------------------- #
    def is_yes(self, text: str) -> bool:
        return bool(_YES.match(" ".join(str(text or "").strip().split())))

    def is_no(self, text: str) -> bool:
        return bool(_NO.match(" ".join(str(text or "").strip().split())))
