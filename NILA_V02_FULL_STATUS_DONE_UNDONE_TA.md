# முழுப் பட்டியல் — முடிந்தது / முடிக்காதது / முடிக்க முடியாதது (எதனால்)

**நாள்:** 2026-11-04 · **கிளை:** `arena/3f2334a4-nila-assistant` · உறுதி: `5510695`
**அடிப்படை:** ZIP `Nila-verification-v0.2-ab-included.zip` · suite **831 passed, 3 skipped**

---

## 0. சுருக்கம் (ஒரு பார்வை)

| | எண்ணிக்கை |
|---|---|
| **முடிந்தது** (வேலை செய்கிறது + டெஸ்ட் உண்டு) | **17** |
| **முடிக்காதது** (கட்ட முடியும், நீங்கள் சொன்னால் கட்டுவேன்) | **11** |
| **முடிக்க முடியாதது** (இந்த வேலையில்/இந்த அமைப்பில் சாத்தியமில்லை) | **6** |
| **வேறு வழி உண்டு** (நேரடியாக முடியாதது, மாற்று வழியில் முடியும்) | **5** |

> "முடிக்க முடியாதது" என்பது *உங்கள் கனவு தவறு* என்று அர்த்தம் அல்ல — **என் கையில் இல்லாத
> ஒன்று** என்று அர்த்தம். ஒவ்வொன்றுக்கும் "ஏன்" + "வேறு வழி" கீழே இருக்கிறது.

---

## 1. ✅ முடிந்தது (17) — இவை *இப்போது* வேலை செய்கின்றன

| # | விஷயம் | எப்படிச் சொல்லலாம் | டெஸ்ட் |
|---|---|---|---|
| 1 | Vault (encrypted, scrypt, TOTP + voice) | `brain init`, `brain unlock` | ✅ |
| 2 | Two-factor: passphrase + authenticator / voice | `brain totp`, `brain enroll-voice` | ✅ |
| 3 | Voice **அடையாளம்** (replay தடை, lockout) | unlock-ல் | ✅ |
| 4 | Memory (encrypted, remember/recall/search, Tamil text) | `brain remember`, `brain recall` | ✅ |
| 5 | **#2 standing instructions** (10 வரை, 200 எழுத்து) | `brain instruct add …` | ✅ 38 owner tests |
| 6 | **#3 connectors screen** (AI + integrations, on/off) | `brain connectors`, `GET /v1/connectors` | ✅ |
| 7 | Connector step-up (fresh login, single use, 403/404/423) | `POST /v1/connectors/<id>/enable` | ✅ |
| 8 | **A. bug capture** — "எனக்கு இந்தப் பிரச்சனை" → சுத்தமான அறிக்கை | `brain issue "…" --write --yes` | ✅ 37 tests |
| 9 | **B. profile** — callme / language / work (online-க்குப் போகாது) | `brain profile set callme "அண்ணா"` | ✅ |
| 10 | **B. strict mode** — code-ல் gate, "yes" = ஒரு வேலை | `brain strict on` | ✅ |
| 11 | Online/offline router (budget, key gate, secret screening) | `brain config`, `brain connectors` | ✅ |
| 12 | Audit log (hash chain, tamper detection) | `brain audit` | ✅ |
| 13 | Backup / restore (encrypted) | `brain backup`, `brain restore` | ✅ |
| 14 | Sync (end-to-end encrypted, local blob store) | `brain sync` | ✅ |
| 15 | Local HTTP API + dashboard (127.0.0.1, bearer token) | `brain serve` | ✅ |
| 16 | Doctor / report / **calculate** tool (இயங்குகிறது) | `brain doctor`, "2+2" | ✅ |
| 17 | Updates channel, device enrolment, recovery codes | `brain update`, `brain device` | ✅ |

---

## 2. ⏳ முடிக்காதது (11) — *உங்கள் பட்டியலில் உள்ளது, கட்டப்படவில்லை*

| # | விஷயம் | இப்போது நிலை | கட்ட எவ்வளவு |
|---|---|---|---|
| 1 | **Reminders** (நேரம், repeat, snooze, PC notification) | இல்லை — "reminder" வார்த்தை memory-ல் போகிறது, **நேரத்தில் அடிக்காது** | நடுத்தரம் |
| 2 | **Calculator on PC** (இயற்கை மொழியில் "18500*12 + 10%") | tool *உள்ளது* (`calculate`) ஆனால் `% ` இல்லை, 2+2 மட்டும்; **"calculator" screen இல்லை** | சிறியது |
| 3 | **Google Calendar** (event + alert) | இல்லை (plugin framework உண்டு) | நடுத்தரம் + OAuth |
| 4 | **Voice output — Nila பேசுவது (TTS)** | **இல்லை** (மிகப்பெரிய இடைவெளி) | பெரியது |
| 5 | **Voice picker + speed/pitch** | இல்லை | நடுத்தரம் |
| 6 | **Push-to-talk** (mic-ல் கேட்டு, பதில் சொல்ல) = STT | **இல்லை** (mic capture helper மட்டும்) | பெரியது |
| 7 | **Phone PWA + passkey** | இல்லை (static-ல் `manifest.json`, service worker இல்லை) | நடுத்தரம்-பெரியது |
| 8 | **Agent profiles + task router** | இல்லை | நடுத்தரம் |
| 9 | **Storage tiers** 7 நாள் / 90 நாள் / நிரந்தரம் + வாராந்திர "delete these?" | இல்லை — **எதுவும் தானாக அழியாது** (purging code-ஏ இல்லை) | நடுத்தரம் |
| 10 | **Mail (read-only)** | இல்லை | நடுத்தரம் + OAuth |
| 11 | **YouTube analysis**, **"Hey Nila"** (ஒரு device) | இல்லை இரண்டும் | பெரியது / முடியாதது-க்கு அருகில் |

*(#11-ல் "Hey Nila" கீழே பிரிவு 3-ல் விவரிக்கப்பட்டுள்ளது.)*

---

## 3. 🚫 முடிக்க **முடியாதது** (6) — ஏன், வேறு வழி என்ன

### 3.1 "Phone primary" — PC off ஆனால் phone-ல் Nila இயங்காது
**ஏன்:** Nila ஒரு **PC-ல் இயங்கும் program** (Python + உங்கள் vault + memory.db).
Phone-ல் அதே program இல்லை; PC off ஆனால் அது இல்லை.
**வேறு வழி:** ① PC-ஐ on வைத்திருங்கள் (Wake-on-LAN / mini-PC / Raspberry Pi — 24×7, 5W).
② Google Calendar-ல் event போட்டால் **Google-ஏ** reminder அனுப்பும் (Nila அல்ல) — PC off-லும் அடிக்கும்.
③ பிறகு ஒரு **சிறிய phone edition** (கீழே 3.3).

### 3.2 "Phone offline-ல் Nila" — நான் இப்போது செய்ய முடியாதது
**ஏன்:** Android-ல் background-ல் எப்போதும் இயங்கும் Python app கட்ட முடியாது (Play Store rule + battery).
**வேறு வழி:** PC-ஐ server ஆக வைத்து phone browser-ல் (PWA) பயன்படுத்துவது — **PC on இருக்க வேண்டும்**.
முழு offline phone edition = தனி Android app (Kotlin/Flutter) — இது **v0.3 கதை**, v0.2 அல்ல.

### 3.3 Third-party app-ல் **background "Hey Nila"** (Android)
**ஏன்:** Android 12+ background microphone-ஐ கட்டுப்படுத்துகிறது; "Hey Google" **Google-ன் சொந்த**
privilege. வேறு app background-ல் hotword கேட்க முடியாது (அல்லது battery-ஐ கொல்லும்).
**வேறு வழி:** ① app **திறந்திருக்கும்போது மட்டும்** "Hey Nila" (இது சாத்தியம் — நீங்கள் சொன்னது
"phone while app open" — அதற்கு ஒத்துக்கொள்கிறேன்). ② PC-ல் எப்போதும் கேட்கலாம் (mic + local model).
③ **Push-to-talk button** (மிக நம்பகமானது).

### 3.4 இந்த sandbox-ல் **internet இல்லை** → Google Calendar / Mail / YouTube-ஐ இங்கே டெஸ்ட் செய்ய முடியாது
**ஏன்:** இந்த இயந்திரத்தில் `github.com`/`api.github.com` மட்டும் திறந்திருக்கிறது;
`generativelanguage.googleapis.com`, `youtube.com` TLS-ல் மறுக்கப்படுகிறது (நான் ஓட்டிப் பார்த்தேன்).
**வேறு வழி:** code-ஐ **mock server** வைத்து டெஸ்ட் செய்வேன் (உண்மையான HTTP shape-உடன்),
உண்மையான OAuth-ஐ **உங்கள் PC-ல்** நீங்கள் ஓட்டுவீர்கள். அதனால் "என் இயந்திரத்தில் ஓடியது"
என்று நான் பொய் சொல்ல மாட்டேன் — mock-ல் ஓடியது என்று சொல்வேன்.

### 3.5 **Voice cloning** — வேண்டாம் என்று *நீங்களே* சொன்னீர்கள்; தொழில்நுட்பமும் இல்லை
**ஏன்:** (அ) உங்கள் கட்டளை; (ஆ) cloning = உங்கள் குரலைப் பதிவு செய்து மற்றவர் பேச வைப்பது — மோசடிக்கு
நேரடி வழி; (இ) Nila-வின் model-ல் அது இல்லை.
**வேறு வழி:** Windows/phone **built-in Tamil+English குரல்** (free, offline, private) — நீங்கள் கேட்டது இதுதான்.

### 3.6 **Nila-ஆல் GitHub issue உருவாக்குவது** (இப்போது) — நான் செய்யவில்லை
**ஏன்:** (அ) நீங்கள் "இஷ்யூஸ் அப்படியே இருக்கட்டும்" என்றீர்கள்; (ஆ) token இல்லாமல் GitHub-ல் எழுத முடியாது;
(இ) தானாக அனுப்புவது = உங்கள் தரவு அனுமதியில்லாமல் வெளியே போவது.
**வேறு வழி:** `brain issue` **file** தரும் → நீங்கள் attach செய்யுங்கள். பிறகு (நீங்கள் ok சொன்னால்)
ஒவ்வொரு முறையும் ஒப்புதல் கேட்டு GitHub API-ல் அனுப்பும் ஆப்ஷன் சேர்க்கலாம் — **v0.3**.

---

## 4. ♻️ "நேரடியாக முடியாது, ஆனால் வேறு வழி உண்டு" (5)

| முடியாதது | ஏன் | வேறு வழி (நான் கட்டக்கூடியது) |
|---|---|---|
| Windows-ல் **Nila பேசுவது** (நல்ல குரல்) | Python-ல் built-in TTS இல்லை; cloud TTS = உங்கள் text internet-ல் போகும் | ① Windows-ன் **SAPI voice** (offline, free, Tamil குரல் Windows-ல் இன்ஸ்டால் செய்யலாம்) ② `pyttsx3` (offline) ③ cloud குரல் **பொது பதில்களுக்கு மட்டும்** (தனி switch, default OFF) |
| **Hey Nila** phone-ல் எப்போதும் | Android rule (3.3) | app திறந்திருக்கும்போது + PC-ல் எப்போதும் + push-to-talk |
| **Calendar alert** PC off-லும் | Nila இயங்கவில்லை | event-ஐ Google Calendar-ல் போடு → **Google-ஏ** alert அனுப்பும் |
| **Mail** படிப்பது | Gmail OAuth தேவை + sandbox-ல் network இல்லை | **read-only** scope (`gmail.readonly`), mock-ல் டெஸ்ட், உண்மையானது உங்கள் PC-ல். அனுப்பவே முடியாது (scope-லேயே இல்லை) |
| **Trending YouTube** | YouTube "trending" API-க்கு quota/பணம்; sandbox-ல் network இல்லை | ① உங்கள் **சொந்த channel** analytics (read-only, free quota) ② RSS feed (free, quota இல்லை) |

---

## 5. "எதனால் முடியாது" — ஒரே வரியில் காரணங்கள்

| காரணம் | எதைத் தடுக்கிறது |
|---|---|
| **PC-ல் தான் program இயங்கும்** | phone offline, PC off-ல் Nila |
| **Android background mic rule** | phone-ல் எப்போதும் "Hey Nila" |
| **Cloud API = text internet-ல் போகும்** | online TTS, cloud STT (privacy) |
| **இந்த sandbox-ல் network இல்லை** | Calendar/Mail/YouTube-ஐ இங்கே உண்மையாக டெஸ்ட் செய்வது |
| **Python-ல் offline TTS/STT built-in இல்லை** | voice output/input-ஐ dependency இல்லாமல் செய்வது |
| **உங்கள் கட்டளை (cloning வேண்டாம்)** | voice cloning |
| **OAuth + உங்கள் கணக்கு** | Calendar/Mail — நான் உருவாக்க முடியாது, நீங்கள் authorize செய்ய வேண்டும் |

---

## 6. நான் பரிந்துரைக்கும் வரிசை (நீங்கள் "சின்ன சின்ன" என்றீர்கள்)

1. **Calculator முழுமை** (சிறியது, 1 ஸ்டேஜ்): `%`, வட்டி, GST, தமிழ் வார்த்தை ("கூட்டு"), screen.
2. **Reminders** (PC, offline): நேரம் + repeat + snooze + PC notification + restart-க்குப் பிறகும்.
3. **Voice output** (Windows SAPI offline) → பிறகு voice picker + speed/pitch.
4. **Push-to-talk** (mic → text; offline Whisper இல்லாவிட்டால் Windows built-in recognition).
5. **PWA** (phone browser, PC on) → பிறகு passkey.
6. **Google Calendar** (read+create, தனி permission) → **Mail read-only** → **YouTube (உங்க channel)**.
7. **Agent profiles + task router** → **storage tiers** (எதுவும் அனுமதியில்லாமல் அழியாது).
8. "Hey Nila" — PC-ல் எப்போதும், phone-ல் app திறந்திருக்கும்போது மட்டும்.
