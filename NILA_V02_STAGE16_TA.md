# Nila v0.2 — Stage 16: "ஒரு முறை கேட்கணும்" — இப்போது உண்மையாகவே

**நாள்:** 2026-11-04 · **Branch:** `arena/3f2334a4-nila-assistant`
**ZIP:** `NILA_V02_STAGE16.zip` · 221 entries · 949,723 bytes
**sha256:** `5d921dcb89b5fd200e8abf7dd3f7134e240a7ef593f2368cb370c0d32c536af4`

---

## 0. முதலில் ஒரு நேர்மையான வாக்குமூலம்

Stage 14-ல் நான் "இப்போது ஒரு முறை மட்டும்தான் கேட்கும்" என்று சொன்னேன். ஆனால்
அந்த code-ல் **மூன்று தவறு** இருந்தது. இப்போது அவற்றை ஓட்டிப் பார்த்து, சரி செய்தேன்:

| # | தவறு | அதனால் என்ன நடந்தது |
|---|---|---|
| 1 | `vaultkey.save()` — key சேமித்ததும், அதைத் **திரும்பப் படிக்காமல்** "saved" என்று சொன்னது | `brain passphrase status` → **"remembered: YES"** என்று சொல்லி, அடுத்த start-ல் **மீண்டும் password கேட்டது** |
| 2 | `change-passphrase` **பழைய passphrase-ஐ** முதலில் கேட்டது | நீங்கள் மாற்றியதாக நினைத்தது **நடக்கவே இல்லை** → "பழைய password இன்னும் வேலை செய்யுது" |
| 3 | vault எங்கே இருக்கிறது என்பதை எந்தத் திரையும் சொல்லவில்லை | ZIP extract செய்து start செய்தால், புதிய vault போலத் தோன்றும் — உண்மையில் பழையது `%LOCALAPPDATA%\Nila\vault`-ல் இருக்கும் |

இந்த மூன்றும் சரி. கீழே proof உண்டு.

---

## 1. "ஸ்டார்ட்டிங் பிரச்சனை" — இப்போது என்ன நடக்கும்

நீங்கள் ZIP-ஐ extract செய்து **`nila-start.bat`**-ஐ மட்டும் double-click செய்யுங்கள்.
அது இதைச் செய்யும் (இதை மாற்ற வேண்டாம், எதையும் delete செய்யாது):

1. **வால்ட் folder எங்கே** என்று சொல்லும் — `%LOCALAPPDATA%\Nila\vault`
   (ZIP folder-க்கு உள்ளே இல்லை; அதனால் re-extract செய்தாலும் நினைவுகள் போகாது).
2. **இந்தக் கணினியில் என்ன இருக்கிறது** என்று ஒரு திரையில் சொல்லும்
   (`brain come-from`): vault இருக்கா, இந்த laptop நினைவில் வைத்திருக்கா, recovery
   code எத்தனை மீதம் + **அவற்றில் எத்தனை உண்மையில் vault-ஐத் திறக்கும்**, passphrase
   இங்கே எப்போதாவது மாற்றப்பட்டதா, folder நகர்த்தப்பட்டதா.
3. Vault **இல்லை** என்றால் மட்டும் setup ஓடும் (passphrase ஒரு முறை கேட்கும்).
4. நினைவில் வைக்கவில்லை என்றால் `passphrase remember` ஓடும் — இப்போது **சேமித்ததைத்
   திரும்பப் படித்து உறுதி செய்த பிறகுதான்** "OK" சொல்லும்.
5. திறக்கவில்லை என்றால் **recovery code** கேட்கும் (passphrase இல்லாமல் திறக்கும்),
   அல்லது "இது தான் பிரச்சனை" என்று தெளிவாகச் சொல்லி நிற்கும் — கருப்பு சாளரம்
   மூடிக்கொண்டு போகாது.

---

## 2. Proof — நான் ஓட்டிப் பார்த்தது (இவை மட்டுமே உண்மை)

| சோதனை | முடிவு |
|---|---|
| சேமித்த key-ஐத் திரும்பப் படிக்காத store | **`VaultKeyError`** — பொய்யாக "saved" சொல்லாது |
| saved key மூலம் vault திறந்து `change-passphrase` | **பழைய passphrase கேட்கவில்லை**; மாற்றிய பிறகு **பழையது வேலை செய்யவில்லை**, புதியது செய்கிறது |
| `brain come-from` | 4 நிலைகளையும் சரியாக அடையாளம் காட்டியது (vault இல்லை / நினைவில் இல்லை / நினைவில் உள்ளது / code பயன்படாதது) |
| `Nila-v0.1.0.zip` உருவாக்கிய vault | **இன்றைய build-ல் அதே passphrase-ல் திறக்கிறது** (kdf n=4096) — re-extract = புதிய vault கிடையாது |
| முழு test suite | **1556 passed · 0 failed · 3 skipped** |
| pytest இல்லாத கணினியில் runner | **1750 passed · 0 failed** |
| `retest_everything.py` | **56 pass · 5 skip · 0 fail** |
| `nila_mandate_probe.py` | **29 pass · 0 FAIL · 0 gap** |
| ZIP-ஐ extract செய்து **அந்த நகலில்** suite ஓட்டியது | **1547 passed · 0 failed · 12 skipped** (12-ம் git/ZIP இல்லாததால்; ZIP-ஐ பக்கத்தில் வைத்து `retest_everything.py` → **56 pass · 5 skip · 0 fail**) |

**சோதிக்க முடியாதது:** Windows-ல் இயக்கவில்லை (sandbox Linux). DPAPI, Credential
Locker, `cmd.exe` — உங்கள் laptop-ல் `brain passphrase status` சொல்வதே உண்மையான சோதனை.

---

## 3. நீங்கள் செய்ய வேண்டியது — படிப்படியாக (step by step)

### A. ZIP-ஐ எடுத்து வைப்பது

1. `NILA_V02_STAGE16.zip`-ஐ download செய்யுங்கள்.
2. `C:\`-ல் right-click → **Extract All** → `C:\Nila` (புதிய folder).
   **பழைய folder-ஐ delete செய்ய வேண்டாம்.** உங்கள் நினைவுகள் அதில் இல்லை —
   `%LOCALAPPDATA%\Nila\vault`-ல் இருக்கு.

### B. முதல் start

3. `C:\Nila`-க்குள் போய் **`nila-start.bat`**-ஐ double-click செய்யுங்கள்.
   (மற்ற .bat களை இப்போது தொட வேண்டாம்.)
4. திரையில் முதல் 5 வரிகள்: "Where your memories live: ..." — அதைப் படியுங்கள்.
5. `What is on this laptop` பகுதியில் வருவதைப் பாருங்கள்:
   - `vault : ...` → vault இருக்கிறது (பழையது கிடைத்தது) ✅
   - `opens without typing : no` → இனி அது ஒரு முறை சரி செய்யும்.
6. Vault இல்லை என்றால் setup ஓடும்: passphrase தேர்ந்தெடுங்கள் (**தாளில் எழுதுங்கள்**),
   authenticator secret-ஐ app-ல் சேருங்கள், recovery codes-ஐத் தாளில் எழுதுங்கள்.
7. `nila-start.bat` முடிவில் vault திறக்கும் (dashboard browser-ல் வரும்).

### C. "இனி கேட்குமா?" என்று சரிபார்ப்பது

8. `nila-cmd.bat`-ஐத் திறந்து:
   ```
   python brain.py come-from
   python brain.py lock-check
   python brain.py passphrase status
   ```
9. மூன்றிலும் **"opens without typing: YES"** / `remembered on this laptop: YES`
   என்று வர வேண்டும். வந்தால் — restart செய்து பாருங்கள்: `nila-start.bat` எதுவும்
   கேட்காமல் நிலாவைத் திறக்கும்.

### D. பழைய password மறந்துவிட்டால் (இப்போது சரியாக வேலை செய்யும்)

10. `nila-cmd.bat` →
    ```
    python brain.py passphrase set
    ```
    → **பழைய passphrase கேட்காது** (laptop-ல் key இருந்தால்). புதியதைத் தாளில் எழுதுங்கள்.
11. சரிபார்ப்பு: `python brain.py passphrase forget` → இப்போது புதிய passphrase-ஐக்
    கேட்கும். அது வேலை செய்தால், `python brain.py passphrase remember` மீண்டும்.
12. Recovery code இருந்தால்: `start-nila.bat recovery` → ஒரு code (ஒரு முறை மட்டும்).

### E. Express / daily

13. அன்றாடம்: **`nila-start.bat`** ஒன்றே போதும் (அல்லது desktop-ல் `Nila` icon).
14. Shortcut வேண்டுமா: `nila-easy-start.bat` ஒரு முறை → icon + `Ctrl+Alt+N`.

---

## 4. இன்னும் இல்லாதது (மறைக்கவில்லை)

- Windows-ல் இயக்கிச் சோதிக்கவில்லை (DPAPI / Credential Locker / `cmd.exe`).
- Phone-ஐ நேரடியாக அணுகும் வசதி — TLS + tunnel + login தேவை, தனி வேலை.
- Dashboard-ல் microphone வேண்டுமென்றே மூடப்பட்டுள்ளது; குரல் `brain wake` /
  `brain talk` மூலம்.
- **உங்கள் ஏற்கனவே உள்ள vault-ல் உள்ள recovery codes** பழைய build-ல் உருவானவை —
  அவை vault-ஐத் திறக்காது. உள்ளே போன பிறகு `python brain.py recovery --count 8`
  ஓட்டினால் திறக்கும் codes கிடைக்கும் (`come-from` இதைச் சொல்லும்).
