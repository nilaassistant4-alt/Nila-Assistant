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
| Voice / push-to-talk (v0.4) | not built (voice *enrolment* exists for unlock, not speech in/out) | phase 2 |
| Passkeys, phone PWA, wake word | not built | phases 4+ |
| Connector wave (Calendar/Drive/Notion/GitHub/Gmail) | not built — the catalogue ships **one working route shape** and CI-tested placeholders | phase 5 |
| Version 0.2.0 and `Nila-v0.2.0.zip` | **not done on purpose** — the owner names the archive | — |

## Known gaps carried forward (from the mandate probe)

Do-not-store (ephemeral) path, launch-only app control, device/account binding to one owner,
Gmail binding, Tamil liveness challenge, strictly per-command activation of plugins.
