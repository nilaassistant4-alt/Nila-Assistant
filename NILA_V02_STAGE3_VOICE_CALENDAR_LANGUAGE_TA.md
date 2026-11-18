# ஸ்டேஜ் 3 முடிந்தது — **Calendar (read-only) + Voice + 10 மொழி புரிதல்**

**நாள்:** 2026-11-04 · **கிளை:** `arena/3f2334a4-nila-assistant`
**suite: 959 passed, 3 skipped** (47 புதிய acceptance tests)
**ZIP:** `Nila-verification-v0.2-ab-included.zip` — 154 files, 622,183 B, `39f373be56ac…4c31`

---

## 1. மொழி விதி — நீங்கள் சொன்னது அப்படியே

> "தமிழ் அண்ட் இங்கிலீஷ்ல மட்டும் தான் நிலா என்கிட்ட பேசணும்… ஆனா 5–10 லாங்குவேஜை புரிந்து என்கிட்ட தமிழ்ல எக்ஸ்ப்ளைன் பண்ணனும்"

| | விவரம் |
|---|---|
| **பேசுவது** | **தமிழ் + English மட்டும்** — வேற எந்த மொழியிலும் பதில் வராது (code-ல் lock) |
| **படிப்பது** | **மலையாளம் · இந்தி · தெலுங்கு · கன்னடம் · பெங்காலி · மராத்தி · குஜராத்தி · பஞ்சாபி · உருது · சமஸ்கிருதம்** |
| **எப்படி அறிகிறது** | script (Unicode) மூலம் — 95% நம்பிக்கை; Roman letters-ல் வந்தாலும் (`ente peru…`, `mera naam…`) பொதுவான வார்த்தைகளால் அறியும் |
| **தமிழில் விளக்கம்** | "இது Malayalam மொழி (95%)" + தெரிந்த வார்த்தைகள் + **முழு மொழிபெயர்ப்புக்கு model தேவை** என்ற நேர்மை |
| **model இருந்தால்** | Gemini key + Online mode → ஒரு prompt-ல் தமிழில் விளக்கம், **யார் மொழிபெயர்த்தது** என்று label உடன் |
| **யூகம் சொல்லாது** | model இல்லாமல் ஒரு பொய்யான மொழிபெயர்ப்பும் சொல்லாது |

**உதாரணம் (இப்போது நிஜமாக வேலை செய்கிறது):**
```
> എന്റെ പേര് അനു, എനിക്ക് സഹായം വേണം
இது Malayalam மொழி என்று தெரிகிறது (95% நம்பிக்கை, script).
  എന്റെ = my ; പേര് = name
முழு மொழிபெயர்ப்புக்கு ஒரு model தேவை…
```

## 2. Calendar — login இல்லாமல் படிக்கலாம்

| ✅ | |
|---|---|
| Google Calendar | Settings → **"Secret address in iCal format"** — OAuth தேவையில்லை |
| Outlook / Apple | export செய்த `.ics` file |
| கேள்வி | `what is on tomorrow?` · `நாளைக்கு என்ன இருக்கு` · `this week meetings` |
| Repeats | தினமும் / வாரந்தோறும் / BYDAY / மாதம் / COUNT / UNTIL |
| All-day + timezone | ✅ |
| பாதுகாப்பு | calendar address = **password போல** — host மட்டுமே காட்டும் |
| Network | URL-ஐ **நீங்கள் "ஆமா" சொன்னால் மட்டுமே** fetch செய்யும் |

## 3. Voice — இரண்டு இன்ஜின், நேர்மையான பதில்

| Engine | எப்போது |
|---|---|
| **Windows SAPI** | Windows-ல் built-in — install தேவையில்லை (தமிழ் குரல் இருந்தால் தமிழ்) |
| **Piper Tamil** | `pip install piper-tts` + `~/.nila/voices/`-ல் ஒரு `.onnx` voice (60 MB, offline) |
| எதுவும் இல்லை | `spoken: false` + **காரணம்** + WAV placeholder — **பொய் சொல்லாது** |

```
brain speak "வணக்கம்"          brain speak --list-voices
brain languages               brain languages "എന്റെ പേര് അനു"
brain calendar --set <path-or-url>     brain calendar --when tomorrow
```

## 4. டெஸ்ட்
47 புதிய acceptance tests: calendar parse/repeat/timezone/all-day · URL permission · address masking · 10 மொழி detection · **model இல்லாமல் மொழிபெயர்ப்பு கண்டிப்பு** · SAPI/Piper mock · HTTP.

**முழு suite: 959 passed, 3 skipped.** ZIP-ஐ பிரித்து மீண்டும் ஓட்டியும் **அதே 959**.

## 5. ⚠️ நேர்மை
- இந்த sandbox **Linux** → **SAPI-ஐ உண்மையில் ஓட்டிப் பார்க்க முடியவில்லை** (mock-ல் டெஸ்ட்). **Windows-ல் நீங்கள் தான் முதல் முறை சோதிக்க வேண்டும்.**
- **Piper voice file** நான் download செய்ய மாட்டேன் — நீங்கள் வைக்க வேண்டும்.
- **Tamil SAPI voice** Windows-ல் இல்லாமல் இருக்கலாம் → Piper தான் உறுதியான offline வழி.
- **Calendar-ல் எழுத முடியாது** (read-only). Event create = OAuth = அடுத்த ஸ்டேஜ்.
- **மொழிபெயர்ப்பு உண்மையில் இல்லை** (model இல்லாமல்) — detection + சொல் அறிவு மட்டும்.
- **Voice input (நீங்கள் பேசுவது)** இன்னும் இல்லை — அடுத்த ஸ்டேஜ் (Whisper/Vosk).

## 6. அடுத்தது — **ஸ்டேஜ் 4**
**Voice input (Whisper offline / Vosk) + File share (PDF/DOCX படித்து சுருக்கம்) + Screen பார்த்தல்** — நீங்கள் சொன்ன "டெக்ஸ்ட் மாதிரியே வாய்ஸ், file share, screen பார்க்கும்" மூன்றும்.

தொடங்கட்டுமா?
