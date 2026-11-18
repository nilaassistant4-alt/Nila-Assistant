# Nila v0.2 → v3: **அடுத்த பேட்ச்** — என்ன சேர்க்கலாம், எதை v3-க்கு வைக்கலாம்

**நாள்:** 2026-11-04 · **கிளை:** `arena/3f2334a4-nila-assistant` (`cbd1ff2`)
இது **ஆய்வு + திட்ட ஆவணம்**. Code மாற்றம் இல்லை.

---

## 1. நீங்கள் சொன்னது → என் புரிதல் (தவறா சொல்லுங்கள்)

| # | நீங்கள் சொன்னது | என் புரிதல் |
|---|---|---|
| 1 | "தமிழ் + English மட்டும் பேசணும், 5–10 மொழி புரியணும்" | ✅ **முடிந்தது** (ஸ்டேஜ் 3) |
| 2 | "Google/Gemini மாதிரி பேக்கில் நிறைய connectors" | Calendar, Drive/Sheets read, Gmail, YouTube, Weather — **கட்டப்பட வேண்டும்** |
| 3 | "YouTube-ல் best video link chat box-ல் கொண்டு வா" | search + link; **transcript படித்து சுருக்கம்** சாத்தியம் (youtube-transcript-api) |
| 4 | "நான் சொல்லச் சொல்ல என் PC-ல் action நடக்கணும்" | tool calling + permission broker (ஏற்கனவே உள்ளது) |
| 5 | "login: voice இல்லன்னா password" | ✅ vault உள்ளது (passphrase/voice/TOTP) — **வேகமாக்க வேண்டும்** |
| 6 | "ZIP → extract → `nila start` → OTP → access" | **சுருக்க வேண்டும்** (இப்போது 3 கட்டளை) |
| 7 | "சின்ன சின்ன bug/பிரச்சனை" | கீழே §5-ல் 12 புள்ளி பட்டியல் |
| 8 | "voice, file share, screen பார்க்கும் — நீங்கள் கேட்ட அடிப்படை" | ஸ்டேஜ் 4 |
| 9 | "இது போதும், v2-க்கு வைப்போம் / v3-க்கு வைப்போம்" | §6-ல் பிரிவு |

---

## 2. **ஸ்டேஜ் 4** — Voice input + File share + Screen

| # | வசதி | என்ன | ஆபத்து | நேர்மை |
|---|---|---|---|---|
| 1 | **Voice input** | Whisper-small (offline, 500 MB) அல்லது **Vosk-small** (50 MB, தமிழ் உண்டு) | mic permission | Whisper தமிழ் நல்லது; Vosk வேகம் ஆனால் குறைவு துல்லியம் |
| 2 | **File share** | PDF/DOCX/TXT drag → சுருக்கம் | file அளவு | `pypdf` / `python-docx` — install தேவை |
| 3 | **Screen பார்க்கும்** | *ஸ்கிரீன்ஷாட் → OCR/படித்து சொல்லுதல்* | **மிக அதிக ஆபத்து** | எப்போதும் அனுமதி கேட்க வேண்டும், அல்லது v3 |
| 4 | **"என்னை அழைத்தால் வா"** (wake word) | எப்போதும் mic கேட்கும் | battery+privacy | **பரிந்துரைக்கவில்லை** — push-to-talk நல்லது |

**என் பரிந்துரை:** ஸ்டேஜ் 4-ல் **voice input (push-to-talk) + file share** மட்டும்; screen-ஐ **v3**-க்கு.

---

## 3. Connectors — Google மாதிரி "பேக்" (மிக முக்கியம்)

ஏற்கனவே உள்ள அடித்தளம்: `IntegrationGateway` — allow-list host + declared ops + `$vault` token + ஒவ்வொரு call-க்கும் ஒரு grant. இதில் **புதிய connector = JSON ஒன்று** (code இல்லை).

| Connector | எப்படி | login தேவையா | மதிப்பு | நிலை |
|---|---|---|---|---|
| **Calendar read** | `.ics` secret address | ❌ | ★★★ | ✅ **முடிந்தது** |
| **Weather** | Open-Meteo (key இல்லை) | ❌ | ★★★ | ஸ்டேஜ் 5 — 1 மணி நேரம் |
| **Wikipedia** | REST API (key இல்லை) | ❌ | ★★★ | ஸ்டேஜ் 5 |
| **YouTube search** | Data API v3 (free 10k/day key) | key | ★★★ | ஸ்டேஜ் 5 |
| **YouTube transcript** | `youtube-transcript-api` | ❌ | ★★★ | ஸ்டேஜ் 5 — **"இந்த வீடியோவில் என்ன"** |
| **RSS (news)** | feed URL | ❌ | ★★ | ஸ்டேஜ் 5 |
| **Gmail read** | OAuth | ✅ | ★★★ | v3 |
| **Drive / Sheets read** | OAuth | ✅ | ★★ | v3 |
| **Calendar write** | OAuth | ✅ | ★★★ | v3 |
| **Telegram bot** | token | token | ★★★ | v3 (phone notification-க்கு உதவும்) |
| **Maps** | OpenStreetMap | ❌ | ★★ | பிற்பாடு |

**முக்கிய கண்டுபிடிப்பு:** `youtube-transcript-api` + `youtube` search சேர்த்தால் —
> *"YouTube-ல் 'drip irrigation' பத்தி best video கொடு, அதில் என்ன சொல்றாங்க என்று தமிழில் சொல்"*
— இது **இன்றே சாத்தியம்**. இது உங்கள் கேள்விக்கு நேரடி பதில்.

---

## 4. "நான் சொல்லச் சொல்ல PC-ல் action" — என்ன சாத்தியம், என்ன இல்லை

| ✅ சாத்தியம் (permission உடன்) | ❌ சாத்தியமில்லை |
|---|---|
| file திற / படி / தேடு · folder list | admin password தேவைப்படும் வேலைகள் |
| app திற (Word, browser, calculator) | **பாதுகாப்பு prompt-ஐ auto-click** (OS தடுக்கும்) |
| reminder, calendar, timer | bank/UPI payment |
| clipboard, screenshot (v3) | வேறு user account |
| backup, sync, audit | OS install / registry |

**எல்லா action-க்கும்:** ஒரு முறை அனுமதி → செய் → audit-ல் பதிவு. **நான் silent-ஆக எதுவும் செய்ய மாட்டேன்.**

---

## 5. 🐛 இப்போது தெரிந்த **சின்ன சின்ன பிரச்சனைகள்** (12) — ஒவ்வொன்றும் சரிசெய்யலாம்

| # | பிரச்சனை | தீர்வு | நேரம் |
|---|---|---|---|
| 1 | Voice input இல்லை (நீங்கள் பேச முடியாது) | Whisper/Vosk | ஸ்டேஜ் 4 |
| 2 | Microphone permission இல்லை | `mic` capability + ஒரு முறை அனுமதி | ஸ்டேஜ் 4 |
| 3 | File (PDF/DOCX) படிக்க முடியாது | pypdf/docx tool | ஸ்டேஜ் 4 |
| 4 | YouTube link கொடுக்க முடியாது | connector | ஸ்டேஜ் 5 |
| 5 | Weather இல்லை | Open-Meteo | ஸ்டேஜ் 5 |
| 6 | Startup 3 கட்டளை (`unlock` → `serve`) | `nila start` ஒரே கட்டளை | ஸ்டேஜ் 5 |
| 7 | Nila மூடியிருந்தால் reminder வராது | Windows Task Scheduler | ஸ்டேஜ் 5 |
| 8 | Gemini model பெயர் யூகம் | `/v1/models`-ல் probe + fallback | ஸ்டேஜ் 5 |
| 9 | `brain ask` = ஒரு கேள்வி, chat இல்லை | `brain chat` REPL உள்ளது — discoverability | docs |
| 10 | Multi-language translation = model தேவை | Gemini free tier; இல்லாமல் detection மட்டும் | வரையறை |
| 11 | Memory-ல் 3 மாச auto-delete இல்லை | ஸ்டேஜ் 6 | ஸ்டேஜ் 6 |
| 12 | Phone app இல்லை | Telegram bot (v3) | v3 |

---

## 6. **v2-க்கு (இப்போது) vs v3-க்கு (பிற்பாடு)** — என் பிரிவு

### ✅ v2-ல் இப்போது (அடுத்த 3 ஸ்டேஜ், ~1–2 வாரம்)
1. **Voice input (push-to-talk, offline)** — Whisper/Vosk
2. **File share** — PDF/DOCX/TXT சுருக்கம்
3. **Free connectors pack** — Weather, Wikipedia, YouTube search + transcript, RSS
4. **நான் சொல்லச் சொல்ல action** — "open Chrome", "find that file", "make a folder" (permission உடன்)
5. **`nila start`** — 3 கட்டளையை 1 ஆக்கு
6. **சின்ன bug 12** சரிசெய்தல்
7. **Telegram bot → phone-க்கு reminder** (phone app-க்கு மலிவான வழி)

### 🅥 v3-க்கு (பிற்பாடு)
- **இசை** (music player, playlist) — *இது BASIC அல்ல, கடைசி*
- **Screen பார்க்கும் / wake word** (privacy risk)
- **Gmail / Drive / Calendar write (OAuth)** — ஒவ்வொரு service-க்கும் consent screen
- **Android app** (battery, notification, hotspot)
- **Multi-user** (குடும்பத்தில் பலர்)
- **Fine-tuned தமிழ் model**
- **Mesh/local network** (வீட்டில் 2 PC)

> நீங்கள் சொன்னது: *"ரொம்ப காம்ப்ளிகேட்டடா இருக்கிறத v3-க்கு ஊத்தி வச்சிருவோம்"* — **சரி, இசை + screen + Gmail அங்கே தான்.**

---

## 7. எனது பரிந்துரை — அடுத்த ஸ்டேஜ் (5)
**Free connectors pack (Weather + Wikipedia + YouTube search/transcript) + `nila start`**
- login தேவையில்லை (key இல்லாத 3-ல் 2)
- **உங்கள் "YouTube best video link" கேள்விக்கு நேரடி பதில்**
- regression ஆபத்து குறைவு (புதிய connector JSON + 1 tool)

அதன் பிறகு **ஸ்டேஜ் 6: voice input + file share** (ஸ்டேஜ் 4-ஐ முன் தள்ளுகிறேன், ஏனெனில் connectors மலிவானவை).

**தொடங்கட்டுமா? "சரி" என்றால் ஸ்டேஜ் 5-ஐக் கட்ட ஆரம்பிக்கிறேன்.**
