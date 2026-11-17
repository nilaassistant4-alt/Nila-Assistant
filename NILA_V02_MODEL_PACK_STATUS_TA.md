# ஸ்டேஜ் 1 முடிந்தது — **Model Pack (சிறிய brain / பெரிய brain ஒர்க்ஃப்ளோ)**

**நாள்:** 2026-11-04 · **கிளை:** `arena/3f2334a4-nila-assistant`
**suite: 861 passed, 3 skipped** (புதிய 29 acceptance tests உட்பட)
**ZIP:** `Nila-verification-v0.2-ab-included.zip` (புதுப்பிக்கப்பட்டது)

---

## 1. நீங்கள் கேட்டது → என்ன கட்டினேன்

| நீங்கள் சொன்னது | இப்போது |
|---|---|
| "**background-ல் connect பண்ணிடு**" | `brain models` — Ollama-வில் எது **உண்மையில்** உள்ளது என்று பார்க்கும் (2 வினாடி probe). இல்லாததை "உள்ளது" என்று சொல்லாது. |
| "**சின்ன கேள்விக்கு சின்ன answer**" | job classifier: short/lookup → **built-in engine** (0 byte, 0 செலவு) → `qwen2.5:3b` |
| "**பெரிய வேலைக்கு பெரிய workbench**" | "plan / compare / step by step / explain in detail / திட்டம் / விளக்கு / எழுது" → **big** → `qwen2.5:7b` → (தேவைப்பட்டால்) `gemini-flash` |
| "**தேவைப்படும்போது மட்டும் கேட்கணும்**" | ஒவ்வொரு பதிலிலும் `route`: `job` (small/big), `brain` (எந்த model), `free_brain` (true/false) |
| "**Qwen2.5-3B/7B, Llama 3.2-3B, Phi-3.5-mini, Whisper, Piper**" | **எல்லாமே pack-ல் உள்ளது** — எடை (GB), RAM load, தமிழ் தரம், install command |
| "**free tier limit-க்குள்**" | `free_only` mode: paid model **ஒருபோதும்** தேர்ந்தெடுக்கப்படாது; ledger budget ஏற்கனவே உள்ளது |

## 2. Pack-ல் என்ன இருக்கு (13)

| Model | வகை | எடை | தமிழ் | எந்த வேலை |
|---|---|---|---|---|
| Built-in engine | local | 0 | நல்லது | சிறிய கேள்வி, கணக்கு, memory, rule |
| **Qwen2.5 3B** | local (Ollama) | 2.0 GB | **நல்லது** | small + big (**default**) |
| Qwen2.5 7B | local | 4.7 GB | நல்லது | big, code |
| Llama 3.2 3B | local | 2.0 GB | மிதம் | small (வேகம்) |
| Phi-3.5 mini | local | 2.2 GB | மிதம் | small, code (சிறிய laptop) |
| **Whisper small** | local | 0.5 GB | மிதம் | speech → text |
| **Piper Tamil** | local | 60 MB | **நல்லது** | text → voice |
| Gemini Flash | cloud | 0 | நல்லது | free tier, பெரிய context |
| Gemini Pro | cloud (paid) | 0 | நல்லது | big reasoning |
| OpenAI-compatible | cloud | 0 | மிதம் | OpenRouter/Groq/LM Studio |
| Claude | cloud (paid) | 0 | மிதம் | நீண்ட document, code |

**முக்கிய விதி:** *"இல்லாத model-ஐ ஒருபோதும் தேர்ந்தெடுக்காதே"* — இந்த விதிக்கு டெஸ்ட் உண்டு.

## 3. எப்படி உபயோகிப்பது

```
brain models                 # என்ன உள்ளது, என்ன இல்லை, எது எந்த வேலை, என்ன add பண்ணலாம்
brain models use qwen2.5:3b  # இதை default small brain ஆக வை
brain models --offline       # network probe வேண்டாம் (வேகம்)
```
HTTP: `GET /v1/models` (அதே தகவல்), `POST /v1/models/choose {"job":"big","free_only":true}`

## 4. டெஸ்ட் முடிவு
- **29 புதிய acceptance tests** — registry, "இல்லாததைத் தேர்ந்தெடுக்காதே", free-only, job classifier (தமிழ் உட்பட), Ollama detection (mock server), HTTP
- **முழு suite: 861 passed, 3 skipped**

## 5. ⚠️ நான் செய்யாதது (நேர்மை)
- **எந்த model-ஐயும் download/install செய்யவில்லை** — pack விவரிக்கிறது மட்டும்; `ollama pull …` நீங்கள் ஓட்ட வேண்டும்.
- **Ollama இல்லாத இந்த sandbox-ல்** "3B/7B வேலை செய்கிறது" என்று நான் சொல்ல முடியாது — *detect* பகுதி mock server-ல் டெஸ்ட் ஆனது; **உண்மையான பதில் தரம் உங்கள் PC-ல் நீங்கள் தான் பார்க்க வேண்டும்**.
- **Whisper/Piper** — install confirm செய்ய முடியாததால் `present: false` என்று நேர்மையாகக் காட்டும் (வேலை செய்யாதது போல் நடிக்காது).
- **`gemini-flash-latest`** என்ற பெயர் இன்னும் **ஒரு யூகம்** (PROGRESS.md-லேயே அப்படி எழுதியிருக்கு) — free tier-ல் பெயர் மாறினால் `brain models` "needs key / not present" என்று காட்டும், ஆனால் **உண்மையான call உங்கள் PC-ல் தான் சோதிக்கப்படும்**.

---

## 6. **பேக்கெட்டில் வேற என்னென்ன connect பண்ணலாம்** — status

| # | விஷயம் | நிலை | குறிப்பு |
|---|---|---|---|
| 1 | Reminder (நேரம், repeat, snooze) | ⏳ அடுத்த ஸ்டேஜ் | offline, stdlib |
| 2 | Calculator (`%`, வட்டி, GST, unit) | ⏳ | tool உண்டு; வலுப்படுத்த வேண்டும் |
| 3 | Calendar **read** (`.ics`, OAuth இல்லை) | ⏳ | login தேவையில்லை — வேகமான வழி |
| 4 | Calendar **create** (OAuth) | ⏳ | PC off-லும் alert |
| 5 | Voice output (Piper/SAPI) | ⏳ pack-ல் இடம் தயார் | audio helper உண்டு |
| 6 | Push-to-talk (Whisper) | ⏳ pack-ல் இடம் தயார் | `sounddevice` optional |
| 7 | PDF/DOCX/File search | ⏳ | offline tools pack |
| 8 | செலவு கணக்கு (expense) | ⏳ | local, private |
| 9 | Weather (Open-Meteo), Wikipedia, RSS | ⏳ | free, key இல்லை |
| 10 | Storage tiers + 3 மாச auto-delete | ⏳ | ஒப்புதல் இல்லாமல் அழியாது |
| 11 | Audit rotation + key-rotate reminder | ⏳ | A1/A10 |
| 12 | Phone app (battery/notification) | ❌ | app தேவை |
| 13 | Gemini-centric master switch | ⏳ | default OFF + எச்சரிக்கை |

---

## 7. நான் பரிந்துரைக்கும் **அடுத்த 3 ஸ்டேஜ்** (நீங்கள் "complete பண்ணி test ஆரம்பிக்கணும்" என்றீர்கள்)

**ஸ்டேஜ் 2 — Reminder + calculator (offline).** நீங்கள் முதன்முதலில் கேட்டது; இன்று இல்லாத மிகப் பெரிய இடைவெளி. Acceptance: offline அடிக்கும் · restart-க்குப் பிறகும் · text online-க்குப் போகாது · 200 கணக்கு சரி (`eval` இல்லை) · health reminder = reminder மட்டும்.

**ஸ்டேஜ் 3 — Calendar read (ICS) + voice output (Piper/SAPI).** "குறிப்பிட்ட தேதிக்கு சொல்லு" + "voice-ல் சொல்லு" — இரண்டும் ஒரே ஸ்டேஜில்.

**ஸ்டேஜ் 4 — Storage tiers + 3 மாச auto-delete + audit rotation.** memory வேகமாக இருக்க, audit file வளராமல் இருக்க.

இதற்குப் பிறகு: Calendar create (OAuth) → push-to-talk → PDF pack → phone app.

**இப்போது சொல்லுங்கள்:** ஸ்டேஜ் 2 (Reminder + calculator) தொடங்கட்டுமா? அல்லது ஸ்டேஜ் 3-ல் உள்ள **voice output**-ஐ முதலில் வேண்டுமா?
