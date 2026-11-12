# #4 — session rule + everything the build said that was not true

**Archive:** `Nila-verification-2312bf34.zip`
**SHA-256:** `2312bf34da471c965a632c94f16d535dab75ce38ce8c14f20528d4a5bc7e98ea`
**Built by:** `scripts/build_package.py` → 134 entries, 90/90 source modules, 499,742 bytes

## 1. The session rule you asked for (30 minutes / 60 minutes)

| | |
|---|---|
| **Mode** | idle = no **owner** action. A message, a click, or a CLI action. |
| **30 minutes** | dashboard banner + "I am still here" button; the REPL prints the warning once |
| **60 minutes** | checkpoint (open project state flushed) **then** lock, keys dropped, reason audited (`session_idle_lock`) |
| **Auto-refresh** | **does not count.** The 2 s dashboard poll no longer refreshes anything |

Found and fixed: the poll *was* calling `touch_session()`, so a laptop left open never locked.

New: `brain session` (shows the rule), `brain session --touch`, `POST /v1/session/touch`.

Migration: `CONFIG_VERSION` is now 3. A config carrying the two retired *built-in*
defaults (900 s, 7200 s) becomes 3600 s; a value you set yourself is kept.

## 2. Things the build said that were not true (fixed)

| Claim | Reality | Now |
|---|---|---|
| `docs/CAPABILITIES.md`: `agent.auto_online true` sends Nila online by itself | **no code ever read that key** | removed; the doc says Auto is always local/offline |
| `.env.example`: "copy to `.env` and edit" | nothing loads a `.env` | the file now says a `.env` is never read |
| `.env.example`: `BRAIN_HOME` defaults to `./.personal-brain` | default is `~/.personal-brain`; the `.bat` files use `%LOCALAPPDATA%\Nila\vault` | corrected |
| `.env.example` key list | missed `GEMINI_API_KEY`, the one the router actually reads | added, with the vault route as the preferred one |
| Capability map: outside services = **BLOCKED**, "each must be enabled" | #3 made them switchable | reads the connectors screen and says which are on |
| `docs/ARCHITECTURE.md`: 20 tools / 25 subcommands | 28 / 32 | corrected |
| `docs/THREAT_MODEL.md`: 2 h idle | now 30 min warn / 60 min lock | corrected (T13, T16, the residual-risk table) |
| `START_HERE.md`: put the Gemini key in a Windows variable | the vault route is safer and now exists | `brain connectors key gemini` first, variable second |
| `README.md`: command list | stopped at `device` | adds `instruct`, `connectors`, `session` |
| Router: "no online key set (environment variable X)" | it also checked the vault (since the #3 fix) | names both, and how to add a key |

## 3. Verified end to end in this sandbox (not only unit tests)

- Fresh vault → `brain session` shows 30/60; `--touch` refreshes; the warning fires at the threshold.
- `brain connectors list` → five AI rows + outside services, keys as last-4 only.
- `brain connectors key gemini` (hidden/stdin) → stored sealed, `key_hint 3210`, `identity.json` contains no key.
- `brain connectors on gemini` → requires the fresh-login approval, then ON.
- `brain connectors test gemini` → reaches the real Gemini endpoint and fails at **TLS**
  (`SSL EOF`) because this sandbox has no outbound internet. **A real key on your PC is
  not verified by me** — that is exactly what `connectors test` is for.
- Dashboard: every route exercised over loopback HTTP (`/`, `/v1/status`, `/v1/connectors`,
  `/v1/files`, `/v1/chat`, `/v1/instructions`, `/v1/consent`, `/v1/audit`, `/v1/projects`,
  `/v1/memory`, `/v1/session/touch`); 401 without a token, 404 on an unknown route, 403 for
  a connector write without approval, 400 for an unsafe instruction.

## 4. Test numbers

| Runner | Result |
|---|---|
| `pytest` (CPython 3.11.2) | **739 passed, 3 skipped** |
| `scripts/run_tests.py` (stdlib fallback) | **690 passed, 0 failed, 52 skipped** |
| both, inside the extracted archive | identical |

Python 3.12 / 3.14 were **not** run: this sandbox has only 3.11.2.

## 5. What I did NOT do (so your testing does not look for it)

- No voice, image understanding, wake phrase, phone/PWA, mobile, video helpers, passkeys,
  business-model interview or weekly report. Those are roadmap phases, not regressions.
- No live provider call succeeded here (network) — see above.
- No browser was driven. The dashboard's JS is syntax-checked and every route is exercised,
  but "it looks right on screen" is your check, not mine.
- Version is still **0.1.0**; the bump to 0.2.0 is yours to call, and stays last.
