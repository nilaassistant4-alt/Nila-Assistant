# ஸ்டேஜ் 6 முடிந்தது — **ஒரே குரல் · எந்தக் கோப்பும் · D: சேமிப்பு**
### + ஆழ்ந்த ஆய்வு: இன்னும் என்ன சின்ன விஷயங்கள் இருக்கு (மற்றும் அதற்கு அப்பால்)

**நாள்:** 2026-11-04 · **கிளை:** `arena/3f2334a4-nila-assistant`
**suite: 1024 passed, 3 skipped** (37 புதிய acceptance tests)
**ZIP:** `Nila-verification-v0.2-ab-included.zip` — 160 files, 661,838 B, `c37e64e0f23c…201b`

---

## 1. **உங்கள் குரல் மட்டும்** — இப்போது code-ல் இருக்கு

| உங்கள் விதி | எப்படிக் கட்டினேன் |
|---|---|
| "**என் வாய்ஸ் மட்டும்தான்** access ஆகணும்" | `brain voice enrol` → voiceprint. வேறு குரல் → **refuse** |
| "வேற யாரு கமெண்ட் கொடுத்தாலும், **stop** கொடுத்தாலும்" | `nila start` / `nila stop` → **குரல் சரிபார்த்த பிறகே** action |
| "எந்த ஒரு வாய்ஸையும் எடுத்துக்கொள்ளக் கூடாது" | profile இல்லை → **எதுவும் accept ஆகாது** (verdict: `no_profile`) |
| replay (பழைய recording போட்டு) | **சொல்ல வேண்டிய எண்** (challenge) — 4 இலக்கம், 180 வினாடி, **ஒரு முறை மட்டும்** |

**எப்படி மதிப்பிடுகிறது:** குரலின் *shape* (spectral) 35% + *pitch* 65%. Pitch தான் மனிதர்களைப் பிரிக்கிறது (என் அளவீட்டில்: ஒரே குரல் ±0.9 semitone, வெவ்வேறு குரல் 6+ semitone).
**Threshold உங்கள் குரலுக்கு ஏற்ப மாறும்** — 2+ sample கொடுத்தால்.

### ⚠️ நேர்மை (மிக முக்கியம்)
- இது **trained speaker model இல்லை** — MFCC+pitch voiceprint. **தெளிவான வேறு குரலை நம்பகமாக மறுக்கும்**, ஆனால் **நல்ல recording-ஆல் ஏமாற்ற முடியும்** → அதனால் challenge number.
- **உண்மையான உங்கள் குரலில் இன்னும் சோதிக்கப்படவில்லை** (இங்கே mic இல்லை). synthetic குரல்களில் 37 டெஸ்ட்.
- ECAPA/x-vector (100 MB) = அடுத்த நிலை; `LIMITS`-ல் எழுதியிருக்கு.

## 2. **"நிலா ஸ்டார்ட்" — நான் சொன்னா வரணும்**

| ✅ இருக்கு | ❌ இல்லை / ஏன் |
|---|---|
| **`brain launcher --write`** → Desktop-ல் icon (Windows: console இல்லாத `.vbs` + `.bat`) | **எப்போதும் mic கேட்கும் wake word** — battery + privacy; **வேண்டாம் என்று முடிவு** |
| **hotkey recipe** (Ctrl+Alt+N) | Nila **மூடியிருக்கும்போது** தானாக எழ முடியாது |
| **"நிலா தொடங்கு"** phrase (audio வந்தால்) | அது Nila ஓடும்போது மட்டும் |

**`brain start`** = unlock → app. Token-ஐ type செய்ய வேண்டாம்.

## 3. **எந்தக் கோப்பும் படிக்கும்**

| வகை | என்ன தரும் | Dependency |
|---|---|---|
| **PDF** | text (compressed/uncompressed) · scanned என்றால் **"OCR தேவை"** | ❌ இல்லை |
| **DOCX / XLSX / PPTX** | text / cells / slides | ❌ |
| **code** (.py .js .ts .java .go .rs…) | **ஒரு வரி சுருக்கம்**: lines, definitions, imports, TODO | ❌ |
| **folder** | எத்தனை file, எந்த வகை, biggest | ❌ |
| **image** | width/height/format (**உள்ளடக்கம் அல்ல** — vision model தேவை) | ❌ |

**`brain file <path>`** — permission-gated (`fs.read`), upload இல்லை.

## 4. **Video — 1 GB / 10 GB** (மிக நேர்மையான பகுதி)

| ✅ இப்போது | ❌ இல்லை |
|---|---|
| 10 GB file-ஐ **memory-க்குள் ஏற்றாமல்** seek செய்து படிக்கும் (**டெஸ்ட்: 80 MB-க்கும் குறைவாகப் படித்தது**) | **உள்ளடக்கத்தைப் புரிந்துகொள்வது** (என்ன நடக்கிறது) — vision model தேவை |
| duration, resolution, fps, frames, codec (**ffprobe இருந்தால்**) | **cut points** — ffmpeg decode தேவை |
| **சரியான ffmpeg command** 3: scene detect, silence detect, trim without re-encode | இங்கே ffmpeg இல்லை → command மட்டும் தருகிறேன் |

**உண்மை:** "AI வீடியோவை analyse செய்யும்" என்பது பொதுவாக 4–5 frame மட்டும் பார்ப்பது. **வீடியோ எடிட்டருக்கு** அது பயனற்றது — அவருக்கு **எங்கே cut, எங்கே silence** வேண்டும். அதற்கு ffmpeg தான் சரியான கருவி. **நான் அதை இங்கே இணைக்கவில்லை; command தருகிறேன்.**

## 5. **D: சேமிப்பு** — நீங்கள் கேட்டபடி

```
brain storage --set "D:/Nila Assistant Storage"
brain storage            → store path, size, free space
```
- **எல்லாம் அங்கே**: `memory.db` (encrypted), audit, backup, sync
- **Drive இல்லை என்றால் → refuse** (C:-ல் silent-ஆக empty database உருவாக்காது — உங்கள் history "போய்விட்டது" போல் தெரியும்)
- `--fallback-c on` = வேண்டுமானால் C:-க்கு
- **Nila தானாக எதையும் delete செய்யாது.** "இதை delete" என்று சொன்னால் மட்டும். `brain forget <id>`

---

## 6. 🔍 **ஆழ்ந்த ஆய்வு: இன்னும் என்ன சின்ன விஷயங்கள் இருக்கு**

### 6.1 குரல் / தொடக்கம்
| # | விஷயம் | தீர்வு | நேரம் |
|---|---|---|---|
| 1 | Voice print உண்மையான குரலில் calibrate ஆகவில்லை | நீங்கள் 3 sample கொடுக்க வேண்டும் | 10 நிமிடம் |
| 2 | Hotkey auto (AutoHotkey இல்லாமல்) | Windows shortcut key | 20 நிமிடம் |
| 3 | Nila மூடியிருக்கும்போது "நிலா ஸ்டார்ட்" | Windows service / startup shortcut | 1 மணி |
| 4 | Background mic (wake word) | **வேண்டாம்** — battery/privacy | — |
| 5 | Voice input (நீங்கள் பேசுவது) | Whisper/Vosk | 1 நாள் |

### 6.2 கோப்பு / வீடியோ
| # | விஷயம் | தீர்வு |
|---|---|---|
| 6 | Scan செய்த PDF (OCR) | `tesseract` install (optional) |
| 7 | Image உள்ளடக்கம் | vision model (Gemini vision / local llava) |
| 8 | Video scene detect | `ffmpeg` install → நான் இணைக்கலாம் |
| 9 | Video transcript | Whisper → subtitle தேடல் |
| 10 | 2 GB+ text file | streaming read (chunk) |
| 11 | Excel formula/date | cell type parse |

### 6.3 அன்றாட பயன்பாடு (நீங்கள் சொன்ன BASIC)
| # | விஷயம் | நிலை |
|---|---|---|
| 12 | App திற ("Chrome திற") | ❌ இல்லை — `run_command` permission உடன் |
| 13 | File தேடு ("அந்த file எங்கே") | ❌ இல்லை — `list_directory` உள்ளது, search இல்லை |
| 14 | Clipboard | ❌ |
| 15 | Screenshot | ❌ |
| 16 | Volume / brightness | ❌ |
| 17 | Music | **v3** (நீங்கள் சொன்னபடி) |

### 6.4 நம்பகத்தன்மை
| # | விஷயம் | தீர்வு |
|---|---|---|
| 18 | Gemini model பெயர் யூகம் | probe + fallback |
| 19 | Free tier limit | ledger உள்ளது; UI-ல் காட்ட |
| 20 | Multi-provider label | model pack உள்ளது |
| 21 | Auto backup | schedule |
| 22 | Telegram → phone notification | 2 மணி |
| 23 | Startup time | 3 கட்டளை → 1 (முடிந்தது) |

---

## 7. ⚖️ **பேலன்ஸ் — மீதி என்ன இருக்கு**

| முடிந்தது (v0.2) | மீதி (v2.1) | v3 |
|---|---|---|
| Vault · memory · 40 tools | Voice input | Music |
| Reminder · calculator | File search / app open | Screen reading |
| Calendar read · voice out | Telegram (phone) | Gmail/Drive OAuth |
| 10 மொழி படிப்பு | OCR / vision | Android app |
| Free connectors | Video scene detect | Multi-user |
| Model pack | Auto backup | Fine-tuned Tamil |
| **Voice lock (ஒரே குரல்)** | Real-voice calibration | Wake word |
| **File/video analysis** | | |
| **D: storage** | | |

**சுருக்கம்:** v0.2-ல் **8 ஸ்டேஜ்** முடிந்தது. **1042 விஷயங்கள்** இல்லை — ஆனால் **முதன்மையான 9** மட்டும் (voice input, file search, app open, Telegram, OCR, vision, ffmpeg, auto backup, real-voice test).

**என் பரிந்துரை:** அடுத்து **Voice input (push-to-talk) + File search** — இரண்டும் "நான் சொல்லச் சொல்ல action" கேள்விக்கு நேரடி பதில்.

தொடங்கட்டுமா?
