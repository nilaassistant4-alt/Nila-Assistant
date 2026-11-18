"""Diagnostics: what works, what is blocked, why, and what to do about it.

Everything here is computed from the real state (permission broker, providers,
vault, audit journal), never from prompts, and contains no message text.
"""
from __future__ import annotations

import shutil
import sys
import time
from typing import Any, Dict, List

from .crypto.envelope import backend as crypto_backend

# state values used by the capability map
POSSIBLE, NEEDS_PERMISSION, NEEDS_SETUP, BLOCKED, PLANNED = (
    "possible", "needs_permission", "needs_setup", "blocked", "planned")


def classify_error(message: str) -> Dict[str, str]:
    """Turn a raw error into a category, a plain explanation and a fix."""
    m = (message or "").lower()
    rules = [
        (("11434", "ollama"), "local_ai", "The local AI (Ollama) is not running.",
         "Open the Ollama app, or run `ollama serve`, then try again."),
        (("http 404", "model not found", "is not found for api"), "model",
         "The online model name is not available (models are retired and renamed often).",
         "Open Google AI Studio, copy a current Flash model name, then run: "
         "brain config set agent.online_model <name>"),
        (("http 401", "http 403", "api key", "no online key", "permission_denied"), "api_key",
         "The online AI key is missing, wrong or not allowed.",
         "Check the key in Google AI Studio and set it again (see docs/CAPABILITIES.md)."),
        (("http 429", "rate", "daily limit", "slow down", "too many"), "quota",
         "A usage limit was reached (daily or per-minute).",
         "Wait for the limit to reset, or keep using the local AI. Nila stopped early on purpose."),
        (("cannot reach", "timed out", "connection", "name or service", "getaddrinfo"), "network",
         "A network connection failed.", "Check your internet connection and try again."),
        (("held:", "paused for safety"), "held",
         "A risky action was paused because untrusted content was in the reply.",
         "Send a new message to continue."),
        (("permission required", "not permitted", "refused", "never-read", "panic"), "permission",
         "An action needs your approval or is blocked by policy.",
         "Check the Approvals box in the dashboard, or see what is allowed in the capability list."),
        (("locked", "unlock"), "vault", "The vault is locked.", "Restart Nila and unlock it."),
        (("rejected:",), "blocked_command", "A command was rejected as unsafe.",
         "Ask for one plain command without & | ; < > or line breaks."),
    ]
    for needles, cat, why, fix in rules:
        if any(n in m for n in needles):
            return {"category": cat, "explain": why, "fix": fix}
    return {"category": "unknown", "explain": "Something unexpected happened.",
            "fix": "Run the diagnostics report and send me the result."}


# --------------------------------------------------------------------------- #
def capabilities(brain: Any) -> List[Dict[str, str]]:
    cfg = brain.config
    items: List[Dict[str, str]] = []

    def add(cid: str, label: str, state: str, reason: str = "", how: str = "") -> None:
        items.append({"id": cid, "label": label, "state": state, "reason": reason, "how": how})

    panic = False
    try:
        panic = bool(brain.permissions.panicked())
    except Exception:
        pass
    if panic:
        add("panic", "Everything on this PC", BLOCKED, "The panic latch is on.",
            "Clear it with `brain permission panic --off` when you are ready.")

    add("memory", "Remember and recall (encrypted)", POSSIBLE)
    add("projects", "Projects, ideas and business models", POSSIBLE)
    add("attachments", "Attach files to projects (encrypted)", POSSIBLE)
    add("offline", "Built-in offline engine", POSSIBLE, "Basic answers, no internet.")

    router = getattr(brain, "router", None)
    if router is not None:
        local = router.lane_config("local")
        from .agent.providers import build_provider
        lp = build_provider(local)
        if lp.available():
            add("local_ai", f"Local AI ({local['model']})", POSSIBLE)
        else:
            add("local_ai", "Local AI (Ollama)", NEEDS_SETUP, "Ollama is not running.",
                f"Install Ollama, run `ollama pull {local['model']}`, and keep it open.")
        st = router.online_status()
        if st["ok"]:
            add("online_ai", f"Online AI ({st['provider'].name})", POSSIBLE,
                "Only for messages without private data.")
        else:
            state = NEEDS_SETUP if "key" in st["reason"] else BLOCKED
            add("online_ai", "Online AI (Gemini)", state, st["reason"],
                "Set GEMINI_API_KEY (see docs/CAPABILITIES.md)." if state == NEEDS_SETUP
                else "It will work again when the limit resets.")
    else:
        add("model", "Configured AI provider", POSSIBLE, f"Provider: {brain.provider.name}")

    add("read_files", "Read files on this PC", NEEDS_PERMISSION,
        "Asks you each time (or a short standing grant for chosen folders).",
        "Approve in the Approvals box when asked.")
    add("run_command", "Run a command", NEEDS_PERMISSION,
        "Always asks first; one plain command only; secrets are hidden from it.",
        "Approve in the Approvals box when asked.")
    add("photos", "Look at photo files (metadata only)", NEEDS_PERMISSION,
        "Always asks first. Photo contents never leave the device.")
    add("export", "Export your data", NEEDS_PERMISSION, "Needs a fresh login check each time.")
    # #3 CHANGED THE TRUTH HERE: the row used to say BLOCKED ("off by default,
    # each service must be enabled") even when the owner had switched a service
    # on, so the capability map disagreed with the connectors screen.
    try:
        conn = getattr(brain, "connectors", None)
        on = [r["id"] for r in (conn.status()["integrations"] if conn else [])
              if r.get("on") and not r.get("placeholder")]
    except Exception:
        on = []
    add("integrations", "Connect to outside services",
        POSSIBLE if on else NEEDS_SETUP,
        f"on: {', '.join(on)}" if on else "off - you switch each one on in Connectors",
        "" if on else "Open Connectors in the dashboard (or `brain connectors`).")
    add("sync", "Sync between devices",
        POSSIBLE if cfg.sync else NEEDS_SETUP,
        "" if cfg.sync else "No sync server is configured.", "Planned for phone access.")
    add("updates", "Updates", NEEDS_PERMISSION, "Nila only suggests updates; you approve them.")

    for cid, label in (("voice", "Voice: talk and hear replies"),
                       ("images", "Understand images"),
                       ("wake", "Wake phrase"),
                       ("mobile", "Phone access"),
                       ("devtasks", "Work on a ZIP project in an isolated workspace"),
                       ("video", "Video editing helpers")):
        add(cid, label, PLANNED, "Not built yet.", "See ROADMAP.md.")
    return items


# --------------------------------------------------------------------------- #
def _check(items: List[Dict[str, str]], name: str, status: str, detail: str = "",
           fix: str = "") -> None:
    items.append({"name": name, "status": status, "detail": detail, "fix": fix})


def health(brain: Any) -> List[Dict[str, str]]:
    cfg, vault = brain.config, brain.vault
    out: List[Dict[str, str]] = []

    if vault.is_unlocked():
        sess = vault.session
        _check(out, "Vault", "ok", f"unlocked ({sess.mode})")
        if sess.mode == "setup":
            _check(out, "Login check", "bad", "Opened with the passphrase only.",
                   "Run `brain totp` to add an authenticator app.")
    else:
        _check(out, "Vault", "bad", "locked", "Restart Nila and unlock it.")

    try:
        pol = vault.policy()
        has_voice = vault.has_voice()
        has_totp = bool(vault.load_totp_secret()) if vault.is_unlocked() else None
        if has_totp or has_voice:
            _check(out, "Second factor", "ok", "authenticator" if has_totp else "voice")
        elif has_totp is None:
            _check(out, "Second factor", "warn", "unknown while locked")
        else:
            _check(out, "Second factor", "bad", "none enrolled", "Run `brain totp`.")
        if pol.get("allow_degraded"):
            _check(out, "Safety override", "warn",
                   "identity.allow_degraded is ON (passphrase-only logins allowed)",
                   "Set allow_degraded back to false in identity.json (it is a safety setting).")
    except Exception as exc:
        _check(out, "Second factor", "warn", f"could not check: {type(exc).__name__}")

    try:
        integ = vault.verify_audit_chain()
        ok = bool(integ.get("ok", integ.get("valid", True))) if isinstance(integ, dict) else bool(integ)
        detail = "chain verified"
        if ok and integ.get("legacy_unprotected"):
            detail = (f"chain verified; {integ['legacy_unprotected']} entries predate "
                      "payload binding and are not verifiable")
        _check(out, "Audit log integrity", "ok" if ok else "bad",
               detail if ok else "the log was altered or damaged",
               "" if ok else "Restore from a backup and investigate.")
    except Exception as exc:
        _check(out, "Audit log integrity", "warn", f"could not verify: {type(exc).__name__}")

    be = crypto_backend()
    _check(out, "Encryption", "ok" if "aes" in be else "warn", be,
           "" if "aes" in be else "Install the `cryptography` package for AES-256-GCM.")

    for c in capabilities(brain):
        if c["id"] == "local_ai":
            _check(out, "Local AI", "ok" if c["state"] == POSSIBLE else "warn",
                   c["label"], c["how"])
        if c["id"] == "online_ai":
            _check(out, "Online AI", "ok" if c["state"] == POSSIBLE else "warn",
                   c["reason"] or c["label"], c["how"])

    try:
        free = shutil.disk_usage(str(cfg.home)).free / 1e9
        _check(out, "Disk space", "ok" if free > 2 else "warn", f"{free:.1f} GB free",
               "" if free > 2 else "Free some disk space.")
    except Exception:
        pass
    _check(out, "Python", "ok", sys.version.split()[0])
    return out


def issues_from_audit(brain: Any, limit: int = 300) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    watch = {"provider_error", "anomaly_halt", "taint_hold", "egress_withheld",
             "host_command_rejected", "dashboard_refused", "permission_denied"}
    try:
        for e in brain.vault.read_audit(limit):
            ev = e.get("event", "")
            if ev in watch:
                counts[ev] = counts.get(ev, 0) + 1
    except Exception:
        pass
    return counts


def budgets(brain: Any) -> List[Dict[str, Any]]:
    router = getattr(brain, "router", None)
    if router is None:
        return []
    cfg = router.lane_config("online")
    from .agent.providers import build_provider
    prov = build_provider(cfg)
    return [brain.ledger.snapshot(prov.name, cfg)]


def report(brain: Any) -> Dict[str, Any]:
    h = health(brain)
    caps = capabilities(brain)
    counts = issues_from_audit(brain)
    suggestions: List[Dict[str, str]] = []
    for c in h:
        if c["status"] in ("bad", "warn") and c["fix"]:
            suggestions.append({"problem": f"{c['name']}: {c['detail']}", "solution": c["fix"]})
    explain = {"provider_error": "An AI provider call failed (network, key or limit).",
               "taint_hold": "A risky action was paused after untrusted content appeared.",
               "egress_withheld": "Private memories were kept from an online AI.",
               "host_command_rejected": "An unsafe command was rejected.",
               "anomaly_halt": "Nila stopped after an unexpected error or refused access."}
    for ev, n in counts.items():
        if ev in explain:
            suggestions.append({"problem": f"{n}× {explain[ev]}",
                                "solution": "Usually fine; open the audit log if it repeats."})
    return {"generated": int(time.time()), "health": h, "capabilities": caps,
            "issue_counts": counts, "budgets": budgets(brain), "suggestions": suggestions}


def report_text(rep: Dict[str, Any]) -> str:
    icon = {"ok": "OK  ", "warn": "WARN", "bad": "FAIL"}
    lines = ["NILA HEALTH REPORT", ""]
    for c in rep["health"]:
        lines.append(f"[{icon.get(c['status'], '?')}] {c['name']}: {c['detail']}")
    lines += ["", "WHAT NILA CAN DO NOW"]
    groups = {POSSIBLE: "possible", NEEDS_PERMISSION: "needs your permission",
              NEEDS_SETUP: "needs setup", BLOCKED: "blocked", PLANNED: "planned (not built)"}
    for st, title in groups.items():
        names = [c["label"] for c in rep["capabilities"] if c["state"] == st]
        if names:
            lines.append(f"  {title}: " + "; ".join(names))
    for b in rep["budgets"]:
        lines.append(f"\nOnline AI today: {b['used']} of {b['allowed']} allowed requests used"
                     + (" (default cap - set your real limit)" if b["cap_is_default"] else ""))
    if rep["suggestions"]:
        lines += ["", "PROBLEMS AND FIXES"]
        for s in rep["suggestions"]:
            lines.append(f"  - {s['problem']}\n    fix: {s['solution']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# structured checks, for the issue bundle (A)
# --------------------------------------------------------------------------- #
def run_checks(config: Any = None) -> List[Dict[str, Any]]:
    """The doctor's checks as data: [{"check","ok","detail"}].

    `cmd_doctor` prints human text and `brain issue` needs a list, so both are
    built from this. Running the same functions keeps the two views honest: a
    report can never claim a clean check the doctor would have failed.
    """
    import os
    import tempfile
    from pathlib import Path

    from .crypto.envelope import backend, decrypt, encrypt
    from .identity.voice import load_model

    checks: List[Dict[str, Any]] = []

    def check(name: str, fn) -> None:
        try:
            checks.append({"check": name, "ok": True, "detail": str(fn())})
        except Exception as exc:
            checks.append({"check": name, "ok": False,
                           "detail": f"{type(exc).__name__}: {exc}"})

    def c_crypto() -> str:
        key = os.urandom(32)
        ct = encrypt(b"doctor", key, aad=b"x")
        assert decrypt(ct, key, aad=b"x") == b"doctor"
        bad = bytearray(ct)
        bad[-1] ^= 1
        try:
            decrypt(bytes(bad), key, aad=b"x")
            raise AssertionError("tamper not detected")
        except Exception as exc:
            if "tamper not detected" in str(exc):
                raise
        return f"backend={backend()}"

    def c_voice() -> str:
        model = load_model()
        if not model.available:
            raise AssertionError("voice model file is missing or unreadable")
        return f"backend={model.name} keep={model.keep} threshold={model.threshold}"

    def c_optional() -> str:
        from .util.misc import try_import
        present = [m for m in ("cryptography", "numpy", "soundfile", "sounddevice",
                               "keyring", "sentence_transformers") if try_import(m)]
        return f"installed: {present or 'none (running pure-stdlib)'}"

    def c_roundtrip() -> str:
        tmp = tempfile.mkdtemp(prefix="brain-issue-")
        try:
            from .config import Config
            from .memory.store import MemoryStore
            from .memory.vault import Vault

            cfg = Config.load(Path(tmp) / "home")
            cfg.fast_kdf = True
            v = Vault(cfg)
            v.initialize("doctor-passphrase", "doctor", "doctor-device")
            m = MemoryStore(cfg, v.session.mk)
            rec = m.remember("doctor probe", tags=["doctor"])
            assert m.get(rec.id).body == "doctor probe"
            assert m.search("doctor probe")
            from .cloud.protocol import LocalBlobStore, SyncEngine
            engine = SyncEngine(cfg, m, v, LocalBlobStore(Path(tmp) / "cloud"))
            report = engine.sync()
            assert report["pushed"] == 1, report
            m.close()
            v.lock()
            return "init -> remember -> search -> e2ee sync all ok"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    check("crypto envelope", c_crypto)
    check("voice calibration model", c_voice)
    check("end-to-end vault+memory+sync", c_roundtrip)
    check("optional dependencies", c_optional)
    if config is not None:
        def c_real() -> str:
            from .memory.vault import Vault
            v = Vault(config)
            chain = v.verify_audit_chain()
            # A locked vault cannot verify session-keyed lines, and saying "ok" for
            # them would be a lie in the other direction.
            note = (" (unlock to verify the rest)"
                    if chain.get("needs_unlock_to_verify") else "")
            return (f"vault ok: user={v.user} device={v.device_id} "
                    f"devices={len(v.devices())} audit_ok={chain['ok']}"
                    f"{note} {chain}")
        check("installed vault", c_real)
    return checks
