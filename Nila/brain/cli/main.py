"""``brain`` command line interface.

Text-first interaction (a stated requirement) with a REPL, plus explicit
sub-commands for every lifecycle operation: init, enrol, unlock, memory,
sync, updates, devices, diagnostics.

Nothing sensitive is ever taken as a command-line argument where it can be
avoided - passphrases come from a hidden prompt, ``--passphrase-env VAR``, or
stdin, because argv is world-readable via /proc on multi-user systems.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Config
from ..memory.store import MemoryStore
from ..memory.vault import Vault
from ..util.misc import dumps


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class UI:
    def __init__(self, quiet: bool = False, json_out: bool = False):
        self.quiet = quiet
        self.json = json_out

    def say(self, msg: str = "") -> None:
        if not self.quiet and not self.json:
            print(msg)

    def warn(self, msg: str) -> None:
        if not self.json:
            print(f"\033[33m! {msg}\033[0m", file=sys.stderr)

    def err(self, msg: str) -> int:
        if self.json:
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(f"\033[31merror:\033[0m {msg}", file=sys.stderr)
        return 1

    def ok(self, msg: str) -> None:
        self.say(f"\033[32m{msg}\033[0m")

    def emit(self, payload: Dict[str, Any]) -> None:
        if self.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            for k, v in payload.items():
                if isinstance(v, (dict, list)):
                    v = dumps(v)
                print(f"  {k:<22s} {v}")


def read_passphrase(args, prompt: str = "master passphrase: ", confirm: bool = False) -> str:
    env = getattr(args, "passphrase_env", None)
    if env:
        val = os.environ.get(env, "")
        if not val:
            raise SystemExit(f"environment variable {env} is empty/unset")
        return val
    if getattr(args, "passphrase_stdin", False):
        return sys.stdin.readline().rstrip("\n")
    if getattr(args, "passphrase", None):
        return args.passphrase
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass(prompt)
    if confirm:
        again = getpass.getpass("repeat it:        ")
        if pw != again:
            raise SystemExit("passphrases did not match")
    return pw


def read_totp(args, prompt: str = "authenticator code (6 digits): ") -> str:
    val = getattr(args, "totp", None)
    if val:
        return str(val).strip()
    if not sys.stdin.isatty():
        # End of input is not a code. Reading an empty line here sent "" to the
        # verifier, which answered "invalid or already-used code" and spent a
        # failed attempt - so a scripted run (or a closed stdin) locked the owner
        # out with a message that blamed their authenticator. (Found in the
        # end-to-end run: `--passphrase-env` with no --totp and stdin closed.)
        line = sys.stdin.readline()
        if not line.strip():
            raise SystemExit("no authenticator code available: pass --totp <code> "
                             "(or run interactively) - the passphrase alone is not "
                             "enough once an authenticator is enrolled")
        return line.strip()
    return input(prompt).strip()


def open_vault(args, require_unlocked: bool = True, mode: str = "passphrase",
               allow_setup: bool = False) -> tuple:
    """Open the vault. FAIL CLOSED on the second factor.

    * TOTP enrolled      -> the authenticator code is required, every time.
    * voice only         -> use ``brain unlock --voice`` (the passphrase alone is refused).
    * nothing enrolled   -> refused, except for the setup commands
                            (``allow_setup``: ``brain totp`` / ``brain enroll-voice``)
                            which exist precisely to enrol a factor.
    ``identity.allow_degraded`` remains the single explicit, audited override.
    """
    config = Config.load(getattr(args, "home", None))
    if getattr(args, "fast_kdf", False):
        config.fast_kdf = True
    vault = Vault(config)
    if not vault.initialized:
        raise SystemExit("no vault here - run `brain init` first "
                         f"(BRAIN_HOME={config.home})")
    if require_unlocked and not vault.is_unlocked():
        locked, wait = vault.locked_out()
        if locked:
            raise SystemExit(f"locked out after failed attempts; retry in {wait}s")
        passphrase = read_passphrase(args)
        try:
            mk = vault.unlock(passphrase)
        except PermissionError as exc:
            vault.register_failure("bad-passphrase")
            raise SystemExit(str(exc)) from exc
        vault.begin_provisional(mk)
        policy = vault.policy()
        secret = vault.load_totp_secret()
        has_voice = vault.has_voice()
        override = bool(policy.get("allow_degraded"))
        if secret and getattr(args, "recovery", None):
            # break-glass: a one-time recovery code instead of the authenticator
            if not vault.consume_recovery_code(str(args.recovery).strip()):
                w = vault.register_failure("bad-recovery-code")
                vault.discard_provisional()
                raise SystemExit("invalid recovery code" + (f" (locked out {w}s)" if w else ""))
            vault.meta_set("re_enrollment_required", True)
            session_mode = "recovery"
            degraded = True
        elif secret:
            code = read_totp(args)
            # ONE-TIME at the CLI too. `totp_verify` alone checks the maths, so
            # the same 6 digits unlocked command after command inside their
            # 30-second window; the API ceremony was fixed for this (audit B6)
            # but the main human route was not. Spend the step, then verify.
            from ..identity.totp import counter_for

            counter = counter_for(secret, code, min_counter=vault.totp_last_counter())
            if counter < 0:
                w = vault.register_failure("bad-totp")
                vault.discard_provisional()
                raise SystemExit("invalid or already-used authenticator code"
                                 + (f" (locked out {w}s)" if w else ""))
            vault.mark_totp_used(counter)
            session_mode = "totp"
            # "degraded" means a STRONGER factor exists and was not used - not
            # that none exists (that was a dead end for owners without a voiceprint).
            degraded = has_voice
        elif has_voice and not (override or allow_setup):
            vault.discard_provisional()
            raise SystemExit("second factor required: run `brain unlock --voice <wav>`, "
                             "or enrol an authenticator with `brain totp`")
        elif not (override or allow_setup):
            vault.discard_provisional()
            raise SystemExit("no second factor is enrolled, so the passphrase alone is "
                             "refused. Run `brain totp` once to enrol an authenticator app.")
        else:
            session_mode = mode if override else "setup"
            degraded = has_voice
        vault.register_success()
        vault.start_session(mk, mode=session_mode, degraded=degraded)
        vault.audit("session_unlock", {"mode": session_mode, "degraded": degraded,
                                       "via": "cli"})
    return config, vault


def open_memory(config: Config, vault: Vault) -> MemoryStore:
    """Open the store and pin it to this session's access level.

    The level decides two things: which vector class keys are loaded (so sealed
    vectors are cryptographically out of reach) and the classification ceiling
    applied to lexical hits. A degraded session - a stronger enrolled factor
    skipped during unlock - therefore gets genuinely narrower recall, not merely
    the same recall with a warning.
    """
    from ..security.classify import session_tier
    return MemoryStore(config, vault.session.mk,
                       access_level=session_tier(vault.session),
                       session=vault.session)


def warn_if_single_factor(ui: "UI", vault) -> None:
    """Warn when a full-tier session rests on ONE factor.

    On a vault with no voiceprint and no TOTP enrolled, the passphrase is the
    only factor that exists, so unlocking with it yields a full-tier session able
    to recall sensitive and restricted memories. Blocking that would make a fresh
    vault unusable (and treating it as degraded made an owner's own health notes
    unrecallable, which is why that was reverted). So it warns, every session.
    """
    if vault.session is None or vault.session.degraded:
        return
    try:
        has_voice = vault.has_voice()
        has_totp = bool(vault._identity().get("totp"))
    except Exception:
        return
    if has_voice or has_totp:
        return
    ui.warn("single-factor session: no voiceprint and no TOTP are enrolled, so "
            "your passphrase alone gave FULL access - including sensitive and "
            "restricted memories. Run `brain enroll-voice` and `brain totp` to "
            "require a second factor.")


def report_withheld(ui: "UI", store: MemoryStore) -> None:
    """Tell the user when matches were held back by the session tier.

    Without this, a tier filter turns into a silent "nothing matched", which is
    the worst possible failure mode: the user concludes the memory is gone
    rather than gated. Counts and classes only - never titles or bodies.
    """
    withheld = getattr(store, "last_withheld", None) or []
    if not withheld:
        return
    classes: dict = {}
    for w in withheld:
        classes[w.get("class", "?")] = classes.get(w.get("class", "?"), 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(classes.items()))
    ui.warn(f"{len(withheld)} match(es) withheld at session tier "
            f"'{store.level}' ({summary}). They exist but are not permitted in "
            f"this session - unlock with your voice factor to reach them.")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_init(args, ui: UI) -> int:
    config = Config.load(args.home)
    if args.fast_kdf:
        config.fast_kdf = True
    vault = Vault(config)
    if vault.initialized:
        return ui.err(f"a vault already exists at {config.home} "
                      "(delete BRAIN_HOME or use `brain device import`)")
    passphrase = read_passphrase(args, "choose a master passphrase: ", confirm=True)
    info = vault.initialize(passphrase, user=args.user, device_label=args.label)
    config.agent.setdefault("device_id", info["device_id"])
    config.save()
    ui.ok(f"vault created at {config.home}")
    ui.emit({"user": info["user"], "device_id": info["device_id"],
             "kdf": f"scrypt n={config.kdf_n} r=8 p=1",
             "crypto_backend": __import__("brain.crypto", fromlist=["x"]).backend(),
             "next": "brain totp  (links your authenticator app - required before the vault will open)"})
    if not args.no_recovery:
        from ..identity.ceremony import IdentityCeremony

        codes = IdentityCeremony(config, vault).enroll_recovery_codes(8)
        ui.warn("recovery codes - write these down NOW, they are shown once:")
        for i in range(0, len(codes), 2):
            ui.say("   " + "   ".join(codes[i:i + 2]))
    vault.audit("cli_init", {"user": args.user})
    vault.lock()
    return 0


def cmd_status(args, ui: UI) -> int:
    config = Config.load(args.home)
    vault = Vault(config)
    from ..crypto.envelope import backend as crypto_backend
    from ..memory.store import make_embedder

    payload: Dict[str, Any] = {
        "home": str(config.home),
        "initialized": vault.initialized,
        "crypto_backend": crypto_backend(),
        "version": __import__("brain").__version__,
    }
    if vault.initialized:
        payload.update({
            "user": vault.user, "device_id": vault.device_id,
            "devices": {k: v.get("label") for k, v in vault.devices().items()},
            "kdf": vault.data.get("kdf", {}).get("n"),
            "unlocked": vault.is_unlocked(),
            "voice_enrolled": bool((vault.data.get("voice") or {}).get("template")),
            "sync": dict(config.sync or {}),
            "updates": {k: v for k, v in (config.updates or {}).items() if k != "voice_model_override"},
            "agent": dict(config.agent or {}),
            "memory_db": str(config.memory_path),
            "audit_entries": len(vault.read_audit(10000)),
        })
        if args.unlock:
            passphrase = read_passphrase(args)
            try:
                vault.start_session(vault.unlock(passphrase), mode="passphrase", degraded=True)
            except PermissionError as exc:
                return ui.err(str(exc))
            memory = open_memory(config, vault)
            payload["voice"] = vault.voice_info()
            payload["memory"] = memory.stats()
            payload["embedder"] = memory.embedder.name
            memory.close()
            vault.lock()
    ui.emit(payload)
    return 0


def cmd_unlock(args, ui: UI) -> int:
    from ..identity.ceremony import CeremonyError, IdentityCeremony

    config = Config.load(args.home)
    vault = Vault(config)
    if not vault.initialized:
        return ui.err("no vault - run `brain init`")
    ceremony = IdentityCeremony(config, vault)
    passphrase = read_passphrase(args)

    submission = None
    if args.voice:
        challenge = ceremony.issue_challenge()
        ui.say(f'\033[36mspeak this challenge:\033[0m  "{challenge["text"]}"')
        if args.record:
            from ..util.audio import AudioError, record_microphone
            from ..util.misc import write_private as _wp

            try:
                audio = record_microphone(seconds=challenge_seconds(config))
            except AudioError as exc:
                return ui.err(str(exc))
            tmp = config.tmp_dir / f"challenge-{challenge['nonce'][:8]}.wav"
            from ..util.audio import write_wav

            write_wav(tmp, audio.samples, audio.sample_rate)
            submission = {"nonce": challenge["nonce"], "audio": str(tmp),
                          "bound": challenge["bound"]}
            _cleanup(tmp)
        else:
            submission = {"nonce": args.nonce or challenge["nonce"],
                          "audio": args.voice, "bound": args.bound or challenge["bound"]}
            if args.nonce and args.nonce != challenge["nonce"]:
                ui.warn("you passed --nonce that differs from the freshly issued challenge; "
                        "using yours (it must have been issued by `/challenge` earlier)")

    mode = "voice" if submission else ("totp" if args.totp else
                                       ("recovery" if args.recovery else "auto"))
    result = ceremony.unlock(passphrase, mode=mode, voice_submission=submission,
                             totp_code=args.totp, recovery_code=args.recovery)
    if not result.ok:
        return ui.err("; ".join(result.reasons) or "unlock failed")

    ui.ok(f"unlocked ({result.mode}"
          f"{', DEGRADED' if result.session.degraded else ''})")

    if getattr(args, "step_up", False):
        # `Session.step_up_ok` gates high-risk tools such as export_backup and is
        # read by the classifier. It used to have no setter at all, so the gate
        # could never open; the flag is consumed by the next high-risk tool call.
        session = getattr(vault, "session", None)
        if session is None:
            return ui.err("unlock succeeded but no session is available for step-up")
        session.step_up_ok = True
        from ..util.misc import now_ms as _now_ms

        session.step_up_at = _now_ms()
        vault.audit("step_up_granted", {"session": session.session_id,
                                        "via": "cli --step-up"})
        ui.ok("step-up granted: the next high-risk action is authorised")
    if args.json:
        print(json.dumps({"ok": True, **result.detail}, indent=2, default=str))
    if args.chat:
        from ..agent.agent import Brain
        from ..agent.repl import run_repl

        memory = open_memory(config, vault)
        brain = Brain(config, vault, memory)
        try:
            return run_repl(brain, verbose=args.verbose, banner=False)
        finally:
            memory.close()
    vault.lock()
    ui.say("session opened and closed (pass --chat to stay interactive)")
    return 0


def challenge_seconds(config: Config) -> float:
    return float(config.voice_config().get("challenge_seconds", 4.0))


def _cleanup(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def cmd_lock(args, ui: UI) -> int:
    config, vault = open_vault(args, require_unlocked=False)
    vault.lock()
    ui.ok("locked; in-memory key zeroised.")
    return 0


def cmd_enroll_voice(args, ui: UI) -> int:
    from ..identity.ceremony import CeremonyError, IdentityCeremony

    config, vault = open_vault(args, allow_setup=True)
    ceremony = IdentityCeremony(config, vault)
    samples: List[Any] = []
    n = args.samples
    if args.file:
        samples = list(args.file)
        n = len(samples)
    ui.say(f"enrolling {n} voice sample(s). Speak naturally, same mic you will use later.")
    while len(samples) < n:
        idx = len(samples) + 1
        if args.record:
            from ..util.audio import AudioError, record_microphone, write_wav

            ui.say(f"  sample {idx}/{n}: recording {args.seconds:.1f}s - speak now…")
            try:
                audio = record_microphone(seconds=args.seconds)
            except AudioError as exc:
                vault.lock()
                return ui.err(str(exc))
            tmp = config.tmp_dir / f"enroll-{idx}.wav"
            write_wav(tmp, audio.samples, audio.sample_rate)
            samples.append(str(tmp))
        else:
            path = input(f"  sample {idx}/{n} - path to a WAV file: ").strip()
            if not path:
                break
            samples.append(path)
    if len(samples) < max(1, min(n, 3)):
        vault.lock()
        return ui.err("need at least 3 samples (or pass --file a.wav b.wav c.wav)")
    try:
        report = ceremony.enroll_voice(samples[:n])
    except CeremonyError as exc:
        vault.lock()
        return ui.err(str(exc))
    for s in samples:
        if str(s).startswith(str(config.tmp_dir)):
            _cleanup(Path(s))
    ui.ok("voice template stored (encrypted with your master key)")
    ui.emit(report)
    if report.get("quality") != "good":
        ui.warn(f"enrolment quality: {report.get('quality')}")
    vault.lock()
    return 0


def cmd_voice_test(args, ui: UI) -> int:
    config, vault = open_vault(args)
    from ..identity.voice import VoiceVerifier

    verifier = VoiceVerifier(vault)
    if not vault.has_voice():
        vault.lock()
        return ui.err("no voice template enrolled")
    results = []
    for path in args.file:
        score = verifier.verify(path)
        results.append({"file": str(path), "score": score.score, "passed": score.passed,
                        "threshold": score.threshold})
        ui.say(f"  {Path(path).name:<28s} score={score.score:+.4f} "
               f"threshold={score.threshold:.4f} -> {'PASS' if score.passed else 'REJECT'}")
    if args.json:
        print(json.dumps(results, indent=2))
    vault.lock()
    return 0 if all(r["passed"] for r in results) else 2


def cmd_recovery(args, ui: UI) -> int:
    """Show how many recovery codes remain, or issue a fresh set.

    The vault had no user-facing reissue path at all: once the printed set was
    used up or exposed there was no remedy short of re-initialising.
    """
    from ..identity.ceremony import IdentityCeremony

    config, vault = open_vault(args)
    try:
        if args.show:
            remaining = vault.recovery_remaining()
            ui.ok(f"{remaining} recovery code(s) remaining")
            ui.emit({"remaining": remaining})
            return 0
        codes = IdentityCeremony(config, vault).reissue_recovery_codes(args.count)
        ui.ok(f"issued {len(codes)} new recovery codes. Every previous code is now invalid.")
        ui.say("Write these down, offline, and store them where you keep your passport:")
        for i, code in enumerate(codes, 1):
            ui.say(f"  {i:2d}. {code}")
        ui.emit({"codes": codes, "count": len(codes)})
        return 0
    finally:
        vault.lock()


def cmd_change_passphrase(args, ui: UI) -> int:
    """Rotate the vault passphrase, re-wrapping the master key for every device.

    ``Vault.rotate_passphrase`` existed but nothing reached it, so a user could
    not change the one secret that protects everything else.
    """
    import getpass as _getpass

    config, vault = open_vault(args)
    try:
        old = read_passphrase(args)
        if args.new_passphrase_stdin:
            new = sys.stdin.readline().rstrip("\n")
        else:
            new = _getpass.getpass("New passphrase: ")
            confirm = _getpass.getpass("Repeat the new passphrase: ")
            if new != confirm:
                return ui.err("the two entries did not match; nothing was changed")
        vault.rotate_passphrase(old, new)
        ui.ok("passphrase changed. Every device uses the new passphrase; the master key "
              "did not change, so existing memories, projects and backups still open.")
        return 0
    finally:
        vault.lock()


def cmd_totp(args, ui: UI) -> int:
    from ..identity.ceremony import IdentityCeremony
    from ..identity.totp import generate_secret, provisioning_uri
    from ..identity.totp import totp as totp_now
    from ..identity.totp import verify as totp_verify

    config, vault = open_vault(args, allow_setup=not args.show)
    ceremony = IdentityCeremony(config, vault)
    if args.show:
        secret = vault.load_totp_secret()
        if not secret:
            vault.lock()
            return ui.err("TOTP not enrolled")
        ui.emit({"secret": secret, "current_code": totp_now(secret)})
    elif vault.has_totp() and not args.replace:
        # A second factor is ALREADY enrolled, and this run cannot prove the new
        # secret works (non-interactive), nor did anyone ask to replace it.
        # The old code path replaced it silently: a cron job or a stray command
        # could invalidate the factor protecting the vault without a prompt
        # (credential-rotation backlog item).
        vault.lock()
        return ui.err("a TOTP factor is already enrolled. Replacing it invalidates your "
                      "current authenticator entry. Re-run with --replace to rotate it "
                      "interactively (the new secret is saved only after a code from your "
                      "app is confirmed).")
    elif sys.stdin.isatty():
        # Interactive: the secret is stored ONLY after you prove your app shows the
        # right code. A mistyped key can therefore never lock you out of your vault.
        secret = generate_secret()
        acct = args.account or vault.user
        ui.ok("Add Nila to your authenticator app (Google or Microsoft Authenticator):")
        ui.say("   1. Open the app and choose “Enter a setup key” (or add an account manually)")
        ui.say(f"   2. Account name: Nila     Key: {secret}")
        ui.say("   3. Type of key: time-based")
        ui.say(f"   (or open this link on a device with the app: {provisioning_uri(secret, acct)})")
        confirmed = False
        for _ in range(5):
            code = input("Type the 6-digit code your app shows now: ").strip()
            if totp_verify(secret, code):
                confirmed = True
                break
            ui.warn("that code did not match - check the key and your phone's clock, then retry")
        if not confirmed:
            vault.lock()
            return ui.err("not confirmed, so nothing was saved. Run this step again.")
        vault.store_totp(secret)
        vault.audit("totp_enrolled", {"account": acct, "confirmed": True})
        ui.ok("authenticator confirmed and saved. You will need its code every time you start Nila.")
    else:
        info = ceremony.enroll_totp(account=args.account)
        ui.ok("TOTP enrolled. Add this to your authenticator app:")
        ui.emit({"secret": info["secret"], "uri": info["uri"],
                 "current_code": totp_now(info["secret"])})
        ui.warn("the secret is stored encrypted; this is the only time it is printed.")
    vault.lock()
    return 0


def cmd_session(args, ui: UI) -> int:
    """Show the idle rule, and (with --touch) say "I am still here".

    The idle window only ever moves for an OWNER action. Nothing in this command
    runs on a timer, and the dashboard's poll cannot call it: an auto-refresh is
    not the owner being present.
    """
    config, vault = open_vault(args)
    try:
        sess = vault.session
        if sess is None:
            return ui.err("session is not open")
        if args.touch:
            if not vault.touch_session():
                return ui.err("session expired; unlock again")
            vault.audit("session_touched", {"via": "cli"})
            ui.ok("still here - the idle window was refreshed")
        idle = vault.idle_seconds() or 0
        payload = {
            "idle_s": idle,
            "warn_after_s": config.warn_after,
            "lock_after_s": int(config.session_timeout),
            "warning_shown": idle >= config.warn_after,
            "locks_in_s": max(0, int(config.session_timeout) - idle),
            "absolute_in_s": sess.seconds_remaining(absolute=True),
            "mode": sess.mode,
            "degraded": sess.degraded,
        }
        if args.json:
            ui.emit(payload)
        else:
            ui.say(f"  last action      {idle // 60} min ago"
                   + ("   \033[33m(warning: Nila will lock soon)\033[0m"
                      if payload["warning_shown"] else ""))
            ui.say(f"  warns after      {config.warn_after // 60} min without an action")
            ui.say(f"  locks after      {int(config.session_timeout) // 60} min without an action")
            ui.say(f"  absolute limit   {payload['absolute_in_s'] // 60} min from now")
            ui.say("  say you are here: brain session --touch")
        return 0
    finally:
        vault.lock()


def cmd_chat(args, ui: UI) -> int:
    from ..agent.agent import Brain
    from ..agent.repl import run_repl

    config, vault = open_vault(args)
    warn_if_single_factor(ui, vault)   # cmd_ask delegates here, so both are covered
    memory = open_memory(config, vault)
    brain = Brain(config, vault, memory)
    if args.verbose:
        ui.say(f"provider: {brain.provider.name if brain.provider.available() else 'local (offline)'}")
    try:
        if args.message:
            turn = brain.handle(args.message)
            if args.json:
                print(json.dumps(turn.to_dict(), indent=2, default=str))
            elif turn.ok:
                ui.say(turn.answer)
            else:
                return ui.err(turn.error)
            return 0
        return run_repl(brain, verbose=args.verbose)
    finally:
        memory.close()
        vault.lock()


def cmd_ask(args, ui: UI) -> int:
    args.message = " ".join(args.text)
    return cmd_chat(args, ui)


def cmd_instruct(args, ui: UI) -> int:
    """Manage standing instructions. THIS is the owner-only route.

    `brain instruct` is a human command; there is no code path from a tool, a
    file, a web page or the model to here. That is the whole point of #2: a
    standing rule is applied to every later turn, so only the owner may write
    one.
    """
    from ..memory.store import InstructionError

    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    try:
        if args.action == "list":
            rows = memory.instructions()
            if args.json:
                ui.emit({"count": len(rows),
                         "instructions": [{"id": r.id, "text": r.body,
                                           "created_at": r.created_at} for r in rows]})
            elif not rows:
                ui.say("no standing instructions")
            else:
                for r in rows:
                    ui.say(f"  {r.id}  {r.body}")
                ui.say(f"\n({len(rows)} instruction(s))")
            return 0
        if args.action == "remove":
            target = args.id or (args.text[0] if args.text else "")
            if not target:
                return ui.err("which instruction? give its id: brain instruct list")
            ok = memory.remove_instruction(target)
            vault.audit("instruction_removed", {"id": target})
            return 0 if ok else ui.err(f"no such instruction: {target}")
        if args.action == "edit":
            target = args.id or (args.text[0] if args.text else "")
            text_parts = args.text[1:] if (not args.id and args.text) else args.text
            if not target:
                return ui.err("which instruction? give its id: brain instruct list")
            try:
                rec = memory.edit_instruction(target, " ".join(text_parts))
            except InstructionError as exc:
                vault.audit("instruction_refused", {"action": "edit", "reason": str(exc)})
                return ui.err(str(exc))
            vault.audit("instruction_edited", {"id": rec.id})
            ui.emit({"ok": True, "id": rec.id, "text": rec.body})
            return 0
        # add
        text = " ".join(args.text) if args.text else ""
        if not text.strip():
            return ui.err("say what the instruction should be, e.g. "
                          "brain instruct add \"call me Anna\"")
        try:
            rec = memory.add_instruction(text)
        except InstructionError as exc:
            vault.audit("instruction_refused", {"action": "add", "reason": str(exc)})
            return ui.err(str(exc))
        vault.audit("instruction_added", {"id": rec.id, "chars": len(rec.body)})
        ui.emit({"ok": True, "id": rec.id, "text": rec.body})
        return 0
    finally:
        memory.close()
        vault.lock()


def cmd_connectors(args, ui: UI) -> int:
    """The connectors screen, for a terminal.

    The one thing this command refuses to do is take a key as an argument: a
    command line is visible in the process list and in shell history. The key is
    read from a hidden prompt (or stdin for a script) and stored sealed in the
    vault; it is never echoed, printed or audited.
    """
    from ..agent.connectors import (CATEGORY_IDS, ConnectorError, ConnectorRegistry,
                                    StepUpRequired)

    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    brain = None
    try:
        from ..agent.agent import Brain
        brain = Brain(config, vault, memory)
        reg = ConnectorRegistry(config, vault, brain, memory)
        action = args.action or "list"

        def approve(target: str, uses: int = 1) -> None:
            """The fresh-login check for one action.

            This process unlocked the vault seconds ago with the passphrase and
            an authenticator code, so that ceremony IS the fresh login. Only if
            the session is not that fresh (or the CLI is ever reused with an
            already-open vault) does this ask again.
            """
            try:
                reg.authorise(target, uses=uses)
            except StepUpRequired:
                reg.authorise(target, read_passphrase(args), read_totp(args), uses=uses)

        if action == "list":
            st = reg.status()
            if args.json:
                ui.emit(st)
                return 0
            ui.say(f"  signed in as: {st['auth_level']}"
                   + ("  (keys editable)" if st["can_edit_keys"] else
                      "  (keys need an authenticator login)"))
            ui.say("\n  AI services")
            for r in st["ai"]:
                key = ("key " + r["key_hint"] + " · " + r["key_source"]) if r["has_key"] else "no key"
                ui.say(f"    {'ON ' if r['on'] else 'off'}  {r['id']:<10} {r['label']:<22}"
                       f" {key:<32} {r['detail'] if not r['ready'] else 'ready'}")
                hosts = ", ".join(r["allowed_hosts"]) or ("this PC only" if r["egress"] == "local_only" else "-")
                u = r["usage"]
                used = (f"{u['requests']} today" + (f" of {u['allowed']}" if u.get("budgeted") else ""))
                ui.say(f"          hosts: {hosts}   used: {used}")
            ui.say("\n  Outside services")
            for r in st["integrations"]:
                if r.get("placeholder"):
                    continue
                ui.say(f"    {'ON ' if r['on'] else 'off'}  {r['id']:<12} "
                       f"hosts: {', '.join(r['allowed_hosts']) or '-'}   "
                       f"{r['key_source'] or 'no key needed'}")
            ui.say("\n  No payment, banking or card connectors exist in this build.")
            return 0

        if action == "approve":
            target = args.name[0] if args.name else ""
            if not target:
                return ui.err("which action? e.g. brain connectors approve connector:gemini:on")
            out = reg.authorise(target, read_passphrase(args), read_totp(args))
            ui.emit(out)
            return 0

        cid = args.name[0] if args.name else ""
        if not cid:
            return ui.err("name a connector: " + ", ".join(CATEGORY_IDS))

        if action in ("on", "off"):
            # The ceremony that opened this process seconds ago IS the fresh
            # login the switch requires, so no second prompt inside 30 s.
            out = reg.set_on(cid, action == "on", approved=False)
            ui.emit(out)
            return 0

        if action == "key":
            # Approve FIRST, then read the secret: reading it first would let a
            # failed approval leave a typed key sitting in the terminal buffer,
            # and would consume a stdin line that belongs to the prompt.
            #
            # ``--key-env`` exists for a script, not for convenience: it keeps the
            # secret out of argv AND out of the shell history.
            approve(f"connector:{cid}:key:set")
            secret = (os.environ.get(args.key_env, "") if args.key_env
                      else (sys.stdin.readline().rstrip("\n") if sys.stdin.isatty() is False
                            else getpass.getpass(f"{cid} API key (not shown): ")))
            if args.key_env and not secret.strip():
                return ui.err(f"environment variable {args.key_env} is empty/unset")
            if not secret.strip():
                return ui.err("no key entered")
            out = reg.set_key(cid, secret, approved=True)
            ui.emit(out)
            ui.say("  stored sealed in the vault; only the last four characters are kept "
                   "readable so you can tell keys apart")
            return 0

        if action == "remove-key":
            approve(f"connector:{cid}:key:remove")
            out = reg.remove_key(cid, approved=True)
            ui.emit(out)
            return 0

        if action == "test":
            approve(f"connector:{cid}:test")
            out = reg.test(cid, approved=True)
            ui.emit(out)
            ui.say(f"  counted against today's budget: "
                   f"{out['usage']['requests']} request(s) used")
            return 0

        return ui.err(f"unknown action: {action}")
    except StepUpRequired as exc:
        return ui.err(str(exc))
    except ConnectorError as exc:
        return ui.err(str(exc))
    finally:
        memory.close()
        vault.lock()


def cmd_remember(args, ui: UI) -> int:
    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    text = " ".join(args.text) if args.text else (sys.stdin.read() if not sys.stdin.isatty() else "")
    if not text.strip():
        text = input("memory: ").strip()
    tags = [t for t in (args.tag or "").split(",") if t]
    rec = memory.remember(text, title=args.title or "", kind=args.kind, tags=tags)
    memory.close()
    vault.lock()
    ui.emit({"ok": True, "id": rec.id, "kind": rec.kind, "tags": rec.tags, "title": rec.title})
    return 0


def cmd_memory(args, ui: UI) -> int:
    """What is old enough to let go - a report, and a purge only when asked."""
    from ..memory import retention

    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    try:
        if args.purge:
            out = retention.purge(memory, days=args.older_than, apply=bool(args.yes))
            if out.get("purged"):
                vault.audit("memory_purge", {"count": out["purged"],
                                             "window_days": out["window_days"]})
        else:
            out = retention.report(memory, days=args.older_than)
    except retention.RetentionError as exc:
        ui.err(str(exc))
        memory.close()
        vault.lock()
        return 2
    try:
        if args.json:
            ui.emit(out)
        else:
            print(out["line"])
            for row in out["older_records"]:
                print(f"  {row['age_days']:>6.0f} d  {row['kind']:<14s} {row['title'][:50]}")
            if out["protected_records"]:
                print(f"\n{out['protected']} kept on purpose:")
                for row in out["protected_records"][:10]:
                    print(f"  {row['kind']:<14s} {row['title'][:50]}")
            print("\n" + out["note"])
            if not args.purge:
                print("To really delete them: `brain memory --older-than "
                      f"{out['window_days']} --purge --yes`")
        return 0
    finally:
        memory.close()
        vault.lock()


def cmd_recall(args, ui: UI) -> int:
    config, vault = open_vault(args)
    warn_if_single_factor(ui, vault)
    memory = open_memory(config, vault)
    rows = memory.search(" ".join(args.query), limit=args.limit, kind=args.kind, tag=args.tag)
    withheld = list(getattr(memory, "last_withheld", []) or [])
    level = memory.level
    memory.close()
    vault.lock()
    if args.json:
        print(json.dumps([{"id": r.id, "kind": r.kind, "tags": r.tags, "title": r.title,
                           "body": r.body, "score": r.score} for r in rows], indent=2))
        return 0
    if withheld:
        # Counts and classes only - never titles or bodies.
        classes: dict = {}
        for w in withheld:
            classes[w.get("class", "?")] = classes.get(w.get("class", "?"), 0) + 1
        summary = ", ".join(f"{n} {k}" for k, n in sorted(classes.items()))
        ui.warn(f"{len(withheld)} match(es) withheld at session tier '{level}' "
                f"({summary}). They exist but are not permitted in this session; "
                f"unlock with your voice factor to reach them.")
    if not rows:
        ui.say("nothing matched." if not withheld else "nothing you may see at this tier matched.")
        return 0
    for r in rows:
        ui.say(f"\033[36m{r.score:.3f}\033[0m  {r.id}  ({r.kind}{', ' + ','.join(r.tags) if r.tags else ''})")
        for line in (r.body or r.title).splitlines()[:6]:
            ui.say(f"      {line}")
    return 0


def cmd_list(args, ui: UI) -> int:
    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    rows = memory.list_records(kind=args.kind, tag=args.tag, limit=args.limit,
                               include_deleted=args.include_deleted)
    stats = memory.stats()
    memory.close()
    vault.lock()
    if args.json:
        print(json.dumps([{"id": r.id, "kind": r.kind, "tags": r.tags, "title": r.title,
                           "updated_at": r.updated_at, "deleted": r.deleted} for r in rows],
                         indent=2))
        return 0
    for r in rows:
        flag = " \033[31m[deleted]\033[0m" if r.deleted else ""
        ui.say(f"  {r.id}  {r.kind:<14s} {','.join(r.tags) or '-':<22s} {r.title[:60]}{flag}")
    ui.say(f"\n{stats['records']} records · {stats['pending_sync']} pending sync · "
           f"embedder {stats['embed_backend']}")
    return 0


def cmd_forget(args, ui: UI) -> int:
    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    if args.all:
        if not args.yes:
            memory.close()
            vault.lock()
            return ui.err("refusing to delete everything without --yes")
        rows = memory.list_records(limit=100000, include_deleted=True)
        for r in rows:
            memory.delete(r.id, hard=args.hard)
        ui.ok(f"deleted {len(rows)} records")
    else:
        ok = memory.delete(args.id, hard=args.hard)
        if not ok:
            memory.close()
            vault.lock()
            return ui.err(f"no such record: {args.id}")
        ui.ok(f"{'hard-deleted' if args.hard else 'tombstoned'}: {args.id}")
    memory.close()
    vault.lock()
    return 0


def cmd_sync(args, ui: UI) -> int:
    from ..cloud.protocol import SyncEngine, SyncError

    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    try:
        engine = SyncEngine(config, memory, vault)
    except SyncError as exc:
        memory.close()
        vault.lock()
        return ui.err(str(exc))
    if args.status:
        ui.emit(engine.status())
    else:
        try:
            report = engine.sync(dry_run=args.dry_run)
        except SyncError as exc:
            memory.close()
            vault.lock()
            return ui.err(str(exc))
        ui.emit(report)
        for warning in report.get("warnings") or []:
            ui.warn(warning)
        if report.get("errors"):
            ui.warn(f"{len(report['errors'])} error(s); first: {report['errors'][0]}")
            # A failed sync used to exit 0, so a scripted backup looked healthy.
            memory.close()
            vault.lock()
            return 1
    memory.close()
    vault.lock()
    return 0


def cmd_update(args, ui: UI) -> int:
    from ..updates.manager import UpdateManager

    config, vault = open_vault(args, require_unlocked=False)
    um = UpdateManager(config)
    if args.action == "list":
        ui.emit({"channels": um.list_channels(), **um.status()})
    elif args.action == "check":
        ui.emit(um.check(args.channel))
    elif args.action == "apply":
        result = um.apply(args.channel, force=args.force)
        ui.emit(result)
        if result.get("requires_restart"):
            ui.warn("a package artifact was installed; restart `brain` to run the new code")
        return 0 if result.get("ok") else 1
    elif args.action == "rollback":
        ui.emit(um.rollback())
    else:
        return ui.err(f"unknown action {args.action}")
    return 0


def cmd_channel_add(args, ui: UI) -> int:
    from ..updates.manager import UpdateManager

    config = Config.load(args.home)
    um = UpdateManager(config)
    result = um.add_channel(args.name, args.url, alg=args.alg, public=args.public or "",
                            key_id=args.key_id or "", mode=args.mode,
                            auto_apply=args.auto_apply)
    ui.emit(result)
    if result.get("warning"):
        ui.warn(result["warning"])
    return 0


def cmd_device(args, ui: UI) -> int:
    # `import` runs on a machine with NO vault yet, so it must not go through
    # open_vault(); every other action does.
    if args.action == "import":
        bundle_path = Path(args.bundle or "").expanduser()
        if not args.bundle or not bundle_path.exists():
            return ui.err(f"bundle not found: {args.bundle or '(none)'} "
                          "(use --path/to/bundle.json)")
        try:
            bundle = json.loads(bundle_path.read_bytes())
        except json.JSONDecodeError as exc:
            return ui.err(f"bundle is not valid JSON: {exc}")
        passphrase = read_passphrase(args)
        config = Config.load(args.home)
        if args.fast_kdf:
            config.fast_kdf = True
        vault = Vault(config)
        if vault.initialized:
            if not args.force:
                return ui.err("this device already has a vault; pass --force to replace it "
                              "(the existing one will be deleted)")
            config.identity_path.unlink(missing_ok=True)
            if config.memory_path.exists():
                config.memory_path.unlink()
            vault = Vault(config)
        try:
            info = vault.import_bundle(bundle, passphrase, device_label=args.label)
        except (PermissionError, ValueError, RuntimeError) as exc:
            return ui.err(str(exc))
        config.agent.setdefault("device_id", info["device_id"])
        config.save()
        ui.ok(f"enrolled as device {info['device_id']} ({info['label']})")
        ui.emit({**info, "next": "brain sync  (then brain chat)"})
        vault.audit("cli_device_import", {"device": info["device_id"]})
        vault.lock()
        return 0

    config, vault = open_vault(args)

    if args.action == "list":
        ui.emit({"this_device": vault.device_id, "devices": vault.devices()})
    elif args.action == "export":
        bundle = vault.export_bundle(label=args.label)
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(bundle, indent=2))
        os.chmod(out, 0o600)
        ui.ok(f"enrolment bundle written to {out}")
        ui.emit({"bytes": out.stat().st_size, "user": bundle["user"],
                 "exported_from": bundle["exported_from"],
                 "contains_plaintext": False,
                 "next": f"on the new device: brain device import {out}"})
        ui.warn("the bundle is ciphertext-only, but treat it as sensitive: it lets anyone "
                "who also knows your passphrase enrol a device.")
    elif args.action == "revoke":
        ok = vault.revoke_device(args.device_id)
        ui.emit({"revoked": ok, "device_id": args.device_id,
                 "note": "run `brain device rotate-key` so the revoked device's MK wrap is useless"
                 if ok else ""})
    elif args.action == "rotate-key":
        passphrase = read_passphrase(args, "current passphrase: ")
        info = vault.rotate_master_key(passphrase)
        ui.emit(info)
        ui.warn("now run `brain memory rekey` with the NEW master key")
    else:
        vault.lock()
        return ui.err(f"unknown action {args.action}")
    vault.lock()
    return 0


def cmd_backup(args, ui: UI) -> int:
    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    data = memory.export_encrypted_backup()
    out.write_bytes(data)
    os.chmod(out, 0o600)
    ident = Path(str(out) + ".identity.json")
    if args.with_identity:
        bundle = vault.export_bundle(label="backup")
        ident.write_text(json.dumps(bundle, indent=2))
        os.chmod(ident, 0o600)
    memory.close()
    vault.lock()
    ui.emit({"backup": str(out), "bytes": len(data),
             "identity_bundle": str(ident) if args.with_identity else None,
             "plaintext": False,
             "restore": "brain restore <backup> [--identity <bundle>]"})
    return 0


def cmd_restore(args, ui: UI) -> int:
    import shutil

    config = Config.load(args.home)
    if config.memory_path.exists() and not args.force:
        return ui.err(f"{config.memory_path} already exists (use --force to overwrite)")
    data = Path(args.backup).expanduser().read_bytes()
    header, _, rest = data.partition(b"\n")
    try:
        meta = json.loads(header)
    except json.JSONDecodeError:
        return ui.err("not a personal-brain backup (bad header)")
    # Format 2 adds the sealed project attachments after the DB snapshot. The
    # rpartition that used to split header/body/tag kept the attachments inside
    # `body`, so the sha256 check failed on every format-2 backup.
    attachments: dict = {}
    if "attachments_sha256" in meta:
        # <db>\n<attachments json>\n<tag>
        #
        # The DB snapshot is BINARY and can contain 0x0a bytes, so neither
        # partition nor rpartition can find the boundary on its own. The header
        # records the exact DB length; cut there and nothing else.
        size = int(meta.get("bytes") or 0)
        body = rest[:size]
        tail = rest[size + 1:] if len(rest) > size else b""
        att_blob, _, _tag = tail.rpartition(b"\n")
        from ..util.misc import sha256_hex as _sha

        if _sha(body) != meta.get("sha256"):
            return ui.err("backup database block failed its integrity check")
        if _sha(att_blob) != meta.get("attachments_sha256"):
            return ui.err("backup attachment block failed its integrity check")
        if att_blob.strip():
            try:
                attachments = json.loads(att_blob)
            except json.JSONDecodeError:
                return ui.err("backup attachment block is not valid JSON")
            if not isinstance(attachments, dict):
                return ui.err("backup attachment block is malformed")
    else:
        body, _, _tag = rest.rpartition(b"\n")
    if args.identity:
        bundle = json.loads(Path(args.identity).expanduser().read_bytes())
        passphrase = read_passphrase(args)
        vault = Vault(config)
        if vault.initialized and args.force:
            config.identity_path.unlink()
            vault = Vault(config)
        vault.import_bundle(bundle, passphrase, device_label=args.label or "restored")
    else:
        vault = Vault(config)
        if not vault.initialized:
            return ui.err("no vault to decrypt with; pass --identity <bundle>")
        passphrase = read_passphrase(args)
        vault.start_session(vault.unlock(passphrase), mode="passphrase", degraded=True)
    from ..util.misc import sha256_hex

    if sha256_hex(body) != meta.get("sha256"):
        vault.lock()
        return ui.err("backup integrity check failed (sha256 mismatch)")
    config.memory_path.write_bytes(body)
    os.chmod(config.memory_path, 0o600)
    restored_files = 0
    if attachments:
        from ..util.misc import b64d as _b64d

        root = config.memory_path.parent / "projects"
        root.mkdir(parents=True, exist_ok=True)
        for name, blob in attachments.items():
            if not name.endswith(".pbx") or "/" in name or "\\" in name:
                continue                     # never let a backup name a path
            try:
                (root / name).write_bytes(_b64d(blob))
                os.chmod(root / name, 0o600)
                restored_files += 1
            except (ValueError, OSError):
                continue
    memory = MemoryStore(config, vault.session.mk, session=vault.session)
    stats = memory.stats()
    memory.close()
    vault.lock()
    ui.ok("restored")
    ui.emit({"sha256": meta.get("sha256"), "records": stats["records"],
             "kinds": stats["kinds"], "embedder": stats["embed_backend"],
             "attachments_restored": restored_files,
             "attachments_expected": len(attachments)})
    return 0


def cmd_config(args, ui: UI) -> int:
    config = Config.load(args.home)
    stores = {"sync": config.sync, "updates": config.updates, "agent": config.agent,
              "identity": config.identity}
    if args.action == "show":
        ui.emit({"home": str(config.home), "fast_kdf": config.fast_kdf,
                 "session_timeout": config.session_timeout,
                 "session_absolute_timeout": config.session_absolute_timeout,
                 **{k: v for k, v in stores.items()}})
        return 0
    if not args.key:
        return ui.err("usage: brain config set <section.key> <value>")
    section, _, name = args.key.partition(".")
    store = stores.get(section)
    if store is None:
        return ui.err(f"unknown section '{section}' (choose from {sorted(stores)})")
    if args.action == "get":
        ui.emit({args.key: store if not name else store.get(name)})
        return 0
    value: Any = args.value
    if value is None:
        store.pop(name, None)
        config.save()
        ui.ok(f"unset {args.key}")
        return 0
    if value.lower() in ("true", "false"):
        value = value.lower() == "true"
    else:
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    store[name] = value
    config.save()
    ui.ok(f"set {args.key} = {value!r}")
    return 0


def cmd_audit(args, ui: UI) -> int:
    config = Config.load(args.home)
    vault = Vault(config)
    rows = vault.read_audit(args.limit)
    integrity = vault.verify_audit_chain()
    if args.json:
        print(json.dumps({"integrity": integrity, "entries": rows}, indent=2))
        return 0 if integrity.get("ok", True) else 3
    import datetime as _dt

    for r in rows:
        when = _dt.datetime.fromtimestamp(r["ts"] / 1000).strftime("%m-%d %H:%M:%S")
        ui.say(f"{when}  {r['event']:<24s} {dumps(r.get('data'))[:90]}")
    ui.say(f"\nchain integrity: {integrity}")
    if integrity.get("legacy_unprotected"):
        ui.warn(f"{integrity['legacy_unprotected']} older entries predate the "
                "payload-bound chain: their `data` was never hashed and cannot be verified.")
    return 0


def cmd_doctor(args, ui: UI) -> int:
    """Self-test: verifies crypto, storage, voice and sync paths locally."""
    import shutil
    import tempfile

    from ..crypto.envelope import backend, decrypt, encrypt
    from ..identity.voice import MfccVoiceBackend, load_model

    checks: List[Dict[str, Any]] = []

    def check(name: str, fn):
        try:
            detail = fn()
            checks.append({"check": name, "ok": True, "detail": detail})
        except Exception as exc:
            checks.append({"check": name, "ok": False,
                           "detail": f"{type(exc).__name__}: {exc}"})

    def c_crypto():
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

    def c_voice_model():
        model = load_model()
        if not model.available:
            raise AssertionError("voice_model.json missing or unreadable")
        ev = model.evaluated
        return (f"backend={model.name} keep={model.keep} threshold={model.threshold} "
                f"eer={ev.get('eer')} at_balanced={ev.get('at_balanced')}")

    def c_optional():
        present = []
        for mod in ("cryptography", "numpy", "soundfile", "sounddevice", "keyring",
                    "sentence_transformers"):
            if __import__("brain.util.misc", fromlist=["x"]).try_import(mod):
                present.append(mod)
        return f"installed: {present or 'none (running pure-stdlib)'}"

    def c_roundtrip():
        tmp = tempfile.mkdtemp(prefix="brain-doctor-")
        try:
            cfg = Config.load(Path(tmp) / "home")
            cfg.fast_kdf = True
            v = Vault(cfg)
            v.initialize("doctor-passphrase", "doctor", "doctor-device")
            m = MemoryStore(cfg, v.session.mk)
            rec = m.remember("doctor probe", tags=["doctor"])
            assert m.get(rec.id).body == "doctor probe"
            assert m.search("doctor probe")
            from ..cloud.protocol import LocalBlobStore, SyncEngine

            engine = SyncEngine(cfg, m, v, LocalBlobStore(Path(tmp) / "cloud"))
            report = engine.sync()
            assert report["pushed"] == 1, report
            m.close()
            v.lock()
            return "init -> remember -> search -> e2ee sync all ok"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    check("crypto envelope", c_crypto)
    check("voice calibration model", c_voice_model)
    check("end-to-end vault+memory+sync", c_roundtrip)
    check("optional dependencies", c_optional)

    config = Config.load(args.home)
    if Vault(config).initialized:
        def c_real():
            v = Vault(config)
            return (f"vault ok: user={v.user} device={v.device_id} "
                    f"devices={len(v.devices())} audit={v.verify_audit_chain()}")

        check("installed vault", c_real)

    failed = [c for c in checks if not c["ok"]]
    for c in checks:
        mark = "\033[32mPASS\033[0m" if c["ok"] else "\033[31mFAIL\033[0m"
        print(f"  [{mark}] {c['check']:<32s} {c['detail']}")
    if args.json:
        print(json.dumps(checks, indent=2))
    return 1 if failed else 0


def cmd_report(args, ui: UI) -> int:
    from .. import diagnostics
    from ..agent.agent import Brain

    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    brain = Brain(config, vault, memory)
    rep = diagnostics.report(brain)
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print(diagnostics.report_text(rep))
    memory.close()
    vault.lock()
    return 0


def _issue_secrets(config, vault) -> List[str]:
    """Every secret value in reach, so the scrubber can remove it verbatim.

    Read-only: this never prints, stores or returns anything to the caller except
    the strings themselves, which only ever go to `scrub`.
    """
    out: List[str] = []
    try:
        from ..agent.connectors import ConnectorVault
        names = ConnectorVault(vault).names()
        for name in names:
            try:
                material = ConnectorVault(vault).key_material(name)
                for k in ("value", "secret", "api_key"):
                    if material.get(k):
                        out.append(str(material[k]))
            except Exception:
                continue
    except Exception:
        pass
    for env in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY"):
        if os.environ.get(env):
            out.append(os.environ[env])
    for attr in ("_passphrase", "passphrase"):
        val = getattr(vault, attr, None)
        if isinstance(val, str) and val:
            out.append(val)
    return [s for s in out if len(s) >= 4]


def _issue_events(vault, limit: int = 20) -> List[Dict[str, Any]]:
    """The last audit rows, reduced to names - never arguments."""
    try:
        rows = vault.read_audit(limit=limit)
    except Exception:
        return []
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append({"name": row.get("event") or row.get("name") or "",
                    "at": row.get("at") or row.get("ts") or 0,
                    "ok": row.get("ok", True)})
    return out


def cmd_issue(args, ui: UI) -> int:
    """Turn "this is what is wrong" into a report you can read before it leaves."""
    from .. import issues_report as ir

    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    try:
        from ..diagnostics import run_checks
        checks = run_checks(config)
        secrets = _issue_secrets(config, vault)
        index = vault.meta_get("issue_index", {}) or {}
        bundle = ir.build_bundle(args.problem or "", checks, home=Path(args.home),
                                 secrets=secrets, events=_issue_events(vault))
        new_index, seen = ir.record_issue(index, bundle)
        show = ir.preview(bundle)
        show["already_reported"] = seen
        show["known_issues"] = ir.known_issues(new_index)
        if not args.write:
            show["will_write"] = None
            if args.json:
                print(json.dumps({"bundle": bundle, "preview": show}, indent=2,
                                 ensure_ascii=False))
            else:
                print(show["text"])
                print(f"already reported: {'yes' if seen else 'no'} "
                      f"(fingerprint {bundle['fingerprint']})")
                print("\nnothing written. re-run with --write --yes to save this "
                      "report as a file (it is never sent anywhere by Nila).")
            return 0
        if not args.yes:
            return ui.err("writing a report is a file write; add --yes after reading "
                          "the preview")
        out_dir = Path(args.out) if args.out else Path(args.home) / "reports"
        path = ir.write_bundle(bundle, out_dir, approved=bool(args.yes))
        vault.meta_set("issue_index", new_index)
        vault.audit("issue_report_written",
                    {"category": bundle["triage"]["category"],
                     "fingerprint": bundle["fingerprint"], "seen_before": seen})
        if args.json:
            print(json.dumps({"ok": True, "path": str(path), "preview": show,
                              "already_reported": seen}, indent=2, ensure_ascii=False))
        else:
            print(f"report written: {path}")
            print("Nila did not send it anywhere. Attach it to an issue yourself, or "
                  "paste the details - it contains no keys, memories or contact details.")
        return 0
    finally:
        if memory is not None:
            memory.close()


def cmd_voice(args, ui: UI) -> int:
    """Enrol the one voice, check a sample, or show a challenge - vault-gated."""
    from ..memory.vault import Vault as _V
    from ..security.speaker import SpeakerError, SpeakerLock
    from ..util import audio as _audio

    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    lock = SpeakerLock(config.home / "speaker.json", vault=vault)
    action = (args.action or "status").lower()
    try:
        if action == "status":
            ui.emit({"ok": True, "enrolled": lock.enrolled(),
                     "samples": len(lock.profile.get("samples") or []),
                     "pitch_hz": lock.profile.get("pitch_median", 0),
                     "threshold": round(lock.accept_threshold(), 3),
                     "limits": __import__("brain.security.speaker",
                                          fromlist=["LIMITS"]).LIMITS})
            return 0
        if action == "reset":
            ui.emit(lock.reset())
            return 0
        if action in ("enrol", "enroll", "check"):
            source = args.file
            if source:
                sample = _audio.load_audio(str(Path(source).expanduser()))
                samples, rate = sample.samples, sample.sample_rate
            else:
                sample = _audio.record_microphone(seconds=float(args.seconds or 3.0))
                samples, rate = sample.samples, sample.sample_rate
            if action == "check":
                decision = lock.judge(samples, rate)
                ui.emit(decision.to_dict())
                return 0 if decision.ok else 1
            result = lock.enrol(samples, rate)
            ui.emit({**result, "note": ("say it twice more to fit the threshold to "
                                        "your voice: `brain voice enrol` again")})
            return 0
        if action == "challenge":
            ui.emit(lock.challenge())
            return 0
        ui.err(f"unknown action '{action}'")
        return 2
    except (SpeakerError, _audio.AudioError) as exc:
        ui.err(str(exc))
        return 2
    finally:
        memory.close()
        vault.lock()


def cmd_file(args, ui: UI) -> int:
    """Read whatever the owner hands over: document, code, image, video."""
    from ..agent import media

    if not args.path:
        ui.err("give a file: `brain file <path>`")
        return 2
    try:
        out = media.read_anything(Path(args.path).expanduser(), limit=args.limit)
    except media.MediaError as exc:
        ui.err(str(exc))
        return 2
    if args.json:
        ui.emit(out)
        return 0
    if out.get("kind") == "document" and args.full:
        print(out.get("text", ""))
        return 0
    if out.get("kind") == "document":
        text = out.get("text", "")
        print(f"{Path(args.path).name}: {out.get('format')}, {out.get('chars'):,} characters"
              + ("  (truncated)" if out.get("truncated") else ""))
        if out.get("note"):
            print(out["note"])
        print()
        print(text[: args.preview])
        return 0
    if out.get("kind") == "video":
        print(out.get("rendered"))
        plan = out.get("edit_plan") or {}
        if plan:
            print("\nTo find the cuts (needs ffmpeg, one pass, no re-encode):")
            print("  scenes  :", plan.get("scene_detect"))
            print("  silence :", plan.get("silence_detect"))
            print("  trim    :", plan.get("trim_without_reencode"))
        return 0
    print(out.get("rendered") or out)
    return 0


def cmd_storage(args, ui: UI) -> int:
    """Where the archive lives - D: by the owner's choice, checked honestly."""
    from ..config import StorageUnavailable

    config = Config.load(args.home)
    agent = dict(config.agent or {})
    if args.set:
        agent["storage_dir"] = str(Path(args.set).expanduser())
        agent["storage_create"] = True
        config.agent = agent
        config.save()
    if args.fallback_c:
        agent["storage_fallback_to_c"] = bool(args.fallback_c == "on")
        config.agent = agent
        config.save()
    if args.clear:
        agent.pop("storage_dir", None)
        agent.pop("storage_fallback_to_c", None)
        config.agent = agent
        config.save()
    try:
        path = config.memory_path
    except StorageUnavailable as exc:
        ui.emit({"ok": False, "error": str(exc),
                 "configured": agent.get("storage_dir"),
                 "fallback": bool(agent.get("storage_fallback_to_c"))})
        return 1
    exists = path.exists()
    ui.emit({"ok": True, "store": str(path),
             "configured": agent.get("storage_dir") or "(default: the Nila home folder)",
             "fallback_to_c": bool(agent.get("storage_fallback_to_c")),
             "exists": exists,
             "size_mb": round(path.stat().st_size / 1048576, 2) if exists else 0,
             "free_gb": _free_gb(path),
             "note": ("Nila never deletes anything on its own. `brain forget <id>` "
                      "removes one record; `brain backup` writes an encrypted copy.")})
    return 0


def _free_gb(path: Path) -> float:
    import shutil as _sh

    probe = path if path.exists() else path.parent
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return round(_sh.disk_usage(str(probe)).free / 1073741824, 1)
    except OSError:
        return 0.0


def cmd_launcher(args, ui: UI) -> int:
    """Make starting Nila one double-click (or one key press)."""
    from ..util import launcher

    config = Config.load(args.home)
    if args.talk:
        # The push-to-talk icon: one press, a bounded listen, then it closes.
        try:
            out = launcher.push_to_talk_launchers(
                config.home, Path(args.out).expanduser() if args.out else None,
                max_seconds=args.seconds)
        except launcher.LauncherError as exc:
            ui.err(str(exc))
            return 2
        if args.json:
            ui.emit(out)
        else:
            what = out["written"]["talk"]
            print(f"icon written: {what}")
            if out["written"].get("startup"):
                print(f"also in Startup: {out['written']['startup']}")
            print(f"runs: {out['runs']}  (listens for {out['seconds']:.0f} s, then closes)")
            print("\n" + out["note"])
        return 0
    if args.plan or not args.write:
        plan = launcher.plan(config.home)
        if args.json:
            ui.emit(plan)
        else:
            print(f"Platform: {plan['platform']}")
            for mode in plan["modes"]:
                mark = "yes" if mode["works"] else "no "
                print(f"  [{mark}] {mode['label']:<34s} {mode['how']}")
            print("\n" + plan["limits"])
            print("\nWrite the launchers with: brain launcher --write")
        return 0
    try:
        out = launcher.write_launchers(config.home,
                                       Path(args.out).expanduser() if args.out else None)
    except launcher.LauncherError as exc:
        ui.err(str(exc))
        return 2
    ui.emit(out)
    if not args.json:
        print("\n" + out["note"])
    return 0


def cmd_ears(args, ui: UI) -> int:
    """Hearing, from the command line: what is installed, or one recording."""
    from ..agent import ears

    if args.status or (not args.listen and not args.file):
        status = ears.available()
        if args.json:
            ui.emit(status)
            return 0
        print(f"Transcriber : {', '.join(status['engines']) or 'none installed'}")
        print(f"Microphone  : {'yes' if status['microphone'] else 'no'}")
        print(f"Best here   : {status.get('best') or 'nothing yet'}")
        print("\n" + status["how"])
        print("\n" + ears.help_text())
        return 0

    def _listen() -> Dict[str, Any]:
        return ears.hear(seconds=float(args.listen or 5.0), language=args.lang,
                         model_size=args.model, source=args.file or None)

    try:
        if args.listen and not args.file:
            # A microphone is opened the way the vault is opened: after the owner
            # proves they are there. The words are then still checked against the
            # enrolled voice before anything is allowed to act on them.
            _config, _vault = open_vault(args)
            print("listening...", flush=True)
            try:
                heard = _listen()
            finally:
                _vault.lock()
        else:
            heard = _listen()
    except (ears.EarsError, ears.AudioError, OSError) as exc:
        ui.err(str(exc))
        print("\n" + ears.help_text())
        return 2

    text = heard.get("text") or ""
    if args.json:
        ui.emit({k: v for k, v in heard.items() if k != "samples"})
        return 0
    print(f"[{heard.get('engine', '?')}] {heard.get('seconds', 0)} s -> "
          f"{text if text else '(nothing heard)'}")
    return 0 if text else 1


def _clock(at_ms: int) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(int(at_ms) / 1000).strftime("%a %d %b, %H:%M")


def _talk_brain(config: Any, vault: Any, memory: Any) -> Any:
    """A Brain for the reminder book alone - no provider, so nothing is called."""
    from ..agent.agent import Brain

    return Brain(config, vault, memory, provider=_NoProvider())


class _NoProvider:
    """Stands in for a model in code paths that must never reach one."""

    name = "none"
    needs_network = False

    def available(self) -> bool:
        return False

    def complete(self, *a, **k):           # pragma: no cover - never reached
        raise RuntimeError("this path must not ask a model anything")


def _due_alerts(brain: Any, spoken: bool = False, voice: str = "") -> List[Dict[str, Any]]:
    """Reminders that came due while Nila was closed, and say them.

    They are consumed (closed) so the same one is not announced twice, and each
    line says how late it is. This is called by the icon paths, which are the only
    ones that run without the owner already looking at Nila.
    """
    try:
        rows = brain.due_reminders(consume=True)
    except Exception:                      # a broken record must not block the action
        return []
    for row in rows:
        late = int(row.get("late_minutes") or 0)
        when = "now" if late < 1 else f"{late} min late"
        line = f"Reminder ({when}): {row.get('title', '')}"
        print(line, flush=True)
        if spoken:
            from ..util import speech

            speech.speak_text(line, voice=voice)
    return rows


def cmd_talk(args, ui: UI) -> int:
    """One press of an icon: listen once, check the voice, act, answer aloud.

    This is the whole of stage 7's loop in one place, and every refusal in it is
    deliberate: no microphone or no transcriber means an honest line and exit 2;
    a voice that is not the owner means the words are dropped before they are
    used; a sentence that is not understood is answered, not guessed at.
    """
    from ..agent import ears
    from ..agent import media
    from ..security.speaker import SpeakerLock
    from ..util import apps as appmod
    from ..util import finder
    from ..util import launcher as launchmod
    from ..util import speech
    from ..util.apps import AppError

    config, vault = open_vault(args)
    memory = open_memory(config, vault)

    def answer(line: str) -> None:
        if args.json:
            return
        print(line)
        if not args.no_speak:
            spoken = speech.speak_text(line, voice=args.speak_voice or "",
                                       voice_dir=args.home / "voices")
            if not spoken.get("spoken"):
                print(f"(not spoken aloud: {spoken.get('reason', '')})")

    try:
        # ---- 1. hear it ---------------------------------------------------- #
        if args.text:
            heard = {"text": args.text, "samples": [], "rate": 0, "engine": "typed"}
        else:
            try:
                heard = ears.hear(seconds=float(args.seconds or 6.0),
                                  language=args.lang, model_size=args.model,
                                  source=args.file or None)
            except (ears.EarsError, ears.AudioError, OSError) as exc:
                reason = str(exc)
                if args.json:
                    ui.emit({"ok": False, "stage": "hear", "error": reason})
                else:
                    answer(f"I could not hear you: {reason}")
                return 2
        said = (heard.get("text") or "").strip()
        if not said:
            answer("I heard nothing I could turn into words.")
            return 1

        # ---- 2. is it the owner? ------------------------------------------- #
        if not args.skip_voice_check and heard.get("samples"):
            lock = SpeakerLock(config.home / "speaker.json", vault=vault)
            decision = lock.judge(heard["samples"], heard["rate"])
            if not decision.ok:
                if args.json:
                    ui.emit({"ok": False, "stage": "voice", "verdict": decision.to_dict(),
                             "words_used": False})
                else:
                    answer(f"I did nothing with that: {decision.reason}")
                return 3

        # ---- 3. what was asked? -------------------------------------------- #
        # Reminders that came due while this icon was closed are announced first,
        # because they are the reason the owner pressed it.
        _due_alerts(_talk_brain(config, vault, memory),
                    spoken=not args.no_speak, voice=args.speak_voice or "")

        spoken = launchmod.spoken_action(said)
        action, argument = spoken.get("action", ""), spoken.get("argument", "")
        result: Dict[str, Any] = {"ok": True, "heard": said, "action": action,
                                 "argument": argument}
        if not action:
            answer(spoken.get("reason") or "I did not understand that.")
            if args.json:
                ui.emit({**result, "ok": False, "reason": spoken.get("reason")})
            return 1

        # ---- 4. do it ------------------------------------------------------ #
        if action == "open":
            try:
                result["app"] = appmod.open_app(argument, config=config,
                                                dry_run=args.show)
                launched = result["app"]
                if args.show:
                    answer(f"{argument} would run: {' '.join(launched['would_run'])}")
                else:
                    answer(f"Opening {argument}.")
            except AppError as exc:
                result["ok"], result["error"] = False, str(exc)
                answer(str(exc))
        elif action == "find":
            found = finder.search(args.search_root or Path.home(), argument,
                                  limit=args.limit, content=args.content)
            result["search"] = found
            hits = found.get("hits") or []
            if hits:
                answer(f"Found {len(hits)}. First: {hits[0]['path']}")
            else:
                answer(f"Nothing matched '{argument}' in "
                       f"{args.search_root or Path.home()}.")
        elif action == "read":
            target = Path(argument).expanduser()
            if not target.is_file():
                result["ok"] = False
                answer(f"I could not find {target} - say 'nila find {argument}' first.")
            else:
                report = media.read_anything(target, limit=args.limit)
                result["file"] = {k: v for k, v in report.items() if k != "text"}
                answer(str(report.get("summary") or report.get("rendered")
                           or f"{target.name} read."))
        elif action == "remind":
            from .reminders import ReminderError, parse_when

            phrase = argument
            try:
                when = parse_when(phrase)
                rec = _talk_brain(config, vault, memory).reminder_book().add(
                    phrase, when, original=said)
                result["reminder"] = {"id": rec.id, "title": rec.title,
                                      "at_ms": when.at_ms}
                answer(f"Reminder set for {_clock(when.at_ms)}: {rec.title}")
            except ReminderError as exc:
                result["ok"] = False
                answer(f"I could not set that reminder: {exc}")
        elif action == "stop":
            answer("Stopping. Nothing else is done.")
        else:
            result["ok"] = False
            answer(f"'{action}' is on the list for a later stage - it is not wired yet.")
        if args.json:
            ui.emit(result)
        return 0 if result.get("ok", True) else 1
    finally:
        memory.close()
        vault.lock()


def cmd_find(args, ui: UI) -> int:
    """Find a file by name, or by words inside it - without opening anything."""
    from ..util import finder

    query = " ".join(args.query or []).strip()
    if not query:
        ui.err("give something to look for: `brain find invoice`")
        return 2
    root = Path(args.in_ or Path.home()).expanduser()
    if not root.is_dir():
        ui.err(f"that folder is not there: {root}")
        return 2
    out = finder.search(root, query, limit=args.limit, content=args.content,
                        newer_than_days=args.since or None)
    if args.json:
        ui.emit(out)
        return 0
    if not out.get("ok"):
        ui.err(out.get("error", "search failed"))
        return 2
    hits = out.get("hits") or []
    print(f"{len(hits)} hit(s) in {root}  ({out.get('scanned', 0):,} files checked"
          f" in {out.get('seconds', 0)} s"
          f"{'' if out.get('complete') else ', stopped at the cap'})")
    for hit in hits:
        print("  " + hit["path"])
        if hit.get("snippet"):
            print(f"     ... {hit['snippet']}")
    if not hits:
        print("  nothing matched. Add --content to look inside text files.")
    return 0


def cmd_open(args, ui: UI) -> int:
    """Open an app from the fixed list - or say why it cannot be opened."""
    from ..util import apps
    from ..util.apps import AppError

    config = Config.load(args.home)
    if not args.app or args.list:
        rows = apps.list_apps(config)
        if args.json:
            ui.emit({"apps": rows})
            return 0
        print(f"{'name':<12s} {'installed':<10s} command")
        for row in rows:
            mark = "yes" if row["installed"] else "no"
            print(f"{row['name']:<12s} {mark:<10s} {row['command'] or '(none on this OS)'}")
        print("\nOnly these names work. `brain open <name> --show` says what would run.")
        return 0
    try:
        out = apps.open_app(args.app, config=config, dry_run=args.show)
    except AppError as exc:
        ui.err(str(exc))
        return 2
    if args.json:
        ui.emit(out)
        return 0 if out.get("ok") else 1
    if out.get("dry_run"):
        print("would run : " + " ".join(out["would_run"]))
        print("installed : " + ("yes" if out.get("installed") else "no"))
        if not out.get("ok"):
            print("note      : " + out.get("error", ""))
            return 1
        print("\nNothing was started. Drop --show to really open it.")
        return 0
    print(f"opened {out['name']} (pid {out['pid']}). {out['note']}")
    return 0


def cmd_web(args, ui: UI) -> int:
    """The free connectors, from the command line."""
    from ..agent import web

    what = (args.what or "").lower()
    query = " ".join(args.text or []).strip()
    try:
        if what in ("weather", "w", "வானிலை"):
            tamil = bool(re.search(r"[\u0B80-\u0BFF]", query))
            place = re.sub(r"\b(weather|in|at|for|today|tomorrow|forecast|"
                           r"வானிலை|இல்)\b", " ", query, flags=re.I).strip(" ,?.")
            res = web.weather(place or "Chennai", days=args.days, tamil=tamil)
        elif what in ("wiki", "wikipedia"):
            res = web.wikipedia(query, lang=args.lang or "en")
        elif what in ("youtube", "yt"):
            config = Config.load(args.home)
            # The key lives in the sealed vault, never in config.json: the
            # connectors screen already stores secrets there, and a plaintext
            # key in a config file is the mistake the integration gateway
            # refuses at load time.
            key = os.environ.get("YOUTUBE_API_KEY", "")
            if not key:
                from ..memory.vault import Vault as _Vault

                try:
                    _v = _Vault(config)
                    _mk = _v.unlock_env_or_prompt(args)
                    if _mk:
                        _v.start_session(_mk, "passphrase")
                        from ..memory.store import MemoryStore as _Store

                        _m = _Store(config, _mk)
                        found = _m.search("secret youtube_api_key", limit=1,
                                          kind="credential_ref")
                        key = found[0].body.strip() if found else ""
                        _m.close()
                        _v.lock()
                except Exception:
                    key = ""
            res = web.youtube_search(query, key, limit=args.limit)
        elif what in ("transcript", "video"):
            res = web.youtube_transcript(query)
            if args.json:
                pass
            else:
                res = {**res, "rendered": (f"{res['title']} ({res['language']})\n"
                                           f"{res['text'][:1200]}"
                                           + ("…" if res["chars"] > 1200 else ""))}
        elif what in ("rss", "news", "feed"):
            res = web.rss(query, limit=args.limit)
        elif what in ("pack", "list", "help", ""):
            rows = web.describe_pack()
            if args.json:
                ui.emit({"ok": True, "connectors": rows})
            else:
                print(web.help_text())
                print(f"\n{len(rows)} free connectors:")
                for r in rows:
                    print(f"  {r['name']:<20s} {r['label']:<26s} needs: {r['needs']}")
            return 0
        else:
            ui.err(f"unknown connector '{what}' - try: "
                   f"{', '.join(sorted(web.PACK))}")
            return 2
    except web.ConnectorError as exc:
        ui.err(str(exc))
        return 2
    if args.json:
        ui.emit(res)
    else:
        print(res.get("rendered") or res)
    return 0


def cmd_start(args, ui: UI) -> int:
    """One command: unlock if needed, then open the app.

    Three steps used to be three commands (`unlock`, then `serve`, then find the
    printed token). The token is not printed here - it goes into the address that
    the browser is opened with, which is the behaviour `brain serve --open`
    already had.
    """
    config, vault = open_vault(args)
    if not vault.is_unlocked():
        ui.err("the vault is still locked, so the app was not started")
        return 2
    memory = open_memory(config, vault)
    from ..api.gateway import serve

    # Anything that came due while Nila was closed is said *now*, before the app
    # is open, because after this the owner is looking at a window and not at the
    # terminal the icon started.
    try:
        _due_alerts(_talk_brain(config, vault, memory), spoken=False)
    except Exception:
        pass
    return serve(config, vault, memory, host=args.host, port=args.port,
                 token=args.token, open_browser=not args.no_browser,
                 show_token=bool(args.show_token))


def cmd_speak(args, ui: UI) -> int:
    """Say something, or say plainly why nothing could be said."""
    from ..util import speech

    if args.list_voices:
        voices = speech.sapi_voices()
        if not voices:
            ui.emit({"ok": True, "voices": [], "hint": speech.piper_install_help()})
        else:
            ui.emit({"ok": True, "voices": voices})
        return 0
    text = " ".join(args.text or [])
    if not text:
        ui.err("what should I say? e.g. `brain speak \"வணக்கம்\"`")
        return 2
    res = speech.speak_text(text, voice=args.voice or "", out_wav=args.wav)
    ui.emit(res)
    return 0 if res.get("spoken") else 1


def cmd_languages(args, ui: UI) -> int:
    """What Nila can read, what she answers in, and what that costs."""
    from ..agent import translate as tr
    from ..util import speech

    if args.text:
        info = tr.detect(" ".join(args.text))
        ui.emit({"ok": True, **info, "gloss": tr.gloss(" ".join(args.text),
                                                       info["language"])})
        return 0
    ui.emit({"ok": True, "speaks": list(tr.SPOKEN),
             "reads": [v for k, v in tr.SCRIPT_LANG.items()],
             "capability": tr.CAPABILITY,
             "voice": speech.engines()})
    return 0


def cmd_calendar(args, ui: UI) -> int:
    """Read-only calendar: show a day's events, or set which feed to read."""
    from ..util import ics

    config = Config.load(args.home)
    agent = dict(config.agent or {})
    if args.set:
        source = args.set.strip()
        agent["calendar_ics"] = source
        # A URL is the owner's explicit choice, but asking once more keeps the
        # rule "nothing leaves the machine silently" true for a secret address.
        if ics.is_url(source):
            agent["calendar_allow_network"] = True
            shown = ics.masked_url(source)
        else:
            agent["calendar_allow_network"] = False
            shown = source
        config.agent = agent
        config.save()
        ui.emit({"ok": True, "calendar": shown,
                 "note": "read-only; creating events needs OAuth (not built yet)"})
        return 0
    source = str(agent.get("calendar_ics") or "")
    if not source:
        ui.err("no calendar set - `brain calendar --set <path-or-ics-url>`")
        return 2
    got = ics.load(source, allow_network=bool(agent.get("calendar_allow_network")))
    if not got.get("ok"):
        ui.err(str(got.get("error")))
        return 2
    start, end, label = ics.range_from_text(args.when or "today")
    data = ics.between(got["text"], start, end)
    if args.json:
        ui.emit(data)
    else:
        print(ics.render(data, label))
    return 0


def cmd_remind(args, ui: UI) -> int:
    """Set, list, close or snooze a reminder - offline, in the encrypted store."""
    from ..agent.reminders import ReminderBook, ReminderError, next_after, parse_when

    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    book = ReminderBook(memory)
    if getattr(args, "due", False):
        # The honest answer to "did anything fire while I was away?": each one is
        # shown with how late it is, and closed, so it is not announced twice.
        rows = _due_alerts(_talk_brain(config, vault, memory),
                           spoken=bool(getattr(args, "say", False)))
        if args.json:
            ui.emit({"ok": True, "due": rows})
        elif not rows:
            print("Nothing is due.")
        memory.close()
        vault.lock()
        return 0
    action = (args.action or "list").lower()
    words = list(args.text or [])
    if action not in ("add", "list", "ls", "done", "snooze", "cancel"):
        words.insert(0, args.action)          # `brain remind in 10 minutes to ...`
        action = "add"
    phrase = " ".join(words)
    try:
        if action == "add" or (action == "list" and phrase):
            if not phrase:
                ui.err("what should I remind you about? e.g. `brain remind in 10 minutes "
                       "to drink water`")
                return 2
            when = parse_when(phrase)
            if when.repeat:
                when.next_ms = next_after(when.at_ms, when.repeat, when.weekday)
            rec = book.add(phrase, when, original=phrase)
            ui.emit({"ok": True, "id": rec.id, "title": rec.title,
                     "at_ms": when.at_ms, "repeat": when.repeat,
                     "line": book.render()})
            return 0
        if action in ("list", "ls"):
            ui.emit({"ok": True, "line": book.render(), "count": len(book.all())})
            return 0
        if action in ("done", "cancel", "snooze"):
            rec = book.get(str(args.id)) or book.by_number(args.id)
            if rec is None:
                ui.err(f"no reminder '{args.id}' - run `brain remind list`")
                return 2
            if action == "cancel":
                book.cancel(rec)
            elif action == "snooze":
                book.snooze(rec, float(args.minutes or 10))
            else:
                book.close(rec, done=True)
            ui.emit({"ok": True, "id": rec.id, "action": action, "line": book.render()})
            return 0
        ui.err(f"unknown action '{action}'")
        return 2
    except ReminderError as exc:
        ui.err(str(exc))
        return 2
    finally:
        memory.close()
        vault.lock()


def cmd_models(args, ui: UI) -> int:
    """The model pack: what is here, what to add, and which brain does which job."""
    from ..agent import models as pack

    config = Config.load(args.home)
    ollama = str((config.agent or {}).get("ollama_url") or "http://127.0.0.1:11434")
    machine = pack.installed(ollama_url=ollama, probe=not args.offline)
    if args.action == "use":
        if not args.model:
            return ui.err("name the model to use, e.g. `brain models use qwen2.5:3b`")
        if args.model not in pack.PACK:
            return ui.err(f"unknown model '{args.model}'. Known: "
                          f"{', '.join(sorted(pack.PACK))}")
        present = set(machine["present"])
        if args.model not in present:
            row = next((r for r in machine["models"] if r["id"] == args.model), {})
            return ui.err(f"'{args.model}' is not usable on this PC yet: "
                          f"{row.get('why_not') or 'not present'}")
        agent = dict(config.agent or {})
        spec = pack.PACK[args.model]
        if spec["kind"] == "local":
            agent["local_model"] = spec["tag"]
        else:
            agent["online_model"] = spec["tag"]
        config.agent = agent
        config.save()
        print(f"now using {args.model} for {', '.join(spec['jobs'])} jobs")
        return 0

    status = pack.pack_status(machine, ollama_url=ollama)
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return 0
    print(f"Ollama on this PC: {'running' if status['ollama_running'] else 'not running'}")
    print("\nPRESENT (usable now):")
    for row in status["present"]:
        print(f"  [ok]   {row['label']:<42s} jobs={','.join(row['jobs'])}"
              + ("" if row["free"] else "  (paid)"))
    if not status["present"]:
        print("  (only the built-in engine)")
    print("\nNOT HERE YET:")
    for row in status["missing"][:8]:
        print(f"  [--]   {row['label']:<42s} {row['why_not'][:70]}")
    print("\nWHICH BRAIN DOES WHAT:")
    for job, ch in status["plan"].items():
        if ch["ok"]:
            print(f"  {job:<6s} -> {ch['label']}  ({'free' if ch['free'] else 'paid'})")
        else:
            print(f"  {job:<6s} -> nothing present: {ch['reason']}")
    if status["advice"]:
        print("\nWHAT TO ADD FIRST:")
        for a in status["advice"][:4]:
            print(f"  {a['label']} ({a['size_gb']} GB, {a['load']}) - {a['for']}")
            print(f"     {a['install']}")
    print("\n" + status["note"])
    return 0


def cmd_profile(args, ui: UI) -> int:
    """The owner's own facts: what to call them, language, work - not orders."""
    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    try:
        from ..memory.store import InstructionError

        action = getattr(args, "profile_cmd", None) or "show"
        if action == "set":
            try:
                rec = memory.set_profile(args.field, args.value)
            except InstructionError as exc:
                vault.audit("profile_refused", {"reason": str(exc)[:120]})
                return ui.err(f"refused: {exc}")
            vault.audit("profile_set", {"field": args.field.lower(), "id": rec.id})
            payload = {"ok": True, "field": args.field.lower(), "value": rec.body,
                       "count": len(memory.profile())}
        elif action == "forget":
            n = memory.forget_profile(args.field or "")
            vault.audit("profile_forgotten", {"field": (args.field or "*").lower(),
                                              "removed": n})
            payload = {"ok": True, "removed": n}
        else:
            rows = memory.profile()
            payload = {"ok": True, "count": len(rows),
                       "profile": [{"field": k, "value": v} for k, v in sorted(rows.items())],
                       "limits": {"max_fields": memory.MAX_PROFILE_FIELDS,
                                  "max_chars": 200},
                       "sent_online": False}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            if "profile" in payload:
                if not payload["profile"]:
                    print("no profile facts yet. `brain profile set callme \"Anna\"`")
                for row in payload["profile"]:
                    print(f"  {row['field']:<12s} {row['value']}")
                print("\nThese stay on this PC and are never sent to a cloud model.")
            elif "value" in payload:
                print(f"{payload['field']} = {payload['value']}")
            else:
                print(f"removed {payload['removed']} profile fact(s)")
        return 0
    finally:
        memory.close()


def cmd_strict(args, ui: UI) -> int:
    """Strict mode: only the commands the owner spells out are allowed (B)."""
    from ..security.strict import StrictMode

    config, vault = open_vault(args)
    st = StrictMode(config, vault)
    action = getattr(args, "strict_cmd", None) or "show"
    if action == "on":
        st.enable()
    elif action == "off":
        st.disable()
    elif action == "allow":
        st.allow(args.tool)
    elif action == "deny":
        st.deny(args.tool)
    elif action == "auto":
        st.set_auto(args.tool, args.decision)
    state = st.state()
    if args.json:
        print(json.dumps(state, indent=2))
    else:
        print(f"strict mode: {'ON' if state['enabled'] else 'off'}")
        print(f"  allowed: {', '.join(state['allowed']) or '(default set)'}")
        print(f"  blocked: {', '.join(state['blocked']) or '(none)'}")
        print(f"  ask-me-instead: {', '.join(sorted(state['ask'])) or '(none)'}")
        print(f"  answered turns left: {state['turns_left']}")
        print("  rule: with strict mode on, a tool runs only if you named it in this "
              "message, or you answer Nila's question with yes.")
    return 0


def cmd_challenge(args, ui: UI) -> int:
    """Issue a voice challenge without unlocking (used with `brain unlock --voice`)."""
    config = Config.load(args.home)
    vault = Vault(config)
    if not vault.initialized:
        return ui.err("no vault - run `brain init`")
    if not vault.has_voice():
        return ui.err("no voice template enrolled yet - run `brain enroll-voice`. "
                      "(the check is ciphertext-only; nothing was decrypted)")
    from ..identity.ceremony import IdentityCeremony

    ch = IdentityCeremony(config, vault).issue_challenge(style=args.style)
    if args.json:
        print(json.dumps(ch, indent=2))
    else:
        ui.say(f'say: "{ch["text"]}"')
        ui.emit({"nonce": ch["nonce"], "bound": ch["bound"], "expires_at": ch["expires_at"]})
        ui.say("\nthen:  brain unlock --voice recording.wav --nonce "
               f"{ch['nonce']} --bound {ch['bound']}")
    return 0


# --------------------------------------------------------------------------- #
# permissions: the human side of the explicit-affirmation boundary
# --------------------------------------------------------------------------- #
def _broker(config):
    from ..security.permission import Broker

    return Broker(path=config.home / "permissions.json")


def cmd_permission(args, ui: UI) -> int:
    from ..security.permission import CAPABILITIES

    config = Config.load(args.home)
    broker = _broker(config)
    action = args.action

    # The second positional means different things per action: a prompt id for
    # `affirm`, a capability name for everything else. Accepting both spellings
    # keeps `brain permission affirm prm_xxx` working, which is the exact
    # command Nila prints when she refuses something.
    second = (args.capability or "").strip()
    prompt = (args.prompt or "").strip()
    if action == "affirm":
        if not prompt and second:
            prompt, args.capability = second, ""
    elif second.startswith("prm_"):
        prompt, args.capability = prompt or second, ""
    args.prompt = prompt

    if action == "panic":
        # The emergency switch. Unlike `revoke`, the latch also blocks NEW
        # grants until a human clears it - so a compromised session cannot
        # re-grant itself access after being revoked.
        if getattr(args, "clear", False):
            cleared = broker.clear_panic()
            ui.say("panic latch cleared - permissions work again." if cleared
                   else "no panic latch was set.")
            if cleared:
                ui.say("every previous grant stayed revoked; re-affirm what you need.")
            if args.json:
                print(json.dumps({"ok": True, "cleared": cleared,
                                  "grants": len(broker.grants())}))
            return 0
        latch = broker.panicked()
        result = broker.panic(getattr(args, "reason", "") or "")
        ui.say("\033[31mPANIC: all permissions revoked and latched off.\033[0m")
        ui.say("nothing host-facing works until `brain permission panic --clear`.")
        ui.say("new grants are refused too, including standing ones.")
        if latch:
            ui.say("(a latch was already set - revoking again)")
        if args.json:
            print(json.dumps({"ok": True, "latched": True, **result}))
        return 0

    if action == "capabilities":
        rows = [{"capability": k, **v} for k, v in sorted(CAPABILITIES.items())]
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            ui.say("capability     risk     durable?  what it allows")
            ui.say("─" * 78)
            for r in rows:
                ui.say(f"{r['capability']:<14s} {r['risk']:<8s} "
                       f"{'yes' if r['durable_ok'] else 'NEVER':<9s} {r['label']}")
                ui.say(f"{'':<32s}{r['detail']}")
        return 0

    if action == "list":
        broker.prune()
        rows = broker.ledger()
        if args.json:
            print(json.dumps(rows, indent=2))
        elif not rows:
            ui.say("no permissions recorded. Nila can read and search her own vault,")
            ui.say("but she cannot touch files, photos, the shell or the network")
            ui.say("until you affirm each capability.")
        else:
            ui.say(f"{'capability':<14s} {'kind':<9s} {'active':<7s} {'uses':<5s} scope")
            ui.say("─" * 78)
            for r in rows:
                mark = "\033[32myes\033[0m" if r["active"] else "\033[90mno\033[0m "
                ui.say(f"{r['capability']:<14s} {r['kind']:<9s} {mark:<16s} "
                       f"{r['uses']:<5d} {r['scope']}")
                if r["purpose"]:
                    ui.say(f"{'':<14s} purpose: {r['purpose']}")
        return 0

    if action == "grant":
        if not args.capability:
            return ui.err("usage: brain permission grant <capability> [--scope PATH] [--purpose TEXT]")
        if args.capability not in CAPABILITIES:
            return ui.err(f"unknown capability '{args.capability}'. "
                          f"Try: brain permission capabilities")
        d = broker.grant_durable(args.capability, args.scope or "*", args.purpose or "")
        if d.allowed:
            ui.ok(f"standing grant recorded: {args.capability} on {args.scope or '*'}")
            ui.warn("this expires in 7 days. Revoke any time with "
                    "`brain permission revoke`.")
        else:
            return ui.err(d.reason)
        return 0

    if action == "deny":
        if not args.capability:
            return ui.err("usage: brain permission deny <capability> [--scope PATH]")
        d = broker.deny(args.prompt or "", standing=args.standing,
                        capability=args.capability, scope=args.scope or "*")
        if args.standing:
            ui.ok(f"standing refusal recorded for {args.capability} on {args.scope or '*'}. "
                  f"Nila will stop asking.")
        return 0 if d.allowed is False else 1

    if action == "revoke":
        if not args.capability:
            return ui.err("usage: brain permission revoke <capability> [--scope PATH]")
        n = broker.revoke(args.capability, args.scope)
        ui.ok(f"revoked {n} grant(s) for {args.capability}"
              + (f" on {args.scope}" if args.scope else ""))
        return 0

    if action == "affirm":
        if not args.prompt:
            return ui.err("usage: brain permission affirm <prompt-id>")
        d = broker.affirm(args.prompt, granted_by="cli", remember=args.remember)
        if d.allowed:
            ui.ok(f"affirmed {d.capability} on {d.scope}"
                  + (" as a standing grant" if args.remember else " for one use only"))
            return 0
        return ui.err(d.reason)

    return ui.err(f"unknown permission action: {action}")


def cmd_serve(args, ui: UI) -> int:
    from ..api.gateway import serve

    config, vault = open_vault(args)
    memory = open_memory(config, vault)
    return serve(config, vault, memory, host=args.host, port=args.port, token=args.token,
                 open_browser=bool(getattr(args, "open", False)),
                 show_token=bool(getattr(args, "show_token", False)))


def cmd_keygen(args, ui: UI) -> int:
    from ..updates.manifest import generate_signing_key

    keys = generate_signing_key()
    if args.out:
        out = Path(args.out).expanduser()
        out.write_text(json.dumps(keys, indent=2))
        os.chmod(out, 0o600)
        ui.ok(f"signing key written to {out} (keep the private half secret)")
    ui.emit(keys)
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="brain",
                                description="Nila - a local-first, end-to-end encrypted "
                                            "personal AI agent")
    p.add_argument("--home", default=None, help="BRAIN_HOME override (default ~/.personal-brain)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--fast-kdf", action="store_true",
                   help="use a weaker scrypt cost (tests/benchmarks only!)")
    p.add_argument("--version", action="version",
                   version=f"personal-brain {__import__('brain').__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_auth(sp):
        sp.add_argument("--passphrase", default=None, help=argparse.SUPPRESS)
        sp.add_argument("--passphrase-env", default=None, metavar="VAR",
                        help="read the passphrase from this environment variable")
        sp.add_argument("--passphrase-stdin", action="store_true",
                        help="read the passphrase from stdin")
        existing = {o for a in sp._actions for o in a.option_strings}   # `unlock` has its own
        if "--totp" not in existing:
            sp.add_argument("--totp", default=None, metavar="CODE",
                            help="authenticator code (otherwise you are prompted)")
        if "--recovery" not in existing:
            sp.add_argument("--recovery", default=None, metavar="CODE",
                            help="one-time recovery code, used instead of the authenticator")

    sp = sub.add_parser("init", help="create the vault and enrol recovery codes")
    sp.add_argument("--user", default="owner")
    sp.add_argument("--label", default="", help="device label")
    sp.add_argument("--no-recovery", action="store_true")
    add_auth(sp)
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("status", help="show vault/config/memory status")
    sp.add_argument("--unlock", action="store_true", help="also report memory + voice details")
    add_auth(sp)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("unlock", help="run the authentication ceremony")
    sp.add_argument("--voice", metavar="WAV", help="path to the recorded challenge")
    sp.add_argument("--nonce", help="challenge nonce (from `/challenge` or `brain challenge`)")
    sp.add_argument("--bound", help="challenge binding HMAC")
    sp.add_argument("--record", action="store_true", help="record from the microphone")
    sp.add_argument("--seconds", type=float, default=4.0)
    sp.add_argument("--totp", help="6-digit TOTP code (degraded session)")
    sp.add_argument("--recovery", help="recovery code (break-glass)")
    sp.add_argument("--chat", action="store_true", help="stay in the REPL after unlocking")
    sp.add_argument("--step-up", action="store_true",
                    help="mark this session as re-authenticated, authorising ONE "
                         "high-risk action (e.g. /export in the REPL)")
    sp.add_argument("--verbose", action="store_true")
    add_auth(sp)
    sp.set_defaults(func=cmd_unlock)

    sp = sub.add_parser("lock", help="lock and zeroise the session")
    sp.set_defaults(func=cmd_lock)

    sp = sub.add_parser("enroll-voice", help="enrol the voice template")
    sp.add_argument("--file", nargs="+", help="WAV files to enrol from")
    sp.add_argument("--record", action="store_true", help="record from the microphone")
    sp.add_argument("--samples", type=int, default=3)
    sp.add_argument("--seconds", type=float, default=4.0)
    add_auth(sp)
    sp.set_defaults(func=cmd_enroll_voice)

    sp = sub.add_parser("voice-test", help="score sample files without unlocking a session")
    sp.add_argument("file", nargs="+")
    add_auth(sp)
    sp.set_defaults(func=cmd_voice_test)

    sp = sub.add_parser("recovery", help="show or reissue break-glass recovery codes")
    sp.add_argument("--show", action="store_true", help="only report how many remain")
    sp.add_argument("--count", type=int, default=8, help="how many codes to issue")
    add_auth(sp)
    sp.set_defaults(func=cmd_recovery)

    sp = sub.add_parser("change-passphrase", help="rotate the vault passphrase")
    sp.add_argument("--new-passphrase-stdin", action="store_true",
                    help="read the new passphrase from stdin (scripting)")
    add_auth(sp)
    sp.set_defaults(func=cmd_change_passphrase)

    sp = sub.add_parser("totp", help="enrol or show the TOTP second factor")
    sp.add_argument("--show", action="store_true")
    sp.add_argument("--replace", action="store_true",
                    help="replace an already-enrolled TOTP factor (interactive confirmation)")
    sp.add_argument("--account", default=None)
    add_auth(sp)
    sp.set_defaults(func=cmd_totp)

    sp = sub.add_parser("instruct", help="add, list, edit or remove standing instructions")
    sp.add_argument("action", choices=["add", "list", "edit", "remove"], nargs="?", default="list")
    sp.add_argument("text", nargs="*", help="the instruction text (for add/edit)")
    sp.add_argument("--id", default=None, help="instruction id (for edit/remove)")
    add_auth(sp)
    sp.set_defaults(func=cmd_instruct)

    sp = sub.add_parser("voice", help="the one enrolled voice: enrol, check, status")
    sp.add_argument("action", nargs="?", default="status",
                    choices=["enrol", "enroll", "check", "challenge", "status", "reset"])
    sp.add_argument("--file", default="", help="a WAV to use instead of the microphone")
    sp.add_argument("--seconds", type=float, default=3.0,
                    help="how long to record (default 3)")
    add_auth(sp)
    sp.set_defaults(func=cmd_voice)

    sp = sub.add_parser("file", help="read a document, code file, image or video")
    sp.add_argument("path", nargs="?", default=None)
    sp.add_argument("--limit", type=int, default=60000, help="max characters to read")
    sp.add_argument("--preview", type=int, default=1500, help="characters to print")
    sp.add_argument("--full", action="store_true", help="print the whole text")
    sp.set_defaults(func=cmd_file)

    sp = sub.add_parser("storage", help="where the archive lives (e.g. D:)")
    sp.add_argument("--set", default="", metavar="FOLDER",
                    help="use this folder, e.g. 'D:/Nila Assistant Storage'")
    sp.add_argument("--clear", action="store_true", help="go back to the default folder")
    sp.add_argument("--fallback-c", choices=["on", "off"], default=None,
                    help="allow falling back to C: when the drive is missing")
    sp.set_defaults(func=cmd_storage)

    sp = sub.add_parser("launcher", help="an icon / hotkey that starts Nila")
    sp.add_argument("--write", action="store_true", help="write the launcher files")
    sp.add_argument("--plan", action="store_true", help="show what is possible here")
    sp.add_argument("--out", default="", help="where to write them (default: Desktop)")
    sp.add_argument("--talk", action="store_true",
                    help="write the press-to-talk icon instead of the start icon")
    sp.add_argument("--seconds", type=float, default=8.0,
                    help="how long one press listens (default 8)")
    sp.set_defaults(func=cmd_launcher)

    sp = sub.add_parser("ears", help="hear you: speech to text (needs a transcriber)")
    sp.add_argument("--status", action="store_true", help="what is installed here")
    sp.add_argument("--listen", type=float, nargs="?", const=5.0, default=None,
                    help="record N seconds (default 5) and transcribe")
    sp.add_argument("--file", default="", help="transcribe a WAV instead of the mic")
    sp.add_argument("--lang", default="ta", help="ta or en (default ta)")
    sp.add_argument("--model", default="small", help="whisper size: tiny/base/small/medium")
    add_auth(sp)
    sp.set_defaults(func=cmd_ears)

    sp = sub.add_parser("talk", help="press once, say it, Nila does it (push-to-talk)")
    sp.add_argument("--seconds", type=float, default=6.0,
                    help="how long to listen, once (default 6)")
    sp.add_argument("--file", default="", help="use a WAV instead of the microphone")
    sp.add_argument("--text", default="", help="type the sentence instead (for testing)")
    sp.add_argument("--lang", default="ta", help="ta or en (default ta)")
    sp.add_argument("--model", default="small", help="whisper size")
    sp.add_argument("--no-speak", action="store_true", help="do not answer aloud")
    sp.add_argument("--speak-voice", default="", help="an installed SAPI/Piper voice name")
    sp.add_argument("--show", action="store_true", help="for 'open': say, do not run")
    sp.add_argument("--skip-voice-check", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--search-root", default="", metavar="DIR",
                    help="where 'find' looks (default: your home folder)")
    sp.add_argument("--content", action="store_true", help="for 'find': look inside files")
    sp.add_argument("--limit", type=int, default=20)
    add_auth(sp)
    sp.set_defaults(func=cmd_talk)

    sp = sub.add_parser("find", help="find a file by name or by words inside it")
    sp.add_argument("query", nargs="*", help="part of a name, or words to look for")
    sp.add_argument("--in", dest="in_", default="", metavar="DIR",
                    help="where to look (default: your home folder)")
    sp.add_argument("--content", action="store_true", help="look inside text files too")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--since", type=int, default=0, metavar="DAYS",
                    help="only files changed in the last N days")
    sp.set_defaults(func=cmd_find)

    sp = sub.add_parser("open", help="open an app from the fixed list")
    sp.add_argument("app", nargs="?", default="", help="chrome, notepad, calculator ...")
    sp.add_argument("--list", action="store_true", help="show every name I know")
    sp.add_argument("--show", action="store_true",
                    help="say what would run, without running it")
    sp.set_defaults(func=cmd_open)

    sp = sub.add_parser("web", help="free connectors: weather, wikipedia, youtube, rss")
    sp.add_argument("what", nargs="?", default="pack",
                    help="weather | wiki | youtube | transcript | rss | pack")
    sp.add_argument("text", nargs="*", help="the place, topic, query or feed address")
    sp.add_argument("--days", type=int, default=3, help="forecast days (weather)")
    sp.add_argument("--lang", default="en", help="wiki language: en or ta")
    sp.add_argument("--limit", type=int, default=3, help="how many results")
    sp.set_defaults(func=cmd_web)

    sp = sub.add_parser("start", help="one command: unlock, then open the app")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--token", default=None, help=argparse.SUPPRESS)
    sp.add_argument("--no-browser", action="store_true", help="do not open a browser")
    sp.add_argument("--show-token", action="store_true",
                    help="print the token (it is normally only in the opened address)")
    add_auth(sp)
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("speak", help="say something out loud (SAPI / Piper, offline)")
    sp.add_argument("text", nargs="*", help="the words to say")
    sp.add_argument("--voice", default="", help="a SAPI voice name")
    sp.add_argument("--wav", default=None, help="write a WAV here instead of speaking")
    sp.add_argument("--list-voices", action="store_true", help="show installed voices")
    sp.set_defaults(func=cmd_speak)

    sp = sub.add_parser("languages", help="what Nila reads, and the two she answers in")
    sp.add_argument("text", nargs="*", help="detect the language of this text instead")
    sp.set_defaults(func=cmd_languages)

    sp = sub.add_parser("calendar", help="read a .ics calendar (read-only, no login)")
    sp.add_argument("--set", default="", metavar="PATH_OR_URL",
                    help="use this .ics file or secret iCal address")
    sp.add_argument("--when", default="today",
                    help="today | tomorrow | week | a weekday | a date")
    sp.set_defaults(func=cmd_calendar)

    sp = sub.add_parser("remind", help="reminders: set, list, done, snooze, cancel (offline)")
    # Not `choices=[...]`: the natural typing is `brain remind in 10 minutes to
    # drink water`, and argparse would reject the sentence before this command
    # ever saw it. The action is recognised from the first word instead.
    sp.add_argument("action", nargs="?", default="list",
                    help="add | list | done | snooze | cancel (or just the sentence)")
    sp.add_argument("text", nargs="*", help="e.g. 'in 10 minutes to drink water'")
    sp.add_argument("--id", help="reminder id or its number in the list")
    sp.add_argument("--minutes", type=float, default=10, help="for snooze")
    sp.add_argument("--due", action="store_true",
                    help="show what came due while Nila was closed, then close it")
    sp.add_argument("--say", action="store_true", help="with --due: speak it aloud too")
    add_auth(sp)
    sp.set_defaults(func=cmd_remind)

    sp = sub.add_parser("models", help="the model pack: what is here, what each brain does")
    sp.add_argument("action", nargs="?", choices=["list", "use"], default="list")
    sp.add_argument("model", nargs="?", default=None, help="model id for `use`")
    sp.add_argument("--offline", action="store_true",
                    help="do not probe Ollama (report only what needs no check)")
    sp.set_defaults(func=cmd_models)

    sp = sub.add_parser("profile", help="facts about you: what to call you, language, work")
    sp.add_argument("profile_cmd", nargs="?",
                    choices=["show", "set", "forget"], default="show")
    sp.add_argument("field", nargs="?", default="",
                    help="field name, e.g. callme, name, language, work")
    sp.add_argument("value", nargs="?", default="", help="the value to store")
    add_auth(sp)
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("strict", help="strict mode: run only what you spelled out")
    sp.add_argument("strict_cmd", nargs="?",
                    choices=["show", "on", "off", "allow", "deny", "auto"],
                    default="show")
    sp.add_argument("tool", nargs="?", default="",
                    help="tool name for allow/deny/auto")
    sp.add_argument("--decision", default=None, choices=["ask", "allow", "block"],
                    help="for `strict auto <tool>`")
    add_auth(sp)
    sp.set_defaults(func=cmd_strict)

    sp = sub.add_parser("issue", help="turn a problem into a safe report you can read")
    sp.add_argument("problem", nargs="*", default=[],
                    help="what is going wrong, in your own words")
    sp.add_argument("--write", action="store_true",
                    help="save the report as a file (after you read the preview)")
    sp.add_argument("--yes", action="store_true",
                    help="confirm writing the file")
    sp.add_argument("--out", default=None, help="where to write it (default: <home>/reports)")
    add_auth(sp)
    sp.set_defaults(func=cmd_issue)

    sp = sub.add_parser("connectors", help="which AI services and outside services are on")
    sp.add_argument("action", nargs="?",
                    choices=["list", "on", "off", "key", "remove-key", "test", "approve"],
                    default="list")
    sp.add_argument("name", nargs="*", help="connector id, e.g. gemini")
    sp.add_argument("--key-env", default=None,
                    help="read the API key from this environment variable (for scripts)")
    add_auth(sp)
    sp.set_defaults(func=cmd_connectors)

    sp = sub.add_parser("session", help="show the idle rule, or say you are still here")
    sp.add_argument("--touch", action="store_true",
                    help="count this as an owner action and refresh the idle window")
    add_auth(sp)
    sp.set_defaults(func=cmd_session)

    sp = sub.add_parser("chat", help="interactive text REPL")
    sp.add_argument("-m", "--message", default=None, help="single turn, then exit")
    sp.add_argument("--verbose", action="store_true")
    add_auth(sp)
    sp.set_defaults(func=cmd_chat)

    sp = sub.add_parser("ask", help="one-shot question")
    sp.add_argument("text", nargs="+")
    sp.add_argument("--verbose", action="store_true")
    add_auth(sp)
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("remember", help="store a memory")
    sp.add_argument("text", nargs="*")
    sp.add_argument("--kind", default="note")
    sp.add_argument("--tag", default="")
    sp.add_argument("--title", default="")
    add_auth(sp)
    sp.set_defaults(func=cmd_remember)

    sp = sub.add_parser("memory", help="what is old enough to let go (report, or --purge)")
    sp.add_argument("--older-than", type=int, default=90, metavar="DAYS",
                    help="the window, in days (default 90 = about 3 months)")
    sp.add_argument("--purge", action="store_true", help="delete, don't just list")
    sp.add_argument("--yes", action="store_true", help="confirm the deletion")
    add_auth(sp)
    sp.set_defaults(func=cmd_memory)

    sp = sub.add_parser("recall", help="hybrid search over memory")
    sp.add_argument("query", nargs="+")
    sp.add_argument("--limit", type=int, default=5)
    sp.add_argument("--kind")
    sp.add_argument("--tag")
    add_auth(sp)
    sp.set_defaults(func=cmd_recall)

    sp = sub.add_parser("list", help="list memories")
    sp.add_argument("--kind")
    sp.add_argument("--tag")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--include-deleted", action="store_true")
    add_auth(sp)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("forget", help="delete a memory")
    sp.add_argument("id", nargs="?")
    sp.add_argument("--hard", action="store_true")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--yes", action="store_true")
    add_auth(sp)
    sp.set_defaults(func=cmd_forget)

    sp = sub.add_parser("sync", help="end-to-end encrypted cloud sync")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--status", action="store_true")
    add_auth(sp)
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("update", help="update channel operations")
    sp.add_argument("action", choices=["list", "check", "apply", "rollback"])
    sp.add_argument("--channel", default=None)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("update-channel-add", help="pin an update channel")
    sp.add_argument("name")
    sp.add_argument("url")
    sp.add_argument("--alg", default="ed25519")
    sp.add_argument("--public", default="")
    sp.add_argument("--key-id", default="")
    sp.add_argument("--mode", default="tofu", choices=["strict", "tofu", "insecure"])
    sp.add_argument("--auto-apply", action="store_true")
    sp.set_defaults(func=cmd_channel_add)

    sp = sub.add_parser("keygen", help="generate an update signing key")
    sp.add_argument("--out", default="")
    sp.set_defaults(func=cmd_keygen)

    sp = sub.add_parser("device", help="multi-device enrolment")
    sp.add_argument("action", choices=["list", "export", "import", "revoke", "rotate-key"])
    sp.add_argument("--out", default="brain-bundle.json")
    sp.add_argument("--bundle", default="")
    sp.add_argument("--label", default="")
    sp.add_argument("--device-id", default="")
    sp.add_argument("--force", action="store_true", help="replace an existing vault on import")
    add_auth(sp)
    sp.set_defaults(func=cmd_device)

    sp = sub.add_parser("backup", help="write an encrypted backup")
    sp.add_argument("out")
    sp.add_argument("--with-identity", action="store_true",
                    help="also export the enrolment bundle (needed to restore on a new device)")
    add_auth(sp)
    sp.set_defaults(func=cmd_backup)

    sp = sub.add_parser("restore", help="restore an encrypted backup")
    sp.add_argument("backup")
    sp.add_argument("--identity", default="")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--label", default="")
    add_auth(sp)
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser("config", help="read/write runtime config")
    sp.add_argument("action", choices=["show", "get", "set"])
    sp.add_argument("key", nargs="?")
    sp.add_argument("value", nargs="?")
    sp.set_defaults(func=cmd_config)

    sp = sub.add_parser("audit", help="tail the hash-chained audit journal")
    sp.add_argument("--limit", type=int, default=25)
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("doctor", help="self-test crypto, voice model, storage and sync")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("report", help="health report: what works, what is blocked, how to fix it")
    sp.add_argument("--json", action="store_true")
    add_auth(sp)
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("challenge", help="issue a voice liveness challenge")
    sp.add_argument("--style", default="digits", choices=["digits", "words"])
    sp.set_defaults(func=cmd_challenge)

    sp = sub.add_parser("permission",
                        help="manage what Nila may touch on this device")
    sp.add_argument("action", choices=["list", "grant", "revoke", "deny", "affirm",
                                       "capabilities", "panic"])
    sp.add_argument("capability", nargs="?", default="")
    sp.add_argument("--scope", default="", help="path, host or command the grant covers")
    sp.add_argument("--purpose", default="", help="shown in the prompt and the ledger")
    sp.add_argument("--prompt", default="", help="prompt id from a refused tool call")
    sp.add_argument("--remember", action="store_true",
                    help="turn a one-shot affirmation into a 7-day standing grant")
    sp.add_argument("--standing", action="store_true",
                    help="remember this refusal so Nila stops asking")
    sp.add_argument("--tool", default="",
                    help="name of the requester, shown in the prompt")
    sp.add_argument("--clear", action="store_true",
                    help="with `panic`: release the latch (grants stay revoked)")
    sp.add_argument("--reason", default="", help="with `panic`: recorded in the audit log")
    sp.set_defaults(func=cmd_permission)

    sp = sub.add_parser("serve", help="run the localhost JSON API (text + voice auth)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--token", default="")
    sp.add_argument("--open", action="store_true",
                    help="open the dashboard in your browser (token is not printed)")
    sp.add_argument("--show-token", action="store_true",
                    help="print the access token (avoid: it lands in terminal history)")
    add_auth(sp)
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ui = UI(quiet=getattr(args, "quiet", False), json_out=getattr(args, "json", False))
    try:
        return int(args.func(args, ui) or 0)
    except KeyboardInterrupt:
        print()
        return 130
    except SystemExit as exc:
        if exc.code and str(exc.code) != "0":
            return ui.err(str(exc.code))
        return 0
    except Exception as exc:  # never dump a traceback on the user
        import traceback

        if os.environ.get("BRAIN_DEBUG"):
            traceback.print_exc()
        return ui.err(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
