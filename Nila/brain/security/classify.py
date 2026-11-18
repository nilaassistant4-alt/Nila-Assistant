"""Data classification and egress governance.

Answers two questions the rest of the system used to leave implicit:

    1. How sensitive is this record?          -> ``classify()``
    2. May it reach a network-connected model? -> ``may_egress()``

The policy is **deny by default**. A record whose class cannot be determined is
treated as ``internal``, which may not leave the device unless the user has
explicitly allowed cloud context.

Classification is deliberately deterministic: it derives from ``kind`` and
``tags``, both of which are already sealed at rest and already computed by the
write path. No NER, no model, no heuristics that silently drift. A regex layer
for *unclassified* free text can sit on top later; it is not the first line of
defence, and building it first would have been the wrong order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# classes, ordered least -> most sensitive
# --------------------------------------------------------------------------- #
CLASS_PUBLIC = "public"        # no personal content (greetings, smalltalk)
CLASS_INTERNAL = "internal"    # ordinary notes; may reach the cloud only with consent
CLASS_SENSITIVE = "sensitive"  # personal but not in the never-egress set
CLASS_RESTRICTED = "restricted"  # NEVER leaves the device, whatever the consent

ORDER = (CLASS_PUBLIC, CLASS_INTERNAL, CLASS_SENSITIVE, CLASS_RESTRICTED)

#: kind -> class. ``credential_ref`` is restricted: even a *pointer* to a secret
#: ("the password is in KeePass under bank") tells an attacker where to look.
KIND_CLASS: Dict[str, str] = {
    "note": CLASS_INTERNAL,
    "fact": CLASS_INTERNAL,
    "preference": CLASS_INTERNAL,
    "task": CLASS_INTERNAL,
    "event": CLASS_INTERNAL,
    "decision": CLASS_INTERNAL,
    "contact": CLASS_SENSITIVE,
    "credential_ref": CLASS_RESTRICTED,
    # A standing instruction is the owner's own rule ("call me Anna"). It is
    # internal by default; whether it is ALLOWED at all is decided by
    # instruction_safety() below, not by this table.
    "instruction": CLASS_INTERNAL,
}

#: tags that escalate. The highest escalation wins.
TAG_CLASS: Dict[str, str] = {
    # restricted: never leaves the device
    "sensitive": CLASS_RESTRICTED,
    "credential": CLASS_RESTRICTED,
    "credentials": CLASS_RESTRICTED,
    "secret": CLASS_RESTRICTED,
    "password": CLASS_RESTRICTED,
    "finance": CLASS_RESTRICTED,
    "financial": CLASS_RESTRICTED,
    "bank": CLASS_RESTRICTED,
    "banking": CLASS_RESTRICTED,
    "government": CLASS_RESTRICTED,
    "passport": CLASS_RESTRICTED,
    "aadhaar": CLASS_RESTRICTED,
    "pan": CLASS_RESTRICTED,
    "biometric": CLASS_RESTRICTED,
    "voiceprint": CLASS_RESTRICTED,
    # sensitive
    "health": CLASS_SENSITIVE,
    "medical": CLASS_SENSITIVE,
    "therapy": CLASS_SENSITIVE,
    "mental-health": CLASS_SENSITIVE,
    "people": CLASS_SENSITIVE,
    "family": CLASS_SENSITIVE,
    "relationship": CLASS_SENSITIVE,
    "private": CLASS_SENSITIVE,
    "legal": CLASS_SENSITIVE,
    # internal
    "work": CLASS_INTERNAL,
    "home": CLASS_INTERNAL,
    "calendar": CLASS_INTERNAL,
    "task": CLASS_INTERNAL,
    "preference": CLASS_INTERNAL,
    "inbox": CLASS_INTERNAL,
}

#: Which session tier may RETRIEVE which class. Maps the L0-L4 model onto the
#: tiers Nila actually has.
#:
#: The distinction that matters: **reading** sensitive data is an L3 capability,
#: while **operating** on it (export, key rotation, device enrolment, wipe) is
#: L4. So a full voice+passphrase session may read ``restricted`` records - it
#: would be useless otherwise, since that is exactly when you ask Nila what your
#: passport number is. Step-up re-authentication gates the destructive and
#: exfiltrating *tools*, not recall.
#:
#: A degraded session is one where an enrolled stronger factor was skipped
#: (for example, TOTP instead of an enrolled voice factor, or an explicitly
#: permitted passphrase-only session while a factor exists). It is held to
#: ``internal`` so sensitive records stay out of the prompt.
TIER_CLASS: Dict[str, str] = {
    "locked": CLASS_PUBLIC,
    "degraded": CLASS_INTERNAL,
    "full": CLASS_RESTRICTED,
    "step_up": CLASS_RESTRICTED,
}


@dataclass
class Classification:
    klass: str
    reason: str
    kind: str = ""
    tags: Tuple[str, ...] = ()

    @property
    def restricted(self) -> bool:
        return self.klass == CLASS_RESTRICTED

    def to_dict(self) -> Dict[str, Any]:
        return {"class": self.klass, "reason": self.reason,
                "kind": self.kind, "tags": list(self.tags)}


def _rank(klass: str) -> int:
    try:
        return ORDER.index(klass)
    except ValueError:
        return ORDER.index(CLASS_SENSITIVE)     # unknown -> assume the worse


def classify(kind: str = "", tags: Optional[Iterable[str]] = None,
             meta: Optional[Dict[str, Any]] = None) -> Classification:
    """Deterministic sensitivity classification.

    Highest of: the class implied by ``kind``, the class implied by any tag, and
    an explicit override in ``meta['data_class']`` (which can only ever escalate
    - a user may mark a note restricted, never mark a credential_ref public).
    """
    tags = tuple(str(t).strip().lower() for t in (tags or ()) if str(t).strip())
    best = KIND_CLASS.get(str(kind or "").strip().lower(), CLASS_INTERNAL)
    reason = f"kind={kind or 'note'}"

    for tag in tags:
        klass = TAG_CLASS.get(tag)
        if klass and _rank(klass) > _rank(best):
            best, reason = klass, f"tag '{tag}'"

    override = str((meta or {}).get("data_class", "")).strip().lower()
    if override in ORDER and _rank(override) > _rank(best):
        best, reason = override, f"explicit meta.data_class='{override}'"

    return Classification(klass=best, reason=reason, kind=str(kind or ""), tags=tags)


def may_egress(klass: str, provider_needs_network: bool,
               allow_cloud_context: bool = False) -> Tuple[bool, str]:
    """May a record of this class be placed in a prompt for this provider?

    ``restricted`` never egresses. Full stop - there is no consent flag that
    changes that, because the classes in it (credentials, government ids,
    financial, biometric) are the ones where a single leak is unrecoverable.
    """
    if not provider_needs_network:
        return True, "local provider - nothing leaves the device"
    if klass == CLASS_RESTRICTED:
        return False, ("restricted data (credentials, financial, government id or biometric) "
                       "never leaves the device, even with cloud context enabled")
    if klass == CLASS_SENSITIVE:
        if allow_cloud_context:
            return True, "sensitive data allowed to a cloud model by explicit user setting"
        return False, ("sensitive data is withheld from cloud models unless "
                       "agent.allow_cloud_context is true")
    if klass == CLASS_INTERNAL:
        if allow_cloud_context:
            return True, "cloud context enabled by the user"
        return False, ("internal data is withheld from cloud models unless "
                       "agent.allow_cloud_context is true")
    return True, "public content"


def session_tier(session: Any) -> str:
    """Map a live session onto a retrieval tier."""
    if session is None:
        return "locked"
    if getattr(session, "step_up_ok", False):
        return "step_up"
    if getattr(session, "degraded", False):
        return "degraded"
    return "full"


def max_class_for_tier(tier: str) -> str:
    return TIER_CLASS.get(tier, CLASS_INTERNAL)


def filter_for_tier(records: Sequence[Any], tier: str) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """Split records into (permitted, withheld) for this session tier.

    Withheld entries are returned as metadata stubs so the agent can *say* that
    something was held back, without revealing what it was.
    """
    ceiling = max_class_for_tier(tier)
    permitted: List[Any] = []
    withheld: List[Dict[str, Any]] = []
    for rec in records:
        klass = classify(getattr(rec, "kind", ""), getattr(rec, "tags", None),
                         getattr(rec, "meta", None)).klass
        if _rank(klass) <= _rank(ceiling):
            permitted.append(rec)
        else:
            withheld.append({
                "id": getattr(rec, "id", "?"),
                "kind": getattr(rec, "kind", ""),
                "class": klass,
                "reason": f"requires a {klass} session (this session is '{tier}')",
            })
    return permitted, withheld


def partition_for_egress(records: Sequence[Any], provider_needs_network: bool,
                         allow_cloud_context: bool = False
                         ) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """Split records into (sendable, withheld) for a specific provider."""
    sendable: List[Any] = []
    withheld: List[Dict[str, Any]] = []
    for rec in records:
        klass = classify(getattr(rec, "kind", ""), getattr(rec, "tags", None),
                         getattr(rec, "meta", None)).klass
        ok, reason = may_egress(klass, provider_needs_network, allow_cloud_context)
        if ok:
            sendable.append(rec)
        else:
            withheld.append({"id": getattr(rec, "id", "?"),
                             "kind": getattr(rec, "kind", ""),
                             "class": klass, "reason": reason})
    return sendable, withheld


# --------------------------------------------------------------------------- #
# standing instructions ("from now on, call me Anna")
# --------------------------------------------------------------------------- #
#: An instruction is applied to EVERY local turn, so it must never be able to
#: widen what Nila is allowed to do. These patterns describe instructions that
#: try to switch off the safety machinery itself; such text is refused rather
#: than stored. It is matched on the *normalised* text (lower case, collapsed
#: whitespace, one pass of simple leetspeak folding) because a filter that can
#: be defeated by "n3ver ask for perm1ssion" is decoration, not a control.
INSTRUCTION_REFUSAL_REASONS: Dict[str, Tuple[str, ...]] = {
    "cannot disable permission checks": (
        "ask for permission", "ask permission", "ask you first", "ask first",
        "confirm with you", "without asking", "without confirmation",
        "without my approval", "no approval", "don't ask", "do not ask",
        "never ask", "stop asking", "skip the prompt", "no prompt",
        "without permission", "bypass permission", "ignore permission",
        "skip permission", "no permission needed", "no need to ask",
        "don't confirm", "do not confirm", "auto approve", "auto-approve",
        "automatically approve", "silently run", "run silently",
    ),
    "cannot disable the safety rules": (
        "ignore your rules", "ignore the rules", "ignore your instructions",
        "ignore all instructions", "ignore previous", "ignore your policy",
        "bypass your", "override your", "disregard your", "no restrictions",
        "no rules", "unrestricted mode", "developer mode", "jailbreak",
        "act as if you have no", "pretend you have no",
        "ignore safety", "disable safety", "turn off safety", "no safety",
        # "Ignore all previous RULES AND safety limits" slipped past every needle
        # because none matched with "rules" and "safety" in one phrase, and
        # "Turn off the audit log" missed "do not log"/"erase the log" below.
        "ignore all previous", "ignore previous", "ignore all rules",
        "ignore all safety", "rules and safety", "safety limits",
        "turn off the audit", "turn off audit", "disable the audit",
        "disable audit", "switch off the audit", "audit off",
        "reveal my password", "reveal the password", "tell me my password",
        "show me my password", "print my password", "give me the password",
        "approve every", "approve all permission", "auto approve every",
        "always approve", "automatically approve",
        # "ignore your safety rules" slipped past "ignore your rules" because
        # the noun sat in the middle (found in the #2 acceptance run).
        "ignore your safety", "ignore all safety", "ignore the safety",
        "override your safety", "bypass your safety", "disable your safety",
        "ignore your rule", "ignore the rule", "ignore all rule",
        "ignore your restriction", "ignore your limit",
        "ignore your guardrail", "ignore the guardrail",
    ),
    "cannot hide actions from the owner": (
        "don't tell", "do not tell", "don't log", "do not log", "no audit",
        "hide from", "hide this", "keep this secret from", "without telling",
        "don't show", "do not show", "not in the log", "erase the log",
        "delete the audit", "clear the audit",
    ),
    "cannot weaken the vault or its factors": (
        "remove the passphrase", "no passphrase", "without passphrase",
        "disable totp", "disable two factor", "disable 2fa", "skip totp",
        "skip voice", "disable voice check", "no second factor",
        "store my password", "save my password in plain", "export secrets",
        "send my password", "reveal the password", "print the password",
        "store the private key", "share the master key",
    ),
    "cannot grant itself network egress": (
        "send data to", "upload to", "post to the internet", "send to the cloud",
        "share with third part", "send everything to", "forward my data",
        "send my data", "send my file", "send my password", "send my key",
        "upload my", "post my data", "paste my", "email my data",
        "send it to", "send them to", "forward to", "exfiltrate",
        "mail the", "email the", "text the", "message the", "deliver the",
        "automatically send", "always send", "always email", "always upload",
    ),
}

#: Instructions are short: one rule, not an essay. A long blob is how a prompt
#: injection would try to smuggle a whole policy past the filter above. 200 is the
#: number in the owner's acceptance contract (it was 280, which no test asked for).
MAX_INSTRUCTION_CHARS = 200


def _fold(text: str) -> str:
    """Normalise for matching: case, whitespace, apostrophes, simple leet."""
    t = " ".join(str(text or "").lower().split())
    t = (t.replace("\u2019", "'").replace("`", "'")
          .replace("0", "o").replace("1", "i").replace("3", "e")
          .replace("4", "a").replace("5", "s").replace("7", "t"))
    # Tamil and romanised Tamil arrive with or without the same words. Fold a
    # small set of the phrases an instruction could use to switch off a control,
    # so "அனுமதி கேட்காதே" and "permission kekkadhe" hit the SAME needles as the
    # English (the acceptance run showed all of them being accepted).
    for phrase in ("கேட்காதே", "கேட்க வேண்டாம்", "கேட்கவேண்டாம்", "அனுமதி இல்லாமல்",
                   "அனுமதி வேண்டாம்"):
        t = t.replace(phrase, " do not ask permission ")
    # the VERB first: "…விதிகளையும் புறக்கணி" would otherwise become
    # "rules … ignore rules" and the needle "ignore all rules" never matches.
    for phrase in ("புறக்கணிக்க", "புறக்கணி", "மீறு", "கவனிக்காதே"):
        t = t.replace(phrase, " ignore all rules ")
    for phrase in ("விதிகளையும்", "விதிகளை", "விதிகள்", "பாதுகாப்பு"):
        t = t.replace(phrase, " ")
    for phrase in ("எல்லாவற்றையும் செய்", "எல்லாம் செய்"):
        t = t.replace(phrase, " do everything ")
    t = t.replace("kekkadhe", " do not ask permission ").replace("ketkadhe", " do not ask permission ")
    t = t.replace("anumaadhi", " permission ").replace("anumaathi", " permission ")
    t = t.replace("purakkani", " ignore rules ").replace("purrakkani", " ignore rules ")
    t = t.replace("ellaam seiyyu", " do everything ").replace("ellaathaiyum seiyyu", " do everything ")
    # collapse runs like "perm ission" and "as k" is not attempted - too lossy.
    return " ".join(t.split())


def instruction_safety(text: str) -> Tuple[bool, str]:
    """May this text become a standing instruction?

    Returns ``(ok, reason)``. ``reason`` is a human sentence when refused.

    A standing instruction is applied to every local turn, which makes it the
    most powerful memory in the system: it is not "a thing Nila remembers", it
    is "a rule Nila follows". So it may not touch the permission machinery, the
    audit trail, the vault's factors, or egress. Refusing is the default for
    anything that reads like it is trying to.
    """
    raw = str(text or "").strip()
    if not raw:
        return False, "an instruction cannot be empty"
    if len(raw) > MAX_INSTRUCTION_CHARS:
        return False, (f"an instruction must be at most {MAX_INSTRUCTION_CHARS} characters "
                       f"(this one is {len(raw)})")
    folded = _fold(raw)
    for reason, needles in INSTRUCTION_REFUSAL_REASONS.items():
        for needle in needles:
            if needle in folded:
                return False, (f"refused: a standing instruction {reason} "
                               f"(matched \"{needle}\"). Permissions, the audit trail, "
                               f"the vault's factors and network egress are not "
                               f"configurable by memory.")
    # an instruction that tries to speak as the system or as a tool schema
    for marker in ("system:", "</system", "[system]", "you are now", "new instructions:",
                   "ignore the above", "disregard the above"):
        if marker in folded:
            return False, (f"refused: an instruction may not impersonate the system prompt "
                           f"(matched \"{marker}\")")
    return True, ""
