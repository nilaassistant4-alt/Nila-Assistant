# ஸ்டேஜ் 2 முடிந்தது — **Reminder + Calculator (முழுவதும் offline)**

**நாள்:** 2026-11-04 · **கிளை:** `arena/3f2334a4-nila-assistant`
**suite: 912 passed, 3 skipped** (51 புதிய acceptance tests)
**ZIP:** `Nila-verification-v0.2-ab-included.zip` — 150 files, 596,985 B, `4ee927189758…d4e8`

---

## 1. என்ன கட்டினேன்

### Reminder (நினைவூட்டல்)
| ✅ | விவரம் |
|---|---|
| நேரம் | `in 10 minutes` · `at 4:30` · `tomorrow at 9` · `on 12/12 at 18:00` |
| திரும்பும் | `every day at 22:00` · `every monday at 8` |
| தமிழ் | `10 நிமிடத்தில் … நினைவூட்டு` · `நாளைக்கு காலை 9 மணிக்கு` · `தினமும் இரவு 22 மணிக்கு` |
| செயல்கள் | `done` · `snooze` · `cancel` · `reminders` பட்டியல் |
| **எங்கே சேமிக்கப்படுகிறது** | **encrypted memory store** — புதிய database இல்லை, audit + backup + forget எல்லாம் அப்படியே வேலை செய்யும் |
| **முக்கியம்** | நீங்கள் சொன்ன வார்த்தைகள் **அப்படியே** சேமிக்கப்படும் — model சுருக்காது |
| Alert | Nila-ஐ அடுத்த முறை தொடும்போது, **பதிலுக்கு மேலே** ⏰ காட்டும் |
| தவறவிட்டால் | `MISSED 12 min ago` என்று **எவ்வளவு தாமதம்** என்று சொல்லும் (பொய் சொல்லாது) |
| Network | **0 byte** — டெஸ்ட்: `urlopen`-ஐ முடக்கி ஓட்டியும் reminder வேலை செய்தது |

### Calculator (வலுப்படுத்தப்பட்டது)
| ✅ | உதாரணம் | பதில் |
|---|---|---|
| சதவீதம் (EN/TA) | `18% of 1250` · `1250-ல 18 சதவீதம்` · `இருநூறு ரூபாய்க்கு 15 சதவீதம்` | 225 · 225 · 30 |
| GST சேர்ப்பு | `GST on 2499` | 449.82, மொத்தம் 2,948.82 |
| GST உள்ளே | `GST inside 2950` | வரிக்கு முன் 2,500 |
| தள்ளுபடி | `10% discount on 899` | 809.10 |
| வட்டி | `simple interest on 50000 at 8% for 3 years` | 12,000 |
| கூட்டு வட்டி | `compound interest on 100000 at 7% for 5 years` | 40,255.17 |
| அலகு | `5 feet in cm` · `2 kg in lb` · `5 அடி எத்தனை சென்டிமீட்டர்` · `100 c in f` | 152.4 · 4.409 · 152.4 · 212 |

**ஒவ்வொரு பதிலிலும் படிகள் காட்டப்படும்.** `eval` இல்லை (code injection சாத்தியமில்லை), பணம் shop-ல் எப்படி round ஆகுமோ அப்படியே (`ROUND_HALF_UP`).

## 2. எப்படி உபயோகிப்பது

```
brain remind "in 10 minutes to drink water"
brain remind "நாளைக்கு காலை 9 மணிக்கு மருந்து"
brain remind list
brain remind snooze --id 1 --minutes 5
brain remind cancel --id 2
```
Chat-ல் command தேவையில்லை:
```
remind me tomorrow at 9 to take the tablet
reminders
18% of 1250
```
HTTP: `GET /v1/reminders` · `POST /v1/reminders {"text":"in 10 minutes to …"}` · `POST /v1/reminders/close` · `GET /v1/reminders/due`

## 3. பாதுகாப்பு / நடத்தை
- Reminder **model-ஐத் தொடுவதே இல்லை** — routing-க்கு முன்னே code handle செய்கிறது.
- One reminder = ஒரு encrypted record, `kind=reminder`; audit-ல் `reminder_added` / `reminder_fired` பதிவு.
- Alert **தனி வரியில்** அச்சிடப்படும், பதிலுக்குள் கலக்காது.
- திரும்பும் reminder fire ஆனபிறகு **அடுத்த நாளுக்கு நகர்கிறது**, அழியாது.

## 4. டெஸ்ட்
**51 புதிய acceptance tests:** நேரம் படித்தல் (EN+TA) · தெளிவற்ற நேரத்தை **மறுத்தல்** · வார்த்தைகள் அப்படியே இருத்தல் · done/snooze/cancel · repeat roll-forward · MISSED தாமதம் · network-off சோதனை · audit · HTTP · calculator 12 கணக்குகள் · code injection refusal.

**முழு suite: 912 passed, 3 skipped.** ZIP-ஐ பிரித்து மீண்டும் ஓட்டியும் **அதே 912**.

## 5. ⚠️ இன்னும் முடியாதவை (நேர்மை)
| ❌ | ஏன் |
|---|---|
| Nila **மூடியிருக்கும்போது** alert | background service இல்லை (Windows Task Scheduler-ல் பதிவு செய்யலாம் — இன்னும் கட்டவில்லை) |
| **PC sleep/off**-ல் சரியான நேரத்தில் alert | எழுந்த பிறகு "MISSED" என்று சொல்லும் |
| **Phone notification** | Android app இல்லை |
| Voice-ல் சொல்லுதல் | TTS இல்லை — **ஸ்டேஜ் 3** |
| Google Calendar-ல் reminder | OAuth — பிற்பாடு |

## 6. அடுத்தது — **ஸ்டேஜ் 3**
**Calendar read (`.ics`, login இல்லாமல்) + Voice output (SAPI + Piper Tamil)**
→ "நாளைக்கு என்ன meeting?" மற்றும் "இதைச் சொல்லு" இரண்டும் ஒரே ஸ்டேஜில்.

தொடங்கட்டுமா?
