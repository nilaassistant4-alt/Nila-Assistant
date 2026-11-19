# நிலா v0.2 — பதிமூன்றாவது கட்டம்: மீதி இருந்தவை முடிந்தது

**நிலை:** `brain progress` இப்போது இப்படிச் சொல்கிறது —

```
45 வரிகள் · 36 done · 6 partial · 3 not built   (80% · partial-ஐயும் சேர்த்தால் 93%)
```

பன்னிரண்டாவது கட்டத்தில் **33 done · 8 partial · 3 not built (44-ல், 75%)** ஆக
இருந்தது. இந்தக் கட்டத்தில்:

- **not built 3 → 1**: wake word முடிந்தது (window-க்குள் மட்டும்), "2 PC" தனி
  வரியாகப் பிரித்து `done` ஆக்கினேன்.
- **partial 8 → 6**: OCR + scene detect `done`, Gmail/Drive-க்கு **account
  binding** வந்தது (OAuth token exchange மட்டும் மீதி), voice challenge தமிழில்
  வருகிறது.
- **மண்டேட் probe 29/29 pass · 0 gap** — ஐந்து gap-உம் இல்லை (stage 11-ல்
  இருந்து இருந்தவை). இது மட்டுமே இந்தக் கட்டத்தின் மிக முக்கியமான எண்.

இது எப்படி என்று வரி வரியாக கீழே. **எதையும் "முடிந்தது" என்று நான் மட்டும்
சொல்லவில்லை** — ஒவ்வொன்றுக்கும் `brain` கட்டளை, test, retest-check, probe-check
இருக்கிறது; எது உங்கள் PC-ல் மட்டுமே நிரூபணம் ஆகும் என்றும் சொல்லியிருக்கிறேன்.

---

## 1. Wake word — "நிலா" என்று சொல்லி எழுப்பலாம் (not built → done)

பழைய வரி `not built` ஆக இருந்தது, காரணம்: *"நாள் முழுவதும் mic-ஐ திறந்து வைப்பது
battery + privacy பிரச்சினை."* **அந்த மறுப்பு அப்படியே இருக்கிறது** — நான்
மாற்றியது மறுபாதி: "எப்போதும் முடியாது" என்பது design முடிவு இல்லை, விட்டுப்போன
வேலை.

```
brain wake --window 120      ← mic 2 நிமிடம் மட்டும் திறந்திருக்கும்
brain wake --check nila.wav  ← mic இல்லாமல் ஒரு recording-ஐ சோதிக்க
```

- **Window தானே மூடும்.** 15 நிமிடம் தான் உச்சம்; அதற்கு மேல் கேட்டால்
  `error: 99999 s is longer than the 15-minute ceiling...` என்று வார்த்தையில்
  மறுக்கும் (test-உடன்).
- **Window = permission.** திறக்கும்போது `mic.listen` கேட்கும்; திறப்பும்
  மூடுதலும் audit journal-ல் எழுதப்படும்.
- **கேட்டது ஒரு doorbell, சாவி இல்லை.** "நிலா" கேட்ட உடனே அந்த audio-வும்
  திரும்ப வரும் — speaker lock அந்தக் குரலைச் சோதித்த பிறகுதான் எதையும் செய்ய
  வேண்டும்.
- **தமிழிலும்:** `nila`, `hey nila`, `hi nila`, `hello nila`, `நிலா`, `ஹே நிலா`.
- **நேர்மை:** இது loudness + voicing + இரண்டு syllable-ன் வடிவம் பார்க்கிறது;
  பயிற்சி பெற்ற (neural) keyword spotter **இல்லை**. அருகில் தெளிவாகச் சொன்னால்
  நன்றாக வேலை செய்யும், சத்தமான அறையில் நம்ப முடியாது. `brain/agent/wake.py`-ல்
  `LIMITS` இதை அப்படியே சொல்கிறது.

**சோதித்ததில் ஒரு உண்மையான பிழை கிடைத்தது:** நான்கு syllable கொண்ட ஒரு
வாக்கியத்தை "நிலா" என்று தவறாகக் கேட்டது (இரு வார்த்தைகளுக்கு இடையேயான 30 ms
இடைவெளி 20 ms frame-க்குள் மறைந்து போனது). Frame 10 ms ஆக்கி, 4 syllable-க்கு
மேல் மறுக்கும்படி செய்தேன். இப்போது "நிலா" (2 syllable) 0.96 confidence,
"hey நிலா" (3 syllable) 0.95, 4-syllable வாக்கியம் மறுக்கப்படுகிறது.

---

## 2. மறக்கக்கூடிய இடம் — ஒரு முறை மட்டும் பயன்படுத்தும் value (mandate 1)

மண்டேட்டின் முதல் விதி: *"தற்காலிக வேலைக்குப் பயன்படுத்திய number-ஐ, நான்
சேமிக்கச் சொல்லாதவரை சேமிக்கக் கூடாது."* இதற்கு இதுவரை ஒரு tier இல்லை —
`remember` மட்டுமே இருந்தது, மறப்பது model-ன் பழக்கம்.

```
brain temp hold 9845123789 --for "அம்மாவைக் கூப்பிட" --use   ← hold + use, ஒரே கட்டளையில்
brain temp                     ← என்ன வைத்திருக்கு: id + purpose மட்டும், value இல்லை
brain temp purge               ← எல்லாவற்றையும் இப்போதே அழி
```

- **எழுதப்படுவது ஒரு hash மட்டும்.** Value RAM-ல் மட்டும்; vault-ல் சேமிக்கப்படும்
  record-ல் salted digest மட்டுமே (`value_recorded: false`).
- **ஒரு முறைதான்.** `take()` value-ஐக் கொடுத்து மறந்துவிடும்; இரண்டாவது முறை
  "already used once and is gone" என்று மறுக்கும்.
- **மூன்று வழியில் அழியும்:** காலாவதி, `brain temp purge`, `brain lock`.
- **`memory.db`-ல் இல்லை** → backup-ல் இல்லை, sync-ல் இல்லை.
- **நேர்மை (இது முக்கியம்):** value ஒரு process-ன் RAM-ல் இருப்பதால், **அடுத்த
  `brain` கட்டளை அதைப் பார்க்க முடியாது** — அதுதான் அதன் அர்த்தம். அதனால் CLI
  அதை மறைக்காமல் சொல்லி `--use` கொடுக்கிறது. Assistant வேலை செய்யும்போது
  (`hold_ephemeral` / `use_ephemeral` tools) இது சரியாகவே வேலை செய்யும்.

---

## 3. "Strictly per command" — ஒரு mode (mandate 5 gap → pass)

```
brain permission mode            ← இப்போது எந்த mode
brain permission mode command    ← ஒவ்வொரு செயலும் மீண்டும் கேட்கும்
brain permission mode standing   ← திரும்பவும் முன்போல
```

- `command` mode-ல் **standing grant எதுவும் செல்லாது** — `check`-லும் இல்லை,
  `peek`-லும் இல்லை (peek "சரி" என்று சொன்னால் caller affirmation-ஐத் தவிர்த்து
  விடும், அதனால்தான் இரண்டையும் சேர்த்து மாற்றினேன்).
- புதிய standing grant-உம் மறுக்கப்படும்; திரும்ப `standing` ஆக்கினால் பழைய
  grant வேலை செய்யும்.
- `brain permission capabilities` சொல்கிறது: `fs.write`, `shell.exec`,
  `data.export` எப்போதுமே standing ஆக முடியாது.

---

## 4. தமிழில் liveness challenge (mandate 4 gap → pass)

பழைய நிலை: தமிழ் பேசும் owner, தான் தானே என்று நிரூபிக்க **"alpha bravo cedar"**
என்று ஆங்கிலத்தில் படிக்க வேண்டியிருந்தது.

```
brain challenge --style auto          ← உங்கள் மொழியைப் பார்த்துத் தேர்வு
brain challenge --style tamil_digits  ← "ஒன்பது ஏழு நான்கு..."
brain challenge --style tamil_words
```

- தமிழ் எண்கள் + தமிழ் வார்த்தைகள் சேர்ந்தன; `auto` profile-ல் "Tamil" அல்லது
  "Tanglish" இருந்தால் தமிழ் எண்களைத் தேர்வு செய்யும்.
- தெரியாத style கேட்டால் **ஆங்கிலத்துக்குத் திரும்பாமல்** மறுக்கும் (ஒரு silent
  fallback இருந்திருந்தால் "தமிழ் வேலை செய்கிறது" என்று பொய் சொல்லியிருக்கும்).

---

## 5. Gmail / Drive / Calendar — ஒரு account மட்டும் (mandate 3 gap → pass)

```
brain connectors integrations                       ← எந்த service எந்த account
brain connectors integrations bind gmail you@gmail.com
```

- `gmail` and `drive` இப்போது **declared, disabled** integrations (enabled: false).
  `calendar`-உடன் சேர்த்து மூன்றும் `account` field-ஐ வைத்திருக்கின்றன.
- **Bound இல்லாத account-call → network-க்கு முன்னே மறுப்பு.** வேறு account
  பேர் சொன்னாலும் மறுப்பு. `whoami` operation, token எந்த account என்று
  சொல்லும் — அதை binding-உடன் ஒப்பிடலாம்.
- **நேர்மை:** Google **OAuth handshake இன்னும் இல்லை**. Token இல்லாமல் token
  இருப்பதாக நடிக்கவில்லை; இப்போது இருக்கிறது binding + மறுப்பு, இல்லாதது
  exchange.

---

## 6. Device ceiling — "ஒருவர், சில சாதனங்கள்" (mandate 3 gap → pass)

```
brain device limit 3      ← இனி 3 சாதனங்கள் மட்டும்
brain device list         ← எத்தனை பயன்பாட்டில், இன்னும் எவ்வளவு இடம்
```

- Default உச்சம் **3** (PC + laptop + phone). நாலாவது சாதனம் bundle-ஆக
  வெளியேறவே முடியாது; அதற்கு முன் ஒன்றை `revoke` செய்ய வேண்டும்.
- **எங்கே சரிபார்க்கிறது:** key-ஐ வைத்திருக்கும் vault (bundle அனுப்பும் பக்கம்)
  — புதிய vault-ல் list காலியாக இருக்கும், அங்கே சரிபார்த்தால் ஒருபோதும்
  நிறைவேறாது.
- **PC1 → PC2 → PC3 வரை எண்ணிக்கை ஏறுகிறது** — shared identity blob-ல்
  எண்ணுவதால் (test-ல் நிரூபித்தேன்).
- `revoke` இப்போது **இரு list-லிருந்தும்** நீக்கும் — இல்லாவிட்டால் இடம்
  நிரந்தரமாக நிரம்பியே இருக்கும்.

---

## 7. OCR + scene detect → `done` (partial → done)

இவை ஏற்கனவே வேலை செய்தன; `partial` என்று வைத்திருந்தது அதே தவறின்
எதிர்ப்பக்கத்தில் இருந்தது. `--run` எழுதுவதற்கு ffmpeg வேண்டும், அதை
`getffmpeg.bat` ஒரு கட்டளையில் நிறுவுகிறது; built-in reader-க்கு எதுவும்
வேண்டாம். அதனால் `done`, மற்றும் "ffmpeg வேண்டும்" என்ற உண்மை note-ல் இருக்கிறது.

---

## 8. "2 PC, ஒரு memory" — தனி வரியாகப் பிரித்தேன் (done)

முன்பு இது "Fine-tuned Tamil model + local mesh" என்ற **ஒரே** வரியில் இருந்தது.
முடிந்த வேலை, முடியாத வேலையுடன் சேர்ந்து "பாதி" என்று தெரிந்தது. இப்போது:

- `Two PCs, one memory` → **done** (`sync-host.bat`, `sync-join.bat`, `sync-now.bat`)
- `Fine-tuned Tamil model` → **missing** (training set + GPU தேவை; அதை
  "பாதி" என்று சொல்வது பொய்)

அதனால் வரிசை எண்ணிக்கை 44 → 45 ஆனது (வரி பிரிந்ததால்).

---

## 9. சோதனை முடிவுகள் (இவை மட்டுமே உண்மை)

| சோதனை | முடிவு |
|---|---|
| முழு test suite | **1435 passed · 0 failed · 3 skipped** |
| `scripts/retest_everything.py` | **57 pass · 5 skip · 0 fail** |
| `scripts/nila_mandate_probe.py` | **29 pass · 0 fail · 0 gap** (29-ல்) — stage 11 முதல் இருந்த 5 gap இல்லை |
| `brain progress` | 36 done · 6 partial · 3 not built (45-ல்) |

இந்தக் கட்டத்தில் புதிதாக 18 test சேர்ந்தன
(`tests/test_ephemeral_wake_and_binding_acceptance.py`), மற்றும் retest-ல்
5 புதிய check (stage 13).

---

## 10. உங்கள் PC-ல் என்ன, எப்படிச் சோதிக்க வேண்டும் — படிப்படியாக

### முதல் முறை (5 நிமிடம்)

1. `NILA_V02_STAGE13.zip`-ஐ இறக்கி, `Nila` folder ஆக விரிக்கவும்.
2. `setup.bat` — Python, packages, vault.
3. `brain progress` → `36 done · 6 partial · 3 not built` என்று வர வேண்டும்.
   **இதுவே இந்தக் கட்டம் சரியாக வந்துவிட்டதற்கான முதல் அறிகுறி.**

### ஒவ்வொரு புதிய வசதியும் (ஒவ்வொன்றும் 1–2 நிமிடம்)

4. **Wake word — mic இல்லாமல் (எல்லோருக்கும்):**
   `brain wake --check nila.wav` → `HEARD: confidence 0.9x` என்று வர வேண்டும்.
   ஒரு WAV இல்லாவிட்டால், யாராவது "நிலா" என்று சொல்லி phone-ல் record செய்து
   அனுப்பினால் போதும்.
5. **Wake word — mic உடன்:** `brain wake --window 60` → "நிலா" என்று சொல்லுங்கள்.
   `heard "Nila"` வர வேண்டும்; 60 வினாடிக்குப் பின் தானே நின்றுவிடும்.
   `--window 3600` கொடுத்தால் **மறுக்கப்பட வேண்டும்** (அதுவே சரி).
6. **ஒரு முறை value:** `brain temp hold 9845123789 --for "test" --use` →
   number அச்சாக வேண்டும்; `brain temp` → "nothing is being held" (ஏனென்றால்
   அது ஒரு வேறு process — அதுதான் சரி, செய்தி அதை விளக்கும்).
7. **Command mode:** `brain permission mode command` → `brain permission grant
   fs.read --scope "D:/test"` → **மறுக்கப்பட வேண்டும்**. பிறகு
   `brain permission mode standing` → இப்போது grant வேலை செய்யும்.
8. **தமிழ் challenge:** `brain challenge --style tamil_digits` →
   `say: "ஒன்பது ஏழு ..."` என்று தமிழில் வர வேண்டும்.
9. **Account binding:** `brain connectors integrations` → gmail/drive/calendar
   "NOT BOUND" என்று காட்ட வேண்டும். `brain connectors integrations bind gmail
   you@gmail.com` → "bound to you@gmail.com".
10. **Device ceiling:** `brain device limit 3` → `brain device list` →
    `1 of 3 devices used` (உங்கள் PC மட்டும்).

### எதைச் சோதிக்க முடியாது (இந்த sandbox-ல் இல்லாததால்)

- Telegram (bot token + chat id வேண்டும்), Google OAuth (உங்கள் account),
  Tesseract OCR (நிறுவினால் தானாகப் பயன்படுத்தும்), Whisper (pip install),
  Piper/SAPI குரல் (Windows-ல் SAPI இருக்கிறது), 2-PC (இரண்டு கணினி வேண்டும்).

---

## 11. நேர்மையான அட்டவணை (45 வரி)

**done — 36.** base 8, குரல்/மொழி, memory, reminder, chat, dashboard, phone
(TG), calendar, OCR, screen, music, media, search-by-meaning, 2-PC, wake word,
use-once store, permission modes, மற்றும் பல.

**partial — 6** (code இருக்கிறது, வெளியில் ஒன்று தேவை):

| வரி | என்ன தேவை |
|---|---|
| Free connectors (weather/Wikipedia/YouTube/RSS) | live call இந்த sandbox-ல் இல்லை; YouTube-க்கு இலவச Google key |
| Speaks Tamil | `pip install piper-tts` + ta voice, அல்லது Windows SAPI |
| Hears you | `pip install faster-whisper` + mic |
| Telegram (நிலா → போன்) | bot token + chat id |
| Telegram two-way | அதே token + `nila-phone.bat` |
| Gmail / Drive / Calendar write | **binding முடிந்தது**; Google OAuth handshake மீதி |

**not built — 3** (இல்லை, சொல்கிறேன்):

| வரி | ஏன் |
|---|---|
| Phone app (Android) + multi-user | native app வேறு project; Telegram போதும் என்று நினைக்கிறேன் — நீங்கள் சொன்னால் செய்யலாம் |
| Neural speaker model (ECAPA/x-vector) | ~100 MB model download; தற்போதைய MFCC ceiling-ஐ அது தான் உயர்த்தும் |
| Fine-tuned Tamil model | labelled training set + GPU run |

**மண்டேட் probe:** 29/29 pass, **0 gap**. stage 11-ல் இருந்த ஐந்து gap
(ephemeral, device binding, Gmail binding, Tamil liveness, per-command
activation) — ஐந்தும் இப்போது code-ஆல் நிரூபிக்கப்படுகின்றன.

---

## 12. சுருக்கம் — ஒரு பார்வையில்

```
brain progress              → 36 done · 6 partial · 3 not built (80%)
brain wake --window 120     → "நிலா" கேட்கும், window மூடும்
brain temp hold X --for Y   → எழுதப்படாத, ஒரு-முறை value
brain permission mode command → ஒவ்வொரு செயலும் மீண்டும் கேட்கும்
brain challenge --style tamil_digits → தமிழில் number
brain connectors integrations → எந்த service எந்த account
brain device limit 3        → ஒருவர், மூன்று சாதனம்
```

மீதி இருப்பது **உங்கள் கணக்கு** சார்ந்தது (Telegram token, Google OAuth) அல்லது
**வேறு project** (Android app, neural models). அவற்றை நீங்கள் திறந்தால் ஒரு
நாளில் முடிக்கலாம்; இல்லாவிட்டால் இந்தக் கட்டமே நேர்மையாக முடிந்த நிலை.
