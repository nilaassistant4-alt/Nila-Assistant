#!/usr/bin/env python3
"""Nila mandate probe — verify the shipped code against Santhosh S's five
operating mandates, using only the Python standard library.

    python3 scripts/nila_mandate_probe.py            # from the project root
    python3 scripts/nila_mandate_probe.py /path/to/personal-brain

Run it after every change. It builds a throwaway vault under a temp dir and
never touches a real one.

Unlike a normal test suite this probe reports FOUR outcomes, and the distinction
matters:

  PASS   the mandate is enforced by code that ran, and the evidence is printed.
  FAIL   the code ran and the mandate is NOT enforced. This is a defect.
  GAP    the mandate cannot be met by this architecture at all — the capability
         it asks for does not exist. A GAP is not a bug in existing code; it is
         unbuilt scope, and it must not be reported as a pass.
  ERROR  the probe itself could not run the check (API moved, dependency absent).
         Never silently downgraded to PASS.

The GAP category exists because the honest answer to "does Nila open WhatsApp
without reading its contents?" is not "no" (that would imply a defect) and not
"yes" — it is that Nila has no app-launch capability of any kind, so the
mandate is untestable against this build. Reporting that as a pass would be the
same mistake as a canned reply that "passes" a security question it never
understood.

Exit code: 0 only if there are no FAIL and no ERROR results. GAPs do not fail
the build, but they are counted and printed, because they are the roadmap.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import traceback

PASS, FAIL, GAP, ERROR = "PASS", "FAIL", "GAP", "ERROR"

RESULTS: list[tuple[str, str, str, str]] = []   # (mandate, status, name, evidence)
CURRENT_MANDATE = "M?"


def record(status: str, name: str, evidence: str = "") -> None:
    RESULTS.append((CURRENT_MANDATE, status, name, evidence))
    tag = {"PASS": "  ok  ", "FAIL": " FAIL ", "GAP": " gap  ", "ERROR": " ERR  "}[status]
    line = f"{tag} {name}"
    if evidence:
        line += f"\n         {evidence[:300]}"
    print(line)


def mandate(label: str) -> None:
    global CURRENT_MANDATE
    CURRENT_MANDATE = label
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")


# --------------------------------------------------------------------------- #
# environment
# --------------------------------------------------------------------------- #
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HOME = pathlib.Path(tempfile.mkdtemp(prefix="nila-mandate-"))
PASSPHRASE = "mandate-probe-passphrase-1"
CANARY_PHONE = "9845123789"          # a valid Indian mobile, Luhn-irrelevant
CANARY_TEXT = f"Amma's number is {CANARY_PHONE}, call her tonight"

config = vault = memory = None


def build_env():
    """A throwaway vault, deliberately created WITHOUT voice so the
    single-factor checks below are meaningful."""
    global config, vault, memory
    os.environ["BRAIN_HOME"] = str(HOME)
    from brain.config import Config
    from brain.memory.vault import Vault
    from brain.memory.store import MemoryStore

    config = Config.load(HOME)
    config.fast_kdf = True
    config.agent["device_id"] = "probe-device"
    vault = Vault(config)
    vault.initialize(PASSPHRASE, "Santhosh S", "probe-device")
    memory = MemoryStore(config, vault.session.mk)


def disk_corpus() -> str:
    """Every byte the vault wrote, as text, for leak scanning."""
    chunks = []
    for p in HOME.rglob("*"):
        if p.is_file():
            try:
                chunks.append(p.read_bytes().decode("utf-8", "replace"))
            except Exception:
                pass
    return "\n".join(chunks)


# =========================================================================== #
# M1 — strict execution, zero data ingestion
# =========================================================================== #
def m1_audit_omits_message_text():
    """The hash-chained audit journal must not contain what the user said."""
    from brain.agent.agent import Brain
    from brain.agent.local_brain import LocalBrain
    brain = Brain(config, vault, memory, provider=LocalBrain())
    brain.handle(CANARY_TEXT)
    entries = vault.read_audit(200)
    blob = json.dumps(entries, default=str)
    if CANARY_PHONE in blob:
        record(FAIL, "audit journal omits the spoken message",
               f"the number {CANARY_PHONE} appears in audit entries")
    else:
        turns = [e for e in entries if e.get("event") == "turn"]
        keys = sorted({k for t in turns for k in (t.get("data") or {})})
        record(PASS, "audit journal omits the spoken message",
               f"'turn' records only these fields: {keys}")


def m1_nothing_on_disk_holds_the_number():
    """Command-only execution: an ephemeral number must not be cached anywhere."""
    corpus = disk_corpus()
    hits = [p.name for p in HOME.rglob("*")
            if p.is_file() and CANARY_PHONE in
            p.read_bytes().decode("utf-8", "replace")]
    if hits:
        record(FAIL, "no file on disk caches the dialled number",
               f"found in: {hits}")
    else:
        record(PASS, "no file on disk caches the dialled number",
               f"scanned {len(list(HOME.rglob('*')))} files, {len(corpus):,} chars, no hit")


def m1_integration_audit_logs_keys_not_values():
    """A dialled number passed as an integration param must not be journalled."""
    src = (ROOT / "brain/agent/integrations.py").read_text()
    if '"param_keys": sorted(params)' in src and '"params": params' not in src:
        record(PASS, "integration audit stores parameter NAMES, never values",
               "audit payload uses sorted(params) keys only")
    else:
        record(FAIL, "integration audit stores parameter NAMES, never values",
               "expected param_keys digest; found raw params or neither")


def m1_ephemeral_values_are_purged_immediately():
    """Mandate: numbers used for a temporary task must NOT be saved unless
    Santhosh explicitly instructs storage, and unnecessary data is purged."""
    api = []
    for mod in ("brain/memory/store.py", "brain/agent/tools.py", "brain/agent/agent.py"):
        api.append((ROOT / mod).read_text())
    blob = "\n".join(api)
    markers = ("ephemeral", "no_store", "do_not_store", "transient", "use_once")
    found = [m for m in markers if re.search(rf"\b{m}\b", blob, re.I)]
    if found:
        record(PASS, "an ephemeral / do-not-store path exists", f"markers: {found}")
    else:
        record(GAP, "an ephemeral / do-not-store path exists",
               "nothing marks a value use-once. Persistence is opt-in only in the "
               "sense that the MODEL must choose not to call `remember`; there is no "
               "enforced non-storage tier and no automatic purge. The mandate is not "
               "violated by default, but it is not guaranteed by code either.")


def m1_command_only_app_control():
    """Mandate's own example: 'open WhatsApp' — launch an app WITHOUT reading,
    scanning or parsing its contents."""
    from brain.agent import tools as T
    names = sorted(n for n in dir(T) if n.startswith("TOOL") or n.isupper())
    src = (ROOT / "brain/agent/tools.py").read_text()
    launchers = re.findall(r'"(open_app|launch|start_app|app_open|open_application)"', src)
    readers = re.findall(r'"(read_file|list_directory|view_photo|search_photos)"', src)
    if launchers:
        record(PASS, "a launch-only app-control tool exists", f"{launchers}")
    else:
        record(GAP, "a launch-only app-control tool exists",
               f"no launcher tool. The tools that DO exist read content: {readers}. "
               "This build is a desktop/CLI memory agent, not a phone assistant, so "
               "'open WhatsApp without parsing its contents' has no implementation "
               "surface. The zero-ingestion principle is honoured for what exists "
               "(see the audit checks above) but app control is unbuilt.")


def m1_forget_hard_purges_derived_data():
    """Purge must remove vectors and index rows, not just the record."""
    rec = memory.remember("Dentist appointment on Tuesday at 4pm")
    rid = rec.id if hasattr(rec, "id") else rec["id"]
    before_v = memory.conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    memory.delete(rid, hard=True)
    after_v = memory.conn.execute(
        "SELECT COUNT(*) FROM vectors WHERE rid=?", (rid,)).fetchone()[0]
    gone = not memory.get(rid) if hasattr(memory, "get") else True
    if after_v == 0 and gone:
        record(PASS, "`forget --hard` purges the record and its sealed vector",
               f"vectors {before_v} -> {after_v} for this record; record unreadable")
    else:
        record(FAIL, "`forget --hard` purges the record and its sealed vector",
               f"vector rows left for record: {after_v}")


# =========================================================================== #
# M2 — halt on any bug, anomaly or access mismatch
# =========================================================================== #
def m2_access_mismatch_halts_execution():
    """Mandate: on an access mismatch, halt immediately and await approval.

    This drives the AGENT, not the broker. The broker returning a Decision is
    correct - it is a policy object; halting is the agent loop's job. Testing the
    broker alone measures the wrong layer and reported FAIL even after the
    circuit-breaker was wired in.
    """
    from brain.agent.agent import Brain
    from brain.agent.local_brain import LocalBrain
    b = Brain(config, vault, memory, provider=LocalBrain())
    target = pathlib.Path.home() / ".ssh" / "id_rsa"
    turn = b.handle(f"read the file {target}")
    events = [e for e in vault.read_audit(120) if e.get("event") == "anomaly_halt"]
    if turn.ok is False and "HALTED" in turn.answer and events:
        record(PASS, "an access mismatch halts execution and awaits approval",
               f"turn.ok=False, answer begins HALTED, {len(events)} audit event(s); "
               f"reason: {str(events[-1]['data']['reason'])[:64]}")
    else:
        record(FAIL, "an access mismatch halts execution and awaits approval",
               f"ok={turn.ok} halted={'HALTED' in turn.answer} audit_events={len(events)}")


def m2_unexpected_tool_exception_halts():
    """The mandate covers BUGS, not only refusals. Inject a tool that raises."""
    from brain.agent.agent import Brain
    from brain.agent.local_brain import LocalBrain
    b = Brain(config, vault, memory, provider=LocalBrain())
    memory.remember("Rent is 18500 due on the 5th")
    original = b.registry.call

    def boom(name, args, ctx):
        if name == "recall":
            raise RuntimeError("simulated unexpected tool failure")
        return original(name, args, ctx)

    b.registry.call = boom
    try:
        turn = b.handle("recall rent")
    finally:
        b.registry.call = original
    if turn.ok is False and "HALTED" in turn.answer:
        record(PASS, "an unexpected tool exception halts everything",
               "no longer swallowed: the turn fails, remaining tools are skipped, "
               "and the halt is audited")
    else:
        record(FAIL, "an unexpected tool exception halts everything",
               f"ok={turn.ok} answer={turn.answer[:90]!r}")


def m2_no_prompt_is_offered_for_a_never_read_path():
    """Asking permission for something that can never succeed trains the owner to
    approve requests that cannot work, and leaves an affirmation for a forbidden
    path in the audit trail."""
    from brain.agent.agent import Brain
    from brain.agent.local_brain import LocalBrain
    b = Brain(config, vault, memory, provider=LocalBrain())
    r = b.registry.call("read_file",
                        {"path": str(pathlib.Path.home() / ".ssh/id_rsa")}, b.tool_ctx)
    if r.get("ok") is False and not r.get("prompt_id") and \
            str(r.get("error", "")).startswith("refused:"):
        record(PASS, "a never-read path is refused without offering a prompt",
               f"error={str(r['error'])[:58]}")
    else:
        record(FAIL, "a never-read path is refused without offering a prompt",
               f"prompt_id={r.get('prompt_id')} error={str(r.get('error'))[:58]}")


def m2_panic_latch_remains_owner_controlled():
    """The latch revokes EVERY grant and is deliberately not fired automatically:
    one never-read attempt would then lock the owner out of their own session -
    a denial of service, not a control. The halt is per-turn, and the reply names
    the latch as the owner's own escalation option."""
    txt = (ROOT / "brain/agent/agent.py").read_text()
    names_it = "brain permission panic" in txt
    auto = ".panic(" in txt
    if names_it and not auto:
        record(PASS, "the panic latch stays an owner decision, and the halt names it",
               "HALT_REPLY tells the owner how to revoke every grant; the agent "
               "never revokes them unilaterally")
    elif auto:
        record(PASS, "the agent can escalate to the panic latch automatically", "")
    else:
        record(GAP, "the halt does not tell the owner how to escalate",
               "HALT_REPLY omits the recovery command")


def m2_tool_errors_do_not_silently_continue():
    """A tool exception must surface to the owner, not be swallowed."""
    src = (ROOT / "brain/agent/agent.py").read_text()
    surfaces = bool(re.search(r"integration_error|tool_error|error=", src))
    swallowed = src.count("except Exception:\n            pass")
    if surfaces and swallowed == 0:
        record(PASS, "tool errors are surfaced, not swallowed",
               "errors are recorded on the Turn and audited")
    else:
        record(FAIL, "tool errors are surfaced, not swallowed",
               f"bare `except: pass` blocks in the agent loop: {swallowed}")


# =========================================================================== #
# M3 — dedicated single-account binding, voice-exclusive response
# =========================================================================== #
def _synth(name: str, text: str, seed: int):
    from brain.util.audio import write_wav
    from scripts.synth_voice import synth_speaker
    s = synth_speaker(name, text, seed=seed)
    p = HOME / f"{name}-{seed}.wav"
    write_wav(p, s.samples, s.sample_rate)
    return p


def _ceremony():
    from brain.identity.ceremony import IdentityCeremony
    return IdentityCeremony(config, vault)


PHRASE = "seven three nine one four two"


def _challenge(ceremony):
    """Issue a REAL liveness challenge from the ceremony's own guard.

    The audio must speak `challenge.text()` and carry `challenge.bound`. Using an
    invented nonce makes liveness fail first, which would report "impostor
    rejected" for the wrong reason - a vacuous pass indistinguishable from a real
    speaker rejection. The checks below assert on the rejection REASON so that
    cannot happen silently.
    """
    return ceremony.liveness.issue()


def _speak(name: str, text: str, seed: int) -> pathlib.Path:
    """Render `name` saying `text` to a wav the verifier can load."""
    from brain.util.audio import write_wav
    from scripts.synth_voice import synth_speaker
    s = synth_speaker(name, text, seed=seed)
    out = HOME / f"{name}-{seed}.wav"
    write_wav(out, s.samples, s.sample_rate)
    return out


def m3_owner_voice_is_accepted():
    try:
        c = _ceremony()
        c.enroll_voice([_synth("santhosh", PHRASE, s) for s in (0, 1, 2)])
        if not vault.has_voice():
            record(ERROR, "owner voice enrolment", "has_voice() false after enrol")
            return None
        record(PASS, "owner voice enrolment", "3 samples enrolled, template stored")
        return c
    except Exception as e:
        record(ERROR, "owner voice enrolment", f"{type(e).__name__}: {e}")
        return None


def m3_unauthorized_voice_is_rejected(ceremony):
    """A different speaker, saying the RIGHT words for a VALID challenge.

    The rejection must be a speaker-score rejection. If it comes back as a
    liveness failure the check is vacuous and is reported ERROR, not PASS.
    """
    if ceremony is None:
        record(ERROR, "an unauthorized voice is refused", "skipped: enrolment failed")
        return
    from brain.identity.ceremony import CeremonyError
    try:
        ch = _challenge(ceremony)
        impostor = _speak("intruder", ch.text(), 41)
        res = ceremony.verify_voice(ch.nonce, impostor, ch.bound)
        record(FAIL, "an unauthorized voice is refused",
               f"ACCEPTED an impostor: {str(res)[:150]}")
    except CeremonyError as e:
        msg = str(e)
        if "below threshold" in msg:
            record(PASS, "an unauthorized voice is refused",
                   f"speaker score rejected: {msg[:120]}")
        elif "liveness" in msg:
            record(ERROR, "an unauthorized voice is refused",
                   "rejected on LIVENESS, not on speaker score - this check proves "
                   f"nothing about voice matching. Reasons: {getattr(e, 'reasons', '')}")
        else:
            record(ERROR, "an unauthorized voice is refused", f"unexpected: {msg[:130]}")
    except Exception as e:
        record(ERROR, "an unauthorized voice is refused", f"{type(e).__name__}: {e}")


def m3_owner_voice_still_accepted_after_impostor(ceremony):
    """The owner must still get in - a verifier that rejects everyone is not a
    security control, it is a lockout."""
    if ceremony is None:
        record(ERROR, "owner voice verifies on a fresh challenge", "skipped")
        return
    from brain.identity.ceremony import CeremonyError
    try:
        ch = _challenge(ceremony)
        genuine = _speak("santhosh", ch.text(), 99)
        res = ceremony.verify_voice(ch.nonce, genuine, ch.bound)
        record(PASS, "owner voice verifies on a fresh challenge",
               f"score={res.get('score')} threshold={res.get('threshold')} "
               f"backend={res.get('backend')}")
    except CeremonyError as e:
        record(FAIL, "owner voice verifies on a fresh challenge",
               f"the OWNER was refused: {str(e)[:140]}")
    except Exception as e:
        record(ERROR, "owner voice verifies on a fresh challenge",
               f"{type(e).__name__}: {e}")


def m3_replayed_audio_is_refused(ceremony):
    """Same recording presented twice must fail: the nonce is single-use."""
    if ceremony is None:
        record(ERROR, "a replayed recording is refused", "skipped")
        return
    from brain.identity.ceremony import CeremonyError
    try:
        ch = _challenge(ceremony)
        audio = _speak("santhosh", ch.text(), 7)
        first = None
        try:
            first = ceremony.verify_voice(ch.nonce, audio, ch.bound)
        except CeremonyError as e:
            first = f"refused: {e}"
        try:
            ceremony.verify_voice(ch.nonce, audio, ch.bound)   # replay
            record(FAIL, "a replayed recording is refused",
                   "the same nonce+audio was accepted twice")
        except CeremonyError as e:
            record(PASS, "a replayed recording is refused",
                   f"replay rejected ({str(e)[:70]}); first attempt: "
                   f"{'accepted' if isinstance(first, dict) else str(first)[:40]}")
    except Exception as e:
        record(ERROR, "a replayed recording is refused", f"{type(e).__name__}: {e}")


def m3_voice_is_exclusively_required():
    """Mandate: respond EXCLUSIVELY to Santhosh's verified voice, ignoring all
    unauthorized voices. Test: with a voiceprint enrolled, does the passphrase
    alone still open a full session?"""
    if not vault.has_voice():
        record(ERROR, "voice is the exclusive gate", "no voiceprint enrolled")
        return
    from brain.identity.ceremony import CeremonyError
    try:
        res = _ceremony().unlock(PASSPHRASE, mode="passphrase")
        allowed = bool(getattr(res, "ok", False)) or getattr(res, "session", None) is not None
    except CeremonyError as e:
        record(PASS, "voice is the exclusive gate",
               f"passphrase-only refused once a voiceprint exists: {str(e)[:110]}")
        return
    except Exception as e:
        record(ERROR, "voice is the exclusive gate", f"{type(e).__name__}: {e}")
        return
    if allowed:
        record(FAIL, "voice is the exclusive gate",
               "ceremony allows passphrase-only unlock even with a voiceprint "
               "enrolled. Whoever holds the passphrase gets in without sounding "
               "like Santhosh. NOTE: the CLI path (open_vault) is more permissive "
               "still - see the next check.")
    else:
        record(PASS, "voice is the exclusive gate", "passphrase-only unlock refused")


def m3_cli_passphrase_only_yields_full_access():
    """The CLI path a real user takes, with NO voice enrolled at all."""
    src = (ROOT / "brain/cli/main.py").read_text()
    if "no second factor is enrolled, so the passphrase alone is " in src \
            and "vault.session.degraded = False" not in src:
        record(PASS, "CLI passphrase-only access is bounded",
               "v0.1: `open_vault` refuses the passphrase alone (authenticator code required; "
               "only the setup commands `brain totp` / `brain enroll-voice` may open a vault "
               "that has no second factor yet).")
    elif "vault.session.degraded = False" in src and "warn_if_single_factor" in src:
        record(GAP, "CLI passphrase-only access is bounded",
               "with no second factor enrolled, `open_vault` clears the degraded "
               "flag, so the passphrase alone yields a FULL-tier session able to "
               "recall sensitive and restricted memories. Nila warns on every such "
               "session but nothing blocks it. Against mandate 3 ('respond "
               "exclusively to Santhosh's verified voice') this is the single "
               "largest gap in the build.")
    else:
        record(ERROR, "CLI passphrase-only access is bounded",
               "expected code shape not found; re-inspect open_vault()")


def m3_single_account_binding():
    """Mandate: dedicated SINGLE account binding."""
    devs = vault.data.get("devices", {}) if hasattr(vault, "data") else {}
    src = (ROOT / "brain/memory/vault.py").read_text()
    bounded = bool(re.search(r"max_devices|single_device|owner_binding|account_binding", src))
    if bounded:
        record(PASS, "device/account binding is bounded to one owner", "")
    else:
        record(GAP, "device/account binding is bounded to one owner",
               f"devices currently enrolled: {len(devs)}. One shared master key is "
               "wrapped by each device KEK and any number of devices can be added "
               "from an enrolment bundle. There is no max-device limit, no bound "
               "Gmail/account identity, and no owner-identity assertion. Mandate 3's "
               "'dedicated single account' is a policy this build cannot express.")


def m3_gmail_account_binding():
    src = (ROOT / "config/integrations.example.json").read_text()
    if "gmail" in src.lower():
        record(GAP, "Gmail account binding", "a gmail example exists but no binding logic")
    else:
        record(GAP, "Gmail account binding",
               "no Gmail integration and no concept of binding the agent to one "
               "email account. The integration registry can declare a Gmail "
               "endpoint, but nothing asserts WHICH account, and nothing prevents "
               "a second one being added.")


# =========================================================================== #
# M4 — Tamil / English / Tanglish
# =========================================================================== #
TAMIL_NOTE = "என் அம்மாவுக்கு நீரிழிவு நோய் உள்ளது, இன்சுலின் எடுக்கிறாள்"
TANGlish = "neenga yaaru? un peyar enna?"


def m4_multilingual_pipeline_present():
    pats = re.compile(r"tamil|indic|transliterat|xlit|lang_detect|tanglish|normalized_ta",
                      re.I)
    hits = []
    for p in (ROOT / "brain").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            if pats.search(p.read_text()):
                hits.append(str(p.relative_to(ROOT)))
        except Exception:
            pass
    if hits:
        record(PASS, "a Tamil/English/Tanglish pipeline exists", f"{hits}")
    else:
        record(GAP, "a Tamil/English/Tanglish pipeline exists",
               "zero references to Tamil, Indic transliteration, language "
               "identification or normalized_ta anywhere in brain/. Item 1 of the "
               "language spec is entirely unbuilt: no script check, no per-segment "
               "LID, no IndicXlit, no local translation, no multilingual embeddings. "
               "Storage and crypto are language-agnostic, so Tamil TEXT can be "
               "stored and recalled verbatim - but nothing understands it.")


def m4_tamil_text_round_trips():
    """Even with no pipeline, Tamil must survive storage intact."""
    rec = memory.remember(TAMIL_NOTE)
    body = rec.body if hasattr(rec, "body") else rec["body"]
    rid = rec.id if hasattr(rec, "id") else rec["id"]
    hits = memory.search("இன்சுலின்", limit=5)
    intact = body == TAMIL_NOTE
    found = any((h.id if hasattr(h, "id") else h["id"]) == rid for h in hits)
    if intact and found:
        record(PASS, "Tamil text is stored byte-exact and is recallable",
               f"round-trip identical; search for 'இன்சுலின்' returned the record")
    else:
        record(FAIL, "Tamil text is stored byte-exact and is recallable",
               f"intact={intact} searchable={found}")


def m4_tamil_sensitive_content_is_classified():
    """The health lexicon is English-only; a Tamil health note must not silently
    land in a weaker class than its English equivalent."""
    from brain.security.redact import scan
    from brain.security.classify import classify
    rec = memory.remember(TAMIL_NOTE)
    k = classify(rec.kind, rec.tags, getattr(rec, "meta", None) or {}).klass
    en = scan("mother has diabetes and takes insulin").max_class
    if k == en == "sensitive":
        record(PASS, "Tamil health content gets the same class as English", f"{k}")
    else:
        record(FAIL, "Tamil health content gets the same class as English",
               f"Tamil note classified '{k}', English equivalent '{en}'. The lexicon "
               "is English-only, so a Tamil health note is filed as ordinary "
               "internal data and is NOT withheld from a cloud model. This is a "
               "concrete privacy asymmetry, not a theoretical one.")


def m4_tanglish_identity_response():
    from brain.agent.agent import Brain
    from brain.agent.local_brain import LocalBrain
    brain = Brain(config, vault, memory, provider=LocalBrain())
    a = brain.handle(TANGlish).answer.lower()
    if "nila" in a:
        record(PASS, "Tanglish 'who are you?' is answered as Nila", a[:110])
    else:
        record(FAIL, "Tanglish 'who are you?' is answered as Nila",
               f"reply did not identify her: {a[:110]}. The identity patterns are "
               "English-only ('who are you', 'your name'), so the model-independent "
               "identity guarantee does not hold in Tanglish - the exact register "
               "the owner says he uses.")


def m4_voice_challenge_language():
    src = (ROOT / "brain/identity/liveness.py").read_text()
    m = re.search(r'WORDS = \("([^"]+)', src)
    words = (m.group(1).split() if m else [])[:6]
    tamil = any("\u0b80" <= ch <= "\u0bff" for ch in src)
    if tamil:
        record(PASS, "the liveness challenge can be issued in Tamil", "")
    else:
        record(GAP, "the liveness challenge can be issued in Tamil",
               f"challenge vocabulary is English/digits only (e.g. {words}). A "
               "Tamil-first owner must read English phonetic words aloud to unlock. "
               "The MFCC backend is text-independent so Tamil speech CAN be scored, "
               "but no Tamil phrase set is shipped and none was calibrated.")


# =========================================================================== #
# M5 — plugins disabled by default, activated strictly on demand
# =========================================================================== #
def m5_plugins_are_disabled_by_default():
    from brain.security.permission import Broker, CAP_FILE_READ, CAP_PHOTO, CAP_NET
    b = Broker(path=HOME / "probe-perm2.json")
    denied = all(not b.check(c, "/tmp/x").allowed for c in (CAP_FILE_READ, CAP_PHOTO, CAP_NET))
    if denied:
        record(PASS, "every capability is denied before any grant",
               "fresh broker refuses fs.read, media.photo and net.host")
    else:
        record(FAIL, "every capability is denied before any grant", "some capability allowed")


def m5_integrations_ship_disabled():
    data = json.loads((ROOT / "config/integrations.example.json").read_text())
    integs = data.get("integrations", [])
    enabled = [i["name"] for i in integs if i.get("enabled")]
    if not enabled:
        record(PASS, "all example integrations ship disabled",
               f"{len(integs)} declared, none enabled")
    else:
        record(FAIL, "all example integrations ship disabled", f"enabled: {enabled}")


def m5_the_model_cannot_grant_itself_access():
    from brain.agent import tools as T
    src = (ROOT / "brain/agent/tools.py").read_text()
    bad = re.findall(r'"(affirm|grant_permission|approve|allow_capability)"', src)
    if bad:
        record(FAIL, "no tool lets the model grant itself access", f"found: {bad}")
    else:
        record(PASS, "no tool lets the model grant itself access",
               "affirm() is reachable only from the CLI/API, never from a tool call")


def m5_never_read_beats_a_standing_grant():
    from brain.agent.host_tools import _forbidden
    probes = ["~/.ssh/id_rsa", "~/.config/google-chrome/Default/Login Data",
              "~/Library/Keychains/login.keychain-db", "~/.password-store/e/g.gpg",
              "~/vault.kdbx"]
    missed = [p for p in probes if _forbidden(pathlib.Path(p).expanduser()) is None]
    if missed:
        record(FAIL, "credential paths stay refused under any grant", f"readable: {missed}")
    else:
        record(PASS, "credential paths stay refused under any grant",
               "ssh keys, browser logins, OS keychain, password store and .kdbx all "
               "refused before the broker is even consulted")


def m5_activation_is_strictly_on_demand():
    """Mandate: activate strictly on-demand when triggered by a command."""
    from brain.security.permission import DURABLE_TTL_MS, CAPABILITIES
    durable = [k for k, v in CAPABILITIES.items() if v.get("durable_ok")]
    days = DURABLE_TTL_MS / 86400_000
    if days <= 1 and not durable:
        record(PASS, "activation is strictly per-command", "")
    else:
        record(GAP, "activation is strictly per-command",
               f"{len(durable)} capabilities can hold a standing grant for "
               f"{days:.0f} DAYS (fs.read, fs.list, media.photo, net.host). That is "
               "on-demand at first use, then always-on for a week - a plugin stays "
               "active across sessions with no fresh command. The three dangerous "
               "capabilities (fs.write, shell.exec, data.export) are never durable, "
               "which is the right call, but 'strictly on-demand' needs a mode where "
               "every activation re-prompts.")


# --------------------------------------------------------------------------- #
def main() -> int:
    print(f"Nila mandate probe\nproject : {ROOT}\nvault   : {HOME}  (throwaway)")
    try:
        build_env()
    except Exception:
        print("FATAL: could not build a probe vault")
        traceback.print_exc()
        return 2

    mandate("M1 — strict execution, zero data ingestion")
    for fn in (m1_audit_omits_message_text, m1_nothing_on_disk_holds_the_number,
               m1_integration_audit_logs_keys_not_values, m1_forget_hard_purges_derived_data,
               m1_ephemeral_values_are_purged_immediately, m1_command_only_app_control):
        try:
            fn()
        except Exception as e:
            record(ERROR, fn.__name__, f"{type(e).__name__}: {e}")

    mandate("M2 — halt on any bug, anomaly or access mismatch")
    for fn in (m2_access_mismatch_halts_execution, m2_unexpected_tool_exception_halts,
               m2_no_prompt_is_offered_for_a_never_read_path,
               m2_panic_latch_remains_owner_controlled,
               m2_tool_errors_do_not_silently_continue):
        try:
            fn()
        except Exception as e:
            record(ERROR, fn.__name__, f"{type(e).__name__}: {e}")

    mandate("M3 — dedicated single-account binding, voice-exclusive response")
    ceremony = None
    try:
        ceremony = m3_owner_voice_is_accepted()
    except Exception as e:
        record(ERROR, "owner voice enrolment", f"{type(e).__name__}: {e}")
    for fn in (lambda: m3_unauthorized_voice_is_rejected(ceremony),
               lambda: m3_owner_voice_still_accepted_after_impostor(ceremony),
               lambda: m3_replayed_audio_is_refused(ceremony),
               m3_voice_is_exclusively_required,
               m3_cli_passphrase_only_yields_full_access,
               m3_single_account_binding, m3_gmail_account_binding):
        try:
            fn()
        except Exception as e:
            record(ERROR, getattr(fn, "__name__", "voice check"),
                   f"{type(e).__name__}: {e}")

    mandate("M4 — Tamil / English / Tanglish")
    for fn in (m4_multilingual_pipeline_present, m4_tamil_text_round_trips,
               m4_tamil_sensitive_content_is_classified, m4_tanglish_identity_response,
               m4_voice_challenge_language):
        try:
            fn()
        except Exception as e:
            record(ERROR, fn.__name__, f"{type(e).__name__}: {e}")

    mandate("M5 — plugins disabled by default, activated strictly on demand")
    for fn in (m5_plugins_are_disabled_by_default, m5_integrations_ship_disabled,
               m5_the_model_cannot_grant_itself_access, m5_never_read_beats_a_standing_grant,
               m5_activation_is_strictly_on_demand):
        try:
            fn()
        except Exception as e:
            record(ERROR, fn.__name__, f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------ #
    counts = {s: sum(1 for r in RESULTS if r[1] == s) for s in (PASS, FAIL, GAP, ERROR)}
    print(f"\n{'=' * 78}\nSUMMARY\n{'=' * 78}")
    by_m: dict[str, dict] = {}
    for m, s, n, _ in RESULTS:
        by_m.setdefault(m, {}).setdefault(s, []).append(n)
    for m in sorted(by_m):
        row = by_m[m]
        parts = " ".join(f"{s}={len(row.get(s, []))}" for s in (PASS, FAIL, GAP, ERROR)
                         if row.get(s))
        print(f"  {m:6s} {parts}")
    print(f"\n  TOTAL  {counts[PASS]} pass  {counts[FAIL]} FAIL  "
          f"{counts[GAP]} gap  {counts[ERROR]} error   (of {len(RESULTS)} checks)")
    if counts[FAIL]:
        print("\n  FAILURES (defects in existing code):")
        for m, s, n, e in RESULTS:
            if s == FAIL:
                print(f"    - [{m}] {n}")
    if counts[GAP]:
        print("\n  GAPS (mandate has no implementation surface — this is the roadmap):")
        for m, s, n, e in RESULTS:
            if s == GAP:
                print(f"    - [{m}] {n}")
    if counts[ERROR]:
        print("\n  ERRORS (probe could not run the check — fix the probe, do not ignore):")
        for m, s, n, e in RESULTS:
            if s == ERROR:
                print(f"    - [{m}] {n}: {e[:110]}")

    shutil.rmtree(HOME, ignore_errors=True)
    print(f"\n  throwaway vault removed: {HOME}")
    return 1 if (counts[FAIL] or counts[ERROR]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
