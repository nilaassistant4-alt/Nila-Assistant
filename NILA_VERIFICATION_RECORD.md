# Verification record — Nila-verification-b8b68a06.zip

**Requested by:** owner (§10.3 — ZIP + SHA-256 on the same branch, verified before the next task)
**Branch:** `arena/3f2334a4-nila-assistant`
**Commit that contains the archive:** `d387738`
**Date:** 2026-11-04

## 1. The archive

| Field | Value |
|---|---|
| File | `Nila-verification-b8b68a06.zip` (repository root) |
| SHA-256 | `b8b68a060513f40db1cfcb3aa44c4a781c49b401d49bb0b217d589d99d2c6196` |
| Size | 454,015 bytes |
| Entries | 131 (87/87 source modules verified present, no build artifacts, no caches, no secrets) |
| Built by | `scripts/build_package.py` (its own integrity check reports "archive verified, no caches or secrets") |
| Determinism | four separate builds (two after a `git reset`) produced this exact hash |

This archive is **not** a release. `brain.__version__` is still `0.1.0`, and no
release-named archive exists. Per instruction, the name `Nila-v0.2.0.zip` is not
used until the owner asks for it.

## 2. Test results, reported separately

Both runs below were executed **inside the extracted archive**, not in the
development tree, so they describe what the owner actually receives.

| Runner | Interpreter | Result |
|---|---|---|
| `pytest` (with the `cryptography` extra installed) | CPython 3.11.2 | **664 passed, 3 skipped, 0 failed** |
| `scripts/run_tests.py` (stdlib fallback, no pytest needed) | CPython 3.11.2 | **621 passed, 0 failed, 46 skipped** |

The 46 fallback skips are fixture-based tests the fallback runner cannot build
(it does not emulate `monkeypatch`); they are reported as skipped, never as
passed. The 3 pytest skips need either a crypto extra that is absent in that
environment or a staged release archive.

### Python 3.12 and 3.14 — **not run, and why**

§10.2 asks for 3.12 and 3.14 results. **This sandbox has only CPython 3.11.2**
(`/usr/lib/python3.11` is the only interpreter present; `python3.12` is not
installable here — the package manager needs root, which this environment does
not grant). I will not report a number I did not produce, so:

- 3.11.2: measured, above.
- 3.12 / 3.14: **unverified.** If you want them, either run
  `python scripts/run_tests.py` inside the extracted archive on those
  interpreters, or give me a sandbox that has them.

The only 3.12+ specific risk I can see by inspection is `distutils`
deprecation, and the code does not import it.

## 3. Per-file fallback results (extracted archive)

```
test_audit_ledger_and_paths             pass=30  fail=0  skip=0
test_audit_redirect_credentials         pass=4   fail=0  skip=2
test_audit_server_containment           pass=9   fail=0  skip=0
test_audit_step_up_and_packaging        pass=11  fail=0  skip=1
test_audit_update_containment           pass=20  fail=0  skip=0
test_backlog_remediation                pass=31  fail=0  skip=1
test_crypto                             pass=15  fail=0  skip=0
test_dashboard                          pass=16  fail=0  skip=0
test_dashboard_packaging                pass=3   fail=0  skip=0
test_forbidden_case                     pass=19  fail=0  skip=0
test_lock_key_isolation                 pass=4   fail=0  skip=0
test_lockout_backoff                    pass=6   fail=0  skip=0
test_mandate_halt                       pass=21  fail=0  skip=0
test_memory                             pass=18  fail=0  skip=0
test_multilingual                       pass=25  fail=0  skip=0
test_online_isolation                   pass=22  fail=0  skip=0
test_online_isolation_acceptance        pass=0   fail=0  skip=24
test_package_integrity                  pass=56  fail=0  skip=2
test_permission_egress                  pass=127 fail=0  skip=0
test_scanner_false_positives_acceptance pass=25  fail=0  skip=0
test_scanner_personal_context_acceptance pass=31 fail=0  skip=0
test_sealed_vectors                     pass=50  fail=0  skip=0
test_session_expiry                     pass=7   fail=0  skip=6
test_sync                               pass=12  fail=0  skip=0
test_v01                                pass=33  fail=0  skip=10
test_voice                              pass=26  fail=0  skip=0
```

## 4. What is claimed, and what is not

**Claimed (measured in this sandbox):** the archive extracts, runs
(`brain.py --version`, `brain doctor` 4/4 PASS), and its test suite is green on
CPython 3.11.2.

**Not claimed:**

- Windows behaviour (`setup.bat`, `start-nila.bat`, tray, shortcuts).
- Real microphone, wake word, or speaker verification on real recordings.
- A real Gemini / Ollama / Anthropic account; no live third-party API was called.
- Remote sync or update channels against a real server (loopback fixtures only).
- Python 3.12 / 3.14.
- The phone, biometrics, tunnel, or OriginOS behaviour.

## 5. Code state in this archive

Completed and regression-tested (details in `NILA_AUDIT_REPORT_2026-11-04.md` §7
and `CHANGELOG.md`):

- **v0.1 audit findings F1–F11** — redirect credentials, path containment,
  update authenticity, publish-server auth, never-read list, usage ledger,
  step-up reachability, undeclared capability, wheel packaging, ZIP
  contamination, false test failures.
- **v0.2 backlog A2, A3, A4, A5, B6–B13** — degraded-session store escalation,
  audit-chain verification, WAL loss in backup, missing attachments, TOTP
  replay, malformed `Content-Length`, ledger corruption, the 200-project page,
  approval replay, TOFU key pinning, sync status/exit, wheel packaging.
- **Credential rotation** — `brain recovery --show/--count`,
  `brain change-passphrase`, and a `--replace` guard on `brain totp`.

Not started (by instruction): instruction memory (#2), connectors screen (#3),
mobile/PWA (#4), session 30/60 policy, version bump to 0.2.0.
