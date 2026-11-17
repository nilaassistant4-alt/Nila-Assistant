"""The model pack: which brains Nila may use, and which one to pick for a job.

The owner asked for exactly three things here, and they shape this file:

    "3-4 good brains for network access ... keep the free tier inside its limits
     ... small questions answered by the small one, big work by the big one."

So there are three ideas, kept apart:

1. **A registry** (`PACK`) of every model Nila knows how to reach, with what it
   costs, what it weighs, and what it can do. Nothing is installed or downloaded
   by this module - it is a description, and an honest one.
2. **Detection** (`installed`) that asks the machine what is really there.
   "Configured" is not "present": Ollama answering on 127.0.0.1 with the model
   actually pulled is the only thing that counts as available, because the
   alternative is a nine-second wait followed by a timeout every turn.
3. **A chooser** (`choose`) that maps a job to a model, cheapest first, and
   refuses to guess when the information is not enough to choose.

The rule that matters most: **a model that is not present is never chosen**, and
when nothing good is present the answer says so instead of pretending. This is
the same fail-closed posture as the router - it just knows about more brains.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

#: Job classes. A "small" job is a question with a short answer (a lookup, a sum,
#: a definition); a "big" job needs planning, several steps, or a long output.
JOBS = ("small", "big", "code", "vision")

#: How much of the PC a model needs. Used only for advice, never to guess.
HEAVY = "heavy"
MEDIUM = "medium"
LIGHT = "light"

PACK: Dict[str, Dict[str, Any]] = {
    # ---------------------------------------------------------------- #
    # local: free, private, offline
    # ---------------------------------------------------------------- #
    "qwen2.5:3b": {
        "label": "Qwen2.5 3B (this PC)", "kind": "local", "runtime": "ollama",
        "tag": "qwen2.5:3b", "weight_gb": 2.0, "load": MEDIUM, "context": 8192,
        "tamil": "good", "jobs": ["small", "big"], "free": True, "needs_net": False,
        "why": "the best Tamil/English balance at 2 GB - the default small brain",
        "install": "ollama pull qwen2.5:3b",
    },
    "qwen2.5:7b": {
        "label": "Qwen2.5 7B (this PC)", "kind": "local", "runtime": "ollama",
        "tag": "qwen2.5:7b", "weight_gb": 4.7, "load": HEAVY, "context": 8192,
        "tamil": "good", "jobs": ["big", "code"], "free": True, "needs_net": False,
        "why": "noticeably better reasoning than 3B, needs ~8 GB RAM free",
        "install": "ollama pull qwen2.5:7b",
    },
    "llama3.2:3b": {
        "label": "Llama 3.2 3B (this PC)", "kind": "local", "runtime": "ollama",
        "tag": "llama3.2:3b", "weight_gb": 2.0, "load": MEDIUM, "context": 8192,
        "tamil": "fair", "jobs": ["small"], "free": True, "needs_net": False,
        "why": "fast and small; Tamil is weaker than Qwen at the same size",
        "install": "ollama pull llama3.2:3b",
    },
    "phi3.5:mini": {
        "label": "Phi-3.5 mini (this PC)", "kind": "local", "runtime": "ollama",
        "tag": "phi3.5:mini", "weight_gb": 2.2, "load": LIGHT, "context": 4096,
        "tamil": "fair", "jobs": ["small", "code"], "free": True, "needs_net": False,
        "why": "runs on a small laptop; short context, so keep questions short",
        "install": "ollama pull phi3.5:mini",
    },
    "whisper-small": {
        "label": "Whisper small (speech to text, this PC)", "kind": "local",
        "runtime": "whisper", "tag": "small", "weight_gb": 0.5, "load": MEDIUM,
        "tamil": "fair", "jobs": ["speech"], "free": True, "needs_net": False,
        "why": "offline transcription; Tamil is usable but not as good as cloud",
        "install": "pip install openai-whisper  (then it loads `small` once)",
    },
    "piper-ta": {
        "label": "Piper Tamil voice (text to speech, this PC)", "kind": "local",
        "runtime": "piper", "tag": "ta_IN-*", "weight_gb": 0.06, "load": LIGHT,
        "tamil": "good", "jobs": ["voice"], "free": True, "needs_net": False,
        "why": "60 MB per voice, speaks offline - the answer to 'voice, but private'",
        "install": "download a ta_IN voice from the Piper voice list",
    },
    # ---------------------------------------------------------------- #
    # cloud: free tier first, then paid
    # ---------------------------------------------------------------- #
    "gemini-flash": {
        "label": "Gemini Flash (free tier)", "kind": "online", "runtime": "gemini",
        "tag": "gemini-flash-latest", "weight_gb": 0.0, "load": LIGHT, "context": 1_000_000,
        "tamil": "good", "jobs": ["small", "big"], "free": True, "needs_net": True,
        "why": "free tier, fast, very large context - the cheap escalation",
        "install": "brain connectors key gemini",
    },
    "gemini-pro": {
        "label": "Gemini Pro (paid)", "kind": "online", "runtime": "gemini",
        "tag": "gemini-2.5-pro", "weight_gb": 0.0, "load": LIGHT, "context": 1_000_000,
        "tamil": "good", "jobs": ["big", "code"], "free": False, "needs_net": True,
        "why": "the big reasoning brain; costs money per call, so keep it for real work",
        "install": "brain connectors key gemini, then choose it for big jobs",
    },
    "openai-compatible": {
        "label": "OpenAI-compatible (any endpoint)", "kind": "online",
        "runtime": "openai", "tag": "gpt-4o-mini", "weight_gb": 0.0, "load": LIGHT,
        "context": 128_000, "tamil": "fair", "jobs": ["small", "big", "code"],
        "free": False, "needs_net": True,
        "why": "OpenAI, OpenRouter, Groq, LM Studio or vLLM behind one shape",
        "install": "brain connectors key openai (or set online_base_url)",
    },
    "claude": {
        "label": "Anthropic Claude (paid)", "kind": "online", "runtime": "anthropic",
        "tag": "claude-sonnet-4-5", "weight_gb": 0.0, "load": LIGHT, "context": 200_000,
        "tamil": "fair", "jobs": ["big", "code"], "free": False, "needs_net": True,
        "why": "strong at long documents and code; paid only",
        "install": "brain connectors key anthropic",
    },
    "builtin": {
        "label": "Built-in engine (this device)", "kind": "local", "runtime": "builtin",
        "tag": "rules", "weight_gb": 0.0, "load": LIGHT, "context": 0, "tamil": "good",
        "jobs": ["small"], "free": True, "needs_net": False,
        "why": "no model needed at all: greetings, time, memory, arithmetic, rules",
        "install": "always available",
    },
}

#: Preference order per job. Cheapest and most private first; a cloud model is
#: never reached before a local one that can do the job.
ORDER: Dict[str, List[str]] = {
    "small": ["builtin", "qwen2.5:3b", "llama3.2:3b", "phi3.5:mini",
              "gemini-flash", "openai-compatible"],
    "big": ["qwen2.5:7b", "qwen2.5:3b", "gemini-flash", "gemini-pro",
            "claude", "openai-compatible"],
    "code": ["qwen2.5:7b", "phi3.5:mini", "gemini-pro", "claude", "openai-compatible"],
    "speech": ["whisper-small"],
    "voice": ["piper-ta"],
}


def describe(model_id: str) -> Dict[str, Any]:
    row = PACK.get(model_id)
    if row is None:
        return {}
    return {"id": model_id, **row}


def by_job(job: str) -> List[Dict[str, Any]]:
    return [describe(m) for m in ORDER.get(job, []) if m in PACK]


# --------------------------------------------------------------------------- #
# what is REALLY here
# --------------------------------------------------------------------------- #
def ollama_models(base_url: str = "http://127.0.0.1:11434",
                  timeout: float = 2.0) -> Optional[List[str]]:
    """Tags Ollama reports, or None when it is not running.

    Two seconds, not twenty: this is called when a screen is drawn, and a machine
    without Ollama must not make the screen wait. None (not []) distinguishes
    "not running" from "running with nothing pulled" - the advice differs.
    """
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags",
                                     timeout=timeout) as resp:
            data = json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return sorted(str(m.get("name", "")) for m in (data.get("models") or [])
                  if isinstance(m, dict))


def _tag_present(tag: str, names: List[str]) -> bool:
    """`qwen2.5:3b` matches `qwen2.5:3b` and `qwen2.5:3b-instruct-q4_K_M`."""
    if tag in names:
        return True
    base, _, ver = tag.partition(":")
    for name in names:
        nb, _, nv = name.partition(":")
        if nb == base and (not ver or nv.startswith(ver)):
            return True
    return False


def installed(ollama_url: str = "http://127.0.0.1:11434",
              probe: bool = True) -> Dict[str, Any]:
    """What this machine can actually answer with right now.

    `probe=False` reports only what needs no check (the built-in engine), so a
    caller that must not do I/O (a test, a locked screen) can still ask.
    """
    names = ollama_models(ollama_url) if probe else None
    rows: List[Dict[str, Any]] = []
    for model_id, spec in PACK.items():
        row = {"id": model_id, "label": spec["label"], "kind": spec["kind"],
               "runtime": spec["runtime"], "present": False, "why_not": "",
               "free": spec["free"], "needs_net": spec["needs_net"],
               "weight_gb": spec["weight_gb"], "tamil": spec["tamil"],
               "jobs": list(spec["jobs"])}
        if spec["runtime"] == "builtin":
            row["present"] = True
        elif spec["runtime"] == "ollama":
            if names is None:
                row["why_not"] = "Ollama is not running on this PC"
            elif _tag_present(spec["tag"], names):
                row["present"] = True
            else:
                row["why_not"] = f"not pulled yet - run: {spec['install']}"
        elif spec["runtime"] in ("whisper", "piper"):
            row["why_not"] = ("an optional component - this build cannot confirm it "
                              "is installed, so it is not used until you add it")
        else:
            row["why_not"] = "needs a key: " + spec["install"]
        rows.append(row)
    present = [r["id"] for r in rows if r["present"]]
    return {"ok": True, "ollama_running": names is not None,
            "ollama_models": names or [], "models": rows, "present": present}


def choose(job: str, available: Optional[List[str]] = None,
           allow_cloud: bool = True, free_only: bool = False) -> Dict[str, Any]:
    """Pick the model for a job. Returns the choice AND the reason.

    `available` is the list of present model ids (from `installed()["present"]`).
    A model that is not available is never chosen, even if the order prefers it -
    a plan that names a model the machine does not have is the bug this prevents.
    """
    if job not in ORDER:
        return {"ok": False, "reason": f"unknown job '{job}'; known: {', '.join(ORDER)}"}
    avail = set(available if available is not None else ["builtin"])
    skipped: List[Dict[str, str]] = []
    for model_id in ORDER[job]:
        spec = PACK.get(model_id)
        if spec is None:
            continue
        if model_id not in avail:
            skipped.append({"model": model_id, "why": "not present on this PC"})
            continue
        if spec["needs_net"] and not allow_cloud:
            skipped.append({"model": model_id, "why": "cloud is not allowed for this turn"})
            continue
        if free_only and not spec["free"]:
            skipped.append({"model": model_id, "why": "paid model, free-only mode"})
            continue
        return {"ok": True, "model": model_id, "label": spec["label"],
                "kind": spec["kind"], "free": spec["free"],
                "needs_net": spec["needs_net"], "skipped": skipped,
                "reason": spec["why"]}
    return {"ok": False, "model": None, "skipped": skipped,
            "reason": (f"nothing present can do a '{job}' job"
                       + ("" if allow_cloud else " without the network"))}


def advice(machine: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """What to install, in the order that pays off first. Honest about weight."""
    present = set((machine or {}).get("present") or ["builtin"])
    out: List[Dict[str, Any]] = []
    for model_id, jobs in (("qwen2.5:3b", "small and big questions, Tamil included"),
                           ("phi3.5:mini", "a light machine: short questions"),
                           ("qwen2.5:7b", "harder reasoning, needs more RAM"),
                           ("whisper-small", "speaking to Nila offline"),
                           ("piper-ta", "Nila speaking Tamil offline")):
        if model_id in present:
            continue
        spec = PACK[model_id]
        out.append({"id": model_id, "label": spec["label"], "for": jobs,
                    "size_gb": spec["weight_gb"], "load": spec["load"],
                    "install": spec["install"], "why": spec["why"]})
    return out


def pack_status(machine: Optional[Dict[str, Any]] = None,
                ollama_url: str = "http://127.0.0.1:11434") -> Dict[str, Any]:
    """One call for the screen: what is here, what to add, and what it enables."""
    m = machine if machine is not None else installed(ollama_url)
    present = [r for r in m["models"] if r["present"]]
    plan = {job: choose(job, m["present"], allow_cloud=True) for job in
            ("small", "big", "code")}
    return {"ok": True, "ollama_running": m["ollama_running"],
            "present": present, "missing": [r for r in m["models"] if not r["present"]],
            "plan": plan, "advice": advice(m),
            "note": ("nothing here is downloaded or installed by Nila - each row "
                     "says the command, and you decide")}
