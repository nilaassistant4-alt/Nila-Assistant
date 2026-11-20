# Nila — படி 19: புதிய vault, ஒரு click reset, மற்றும் "Windows PIN" உண்மை

**குறுகிய பதில் (ஆம்/இல்லை):**

1. **புதிய vault — ஆம், முடிந்தது.** `reset-vault.bat` ஒரு double-click. ஆனால் நீங்கள்
   சொன்ன "folder-ஐ `vault_old` என்று rename செய்" — அதை நான் **செய்யவில்லை**, ஏனெனில்
   rename செய்தால் **ஒரே ஒரு copy** மட்டுமே இருக்கும். அது தான் ஒரே ஆபத்து. Nila
   **copy** செய்கிறது, ஒவ்வொரு file-ஐயும் ஒப்பிட்டு பார்த்து, பிறகு தான் புதிய vault.
2. **Windows Hello / PIN prompt Nila-வில் இருந்து — இல்லை, build ஆகவில்லை.** இதை நான்
   பொய்யாக "இருக்கு" என்று சொல்ல மாட்டேன். காரணம் கீழே.
3. **வாரம் ஒரு முறை passphrase கேட்பது — இல்லை.** இது build ஆகவில்லை; வேண்டுமானால்
   `brain passphrase forget` கொடுத்து prompt-ஐ திரும்ப வரவைக்கலாம்.
4. **உங்கள் கேள்வி: "என் laptop lock screen PIN கேட்குமா, password கேட்குமா, ஒன்றும்
   கேட்காதா?"** — இதை என்னால் இங்கிருந்து பார்க்க முடியாது (நான் Linux-இல்). கீழே
   **30 வினாடியில் நீங்களே பார்க்கும் வழி** இருக்கிறது. ஒன்றும் கேட்கவில்லை என்றால் →
   Windows PIN வைக்கவும், அப்போது "A" வழி (PIN + நினைவில் வைத்த key) வேலை செய்யும்.

---

## 1. நீங்கள் எழுதியதில் எது சரி, எது சரி இல்லை

நீங்கள் repo-வை படித்து முடிவெடுத்தது பெரும்பாலும் **சரி**. இரண்டு இடத்தில் திருத்தம்:

| நீங்கள் சொன்னது | உண்மை |
|---|---|
| passphrase தான் master key-ஐ மூடுகிறது | **சரி.** `identity.json`-ல் passphrase-ஆல் மூடப்பட்ட master key. passphrase இல்லாமல் அதைத் திறக்க முடியாது. |
| bypass / backdoor இல்லை | **சரி.** எந்த இரகசியக் கதவும் இல்லை. நானும் அதைத் திறக்க முடியாது. |
| recovery code-ஐ passphrase-உடன் சேர்த்து தான் கொடுக்க வேண்டும் | **இது தவறு.** `start-nila.bat recovery` → ஒரு code மட்டும் போதும், passphrase இல்லாமல், phone இல்லாமல் vault திறக்கும். 8 codes, ஒவ்வொன்றும் **ஒரு முறை** மட்டும். (இதை stage 15-ல் சோதித்து உறுதி செய்தோம்.) |
| அதனால் வழி = புதிய vault | **சரி** — code இல்லை என்றால் மட்டும். code இருந்தால் அது முதலில். |

அதாவது: **உங்கள் diagnosis சரி** — passphrase தெரியவில்லை, code-ம் இல்லை என்றால்,
புதிய vault தான் வழி. ஆனால் "code + passphrase இரண்டும் வேண்டும்" என்ற இடத்தில்
உங்களுக்கு ஒரு வாய்ப்பு இன்னும் இருக்கிறது.

---

## 2. படி 1 — புதிய vault: என்ன கிடைத்தது

**`reset-vault.bat`** (Nila folder-ல், புதிய file):

1. முதலில் vault folder எது என்று **print** செய்கிறது
   (வழக்கமாக `%LOCALAPPDATA%\Nila\vault`).
2. `START` என்று **நீங்கள் type செய்தால் மட்டுமே** முன்னே போகும். வேறு எதை அழுத்தினாலும்
   "எதுவும் மாறவில்லை" என்று சொல்லி நிற்கும்.
3. பழைய vault-ஐ `vault-old-<தேதி>` என்ற folder-ஆக **copy** செய்கிறது, ஒவ்வொரு file-ஐயும்
   அளவு + SHA-256 கொண்டு ஒப்பிடுகிறது. copy சரி இல்லை என்றால் **நிற்கிறது.**
4. **rename இல்லை, delete இல்லை.** பழைய folder அப்படியே இருக்கும், பழைய passphrase-ஆல்
   இன்னும் திறக்கும்.
5. புதிய passphrase (குறைந்தது 6 எழுத்து), புதிய recovery codes 8, புதிய authenticator
   secret — **ஒரே ஒரு முறை** காட்டும். இந்த laptop-ஐ நினைவில் வைக்கும்.

### அதே வேலையை நான் இங்கே சோதித்தது (Linux-இல், உண்மையாக ஓட்டி)

| சோதனை | முடிவு |
|---|---|
| பழைய vault-ல் 3 memory, புதிய vault உருவாக்கியது | copy `vault-old-20261120-161746` கிடைத்தது, **"memories in it: 3"** என்று சொன்னது |
| புதிய vault + பழைய passphrase | **refuse** (`PermissionError`) — பழையது புதியதைத் திறக்காது |
| புதிய vault + புதிய passphrase | **திறக்கிறது** |
| பழைய copy + பழைய passphrase | **திறக்கிறது** — memory-கள் அப்படியே இருக்கின்றன |
| copy தோல்வி (disk full போல) | எதுவும் மாறவில்லை, vault அப்படியே |

முழு test suite: **1598 pass, 0 fail, 3 skip**. ZIP-ஐ பிரித்து அதற்குள்ளேயும் ஓட்டினேன்:
**1589 pass, 0 fail**.

### நீங்கள் செய்ய வேண்டியது, வரிசையாக

1. Nila-வின் எல்லா window-ஐயும் மூடுங்கள்.
2. `Nila` folder-ல் **`reset-vault.bat`**-ஐ double-click செய்யுங்கள்.
3. திரையில் வரும் vault folder சரியா என்று பாருங்கள் (உங்க memory-கள் `C:\`-ல்
   இல்லை, `%LOCALAPPDATA%\Nila\vault`-ல்).
4. `Type START to begin` என்று வரும்போது **START** என்று type செய்து Enter.
5. புதிய passphrase-ஐ **காகிதத்தில் எழுதி**, பிறகு type செய்யுங்கள் (இரண்டு முறை கேட்கும்;
   type செய்யும்போது திரையில் எதுவும் தெரியாது — அது சரி, `XX` மட்டும் வரும்).
6. வரும் **8 recovery codes**, **authenticator secret**, **current code** — மூன்றையும்
   காகிதத்தில் எழுதுங்கள். ஒரே முறை தான் காட்டும்.
7. Authenticator app-ல்: *Add account → Enter a setup key → செயலியின் பெயர் `Nila` → secret-ஐ paste*.
8. `nila-start.bat`-ஐ double-click செய்யுங்கள். **எதுவும் கேட்காது.**

> passphrase எங்கே கேட்கும்: `reset-vault.bat`-ல், `START` type செய்த பிறகு, "new passphrase:" என்று.
> recovery code எங்கே கேட்கும்: `nila-start.bat` திறக்க முடியாதபோது வரும் `:stuck` திரையில்,
> அல்லது `start-nila.bat recovery`-ல்.

---

## 3. படி 2 — "ஒரு click, passphrase இல்லை": எது இருக்கு, எது இல்லை

| நீங்கள் கேட்டது | இன்று உண்மையா | உண்மையில் என்ன நடக்கிறது |
|---|---|---|
| இந்த laptop-ல் passphrase கேட்கக்கூடாது | **ஆம், ஏற்கனவே இருக்கு** | key Windows-இன் சொந்த பாதுகாப்பான store-ல் (Credential Locker, அல்லது DPAPI). **ஒரு laptop-க்கு ஒரு முறை** type — வாரம் ஒரு முறை அல்ல. |
| வாரம் ஒரு முறை மறுபடி கேட்க வேண்டும் | **இல்லை** | 7-நாள் timer build ஆகவில்லை. இப்போதே prompt வேண்டுமானால்: `brain passphrase forget` |
| Nila-வில் இருந்து Windows PIN / Hello prompt | **இல்லை — build ஆகவில்லை** | அதற்கு compile செய்யப்பட்ட சிறிய Windows helper (ctypes + WinRT `UserConsentVerifier`) + உண்மையான Windows சோதனை வேண்டும். `python brain.py winmode` இதைத் தெளிவாகச் சொல்லும்: *"Windows Hello prompt: not in this build"* |
| Desktop shortcut | **ஆம்** | `nila-start.bat` முதல் முறை ஓடும்போது Desktop-ல் `Nila` icon எழுதும் |
| Windows login-ல் தானாகத் திறக்க | **ஆம்** | உங்கள் **சொந்த** Startup folder-ல் ஒரு `.vbs` (service இல்லை, registry இல்லை) — `Ctrl+Alt+N` shortcut key-ஐ Windows-இலேயே icon-ன் Properties-ல் வைக்கலாம் |

இன்று Nila-ஐத் திறக்க **மூன்று வழி**: Desktop icon, `Ctrl+Alt+N`, `nila-start.bat`.
மூன்றும் இந்த laptop-ல் எதுவும் கேட்காது.

`python brain.py winmode` (அல்லது `nila-cmd.bat`-ல்) இதையே உங்கள் PC-யில் காட்டும் —
vault எங்கே, key நினைவில் இருக்கிறதா, எந்த store-ல், என்ன இல்லை.

---

## 4. உங்கள் கேள்விக்கான பதில்: lock screen PIN கேட்குமா?

இதை நான் இங்கிருந்து பார்க்க முடியாது. **30 வினாடியில் நீங்களே பாருங்கள்:**

1. கணினியை **lock** செய்யுங்கள்: `Windows` key + `L`.
2. திரையை அழுத்துங்கள் / எழுந்திருங்கள்.
3. என்ன வருகிறது என்று பாருங்கள்:
   - **"PIN" என்று எண் கட்டம்** → நல்லது. A வழி (PIN + நினைவில் வைத்த key) **வேலை செய்யும்**.
   - **"Password" என்று காலி கட்டம்** → Windows password வைத்திருக்கிறீர்கள். அதுவும் போதும்.
   - **உடனே desktop வந்துவிட்டது** → **ஒன்றும் கேட்கவில்லை**. இப்போது யார் laptop-ஐ
     எடுத்தாலும் Nila-ஐ எதுவும் கேட்காமல் திறக்க முடியும். இது Nila-வின் பிரச்சினை இல்லை,
     Windows account-ன் பிரச்சினை.
4. **ஒன்றும் கேட்கவில்லை என்றால்**, வரிசையாக:
   1. `Windows` key → **Settings** → **Accounts** → **Sign-in options** → **PIN (Windows Hello)**
      → **Add** → உங்களுக்குத் தெரிந்த 4–6 இலக்கம் → OK.
   2. `Windows` + `L` அழுத்தி, PIN கேட்கிறதா என்று பாருங்கள்.
   3. பிறகு Nila: Desktop icon அல்லது `Ctrl+Alt+N` — எதுவும் கேட்காது, PIN தான் கேட்கும்.

**எச்சரிக்கை (நேர்மையாக):** Nila-வின் key இந்த laptop-ல் நினைவில் இருக்கிறது. அதாவது
**lock ஆகாத laptop = யாருக்கும் திறந்த Nila.** Windows PIN தான் அதை மூடுகிறது —
Nila-வால் அதைச் செய்ய முடியாது.

---

## 5. நீங்கள் கொடுத்த code plan — எது முடிந்தது

| உங்கள் plan | நிலை |
|---|---|
| `reset-vault.bat` | **முடிந்தது** (copy + verify, rename/delete இல்லை) |
| Desktop shortcut | **ஏற்கனவே இருந்தது** (stage 17), இப்போது ஒரே முறை எழுதும் |
| login-ல் தானாக start | **முடிந்தது** (Startup folder `.vbs`) |
| `brain/winauth.py` (DPAPI + Hello verify) | **இல்லை.** DPAPI/Credential Locker ஏற்கனவே `brain/security/vaultkey.py`-ல் இருக்கு; Hello verify செய்ய compile ஆன helper + Windows சோதனை வேண்டும் — இங்கு Linux, சோதிக்க முடியாது. பொய் சொல்ல விரும்பவில்லை. |
| setup-ல் random passphrase, store-ல் மட்டும் | **இல்லை.** இப்போது நீங்கள் type செய்யும் passphrase + laptop-ல் நினைவில். (random passphrase = காகிதம் இல்லாமல் எங்கும் இல்லை; laptop போனால் vault போச்சு.) |
| TOTP-ஐ optional ஆக்குவது (`require_second_factor`) | **இல்லை** — இப்போது authenticator code vault திறக்கத் தேவைப்படுகிறது (நினைவில் உள்ள key இருந்தால் மட்டும் தவிர்க்கப்படும்) |
| 7-நாள் timer | **இல்லை** (மேலே சொன்னது) |

அடுத்து என்ன செய்ய வேண்டும் என்று சொல்லுங்கள் — **A** (PIN மட்டும், இன்றே வேலை செய்யும்),
**B** (Hello/WinRT helper — Windows-ல் சோதித்து தான் சொல்ல முடியும்), அல்லது
**C** (TOTP optional + 7-நாள் timer). நான் இப்போது **படி 1-ஐ முடித்து** நிற்கிறேன், நீங்கள்
சொன்னபடி.

---

## 6. உங்கள் PC-யில் நான் சோதிக்க முடியாதது (நேர்மையாக)

DPAPI / Credential Locker, உண்மையான `cmd.exe`, icon-ன் Shortcut key, Windows Hello,
voice / mic / Telegram. இவற்றை Windows-ல் தான் சோதிக்க முடியும்.

`report.bat`-ஐ double-click செய்து அதன் text-ஐ அனுப்புங்கள் — `reset-vault.bat`
ஓடியதா, `winmode` என்ன சொல்கிறது என்று அதில் தெரியும். அதைப் பார்த்த பிறகே
"Windows-ல் வேலை செய்கிறது" என்று நான் சொல்வேன்.
