# ஸ்டேஜ் 7 முடிந்தது — **கேட்பது · கோப்பு தேடல் · app திறப்பது**
### + "press once, say it" icon (terminal typing இல்லை)

**நாள்:** 2026-11-04 · **கிளை:** `arena/3f2334a4-nila-assistant`
**suite: 1069 passed, 3 skipped** (39 புதிய acceptance tests, ~117 s)
**ZIP:** `Nila-verification-v0.2-ab-included.zip` — **181 files**, 676,966 B, `443662c0ca47…b171`
(ZIP-ஐ extract செய்து `/tmp/ziptest7`-ல் மீண்டும் ஓட்டினேன் → **1069 passed** அதே)

---

## 1. இந்த ஸ்டேஜில் என்ன வந்தது (3 விஷயம்)

| # | உங்கள் கேள்வி | இப்போது இருக்கிறது | எப்படி |
|---|---|---|---|
| 1 | "**type பண்ணாம, பேசி** சொல்லணும்" | `brain talk` — ஒரு press: கேட்கும் → குரல் சரிபார்ப்பு → செயல் → **குரலில் பதில்** | `brain/agent/ears.py` + `brain talk` |
| 2 | "**அந்த bill எங்க?** PC-ல தேடு" | `brain find invoice` · `--content` (உள்ளேயும் தேடும்) | `brain/util/finder.py` |
| 3 | "**notepad / chrome திற**" | `brain open notepad` — **நிரந்தர பட்டியல்** மட்டும் | `brain/util/apps.py` |

**மூன்றும் சேர்ந்து ஒரு வாக்கியமாகவும் வேலை செய்யும்:**
```
"nila open notepad"      → open
"nila find invoice"      → find
"nila read D:/bill.pdf"  → read
"nila stop"              → stop (எதுவும் செய்யாது)
"நிலா திற chrome"        → open
```

---

## 2. ⚠️ மிக முக்கியமான நேர்மை: **கேட்பது இன்னும் உண்மையில் இல்லை**

| ✅ கட்டி முடிந்தது | ❌ இல்லை (இது பொய் சொல்லாம இருக்க வேண்டியது) |
|---|---|
| microphone, WAV, 3 engine-ஐயும் **detect** செய்யும் | **Whisper / faster-whisper / vosk எதுவும் install இல்லை** → இந்த build-ல் **எந்தப் பேச்சையும் எழுத்தாக்க முடியாது** |
| எதுவும் இல்லாதபோது **சரியான install command-ஐ** சொல்லும் (`brain ears --status`) | **இந்தக் கணினியில் mic இல்லை** → `brain ears --listen` → "needs `pip install sounddevice numpy`" |
| WAV file இருந்தால் transcribe ஆகும் (engine install ஆனபின்) | **நான் உங்களுக்காக install செய்ய மாட்டேன்** (உங்கள் விதி) |

**நீங்கள் ஒரு கட்டளை ஓட்டினால் போதும்** (எதை வேண்டுமானாலும் தேர்ந்தெடுங்கள்):
```
pip install faster-whisper     ← பரிந்துரை: Whisper துல்லியம், 3-4x வேகம்
pip install openai-whisper     ← தமிழ் நல்லது, CPU-ல் மெதுவாக
pip install vosk               ← 50 MB, வேகம், தமிழ் பலவீனம் (+ ta model)
pip install sounddevice numpy  ← microphone வேண்டும் என்றால்
```
இதுவரை **"கேட்டேன்" என்று ஒரு முறையும் சொல்லவில்லை** — engine இல்லாதபோது `brain talk` நேரடியாக "I could not hear you: …" என்று சொல்லி **exit 2** ஆகிறது. **இது ஒரு wiring வேலை, முடிந்த feature அல்ல.**

---

## 3. குரல் பாதுகாப்பு — ஸ்டேஜ் 6 விதி இங்கேயும் தொடர்கிறது

```
கேட்டது → voice lock (அதே audio) → owner இல்லை என்றால் → வார்த்தைகள் THROW AWAY
```
| நிலை | முடிவு |
|---|---|
| உங்கள் குரல் | `owner 0.95` → செயல் நடக்கும் |
| 210 Hz வேறு குரல் | `other 0.32` → **"I did nothing with that"**, வார்த்தை பயன்படுத்தப்படவாது |
| குறுகிய clip | `uncertain` → refuse |
| voiceprint இல்லை | `no_profile` → **யாரையும் ஏற்காது** ("run `brain voice enrol`") |

**இந்த ஸ்டேஜில் பிடித்த உண்மையான bug:** `brain voice enrol` **செயலில் crash** ஆனது —
lock-க்கு memory store அனுப்பப்பட்டது, ஆனால் voiceprint **vault-ல்** சேமிக்கப்படுகிறது.
மேலும் `speaker.json` file இருந்தால் மட்டுமே load ஆகும் என்ற condition இருந்ததால்,
**ஒவ்வொரு restart-க்குப் பிறகும் "யாரும் enrol ஆகவில்லை"** என்று சொல்லி **உங்கள் குரலையே மறுத்திருக்கும்**.
இரண்டையும் சரிசெய்து, vault-ல் மட்டும் சேமிக்கும்; disk-ல் `speaker.json` **உருவாக்கப்படுவதில்லை** (டெஸ்ட் செய்தேன்).

---

## 4. "Press once, say it" — terminal தேவை இல்லை

```
brain launcher --talk --write         # Desktop-ல் 'Nila Talk' icon
```
| ✅ | ❌ |
|---|---|
| Windows: console இல்லாத `.vbs` + **Startup folder**-ல் நகல் (Win key → "Nila") | true hotkey (Ctrl+Alt+N) — **Windows-ன் Properties-ல் நீங்கள் அமைக்க வேண்டும்**, நான் முடியாது |
| Linux/mac: double-click icon | **எப்போதும் கேட்கும் mic** — வேண்டாம் என்று முடிவு |
| **bounded**: 8 வினாடி கேட்டு மூடும் — `brain start` அல்ல | Nila **மூடியிருக்கும்போது** தானாக எழ முடியாது |

---

## 5. கோப்பு தேடல் — வேகம் + நேர்மை

| ✅ | விவரம் |
|---|---|
| பெயரால் தேடல் | `.git`, `node_modules`, `AppData`, recycle bin **skip** |
| உள்ளே தேடல் | **opt-in** (`--content`), 2 MB வரம்பு, text வகை file மட்டும், சொற்றொடர் snippet |
| "எவ்வளவு பார்த்தேன்" | `12 files checked` / `200,000 files checked, stopped at the cap` — **0 match-ன் அர்த்தம் வேறு** |
| எதையும் திறக்காது / ஓட்டாது | path மட்டும் திருப்பித் தரும் |
| `--since 30` | கடைசி 30 நாளில் மாறியவை |

**அளவீடு:** `.git/config`-ல் "electricity bill" இருந்தும், search **skip** செய்தது; `notes.txt`-ல் snippet கிடைத்தது.

---

## 6. App திறப்பது — "open" ஒரு shell ஆக மாறாமல்

| ✅ | ❌ |
|---|---|
| **நிரந்தர பட்டியல்**: chrome, edge, firefox, notepad, calculator, explorer, files, word, excel, settings, paint, terminal | பட்டியலில் இல்லாத பெயர் → **refuse** + அருகில் உள்ள பெயர் ("Did you mean: terminal?"). `rm -rf /` → refuse |
| `shell=False`, அதன் சொந்த arguments மட்டும் | app-ன் **window-ஐ பார்க்க முடியாது** (screen access இல்லை) |
| `--show` → என்ன ஓடும் என்று சொல்லி **எதுவும் ஓட்டாது** | "excel/word" Linux-ல் இல்லை → **"Windows/macOS app"** என்று சொல்லும் |
| உங்கள் சொந்த பெயர்: `{"agent": {"apps": {"studio": ["code","--new-window"]}}}` | |

**இந்த ஸ்டேஜில் பிடித்த 2ஆவது bug:** `files` → command `'.'` (புள்ளி தான் program ஆகிவிட்டது);
`notepad` → `gedit kate mousepad nano` (4 program ஒரே கட்டளையாக). இப்போது candidate = **முழு argv** —
`["xdg-open", "."]` என்பது "xdg-open *with* a dot". 12 பெயரும் சரிபார்க்கப்பட்டது.

---

## 7. புதிய கட்டளைகள் (சுருக்கம்)

```
brain ears --status                 # transcriber + mic இருக்கா, இல்லையா
brain ears --file me.wav            # WAV → text
brain ears --listen 5               # 5 வினாடி record (vault unlock தேவை)
brain talk                          # ஒரு press: கேள் → சரிபார் → செய் → பதில்
brain talk --text "nila open files" --show     # mic இல்லாமல் பாதையை சோதிக்க
brain find invoice [--in DIR] [--content] [--since 30]
brain open --list | brain open notepad --show | brain open chrome
brain launcher --talk --write       # press-to-talk icon
```

---

## 8. 🧪 சோதனை — உண்மையான ஓட்டங்கள் (opinion அல்ல)

| சோதனை | முடிவு |
|---|---|
| `python -m pytest` (முழு) | **1069 passed, 3 skipped** (116.7 s) |
| ZIP extract → மீண்டும் ஓட்டியது | **1069 passed** (118.6 s) |
| `brain voice status` (உண்மையான vault, TOTP-உடன்) | `enrolled: true, samples: 2, threshold 0.82` |
| vault-ல் enrol → **restart** → judge | `after restart enrolled: True` · owner `0.9846` · stranger `0.3242` |
| vault lock ஆனபின் meta படிக்க | `''` (sealed — கசிவு இல்லை) · `speaker.json` **உருவாக்கப்படவில்லை** |
| `brain talk --text "nila open files" --show` | `files would run: xdg-open .` |
| `brain talk --text "nila stop"` | `Stopping. Nothing else is done.` |
| `brain talk --file /tmp/none.wav` | `I could not hear you: audio file not found: /tmp/none.wav` |
| `brain ears --status` | `Transcriber : none installed` · `Microphone : no` + 3 install lines |
| `brain open notepad --show` | `would run : gedit` / `installed : no` |
| `brain find electricity --content --in /tmp/ftree` | `1 hit(s) … notes.txt … electricity bill due 12th` |

---

## 9. ❌ செய்யாதவை (நேர்மை)

- **உண்மையான பேச்சு சோதனை இல்லை** — engine-ம் mic-ம் இல்லை. Whisper install ஆனபின் `brain ears --file` மூலம் **உங்கள் குரலில்** சோதிக்க வேண்டும்; தமிழ் துல்லியம் எந்த அளவு என்று **நான் உறுதி சொல்ல முடியாது**.
- **`--skip-voice-check`** flag இருக்கிறது (hidden, டெஸ்டுக்கு) — production-ல் யாரும் அதை ஒரு shortcut ஆக பயன்படுத்தக் கூடாது.
- **`read` செயல்** file-ஐ படிக்கும், ஆனால் PDF-ன் "short and sweet" சுருக்கம் **model இல்லாமல்** extract ஆன text மட்டுமே.
- **சொற்கள் அடையாளம்** — "nila open …" verb list சிறியது (open/find/read/stop + calendar/remind இடம்). தமிழ் ஒலிபெயர்ப்பு (transliteration) பிழை இருந்தால் match ஆகாது.
- **Phone / screen / music** — இன்னும் இல்லை (v3).
- **Sandbox-ல் network இல்லை** — weather/wiki/YouTube இன்னும் live சோதிக்கப்படவில்லை.

## 10. அடுத்து (நான் பரிந்துரைப்பது)

1. **நீங்கள் `pip install faster-whisper`** ஓட்டுங்கள் (அல்லது vosk) → நான் `brain ears --file` + `brain talk`-ஐ உங்கள் உண்மையான குரலில் சரிபார்த்து, தமிழ் துல்லியத்தை அளந்து சொல்கிறேன்.
2. அதுவரை: **ஸ்டேஜ் 8 — 12 சின்ன bug பட்டியல்** (`NILA_V03_BATCH_PLAN_AND_EXTRAS_TA.md` §5): reminder Nila மூடியிருக்கும்போதும் அடிக்கும், Gemini model-name probe, Telegram வழி phone delivery.
3. v3 (சிக்கலானவை): wake word, screen பார்க்க, music player, Android, neural speaker model.
