# OpenJarvis — முழு ஆய்வு, நம்ம Nila-வுக்கு என்ன எடுக்கலாம்

**ஆய்வு செய்தது:** `github.com/open-jarvis/OpenJarvis` (Stanford Hazy Research + Scaling Intelligence Lab).
**Commit:** `5a9b526` (2026-11-21). **License: Apache-2.0** (நம்ம code-ல் use பண்ணலாம், "எங்கிருந்து எடுத்தோம்" என்று ஒரு வரி எழுதினால் போதும்).
**எப்படி ஆய்வு செய்தேன்:** repo-வை clone செய்து (149 MB), README + 85 doc files + 691 source files + 729 test files + CI workflows + manifests + actual code-ஐ படித்தேன். அவங்க code-ஐ நான் ஓட்டவில்லை (அவங்க install-க்கு `uv` + Rust toolchain + 56 optional dependency sets வேண்டும்) — ஆனால் **ஒரு உண்மையான பாதுகாப்பு பிழையை** அவங்க code-ஐ படித்ததில் கண்டுபிடித்து, **நம்ம Nila-விலும் அதே பிழை இருப்பதை ஓட்டிச் சோதித்து**, இன்றே சரி செய்தேன் (§7). அதுவே இந்த ஆய்வின் மிகப் பெரிய பலன்.

---

## 0. ஒரு வரியில்

OpenJarvis = **"ஒரு laptop-ல் முழு AI agent stack"** — 8 agent வகைகள், 66 tool, 31 messaging channel, 27 data connector, 5 memory backend, scheduler, background "operator" agents, skills, MCP, A2A, 3-அடுக்கு பாதுகாப்பு, telemetry + traces + learning loop. **ஆனால்** அதன் memory **encrypt ஆகவில்லை** (plain SQLite), install-க்கு Python+Rust+uv வேண்டும், default-ஆ 500+ package. அதாவது **அவங்க breadth-ல் மிகப் பெரியவங்க; நம்ம encryption + Windows one-click + Tamil + voice-ல் மிகப் பெரியவங்க.**

**அளவு ஒப்பீடு:**

| | OpenJarvis | Nila v0.2 |
|---|---|---|
| Python source | 691 files / 1,80,848 lines | 101 files / 38,270 lines |
| Tests | 729 files / 1,47,777 lines / **7,990 test functions** | 60 files / 16,531 lines / 1,598 tests |
| Rust core | 17 crates / 27,709 lines | இல்லை |
| UI | React+TypeScript 105 files + Tauri desktop `.exe` | dashboard (Python, install-as-app) + 19 `.bat` |
| Docs | 85 markdown pages + mkdocs site | START_HERE + 10 docs + 20 stage notes |
| Tools | 66 | 39 |
| Channels (phone/messaging) | 31 | 1 (Telegram, two-way, working) |
| Install | `uv` + Rust + MSVC + 56 dependency sets | ZIP → double-click `.bat` |
| Memory at rest | **plaintext SQLite** | **AES + passphrase + TOTP + voice + hash-chained audit** |

---

## 1. அவங்க மையக் கட்டமைப்பு ("5 primitives") — இதைப் புரிந்தால் மீதம் எல்லாம் எளிது

ஒரு **registry pattern**: ஒவ்வொரு பகுதியும் ஒரு abstract base class + `@XxxRegistry.register("name")` decorator. புதிய engine/tool/channel சேர்க்க ஒரே file மட்டும் திருத்தினால் போதும் — எங்கும் factory இல்லை, YAML இல்லை. இது தான் அவங்க code-ஐ இவ்வளவு பெரியதாக வளர்த்தது.

| Primitive | அது என்ன | அவங்க வகைகள் |
|---|---|---|
| **Intelligence** | model catalog (params, context, VRAM, quant) | builtin model list + runtime-ல் கண்டுபிடித்தது merge |
| **Engine** | inference runtime | Ollama, vLLM, SGLang, llama.cpp, MLX, LM Studio, Cloud (OpenAI/Anthropic/Google) |
| **Agents** | வேலை செய்பவர் | simple, orchestrator (multi-turn tool loop), ReAct, CodeAct, RLM, OpenHands, ClaudeCode, operative, monitor-operative = **9** |
| **Memory** | தேடக்கூடிய நினைவு | SQLite/FTS5 (default), FAISS, ColBERTv2, BM25, **Hybrid RRF** = 5 |
| **Learning** | trace-களைப் பார்த்து routing மேம்படுத்து | heuristic, trace-driven, GRPO + DSPy/GEPA/**ACE** optimizers |

**நமக்குத் தேவையான பாடம்:** நம்ம `ToolRegistry` (brain/agent/tools.py) ஏற்கனவே இதே மாதிரி `@registry.tool(...)` decorator வடிவத்தில் இருக்கு — நல்லது, அதை விட வேண்டியதில்லை. ஆனால் அவங்க **models/engines/memory backends**-க்கும் அதே pattern use பண்றாங்க; நாமும் memory backend-ஐ (SQLite FTS5 / vector / hybrid) ஒரு registry-ஆக மாற்றினால், பிறகு "hybrid search" சேர்ப்பது 1 file வேலை.

---

## 2. அவங்க கொடுத்திருக்கும் features — முழு பட்டியல் (எது நமக்கு உதவும் என்று குறிப்புடன்)

### 2.1 Agents (9 + autonomous operators)
- **orchestrator**: multi-turn tool calling, function-calling + structured modes.
- **ReAct / CodeAct / RLM**: Thought-Action-Observation; code எழுதி ஓட்டுவது; recursive LM with persistent REPL.
- **SandboxedAgent**: ஒரு agent-ஐ Docker/Podman-ல் mount-policy உடன் ஓட்டுவது.
- **Operative / MonitorOperative**: **நிரந்தரமாக ஓடும் agent** — tick, state, memory compression, resume.
- **ProactiveAgent**: 5am cron-ல் ஓடி, "இதை நான் செய்யப் போகிறேன்" என்று **approval queue**-ல் போடும் — `trivial / low / medium / high` tier, "always yes / always no" நினைவு வைக்கும், `/yes all` reply-ஐ parse செய்யும்.
- **LoopGuard**: ஒரே tool-call-ஐ மூன்று முறைக்கு மேல் அடித்தால் தடு; A-B-A-B ping-pong கண்டுபிடி; polling tool-க்கு budget; context overflow-க்கு 4-stage compression.
- **Budget**: ஒரு agent-க்கு `max_cost` / token budget; முடிந்தால் `budget_exceeded`.

**நமக்கு:** ✅ **LoopGuard + Proactive approval queue** — இவை இரண்டும் நம்ம tool loop-ல் (`MAX_TOOL_ROUNDS`) இல்லாத உண்மையான பாதுகாப்பு. ✅ **Operative (scheduled) agent** — நம்ம reminders-க்கு அப்பால் "தானாக ஓடும் வேலை".

### 2.2 Tools (66)
calculator, think (சிந்தனை scratchpad), code_interpreter (+ docker), repl, web_search, weather, **fx_macro_data**, file_read/write (**allowed_dirs** restriction), **apply_patch**, http_request (**SSRF protection**), memory_store/retrieve/search/index, llm (sub-model), **channel_send/list/status**, **schedule_task/list/pause/resume/cancel**, **mcp_adapter**, **kg_add_entity / kg_add_relation / kg_query** (knowledge graph), **browser** (Playwright: click/type/extract), pdf, image, audio, text_to_speech, git, db_query, docker_shell_exec, scan_chunks.

- ஒவ்வொரு tool-க்கும் `ToolSpec`: `cost_estimate`, `latency_estimate`, `requires_confirmation`, `category`.
- `available_tools()` + `get_openai_tools()` → **same tools OpenAI function-calling format**-ல்.
- **`build_tool_descriptions()`** — tool விவரங்களை system prompt-க்கு Markdown-ஆக உருவாக்கும் *ஒரே* இடம் (ReAct/CodeAct/RLM எல்லாம் இதைத் தான் use பண்ணும்). இது நம்மில் இல்லை — prompt-ல் tool list-ஐ கை நிரப்பத்தேவை இல்லை.

**நமக்கு:** ✅ `think` (சிந்தனைக்கு ஒரு இடம் — model-ஐ குழப்பாமல்), ✅ **tool description builder** (ஒரே இடம்), ✅ **allowed_dirs** (file tool-க்கு folder-வரம்பு), ✅ **apply_patch** (code வேலைக்கு), ✅ **knowledge graph** (நம்ம memory-ல் "யார்/எது/எப்படி சம்பந்தம்" — நம்ம `kind/tags`-ஐ விட வலுவானது).

### 2.3 Memory (5 backends + pipeline)
- **SQLite/FTS5** (zero dependency), **FAISS** (dense), **ColBERTv2** (late interaction), **BM25**, **Hybrid RRF** (sparse+dense fusion, k=60, weights).
- **Chunking**: 512 token chunk, 64 overlap, paragraph boundaries, min 50.
- **Ingest**: file/dir → type detect (markdown/pdf/code/text), skip binary/hidden, PDF via pdfplumber.
- **Context injection**: top_k, min_score, max_context_tokens **budget**; ஒவ்வொரு துண்டுக்கும் `[Source: docs/api.md]` **citation**.
- **Source attribution** = "எங்கிருந்து வந்தது" — நம்ம `provenance`-க்கு இணையானது, ஆனால் document-அளவில்.
- **Knowledge graph** (kg_* tools) + **attachment store** (content-addressed, SHA-256 folder) + **sync engine** (checkpoint/cursor → interrupted sync திரும்ப தொடரும்).

**நமக்கு:** ✅✅ **"ஒரு folder-ஐ index செய், பிறகு கேள் — பதில் citation உடன்"**. நம்மில் `brain file` ஒரு file-ஐ படிக்கும், `brain recall` நினைவுகளைத் தேடும் — **ஆனால் ஒரு folder-ஐ corpus-ஆக index செய்யும் வழி இல்லை**. இது owner-க்கு மிகப் பெரிய மேம்பாடு ("என் D: drive-ல் உள்ள ஆவணங்களில் இருந்து பதில் சொல்"). ✅ **Chunking + citation + token budget** — நேரடியாக எடுக்கலாம் (Apache-2.0). ✅ **Hybrid RRF** — நம்ம FTS5+vector-ஐ இன்னும் நல்ல முறையில் கலக்க.
❌ **எடுக்கக் கூடாது:** அவங்க `memory.db` **encrypt இல்லை** (documents table plaintext). நம்ம vault design தான் சரி — நாம memory corpus-ஐயும் vault key-ல் seal செய்வோம்.

### 2.4 Messaging channels (31) — "போன் எவ்வளவு சிம்பிள்?"
Registry + `BaseChannel` (connect/disconnect/send/receive/status) + `ChannelMessage` + `ChannelRegistry`. 31: Telegram, Discord, Slack, WhatsApp (Business API + Baileys), Signal, iMessage (SendBlue / BlueBubbles), SMS (Twilio), Email (SMTP/IMAP), WebChat, Teams, Matrix, Mattermost, Rocket.Chat, Zulip, Google Chat, Feishu, LINE, Viber, Messenger, Reddit, Mastodon, XMPP, Twitch, Nostr, IRC, webhook.

**"எவ்வளவு சிம்பிள்" என்ற கேள்விக்கு நேர்மையான பதில்:** அவங்களிடம் **phone-க்கு ஒரு app இல்லை**. வழி = (a) **SendBlue** (காசு கொடுத்து ஒரு US phone number) + **ngrok** public URL + webhook, அல்லது (b) **Slack** app + Socket Mode. இரண்டும் **developer setup** — நம்ம Telegram (BotFather → token → chat id, 5 நிமிடம், இலவசம்) **அவங்களை விட சிம்பிள்**. ஆனால் அவங்க **ஒரே `BaseChannel` வடிவம்** — புதிய channel சேர்க்க 1 file. ✅ இதை நாம் எடுக்கலாம்: `brain/channels/` ஒரு registry, அதில் Telegram இன்றே இருக்கும், பிறகு WhatsApp/Slack/Discord = தலா 1 file. ✅ **auto-restore on restart** (daemon restart-க்குப் பிறகு channel தானாக மீண்டும் இணையும்) — நம்ம `nila-phone.bat`-ல் இல்லை.

### 2.5 Data connectors (27)
gmail / gmail_imap / outlook / imap, gdrive, gcalendar, **google_tasks**, gcontacts, notion, obsidian, dropbox, slack, granola, apple_notes / calendar / contacts / health / music, imessage, **spotify**, **oura** (ring), **strava**, weather, hackernews, news_rss, github_notifications, whatsapp.
- Credentials `~/.openjarvis/connectors/*.json`, mode **0600**.
- `SyncEngine`: checkpoint + cursor + last_sync + error → **interrupted sync resume**.
- `IngestionPipeline` → chunk → embed → store.

**நமக்கு:** ✅ **Obsidian/உள்ளூர் folder connector** (மிக எளிது, network இல்லை), ✅ **RSS + Hacker News** (நம்ம `web_rss` உள்ளது — அதை connector-ஆக மாற்று), ✅ **sync checkpoint** (நம்ம sync blob-ல் இல்லை → பெரிய backup நடுவில் அறுபட்டால் மீண்டும் முதல் இருந்து தான் ஆகும்).
⚠️ OAuth (Gmail/Drive) — owner-க்கு Google Cloud project உருவாக்கச் சொல்வது **அவர் விரும்பாத வேலை**. நம்ம stage-18 முடிவு (declared but disabled + single-account binding) தான் சரி. App-password (IMAP) வழி மட்டும் பரிசீலிக்கலாம்.

### 2.6 Scheduler + Operators (இது தான் அவங்க "differentiator")
- `TaskScheduler`: `once` / `interval` / `cron`, SQLite-ல் நிரந்தரம், 60s-க்கு ஒரு poll, pause/resume/cancel, run log.
- 5 scheduler tool → **model-ஏ ஒரு வேலையை schedule செய்யும்**.
- **Operators**: ஒரு `.toml` manifest (`schedule`, `tools`, `max_turns`, `system_prompt`) — 4 ready-made: `news_digest`, `researcher`, `system_monitor`, `knowledge_curator`.
- **Morning digest**: email+calendar+health+news → local LLM → ஒரு narrative → **TTS audio** (6am cron), persona/honorific ("sir"), voice/speed config.

**நமக்கு:** ✅✅ **`brain schedule`** — ஒரு reminder அல்ல, **திரும்பத் திரும்ப ஓடும் வேலை** ("ஒவ்வொரு திங்கள் காலையும் இந்த project-ன் நிலை சொல்"). ✅ **Operator manifest** (TOML) — "எந்த tool, எத்தனை turn, என்ன prompt" ஒரு file-ல்; நம்ம `.bat`-ஐ விட சுத்தம். ✅ **digest + `brain speak`** — நம்மில் TTS ஏற்கனவே இருக்கு (espeak/Piper/SAPI), அதனால் "காலை 7 மணிக்கு என் நாளின் சுருக்கம் கேட்கணும்" **மிகச் சிறிய வேலை**. ⚠️ Background service — owner-க்கு Windows Startup shortcut தான் சரி (service வேண்டாம்; அவங்களும் scheduled task optional-ஆ தான் வைக்கிறாங்க).

### 2.7 Skills (agentskills.io standard)
- ஒரு skill = folder + `SKILL.md` (Markdown வழிமுறைகள்) அல்லது `skill.toml` (pipeline steps) அல்லது இரண்டும்.
- **ஒவ்வொரு skill-உம் ஒரு tool** — agent catalog-ல் பார்த்து தேவைப்பட்டால் அழைக்கும்.
- `jarvis skill install hermes:arxiv`, `skill sync openclaw --category research` → **~13,700 community skill**.
- `skill run`, step templates `{placeholder}`, nested skills, `output_key`.
- Optimize: DSPy / GEPA / **ACE** (playbook — "எது உதவியது, எது தீமை" என்ற எண்ணிக்கையுடன் ஒரு படிக்கக்கூடிய strategy file).

**நமக்கு:** ✅ **`brain skill`** — "என் வங்கிக் கடிதத்தைப் படித்து 3 வரியில் சொல்" மாதிரி **ஒரு வேலையை ஒரு Markdown file-ஆக** சேமித்து, பிறகு பெயரால் அழைப்பது. owner-க்கு இது "நான் சொல்லித்தரும் வேலை" — நம்ம `instruct` (standing rules) + skill (procedure) சேர்ந்தால் மிக வலுவானது. ⚠️ Community 13,700 skill-ஐ நேரடியாக install செய்யக் கூடாது — அவை வெளி code; நாம **உள்ளூர் folder-ல் இருந்து மட்டும்** load செய்வோம் (sandbox principle).

### 2.8 MCP + A2A (protocol reach)
- **MCP** (Model Context Protocol): client + server, transports — in-process, **stdio**, **SSE**, **streamable HTTP**. `mcp_adapter` tool மூலம் வெளி MCP server-ன் tool-கள் agent-க்குக் கிடைக்கும் (Home Assistant உதாரணம் doc-ல் உள்ளது). Tool filtering support.
- **A2A** (Google Agent-to-Agent): `/.well-known/agent.json` agent card, tasks — ஒரு agent இன்னொரு agent-ஐ அழைக்கும்.

**நமக்கு:** ✅ **MCP stdio** மட்டும் எடுத்தால் போதும் — அதனால் Home Assistant, filesystem, database, browser போன்ற 1000+ ready-made MCP server நம்ம Nila-க்கு tool ஆகிவிடும் **code எழுதாமல்**. ⚠️ ஆனால் MCP server = வெளி process; நம்ம permission broker-ல் **ஒரு முறை அனுமதி** கட்டாயம். A2A = இன்னும் ஆரம்பம், நமக்கு இப்போது தேவை இல்லை.

### 2.9 Security (இது அவங்க strongest பகுதி — நாம இங்கே நிறைய கற்கலாம்)
| அவங்க | என்ன செய்கிறது | நம்மில் |
|---|---|---|
| `GuardrailsEngine` | எந்த engine-ஐயும் சுற்றி scan (input + output), 3 mode: **WARN / REDACT / BLOCK** | இல்லை (நம்ம `redact.py` write path-ல் மட்டும்) |
| `SecretScanner` / `PIIScanner` | regex + checksum, threat level | ✅ உள்ளது (`brain/security/redact.py`) |
| `InjectionScanner` | "ignore previous instructions", role override, exfiltration, shell injection — 30+ pattern, severity, matched_text 100 chars மட்டும் | ⚠️ நம்மில் `classify.py`-ல் சில வரிகள் மட்டும் |
| **Taint tracking** | `TaintSet{PII, SECRET, USER_PRIVATE, EXTERNAL}` + **SINK_POLICY** (web_search/http_request/file_write/channel_send-க்கு tainted data போகாது), `propagate_taint`, `declassify` | ✅ நம்மில் `_held_when_tainted` உள்ளது — **கருத்து ஒன்றே**; அவங்க label-கள் நுட்பமானவை |
| **Capability policy** (RBAC) | YAML policy: `file:read`, `network:fetch`, `code:execute`, `schedule:create`… per-agent grant | ✅ நம்ம `CAP_*` + broker உள்ளது |
| `approval_store` | pending action + remembered decision + tier + expiry | ⚠️ பாதி (broker உள்ளது, queue இல்லை) |
| **SSRF guard** | private CIDR, cloud metadata, **disguised IPv4 (hex/octal/short/dotted)**, DNS resolve → private-ஆ இருந்தால் block, unresolvable = **fail closed** | ⚠️ **பிழை இருந்தது — இன்று சரி செய்தேன் (§7)** |
| `subprocess_sandbox`, `rate_limiter`, `signing` | process isolation, per-agent rate limit, signed artifacts | ⚠️ rate limit உள்ளது, sandbox இல்லை |
| `audit.py` (SQLite) + `data_boundary_audit` | எல்லா security event-உம் SQLite-ல்; **`jarvis scan --data-boundaries`** = config posture report (cloud on?, telemetry on?, `0.0.0.0` bind?, connectors?, credentials?, `memories + cloud` combo?) — `--strict` CI-க்கு | ✅ hash-chained audit உள்ளது; ❌ **data-boundary report இல்லை** |
| `file_policy` | `.env`, `*.pem`, `id_rsa`, `credentials.*` refuse | ✅ உள்ளது (`host_tools`) |

**நமக்கு:** ✅✅ `scan --data-boundaries` **மாதிரி** ஒரு `brain scan` (நம்ம `report.bat`-ஐ விட நுட்பமானது, JSON, `--strict` exit code). ✅ **InjectionScanner pattern list** (நம்ம classify-ல் சேர்). ✅ **REDACT mode** — cloud-க்குப் போகும் முன் PII/secret-ஐ மாற்று. ✅ **Taint label நுட்பம்** (SECRET vs PII vs USER_PRIVATE) — நம்ம boolean-ஐ விட சிறந்தது.

### 2.10 Observability + Learning
- **Telemetry**: ஒவ்வொரு inference-க்கும் `prompt_tokens, completion_tokens, latency, ttft, cost_usd, energy_joules, power_watts` → SQLite; `TelemetryAggregator` summary / per-model / per-engine / top / export.
- **Traces**: முழு interaction (routing → memory → inference → tool → answer) SQLite-ல்; `TraceAnalyzer`; `TraceDrivenPolicy` "எந்த model/agent/tool சிறந்தது" என்று கற்றுக்கொள்ளும்.
- **Bench**: latency / throughput benchmark suite; **Evals** 37k lines (leaderboard, comparison, scorers).
- **Energy**: GPU/CPU power → joules per token ("intelligence per watt" ஆய்வு).

**நமக்கு:** ✅ **ஒரு turn-க்கு tokens + latency + cost + எந்த engine** என்று SQLite-ல் எழுதி, `brain stats` ("இந்த மாதம் 340 கேள்வி, 12 cloud call, ₹0, சராசரி 0.8s") — **சிறிய வேலை, பெரிய நம்பிக்கை**. ⚠️ Learning/evals/bench/energy = ஆய்வுக்கானவை, நமக்கு இப்போது தேவை இல்லை.

### 2.11 Interfaces + packaging (நாம இங்கே கவனிக்க வேண்டியது)
- CLI: `ask`, `chat`, `gui`, `serve`, `doctor`, `scan`, `vault`, `memory`, `telemetry`, `bench`, `skill`, `agents`, `operators`, `channels`, `connect`, `scheduler`, `tunnel`, `gateway`, `config`, `init`, `quickstart`… (Click-based, Rich tables).
- **`jarvis doctor`**: python version, config, engine health, GPU, ports — `ok/warn/fail` table + JSON. (நம்ம `brain doctor` + `report` உள்ளது — நல்லது.)
- **OpenAI-compatible API**: `POST /v1/chat/completions`, `/v1/models`, `/health`, `/v1/channels*` — அதாவது **எந்த OpenAI client-உம் Nila-ஐப் பயன்படுத்தலாம்**. `OPENJARVIS_API_KEY` இல்லாமல் 0.0.0.0 bind செய்தால் server **start ஆகாது** (fail closed) — நல்ல பாடம்.
- **Desktop**: Tauri (React) `.exe/.dmg/.deb/.AppImage` + **signed auto-update** (minisign pubkey app-ல்), "backend required" என்று நேர்மையாகச் சொல்கிறது.
- Install: `install.sh` / `install.ps1` (Python 3.11–3.13 check, uv, Rust+MSVC எச்சரிக்கை, venv, Ollama + starter model, optional scheduled task), Docker, systemd, launchd, Render.
- **Telemetry to PostHog**: opt-in, prompt-ல் "Enter = சம்மதம்", `[analytics] enabled` config-ல், install id UUID, **chat content ஒருபோதும் இல்லை**, 365 நாள் retention — honest ஆனால் **நாம இதை எடுக்கக் கூடாது** (நம்ம promise: எதுவும் வெளியே போகாது).

### 2.12 Testing (7,990 tests — இது தான் அவங்க உண்மையான பலம்)
1. **3-OS CI matrix** (ubuntu 3.11 + 3.12, **windows-latest**): RAM detect, CLI, daemon, GUI process cleanup, PyO3 import, file aliases/network policy, capability parity — Windows-specific job தனியாக.
2. **14 workflows**: ci, security-sast (mandatory audit), desktop build/release, frontend, docs, docker-validation, **installer-integration**, bash-tests, claude-review.
3. **`tests/conftest.py` autouse fixtures** — ஒவ்வொரு test-க்கும்: (a) `OPENJARVIS_HOME` ஒரு temp folder-க்கு, (b) **எல்லா registry-ஐயும் clear** (leak இல்லை), (c) config `lru_cache` clear, (d) update-check off. இது தான் 7,990 test ஒரே நேரத்தில் நம்பகமாக ஓடக் காரணம்.
4. **`respx`** — HTTP-ஐ mock (15 test files) → network இல்லாமல் connector test.
5. **`docs/testing/agent-qa-runbook.md`** — கைமுறை QA அட்டவணை: scenario / steps / expected / pass-checkbox (CLI 12, desktop 10, channel matrix 10). நம்ம stage notes-ஐ விட சிறந்த வடிவம்.
6. **`REVIEW.md`** — PR review checklist (relevance/completeness/correctness/testing/security) + `blocking/suggestion/nit` severity. AI reviewer-க்கான வழிமுறை.
7. ஒவ்வொரு security feature-க்கும் **regression test** — எ.கா. disguised IPv4-க்கு `test_ssrf_disguised.py`.

**நமக்கு:** ✅ **autouse fixture pattern** (நம்ம tests-ல் registry/vault leak தடுக்க), ✅ **respx-style HTTP mock**, ✅ **QA runbook Table** வடிவம், ✅ **Windows CI job** — நாம Windows-ல் ஓட்ட முடியாதது தான் நம்ம பெரிய குறை; GitHub Actions-ல் `windows-latest` job ஒன்று சேர்த்தால் `.bat` + shortcut + DPAPI உண்மையில் சோதிக்கப்படும் (**இது தான் நம்ம biggest gap-க்கு நேரடி தீர்வு**).

---

## 3. அவங்க சொல்லாத / செய்யாத விஷயங்கள் (நாம முன்னால் இருக்கும் இடம்)

| விஷயம் | OpenJarvis | Nila |
|---|---|---|
| Memory at rest | **plaintext SQLite** (`documents.content`), file permission மட்டும் | AES + passphrase + TOTP + voice lock + hash-chained audit |
| Voice as second factor | இல்லை (Whisper STT மட்டும்) | ✅ voiceprint + liveness challenge |
| Tamil | இல்லை | ✅ detect, answer, speak (3 engines) |
| Windows one-click | `.ps1` + uv + Rust + MSVC | ZIP → `nila-start.bat` → Desktop icon, passphrase once |
| Backup/restore | Docker/deploy docs | `backup-to-d.bat` + restore check + D: pointer |
| Screen reading / OCR / music / wake word | ❌ "no computer use" (doc-ல் நேர்மையாக ஒப்புக்கொள்கிறார்கள்) | ✅ உள்ளது |
| Anything leaving the device | PostHog analytics (opt-in), cloud engines | ✅ opt-in cloud மட்டும், ledger + budget, redaction |

**அதாவது: அவங்க "breadth", நாம "depth + privacy + owner's language".** நாம அவங்களைப் போல் 66 tool சேர்க்க வேண்டாம்; **6–8 சரியானவை** சேர்த்தால் போதும்.

---

## 4. நாம எடுக்க வேண்டியவை — முன்னுரிமை வரிசையில்

### P0 — இன்று/இந்த வாரம் (சிறிய வேலை, பெரிய பாதுகாப்பு)
| # | என்ன | ஏன் | அளவு | நிலை |
|---|---|---|---|---|
| 1 | **Disguised IPv4 SSRF fix** (`0x7f.0.0.1`, `0177.0.0.1`, `127.1`) | இன்று நம்மில் **உண்மையான ஓட்டை** இருந்தது — ஓட்டிப் பார்த்து உறுதி செய்தேன் | ~15 lines + 6 tests | **✅ முடிந்தது, commit ஆகிவிட்டது** |
| 2 | **LoopGuard** (ஒரே call ×3, ping-pong, poll budget) | tool loop-ல் model சிக்கினால் நம்ம `MAX_TOOL_ROUNDS` வரை மீண்டும் மீண்டும் அதே call | ~150 lines + tests | அடுத்த stage |
| 3 | **InjectionScanner** pattern list | tool output / RSS / PDF-ல் "ignore previous instructions" வந்தால் | ~120 lines | அடுத்த stage |
| 4 | **`brain scan`** (data-boundary report, `--strict`) | "cloud on-ஆ? vault encrypted-ஆ? phone open-ஆ?" ஒரே JSON-ல் | ~200 lines | அடுத்த stage |
| 5 | **Turn telemetry** (tokens/latency/engine per turn → SQLite) + `brain stats` | "எவ்ளோ வேகம், எவ்ளோ cloud" என்ற கேள்விக்கு எண் | ~200 lines | அடுத்த stage |

### P1 — அடுத்த இரண்டு stage (owner-க்கு நேரடியாகத் தெரியும்)
| # | என்ன | owner-க்கு என்ன பலன் |
|---|---|---|
| 6 | **Document index + citation** (`brain index D:\docs` → `brain ask` citation உடன்) | "என் ஆவணங்களில் இருந்து பதில் சொல் — எந்த file-ல் இருந்து என்றும் சொல்" |
| 7 | **`brain schedule`** (once / interval / daily) + Startup daemon-less tick | "ஒவ்வொரு திங்கள் காலை 9-க்கு project நிலை சொல்" |
| 8 | **Approval queue** (tier + "always yes/no" + phone `/yes 3`) | Nila தானாக ஏதாவது செய்ய முன் போனில் கேட்பது |
| 9 | **Morning digest → `brain speak`** | காலை சுருக்கம் காதில் (Tamil-ல்!) |
| 10 | **`brain skill`** (உள்ளூர் Markdown procedure) | "இந்த வேலையை இப்படிச் செய்" என்று ஒரு முறை சொல்லித்தந்தால் நினைவில் |
| 11 | **TOML operator manifest** | எந்த tool / எத்தனை turn / என்ன prompt — ஒரு file |

### P2 — பெரியது, ஆனால் மதிப்பு அதிகம்
| # | என்ன | குறிப்பு |
|---|---|---|
| 12 | **MCP stdio client** | 1000+ ready tool code எழுதாமல் |
| 13 | **OpenAI-compatible `/v1/chat/completions`** | எந்த OpenAI client / IDE-யும் Nila-ஐ use பண்ணும் |
| 14 | **`BaseChannel` registry** (Telegram இன்றே; பிறகு WhatsApp/Slack) | "போன் இணைப்பு" ஒரு file வேலை |
| 15 | **Hybrid RRF memory** + memory backend registry | நம்ம FTS5 + vector-ஐ சரியாகக் கலக்கும் |
| 16 | **Windows CI job** (GitHub Actions `windows-latest`) | `.bat`, shortcut, DPAPI உண்மையில் சோதிக்கப்படும் — நம்ம biggest honesty gap |
| 17 | **QA runbook** (`docs/testing/` scenario table) | owner-க்கு "இதைச் செய்து பாருங்கள்" checklist |

### எடுக்கக் கூடாதவை (வேண்டுமென்றே)
- ❌ PostHog analytics / leaderboard — நம்ம promise-க்கு எதிரானது.
- ❌ Default `host = "0.0.0.0"` — நாம `127.0.0.1` மட்டும்.
- ❌ Plaintext `memory.db` — நம்ம vault key-ல் seal.
- ❌ `shell_exec` (allowlist இல்லாதது) — நம்மில் `run_command` policy-யுடன் உள்ளது, அதுவே சரி.
- ❌ 56 optional dependency sets / uv / Rust / MSVC — owner-ன் laptop-ல் `extract → double-click` என்று இருக்க வேண்டும்.
- ❌ 31 channel — ஒன்று (Telegram) சரியாக இருப்பது போதும்; இரண்டாவது தேவைப்பட்டால் அப்போது.
- ❌ Browser automation / computer-use — நம்ம screen+OCR அணுகுமுறை owner-க்கு மேலும் பாதுகாப்பானது.

---

## 5. நான் பரிந்துரைக்கும் அடுத்த 3 stage (நேர்மையான மதிப்பீடு)

| Stage | வேலை | நான் சோதிக்க முடியும்? |
|---|---|---|
| **20** | LoopGuard + InjectionScanner + `brain scan` + turn telemetry + `brain stats` | ✅ Linux-ல் முழுமையாக |
| **21** | Document index (chunk + citation + token budget) + `brain schedule` + approval queue | ✅ Linux-ல்; Windows-ல் `report.bat` |
| **22** | Morning digest (Tamil TTS) + `brain skill` + MCP stdio + OpenAI-compatible API | ⚠️ MCP/OpenAI API Linux-ல் சோதிக்கலாம்; TTS Windows SAPI-ல் owner தான் சொல்ல வேண்டும் |

**ஒரு stage-க்கு ஒரு வேலை** என்ற நம்ம வழக்கத்தை மீறாமல், P0-வை stage-20-ல் அடக்கலாம்.

---

## 6. Attribution (சட்டம் — நேர்மையான பகுதி)

அவங்க code **Apache-2.0**. நாம் ஒரு கருத்து/algorithm/pattern எடுத்தால்:
1. அந்த file-ன் தலைப்பில்: `# Idea from OpenJarvis (Apache-2.0) — https://github.com/open-jarvis/OpenJarvis` என்று எழுத வேண்டும்.
2. `docs/THIRD_PARTY_NOTICES.md` file ஒன்றில் பட்டியல்.
3. **Code நேரடியாக copy செய்தால்** Apache-2.0-ன் §4: license copy + NOTICE file + "modified" என்று குறிப்பு. நான் **கருத்தையும் அளவையும்** எடுத்து **நம்ம மொழியில் மீண்டும் எழுதுவேன்** (அவங்க code-ல் `openjarvis` import-கள் நம்மில் வேலை செய்யாது; அவங்க dependencies-ஐ நாம் சேர்க்கக் கூடாது).
4. Nila **Proprietary** — Apache-2.0 code-ஐ proprietary-ல் வைப்பது அனுமதிக்கப்பட்டது (copyleft இல்லை). ஆனால் attribution **கட்டாயம்**.

---

## 7. இந்த ஆய்வில் கிடைத்த உண்மையான பிழை — இன்றே சரி செய்தேன்

அவங்க `security/ssrf.py`-ல் `_disguised_ipv4()` + regression test பார்த்தேன். நம்ம `brain/util/http.py::is_public_url`-ஐ **ஓட்டிப் பார்த்தேன்**:

| URL | சரியான பதில் | சரி செய்யும் முன் நம்மில் |
|---|---|---|
| `http://127.0.0.1:8790/x` | block | ✅ block |
| `http://2130706433/x` | block | ✅ block (dot இல்லை) |
| `http://169.254.169.254/` | block | ✅ block |
| `http://0x7f.0.0.1/x` | **block** | ❌ **ALLOWED** |
| `http://0177.0.0.1/x` | **block** | ❌ **ALLOWED** |
| `http://127.1/x` | **block** | ❌ **ALLOWED** |

அதாவது: Nila ஒரு public feed-ஐப் படிக்கும்போது, அந்த feed `http://127.1/...` என்று redirect செய்தால் **நம்ம localhost API-ஐ token இல்லாமல் அழைக்க முடியும்**. இப்போது `socket.inet_aton` (glibc resolver-ஐப் போலவே hex/octal/short form-ஐ ஏற்கும்) மூலம் normalize செய்து block செய்கிறது.

**முடிந்தது — commit-ஆகிவிட்டது:**
- `brain/util/http.py`: `_disguised_ipv4()` + `is_public_url`-ல் இணைப்பு.
- `tests/test_audit_redirect_credentials.py`: **6 புதிய regression test** (redirect வழியும் சேர்த்து, public IP சரியாக அனுமதிக்கப்படுகிறதா என்ற positive control-உம்).
- `docs/THIRD_PARTY_NOTICES.md`: Apache-2.0 attribution (சட்டத் தேவை).
- முழு suite: **1607 pass / 0 fail / 3 skip** (முன் 1598). retest **60/5/0**. probe **29/0/0**.

**இது தான் இந்த ஆய்வின் மிக முக்கியமான முடிவு** — 18 stage-ல் யாரும் பிடிக்காத ஒன்றை, அவங்க code-ஐப் படித்ததால் பிடித்தோம்.

---

## 8. சுருக்கம் (ஒரே பாராவில்)

OpenJarvis ஒரு **ஆய்வு stack** — மிக அழகான registry architecture, 66 tool, 31 channel, scheduler + background operators, skills ecosystem, MCP, மற்றும் 7,990 test. நாம அவங்களிடமிருந்து **கருத்துகளை** எடுக்கிறோம்: **loop guard, approval queue, injection scanner, data-boundary scan, per-turn telemetry, document index + citation, scheduler, operator manifest, skill files, MCP**. அவங்க **storage/analytics/install** மாதிரியை எடுக்கக் கூடாது — அங்கே நாம் முன்னால் இருக்கிறோம் (encryption, Windows one-click, Tamil, voice, backup). இன்று ஒரு **உண்மையான SSRF ஓட்டை** சரி செய்தோம். அடுத்து stage 20: LoopGuard + injection patterns + `brain scan` + telemetry.
