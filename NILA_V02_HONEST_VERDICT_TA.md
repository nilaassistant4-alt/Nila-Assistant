# நிலா v0.2 — "100% வந்துடுச்சா?" சரியான பதில்

**நாள்:** 2026-11-18 · **கிளை:** `arena/3f2334a4-nila-assistant` · **push செய்யவில்லை** (நீங்கள் சொன்னபிறகுதான்)
**சோதனை:** ZIP-உள்ளே இருந்து — **1170 tests · 0 failed · 3 skipped** ·
re-test **42 passed · 7 skipped · 0 failed** · mandate probe **24 pass · 0 FAIL · 5 gap**

---

## 1. நேரடிப் பதில்: **இல்லை — 100% ஆகவில்லை. 70%.**

> நீங்கள் "100% ஆச்சா?" என்று கேட்டீர்கள். நேர்மையான பதில் **இல்லை**.
> **43 விஷயங்களில் 30 முடிந்தது (70%)**, 4 ஒரு install/key-க்குக் காத்திருக்கிறது,
> 9 கட்டப்படவே இல்லை. அதனால் ZIP-ஐ இந்தக் கட்டத்தில் **கொடுக்கவில்லை**.

"100%" என்று சொல்வதற்கு ஒரு வரி இருக்கிறது: அதன் code இந்த நிறுவலில் இருக்க வேண்டும்
(`brain progress` ஒவ்வொரு வரியையும் import செய்து சோதிக்கிறது). அட்டவணையைத் திருத்தி
எதையும் "முடிந்தது" ஆக்க முடியாது.

```
$ brain progress
Nila v0.2 - 30/43 done (70%), 4 partial, 9 not built
```

---

## 2. இந்தக் கட்டத்தில் **புதிதாக முடித்தது** (உங்கள் v0.2 பட்டியலில் இருந்தவை)

நீங்கள் v0.2-க்கு உறுதி செய்த பட்டியலை மறுபடியும் படித்தேன். அதில் **நான்கு** விஷயங்கள்
இருந்தும் கட்டப்படவில்லை என்பதைக் கண்டேன் — அதை இப்போது கட்டி, டெஸ்ட் எழுதி முடித்தேன்:

| # | உங்கள் வரி | இப்போது |
|---|---|---|
| 1 | *"business-model interview: ஒரு நேரத்தில் ஒரு கேள்வி, 9 பகுதிகளில் சேமிப்பு"* | `brain project ask` / `answer` — ஒரு கேள்வி, வரிசை மாறாது, block பெயர் தெரிய வேண்டாம். dashboard-ல் ஒரே box. **done** |
| 2 | *"milestone due dates; தாமதமானவை dashboard-ல் முன்னிலைப்படுத்தல்"* | `--due 2026-12-01` (அல்லது `01/12/2026`, `tomorrow`) · `brain project late` · dashboard-ல் **LATE** பட்டை மேலே. **done** |
| 3 | *"கேட்டால் மட்டும் வாராந்திரச் சுருக்கம்"* | `brain project weekly` — மூன்று வரி; தானாக ஒருபோதும் ஓடாது; network-க்குப் போகாது (டெஸ்ட் உண்டு). **done** |
| 4 | *"project summary-ஐ text file-ஆக export"* | `brain project export "Tea shop" --folder D:/Notes` → `.txt`, நீங்கள் சொன்ன இடத்தில். **done** |
| 5 | *"Chrome 'Install as app' (taskbar icon)"* | manifest + icon + CSP — Chrome இப்போது **Install as app** காட்டும். **done** |
| 6 | *"`move-to-d.bat`"* | copy → **ஒவ்வொரு கோப்பின் SHA-256 ஒப்பீடு** → அப்புறம்தான் switch. ஓடிக்கொண்டிருக்கும்போது மறுக்கும்; `vault-old`-ஐ வைத்திருக்கும்; எதையும் அழிக்காது. **done** |
| 7 | *"`backup-to-d.bat`"* | backup → **திரும்ப restore செய்து record எண்ணிக்கை ஒப்பீடு**; பொருந்தவில்லை என்றால் **BACKUP FAILED**; Google Drive/OneDrive folder-ஐ வேண்டுமென்றே மறுக்கும். **done** |
| 8 | *"`install-local-ai.bat`"* | Ollama + `llama3.2:3b`, பிறகு **உண்மையாக ஒரு கேள்வி கேட்டு** பார்த்துத்தான் "முடிந்தது" என்பது. winget இல்லாவிட்டால் official download பக்கம். **done** |

**30 → இது 8 புதிய `done` வரிகள்** (43-ல்).

---

## 3. அதே நேரத்தில் கண்டுபிடித்த **மூன்று உண்மையான பிழைகள்** (சரிசெய்துவிட்டேன்)

நீங்கள் சொன்னபடி "அனைத்தையும் reset செய்து மறுபடியும் ஓட்டினேன்". அப்போது இவை வெளிச்சத்துக்கு வந்தன:

1. **`brain memory report` ஒரு பிழை** — `--passphrase-env PW` கொடுத்தால் 20 வரி usage error.
   `brain progress next --passphrase-env PW` மட்டும் வேலை செய்தது. விளைவு: ஒரு script-ல்
   ஒரே வடிவம் எல்லா கட்டளைக்கும் வேலை செய்யாது.
   **சரி:** அனைத்து 53 subcommand-க்கும் unlock flags; **இரு நிலைகளிலும்** ஏற்கும்
   (`brain --passphrase-env PW chat` மற்றும் `brain chat --passphrase-env PW`). டெஸ்ட் உண்டு.
2. **`brain init --quiet` authenticator secret-ஐ மறைத்தது** — அதுதான் ஒரே copy.
   **சரி:** recovery codes + secret + code இப்போது `--quiet`/`--json`-லும் stderr-ல் வரும்.
3. **புதிய நிறுவலில் முதல் கட்டளை மறுக்கப்பட்டது** — `setup.bat` vault-ஐ இரண்டாம் காரணி
   இல்லாமல் உருவாக்கியது, அதனால் `start-nila.bat` "second factor required" என்று நிறுத்தியது.
   **சரி:** `brain init`-ஏ authenticator-ஐ இணைக்கிறது; `setup.bat` இரண்டாவது முறை கேட்காது.

மேலும்: mandate probe-ல் இருந்த ஒரு சிவப்பு FAIL **பிழை அல்ல, தவறான சோதனை** என்று
நிரூபித்து அதைச் சரிசெய்தேன் (tool-loop-ஐ அல்ல, முழு file-ஐ text-ஆக grep செய்தது).
இப்போது **0 FAIL**.

---

## 4. "reset செய்து மறுபடியும் ஓட்டு" — என்ன செய்தேன், என்ன ஆனது

| செய்தது | முடிவு |
|---|---|
| புதிய vault (`brain init`), புதிய கடவுச்சொல் + authenticator | ✅ |
| ZIP-ஐ பிரித்த இடத்திலிருந்து 6 கட்டளை (`progress`, `doctor`, `project list`, `remind list`, `telegram status`, `memory report`) | ✅ 6/6 exit 0 |
| புதிய vault-ல் business flow: `new → ask → answer → milestone(late) → late → weekly` | ✅ 2114 நாட்கள் தாமதம் சரியாகக் காட்டியது |
| Tree-ல் 12 கட்டளை (`status`, `doctor`, `strict show`, `session`, `voice status`, `challenge`, `audit`, `config show`, `models`, `connectors`, `launcher --plan`, `languages`) | ✅ 12/12 exit 0 |
| முழு suite, coverage | ✅ 1170 tests, 0 failed |
| 1165 → 1170 வரை **ஒவ்வொரு முறையும்** முழு suite | ✅ (ஒருமுறை 1 fail — அது packaging test; அது கண்டுபிடித்தது சரியே, சரிசெய்தேன்) |

**Windows-ல் ஓட்ட முடியாதது (இங்கே Linux மட்டுமே):** `setup.bat`, `start-nila.bat`,
`move-to-d.bat`, `backup-to-d.bat`, `install-local-ai.bat` — இவற்றின் *logic* இங்கே
டெஸ்ட் செய்யப்பட்டது (43 re-test checks), ஆனால் **.bat கோப்புகள் உங்கள் PC-ல் ஓடினால் மட்டுமே**
உறுதி. இது "100%" சொல்ல முடியாத இடம்.

---

## 5. 100%-க்கு எஞ்சியது — சரியாக 13

**4 partial** (code இருக்கு, ஒரு install/key வேண்டும் — நீங்கள் செய்யக்கூடியது):
1. `pip install faster-whisper` → **கேட்கும்** (இதுவே `progress next`)
2. Tamil குரல்: Windows SAPI அல்லது Piper
3. mic capture: `pip install sounddevice numpy`
4. Telegram: bot token + chat id (`brain telegram how`)

**9 missing** (கட்டப்படவே இல்லை — இவை **v3**):
screen viewing · always-on wake word · music player · Gmail/Drive/Calendar write (OAuth) ·
Android app + multi-user · neural speaker model · OCR + ffmpeg scene detect ·
fine-tuned Tamil + 2-PC mesh · **Telegram two-way**

இந்த 13 வரிகளும் இல்லாமல் 100% சொல்ல முடியாது. v3 பட்டியல் தனிக் கோப்பில்:
`NILA_V03_IDEAS_PARKED_TA.md` — **நீங்கள் சொல்லும்போது மட்டும்** எடுப்பேன்.

---

## 6. ZIP நிலை

`Nila-verification-v0.2-ab-included.zip` — **198 files, 744,849 B**,
sha256 `d0a9048ef1e9f3f7e2fdd37c2e764823565402a29422007e61db11d5a3302a67`.
**தயாராக உள்ளது, ஆனால் இப்போது கொடுக்கவில்லை** — ஏனெனில் நீங்கள் சொன்னது:
*"100% வந்தா மட்டும் ZIP கொடு, இல்லன்னா கொடுக்காத."* 70% ஆகையால் இல்லை.
`git push`-ம் செய்யவில்லை (உங்கள் வார்த்தைக்காகக் காத்திருக்கிறது).
