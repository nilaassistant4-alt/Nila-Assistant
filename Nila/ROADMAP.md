# Nila roadmap

Phases are in dependency order, not time estimates. A phase ships only when its tests, the
mandate probe (`scripts/nila_mandate_probe.py`) and the full suite pass.

| Phase | Contents | Status |
|---|---|---|
| 0 Foundation | Second factor enforced, token hygiene, shorter grants, hardened command tool, taint rule, tests, docs | **done in v0.1** |
| 1 MVP (PC) | Chat panel, router (Ollama + Gemini + offline), usage ledger, projects + encrypted files, capability map, diagnostics | **done in v0.1** (Windows run unverified) |
| 2 Voice, image, reports | Push-to-talk, local speech-to-text and text-to-speech (Tamil/English), image ingest with metadata stripping and local description, custom wake phrase on PC (opt-in), daily/weekly reports, approvals inbox, update suggestions with approval | planned |
| 3 Isolated development | ZIP intake, per-task workspace and path manifest, diff review, OS-level sandbox (WSL2) for running tests | planned |
| 4 Mobile + stronger auth | Phone web app (Android) over a private tunnel or encrypted sync; passkey / Windows Hello as optional factor | planned |
| 5 Business + media | Multi-agent runtime, cache, scorer and judge, best-of-N for flagged tasks, business model builder, trend/idea/marketing tools, ffmpeg video helpers, optional Claude provider | planned |
| 6 Hardening | Pinned dependencies, signed release pipeline with canary channel, external review | planned |

## Phase status for the owner's June review (what is *actually* built)

| Item | State | Where |
|---|---|---|
| Standing instructions (#2) | **built** | `brain instruct*`, dashboard panel, `instruction_*` audit events |
| AI services + keys (#3) | **built** — live Gemini call unverified (no network here) | `brain connectors *`, `POST /v1/connectors/*` |
| Session rule 30/60 | **built** | `brain session`, `POST /v1/session/touch`, `session_idle_lock` audit |
| Unlock again after a lock, in the browser | **built this pass** | `lockedOut()` → `POST /v1/auth/verify` |
| Lessons / "that was wrong" loop (v0.3) | not built | next phase |
| T0–T4 access tiers (v0.3) | not built — `session_tier()` exists, the 5 levels do not | next phase |
| Voice in and out, Tamil (v0.2 stage 11) | **built** — `brain talk` (press once), `brain ears`, `brain speak`; the engines need one install (`brain fix voice` / `brain fix hearing`) | `brain agent ears/speech` |
| Image ingest with metadata stripping | **built** | `brain file <image>` |
| Daily report | **built** | `report.bat`, `brain report` |
| Wake word ("Nila"), inside a window you open | **done** (stage 13) - `brain wake --window N`, 15-minute ceiling, audited | done |
| Passkeys, phone PWA | not built | phases 4+ |
| Connector wave (Calendar/Drive/Notion/GitHub/Gmail) | calendar is **written**; gmail, drive, notion and github ship as declared, disabled integrations with single-account binding. What is missing is the token exchange (Google OAuth / a PAT), not the plumbing | `brain connectors integrations` |
| Version 0.2.0 and `Nila-v0.2.0.zip` | **not done on purpose** — the owner names the archive | — |

## Known gaps carried forward (from the mandate probe)

**None remain.** As of stage 13 the probe is 29 pass, 0 fail, 0 gap: the do-not-store
(ephemeral) path, device/account binding to one owner, Gmail/Drive account binding, the
Tamil liveness challenge and strictly per-command activation were all closed, each with a
check that drives the code rather than reading it. Launch-only app control was already
built (`open_app`); what was missing was a probe that looked for it by the names the registry
actually uses, which is why it used to be counted as a gap in a build that had it.

Run `python scripts/nila_mandate_probe.py` to see the 29 checks and their evidence.
