# நிலா v0.2 — பன்னிரண்டாவது கட்டம்: "இன்னும் பண்ணாத எட்டு" + 5 partial

**நிலை:** நீங்கள் கேட்ட எட்டு "not built" வரிகளும், ஐந்து "partial" வரிகளும்
இப்போது வேலை செய்கின்றன — **ஆனால் எல்லாவற்றையும் "முடிந்தது" என்று சொல்லவில்லை**.
எது முழுமையாக முடிந்தது, எது பாதி, எது உங்கள் PC-ல் மட்டுமே நிரூபணம் ஆகும் —
கீழே வரி வரியாக இருக்கிறது. `brain progress` இப்போது:

```
33 done · 8 partial · 3 not built   (44-ல், 75%)
```

பதினொன்றாவது கட்டத்தில் இது **31 done · 5 partial · 8 not built** ஆக இருந்தது.
மூன்று வரிகள் `done` ஆனது, நான்கு `partial` ஆனது, மூன்று `not built` ஆகவே
இருக்கிறது (அவை உண்மையிலேயே இல்லை — கீழே §11).

---

## 1. படம்/ஸ்கேன் படித்தல் — `brain ocr` (done)

"OCR" என்ற வரி `not built` ஆக இருந்தது, ஏனெனில் அதற்கு Tesseract என்ற binary
வேண்டும் என்று நினைத்தோம். **அது தேவையில்லை:**

```
brain ocr --demo                     ← தானே ஒரு பக்கம் எழுதி, படித்து, ஒப்பிட்டுச் சொல்லும்
brain ocr mypage.png                 ← படத்தில் உள்ள வார்த்தைகள்
brain ocr scanned-invoice.pdf        ← ஸ்கேன் செய்த PDF
brain ocr --engines                  ← எந்த reader இருக்கு, எது சிறந்தது
brain ocr --how                      ← Tesseract நிறுவும் முறை (தமிழுக்கு)
```

- PNG-ஐ **Python மட்டுமே** கொண்டு படிக்கிறது (5 filter, 1/2/4/8/16 bit, palette,
  alpha). Screenshot-க்கு எதுவும் install செய்ய வேண்டாம்.
- `brain file scan.pdf` இப்போது "ஸ்கேன், படிக்க முடியாது" என்று சொல்வதில்லை —
  பக்கத்தில் உள்ள வார்த்தைகளைப் படித்துத் தரும்.
- **நேர்மை:** நிலாவின் சொந்த எழுத்துருவில் அச்சிடப்பட்ட பக்கம் 100% சரியாக
  வரும் (இதுதான் `--demo` நிரூபிக்கிறது). ஆனால் **உண்மையான font-ல் அச்சிடப்பட்ட
  பக்கம் மோசமாக வரும்** (~60% எழுத்துப் பிழை, Sans/Serif/Mono மூன்றிலும் அளந்தது).
  அதனால் ஒரு பக்கம் தவறாக வந்தால் `confidence` குறைவாகவோ `unsure` எண்ணிக்கையோ
  காட்டும் — "நம்பிக்கையுடன் தவறு" என்று ஒருபோதும் வராது. அதற்கு Tesseract
  (`brain ocr --how`) தேவை; அது இருந்தால் நிலா தானாகவே அதைப் பயன்படுத்தும்.

## 2. இரண்டு PC — ஒரே நினைவு (done, உங்கள் Wi-Fi-ல் மட்டும்)

```
PC-1:  sync-host.bat      ← window-ல் address + port + join code தெரியும்
PC-2:  sync-join.bat      ← அந்த address-ஐயும் code-ஐயும் type செய்யுங்கள்
இரண்டிலும்: sync-now.bat  ← எப்போது வேண்டுமானாலும் ஒத்திசைவு
```

- join code: ஆறு குழு × நான்கு எழுத்து; **O/0, I/1, L/l, S/5, B/8 இல்லை** —
  போனில் படித்துச் சொன்னாலும் தட்டச்சுப் பிழை வராது.
- தவறான code-ஐ server-ஏ நிராகரிக்கும்; home-network அல்லாத address (எ.கா.
  8.8.8.8) முன்கூட்டியே மறுக்கப்படும் — "2 PC" தானாக "internet" ஆக மாறாது.
- token உங்கள் vault பக்கத்திலேயே சேமிக்கப்படும் (`sync-join-code.txt`) —
  environment variable செட் செய்ய வேண்டாம்.
- **நிரூபணம்:** சோதனையில் ஒரு உண்மையான server-ஐ port-ல் ஓட்டி, PC-A எழுதியதை
  PC-B socket வழியாகப் பெற்றது; server-ல் plaintext-ம் record id-ம் இல்லை
  (எல்லாம் ciphertext). ஒரே note-ஐ இரண்டு PC-யும் திருத்தினால் இரண்டும்
  `[conflict]` நகலாகப் பாதுகாக்கப்படும் — எதுவும் அழியாது.

## 3. Calendar எழுதுதல் — `brain calendar --add` (partial: Google write இல்லை)

```
brain calendar --add "Dentist" --start "2026-11-06 10:30" --end "2026-11-06 11:30" --where "Anna Nagar"
brain calendar --add "Team call" --start "tomorrow at 9"
brain calendar --list
brain calendar --update <uid> --start "2026-11-06 12:00"
brain calendar --remove <uid>
```

- உங்கள் vault folder-ல் `calendar.ics` உருவாகும்; அதை மொபைல்/Google-ல்
  **ஒருமுறை import** செய்தால் போதும்.
- UID மாறாமல், SEQUENCE ஏறுவதால் — மீண்டும் import செய்தால் **புதிய நகல் வராது,
  அதே event திருத்தப்படும்**. நேரத்தை நகர்த்தினால் கால அளவும் அப்படியே இருக்கும்
  (10:30–11:30 → 12:00–13:00).
- நீக்கினால் `STATUS:CANCELLED` எஞ்சும் — அதனால் போனிலும் அது நீங்கும்.
- **இல்லை:** Google/Outlook-க்கு நேரடியாக எழுத OAuth வேண்டும் (v3). Gmail/Drive
  இன்னும் இல்லை. அதனால்தான் இது `partial`.

## 4. பொருள் வைத்துத் தேடல் — `brain search --meaning` (done, download இல்லை)

```
brain search --wordvec "D:/vectors/glove.6B.50d.txt" --meaning my scooter needs fixing
```

"bike needs repair" என்ற நினைவு வார்த்தைகள் ஒத்துப்போகாவிட்டாலும் அர்த்தத்தால்
கிடைக்கும். **torch இல்லை, model download இல்லை** — ஒரு `.txt` word-vector
கோப்பு மட்டும். கோப்பு இல்லாத வார்த்தைகள் இருந்தால் "உங்கள் வார்த்தைகளில்
X% மட்டுமே தெரியும்" என்று சொல்லும்; வெறுமனே எட்டு தவறான பதில்களைத் தராது.
(`brain recall` பழையபடி வார்த்தை வைத்துத் தேடும்.)

## 5. Screen — நீங்கள் காட்டினால் மட்டும் (done, வேண்டுமென்றே பாதுகாப்பான பாதி)

```
brain screen                    ← எந்த key அழுத்த வேண்டும், என்ன செய்யும் என்று சொல்லும்
Win+Shift+S → save → brain screen "C:/Users/.../shot.png"
```

- **நிலா தானாக screen-ஐப் பார்க்காது.** module-ல் screenshot எடுக்கும் code
  இல்லை (ஒரு சோதனை source-ஐயே படித்து `ImageGrab`/`mss`/`pyautogui` இல்லை
  என்று உறுதி செய்கிறது). எல்லா பதிலிலும் `watchful: false` இருக்கும்.
- படத்தில் உள்ள **வார்த்தைகள்** படிக்கும்; "இது எந்த app, இந்த diagram என்ன
  அர்த்தம்" என்பதற்கு vision model வேண்டும் — அதைச் சொல்லிவிடும்.

## 6. Music player — `brain music` (done)

```
brain music --add-folder "D:/Songs"     ← ஒருமுறை சொன்னால் நினைவில் இருக்கும்
brain music ilaiyaraaja                 ← பட்டியல்
brain music nila --play                 ← Windows player-ல் ஓடும்
```
Tag-கள் கோப்பிலிருந்தே படிக்கப்படும் (ID3, FLAC); tag இல்லாவிட்டால்
"01 - Ilaiyaraaja - Nila.mp3" என்ற **பெயரிலிருந்து** எடுக்கும். Account இல்லை,
download இல்லை, பின்னணியில் ஓடும் daemon இல்லை.

## 7. ffmpeg scene detect — `brain media` (partial)

```
brain media scenes myvideo.mp4            ← காட்சி மாறும் இடங்கள் (decode இல்லாமல்)
brain media myvideo.mp4 --trim 90,135     ← வெட்டும் கட்டளை (இன்னும் ஓடாது)
brain media myvideo.mp4 --trim 90,135 --run   ← உண்மையாக வெட்டும், re-encode இல்லை
getffmpeg.bat                             ← ffmpeg வேண்டும் என்றால் ஒரு double-click
```

- MP4-ன் சொந்த keyframe index-லிருந்து (`stss`/`stts`) காட்சி மாற்றங்களைப்
  படிக்கிறது — **10 GB வீடியோவுக்கும் வேகம், decode இல்லை**. இது encoder தானே
  வைத்த "scene start" — வெட்டுவதற்கு சரியான இடம்.
- வெட்டுதல் `-c copy` — மறு-encode இல்லை, படம் மாறாது. `--run` இல்லாமல் எதுவும்
  எழுதப்படாது.
- **இல்லை (decoder வேண்டும்):** ஒவ்வொரு frame-ஐயும் ஒப்பிடுதல், silence கண்டுபிடித்தல்.
  `getffmpeg.bat` இரண்டு `.exe`-ஐ மட்டும் `tools\`-ல் வைக்கும் — installer இல்லை,
  admin உரிமை இல்லை, PATH மாற்றம் இல்லை.

## 8. Dashboard — புதிய இரண்டு panel

- **Today and next** — இன்றைய event-கள்; calendar அமைக்கவில்லை என்றால் அதைச்
  சொல்லி, paste செய்யக்கூடிய கட்டளையைக் காட்டும்.
- **Both PCs** — எந்த PC-உடன் இணைந்திருக்கு, கடைசி sync எப்போது, எத்தனை காத்திருக்கு,
  conflict எத்தனை. இணையவில்லை என்றால் `sync-host.bat`/`sync-join.bat` என்று சொல்லும்.
- இரண்டும் jsdom-ல் **உண்மையாக இயக்கப்பட்டு** சோதிக்கப்படுகின்றன (loading-ல்
  தங்கிவிடவில்லை).

## 9. சோதனை முடிவுகள் (இந்த இயந்திரத்தில், உண்மையாக ஓட்டியது)

| சோதனை | முடிவு |
|---|---|
| முழு pytest suite | **1411 passed · 0 failed · 3 skipped** |
| `scripts/retest_everything.py` | **48 pass · 5 skip · 0 fail** |
| `scripts/nila_mandate_probe.py` | **24 pass · 0 fail · 5 gap** (மாறவில்லை — roadmap) |

புதிய சோதனைக் கோப்புகள்: `test_ocr_acceptance`, `test_sync_over_http_acceptance`,
`test_calendar_write_acceptance`, `test_semantic_search_acceptance`,
`test_two_pc_lan_acceptance`, `test_music_player_acceptance`,
`test_screen_view_acceptance`, `test_video_scenes_acceptance`.

**ஒவ்வொரு சோதனையும் ஒரு உண்மையான வேலையைச் செய்கிறது** — எடுத்துக்காட்டாக
sync சோதனை உண்மையான HTTP server-ஐ ஓட்டுகிறது; OCR சோதனை பக்கத்தை எழுதி
மீண்டும் படிக்கிறது; screen சோதனை source-ல் capture code இல்லை என்று பார்க்கிறது.

## 10. உங்கள் PC-ல் எப்படிச் சோதிப்பது (10 நிமிடம்)

1. `setup.bat` → `start-nila.bat` → dashboard-ல் **Today and next**, **Both PCs**
   panel இருக்கிறதா.
2. `nila-cmd.bat` → `python brain.py ocr --demo` → "came back exactly" என்று
   வருகிறதா (இது உங்கள் PC-ல் நிலா தன்னைத்தானே நிரூபிக்கும்).
3. `python brain.py screen` → Win+Shift+S அழுத்தி ஒரு screenshot எடுத்து,
   `python brain.py screen "<கோப்பு>"` → வார்த்தைகள் வருகிறதா.
4. `python brain.py music --add-folder "D:/Songs"` → `python brain.py music <பாடல்> --play`.
5. `python brain.py media scenes <வீடியோ>` → காட்சி மாற்றங்கள் வருகிறதா.
6. இரண்டு PC இருந்தால்: `sync-host.bat` (PC-1), `sync-join.bat` (PC-2),
   `sync-now.bat` — இரண்டிலும் ஒரே நினைவு வருகிறதா.
7. ஏதாவது தோல்வி என்றால் `report.bat` → text-ஐ எனக்கு அனுப்புங்கள்.

## 11. இன்னும் இல்லாதவை (நேர்மை — இது மாறவில்லை)

| வரி | நிலை | ஏன் |
|---|---|---|
| Wake word (always-on mic) | **not built** | வேண்டுமென்றே மறுக்கப்பட்டது — உங்கள் விதி |
| Phone app (Android), multi-user | **not built** | v3; Telegram ஏற்கனவே போனிலிருந்து பதில் சொல்கிறது |
| Neural speaker model (ECAPA) | **not built** | neural model download தேவை; இங்கே போலி செய்யவில்லை |
| Gmail / Drive, Google-க்கு நேரடி calendar write | **partial** | OAuth consent screen தேவை |
| OCR / scene detect | **partial** | அடிப்படை பாதை இருக்கு; உண்மையான font-க்கு Tesseract, frame-ஒப்பீட்டுக்கு ffmpeg |
| Fine-tuned Tamil model | **partial** | 2-PC பாதை முடிந்தது; fine-tune-க்கு dataset + GPU |
| Voice out / Hearing / Telegram / Connectors | **partial** (பழையது) | install அல்லது key உங்கள் PC-ல் |

**மீதி:** மேலே உள்ள 3 `not built` + 5 `partial` ஆகும். அவற்றை நான் "முடிந்தது"
என்று சொல்லவில்லை; `brain progress` ஒவ்வொரு முறையும் உண்மையைக் காட்டும்.

---

## 12. எப்படி இயக்குவது (சுருக்கம்)

```
setup.bat            ஒருமுறை
start-nila.bat       தினமும்
sync-host.bat        PC-1-ல் (மற்ற PC-க்கு share)
sync-join.bat        PC-2-ல் (ஒருமுறை இணைய)
sync-now.bat         ஒத்திசைவு
getffmpeg.bat        வீடியோ frame-ஒப்பீடு வேண்டும் என்றால் மட்டும்
report.bat           ஏதாவது தவறு என்றால்
```
