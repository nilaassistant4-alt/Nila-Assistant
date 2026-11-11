# Nila / Personal Brain — Evidence-Based Audit Report

> **STATUS — REMEDIATED 2026-11-04.** Every "Verified finding" below (F1–F11) was
> fixed in the source tree on branch `arena/3f2334a4-nila-assistant`, with a
> regression test per finding and a re-run of the original probe showing it is now
> blocked. Full suite: **633 passed, 2 skipped** (the two skips need a built ZIP;
> with a ZIP staged they pass too). The fixes are recorded in `CHANGELOG.md` under
> "Unreleased — audit remediation" and in `docs/THREAT_MODEL.md` (T24/T25/T30
> rewritten, T45–T47 added). This section is the audit as it was found; §6 is what
> changed.

**Date:** 2026-11-04
**Target:** `nilaassistant4-alt/Nila-Assistant`, branch `arena/3f2334a4-nila-assistant` (base `76d8e48`)
**Audit type:** read-only source review + controlled local dynamic tests with synthetic fixtures
**Change footprint at audit time:** no Nila source file was modified; this report was the only new file. Temporary servers, vaults, ZIPs, wheels and fixtures were deleted after each test. The remediation that followed is described in §6.

---

## 0. How to read this report

Every finding below carries an evidence tag:

| Tag | Meaning |
|---|---|
| **[REPRO]** | Reproduced in this sandbox with a synthetic fixture. Exact command behaviour is described. |
| **[SRC]** | Established by reading the source (file:line given), not executed. |
| **[USER]** | A defect the operator already confirmed in earlier hand-testing (from `NILA_V02_PLAN_DRAFT_TA.md`), not re-tested by me. |
| **[N/A]** | Could not be tested here (no Windows, no device, no provider account, no production service). |

Environment: Linux x86_64, Python 3.11.2, 2 vCPU, no `adb`/`gradle`/`ollama`/browser. The shipped tree in git is **one file** (`Nila-v0.1.0.zip`); all code reviewed lives in the untracked `Nila/` directory of this branch. Test counts below come from a throwaway venv with real `pytest` 9.2.0.

---

## 1. Verified findings

### F1 — HIGH — Update install executes package code, and `extract_to` is not contained

Two independent defects that combine into remote code execution when a channel is configured.

**1a. Arbitrary write outside the install root [REPRO]**

`brain/updates/installer.py:109` builds the destination as `target / (art.extract_to or art.kind)` with no validation, and `:113` then does `dest_root.relative_to(target)`. A manifest artifact declaring

```json
"extract_to": "../../../../outside"
```

caused `apply()` to return `{"ok": true}` and write `<tmp>/outside/marker3.txt` — i.e. **four levels above `$BRAIN_HOME/updates/installed/<version>/`**. With `"extract_to": "../../escaped-from-updates"` the file landed directly under `$BRAIN_HOME`. An **absolute** `extract_to` (`/tmp/...`) instead raises `ValueError` at `installer.py:113` **after** files have already been unpacked, leaving a half-installed tree with no cleanup.

The ZIP-entry defence (`installer.py:143-157`) is not the problem: entries are checked for `..`, absolute paths and symlink bits. The *destination directory itself* is the unchecked value, and it comes straight from the manifest.

**1b. The "import test" runs untrusted code at install time [REPRO]**

`installer.py:159-187` performs `importlib.import_module(marker)` / `importlib.reload(mod)` on the freshly unpacked package before the `current` pointer is swapped. A synthetic package whose module body wrote a file produced:

```
apply_ok=True  notes=['unpacked pkg.zip -> runtime', 'import test ok: auditpkg.probe from runtime']
module_code_ran_during_install=True
```

So installing an update **is** code execution as the user — by design, but it means the only thing standing between a hostile/compromised feed and the machine is manifest authenticity, which F1c shows is weak in `tofu` mode.

**1c. `tofu` mode accepts unsigned manifests indefinitely and pins nothing [REPRO]**

`manager.py:127-147`: `verify_signature()` returns `ok` for an unsigned manifest, `sig["pin"]` is empty, so the pinning branch is skipped and `channel.key_id` stays `""`. Result:

```
first  unsigned check: ok=True,  tofu_pinned_now=False
second (different) unsigned check: ok=True, tofu_pinned_now=False
```

The channel never becomes pinned, so every later check is equally unauthenticated. A user who follows the printed advice (`update-channel-add ... --mode tofu`) has configured a channel that trusts whatever it fetches, forever.

**Recommended fix:** whitelist `extract_to` to a charset and require it to stay inside the version directory via `Path.resolve().is_relative_to(target)`; refuse absolute/traversal values at *parse* time (`manifest.py`), not mid-install; make the import test opt-in for unsigned content, or require a pinned key before any package artifact is installed; make `tofu` fail closed on an unsigned manifest (or rename it `insecure` and say so).

---

### F2 — MEDIUM — String-prefix path containment is bypassable in three places [REPRO]

All three check `str(candidate).startswith(str(root))`, which a sibling directory with a matching prefix satisfies.

| Location | Probe | Result |
|---|---|---|
| `cloud/update_server/app.py:62-64` | root `releases/`, symlink `releases/link.txt -> releases-private/secret.txt` | `GET /v1/link.txt` → **200**, fixture bytes returned |
| `cloud/sync_server/app.py:158-160` | same shape with `Handler.update_dir` | `GET /v1/link.txt` → **200**, fixture bytes returned |
| `brain/updates/installer.py:152-154` | same shape for ZIP entries | same defect class [SRC] |

`..` in the URL is rejected (`400`), so the plain traversal attempt is handled; the symlink case is not, because `resolve()` follows the link *after* the prefix test and the returned path `/…/releases-private/secret.txt` starts with `/…/releases`.

**Recommended fix:** use `Path.is_relative_to()` (or `os.path.commonpath`) on resolved paths, and open with `O_NOFOLLOW`/`Path.lstat()` check so a symlink is refused rather than followed. Same helper in all three places.

---

### F3 — MEDIUM — Update publish endpoint is unauthenticated by default and copies any directory named in the request [SRC + earlier REPRO]

`cloud/update_server/app.py:77`: `if self.tokens and header[7:].strip() not in self.tokens` — when `Handler.tokens` is empty (its default, and what `main()` sets when `BRAIN_UPDATE_TOKEN` is unset, `:113`), **POST is allowed with no credential**. `:84` then takes `release_dir` from the JSON body and rglobs it into the public release root (`:88-93`), skipping only a file literally named `keys.json`.

Earlier in this audit a POST with an empty token set published a temporary fixture successfully. Today's contribution is the source confirmation that this is the *default*, not a misconfiguration: `--host` defaults to `127.0.0.1` (good), but the server prints no warning that publishing is open, and `--help` describes only GET as public.

**Recommended fix:** refuse to start without a token unless `--open` (mirror the sync server's behaviour, `cloud/sync_server/app.py:224-229`), require the token for POST unconditionally, and restrict `release_dir` to a configured releases directory instead of accepting an arbitrary path.

---

### F4 — MEDIUM — Redirects carry credentials to hosts that were never allow-listed [REPRO, dummy tokens only]

Policy is checked once, against the declared URL (`brain/agent/integrations.py:227` host allow-list, `:310` `urlopen`). `urllib` follows 3xx by default, so a 302 from an allow-listed host is followed to an arbitrary host with the same `Authorization` header. Reproduced with dummy values in three paths:

1. integration call from an allow-listed integration → different host received `Bearer <dummy>`;
2. OpenAI-compatible provider 302 → followed as GET with `Authorization: Bearer <dummy>`;
3. sync `HttpBlobStore.get` → forwarded `Bearer <dummy>` after redirect.

No real key was used and no external host was contacted. `brain/agent/providers.py:186-190` already documents the intent ("the key must never follow a redirected `base_url`"), so the gap is implementation, not design.

**Recommended fix:** a shared opener/`RedirectHandler` that re-runs `_check_policy` per hop and refuses cross-host redirects for any request carrying a secret header; better, set `redirect=False` and handle redirects explicitly.

---

### F5 — MEDIUM — The never-read list does not cover common credential filenames [REPRO]

The threat model says credential material is refused whatever the grant (`docs/THREAT_MODEL.md` §11, T30). With an *allowed* broker decision, `brain/agent/host_tools.py` returned:

| Path | Read? |
|---|---|
| `.ssh/config` | **refused** (directory) |
| `id_rsa.pub`, `id_ed25519_copy.key` | **refused** |
| `vault.kdbx` | **refused** |
| `holiday-photo.txt -> vault.kdbx` (symlink) | **refused** ("after resolving a symlink") — the post-resolve recheck works |
| `.env` | **readable** |
| `api_keys.json` | **readable** |
| `secrets.json` | **readable** |
| `service.pem` | **readable** |
| `credentials.yml` | **readable** |
| `notes.key` | **readable** |

The suffix list (`host_tools.py:83-86`) contains `.pfx/.p12/.kdbx/...` but not `.pem` or `.key`; the name list (`:72-81`) contains `credentials` but not `.env`/`api_keys.json`. Directory-scoped rules (`.ssh/`, `/.config/gh/`) work. **Suffix/name matching is fundamentally incomplete** — a secret copied to `plans.txt` is still readable — but `.env` and `*.pem` are the two obvious omissions for a tool that can already read shell-adjacent material.

**Recommended fix:** add `.env*`, `*.pem`, `*.key`-with-credential-shape, `api_keys.json`, `secrets.json`, `.npmrc`-style names to the list; add a `.gitignore`-style user config for extra denies; keep the current correct behaviour (refusal *before* the broker, recheck after resolution).

---

### F6 — MEDIUM — Usage caps reset when the process restarts [REPRO]

`brain/agent/ledger.py` keeps the rolling/minutely window in memory (`:33` `_recent`) and persists only daily aggregates (`:92-111`). A controlled test where the first `UsageLedger` instance denied request six after five succeeded — with a **fresh instance on the same ledger file** the next request was permitted immediately, despite the persisted daily count. Daily totals survive; the per-window budget does not.

**Recommended fix:** derive the window from persisted timestamps (or persist the deque), and treat a ledger that fails to parse as "cap reached", not "zero usage".

---

### F7 — MEDIUM (functional) — `export_backup` is unreachable through the normal flow [REPRO]

`registry.call("export_backup")` refused with `ToolError` because `session.step_up_ok` was false; source review finds no setter or call site that ever sets it (`grep step_up` → `agent/tools.py:111`, `security/classify.py:182-183`, `identity/ceremony.py:252`). The gate is honest (it fails closed) but the user-facing feature is dead.

**Recommended fix:** wire `Ceremony.step_up()` to set the flag for the current session, or gate `export_backup` on a fresh passphrase prompt inside the handler.

---

### F8 — LOW/MEDIUM — Two tools skip the central capability gate [SRC]

`run_sync` and `check_updates` are registered with no `ToolSpec.capability`, so the central broker check at `agent/tools.py:111` has nothing to enforce; they rely on whatever the handler does. Network-capable operations should carry `net.host` (or an explicit `sync.remote`) capability so a future caller cannot invoke them ungated. Integration calls, by contrast, *do* enforce `net.host` at the gateway (subject to F4).

---

### F9 — MEDIUM (packaging) — The wheel is unusable; `packages = ["brain"]` ships only the top level [REPRO]

`pyproject.toml:37` lists only `brain`. Built wheel (`personal_brain-0.1.0-py3-none-any.whl`) contains **4** Python modules (`brain/__init__.py`, `__main__.py`, `config.py`, `diagnostics.py`) and **no subpackages**. In a clean venv, from a neutral cwd:

```
brain            = import_ok
brain.config     = ModuleNotFoundError: No module named 'brain.util'
brain.cli.main   = ModuleNotFoundError: No module named 'brain.cli'
nila --help      = exit 1, ModuleNotFoundError: No module named 'brain.cli'
brain --help     = exit 1, same
```

The console scripts (`nila`, `brain`) therefore cannot run from an installation. The declared workflow is ZIP-based, so this is not a release blocker today — but `pip install personal-brain` is a broken promise, and `test_package_integrity.py` does not catch it because it tests the ZIP.

**Recommended fix:** `[tool.setuptools.packages.find] include = ["brain*"]` (or list all 11 subpackages), plus a wheel smoke test in CI.

---

### F10 — LOW — The distributable ZIP silently absorbs leftover build artifacts [REPRO]

`scripts/build_package.py` on a clean tree produces **123 entries / 407,579 bytes / sha256 e10528b7b7f829829a7bec20d22a2e714b4b3a8cd8fde18e9d9459b4ec41b74a** (reproduced identically twice). With `build/` and `personal_brain.egg-info/` present from a prior wheel build, the same command produces **137 entries / 447,433 bytes**, including `Nila/build/lib/brain/...` and six `egg-info` files. Nothing rejects them: the integrity test only refuses `__pycache__`, `.pyc` and secret-shaped files.

**Recommended fix:** exclude `build/`, `*.egg-info/`, `dist/` in the builder, and add them to the archive-integrity assertion.

---

### F11 — LOW — Documentation and test-runner drift [REPRO]

- `README.md:64-65` advertises "295 tests" and "160 tests"; the suite is now **554 passed, 2 skipped**.
- `scripts/run_tests.py` reports `55 passed, 2 failed, 253 skipped` with real pytest present, and the two failures are `AttributeError: 'NoneType' object has no attribute 'seek'` from `test_package_integrity.py:114,131` — a **missing ZIP is supposed to skip, not crash**. It also swallows pytest's summary line (`-q` applied twice via `pyproject` `addopts`).
- The fallback runner is scripted to defer to pytest, so this is cosmetic, but a red run on a fresh clone is a bad first impression.

**Recommended fix:** read counts from one place, or state "run pytest for the count"; guard the ZIP tests with an existence check that calls `pytest.skip` *before* opening the file.

---

### F12 — INFO — User-confirmed defects not independently re-tested here [USER]

From `NILA_V02_PLAN_DRAFT_TA.md` §1, already confirmed by the operator: online-egress leakage of `recall` results under `allow_cloud_context=false`; stale key after API re-login; audit-hash verifier accepting mutated entries; live-WAL records missing from backup; attachment bytes missing from backup; TOTP replay in one window; negative `Content-Length` hang; corrupted-ledger cap bypass; project list silently truncating at 200; one-shot integration approval replay; sync status/exit-code mismatch. My review is consistent with each (e.g. ledger handling in F6, redirect policy in F4), but I did not re-run those specific repros and do not claim to have.

---

## 2. What held up under test

These are positive results, not assumptions:

1. **Default posture is closed.** With no runtime config at `~/.personal-brain`, the registry builds **28 tools, all requiring an unlocked vault**; exactly one (`export_backup`) declares step-up. The four example integrations are **disabled**; no integration is connected.
2. **Local API boundary.** Unauthenticated `/v1/status` → 401; untrusted `Host` → 403; valid bearer → 200; oversized body → 413; the thirteenth empty-passphrase verify attempt → 429. Sustained load: 300 authenticated GETs / 25 workers → 236.6 req/s, p50 9.3 ms (p95 1.0 s — long tail still unexplained).
3. **Memory store.** 250 synthetic records inserted at 391/s, DB 1.2 MB; an injection-shaped FTS query returned ordinary results and left the table at 250; 50 hybrid searches p50 45 ms / p95 52 ms. WAL, FTS5, per-record encryption and sealed vectors are real in source.
4. **Host tools.** Shell rejects `& | ; < > \` $(` and newlines, scrubs credential-shaped env vars, runs in a workspace dir with a 20 s timeout; there is **no write/delete tool**. Never-read refusals for `.ssh`, `id_rsa`, `.kdbx` fire *before* the broker and again after symlink resolution.
5. **Update staging.** Hash and size are verified before unpacking; ZIP entries with `..`, absolute paths or symlink bits are refused; a failed verification deletes the staging dir; `current`/`previous` are symlink-swapped, and rollback exists.
6. **Sync server.** Refuses to start with an empty token env unless `--open`, and warns loudly when bound off-loopback.
7. **Voice and crypto are honestly labelled.** `voice_model.json` says `"measured on SYNTHETIC speakers only - not a real-world EER claim"`; the backend is classic MFCC, and the threat model lists anti-spoof/ASR challenge as open (T9). No claim of a production biometric.
8. **Tests are real.** In an isolated venv with pytest: **554 passed, 2 skipped in 65.19 s**; the two skips require a distributable ZIP. With a ZIP staged, `tests/test_package_integrity.py` passes fully.

---

## 3. Could not be verified here [N/A]

No Windows or macOS runtime; no microphone or real speaker; no biometric hardware; **no Android project exists in the repository** (no `AndroidManifest.xml`, Gradle files, `.kt`/`.java`), so all Android feasibility statements remain design guidance, not test results; no real Gemini/Anthropic/OpenAI/Ollama account or network call; no TLS-terminating reverse proxy or public exposure; no production sync/update deployment; no real credentials (only `GITHUB_TOKEN` presence was observed; its value was neither read nor used); no long-run soak or memory-leak test; no independent crypto/penetration review. Findings about default token emptiness, redirects and path containment were proven **locally with synthetic data only** — whether a real deployment is exposed depends on deployment choices I cannot see.

---

## 4. Recommended order of work

**Before Gemini/online keys are added (gate):**
1. Fix credential-following redirects in integrations, providers and sync (F4) — one shared policy-checking opener.
2. Close the contextual-egress boundary and re-test with a fake provider (F12, user-confirmed).
3. Keep integrations **off** and update channels **off** until F1/F2/F3 have tests that fail on the old code.

**Before any release that includes updates:**
4. Validate `extract_to`; contain installs with `is_relative_to`; make import-test execution conditional on a pinned publisher key (F1a/b).
5. Make `tofu` reject unsigned manifests, or drop the mode (F1c); ship a pinned key in the package.
6. Apply the same containment helper to the update server, sync server and installer (F2); require a publish token and a fixed release directory (F3).

**Hardening batch:**
7. Extend the never-read list for `.env`, `*.pem`, `api_keys.json`, `secrets.json`, `*.key` (F5).
8. Persist rate windows and fail closed on corrupt ledgers (F6).
9. Declare capabilities on `run_sync` / `check_updates` (F8); wire the step-up flow so backup export works (F7).
10. Fix `packages` and add a wheel smoke test (F9); exclude `build/`/`egg-info` from the ZIP (F10); correct README counts and make missing-ZIP tests skip (F11).

**Housekeeping:** README/CHANGELOG counts; `Nila/` is untracked in git (the branch's HEAD holds only `Nila-v0.1.0.zip`), so "current source" is only reproducible from this workspace ZIP, not from the repository history — worth fixing before more reviews are based on it.

---

## 5. Bottom line

The security architecture is genuinely more disciplined than the average local-first prototype: capabilities are centralised, the default registry is closed, host tools are read-only, the API checks Host and auth, and the docs are unusually candid about residual risk. The failures cluster where **trust boundaries meet I/O**: redirects that keep their `Authorization` header, path checks done by string prefix, a manifest field that picks the extraction directory, and an update mode that accepts unsigned input forever. None of these requires an exotic attacker — each is a small, testable fix — but together they mean the update and integration paths should stay switched off until they are fixed, and that "install an update" should be treated as "execute code" in the threat model, because that is what it is.

---

## 6. Remediation (2026-11-04, same branch)

Every finding was fixed **after** its probe had been reproduced, and each fix ships
with a test that fails on the old code. Original probes were re-run at the end.

### 6.1 What changed, per finding

| # | Fix | Where | Test |
|---|---|---|---|
| **F1a** | `extract_to` validated as a relative path at **parse** time and re-proved inside the version directory at install time; an `installed/<version>` symlink is refused | `updates/manifest.py`, `updates/installer.py` | `test_audit_update_containment.py` |
| **F1b** | The import test (which executes package code) runs **only** when the signature verified against a pinned key; otherwise skipped and reported | `updates/manager.py::_authenticated`, `installer._import_test` | same |
| **F1c** | Unsigned manifests refused in `tofu` (explicit `insecure` remains the escape hatch); a `key_id`-only HMAC pin with no shared secret no longer verifies | `updates/manifest.py::verify_signature` | same |
| **F2** | One boundary-safe containment module; used by the update server, sync server and installer | new `brain/util/safe_paths.py` | `test_audit_server_containment.py` |
| **F3** | Update server refuses to start without a token (`--open` = read-only, POST disabled), constant-time token check, `release_dir` confined to `--publish-from` | `cloud/update_server/app.py` | same |
| **F4** | One HTTP client that **refuses** redirects on any credentialed request; credential-free redirects are re-checked against policy | new `brain/util/http.py`; `integrations.py`, `providers.py`, `cloud/protocol.py` | `test_audit_redirect_credentials.py` |
| **F5** | `.env`, `.env.*`, `api_keys.json`, `secrets.*`, `credentials.*`, `service-account.json`, `terraform.tfstate`, `.pgpass`, `*.pem`, `*.key`, `*.der`, `*.pkcs12` refused; templates stay readable | `agent/host_tools.py` | `test_audit_ledger_and_paths.py` |
| **F6** | Per-minute window persisted; corrupt ledger fails **closed** | `agent/ledger.py` | same |
| **F7** | `Session.step_up_ok` now has a setter (`Ceremony.step_up`, `brain unlock --step-up`) and is consumed by one high-risk call | `memory/vault.py`, `identity/ceremony.py`, `agent/tools.py`, `cli/main.py` | `test_audit_step_up_and_packaging.py` |
| **F8** | `run_sync` and `check_updates` declare `net.host` | `agent/tools.py` | same |
| **F9** | `packages.find` over `brain.*` — wheel now carries 52 modules; `nila`/`brain` exit 0 from a clean venv | `pyproject.toml` | same |
| **F10** | `build/`, `dist/`, `*.egg-info` excluded from the ZIP and asserted against | `scripts/build_package.py` | same |
| **F11** | Fallback runner honours `pytest.skip`; ZIP tests skip before opening a missing file; README counts corrected | `scripts/run_tests.py`, `tests/test_package_integrity.py`, `README.md` | suite output |

**New/changed behaviour worth knowing as a user**

- `brain unlock --step-up` authorises exactly one high-risk action (e.g. `/export`).
- An update channel in `tofu` mode no longer accepts an unsigned manifest. If you
  serve your own unsigned feed locally, use `--mode insecure` and know what that means.
- The update server needs `BRAIN_UPDATE_TOKEN` set (or `--open` for read-only serving).
- `.env`, `*.pem`, `*.key` files are no longer readable by the agent; `.env.example`
  still is.

### 6.2 Verification after the fixes

| Check | Result |
|---|---|
| Full suite (real pytest) | **633 passed, 2 skipped** in 72 s |
| Same suite in a clean room, extracted from the delivered ZIP | **633 passed, 2 skipped** |
| Fallback runner (`scripts/run_tests.py`), was 2 false failures | **64 passed, 0 failed** |
| Original probes F1a/F1b/F1c/F5/F6 re-run | all **BLOCKED** |
| Original probes F2 (both servers) / F3 re-run | **403 / 401 / 403** — no fixture served, nothing published |
| Wheel in a clean venv | 52 modules; `nila --help` and `brain --help` exit **0** |
| ZIP built twice from the same tree | byte-identical (`ad6eefbf…`) |
| ZIP contents | 130 entries, 86/86 source modules, 0 build artifacts, 0 caches |

### 6.3 Still open — not fixed deliberately

- **`tofu` still trusts the first connection.** Fixing that needs a publisher key
  shipped in the package; it is a release-process decision, not a code edit.
- **`insecure` mode still installs anything it is given.** It is now loud and
  refuses to execute the import test, but it remains an escape hatch by design.
- **Never-read list is still a list.** A secret copied to `plans.txt` is readable;
  suffix matching is a mitigation, not a boundary.
- **No post-apply health check / automatic rollback** (T25).
- ~~The 13 user-confirmed backlog groups in `NILA_V02_PLAN_DRAFT_TA.md` were out of
  scope.~~ **They were completed in the second batch — see §7.**
- All device/production checks from §3 remain unverified.

---

## 7. Second batch — the v0.2 backlog (completed 2026-11-04)

The 13 confirmed groups in `NILA_V02_PLAN_DRAFT_TA.md` §1 were worked one at a time,
each reproduced against the shipped code *before* being changed, then re-probed with
the same script, then locked in by `tests/test_backlog_remediation.py` (29 tests).

| # | Reproduction (before) | Result (after) |
|---|---|---|
| **A1** online egress boundary | Already remediated earlier; re-probed with four fake-provider variants (tool result appended then re-sent, mid-turn fallback, legacy `needs_network=True` on the local lane). No private marker reached a network request in any variant. | 46 isolation tests green; no change needed in this batch. |
| **A2** stale key after re-login | `MemoryStore(..., access_level="full")` on a **degraded** session returned the restricted row (`tier filter would withhold: 1`). | Store derives the level from the session: `full` becomes `degraded`; expired sessions are refused. **Fixed.** |
| **A3** audit-hash mutation | Editing an entry's `data`, deleting an entry, and reordering all returned `{'ok': True}`. | Chain now covers the whole entry; verifier recomputes every line. All three (plus truncation) → `ok: False`. Pre-fix journals → `legacy_unprotected`, not accused. **Fixed.** |
| **A4** live WAL loss | 5 committed records, `memory.db` 4,096 B, restore → **0 records**. | SQLite online `conn.backup()`; restore → **5/5**, block 102,400 B. **Fixed.** |
| **A5** attachments missing | Backup header had no attachment entry at all. | Format 2 + `attachments` + `attachments_sha256`; restored bytes identical. **Fixed.** |
| **B6** TOTP replay | Same code accepted twice inside one window. | Counter recorded; reuse refused. **Fixed.** |
| **B7** malformed `Content-Length` | `-1` pinned the handler thread. | `-1`→400, `abc`→400, `999999999`→413, normal→200, all ≤0.01 s. **Fixed.** |
| **B8** ledger corruption | `{"days": []}` → cap bypassed; `"requests": "lots"` → `ValueError` escaping `usage()`. | Structural corruption check, fails **closed**; four corrupt shapes all refused. **Fixed.** |
| **B9** project 200 cap | 205 projects → list stopped at 200, oldest "no project matches". | `SCAN_LIMIT = 2000`, `listing()` reports `truncated`; oldest reachable by name. **Fixed.** |
| **B10** approval replay | One affirmed `net.host` grant → **two** server hits, `uses` stayed 0. | `Broker.consume()` + `spend_unused()` → one hit; replay refused. Redirect half re-probed: credentialed 302 refused, secret never sent. **Fixed.** |
| **B11** update authenticity | Unsigned refused (already), but a *signed first use* pinned a key_id and then failed every later check — and TOFU pinned a symmetric key straight from the attacker-supplied manifest. | TOFU refuses to pin a symmetric key; with the key supplied out of band, valid manifests verify, forged/unsigned/wrong-`key_id` are refused. **Fixed.** |
| **B12** sync status/exit | `run_sync` imported `brain.cloud.sync` (does not exist) → `ModuleNotFoundError` **on every call**; `sync_status` had the same import; `sync.backend=""` crashed; status claimed remote with a local backend. | Both tools fail soft and report; `backend`/`remote`/`warnings` truthful; CLI exits **1** on errors. **Fixed.** |
| **B13** wheel packaging | Already fixed earlier; re-verified in a fresh venv from the built wheel (`brain --version`, imports, `doctor` all pass). | No change needed. |

### 7.1 Extra work from the backlog's "credential rotation" section

- `brain recovery --show` / `--count N` — show remaining codes, or reissue a set.
  Every previously issued code is invalidated (`reissue_recovery_codes`).
- `brain change-passphrase` — `Vault.rotate_passphrase()` had **no caller at all**, so
  the one secret protecting everything could not be changed. Verified: the old
  passphrase stops working, the new one opens, memories survive (the master key does
  not change).
- A **non-interactive** `brain totp` used to replace an enrolled factor silently. It now
  refuses and requires `--replace`, which only saves after a code from your app is
  confirmed.

### 7.2 Bugs found while doing the above (fixed too)

- `sync_status` carried the **same** dead import as `run_sync` — the first fix only
  covered half the bug.
- Both sync tools let an unexpected exception escape and kill the turn; they now
  return `{"ok": false, "error": ...}`.
- The audit verifier reported chain mismatches as `unparsable`, which is a different
  thing; they are now counted separately (`mismatched` vs `unparsable`).
- `brain audit` exited `0` even when the chain failed verification; it now exits `3`
  in `--json` mode so a script can gate on it.
- `doctor` and `brain audit` now surface `legacy_unprotected` instead of silently
  calling an unverifiable journal "verified".

### 7.3 Verification after the second batch

| Check | Result |
|---|---|
| Full suite (real pytest, with the crypto extra) | **664 passed, 3 skipped** |
| Fallback runner `scripts/run_tests.py` (no pytest) | **621 passed, 0 failed**, 46 skipped (fixture-based) |
| New regression file | `tests/test_backlog_remediation.py` — **32 passed** |
| Wheel in a clean venv, from the built wheel | `brain --version`, `import brain.cli.main`, `brain doctor` → all pass |
| End-to-end CLI harnesses | backup→restore 5/5; attachment round-trip; TOTP replay; lock/rotation; project 205; sync exit code |

### 7.4 Still open (honest limits)

- ~~`ed25519` TOFU still trusts the first key~~ — inherent to TOFU; the fingerprint is
  printed so you can verify it out of band.
- **Integrations remain OFF by default** until the owner enables a named integration;
  the replay and redirect holes are closed, but no live third-party API was tested.
- **Remote sync / update channels were not exercised against a real server.** All
  probes used loopback fixtures.
- Windows, real microphones, real Ollama/Gemini, and the packaging `setup.bat` path
  remain untested here (§3).
