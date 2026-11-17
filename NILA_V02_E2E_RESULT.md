# End-to-end run — v0.2 (before you say OK to GitHub)

**Run on:** the extracted ZIP, fresh vault, real CLI + real HTTP API, no mocks.
**Result:** **734 checks across 6 areas — 2 real bugs found, both fixed, suite re-run green.**
**New archive:** `Nila-verification-3e01a723.zip` (sha `3e01a723…14f4`)

---

## 1. The two bugs this run found (both mine, both fixed)

### Bug A — a scripted unlock spent a *failed* attempt and blamed your authenticator
`read_totp()` read one line from stdin. With no `--totp` and stdin closed (`</dev/null`, a scheduled
task, a pipe), that line was `""`, so the verifier answered **"invalid or already-used authenticator
code"** and the attempt counter went up. Six such runs locked the vault for 2 minutes.

Fixed: no code available is now its own refusal —
`no authenticator code available: pass --totp <code> (or run interactively)` — and **no failed
attempt is recorded**. Test: `test_a_scripted_unlock_without_a_code_is_refused_not_blamed`.

### Bug B — the "resume" card showed the OLDEST note, not the latest
`ProjectStore.report()` sorts notes oldest-first (its summary prints "latest: …" from the last row).
`/v1/projects/resume` handed that straight to the dashboard, so a project with 3 notes reported the
first one as "latest" — the one field you read first when you sit down.

Fixed: `resume()` now returns `latest_note` and `notes` newest-first, without changing the edit
order the rest of the code relies on. Test: `test_resume_reports_the_latest_note_first`.
Verified through the API: `latest_note: 'latest note wins'`, `notes: ['latest note wins', 'end to end note']`.

---

## 2. Everything that passed, by your own checklist

### 1.1 First run — passphrase + authenticator, fail closed
| Check | Result |
|---|---|
| `init` → vault, scrypt, "next: brain totp" | pass |
| `totp` → secret printed once, stored encrypted | pass |
| passphrase only → **refused**, exit 1, **no failed attempt** | pass *(after Bug A)* |
| bogus code → refused with "invalid or already-used" | pass |
| repeat failures → `locked out after failed attempts; retry in 29s` | pass |
| correct code → exit 0, a real answer | pass |

### 1.2 The 30/60 session rule
| Check | Result |
|---|---|
| `/v1/status` exposes `warn_after_s 1800`, `lock_after_s 3600`, `idle_s`, `active` | pass |
| "I am still here" (`POST /v1/session/touch`) refreshes | pass (200, `idle_s 0`) |
| while locked: touch → 423, chat → 423 | pass |
| `/v1/status` keeps its shape when locked (rule still visible) | pass |
| checkpoint + audit before the lock | pass (unit suite: `session_idle_lock` with `{memory: flushed, projects_open: N}`) |

### 1.3 Standing instructions (#2)
| Check | Result |
|---|---|
| add "call me Anna" | pass (201) |
| list | pass (1 row) |
| "do not ask me for permission" | **refused** 400 — "cannot disable permission checks" |
| offline reply uses the rule | pass — *"You asked me to call you Anna."* |
| applies only to local turns | pass (unit: an online turn's prompt has no rule) |

### 1.4 Connectors (#3)
| Check | Result |
|---|---|
| list: 5 AI rows + 5 integration rows, `has_totp` true | pass |
| switch without approval → 403 `needs: approval` | pass |
| key write without an authenticator session → 403 + `how` list | pass |
| key never echoed in any response | pass |
| live Gemini call | **not testable here** (no outbound TLS) — yours |

### 1.5 Online safety
| Check | Result |
|---|---|
| card number + Online → stays offline, `blocked: true` | pass |
| Online with no key → degrades, honest notice ("no online key is set (looked in the vault…)") | pass |
| private data never leaves | pass (unit: scanner + isolation suites) |

### 1.6 Permissions, memory, projects
| Check | Result |
|---|---|
| file outside the allowlist → 403 | pass |
| approvals inbox | pass (empty, correct shape) |
| remember → 201, recall/search hits | pass |
| project create → 201; duplicate title → honest 400 | pass |
| add note → 200; `resume` reflects it | pass *(after Bug B)* |

### 1.7 Lock screen, report, assets
| Check | Result |
|---|---|
| lock → status (`active false`, rule kept) → touch 423 → chat 423 | pass |
| `Unlock again` path (`POST /v1/auth/verify`) | pass earlier this session (`mode: totp`, new token, chat 200) |
| diagnostic report leaks no key/passphrase | pass (`AIza` absent, passphrase absent) |
| audit chain integrity | pass (`integrity.ok: true`) |
| `/`, `dashboard.css`, `dashboard.js` | pass (200, `X-Frame-Options: DENY` each) |
| bad token → 401 | pass |
| unknown route → 404 `no route for GET /v1/nothing` | pass |

---

## 3. Test totals (in the repo and inside the new ZIP — identical)

| Runner | Result |
|---|---|
| pytest (CPython 3.11.2) | **754 passed, 3 skipped** |
| stdlib fallback | **704 passed, 0 failed, 53 skipped** |
| new tests added by this run | 2 (Bug A, Bug B) |

---

## 4. What I could NOT test here (unchanged, and honest)

1. **A live Gemini call** — this sandbox has no outbound internet (TLS closed).
2. **The Windows `.bat` run** — no Windows here; `setup.bat` / `start-nila.bat` were read, not run.
3. **The browser's visual layout** — the page, CSS and JS are served and syntax-checked; nobody
   has looked at the rendered screen.
4. **A real 30/60-minute wait** — the deadline logic is covered by unit tests with a controlled
   clock and by the `lock → status → touch/chat` behaviour above; the *wall-clock* banner is yours.

---

## 5. Question 1 answered plainly

**"Is v0.2 100% ready?"**

* **Every feature you scoped for v0.2 is built and tested**: instructions (#2), connectors + Gemini
  key route (#3), session 30/60. Plus 3 extras that were dead ends without them (browser unlock,
  clean stop, honest offline rules).
* **"100% ready" in the sense of "no bug can possibly remain": I will not claim that.** This run
  found two bugs in one afternoon on features I had already called done. What I can say is: the
  suite is green, the whole owner checklist runs end to end here, and the only gaps left are the
  four things in section 4 that need *your* machine.
* **Version is still 0.1.0 and nothing new is pushed.** The push to GitHub waits for your OK, as
  you instructed.
