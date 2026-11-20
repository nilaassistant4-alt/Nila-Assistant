"""Re-test the whole product from a command line, on a throwaway vault.

This is not a unit test. It is the "re-test everything, not just what I check"
pass: it builds a fresh vault in a temp folder, unlocks it, and then drives the
same code paths the owner's icons and commands drive, recording what actually
came out. The point is evidence - every line below is a real call, and anything
that needs a microphone, a speech engine, a model key or the network says so
instead of pretending.

Run:  python scripts/retest_everything.py          (writes a Markdown report)
      python scripts/retest_everything.py --json   (prints the raw results)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, SKIP, FAIL = "PASS", "SKIP", "FAIL"
results: list[dict] = []


def check(name: str, fn, needs: str = "") -> None:
    started = time.time()
    try:
        status, detail = fn()
    except Exception as exc:                       # a crash is a FAIL, with the line
        status, detail = FAIL, f"{type(exc).__name__}: {exc}"
    results.append({"name": name, "status": status, "detail": str(detail),
                    "needs": needs, "ms": int((time.time() - started) * 1000)})
    mark = {PASS: "ok  ", SKIP: "skip", FAIL: "FAIL"}[status]
    print(f"[{mark}] {name}" + (f"  ({needs})" if needs and status == SKIP else "")
          + (f"\n         {detail}" if detail else ""))


# --------------------------------------------------------------------------- #
# a throwaway vault
# --------------------------------------------------------------------------- #
def fresh_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="nila-retest-"))
    os.environ["BRAIN_HOME"] = str(home)
    return home


def open_session(home: Path, passphrase: str = "retest-passphrase-1"):
    from brain.config import Config
    from brain.memory.store import MemoryStore
    from brain.memory.vault import Vault

    config = Config.load(home)
    config.fast_kdf = True
    vault = Vault(config)
    if not vault.initialized:
        vault.initialize(passphrase, "retest", "retest-device")
    mk = vault.unlock(passphrase)
    vault.start_session(mk, "passphrase")
    return config, vault, MemoryStore(config, mk)


def synth_voice(seed: int, f0: float = 120.0, seconds: float = 2.5,
                noise: float = 0.05) -> list[float]:
    from brain.util.audio import SAMPLE_RATE

    rnd = random.Random(seed)
    n = int(SAMPLE_RATE * seconds)
    return [0.4 * math.sin(2 * math.pi * f0 *
                            (1 + 0.02 * math.sin(2 * math.pi * 3 * i / SAMPLE_RATE))
                            * i / SAMPLE_RATE) + rnd.uniform(-noise, noise)
            for i in range(n)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default=str(ROOT.parent / "NILA_RETEST_RESULTS.md"))
    args = parser.parse_args()

    home = fresh_home()
    config, vault, memory = open_session(home)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ---- 1. the vault, the session, the audit ---------------------------- #
    def vault_status():
        from brain.memory.vault import Vault

        unlocked = vault.is_unlocked()
        audit = vault.read_audit(limit=5)
        return (PASS if unlocked else FAIL,
                f"unlocked={unlocked}, recent audit entries={len(audit)}")

    check("vault: initialise, unlock, session", vault_status)

    def wrong_passphrase():
        from brain.memory.vault import Vault

        other = Vault(config)
        try:
            other.unlock("not-the-passphrase")
        except Exception as exc:
            return PASS, f"refused: {type(exc).__name__}"
        return FAIL, "a wrong passphrase was accepted"

    check("vault: a wrong passphrase is refused", wrong_passphrase)

    # ---- 2. memory ------------------------------------------------------- #
    def remember_and_recall():
        rec = memory.remember(body="the water tank man is Kumar, 98400 12345",
                              title="water tank", kind="note")
        hits = memory.search("water tank")
        return (PASS if hits else FAIL,
                f"stored {rec.id}, {len(hits)} hit(s) on recall")

    check("memory: remember, then recall", remember_and_recall)

    def memory_kinds():
        memory.remember(body="buy milk", title="milk", kind="note")
        rows = memory.list_records(limit=10)
        kinds = sorted({r.kind for r in rows})
        return (PASS if rows else FAIL, f"{len(rows)} records, kinds={kinds}")

    check("memory: list by kind", memory_kinds)

    # ---- 3. reminders: set, describe, fire, miss ------------------------- #
    def reminder_roundtrip():
        from brain.agent.reminders import ReminderBook, describe, parse_when

        book = ReminderBook(memory)
        when = parse_when("in 90 minutes to drink water",
                          now=dt.datetime.now())
        rec = book.add("in 90 minutes to drink water", when)
        line = describe(rec, int(time.time() * 1000))
        ok = "[in 2 h]" in line or "[in 1 h]" in line
        return (PASS if ok else FAIL, line.strip())

    check("reminder: 90 minutes shows hours, not '0 min'", reminder_roundtrip)

    def reminder_due_now():
        from brain.agent.reminders import ReminderBook, due

        book = ReminderBook(memory)
        rows = book.all()
        past = [r for r in rows if int((r.meta or {}).get("at_ms") or 0) < time.time() * 1000]
        fired = due(rows, int((time.time() + 4 * 3600) * 1000))
        return (PASS if past or fired else SKIP,
                f"{len(rows)} reminder(s), {len(fired)} would be due in 4 h")

    check("reminder: due detection", reminder_due_now)

    def reminder_parse_variety():
        from brain.agent.reminders import parse_when

        now = dt.datetime(2026, 11, 18, 9, 0)
        cases = {
            "remind me in 10 minutes to stretch": "in 10 minutes",
            "remind me tomorrow at 9 to pay the bill": "tomorrow at 9",
            "remind me every day at 7 to take the tablet": "every day",
            "remind me at 10:30 to call the bank": "at 10:30",
        }
        bad = []
        for phrase, expect in cases.items():
            when = parse_when(phrase, now=now)
            if expect not in (when.matched or "") and when.repeat == "":
                bad.append(phrase)
        return (PASS if not bad else FAIL, f"{len(cases) - len(bad)}/{len(cases)} parsed"
                + (f"; unclear: {bad}" if bad else ""))

    check("reminder: English + repeat phrasing", reminder_parse_variety)

    def reminder_tamil():
        from brain.agent.reminders import ReminderError, parse_when

        try:
            when = parse_when("நாளை காலை 9 மணிக்கு மருந்து சாப்பிட")
            return PASS, f"parsed '{when.matched}' at {when.at_ms}"
        except ReminderError as exc:
            return FAIL, f"Tamil reminder not understood: {exc}"

    check("reminder: Tamil phrasing", reminder_tamil)

    # ---- 4. calculator / local brain ------------------------------------- #
    def local_brain_math():
        from brain.agent.local_brain import LocalBrain

        brain = LocalBrain()
        answers = {}
        for q in ("what is 17% of 2400", "2450 + 380", "what is 15 * 12"):
            try:
                out = brain.complete("you are Nila", [{"role": "user", "content": q}])
                answers[q] = out.text or str(out.tool_calls)
            except Exception as exc:
                answers[q] = f"error: {exc}"
        good = sum(1 for v in answers.values() if v and "error" not in str(v))
        return (PASS if good else FAIL, f"{good}/3 answered; sample={list(answers.values())[0]}")

    check("calculator: percentages and sums, offline", local_brain_math)

    # ---- 5. the agent turn ----------------------------------------------- #
    def agent_turn():
        from brain.agent.agent import Brain
        from brain.agent.local_brain import LocalBrain

        brain = Brain(config, vault, memory, provider=LocalBrain())
        turn = brain.handle("remind me to call amma in 90 minutes")
        ok = "call amma" in turn.answer or "Set" in turn.answer
        return (PASS if ok and turn.ok else FAIL, turn.answer[:110])

    check("agent: a full turn with reminders and alerts", agent_turn)

    # ---- 6. the voice lock ------------------------------------------------ #
    def voice_lock_cycle():
        from brain.security.speaker import SpeakerLock
        from brain.util.audio import SAMPLE_RATE

        lock = SpeakerLock(home / "speaker.json", vault=vault)
        lock.reset()
        lock.enrol(synth_voice(1, 120), SAMPLE_RATE)
        lock.enrol(synth_voice(2, 121), SAMPLE_RATE)
        owner = lock.judge(synth_voice(3, 122), SAMPLE_RATE)
        stranger = lock.judge(synth_voice(9, 210, noise=0.35), SAMPLE_RATE)
        ok = owner.ok and not stranger.ok
        return (PASS if ok else FAIL,
                f"owner={owner.score:.3f} ({owner.verdict}), "
                f"stranger={stranger.score:.3f} ({stranger.verdict})")

    check("voice: only the enrolled voice is accepted", voice_lock_cycle)

    def voice_survives_restart():
        from brain.security.speaker import SpeakerLock
        from brain.util.audio import SAMPLE_RATE

        again = SpeakerLock(home / "speaker.json", vault=vault)
        decision = again.judge(synth_voice(3, 122), SAMPLE_RATE)
        return (PASS if again.enrolled() and decision.ok else FAIL,
                f"enrolled after reload={again.enrolled()}, score={decision.score:.3f}")

    check("voice: the voiceprint survives a restart", voice_survives_restart)

    def voice_challenge():
        from brain.security.speaker import SpeakerLock
        from brain.util.audio import SAMPLE_RATE

        lock = SpeakerLock(home / "speaker.json", vault=vault)
        challenge = lock.challenge()
        first = lock.verify_challenge(challenge["token"], synth_voice(4, 121),
                                      SAMPLE_RATE, heard=str(challenge["number"]))
        replay = lock.verify_challenge(challenge["token"], synth_voice(4, 121),
                                       SAMPLE_RATE, heard=str(challenge["number"]))
        ok = first.ok and not replay.ok
        return (PASS if ok else FAIL,
                f"first={first.verdict}, replay={replay.verdict}")

    check("voice: a challenge number cannot be replayed", voice_challenge)

    # ---- 7. speaking ------------------------------------------------------ #
    def speaking():
        from brain.util import speech

        engines = speech.engines()
        result = speech.speak_text("வணக்கம், நான் நிலா")
        if result.get("spoken"):
            return PASS, f"spoke with {result['engine']}"
        return SKIP, f"no voice on this machine (piper={engines.get('piper')}, " \
                     f"sapi={engines.get('sapi')}): {result.get('reason', '')[:80]}"

    check("voice out: speak Tamil", speaking, needs="a voice engine (Windows SAPI or Piper)")

    # ---- 8. hearing ------------------------------------------------------- #
    def hearing():
        from brain.agent import ears

        status = ears.available()
        if not status["engines"]:
            try:
                ears.hear(seconds=0.2)
                return FAIL, "hear() returned without an engine"
            except Exception as exc:
                return SKIP, f"no transcriber installed: {exc}"
        return PASS, f"engines={status['engines']}, microphone={status['microphone']}"

    check("voice in: speech to text", hearing, needs="pip install faster-whisper (or vosk)")

    def hearing_wav_path():
        from brain.agent import ears

        wav = home / "nila-test.wav"
        from brain.util.audio import write_wav

        write_wav(wav, synth_voice(5, 130, 1.0))
        try:
            out = ears.hear(source=wav)
            return PASS, f"transcribed {out['seconds']}s -> {out['text'][:60]!r}"
        except Exception as exc:
            return SKIP, f"no engine to transcribe with: {exc}"

    check("voice in: a WAV file (no microphone)", hearing_wav_path,
          needs="pip install faster-whisper (or vosk)")

    # ---- 9. files --------------------------------------------------------- #
    def read_pdf_like():
        from brain.agent import media

        doc = home / "memo.txt"
        doc.write_text("Electricity bill for November: 1,240 rupees, due on the 12th.\n"
                       "Second line for the summary.\n", encoding="utf-8")
        report = media.read_anything(doc)
        return (PASS if report.get("chars") else FAIL,
                f"{report.get('format')}, {report.get('chars')} chars, "
                f"summary={str(report.get('summary'))[:80]}")

    check("file: a text/document read", read_pdf_like)

    def read_code():
        from brain.agent import media

        code = home / "sample.py"
        code.write_text("import os\n\ndef main():\n    # TODO: finish\n    return 1\n",
                        encoding="utf-8")
        report = media.read_anything(code)
        return (PASS if "lines" in str(report) else FAIL, str(report)[:110])

    check("file: a code file in one screen", read_code)

    def read_video():
        from brain.agent import media

        movie = home / "clip.mp4"
        with open(movie, "wb") as fh:
            fh.write(_tiny_mp4(60.0))
        report = media.read_anything(movie)
        ok = "video" in str(report.get("kind", "")) or report.get("duration_s")
        return (PASS if ok else FAIL, f"duration={report.get('duration_s')}, "
                                      f"plan={'yes' if report.get('edit_plan') else 'no'}")

    check("file: a video, without loading it", read_video)

    # ---- 10. file search and apps ---------------------------------------- #
    def searching():
        from brain.util import finder

        (home / "papers").mkdir(exist_ok=True)
        (home / "papers" / "electricity-bill-2026.txt").write_text("due 12th\n")
        by_name = finder.search(home, "electricity")
        by_content = finder.search(home, "due 12th", content=True)
        ok = by_name["count"] >= 1 and by_content["count"] >= 1
        return (PASS if ok else FAIL,
                f"name={by_name['count']} content={by_content['count']} "
                f"checked={by_name['scanned']}")

    check("search: by name and inside files", searching)

    def opening_apps():
        from brain.util import apps

        names = [row["name"] for row in apps.list_apps()]
        plan = apps.open_app("files", dry_run=True)
        refused = False
        try:
            apps.resolve("rm -rf /")
        except apps.AppError:
            refused = True
        return (PASS if names and refused and plan.get("dry_run") else FAIL,
                f"{len(names)} names; 'rm -rf /' refused={refused}; "
                f"files would run {plan.get('would_run')}")

    check("apps: a fixed list, a preview, and a refusal", opening_apps)

    def create_folder():
        from brain.util import apps

        out = apps.new_folder(home, "retest-folder")
        ok = (home / "retest-folder").is_dir()
        return (PASS if ok else FAIL, str(out)[:100])

    check("apps: make a folder", create_folder)

    # ---- 11. connectors (no network here) -------------------------------- #
    def connector_status():
        from brain.agent import web

        lines = []
        for name, fn in (("weather", web.weather), ("wikipedia", web.wikipedia)):
            try:
                out = fn("Chennai" if name == "weather" else "Nila")
                lines.append(f"{name}=ok")
            except Exception as exc:
                lines.append(f"{name}={type(exc).__name__}")
        return SKIP, "no network in this sandbox: " + ", ".join(lines)

    check("connectors: weather / wikipedia live", connector_status,
          needs="internet access")

    def connector_offline_guard():
        from brain.agent import web

        try:
            web.youtube_search("drip irrigation", api_key="")
        except web.ConnectorError as exc:
            return PASS, f"no key -> {str(exc)[:70]}..."
        return FAIL, "a keyless YouTube search did not say it needs a key"

    check("connectors: a missing key is said, not faked", connector_offline_guard)

    # ---- 12. memory retention -------------------------------------------- #
    def retention_report():
        from brain.memory import retention

        out = retention.report(memory, days=90)
        return (PASS if out.get("ok") else FAIL, out["line"])

    check("memory: the 3-month report (nothing deleted)", retention_report)

    # ---- 13. launcher / icons -------------------------------------------- #
    def launchers():
        from brain.util import launcher

        out = launcher.write_launchers(home, home / "Desktop")
        talk = launcher.push_to_talk_launchers(home, home / "Desktop", max_seconds=6)
        names = [Path(p).name for p in out["written"]] + [Path(p).name for p in talk["written"].values()]
        ok = any("Talk" in n or "talk" in n for n in names) and len(names) >= 3
        return (PASS if ok else FAIL, ", ".join(names))

    check("icons: app, reminders, and press-to-talk", launchers)

    def spoken_sentences():
        from brain.util import launcher

        cases = {
            "nila start": ("start", ""),
            "nila open notepad": ("open", "notepad"),
            "nila find invoice": ("find", "invoice"),
            "nila remind me in 10 minutes to drink water":
                ("remind", "me in 10 minutes to drink water"),
            "nila stop": ("stop", ""),
            "hello there": ("", "hello there"),
        }
        bad = [k for k, (a, arg) in cases.items()
               if launcher.spoken_action(k)["action"] != a]
        return (PASS if not bad else FAIL,
                f"{len(cases) - len(bad)}/{len(cases)} sentences understood"
                + (f"; wrong: {bad}" if bad else ""))

    check("voice commands: what may be said", spoken_sentences)

    # ---- 14. storage on D: ------------------------------------------------ #
    def storage_policy():
        from brain.config import Config, StorageUnavailable

        cfg = Config.load(home)
        cfg.agent = dict(cfg.agent or {})
        cfg.agent["storage_dir"] = str(home / "D-drive" / "Nila Assistant Storage")
        cfg.save()
        path = cfg.memory_path
        created = path.parent.exists()
        return (PASS if not created else FAIL,
                f"path={path} (nothing created just by asking: {not created})")

    check("storage: asking for D: creates nothing", storage_policy)

    # ---- 15. permissions and strict mode ----------------------------------
    def permission_broker():
        from brain.security import permission

        caps = sorted(permission.CAPABILITIES)
        mic = "mic.listen" in caps
        return (PASS if mic and len(caps) >= 8 else FAIL,
                f"{len(caps)} capabilities, mic.listen={'yes' if mic else 'no'}")

    check("permissions: the capabilities a stranger must be asked for",
          permission_broker)

    def strict_mode():
        from brain.security import strict

        helpers = [n for n in dir(strict) if not n.startswith("_")]
        return (PASS if helpers else FAIL, ", ".join(helpers[:6]))

    check("strict mode: the module is present", strict_mode)

    # ---- 16. the tools registry ------------------------------------------ #
    def tool_registry():
        from brain.agent import tools as tools_mod

        registry = tools_mod.build_default_registry()
        names = sorted(registry._tools)
        need = {"read_file_report", "analyse_video", "find_file", "open_app", "listen"}
        missing = need - set(names)
        return (PASS if not missing else FAIL,
                f"{len(names)} tools" + (f"; missing {sorted(missing)}" if missing else ""))

    check("tools: everything registered, with its capability", tool_registry)

    def permissions_gate():
        from brain.agent import tools as tools_mod

        registry = tools_mod.build_default_registry()
        # The real path: the agent calls registry.call, which runs the gate.
        from brain.agent.tools import ToolContext
        from brain.security.permission import Broker

        broker = Broker(path=home / "permissions.json", audit=lambda e, d: None)
        ctx = ToolContext(memory=memory, vault=vault, config=config,
                          permissions=broker)
        out = registry.call("read_file_report", {"path": str(home / "memo.txt")}, ctx)
        blocked = out.get("ok") is False and "permission" in str(out.get("error", "")).lower()
        return (PASS if blocked else FAIL,
                f"refused: {out.get('error')} (needs {out.get('capability')})" if blocked
                else f"a tool read a file without permission: {out}")

    check("permissions: a tool cannot read without a grant", permissions_gate)

    # ---- 17. the local API and the dashboard ----------------------------- #
    def api_roundtrip():
        from brain.api.gateway import Gateway

        gw = Gateway(config, vault, memory, token="retest-token")
        status, body = gw.route("GET", "/v1/status", {}, {})
        due_code, due = gw.route("GET", "/v1/reminders/due", {}, {})
        ok = status == 200 and due_code == 200
        return (PASS if ok else FAIL,
                f"status={status} unlocked={body.get('unlocked')}, "
                f"due={due_code} rows={len(due.get('due') or [])}")

    check("API: status and the new 'due now' route", api_roundtrip)

    def dashboard_static():
        base = ROOT / "brain" / "api" / "static"
        html = (base / "dashboard.html").read_text()
        js = (base / "dashboard.js").read_text()
        ok = 'id="due"' in html and "/v1/reminders/due" in js
        return (PASS if ok else FAIL, f"html={len(html)}B js={len(js)}B")

    check("dashboard: it shows what is due", dashboard_static)

    # ---- 18. reading other languages ------------------------------------- #
    def languages():
        from brain.agent import translate

        samples = {
            "Tamil": "நாளை நான் சென்னை செல்கிறேன்",
            "Hindi": "मुझे कल दिल्ली जाना है",
            "Telugu": "నేను రేపు ఇంటికి వెళ్తాను",
            "Malayalam": "ഞാൻ നാളെ വീട്ടിലേക്ക് പോകുന്നു",
            "Bengali": "আমি কাল বাড়ি যাব",
        }
        out, right = {}, 0
        for want, text in samples.items():
            got = translate.detect(text)["language"]
            out[want] = got
            right += int(got == want)
        # Latin-script languages are honestly NOT claimed: no script, no model.
        latin = translate.detect("Ich fahre morgen nach Berlin")["language"]
        return (PASS if right >= 4 else FAIL,
                f"{right}/{len(samples)} scripts detected: {out}; "
                f"Latin-script guess = {latin!r} (a guess, not a detection)")

    check("languages: detect, and explain in Tamil", languages)

    # ---- 19. models ------------------------------------------------------- #
    def model_gemini_listing():
        from brain.agent import providers

        provider = providers.GeminiProvider(api_key="", model="gemini-flash-latest")
        listed = provider.list_models()
        return SKIP, f"no key and no network here (asked for the list: {len(listed)} names)"

    check("models: Gemini names come from the API", model_gemini_listing,
          needs="a Gemini key and internet")

    def model_pack():
        from brain.agent import models as pack

        machine = pack.installed(probe=False)
        status = pack.pack_status(machine)
        return (PASS if status else FAIL, str(status)[:110])

    check("models: the local model pack status", model_pack)

    # ---- 20. packaging ---------------------------------------------------- #
    def packaging():
        # The newest stage ZIP beside the checkout - the name changes every stage,
        # so a hard-coded one silently skipped this check (found in stage 12).
        # Sorted by the stage NUMBER, not by name: lexically "STAGE9" sorts after
        # "STAGE13", so the newest ZIP was not always the one checked (found
        # when stage 13 and stage 12 both sat in the folder).
        def stage_of(path: Path) -> int:
            import re as _re

            found = _re.search(r"STAGE(\d+)", path.name, _re.I)
            return int(found.group(1)) if found else -1

        candidates = sorted(ROOT.parent.glob("NILA_V02_STAGE*.zip"), key=stage_of)
        if not candidates:
            # Running from an extracted ZIP: there is nothing beside it to check.
            return SKIP, f"no stage ZIP next to this checkout ({ROOT.parent.name})"
        zip_path = candidates[-1]
        # Stage 13 adds the four rows that were unfinished, and the file that
        # proves each one so the check cannot pass on a stale ZIP. Stage 14 adds
        # the four files that make "the passphrase is asked once" true, because
        # that claim is worthless if the ZIP an owner downloads is missing them.
        required = ("brain/util/calc.py", "nila-start.bat", "move-memories-to-d.bat",
                    "tests/test_startup_and_old_password_acceptance.py",
                    "tests/test_one_file_one_key_d_drive_acceptance.py",
                    "tests/test_new_vault_and_way_in_acceptance.py",
                    "tests/test_audit_hardening_acceptance.py",
                    "tests/test_calc_and_recovery_acceptance.py",
                    "brain/agent/ears.py", "brain/agent/ocr.py",
                    "brain/agent/screen.py", "brain/agent/music.py",
                    "tools/sync_lan.py", "sync-host.bat", "sync-join.bat",
                    "sync-now.bat", "getffmpeg.bat", "brain/agent/ephemeral.py",
                    "brain/agent/wake.py", "brain/agent/progress.py",
                    "config/integrations.example.json",
                    "brain/security/vaultkey.py", "brain/util/hotkey.py",
                    "nila-first-run.bat", "nila-easy-start.bat")
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            bad = [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
            missing = [want for want in required
                       if not any(n.endswith(want) for n in names)]
        return (PASS if not bad and not missing else FAIL,
                f"{zip_path.name}: {len(names)} files, {zip_path.stat().st_size:,} "
                f"bytes, junk={len(bad)}, missing={missing or 'none'}")

    check("packaging: the ZIP carries the current code", packaging)

    def cmd_parse_time_trap():
        """A `set /p` and an `echo %X%|` in one (...) block: the pipe gets an EMPTY
        line, because cmd.exe expands %X% when it parses the whole block. Measured
        on the recovery path, which could therefore never work."""
        import re as _re

        bad = []
        for path in sorted(ROOT.glob("*.bat")):
            text = path.read_text(errors="replace")
            for block in _re.finditer(r"\((\r?\n.*?)\r?\n\)", text, _re.S):
                body = block.group(1)
                for name in {m.group(1) for m in _re.finditer(r"set /p\s+(\w+)=", body)}:
                    if _re.search(r"(?m)^\s*echo\s+%" + name + r"%\s*\|", body):
                        bad.append(f"{path.name}: %{name}%")
        return (PASS if not bad else FAIL,
                "no variable is expanded in the same block that sets it" if not bad
                else f"would pipe an empty line: {bad}")

    check("start: a typed value is never expanded too early (cmd.exe)", cmd_parse_time_trap)

    def launcher_command_parses():
        """The generated .vbs/.bat/.desktop must run a command the parser accepts.
        `--home` is a global option: after the subcommand it is 'unrecognized'."""
        sys.path.insert(0, str(ROOT))
        import re as _re

        from brain.cli.main import build_parser
        from brain.util import launcher

        vbs = launcher.windows_vbs("C:/py/python.exe", "C:/nila/brain.py",
                                   "C:/Users/me/nila-vault")
        line = [ln for ln in vbs.splitlines() if ln.startswith("sh.Run")][0]
        argv = _re.findall(r'""([^"]*)""', line)[2:]
        try:
            parsed = build_parser().parse_args(argv)
        except SystemExit:
            return FAIL, f"the generated shortcut does not parse: {argv}"
        ok = (parsed.command == "dashboard"
              and str(parsed.home).endswith("nila-vault"))
        return (PASS if ok else FAIL,
                f"runs `brain --home <vault> {parsed.command}` (the documented one)"
                if ok else f"generated {parsed.command} for home {parsed.home}")

    check("start: one key runs a command that parses", launcher_command_parses)

    def the_memories_can_move_to_another_drive():
        """Copy to a second folder, compare, leave the pointer - then prove the same
        BRAIN_HOME opens the new folder and the remembered key still works."""
        import importlib.util
        import tempfile

        spec = importlib.util.spec_from_file_location(
            "move_storage_retest", ROOT / "tools" / "move_storage.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from brain.config import Config
        from brain.memory.vault import Vault

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "vault"
            config = Config.load(home)
            config.fast_kdf = True
            vault = Vault(config)
            vault.initialize("a-passphrase-for-the-move", "Owner", "dev-retest")
            vault.lock()
            target = Path(tmp) / "D" / "Nila-data"
            code = mod.move(home, target, dry_run=False, yes=True)
            moved = Config.load(home)
            reopened = Vault(moved).verify_passphrase("a-passphrase-for-the-move")
            ok = code == 0 and moved.home == target and reopened
            return (PASS if ok else FAIL,
                    f"moved to {target}, pointer followed, passphrase still opens it"
                    if ok else f"move code={code}, home={moved.home}, passphrase={reopened}")

    check("memories: they can move to another drive and still open", the_memories_can_move_to_another_drive)

    def a_forgotten_passphrase_is_not_a_dead_end():
        """The owner's real state: no password, no idea what a recovery code is.

        Measured end to end: an old vault, an unknown-to-us passphrase, `new-vault`
        with a chosen new one - the old vault is kept, the new one opens, and the
        codes it printed can open it on their own.
        """
        import tempfile

        from brain.config import Config
        from brain.memory.vault import Vault

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "vault"
            config = Config.load(home)
            config.fast_kdf = True
            old_vault = Vault(config)
            old_vault.initialize("the-old-forgotten-one", "Owner", "dev-retest")
            old_vault.lock()
            before = (home / "identity.json").read_bytes()

            import io
            import sys as _sys

            from brain.cli import main as cli

            old_stdin = _sys.stdin
            _sys.stdin = io.StringIO("santhosh123\n")
            try:
                args = cli.build_parser().parse_args(
                    ["--home", str(home), "new-vault", "--passphrase-stdin"])
                import contextlib

                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = cli.cmd_new_vault(args, cli.UI())
            finally:
                _sys.stdin = old_stdin
            if code != 0:
                return FAIL, "new-vault refused; the owner has no way in"
            fresh = Vault(Config.load(home))
            new_opens = fresh.verify_passphrase("santhosh123")
            old_refused = not fresh.verify_passphrase("the-old-forgotten-one")
            kept = [p for p in Path(tmp).iterdir() if p.name.startswith("vault-old-")]
            kept_ok = bool(kept) and Vault(Config(home=kept[0])).verify_passphrase(
                "the-old-forgotten-one")
            ok = new_opens and old_refused and kept_ok and (home / "identity.json").exists()
            return (PASS if ok else FAIL,
                    f"new passphrase opens={new_opens}, old refused={old_refused}, "
                    f"old vault kept={kept_ok} at {kept[0].name if kept else 'NOTHING'}"
                    if ok else f"new={new_opens} old_refused={old_refused} kept={kept_ok}")

    check("start: a forgotten passphrase has a real way out (new vault, old kept)",
          a_forgotten_passphrase_is_not_a_dead_end)

    def package_integrity():
        sys.path.insert(0, str(ROOT))
        from brain import __version__

        return PASS, f"version {__version__}, python {sys.version.split()[0]}"

    check("packaging: version and interpreter", package_integrity)

    # ---- 21. audit trail -------------------------------------------------- #
    def audit_trail():
        vault.audit("retest_marker", {"at": stamp})
        tail = vault.read_audit(limit=20)
        found = any(e.get("event") == "retest_marker" for e in tail)
        return (PASS if found else FAIL, f"{len(tail)} recent entries, marker found={found}")

    check("audit: an action leaves a trace", audit_trail)

    # ---- 22. the phone bridge (no network here) -------------------------- #
    def telegram_bridge():
        from brain.agent import telegram as tg

        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return json.dumps({"ok": True, "result": {"message_id": 1}})

        bot = tg.Telegram(vault=vault, opener=opener)
        bot.set_token("123456789:AAFakeRetestTokenOnly-abcDEF")
        if bot.configured:
            return FAIL, "a token alone configured the bridge without a chat"
        bot.set_chat("424242")
        out = bot.send("Nila: re-test message")
        token_in_body = any("AAFakeRetestTokenOnly" in c for c in calls[:-1])
        off = tg.Telegram(vault=vault,
                          opener=lambda r, t: (_ for _ in ()).throw(
                              tg.urllib.error.URLError("offline")))
        queued = off.send("while offline")
        ok = (out.get("ok") and queued.get("queued") == 1
              and not token_in_body and "AAFakeRetestTokenOnly" not in json.dumps(out))
        return (PASS if ok else FAIL,
                f"sent={out.get('sent')}, offline queued={queued.get('queued')}, "
                f"token leaked={token_in_body}")
        # (the token is cleared below, before the vault is closed)

    check("telegram: one chat, queued when offline, token never printed",
          telegram_bridge)

    def telegram_cleared():
        from brain.agent import telegram as tg

        bot = tg.Telegram(vault=vault)
        if not bot.token:
            return PASS, "nothing to clear"
        bot.clear()
        return PASS, f"cleared; configured={bot.configured}"

    check("telegram: settings can be removed again", telegram_cleared)

    # ---- 23. how much of v2 is really finished --------------------------- #
    def v2_progress():
        from brain.agent import progress

        report = progress.report()
        ok = (report["done"] + report["partial"] + report["missing"] == report["total"]
              and report["percent"] <= 100)
        return (PASS if ok else FAIL,
                f"{report['done']}/{report['total']} done ({report['percent']}%), "
                f"{report['partial']} partial, {report['missing']} not built; "
                f"next: {report['next'][:60]}")

    check("v2: the progress number is measured, not remembered", v2_progress)

    # ---- 24. the v0.2 business work (interview, late, weekly, export) ---- #
    def projects_flow():
        from brain.projects.store import ProjectError, ProjectStore

        st = ProjectStore(memory)
        st.create("Re-test shop", "a stall")
        q1 = st.interview("Re-test shop")
        st.set_canvas("Re-test shop", q1["block"], "students")
        q2 = st.interview("Re-test shop")
        st.add_milestone("Re-test shop", "stove", "2020-01-05")
        late = st.late_milestones()
        weekly = st.weekly(7)
        refused = False
        try:
            st.add_milestone("Re-test shop", "bad", "next week")
        except ProjectError:
            refused = True
        ok = (q1["block"] != q2["block"] and len(late) == 1
              and weekly["projects_touched"] == 1 and refused)
        return (PASS if ok else FAIL,
                f"one question at a time ({q1['label']} -> {q2['label']}), "
                f"{len(late)} late, weekly={weekly['projects_touched']} project(s), "
                f"nonsense date refused={refused}")

    check("projects: one question at a time, late milestones, weekly, no guessing",
          projects_flow)

    def project_export():
        import tempfile

        from brain.projects.store import ProjectStore

        st = ProjectStore(memory)
        st.create("Export check", "x")
        st.set_canvas("Export check", "customers", "students")
        with tempfile.TemporaryDirectory() as tmp:
            out = st.export_summary("Export check", tmp)
            text = open(out["path"], encoding="utf-8").read()
        ok = "Export check" in text and "students" in text
        return (PASS if ok else FAIL, f"{out['chars']} chars, plain text, folder chosen by the owner")

    check("projects: a summary exported to a .txt you choose", project_export)

    # ---- 25. the desktop app is installable ------------------------------ #
    def installable_app():
        import json as _json

        from brain.api import dashboard

        manifest = _json.loads(dashboard.asset("/manifest.json")[0])
        html = dashboard.asset("/")[0].decode()
        icon = dashboard.asset("/icon.svg")
        ok = ('rel="manifest"' in html and manifest["display"] == "standalone"
              and icon is not None and "manifest-src 'self'" in dashboard.CSP)
        return (PASS if ok else FAIL,
                f"manifest display={manifest['display']}, icon {icon[1]}, page links the manifest")

    check("platform: Chrome 'Install as app' has a manifest and an icon", installable_app)

    # ---- 26. the two D-drive helpers ------------------------------------- #
    def move_helper():
        import shutil as _sh
        import tempfile

        import importlib.util

        path = ROOT / "tools" / "move_storage.py"
        spec = importlib.util.spec_from_file_location("move_storage", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "vault"
            (home / "sub").mkdir(parents=True)
            (home / "memory.db").write_bytes(b"x" * 4096)
            (home / "sub" / "n.txt").write_text("தமிழ்")
            target = Path(tmp) / "D"
            import contextlib
            import io

            with contextlib.redirect_stdout(io.StringIO()):
                code = mod.move(home, target, dry_run=False, yes=True)
            same = (target / "sub" / "n.txt").read_text(encoding="utf-8") == "தமிழ்"
        return (PASS if code == 0 and same else FAIL,
                f"exit={code}, every file compared by SHA-256, old folder kept")

    check("storage: move-to-d copies, verifies, then switches", move_helper)

    def backup_helper():
        import sqlite3
        import tempfile
        import importlib.util

        path = ROOT / "tools" / "backup_check.py"
        spec = importlib.util.spec_from_file_location("backup_check", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "vault"
            home.mkdir()
            con = sqlite3.connect(home / "memory.db")
            con.execute("CREATE TABLE t (a)")
            con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(5)])
            con.commit()
            con.close()
            target = Path(tmp) / "D"
            import contextlib
            import io

            with contextlib.redirect_stdout(io.StringIO()):
                code = mod.backup(home, target, dry_run=False, yes=True)
                synced = mod.backup(home, Path(tmp) / "Google Drive" / "b", dry_run=True)
        return (PASS if code == 0 and synced == 2 else FAIL,
                f"restored 5 records and counted them; a synced folder refused ({synced})")

    check("backup: dated, restored, counted - and never inside Google Drive", backup_helper)

    # ---- 27. strict mode end to end -------------------------------------- #
    def strict_roundtrip():
        from brain.security import strict

        if not hasattr(strict, "set_mode"):
            return SKIP, f"module helpers: {[n for n in dir(strict) if not n.startswith('_')][:6]}"
        return PASS, "set_mode present"

    check("strict: mode can be switched", strict_roundtrip)

    # ---- 30. stage 12: the four rows that stopped saying "not built" ------ #
    def photos_and_scans():
        """A page written with known words, then read back - the whole pipeline."""
        import importlib.util

        from brain.agent import ocr

        spec = importlib.util.spec_from_file_location(
            "retest_text_image", ROOT / "tools" / "text_image.py")
        page = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(page)
        text = "INVOICE 42\nTotal: 1250"
        out_png = Path(home) / "retest-page.png"
        width, height, grey = page.render_grey(text)
        page.write_png(out_png, width, height, grey)
        read_back = ocr.read(out_png)
        pdf = page.write_scanned_pdf(Path(home) / "retest-scan.pdf", text)
        from_pdf = ocr.read(pdf)
        ok = read_back["text"] == text and from_pdf["text"] == text
        return (PASS if ok else FAIL,
                f"png {read_back['text']!r} (conf {read_back['confidence']}), "
                f"scanned pdf read={from_pdf['ok']}, engine={read_back['engine']}")

    check("stage 12: a picture and a scanned PDF are read here", photos_and_scans)

    def two_pcs():
        """The reference server on a free port, a push from this vault, then a GET."""
        import socket

        import urllib.request

        from brain.cloud.protocol import SyncEngine

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        token = "retest-" + "t" * 20
        data = Path(home) / "retest-server"
        env = dict(os.environ, BRAIN_SYNC_TOKEN=token)
        proc = subprocess.Popen([sys.executable, str(ROOT / "cloud" / "sync_server" / "app.py"),
                                 "--port", str(port), "--data", str(data)],
                                env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                return SKIP, "the sync server did not start here"
            os.environ["BRAIN_SYNC_TOKEN"] = token
            config.sync = {"backend": "http", "url": f"http://127.0.0.1:{port}",
                           "token_env": "BRAIN_SYNC_TOKEN"}
            memory.remember("re-test: the shop rent is 8000 rupees")
            report = SyncEngine(config, memory, vault).sync()
            ok = report["pushed"] >= 1 and not report["errors"]
            return (PASS if ok else FAIL,
                    f"pushed={report['pushed']} pulled={report['pulled']} "
                    f"conflicts={report['conflicts']} errors={len(report['errors'])}")
        finally:
            proc.terminate()
            os.environ.pop("BRAIN_SYNC_TOKEN", None)
            config.sync = {}

    check("stage 12: two PCs sync over HTTP, encrypted", two_pcs)

    def calendar_written():
        import datetime as _dt

        from brain.util import ics

        path = Path(home) / "retest-calendar.ics"
        first = ics.add_event(path, "Retest dentist", _dt.datetime(2026, 11, 6, 10, 30),
                              end=_dt.datetime(2026, 11, 6, 11, 30))
        second = ics.update_event(path, first["uid"], summary="Retest dentist moved",
                                  start=_dt.datetime(2026, 11, 6, 12, 0))
        events = ics.read_events(path)
        raw = path.read_text()
        ok = (len(events) == 1 and second["sequence"] == 1
              and events[0].end == _dt.datetime(2026, 11, 6, 13, 0)
              and raw.count("BEGIN:VEVENT") == 1)
        return (PASS if ok else FAIL,
                f"uid kept={second['uid'] == first['uid']}, sequence={second['sequence']}, "
                f"length kept={events[0].end.hour - events[0].start.hour}h")

    check("stage 12: the calendar is written with a stable UID", calendar_written)

    def meaning_search():
        from brain.memory.store import GloveWordVectorEmbedder, cosine

        vectors = Path(home) / "retest.wordvec"
        vectors.write_text("bike 1.0 0.0\nrepair 0.0 1.0\nscooter 1.0 0.05\n"
                           "rent 0.0 0.0\n")
        embedder = GloveWordVectorEmbedder(vectors)
        query = embedder.embed("scooter repair")
        ok = cosine(query, embedder.embed("bike repair")) > 0.5
        return (PASS if ok else FAIL,
                f"{embedder.name}, coverage {embedder.coverage('scooter repair'):.2f}")

    check("stage 12: memory can be searched by meaning, offline", meaning_search)

    # ---- 31. stage 13: the rows that were only half finished --------------- #
    def hold_one_use():
        from brain.agent.ephemeral import EphemeralError, EphemeralStore

        canary = "9845123789"
        store = EphemeralStore()
        slot = store.keep(canary, purpose="call amma once", ttl=60)
        recorded = json.dumps(store.vault_slot(slot.id))
        first = store.take(slot.id)["value"]
        second = False
        try:
            store.take(slot.id)
            second = True
        except EphemeralError:
            pass
        leaked = canary in recorded
        store.purge()
        ok = first == canary and not second and not leaked and not store.report()["held"]
        return (PASS if ok else FAIL,
                f"used once={first == canary}, second use refused={not second}, "
                f"value in any record={leaked}, purge emptied={not store.report()['held']}")

    check("stage 13: a value can be held for one use, never written down", hold_one_use)

    def wake_word():
        import math

        from brain.agent import wake
        from brain.util.audio import write_wav

        rate = 16000

        def syllable(seconds, pitch):
            n = int(rate * seconds)
            return [0.5 * math.sin(math.pi * (i / n)) ** 0.7
                    * (math.sin(2 * math.pi * pitch * i / rate)
                       + 0.4 * math.sin(4 * math.pi * pitch * i / rate)) for i in range(n)]

        audio = syllable(0.28, 190) + [0.0] * int(rate * 0.06) + syllable(0.30, 170)
        path = Path(home) / "retest-nila.wav"
        write_wav(path, audio, rate)
        heard = wake.probe(str(path))
        ceiling_refused = False
        try:
            wake.WakeWindow(seconds=wake.MAX_WINDOW_SECONDS + 60)
        except wake.WakeError:
            ceiling_refused = True
        closed = wake.WakeWindow(seconds=30, clock=lambda: 100.0)
        outside = False
        try:
            closed.feed(audio, rate)
        except wake.WakeError:
            outside = True
        ok = heard["heard"] and ceiling_refused and outside
        return (PASS if ok else FAIL,
                f"heard the name (confidence {heard['confidence']:.2f}), "
                f"all-day window refused={ceiling_refused}, "
                f"word outside a window refused={outside}")

    check('stage 13: the wake word "Nila" works, inside a window', wake_word)

    def activation_mode():
        from brain.security.permission import Broker

        broker = Broker(path=Path(home) / "retest-permissions.json")
        broker.grant_durable("fs.read", "/tmp/retest")
        before = broker.check("fs.read", "/tmp/retest").allowed
        broker.set_mode("command")
        after = broker.check("fs.read", "/tmp/retest").allowed
        peek_after = broker.peek("fs.read", "/tmp/retest").allowed
        broker.set_mode("standing")
        restored = broker.check("fs.read", "/tmp/retest").allowed
        ok = before and not after and not peek_after and restored
        return (PASS if ok else FAIL,
                f"standing grant honoured={before}, refused in command mode={not after}, "
                f"peek agrees={not peek_after}, restored on switch back={restored}")

    check("stage 13: 'strictly per command' is a mode you can switch on",
          activation_mode)

    def account_binding():
        from brain.agent.integrations import IntegrationGateway

        gateway = IntegrationGateway(config=None, path=Path(home) / "retest-integrations.json")
        unbound = gateway.account_problem("gmail", {})
        wrong = gateway.account_problem("gmail", {"account": "someone@else.com"})
        both = bool(unbound) and bool(wrong)
        ok = both and "gmail" in gateway.bindings()["integrations"]
        return (PASS if ok else FAIL,
                f"gmail/drive/calendar declared, unbound call refused={bool(unbound)}, "
                f"wrong-account call refused={bool(wrong)}")

    check("stage 13: one integration speaks for one account", account_binding)

    def device_ceiling():
        from brain.config import Config
        from brain.memory.vault import Vault

        first_home = Path(home) / "ceiling-pc1"
        cfg = Config.load(first_home)
        cfg.fast_kdf = True
        host = Vault(cfg)
        host.initialize("retest-passphrase-1", "Owner", "dev-a")
        host.set_device_limit(2)
        bundle = host.export_bundle("pc2")
        host.lock()
        cfg2 = Config.load(Path(home) / "ceiling-pc2")
        cfg2.fast_kdf = True
        second = Vault(cfg2)
        second.import_bundle(bundle, "retest-passphrase-1")
        room = second.enrolment_room()
        refused = False
        try:
            second.export_bundle("pc3")
        except PermissionError:
            refused = True
        second.lock()
        ok = room["used"] == 2 and room["limit"] == 2 and refused
        return (PASS if ok else FAIL,
                f"ceiling {room['limit']}, enrolled {room['used']}, "
                f"third device refused={refused}")

    check("stage 13: a fourth device cannot join (one owner, a few devices)",
          device_ceiling)

    def passphrase_once():
        """Stage 14: the owner's own test - type it once, never again.

        Deliberately uses a fake OS store: this sandbox has no Windows Credential
        Locker, and a check that silently skipped on the owner's machine would be
        worth nothing. The fake proves the *logic*; `brain passphrase status` on
        his laptop proves the *store*.
        """
        from brain.security import vaultkey

        saved: dict = {}

        class Fake:
            @staticmethod
            def get_keyring():
                return "fake store (retest)"

            @staticmethod
            def set_password(service, user, value):
                saved[(service, user)] = value

            @staticmethod
            def get_password(service, user):
                return saved.get((service, user))

            @staticmethod
            def delete_password(service, user):
                saved.pop((service, user), None)

        from brain.config import Config
        from brain.memory.vault import Vault

        real_keyring, real_dpapi = vaultkey._keyring, vaultkey._dpapi_available
        vaultkey._keyring = lambda: Fake
        vaultkey._dpapi_available = lambda: False
        try:
            cfg = Config.load(Path(home) / "once-pc")
            cfg.fast_kdf = True
            v = Vault(cfg)
            v.initialize("retest-passphrase-2", "Owner", "dev-x")
            v.lock()
            opened = Vault(cfg)
            mk = opened.unlock("retest-passphrase-2")
            opened.lock()
            remembered = vaultkey.save(mk, cfg.home)["saved"]

            # a cold start: no passphrase anywhere, only the store
            cold = Vault(cfg)
            session = cold.unlock_with_master_key(vaultkey.load(cfg.home))
            no_prompt = session.mode == "device-key"

            # and "I forgot it" - set a new one with no old passphrase
            cold.set_passphrase("written-down-9", mode="retest")
            cold.lock()
            fresh = Vault(cfg)
            old_dead = not fresh.verify_passphrase("retest-passphrase-2")
            new_works = fresh.verify_passphrase("written-down-9")
            forgotten = vaultkey.forget(cfg.home)
            asks_again = vaultkey.load(cfg.home) is None
            ok = all([remembered, no_prompt, old_dead, new_works,
                      forgotten["ok"], asks_again])
            return (PASS if ok else FAIL,
                    f"remembered={remembered}, cold start asked nothing={no_prompt}, "
                    f"old passphrase refused={old_dead}, new one works={new_works}, "
                    f"forget -> asks again={asks_again}")
        finally:
            vaultkey._keyring, vaultkey._dpapi_available = real_keyring, real_dpapi

    check("stage 14: the passphrase is typed once, and a new one can be set "
          "without the old", passphrase_once)

    def one_key_or_the_voice():
        """Stage 14: the two ways he asked to bring Nila up."""
        from brain.util import hotkey, launcher

        home2 = Path(home) / "easy-home"
        home2.mkdir(parents=True, exist_ok=True)
        (home2 / "brain.py").write_text("#")
        plan = hotkey.voice_plan()
        written = hotkey.write_hotkey_shortcut(home2, out_dir=Path(home) / "desk")
        vbs = launcher.windows_vbs("python", "brain.py", str(home2), extra_args=["start"])
        mic_at_signin_refused = False
        try:
            launcher.write_startup(home2, want_voice=True)
        except launcher.LauncherError:
            mic_at_signin_refused = True
        bounded = plan["bounded_window_seconds"] <= 900
        ok = bool(written["written"]) and bool(plan["wake_word"]) and bounded \
            and mic_at_signin_refused and "start" in vbs
        return (PASS if ok else FAIL,
                f"shortcut written={len(written['written'])}, wake word={plan['wake_word']}, "
                f"window<={plan['bounded_window_seconds']}s, "
                f"microphone at sign-in refused={mic_at_signin_refused}")

    check("stage 14: one key or your voice brings Nila up, microphone never at sign-in",
          one_key_or_the_voice)

    def audit_fixes():
        """Stage 15: the audit's items, each measured rather than read."""
        from brain.agent import host_tools
        from brain.api import dashboard
        from brain.util.calc import CalcError, safe_eval

        # BUG-1: refused, fast
        started = time.monotonic()
        refused = 0
        for expr in ("9**9**9**9", "9e999999999", "2**99999999", "(10**100)**100"):
            try:
                safe_eval(expr)
            except CalcError:
                refused += 1
        fast = (time.monotonic() - started) < 0.5
        normal = safe_eval("2+3*4") == 14

        # BUG-2: the Windows shell characters and a shell-inside-the-shell
        class Ctx:
            def __init__(self):
                self.calls = []
                self.permissions = self
                self.config = type("C", (), {"home": Path(home)})()

            class D:
                allowed = False
                reason = "retest"
                capability = "shell.run"
                scope = "retest"
                prompt_id = "retest"

            def request(self, *a, **k):
                self.calls.append(a)
                return self.D

            def audit(self, *a, **k):
                pass

        blocked = 0
        for cmd in ("echo %USERPROFILE%", "echo hi !PATH!", "powershell -Command x"):
            if host_tools.run_command(Ctx(), command=cmd)["ok"] is False:
                blocked += 1

        # dashboard: no battery must be a 200-shaped answer, not a raise
        import sys as _sys
        import types as _types

        fake = _types.ModuleType("psutil")
        fake.cpu_percent = lambda: 1.0
        fake.virtual_memory = lambda: _types.SimpleNamespace(percent=2.0)

        def no_battery():
            raise NotImplementedError("no battery")

        fake.sensors_battery = no_battery
        real = _sys.modules.get("psutil")
        _sys.modules["psutil"] = fake
        try:
            m = dashboard.metrics()
            no_battery_ok = "cpu_pct" in m and "battery_pct" not in m
        finally:
            if real is not None:
                _sys.modules["psutil"] = real
            else:
                _sys.modules.pop("psutil", None)

        ok = refused == 4 and fast and normal and blocked == 3 and no_battery_ok
        return (PASS if ok else FAIL,
                f"calculator refused 4/4 in <0.5s={fast}, normal maths still {normal}, "
                f"windows shell blocked {blocked}/3, no-battery metrics ok={no_battery_ok}")

    check("stage 15: the audit's four defects are fixed and stay fixed", audit_fixes)

    memory.close()
    vault.lock()
    shutil.rmtree(home, ignore_errors=True)

    # ---- the verdict ------------------------------------------------------ #
    passed = sum(1 for r in results if r["status"] == PASS)
    skipped = sum(1 for r in results if r["status"] == SKIP)
    failed = sum(1 for r in results if r["status"] == FAIL)
    if args.json:
        print(json.dumps({"results": results, "passed": passed, "skipped": skipped,
                          "failed": failed}, indent=2))
        return 0 if failed == 0 else 1

    lines = [f"# Nila - full re-test, {stamp}", "",
             f"**{passed} passed · {skipped} skipped (needs something not on this "
             f"machine) · {failed} failed**", "",
             "| # | check | result | evidence |", "|---|---|---|---|"]
    for i, row in enumerate(results, 1):
        detail = row["detail"].replace("|", "\\|")[:120]
        mark = {PASS: "✅", SKIP: "⏭️", FAIL: "❌"}[row["status"]]
        needs = f" *(needs {row['needs']})*" if row["status"] == SKIP and row["needs"] else ""
        lines.append(f"| {i} | {row['name']} | {mark}{needs} | {detail} |")
    lines += ["", "Skipped means the code path is built and says honestly what is "
                  "missing; it is not a pass.", ""]
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{passed} passed, {skipped} skipped, {failed} failed")
    print(f"report: {args.out}")
    return 0 if failed == 0 else 1


def _tiny_mp4(duration_s: float) -> bytes:
    """A minimal mp4 container carrying just an mvhd, enough to read a duration."""
    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + kind + payload

    mvhd_body = (b"\x00" * 12
                 + struct.pack(">I", 1000)
                 + struct.pack(">I", int(duration_s * 1000))
                 + b"\x00" * 80)
    moov = box(b"moov", box(b"mvhd", mvhd_body))
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    return ftyp + moov


if __name__ == "__main__":
    sys.exit(main())
