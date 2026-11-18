"""Bug capture: "this is the problem I have" -> a safe, reviewable issue bundle (A).

The owner's request was specific and it shapes this file:

    "When I say something is wrong, Nila should find the problem, and when I say
     I am going to update you, it should be able to say: these are the problems
     I have, these are the issues."

So there are three separate jobs here, and they are kept separate on purpose:

1. ``triage(complaint)``   - turn a sentence ("voice unlock fails") into a
                             category and the checks that would prove it.
2. ``checks`` (from doctor)- the EVIDENCE. This module never invents a cause.
3. ``build_bundle``        - a file the owner can read before it goes anywhere.

Three rules hold everywhere below:

* **Nothing leaves the machine.** No network call is made in this module, at
  all, by design. The bundle is a local file; attaching it to GitHub is the
  owner's action.
* **Every secret is scrubbed twice** - once when a value is collected (the
  ``_redact`` call sites) and once over the finished text (``scrub``). A single
  miss at a call site must not be the difference between a safe report and a
  leaked key, so the second pass is not redundant, it is the guarantee.
* **The preview is the whole content.** ``preview`` and the file are produced
  from the same dict, so "show me what you would send" cannot show less than
  what is actually written.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "0.1.0"
BUNDLE_FORMAT = 1

#: A complaint is triaged into one of these. The names are stable because they
#: end up in the bundle and in the audit log.
CATEGORIES: Dict[str, Dict[str, Any]] = {
    "unlock": {
        "label": "unlock / passphrase / authenticator / voice",
        "keywords": ("unlock", "passphrase", "password", "totp", "authenticator", "code",
                     "voice", "voiceprint", "biometric", "lockout", "locked out",
                     "recovery", "login", "log in", "sign in",
                     "திறக்க", "கடவுச்சொல்", "குரல்", "குறியீடு", "உள்நுழை"),
        "checks": ("installed vault", "crypto envelope", "voice calibration model"),
    },
    "memory": {
        "label": "remember / recall / search / Tamil text",
        "keywords": ("remember", "recall", "memory", "memories", "search", "find",
                     "forget", "delete", "tamil", "tanglish", "save",
                     "நினைவு", "தேடு", "மறந்து", "சேமி", "தமிழ்"),
        "checks": ("end-to-end vault+memory+sync",),
    },
    "voice_output": {
        "label": "speaking / voice picker / microphone",
        "keywords": ("speak", "speech", "voice picker", "microphone", "mic", "audio",
                     "sound", "tts", "hear", "push to talk", "push-to-talk",
                     "பேசு", "குரல்", "ஒலி", "மைக்ரோஃபோன்"),
        "checks": ("optional dependencies", "voice calibration model"),
    },
    "connectors": {
        "label": "AI provider / connectors screen / API key",
        "keywords": ("connector", "gemini", "openai", "anthropic", "ollama", "api key",
                     "provider", "online", "offline", "quota", "budget",
                     "இணைப்பு", "வழங்குநர்", "சாவி"),
        "checks": ("optional dependencies", "installed vault"),
    },
    "sync": {
        "label": "sync / backup / restore / devices",
        "keywords": ("sync", "backup", "restore", "device", "export", "import",
                     "encrypt", "decrypt", "corrupt",
                     "ஒத்திசை", "காப்பு", "சாதனம்"),
        "checks": ("end-to-end vault+memory+sync", "crypto envelope"),
    },
    "update": {
        "label": "update / install / version",
        "keywords": ("update", "upgrade", "install", "version", "wheel", "pip",
                     "patch", "release", "zip",
                     "புதுப்பி", "நிறுவு", "பதிப்பு"),
        "checks": ("optional dependencies",),
    },
    "slow": {
        "label": "slow / hung / crash",
        "keywords": ("slow", "hang", "hangs", "freeze", "frozen", "crash", "stuck",
                     "not responding", "timeout", "takes forever",
                     "மெதுவா", "முடங்க", "நேரம்"),
        "checks": ("optional dependencies", "end-to-end vault+memory+sync"),
    },
    "privacy": {
        "label": "privacy / something sent online / secrets",
        "keywords": ("privacy", "private", "leak", "leaked", "sent online", "upload",
                     "share", "secret", "key exposed", "network",
                     "தனியுரிமை", "வெளியே", "பாதுகாப்பு"),
        "checks": ("crypto envelope", "installed vault"),
    },
    "other": {
        "label": "anything else",
        "keywords": (),
        "checks": ("crypto envelope", "optional dependencies", "installed vault"),
    },
}

#: The only machine facts a bundle ever carries. Anything else is left out, so a
#: new field somewhere else in the app cannot leak in through a "dump everything"
#: path.
_ENV_KEYS = ("LANG", "LC_ALL", "PYTHONIOENCODING")

#: A path is cut to its shape: no user name, no folder names.
def _shape_path(path: str) -> str:
    """`/home/anna/Nila/vault.json` -> `<home>/Nila/vault.json`.

    Keeps the last two components (enough to tell "the vault file is missing"
    from "the config is missing") and drops the owner's account name.
    """
    parts = Path(str(path)).parts
    tail = "/".join(parts[-2:]) if len(parts) >= 2 else (parts[0] if parts else "")
    return f"<home>/{tail}" if tail else ""


# --------------------------------------------------------------------------- #
# scrubbing
# --------------------------------------------------------------------------- #
_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    # provider keys: the shapes that actually appear in the wild
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"), "<GEMINI_KEY>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "<OPENAI_KEY>"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}\b"), "<ANTHROPIC_KEY>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "<GITHUB_TOKEN>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.I), "Bearer <TOKEN>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<AWS_KEY>"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "<SLACK_TOKEN>"),
    #: Telegram bot tokens (stage 9). They are the one secret this product now
    #: stores besides the vault passphrase, so they get a shape rule of their own:
    #: <bot id>:<35 chars>. Written to also catch the api.telegram.org/bot<token>
    #: URL, which is how a token most often ends up inside an error message.
    (re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_\-]{30,}"), "<TELEGRAM_TOKEN>"),
    # private key material
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "<PRIVATE_KEY>"),
    # generic "key = value" / "token: value" / "--passphrase xxx"
    (re.compile(r"(?i)\b(api[_-]?key|secret|passphrase|password|token|otp|totp)\b"
                r"\s*[:=]\s*[\"']?([^\s\"',;]{3,})"), r"\1=<REDACTED>"),
    (re.compile(r"(?i)--(passphrase|password|totp|recovery)[= ]+(\S+)"),
     r"--\1=<REDACTED>"),
    # e-mail, phone, card, Aadhaar-like, IBAN-ish
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"), "<EMAIL>"),
    #: ORDER MATTERS. A card number is four groups of four, which the phone rule
    #: below would otherwise swallow as one long ("<NUMBER>") run - so the card
    #: and ID shapes are matched first, and the phone rule is what is left.
    (re.compile(r"(?<!\d)(?:\d{4}[ -]?){3}\d{4}(?!\d)"), "<CARD>"),
    (re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)"), "<ID_NUMBER>"),
    #: 10-14 digit runs, i.e. phone-length. A 13-digit millisecond timestamp was a
    #: false positive that also broke the report's own header, so the bound is what
    #: a phone number actually is: 10 to 14 digits, not "any long number".
    (re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9,13}\d(?!\d)"), "<NUMBER>"),
    # a home directory with a real account name in it
    (re.compile(r"(?i)([A-Z]:\\Users\\|/home/|/Users/)[^\\/\s]+"), r"\1<user>"),
)

#: Any string the caller KNOWS is a secret is added here for the scrub pass, so
#: a key with an unusual shape (a local test key, a short one) is still removed.
def scrub(text: str, extra: Iterable[str] = ()) -> str:
    """Remove secrets and personal data from free text. Idempotent."""
    out = "" if text is None else str(text)
    for secret in extra:
        if secret and len(str(secret)) >= 4:
            out = out.replace(str(secret), "<REDACTED>")
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def _redact(text: Any, extra: Iterable[str] = ()) -> str:
    return scrub(str(text), extra)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# 1. triage - what kind of problem is this?
# --------------------------------------------------------------------------- #
def triage(complaint: str) -> Dict[str, Any]:
    """Classify a complaint into a category and the checks that would prove it.

    Deliberately does NOT try to diagnose. The evidence comes from the doctor
    checks; this only decides which of them are worth running and showing, so a
    wrong guess costs a slightly wider check list, never a wrong conclusion.
    """
    text = str(complaint or "").strip().lower()
    scores: Dict[str, int] = {}
    for name, spec in CATEGORIES.items():
        hits = [k for k in spec["keywords"] if k in text]
        if hits:
            scores[name] = len(hits)
    best = max(scores, key=lambda k: scores[k]) if scores else "other"
    if len(scores) > 1:
        # Two categories with the same weight: keep the first in declaration
        # order, which runs from the most common complaint to the rarest.
        order = list(CATEGORIES)
        top = max(scores.values())
        best = next(k for k in order if scores.get(k) == top)
    spec = CATEGORIES[best]
    return {
        "category": best,
        "label": spec["label"],
        "matched": [k for k in spec["keywords"] if k in text],
        "checks": list(spec["checks"]),
        "alternatives": sorted((k for k in scores if k != best),
                               key=lambda k: -scores[k])[:2],
    }


#: What to do about each category when the doctor is clean. Written as advice the
#: owner can act on, not as a code path, so it cannot claim a cause it has not
#: seen.
_NEXT_STEPS = {
    "unlock": ["re-run `brain unlock` and read the exact refusal (wrong code vs "
               "locked-out vs no second factor are three different fixes)",
               "`brain recovery` shows your recovery code and its remaining uses",
               "if the voice check fails, enrol again with `brain enroll-voice`"],
    "memory": ["`brain recall \"<the words you used>\"` - recall is word-based, so "
               "one shared word is enough",
               "Tamil text is stored and found exactly; a partial word is not a hit",
               "`brain stats` shows how many records exist and in which tier"],
    "voice_output": ["speaking needs an optional component, and this build may not "
                     "have it - check the optional dependencies line above",
                     "the microphone extra is `sounddevice`; without it Nila cannot "
                     "listen, and voice unlock says so instead of failing silently"],
    "connectors": ["`brain connectors` lists each provider and why it is not ready",
                   "`brain connectors key gemini` stores a key in the vault; never "
                   "paste a key into chat",
                   "a switched-off provider is reported before a missing key, so "
                   "check the switch first"],
    "sync": ["the same passphrase is needed on both devices",
             "`brain backup` then `brain restore` on a scratch home proves the round "
             "trip without touching your real data"],
    "update": ["`brain update check` reads the channel you configured",
               "a version mismatch usually means the ZIP was unpacked over an old "
               "folder - unpack into an empty one"],
    "slow": ["`brain doctor` timing tells you whether it is crypto or storage",
             "a very large memory file is the usual cause; `brain stats` shows size"],
    "privacy": ["open Connectors: every egress-capable row says what it may reach",
                "`brain audit` shows every attempt, including refusals",
                "if a key was ever pasted somewhere, rotate it - removal from a file "
                "is not removal from the world"],
    "other": ["describe what you did just before it went wrong, in your own words - "
              "the triage improves with your sentence, not with a stack trace"],
}


# --------------------------------------------------------------------------- #
# 2. the bundle
# --------------------------------------------------------------------------- #
def machine_facts(home: Optional[Path] = None) -> Dict[str, Any]:
    """Facts about THIS machine that help reproduce a bug - and nothing else."""
    facts: Dict[str, Any] = {
        "version": VERSION,
        "python": platform.python_version(),
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "locale": os.environ.get("LANG") or os.environ.get("LC_ALL") or "",
        "crypto_backend": "",
        "optional": {},
    }
    try:
        from .crypto.envelope import backend
        facts["crypto_backend"] = backend()
    except Exception:
        facts["crypto_backend"] = "unavailable"
    try:
        from .util.misc import try_import
        for mod in ("cryptography", "numpy", "soundfile", "sounddevice", "keyring"):
            facts["optional"][mod] = bool(try_import(mod))
    except Exception:
        pass
    if home is not None:
        facts["home_shape"] = _shape_path(str(home))
    return facts


def _check_rows(checks: List[Dict[str, Any]], wanted: Iterable[str],
                extra: Iterable[str] = ()) -> List[Dict[str, Any]]:
    want = list(wanted)
    out: List[Dict[str, Any]] = []
    for row in checks:
        name = str(row.get("check", ""))
        if want and name not in want:
            continue
        out.append({"check": name, "ok": bool(row.get("ok")),
                    "detail": _redact(row.get("detail", ""), extra)})
    return out


def build_bundle(complaint: str, checks: List[Dict[str, Any]],
                 home: Optional[Path] = None,
                 secrets: Iterable[str] = (),
                 events: Optional[List[Dict[str, Any]]] = None,
                 now: Optional[float] = None) -> Dict[str, Any]:
    """Assemble the report. Pure: no I/O, no network, same input -> same output.

    ``secrets`` is the list of strings the CALLER knows to be secret (keys in the
    vault, the passphrase if it is in hand). They are removed from every string,
    on top of the pattern scrub.
    """
    secrets = [s for s in secrets if s]
    t = triage(complaint)
    facts = machine_facts(home)
    picked = _check_rows(checks, t["checks"], secrets)
    failed = [c for c in picked if not c["ok"]]
    all_failed = [{"check": str(c.get("check", "")),
                   "detail": _redact(c.get("detail", ""), secrets)}
                  for c in checks if not c.get("ok")]
    body = {
        "format": BUNDLE_FORMAT,
        "created": int((now or time.time()) * 1000),
        "complaint": _redact(complaint, secrets),
        "triage": {"category": t["category"], "label": t["label"],
                   "matched": t["matched"], "alternatives": t["alternatives"]},
        "machine": facts,
        "checks": picked,
        "failed_checks": failed,
        "other_failed_checks": [c for c in all_failed
                                if c["check"] not in {p["check"] for p in picked}],
        "recent_events": [_redact_event(e, secrets) for e in (events or [])][-20:],
        "next_steps": _NEXT_STEPS.get(t["category"], _NEXT_STEPS["other"]),
        "what_is_included": [
            "your sentence (secrets removed)",
            "which checks ran and their pass/fail line",
            "Python version, OS name and version, locale, crypto backend",
            "which optional components are installed",
            "the last 20 audit event NAMES (no text, no arguments)",
        ],
        "what_is_never_included": [
            "API keys, passphrases, authenticator codes, recovery codes",
            "your memories, notes, chats, project text",
            "e-mail addresses, phone numbers, card or ID numbers",
            "file paths containing your account name",
            "anything from the network: this bundle is built offline",
        ],
        "confirmed_no_secrets": True,
    }
    # The guarantee: scrub every STRING in the finished tree once more, so a value
    # that slipped into a nested field (a check detail written by some other
    # module, an audit name) cannot survive.
    #
    # This walks the values, it does not run a regex over the serialised JSON. The
    # first version did the latter and the "long digit run" rule ate a
    # millisecond timestamp, turning the document into invalid JSON - a scrubbed
    # report that cannot be read is not a safe report, it is a broken one.
    body, changed = _scrub_tree(body, secrets)
    if changed:
        body["scrubbed_again"] = True
    # Formatted timestamps are OUR rendering of a number, not the owner's text, so
    # they are added AFTER the scrub pass - otherwise the phone-number rule turned
    # "2026-11-04 11:46" into "<NUMBER>:46" in the report's own header.
    body["created_display"] = time.strftime(
        "%Y-%m-%d %H:%M", time.localtime(body["created"] / 1000))
    for ev in body.get("recent_events", []):
        at = int(ev.get("at") or 0)
        ev["at_display"] = (time.strftime("%H:%M:%S", time.localtime(at / 1000))
                            if at else "")
    body["fingerprint"] = _hash(body["complaint"] + "|" +
                                str(body["triage"]["category"]) + "|" +
                                str(sorted(c["check"] for c in body["failed_checks"])))
    return body


def _scrub_tree(obj: Any, secrets: Iterable[str]) -> Tuple[Any, bool]:
    """Scrub every string value in a nested structure. Returns (new, changed)."""
    changed = False
    if isinstance(obj, str):
        clean = scrub(obj, secrets)
        return clean, clean != obj
    if isinstance(obj, dict):
        out: Dict[Any, Any] = {}
        for k, v in obj.items():
            new_v, c = _scrub_tree(v, secrets)
            changed = changed or c
            out[k] = new_v
        return out, changed
    if isinstance(obj, (list, tuple)):
        items = []
        for v in obj:
            new_v, c = _scrub_tree(v, secrets)
            changed = changed or c
            items.append(new_v)
        return items, changed
    return obj, False


def _redact_event(event: Dict[str, Any], secrets: Iterable[str]) -> Dict[str, Any]:
    """An audit row, reduced to name + time. Arguments are never included."""
    at = int(event.get("at") or event.get("ts") or 0)
    return {"name": _redact(event.get("name") or event.get("event") or "", secrets),
            "at": at, "ok": bool(event.get("ok", True))}


def render_markdown(bundle: Dict[str, Any]) -> str:
    """The file the owner reads. Same content as ``preview``, as text."""
    b = bundle
    m = b["machine"]
    lines = [f"# Nila problem report — {b['triage']['label']}", "",
             f"- **Your words:** {b['complaint']}",
             f"- **Category:** `{b['triage']['category']}`"
             + (f" (also considered: {', '.join(b['triage']['alternatives'])})"
                if b["triage"].get("alternatives") else ""),
             f"- **Created:** {b.get('created_display') or ''}",
             f"- **Fingerprint:** `{b['fingerprint']}` (same problem → same fingerprint)",
             "", "## Checks", ""]
    for c in b["checks"]:
        lines.append(f"- [{'PASS' if c['ok'] else 'FAIL'}] **{c['check']}** — {c['detail']}")
    if b.get("other_failed_checks"):
        lines += ["", "## Also failing (outside this category)", ""]
        for c in b["other_failed_checks"]:
            lines.append(f"- [FAIL] **{c['check']}** — {c['detail']}")
    lines += ["", "## Machine", "",
              f"- Nila {m['version']} · Python {m['python']} · {m['os']} {m['os_release']} ({m['machine']})",
              f"- locale `{m['locale']}` · crypto `{m['crypto_backend']}`",
              "- optional: " + ", ".join(f"{k}={'yes' if v else 'no'}"
                                         for k, v in m.get("optional", {}).items())]
    if b.get("recent_events"):
        lines += ["", "## Last events (names only)", ""]
        for i, e in enumerate(b["recent_events"]):
            # The time comes from the tree (already scrubbed-with-a-remembered
            # value), never from re-formatting a number the scrubber may have
            # replaced.
            stamp = e.get("at_display") or time.strftime("%H:%M:%S",
                                                         time.localtime(e["at"] / 1000))
            lines.append(f"- {stamp} {e['name']}")
    lines += ["", "## What to try next", ""] + [f"1. {s}" for s in b["next_steps"]]
    lines += ["", "## Exactly what this file contains", ""] + \
             [f"- {s}" for s in b["what_is_included"]]
    lines += ["", "## Never in this file", ""] + \
             [f"- {s}" for s in b["what_is_never_included"]]
    return "\n".join(lines) + "\n"


def preview(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """What the owner is shown BEFORE anything is written.

    Includes the full text that would be written, so "show me" cannot be less
    honest than the file itself.
    """
    return {"ok": True, "category": bundle["triage"]["category"],
            "fingerprint": bundle["fingerprint"],
            "failed_checks": [c["check"] for c in bundle["failed_checks"]],
            "what_is_included": bundle["what_is_included"],
            "what_is_never_included": bundle["what_is_never_included"],
            "text": render_markdown(bundle),
            "will_write": None,       # filled by the caller with the target path
            "sends_anything": False}


def write_bundle(bundle: Dict[str, Any], out_dir: Path,
                 approved: bool = False) -> Path:
    """Write the file - and refuse without ``approved``.

    This is the one place a bug report touches the disk, so the check lives here
    rather than in a caller that might forget it.
    """
    if not approved:
        raise PermissionError("the owner must approve the report before it is written")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(bundle["created"] / 1000))
    path = out_dir / f"nila-report-{bundle['triage']['category']}-{stamp}-{bundle['fingerprint'][:8]}.md"
    text = render_markdown(bundle)
    # The text is already scrubbed (every string in the tree went through it).
    # Scrubbing the RENDERED text again would eat the date in the header and the
    # times in the event list - and a report the owner cannot read is not safer,
    # it is just broken. `preview()` and this function return the same string, so
    # what you approved is what is written.
    path.write_text(text, encoding="utf-8")
    return path


def known_issues(store_index: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Fingerprints seen before, from a small local index.

    "Same problem twice" is normal (a restart, a second attempt). Reporting it as
    two issues wastes the owner's time and hides the pattern, so the index keeps
    one row per fingerprint with a count and the first/last time.
    """
    idx = store_index or {}
    rows = [{"fingerprint": fp, **val} for fp, val in idx.items() if isinstance(val, dict)]
    return sorted(rows, key=lambda r: -int(r.get("last") or 0))


def record_issue(index: Dict[str, Any], bundle: Dict[str, Any],
                 now: Optional[float] = None) -> Tuple[Dict[str, Any], bool]:
    """Add a bundle to the index. Returns (new_index, was_already_known)."""
    out = dict(index or {})
    fp = bundle["fingerprint"]
    ts = int((now or time.time()) * 1000)
    seen = fp in out
    if seen:
        row = dict(out[fp])
        row["count"] = int(row.get("count", 1)) + 1
        row["last"] = ts
        out[fp] = row
    else:
        out[fp] = {"category": bundle["triage"]["category"], "count": 1,
                   "first": ts, "last": ts}
    return out, seen
