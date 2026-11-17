# ஸ்டேஜ் 2 → 4: என்ன பண்ண முடியும், **என்ன பண்ண முடியாது**

**நாள்:** 2026-11-04 · **கிளை:** `arena/3f2334a4-nila-assistant` (`17b4a3f`)
இது ஒரு **பதில் ஆவணம்** — இதில் code மாற்றம் இல்லை. கீழே உள்ள எல்லாமே இன்று
repository-ஐப் பார்த்து எழுதியது (`brain/agent/tools.py`, `brain/cli/main.py`,
`brain/util/audio.py`, `brain/agent/models.py`).

---

## 0. இன்று **ஏற்கனவே** வேலை செய்கிறது (நிரூபிக்கப்பட்டது)

| உள்ளது | எப்படி |
|---|---|
| Vault + passphrase / voice / TOTP unlock | `brain unlock`, `brain enroll-voice`, `brain totp` |
| Memory (encrypted) — remember / recall / forget / list | `brain remember`, `brain recall` |
| 19 tools (memory, time, calculate, file read/list, photo, run_command, integration, backup, updates) | `brain/agent/tools.py` |
| **Calculate** — `+ - * / // % ** ( )` | tool `calculate` |
| Permission broker (one_shot / durable / denied) + hash-chained audit | `brain/security/permission.py` |
| E2E encrypted sync, backup/restore, device enrolment | `brain sync`, `brain backup` |
| Strict mode, issue report, doctor, report | `brain strict`, `brain issue`, `brain doctor` |
| **Model pack** (சிறிய/பெரிய brain, 13 models) | `brain models` ← **இன்று கட்டியது** |
| Local API + dashboard (battery/CPU/RAM/disk) | `brain serve` |

**⚠️ இல்லாதது (இன்று வரை):** reminder · calendar · voice output (TTS) · speech-to-text ·
PDF/DOCX படிக்க · scheduler · storage tiers · auto-delete · audit rotation ·
phone app · Wi-Fi/hotspot/Bluetooth panel.

---

## 1. **ஸ்டேஜ் 2 — Reminder + Calculator** (நான் அடுத்து கட்டப் போவது)

### ✅ பண்ண முடியும் (உறுதி)
| # | என்ன | உதாரணம் |
|---|---|---|
| 1 | நேரம் சார்ந்த நினைவூட்டல் | "10 நிமிடம் கழிச்சு தண்ணி குடி" |
| 2 | தேதி + நேரம் | "நாளைக்கு காலை 9 மணிக்கு மருந்து" |
| 3 | தினமும் / வாரந்தோறும் திரும்பும் | "தினமும் இரவு 10க்கு மருந்து" |
| 4 | Snooze / done / cancel | "5 நிமிடம் தள்ளு", "முடிஞ்சது" |
| 5 | "இன்னைக்கு என்ன reminder?" பட்டியல் | voice இல்லை, text |
| 6 | PC restart-க்குப் பிறகும் நினைவில் இருக்கும் | JSON + audit |
| 7 | **முழுவதும் offline** — எழுத்து ஒன்றும் network-க்குப் போகாது | |
| 8 | Calculator: `%`, வட்டி (simple/compound), **GST 5/12/18/28%**, டிகவுண்ட் | "1250-ல 18% GST எவ்வளவு" |
| 9 | Unit மாற்றம்: km↔mile, kg↔lb, °C↔°F, சென்ட்↔அடி | offline table |
| 10 | **தமிழ் வார்த்தைக் கணக்கு** | "இருநூறு ரூபாய்க்கு 15 சதவீதம்" |
| 11 | தவறான கணக்கைச் சொல்லாமல் இருத்தல் | `eval` இல்லை, allow-list சோதனை |

### ❌ பண்ண **முடியாது** (நேர்மையாக)
| # | முடியாதது | ஏன் |
|---|---|---|
| 1 | **Nila ஓடவில்லை என்றால் alert வராது** | PC-ல் background service இல்லை. (Windows Task Scheduler-ல் பதிவு செய்யலாம் — ஆனால் அது இப்போது கட்டப்படவில்லை) |
| 2 | **PC sleep/off-ல் இருக்கும்போது சரியான நேரத்தில் alert** | எழுந்த பிறகு தான் "தவறவிட்ட reminder" என்று சொல்லும் |
| 3 | **Phone-க்கு notification** | Android app வேண்டும் — இல்லை |
| 4 | **Google/Outlook Calendar-ல் reminder வைப்பது** | OAuth வேண்டும் (ஸ்டேஜ் 3-ல் கூட read மட்டும்) |
| 5 | **Voice-ல் சொல்லுதல்** | TTS இல்லை (ஸ்டேஜ் 3) |
| 6 | **Email/SMS/WhatsApp-ல் அனுப்புதல்** | API/account இல்லை; வேண்டாம் என்று முடிவு செய்யப்பட்டது |
| 7 | **"அம்மாவுக்கு நினைவூட்டு"** (வேறு நபருக்கு) | ஒரே பயனர் கணக்கு; multi-user இல்லை |

**அளவு:** ~600 வரி + ~30 acceptance tests. **பாதிப்பு:** புதிய file மட்டும் — ஏற்கனவே உள்ள எதையும் மாற்றாது (regression ஆபத்து குறைவு).

---

## 2. **ஸ்டேஜ் 3 — Calendar (ICS) + Voice output**

### ✅ பண்ண முடியும்
| # | என்ன | குறிப்பு |
|---|---|---|
| 1 | **Google Calendar-ஐ login இல்லாமல் படிக்க** | Calendar → Settings → "Secret address in iCal format" → `.ics` URL. **OAuth தேவையில்லை** |
| 2 | "நாளைக்கு என்ன meeting?" / "இந்த வாரம் என்ன இருக்கு?" | offline parse அல்லது `gemini-flash` |
| 3 | Outlook / Apple Calendar (export செய்த `.ics` file) | file-ஆகவும் படிக்கும் |
| 4 | **Voice output** — Windows SAPI (built-in, install இல்லை) | தமிழ் குரல் Windows-ல் **இருக்கலாம்/இருக்காமல் இருக்கலாம்** — சோதிக்க வேண்டும் |
| 5 | **Piper Tamil** (60 MB, offline, தரம் நல்லது) | pack-ல் இடம் தயார் |
| 6 | "சொல்லு, எழுதாதே" mode | text + WAV file |

### ❌ பண்ண **முடியாது**
| # | முடியாதது | ஏன் |
|---|---|---|
| 1 | **Calendar-ல் புதிதாக event உருவாக்க** | read-only `.ics` URL-ல் எழுத முடியாது → OAuth தேவை (ஸ்டேஜ் 3-க்குப் பிறகு) |
| 2 | **`.ics` URL-ஐ Google தடுத்தால்** | private URL ரத்தானால் படிக்க முடியாது — நீங்கள் புதிதாக எடுக்க வேண்டும் |
| 3 | **Piper-ஐ Nila தானே download செய்வது** | நான் எதையும் download செய்ய மாட்டேன்; நீங்கள் file-ஐ வைக்க வேண்டும் |
| 4 | **இயற்கையான மனிதக் குரல்** | Piper/SAPI robotic தான்; cloud TTS (Google/ElevenLabs) தேவைப்பட்டால் — பணம் + privacy |
| 5 | **இரண்டு பேர் பேச்சைப் பிரித்தல்** | முடியாது (single speaker only) |
| 6 | **"Hey Nila" wake word** | எப்போதும் mic கேட்க வேண்டும் → battery + privacy; **பரிந்துரைக்கவில்லை** |

---

## 3. **ஸ்டேஜ் 4 — Storage tiers + 3 மாச auto-delete + audit rotation**

### ✅ பண்ண முடியும்
| # | என்ன |
|---|---|
| 1 | memory-ஐ **hot / warm / archive** ஆகப் பிரித்தல் (தேடல் வேகம் ↑) |
| 2 | **90 நாள்** பழையதை archive-க்கு நகர்த்துதல் (**ஒப்புதல் கேட்ட பிறகு தான்**) |
| 3 | "delete preview" — என்ன அழியும் என்று முன்னால் காட்டுதல் |
| 4 | Audit log rotation (10 MB ஆனால் பழையது → `.1`) |
| 5 | Passphrase rotate நினைவூட்டல் (180 நாள்) |
| 6 | Backup verify (restore சோதனை) |

### ❌ பண்ண **முடியாது**
| # | முடியாதது | ஏன் |
|---|---|---|
| 1 | **Nila ஓடாத நாட்களில் தானாக அழித்தல்** | background daemon இல்லை → அடுத்த முறை திறக்கும்போது தான் |
| 2 | **அழித்ததைத் திரும்பப் பெறுதல்** | அழித்த பிறகு மீட்பு இல்லை (backup இருந்தால் மட்டும்) |
| 3 | **உங்கள் அனுமதி இல்லாமல் auto-delete** | **ஒருபோதும்** செய்யாது — இது வடிவமைப்பு முடிவு |
| 4 | **Cloud-ல் உள்ள நகலை அழித்தல்** | sync backup-ல் உள்ள பழைய பதிப்பு நீங்கள் தான் அழிக்க வேண்டும் |

---

## 4. ஒட்டுமொத்தம்: **இப்போதைக்கு நம்மகிட்ட உள்ள சாத்தியங்கள்**

### ✅ இப்போது செய்யக் கூடியவை (code உள்ளது)
memory · strict mode · calculator · file படித்தல் · photo search · encrypted sync/backup · audit · model pack · local API · battery/CPU/RAM/disk dashboard · issue report · doctor · voice **unlock** (voice output அல்ல)

### ✅ கட்டினால் உடனே செய்யக் கூடியவை (ஸ்டேஜ் 2–4)
reminder · GST/வட்டி/unit கணக்கு · calendar **read** · voice **output** · 90 நாள் archive · audit rotation

### ❌ எப்போதும் **முடியாதவை** (app அல்லது account இல்லாமல்)
| முடியாதது | தேவை |
|---|---|
| Phone battery · notification · hotspot · Bluetooth | **Android app** |
| Google Calendar-ல் **எழுதுதல்** | OAuth (ஸ்டேஜ் 5) |
| Gmail அனுப்புதல்/படித்தல் | OAuth |
| WhatsApp / SMS | account + ToS மீறல் |
| Bank / UPI / பணம் | — |
| **கைஎழுத்துத் தமிழ் காகிதம் படித்தல்** | OCR/vision — offline-ல் இல்லை |
| Screen-ல் என்ன இருக்கு என்று பார்த்தல் | screen capture + vision — இல்லை |
| Nila மூடியிருக்கும்போது எதையும் செய்தல் | background service — இல்லை |
| "Hey Nila" wake word | எப்போதும் mic — பாதுகாப்பு ஆபத்து |
| ஒரு கேள்விக்கு 100% சரியான பதில் உத்தரவாதம் | எந்த model-உம் தராது |

---

## 5. முடிவு

**ஸ்டேஜ் 2-ல்** சொல்லப்பட்ட 11 விஷயங்கள் **உறுதியாக** முடியும்; **7 முடியாது** —
அவற்றில் **5-ம் ஸ்டேஜ் 3-ல் தீரும்** (voice, calendar read), **2 மட்டும் நிரந்தரம்**
(Nila மூடியிருக்கும்போது alert இல்லை, phone notification இல்லை).

**என் பரிந்துரை:** ஸ்டேஜ் 2-ஐ இப்போது தொடங்குங்கள் — 100% offline, புதிய file மட்டும்,
regression ஆபத்து இல்லை, உங்கள் முதல் கேள்விக்கான (reminder + கணக்கு) நேரடி பதில்.

**தொடங்கட்டுமா?** "ஆமா" என்றால் ஸ்டேஜ் 2-ஐக் கட்டி, acceptance tests + ZIP + push உடன் முடிப்பேன்.
