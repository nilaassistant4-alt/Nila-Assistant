# Audit part 1 + 5 "critical/high" items — verification and the plan

**Date:** 2026-11-04 · **Branch:** `arena/3f2334a4-nila-assistant` @ `1c27418` (clean)
**Nothing in the code was changed for this report.** Two read-only checks were run in
throwaway folders and those folders were deleted.

---

## 0. The one thing that decides what is still real

The report is precise, and every line number it gives matches **`Nila-v0.1.0.zip`**
(dated 2026-10-27, 116 files, `pyproject` version `0.1.0`). That ZIP is an old build.
Today's tree is **v0.2, `NILA_V02_STAGE14.zip`, 216 files**.

So the report is not wrong — it is **about a different, earlier snapshot**. I checked
each claim against the old ZIP (where it reproduces) *and* against today's tree (where
I re-ran it). Below, every item is marked:

- **STILL REAL** — reproduces in today's tree, needs fixing
- **FIXED SINCE THAT ZIP** — reproduces in `Nila-v0.1.0.zip`, already fixed in the tree

For the record, both of these are true at once:

| | `Nila-v0.1.0.zip` | today (`stage 14`) |
|---|---|---|
| update publish without a token | **open** (`if self.tokens and …`) | refuses, constant-time compare |
| sync-server path traversal | **`../updates-private` served** | refused (`open_within`) |
| dashboard battery | raises → HTTP 500 | **still raises → HTTP 500** |
| `pip install .` then `brain` | `No module named 'brain.cli'` | `packages.find` ships every subpackage |
| tests | 434 passed | 1459 passed |

---

## 1. Verification of the five items (with the evidence I ran)

### ① Update Server — publish without authentication — **FIXED SINCE THAT ZIP**

- *Old ZIP* `cloud/update_server/app.py:77`: `if self.tokens and header[7:]…` — an unset
  token meant **publish to anyone**. Confirmed by reading that exact line.
- *Today* `app.py:96`: the server **refuses to start** without `$BRAIN_UPDATE_TOKEN`
  unless `--open`; with `--open`, `POST /v1/publish` returns 403; token compare is
  `hmac.compare_digest`. Also `release_dir` must be inside `--publish-from`.
- **Residual (small, worth closing):** the *sync* server still compares its bearer token
  with `in self.tokens` (`cloud/sync_server/app.py:138`) — not constant-time. Real risk is
  low over a network, but it is a one-line fix.
- **Residual (design):** publishing **overwrites** the served directory and there is no
  rate limit / max body size on `POST /v1/publish`. An authenticated client can fill the
  disk. The *forgery* risk the report describes is gone (the client verifies the manifest
  signature against a pinned key), but a token holder can still cause damage.

### ② Sync Server — path traversal — **FIXED SINCE THAT ZIP**

I reproduced the old code's exact two lines against a scratch tree:

```
requested: /v1/../updates-private/secret.txt
resolved : /tmp/oldsync/srv/updates-private/secret.txt
served without a token? True        <-- unauth read outside the update dir
content  : PRIVATE customer list
```

Today, the same inputs through `brain/util/safe_paths.open_within`:

```
'../updates-private/secret.txt'      -> refused (contains '..')
'..%2Fupdates-private/secret.txt'    -> refused
'%2e%2e/updates-private/secret.txt'  -> refused
'/etc/passwd'                        -> refused (must be relative)
'manifest.json'                      -> served
symlink pw.txt -> /etc/passwd        -> refused (escapes root)
is_within('/srv/updates-private/x', '/srv/updates') -> False
```

The string-prefix flaw (`/srv/updates-private` passim as `/srv/updates`) is exactly what
`is_within`/`open_within` now prevent. **Nothing to fix here.** What I *will* add is a
regression test that pins these five strings, so a future edit cannot quietly undo it.

### ③ API token leakage through redirects — **STILL REAL**

`brain/agent/integrations.py:303-310` builds an `urllib.request.Request(url, …)` with
`Authorization: Bearer …` and calls `urlopen`. `urllib` follows 301/302/307 and **re-sends
the `Authorization` header to the new host**. No `HTTPRedirectHandler` override exists.

- Attack needs a redirect from an allow-listed host (a compromised Notion/GitHub/DNS path),
  so the likelihood is low; the consequence is a leaked token.
- Second, smaller item found while reading: a token-like value in a template body is passed
  through unescaped (`_render_json`), and `quote()` is only used on path params.
- **Fix:** a redirect handler that refuses a redirect to a different host/scheme and
  re-attaches credentials only for the original origin. ~25 lines + 3 tests.

### ④ `pip install .` — CLI does not work — **FIXED SINCE THAT ZIP**

Old `pyproject.toml`: `packages = ["brain"]` ships only the top level. Today:
`[tool.setuptools.packages.find] include = ["brain", "brain.*"]`. I verified by installing
today's tree into a throwaway target and importing `brain.cli.main` **and**
`brain.agent.tools` from it — both import.

**But the report points at a real weakness I should still close:** that fix has **no test**.
`tests/test_package_integrity.py` never builds/installs the wheel, so the exact regression
can return silently. Fix: a test that builds the wheel (`python -m build`/`pip install
--target`) and asserts every `brain.*` subpackage is inside it + both console scripts
resolve. (Add `nila`/`brain` script smoke test too.)

### ⑤ Dashboard 500 when there is no battery — **STILL REAL (and the most user-visible)**

Reproduced by faking `psutil` on a machine with no battery — which is most desktops, many
Windows VMs, WSL:

```
metrics():  RAISES -> NotImplementedError: no battery on this machine
OSError        : raises -> OSError
AttributeError : raises -> AttributeError
RuntimeError   : raises -> RuntimeError
```

`brain/api/dashboard.py:169-177` catches **only `ImportError`**. So on your own Windows 11
desktop (no battery) or a VM, `/api/metrics` is a 500 and the dashboard's top strip
dies. This is the highest-value fix of the five for daily use.
**Fix:** catch `Exception` around the psutil block, keep `cpu_pct`/`mem_pct`, drop only
battery; plus a test that fakes the four exceptions above.

---

## 2. The audit's BUG-1 … BUG-6 — what is still real today

| # | Claim | Verdict today | Notes |
|---|---|---|---|
| BUG-1 | `9**9**9**9` hangs the agent | **STILL REAL** | filter `[0-9+\-*/%(). eE]` accepts it; `eval` at `tools.py:464`; measured: child killed by CPU limit, never returned. **Worse than reported:** the charset allows `e`, so `9e999999999` → an integer with ~10⁹ digits; converting it to text kills the process. Fix: AST walker, cap exponent **and** result bit-length, `time.monotonic()` budget. |
| BUG-2 | shell filter is POSIX, Windows uses `cmd.exe` | **STILL REAL** | `_SHELL_META = & \| ; < > ` $( \n` — measured: `echo %USERPROFILE%` and `echo hi !PATH!` pass the filter. `shell=True` (`host_tools.py:422`) means `%VAR%`/`!VAR!` expand inside `cmd.exe`. Mitigations stand (per-command approval, scrubbed env), and I found **no escape** — the blocked `&` is needed for the usual tricks. Fix: `shell=False` + a POSIX/Windows-aware splitter, or an allow-list. |
| BUG-3 | recovery code on the command line | **STILL REAL** | `start-nila.bat:31`: `brain.py dashboard --recovery %RC%` → argv, visible in Task Manager / process listings for the life of the process. Fix: `--recovery-stdin` (read one line), a one-line change in the .bat, and the code never touches argv. |
| BUG-4 | fallback runner misreports with pytest installed | **FIXED (but unproven)** | measured today: `scripts/run_tests.py` → `227 passed, 0 failed, 745 skipped (real pytest present)`. It now says "real pytest is present" instead of failing. The old root cause (a hand-rolled skip shim) is gone, but nothing tests the fallback, so it can rot again. Fix: a test that runs the runner in a subprocess and asserts `0 failed`. |
| BUG-5 | README says the wrong names | **STILL REAL (smaller)** | measured: `README.md:29-30` still says `unzip personal-brain-mvp.zip` / `cd personal-brain`. The test counts it mentions are gone in today's README (so that half is stale-report). Fix: 2 lines + keep the counts out of prose (they rot). |
| BUG-6 | `.gitignore` incomplete | **STILL REAL** | measured: `git check-ignore` says **none** of `memory.db`, `workspace/`, `.personal-brain/`, `audit.log`, `permissions.json` are ignored. Real risk: the default `BRAIN_HOME` is `~/.personal-brain`, but a run with `BRAIN_HOME` set *inside* the folder (or `workspace/`, which Nila writes by default) is committable. Fix: add them — and `build/`, `*.egg-info/` (I just created both by installing; good proof). |

---

## 3. Two more things I found while verifying (not in either report)

- **`Nila/.gitignore` and the root `.gitignore` have drifted apart** (97 vs 81 bytes).
  Whichever one applies depends on where the repo root is; a rule in the wrong file is a
  rule that does not apply. Fix: one generated file, or a comment pointing at the other.
- **`cloud/sync_server` compares tokens with `in`** (noted in ①) — same class of bug as the
  update server had, one line away from being forgotten.

---

## 4. What I propose, in order, and how "done" is proven

The order below is the report's order (security first), with the two cheap items pulled
forward because they are smaller than the test I would have to write for them anyway.

| Order | Item | Change | Proof (test that must pass) |
|---|---|---|---|
| 1 | ⑤ dashboard 500 | catch all psutil failures; battery optional | fake `psutil.sensors_battery` raising `NotImplementedError` / `OSError` / `AttributeError` → `/api/metrics` 200 |
| 2 | BUG-1 calculator hang | AST evaluator: no `**` chain, exponent ≤ 100, result capped at 1000 digits, 200 ms budget | `9**9**9**9`, `9e999999999`, `(10**100)**100` → refused in words; normal maths unchanged |
| 3 | BUG-6 `.gitignore` | add vault/workspace/build entries to both files, with a comment | `git check-ignore` on 7 paths |
| 4 | BUG-3 recovery argv | `--recovery-stdin`, .bat pipes one line | code never appears in `sys.argv`; bat hygiene |
| 5 | BUG-2 shell on Windows | `shell=False` + `shlex.split(..., posix=(os.name != "nt"))`, metachar filter kept | `%USERPROFILE%` no longer expands; a plain command still runs |
| 6 | ③ redirect token leak | same-origin redirect policy | a local server that 302s to another port sees **no** `Authorization` |
| 7 | ① sync token compare | `hmac.compare_digest`, matching the update server | timing is not asserted; the code path is |
| 8 | ② regression pin | 5 traversal strings into the existing audit test | the five strings stay refused |
| 9 | ④ packaging guard | install into a throwaway target inside a test; assert subpackages + scripts | the v0.1.0 failure returns → test fails |
| 10 | BUG-4 fallback guard | run `scripts/run_tests.py` in a subprocess | `0 failed` |
| 11 | BUG-5 README names | 2 lines | `tests/test_bat_hygiene.py` style name check |
| 12 | re-run everything | suite + retest + probe, rebuild ZIP, commit, push | 1459 → higher, 0 failed; new ZIP sha256 |

Estimated: items 1–4 are the cheap ones, 5–6 are the careful ones. Nothing touches the
vault format or the passphrase work from stage 14, so an owner mid-setup is not disturbed.

**Two things I will not do without you saying so:**
1. I will not touch `Nila-v0.1.0.zip` or its history — it is a released artifact (and the
   audit's reference). Fixes land in the tree and in a new stage ZIP only.
2. For item 5 (`shell=False`) there is a real behaviour choice: `eval`-style splitting makes
   `dir /w` fine but breaks a quoted Windows path containing `&`. I would rather offer it as
   `brain config shell.mode strict|split` and default to **strict**, than silently change how
   every approved command runs.

---

## 5. Not decidable from here (same list as the audit's, plus mine)

- Nothing Windows-specific can be *proven* on this Linux sandbox: the battery fix is proven
  by faking psutil (that is honest — the code path is identical), but DPAPI, Credential
  Locker, `cmd.exe` semantics and `Startup\…\Nila.vbs` still need one run on your laptop.
- No live URL: correct, Nila is local (`127.0.0.1`). A phone cannot reach it — that is the
  design limit the report describes, not a bug. The existing answer is
  `brain telegram` + `nila-phone.bat`; anything else needs TLS + a tunnel + a decided auth.
- GitHub vs ZIP: `gh` access works here, so if you want, I can diff the released ZIP against
  the branch and say exactly which of the two reports' findings shipped in which artifact.
