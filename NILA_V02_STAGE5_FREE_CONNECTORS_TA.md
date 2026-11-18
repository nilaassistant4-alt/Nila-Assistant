# ஸ்டேஜ் 5 முடிந்தது — **Free connectors pack + `brain start`**

**நாள்:** 2026-11-04 · **கிளை:** `arena/3f2334a4-nila-assistant`
**suite: 984 passed, 3 skipped** (25 புதிய acceptance tests)
**ZIP:** `Nila-verification-v0.2-ab-included.zip` — 156 files, 635,035 B, `1fcd96de3e69…0ec7`

---

## 1. இப்போது வேலை செய்யும் connectors (6)

| Connector | Command | Login/key | என்ன தரும் |
|---|---|---|---|
| **Weather** | `brain web weather Chennai` | ❌ **எதுவுமில்லை** | இன்றைய வெப்பநிலை + 3 நாள் மழை வாய்ப்பு |
| **Weather (தமிழ்)** | `brain web weather "மதுரை"` | ❌ | **தமிழில்** பதில் ("மழை", "பகுதி மேகம்") |
| **Wikipedia** | `brain web wiki "drip irrigation"` | ❌ | சுருக்கம் + **source link** |
| **Wikipedia (தமிழ்)** | `brain web wiki "தமிழ்" --lang ta` | ❌ | தமிழ் Wikipedia |
| **YouTube search** | `brain web youtube "drip irrigation"` | ✅ free key | **3 video link + channel + தேதி** |
| **YouTube transcript** | `brain web transcript <link>` | ❌ **எதுவுமில்லை** | வீடியோவில் பேசியது (தமிழ் track இருந்தால் தமிழ்) |
| **RSS** | `brain web rss <feed-url>` | ❌ | தலைப்புச் செய்திகள் |
| **Pack பட்டியல்** | `brain web pack` | — | எல்லா connector + என்ன தேவை |

### உங்கள் கேள்விக்கு நேரடி பதில்
> "YouTube-ல் best video link கொண்டு வா, அதில் என்ன சொல்றாங்க என்று தமிழில் சொல்"

```
brain web youtube "best drip irrigation video"     → 3 links
brain web transcript https://youtu.be/xxxxx        → பேசியது (transcript)
```
**இரண்டும் சேர்ந்தால்** — link + உள்ளடக்கம். (தமிழில் சுருக்க: Gemini key இருந்தால்.)

## 2. `brain start` — 3 கட்டளை → **1 கட்டளை**

**முன்பு:** `brain unlock` → `brain serve` → token-ஐ copy → browser
**இப்போது:** `brain start` → unlock → app திறக்கும். token URL-ல் தானாக.

## 3. பாதுகாப்பு — எல்லா connector-க்கும் ஒரே விதி

- ஒவ்வொரு call-ம் **`net.host` permission** கேட்கும் → **ஒரு அனுமதி = ஒரு call** (டெஸ்ட் உண்டு)
- allow-list host மட்டுமே · rate limit · 2 MB response cap · audit
- **YouTube key** → sealed vault-ல் (config file-ல் இல்லை) அல்லது `YOUTUBE_API_KEY`
- **எந்த connector-க்கும் silent access இல்லை**

## 4. டெஸ்ட்
25 புதிய acceptance tests — Open-Meteo geocode+forecast/tamil · Wikipedia EN/TA/404 · YouTube search/9 link shape/transcript/captions-இல்லை · RSS+Atom · permission gate · `brain start`. எல்லாமே **network stub** உடன் (இந்த sandbox-ல் internet இல்லை).

**முழு suite: 984 passed, 3 skipped.** ZIP-ஐ பிரித்து மீண்டும் ஓட்டியும் **அதே 984**.

## 5. ⚠️ நேர்மை
- **இந்த sandbox-ல் internet இல்லை** → weather/wikipedia LIVE-ஆ ஓட்டிப் பார்க்க முடியவில்லை. JSON shape-ஐ **test-ல் mock** செய்து சரிபார்த்தேன்; **உங்கள் PC-ல் முதல் call நீங்கள் தான் பார்க்க வேண்டும்**.
- **YouTube search** — free key இல்லாமல் வேலை செய்யாது (சரியான செய்தியே காட்டும்).
- **Transcript** — uploader captions off செய்திருந்தால் கிடையாது (அதைத் தெரிவிக்கும்).
- **RSS/news** — feed address நீங்கள் தான் கொடுக்க வேண்டும்.
- **Gmail / Drive / Calendar-write** — OAuth தேவை → **v3**.

## 6. அடுத்தது — **ஸ்டேஜ் 6: Voice input + File share**
- **Push-to-talk** (Whisper small offline, அல்லது Vosk 50 MB)
- **PDF / DOCX / TXT** சேர் → சுருக்கம்
- mic permission = `net.host` மாதிரி ஒரு capability, ஒரு முறை அனுமதி

தொடங்கட்டுமா?
