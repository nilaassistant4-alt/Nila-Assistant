# நிலா v0.2 — ஒன்பதாவது கட்டம்: போன், மற்றும் உண்மையான முழுமை எண்

**நிலை:** கட்டம் 9 முடிந்தது. கீழே சொல்லப்பட்டது எல்லாம் இந்தக் கட்டத்தில்
உருவாக்கப்பட்டது, சோதிக்கப்பட்டது. **எதுவும் GitHub-க்கு அனுப்பப்படவில்லை** —
நீங்கள் சொன்ன பிறகுதான் `git push`.

---

## 1. போனுக்கு ஒரு செய்தி (Telegram)

நீங்கள் கேட்டது: *"எதுவாக இருந்தாலும் — ஒரு அப்டேட், ஒரு பிரச்சனை — எனக்கு
Telegram-ல ஒரு மெசேஜ் போகணும்."*

இப்போது நாலு வகையான செய்திகள் போகும், ஒவ்வொன்றையும் தனித்தனியாக நிறுத்தலாம்:

| வகை | எப்போது போகும் |
|---|---|
| `reminders` | நினைவூட்டல் நேரம் வந்ததும், தவறவிட்டது தெரிந்ததும் — **urgent** |
| `errors` | ஒரு செயல் தோல்வியடைந்ததும் |
| `updates` | நிலா தொடங்கியதும் ("Nila started on your PC.") |
| `reports` | சுருக்கம் / வாராந்திர தகவல் |

**அமைக்கும் வழி (ஐந்து படிகள், `brain telegram how` இதையே காட்டும்):**

```
brain telegram token 123456789:AA…    ← BotFather கொடுத்தது
   (போனில் @BotFather → /newbot → பெயர் → token)
போனில் போட்டின் chat-ல் Start அழுத்துங்கள்
brain telegram whoami                 ← உங்கள் chat id-ஐ நிலா படித்துச் சொல்லும்
brain telegram chat 987654321
brain telegram test                   ← உண்மையாக ஒரு மெசேஜ் வர வேண்டும்
```

**போன் நம்பர் தேவையில்லை.** Telegram கணக்கு ஒரு நம்பருடன் இணைந்தது உண்மைதான்,
ஆனால் போட்டுக்கு அது தேவையில்லை. நிலாவுக்குத் தேவை: **bot token** + உங்கள்
**chat id** (987654321 போன்ற ஒரு எண்). நீங்கள் போன் நம்பர், Telegram login, SIM
எதுவும் கொடுக்க வேண்டாம்; நிலா எதையும் சேமிக்காது. போட் முதலில் யாருக்கும்
செய்தி அனுப்ப முடியாது (Telegram விதி) — அதனால்தான் "Start" ஒருமுறை
அழுத்த வேண்டும்.

**Token ஒரு கடவுச்சொல்.** அது vault-க்குள் மறையூட்டப்பட்டே இருக்கும் —
`config.json`-ல் இல்லை. திரையில் `123456…wXyZ` என்றே தெரியும் (முழு token
எங்கும் அச்சிடப்படாது). அது `api.telegram.org`-க்கு மட்டுமே போகும்; வேறு host-க்குப்
போகும் கோரிக்கை கிளம்பும் முன்னே மறுக்கப்படும். பிழைச் செய்தியிலோ log-லோ வந்தால்
`issue report` அதை `<TELEGRAM_TOKEN>` என்று மாற்றிவிடும்.

**போன் ஆஃப்லைனில் இருந்தால் செய்தி தொலைந்துவிடாது.** vault-க்குள் (மறையூட்டி)
சேமிக்கப்படும் — புதிய 50 வரை. அடுத்த முறை இணைப்பு வந்ததும், அல்லது
`brain telegram flush` போட்டதும், வரிசையாகப் போகும். `brain telegram queue`
என்ன காத்திருக்கிறது என்று காட்டும்.

**ஒரு திசை மட்டுமே, வேண்டுமென்றே.** நிலா உங்கள் போனுக்கு எழுதும்; நீங்கள்
அனுப்பும் பதிலைப் படிக்காது. போட்டைப் படித்து பதில் சொல்ல ஒரு நிரல் நாள் முழுவதும்
இணைப்பைத் திறந்து வைக்க வேண்டும் — அது இன்னும் கட்டப்படவில்லை. அதனால்
`brain progress`-ல் "Telegram two-way" என்பது **missing** என்றே உள்ளது.
`brain launcher --plan`-லும் இது அதே வார்த்தைகளில் எழுதப்பட்டுள்ளது.

---

## 2. "100%" — உண்மையான எண்

நீங்கள் கேட்டது: *"100% ஆயிடுச்சா, இல்லையா — நேர்மையா சொல்லுங்க."*

```
$ brain progress
Nila v0.2 - 21/34 done (62%), 4 partial, 9 not built
$ brain progress next
Next: install a transcriber (pip install faster-whisper), then speak to Nila for the first time
```

**விதி:** ஒரு வரி `done` ஆக இருக்க, அதன் code இந்த நிறுவலில் **இருக்க வேண்டும்**
(ஒவ்வொரு வரிக்கும் ஒரு dotted path சோதிக்கப்படுகிறது). இல்லையென்றால் தானாக
`missing` ஆகிவிடும் — அட்டவணையைத் திருத்தி எதையும் "முடிந்தது" ஆக்க முடியாது.
`partial` = code இருக்கிறது, ஆனால் ஒரு install அல்லது ஒரு key-க்காகக் காத்திருக்கிறது
(எந்த வரி எதற்காக என்று அதே வரியில் எழுதப்பட்டுள்ளது).

| நிலை | எண்ணிக்கை | விளக்கம் |
|---|---|---|
| done | 21 | vault, TOTP, குரல் இரண்டாம் காரணி, speaker lock, permissions, strict mode, audit, session, issue report, reminders (தமிழ் உட்பட), கணக்கு, calendar, memory, retention, chat, dashboard, connectors (எல்லாம் offline), கோப்பு படிப்பு, video, தேடல், apps, storage on D:, icons |
| partial | 4 | connectors-க்கு network இல்லாதது இந்த sandbox-ல் மட்டும், தமிழ்ப் பேச்சு (Piper/SAPI நிறுவ வேண்டும்), Whisper/mic, Telegram token |
| missing | 9 | screen viewing, wake word, music, Gmail/Drive எழுத்து (OAuth), Android app + multi-user, neural speaker, OCR/ffmpeg, fine-tuned தமிழ் + 2-PC mesh, Telegram two-way |

**62% முடிந்தது · 74% (partial-ஐயும் சேர்த்து) · 100% இல்லை.** இதை 100% என்று
சொல்வது பொய்யாகும், அதனால் சொல்லவில்லை.

இதே எண் dashboard-லும் (15 வினாடிக்கு ஒருமுறை) தெரியும், `/v1/progress`
என்ற read-only API-யிலும் கிடைக்கும்.

---

## 3. இந்தக் கட்டத்தில் கண்டுபிடித்து சரிசெய்த பிழை

**புதிய நிறுவலில் முதல் கட்டளை மறுக்கப்பட்டது.** `setup.bat` vault-ஐ உருவாக்கிய
பிறகு இரண்டாம் காரணி (authenticator) இல்லாததால், `start-nila.bat` முதல் தடவை
"second factor required" என்று நிறுத்தியது — அதாவது புது உரிமையாளர் ஓடிய
முதல் கட்டளையே தோல்வி.

இப்போது `brain init`-ஏ authenticator-ஐ இணைத்து விடும் (secret + அந்த நேரக் குறியீடு
ஒருமுறை காட்டப்படும்; Google Authenticator → *Add account → Enter a setup key*).
இரண்டாம் காரணி பலவீனமாகவில்லை — தொடக்கத்திலிருந்தே இருக்கிறது. `setup.bat`-லும்
இரண்டாவது முறை enrol செய்யும் தனிக் கட்டம் நீக்கப்பட்டது (`python brain.py totp`
என்று நினைவூட்டல் மட்டும்). `brain init --no-totp` கொடுத்தால் பழையபடியே.

சோதிக்கப்பட்டது: புது vault → `brain progress next` **ஒரே கட்டளையில்** வேலை செய்தது.

---

## 4. சோதனை முடிவுகள்

| சோதனை | முடிவு |
|---|---|
| முழு test suite (ZIP-உள்ளே) | **1133 tests · 0 failed · 3 skipped** |
| `scripts/retest_everything.py` | **37 passed · 7 skipped · 0 failed** |
| புதிய `tests/test_telegram_and_progress_acceptance.py` | **36 tests** — token mask, host pin, offline queue, drain, 50-cap, reminder push, progress arithmetic, init fix |
| `brain telegram test` (chat இல்லாமல்) | `no chat chosen yet - brain telegram chat <id>` — சரியான பிழை |

ZIP: `Nila-verification-v0.2-ab-included.zip` — 187 files, 2,094,592 B,
sha256 `060ced8fcf5c865e2960d3dd85d1050d…` (இந்தக் கட்டத்தின் இறுதி build).

---

## 5. அடுத்தது (v3)

1. **Whisper** — `pip install faster-whisper` செய்தால் `brain ears` உண்மையாகக் கேட்கும்;
   இதுவே `progress next` சொல்லும் முதல் படி.
2. **Telegram two-way** — போட்டைப் படிக்கும் ஒரு சிறிய background worker.
3. **Piper தமிழ் குரல்** — `brain speak` ஆஃப்லைனில் தமிழ் பேச.
4. Screen viewing, OCR — v3 பட்டியலில் உள்ளது.
