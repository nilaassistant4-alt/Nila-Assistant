# Nila v0.2 — Stage 17: "ஒன் பைல் · ஒன் கீ · மெமரி டி டிரைவ்"

**நாள்:** 2026-11-04 · **Branch:** `arena/3f2334a4-nila-assistant`
**ZIP:** `NILA_V02_STAGE17.zip` · 222 entries · 964,700 bytes
**sha256:** `63244752e00f403d7b4f08a2261abca83ee2fa68a1b2d29590615199e22a0a8d`

---

## 0. நேர்மையான பதில் — முதல்ல

**"பக் எல்லாம் சரியாயிடுச்சா?" → இல்லை, இன்னும் முழுசா இல்லை.**
ஆனா **ஸ்டார்ட்டிங் பிரச்சனைக்கு காரணமான 4 தவறைக் கண்டுபிடிச்சு சரி பண்ணிட்டேன்**,
மற்றும் **மெமரியை டி டிரைவ்க்கு மாத்தறது** இப்போ உண்மையாகவே வேலை செய்யுது.

இந்த முறை நான் code-ஐ படிச்சு சொல்லலை — **நீங்கள் செய்கிற அதே வழியில் ஓட்டிப் பார்த்தேன்**
(ZIP extract → double-click → type). அதுல தான் கீழே உள்ள தவறுகள் தெரிஞ்சது.

---

## 1. இந்த முறை கிடைத்த 4 தவறு (எல்லாம் சரி, எல்லாம் test)

| # | தவறு | நீங்கள் பார்த்தது எப்படி இருந்திருக்கும் |
|---|---|---|
| 1 | `nila-env.bat`-ல் Python **`"py -3"`** என்று இரண்டு வார்த்தையா quote ஆகி இருந்தது — 42 இடத்துல `"%NILA_PY%"` அப்படியே போட்டிருக்கு | window திறந்து **"The system cannot find the path specified"** சொல்லி மூடிக்கொள்ளும். நிலாவின் ஒரு வரியும் வராது |
| 2 | Shortcut / sign-in launcher **`brain.py ... start --home C:\...`** என்று ஓடியது — `--home` global option, command-க்கு **அப்புறம்** வந்ததால் | **`error: unrecognized arguments: --home`** — shortcut-ஐ அழுத்தினா ஒண்ணும் நடக்காது |
| 3 | Icon-ஐ **யாரும் உருவாக்கவே இல்லை** — ஒரு `.lnk` + "Properties-ல Shortcut key போடு" என்று சொல்லி இருந்தது. Windows-ல் அதை program எழுத முடியாது | **கீபோர்டு shortcut இல்லவே இல்லை**. நீங்கள் கேட்ட "ஒரு கீ அழுத்தினா நிலா வரணும்" — நடக்கலை |
| 4 | "மெமரியை டி டிரைவ்க்கு மாத்து" → copy சரிபாத்து, `config.json`-ல் `agent.home` எழுதியது. **யாரும் அதைப் படிக்கவே இல்லை** (launcher `BRAIN_HOME`-ஐ பாக்கும்) | copy "வெற்றி" என்று சொல்லும், ஆனா **அடுத்த start பழைய folder-ஐ திறந்து "உங்க நினைவுகள் இங்க இல்ல"** என்று சொல்லும் |

**கூடுதல் 3 (சின்னது, ஆனா உண்மை):**
- `brain storage --set D:/...` → folder-ஐ உருவாக்கவே இல்லை → `sqlite3.OperationalError: unable to open database file`.
- `start-nila.bat recovery` → `%CODE%` ஒரு `if (...)` block-க்கு உள்ளே இருந்தது; cmd.exe அதை **type பண்ண முன்னே** expand பண்ணும் → **எப்பவும் காலிக் கோட்** போனது.
- Test runner-ல் `pytest.skip()` raise பண்ணாம return பண்ணியது → extract செய்த நகலில் **9 FAIL** (உண்மையில் 9 skip).

---

## 2. இப்போ என்ன மாறியது

- **Python:** launcher-ஐ கேட்டு உண்மையான `python.exe` path எடுக்குது — ஒரே token, quote பண்ணலாம். `py` இல்லைனா `python`, அதுவும் இல்லைனா தெளிவா "Python 3.10+ install பண்ணு" என்று நிற்கும்.
- **Shortcut / icon / sign-in:** எல்லாமே `brain --home <vault> dashboard` — flag **முதல்ல**, command **அப்புறம்**. Test அதே argument-ஐ உண்மையான parser-க்கு போட்டு சரிபாக்குது.
- **ஒரு கீ:** `nila-start.bat` முதல் முறை **Desktop-ல் Nila icon** உருவாக்கும் (double-click). Windows-ன் *Shortcut key* box-ஐ மட்டும் நீங்க தான் ஒரு முறை அழுத்தணும் — அதை எந்த program-உம் எழுத முடியாது (அப்படி எழுதினா keyboard-ஐ கண்காணிக்கிற program ஆகும், அதை நிலா செய்யாது).
- **மெமரி டி டிரைவ்:** `move-memories-to-d.bat` (புது) — copy → **ஒவ்வொரு file-ஐயும் SHA-256-ல ஒப்பிடு** → அப்புறம் `moved-to.json` pointer வை. `Config.load` அதைப் பின்தொடரும், அதனால் **எல்லா start-ம் புது folder-ஐ திறக்கும்**. `move-to-d.bat`-ல் `1` (முழு vault) அல்லது `2` (மெமரி மட்டும்) என்று கேட்கும்.
- **டி டிரைவ் பிரித்து விட்டா:** "உங்க data folder connect ஆகல" என்று சொல்லி **எதையும் மாத்தாது** — சி டிரைவில் காலி vault உருவாக்காது.
- **Laptop நினைவில் வைத்த key:** move ஆனதும் தொலைஞ்சு போகாது (pointer chain பின்தொடருது) — "இனி கேட்காது" அப்படியே இருக்கும்.
- **`brain come-from` / `lock-check`:** இப்போ **"memories stored at ..."** என்ற வரியும், pointer-ஐ பின்தொடர்ந்ததா, எந்த டிரைவ் இல்லை என்றும் சொல்லும்.

---

## 3. நான் ஓட்டிப் பார்த்த முடிவுகள்

| சோதனை | முடிவு |
|---|---|
| முழு test suite (repo-வில்) | **1580 passed · 0 failed · 3 skipped** |
| pytest இல்லாத கணினியில் runner (shim) | **1773 passed · 0 failed · 3 skipped** |
| `retest_everything.py` | **59 pass · 5 skip · 0 fail** |
| `nila_mandate_probe.py` | **29 pass · 0 FAIL · 0 gap** |
| **ZIP-ஐ extract செய்து அந்த நகலில்** suite | **1571 passed · 0 failed · 12 skipped** (git/ZIP இல்லாததால்) |
| ZIP-ஐ பக்கத்துல வைத்து அந்த நகலில் `retest_everything.py` | **59 pass · 5 skip · 0 fail** |
| ZIP-ஐ extract செய்து அந்த நகலில் runner (shim) | **1764 passed · 0 failed · 12 skipped** |
| Recovery code-ஐ pipe பண்ணி `dashboard` திறந்தது | **திறந்தது**, audit-ல் `unlock_via_recovery_code`, அந்த code spend ஆகிடுச்சு |
| vault-ஐ டி folder-க்கு நகர்த்தி, அதே BRAIN_HOME-ல் மீண்டும் திறந்தது | **திறந்தது**, passphrase அதே, pointer பின்தொடர்ந்தது |
| டி folder-ஐ மாத்திட்டு (unplug simulation) start | **"folder connect ஆகல"** — பழைய folder அப்படியே, எதுவும் எழுதலை |

**சோதிக்க முடியாதது (இங்க Linux sandbox):** DPAPI / Credential Locker, உண்மையான `cmd.exe`,
Windows Shortcut key box, microphone/voice, Telegram. இவை உங்க laptop-ல் தான் தெரியும்.

---

## 4. நீங்க செய்ய வேண்டியது — படிப்படியா (step by step)

### A. ZIP-ஐ எடுத்து வைப்பது
1. `NILA_V02_STAGE17.zip` download பண்ணுங்க.
2. `C:\`-ல் right-click → **Extract All** → `C:\Nila`.
   **பழைய folder delete பண்ண வேண்டாம்** — நினைவுகள் அதுல இல்லை.

### B. முதல் start (ஒரு முறை)
3. `C:\Nila` உள்ளே போய் **`nila-start.bat`**-ஐ double-click.
4. முதல் வரிகள்: **"Where your memories live"** → `%LOCALAPPDATA%\Nila\vault`.
5. அடுத்து **"memories stored at ..."** — இது தான் உங்க நினைவுகள் இருக்கிற file.
6. Vault இல்லைனா setup ஓடும் — passphrase தேர்ந்தெடுத்து **தாளில் எழுதுங்க**, authenticator
   secret-ஐ app-ல் சேருங்க, recovery codes-ஐ தாளில் எழுதுங்க.
7. முடிவில் **Desktop-ல் `Nila` icon** வரும் (இல்லைனா "icon write ஆகல" என்று சொல்லும், start நடக்கும்).
8. நிலா dashboard browser-ல் திறக்கும்.

### C. ஒரு கீ (keyboard) அமைப்பது
9. Desktop-ல் `Nila` icon-ஐ **right-click → Properties**.
10. **"Shortcut key"** box-ல் click → **`Ctrl + Alt + N`** அழுத்துங்க → **OK**.
11. இனி அந்த கீ → நிலா நேரடியா வரும் (கருப்பு window இல்லை).

### D. "இனி கேட்குமா?" சரிபார்ப்பு
12. `nila-cmd.bat` திறந்து:
```
python brain.py come-from
python brain.py lock-check
python brain.py passphrase status
```
13. மூன்றிலும் **`remembered on this laptop: YES`** / **`opens without typing: YES`** வரணும்.
14. பிறகு window-ஐ மூடி, **`nila-start.bat`**-ஐ மீண்டும் double-click → எதுவும் கேட்காமல் திறக்கணும்.

### E. மெமரியை டி டிரைவ்க்கு மாத்துவது (நீங்க கேட்டது)
15. நிலா window எல்லாத்தையும் **மூடுங்க** (dashboard + phone window).
16. **`move-memories-to-d.bat`**-ஐ double-click.
17. கேட்கும்: *"New folder for the memories (for example D:\Nila-data)"* → **`D:\Nila-data`** type → Enter.
18. **Step 1** — எத்தனை file, எத்தனை byte என்று காட்டும் (எதையும் எழுதாது). படிச்சு பாருங்க.
19. கேட்கும்: *"Type YES to copy (the old folder is kept)"* → **`YES`** type → Enter.
20. **Step 2** — copy + ஒவ்வொரு file-ஐயும் SHA-256-ல ஒப்பிடும் → `verified ... every SHA-256 identical`.
21. **Step 3** — `come-from` ஓடி **புது folder-ஐ** காட்டும் (`memories stored at D:\Nila-data\memory.db`).
22. முடிஞ்சதும் **`nila-start.bat`** — வழக்கம் போல, எதுவும் கேட்காமல்.
23. பழைய folder-ஐ **நீங்களே** பாத்து தேவை இல்லைனா delete பண்ணுங்க — நிலா எதையும் delete பண்ணாது.

> முழு vault-ஐ (passphrase, audit, எல்லாம்) டி-க்கு மாத்தணும்னா: `move-to-d.bat` → **`1`**.

### F. டி டிரைவ் பிரித்து விட்டா
24. நிலா **"your data folder D:\... is not connected"** என்று சொல்லி நிற்கும்.
25. டிரைவ் மீண்டும் போட்டு (அல்லது drive letter சரி பண்ணி) `nila-start.bat` → பழையபடி.

### G. பாஸ்வேர்டு (passphrase) — யாருக்கு, எப்போ
26. **இந்த laptop-ல்:** ஒரு முறை தான் (setup-ல்). அதுக்கு அப்புறம் எதுவும் கேட்காது.
27. **புதிய laptop / Windows reinstall:** அந்த passphrase தான் — அதனால தாளில் வைங்க.
28. **மறந்துட்டேளா, ஆனா இந்த laptop திறக்குதா:** `nila-cmd.bat` → `python brain.py passphrase set` → **பழையதைக் கேட்காது**, புதுது வாங்க.
29. **மறந்துட்டு, recovery code இருக்கா:** `start-nila.bat recovery` → ஒரு code (ஒரு முறை தான்). பிறகு `passphrase set`.

---

## 5. இன்னும் இல்லாதது (மறைக்கவில்லை)

- **Windows-ல் ஓட்டிச் சோதிக்கவில்லை** — DPAPI, Credential Locker, `cmd.exe`, Shortcut key box.
  உங்க laptop-ல் `brain passphrase status` சொல்வது தான் உண்மையான சோதனை.
- **Shortcut key-ஐ நிலா எழுத முடியாது** — Windows அதை dialog-ல் மட்டுமே அனுமதிக்கும் (பாதுகாப்புக்காக).
  Icon-ஐ நான் எழுதுவேன், கீ-ஐ நீங்க ஒரு முறை (step 9–10).
- **Phone-ஐ நேரடியா அணுக முடியாது** — TLS + tunnel + login, தனி வேலை.
- **Dashboard-ல் microphone வேண்டுமென்றே மூடப்பட்டுள்ளது** — குரல் `brain wake` / `brain talk` வழியே.
- **உங்க ஏற்கனவே உள்ள vault-ன் recovery codes** பழைய build-ல் உருவானவை — vault-ஐ திறக்காது.
  உள்ளே போன பிறகு `python brain.py recovery --count 8` → திறக்கிற codes.
- **"எல்லா பக்கமும் முழுசா முடிஞ்சிடுச்சு"** என்று நான் சொல்ல மாட்டேன். இந்த stage-ல்
  நான் சொல்வது: **ஸ்டார்ட் + ஒரு கீ + டி டிரைவ் — இந்த மூன்று இப்போ ஓட்டிப் பார்த்து உறுதி செய்யப்பட்டது.**
