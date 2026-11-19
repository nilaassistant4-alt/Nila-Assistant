# Nila v0.2 — Stage 14: "கடவுச்சொல் ஒரு முறை மட்டும்"

**நாள்:** 2026-11-04
**நீங்கள் சொன்னது:** "unzip பண்ணி, extract பண்ணி, அப்புறம் password கேட்குது; திரும்பத்
திரும்பக் கேட்குது. நான் அந்த password-ஐ மறந்துட்டேன்; பழைய password இன்னும் வேலை
செய்யுது. எப்படி மாற்றுவதுன்னு தெரியல. ஒரு முறை மட்டும் type பண்ணா போதும்; restart
பண்ணிட்டு வந்தாலும் கேட்கக் கூடாது. விசைப்பலகை shortcut-ல் நிலா வரணும், இல்லாட்டி
என் குரல்-ல வரணும். Vault போய்விடக் கூடாது."

முதலில் **விளக்கம்**, அப்புறம் **என்ன கட்டப்பட்டது**. கடைசியில் **நீங்கள் என்ன
செய்ய வேண்டும்** — எண் வரிசையில்.

---

## 1. முதலில் விளக்கம் — vault, password, key (short, தமிழ்)

**Vault** = `%LOCALAPPDATA%\Nila\vault` folder. உங்கள் நினைவுகள் (reminders, notes,
அப்பாயிண்ட்மெண்ட்) அனைத்தும் **encrypt** செய்யப்பட்டு அங்கே இருக்கு. ZIP-ஐ
extract செய்வதற்கும் இதற்கும் சம்பந்தம் இல்லை — ZIP-ஐ எத்தனை முறை வேண்டுமானாலும்
extract செய்யுங்கள், vault அதே இடத்தில், அப்படியே இருக்கும். (அதனால்தான் அந்தப்
பழைய vault-ஐ நீங்கள் தொடர்ந்து வைத்துக்கொள்ளலாம்.)

**Password (passphrase)** = vault-ஐத் திறக்கும் சாவி. ஆனால் உண்மையில் உள்ளே
நடப்பது இதுதான்:

```
உங்கள் passphrase  ──(scrypt)──►  master key  ──►  நினைவுகள் எல்லாம் encrypt
```

நினைவுகள் **passphrase-ஆல்** encrypt ஆகவில்லை; ஒரு **master key**-ஆல் encrypt
ஆகின்றன. Passphrase அந்த master key-ஐ மூடி (wrap) வைக்கிறது. இது மிக முக்கியம்,
ஏனெனில் இதனால்தான்:

- passphrase மாற்றினால் **எந்த நினைவும் அழியாது** (master key அதே; சுற்றியுள்ள
  உறை மட்டும் புதுசு),
- passphrase-ஐ நினைவில் வைக்க வேண்டிய அவசியமே இல்லை, **master key-ஐ** laptop
  நினைவில் வைத்தால் போதும்.

**ஏன் திரும்பத் திரும்பக் கேட்டது?** பழைய build-ல் எந்த command-க்கும் vault
மீண்டும் திறக்கப்பட்டது, ஒன்றும் நினைவில் வைக்கப்படவில்லை. எனவே ஒவ்வொரு
தடவையும் passphrase கேட்டது. அது ஒரு bug அல்ல — அந்த வசதியே இல்லை.

**"மறந்துட்டேன், பழையது இன்னும் வேலை செய்யுது"** — இது **உண்மையான bug**.
`rotate_passphrase`-க்கு பழைய passphrase தேவைப்பட்டது; நீங்கள் மாற்றியதாக
நினைத்தது உண்மையில் நடக்கவில்லை. இப்போது சரி செய்யப்பட்டது.

**Vault போய்விடுமா?** இல்லை — நீங்கள் எதையும் delete செய்ய வேண்டாம், நான்
செய்யவில்லை, இந்த build-ல் எந்த command-ம் vault-ஐ அழிக்காது. ஆனால் ஒரு
நேர்மையான எச்சரிக்கை: **passphrase + recovery codes + backup மூன்றும் இல்லாமல்
போனால்**, யாராலும் (நானும்) அதைத் திறக்க முடியாது. அதற்குத்தான் `backup-to-d.bat`
இருக்கிறது.

---

## 2. என்ன கட்டப்பட்டது — மூன்று வழிகள்

| # | உங்கள் கோரிக்கை | நிலை | எப்படி |
|---|---|---|---|
| 1 | முதல் முறை **ஒரு முறை** type; restart-க்குப் பிறகும் கேட்கக் கூடாது | **built** | `brain init` + Windows Credential Locker / DPAPI-ல் key சேமிப்பு |
| 2 | **விசைப்பலகை shortcut** → நிலா வரணும் | **built** (ஒரு கை உங்களது) | `brain hotkey --write` → desktop-ல் Nila icon → Windows-இன் "Shortcut key" box-ல் `Ctrl+Alt+N` |
| 2b | இல்லையேல் **குரல்** → நிலா வரணும் | **built** | `brain wake --window 120` ("நிலா" சொல்லுங்கள்), `brain talk` (ஒரு வாக்கியம்) |
| 3 | மறந்த password-ஐ சமாளிக்க | **built** | `brain passphrase set` — பழையது தேவையில்லை, நினைவுகள் அழியாது |
| 4 | restart-க்குப் பிறகு நிலாவே வந்திருக்கணும் | **built** (opt-in) | `brain hotkey --startup` — sign-in-ல் தானே dashboard |
| 5 | vault போய்விடக் கூடாது | **built** (எல்லை வரை) | vault-ஐ அழிக்கும் command இல்லை; `backup-to-d.bat`; `brain backup` |

### புதிய கோப்புகள் / commands

| கோப்பு / command | என்ன செய்கிறது |
|---|---|
| `nila-first-run.bat` | **முதல் முறை இதை மட்டும் double-click.** setup + laptop-ஐ நினைவில் வை + "இனி கேட்குமா?" என்று பதில் |
| `nila-easy-start.bat` | **ஒவ்வொரு நாளும் இது.** shortcut + voice + check + நிலா |
| `start-nila.bat` | பழையது போலவே; ஆனால் இப்போது "no passphrase needed" அல்லது "nila-first-run.bat ஒருமுறை" என்று உண்மையைச் சொல்லும் |
| `brain lock-check` | ஒரு திரை: vault எங்கே, இனி கேட்குமா, ஆமா/இல்லையா |
| `brain passphrase status` | `remembered on this laptop: YES / no` |
| `brain passphrase remember` | இப்போதே நினைவில் வை (setup செய்திருந்தால் தேவையில்லை) |
| `brain passphrase forget` | மீண்டும் கேட்கவை (நினைவுகள் அழியாது) |
| `brain passphrase set` | **புதிய passphrase** — பழையது தேவையில்லை |
| `brain hotkey --write` | shortcut கோப்பு + `Ctrl+Alt+N` எப்படி வைப்பது என்று step-by-step |
| `brain hotkey --startup` | sign-in-ல் நிலா தானே வரும் (`--no-startup` நீக்கும்) |
| `brain hotkey --voice` | குரல்-பகுதிக்கு என்ன தேவை என்று |

`--no-saved-key` flag: எந்த command-க்கும் கொடுத்தால், அந்த ஒரு தடவை மட்டும்
நினைவில் வைத்த key-ஐப் பயன்படுத்தாது — passphrase கேட்கும் (உதாரணமாக வேறு
ஒருவர் முன்னால்).

---

## 3. "நினைவில் வைத்தல்" என்றால் என்ன, என்ன ஆபத்து (நேர்மை)

- சேமிப்பது **passphrase அல்ல — master key**. Windows-ல்: Credential Locker
  (`keyring`), இல்லாவிட்டால் **DPAPI** (உங்கள் Windows account-உடன் பிணைந்த
  `device-key.dpapi`).
- இது **உங்கள் laptop, உங்கள் Windows account**-க்கு மட்டும். வேறு laptop,
  வேறு account → passphrase கேட்கும். அதனால் தாள் copy அவசியம்.
- **திறந்த மடிக்கணினியில்** யாராவது அமர்ந்தால் நிலா திறக்கும். அதுதான்
  "remember me" என்பதன் அர்த்தம்; நான் அதை மறைக்கவில்லை. Journal-ல்
  `unlock_via_device_key` என்று தனியாகப் பதிவாகும் — "நீங்கள் type செய்தீர்களா,
  laptop-ல் சாவி இருந்ததா" என்ற வித்தியாசம் என்றும் காணாமல் போகாது.
- Microphone **sign-in-ல்** ஒருபோதும் திறக்காது. `brain hotkey --startup`
  dashboard-ஐ மட்டுமே தொடங்கும்; குரல் வேண்டுமானால் `nila-easy-start.bat --voice`.

---

## 4. சோதனை முடிவுகள் (இவை மட்டுமே உண்மை)

| சோதனை | முடிவு |
|---|---|
| முழு test suite | **1459 passed · 0 failed · 3 skipped** (stage 13: 1435 → +24) |
| `scripts/retest_everything.py` | **57 pass · 5 skip · 0 fail** (2 புதிய stage-14 check) |
| `scripts/nila_mandate_probe.py` | **29 pass · 0 FAIL · 0 gap · 0 error** |
| `brain progress` | **39 done · 6 partial · 3 not built** (48-ல், 81%) |

இந்தக் கட்டத்தில் புதிய 15 test: `tests/test_first_run_and_no_password_acceptance.py`
(fake OS store-உடன் — இந்த sandbox-ல் Windows Credential Locker இல்லை).

**சோதிக்க முடியாதது (நேர்மையாக):** sandbox Linux, Windows இல்லை. எனவே
Credential Locker / DPAPI-இன் உண்மையான சேமிப்பு இங்கே ஓடவில்லை — logic மட்டும்
fake store-ல் ஓடியது. **உங்கள் laptop-ல் `brain passphrase status` என்பதே
உண்மையான சோதனை** (அது YES என்று சொன்னால் சேமிப்பு வேலை செய்கிறது).

---

## 5. நீங்கள் என்ன செய்ய வேண்டும் — படிப்படியாக

### முதல் முறை (இப்போது, 3 நிமிடம்)

1. `NILA_V02_STAGE14.zip`-ஐ இறக்கி `C:\Nila`-க்கு extract செய்யுங்கள்.
2. **`nila-first-run.bat`**-ஐ double-click செய்யுங்கள் (setup.bat அல்ல).
3. அது vault இல்லை என்று பார்த்தால் setup ஓடும். அப்போது:
   - ஒரு **passphrase** தேர்ந்தெடுங்கள் (குறைந்தது 8 எழுத்து) — **தாளில் எழுதி
     வையுங்கள்**;
   - authenticator app-ல் காட்டும் secret-ஐச் சேருங்கள்;
   - recovery codes-ஐத் தாள்/PDF-ஆக வைத்துக்கொள்ளுங்கள் (இவை ஒவ்வொன்றும் ஒரு
     முறை மட்டும் வேலை செய்யும்).
4. அதே சாளரம் கடைசியில் சொல்லும்:
   `remembered on this laptop: YES` → **இனி passphrase கேட்காது.**
5. `start-nila.bat` → "no passphrase needed on this laptop" என்று சொல்லி நிலா திறக்கும்.

### விசைப்பலகை shortcut (2 நிமிடம், ஒரு முறை)

6. **`nila-easy-start.bat`**-ஐ double-click செய்யுங்கள். அது desktop-ல் **Nila**
   icon-ஐ உருவாக்கும்.
7. அந்த icon-மேல் **right-click → Properties → "Shortcut key"** box → கர்சரை
   அங்கே வைத்து **`Ctrl+Alt+N`** அழுத்துங்கள் → **OK**.
8. இப்போது எப்போது `Ctrl+Alt+N` அழுத்தினாலும் நிலா வரும் (கருப்பு சாளரம் இல்லாமல்).

> இந்த ஒரு கை ஏன் உங்களது? Windows-ல் hotkey என்பது `.lnk` கோப்பின் **உள்ளே**
> இருக்கும் binary புலம்; அதை code-ல் எழ எந்த supported வழியும் இல்லை. யாராவது
> "நாங்கள் அழுத்திவிடுகிறோம்" என்றால், அது உங்கள் keyboard-ஐ எப்போதும்
> கேட்டுக்கொண்டிருக்கும் ஒரு background program — அதை இந்த build வேண்டுமென்றே
> செய்யவில்லை. shortcut-ஐ நான் உருவாக்கி, சரியான இடத்தில் வைத்து, சரியான
> command-ஐ இணைத்து, சரியான வழியைக் காட்டுகிறேன்; கடைசி ஒரு விசையை Windows
> கேட்கிறது.

### குரல் (முதல் முறை ஒரு install)

9. `brain hotkey --voice` → என்ன தேவை என்று சொல்லும். பொதுவாக:
   `pip install sounddevice numpy` (mic) மற்றும் `pip install faster-whisper`
   (பேச்சை எழுத்தாக்க). இல்லாமலும் `brain wake --check nila.wav` வேலை செய்யும்.
10. install-க்குப் பிறகு: **`nila-easy-start.bat --voice`** → "நிலா" என்று
    சொல்லுங்கள். 2 நிமிடம் கேட்டு, தானே நிற்கும்.

### restart-க்குப் பிறகு தானே வர வேண்டுமா?

11. விரும்பினால்: `nila-cmd.bat`-ஐத் திறந்து `brain.py hotkey --startup`.
    sign-in-ல் நிலா dashboard தானே வரும். நீக்க: `--no-startup`.
    (Microphone மட்டும் sign-in-ல் ஒருபோதும் திறக்காது.)

### மறந்துவிட்டால் (இப்போது உங்கள் நிலை)

12. `nila-cmd.bat` → `python brain.py lock-check` → "no-password: YES" என்று
    வந்தால், நீங்கள் உள்ளே இருக்கிறீர்கள்:
13. `python brain.py passphrase set` → புதிய passphrase-ஐத் தேர்ந்தெடுங்கள்
    (**பழையது கேட்காது**). அதைத் தாளில் எழுதுங்கள்.
14. சரிபார்ப்பு: `python brain.py passphrase status` → YES;
    `python brain.py passphrase forget` → இப்போது புதிய passphrase-ஐக் கேட்கும்
    (அது வேலை செய்கிறதா என்று பாருங்கள்). முடிந்ததும் `passphrase remember`
    மீண்டும்.

---

## 6. இன்னும் இல்லாதது (மறைக்கவில்லை)

- **Windows-ல் இயக்கிச் சோதிக்கவில்லை** — இந்த sandbox Linux. `keyring`
  install ஆகவில்லை என்றால் DPAPI fallback வேலை செய்யும்; இரண்டும் தோற்றால்
  `brain passphrase status` "no" என்று சொல்லும், நிலா passphrase கேட்கும்
  (அது தோல்வி அல்ல, தெரிந்த நடத்தை).
- **Hotkey-ஐ code-ல் அமைக்க முடியாது** — மேலே சொன்ன காரணம்.
- **குரல்** வேலை செய்ய mic + transcriber வேண்டும் (install ஒரு முறை).
- **Vault-ஐ மீட்க முடியாத நிலை**: passphrase + recovery codes + backup மூன்றும்
  இல்லாமல் போனால், யாரும் திறக்க முடியாது. இது Nila-வின் வரம்பு அல்ல; எந்த
  encrypt ஆன vault-ம் இப்படித்தான்.

---

## 7. Stage 13-ல் இருந்து என்ன மாறியது (ஒரு பார்வை)

| | Stage 13 | Stage 14 |
|---|---|---|
| ஒவ்வொரு start-லும் passphrase | **கேட்கும்** | **கேட்காது** (laptop நினைவில் வைத்தபின்) |
| passphrase மாற்ற | பழையது தேவை (மறந்தால் முடியாது) | **பழையது தேவையில்லை** |
| மறந்த பிறகு பழையது வேலை செய்யுமா | **ஆமா (bug)** | **இல்லை** |
| நிலாவை எழுப்ப | `start-nila.bat` | **`Ctrl+Alt+N`** அல்லது **"நிலா"** அல்லது icon |
| test | 1435 | **1459** |
