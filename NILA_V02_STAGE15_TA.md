# Nila v0.2 — Stage 15: "திறக்கவே முடியல" — உண்மையான காரணம், சரிசெய்தது

**நாள்:** 2026-11-04
**நீங்கள் சொன்னது:** "extract பண்ணி, பாஸ்வேர்ட் கேட்குது, திறக்கவே முடியல. ஒரு முறை
மட்டும் கேட்கணும். நிறைய சின்ன சின்ன பிரச்சனை இருக்கு — ஒவ்வொன்னா சரி பண்ணு."
**என்ன செய்தேன்:** code-ஐ மாற்றி, 5 பிழை + 1 பெரிய பிழையைச் சரி செய்து, push செய்தேன்.

---

## 1. மிக முக்கியமான கண்டுபிடிப்பு — recovery code வேலை செய்யவே இல்லை

முன்பு நான் (audit report-ஐ மீண்டும் சொல்லி) சொன்னேன்: *"recovery code இருந்தால்
`start-nila.bat recovery` ஓட்டுங்கள்"*. **அது பொய்.** நான் அதை ஓட்டிப் பார்த்தபோது
தான் தெரிந்தது:

```
$ brain.py status --recovery-stdin      # code-ஐ கொடுத்தேன்
incorrect passphrase                    # → passphrase கேட்டது (code பயன்படவே இல்லை)
```

**ஏன்?** மூன்று தவறுகள் ஒன்றன் மேல் ஒன்று:

1. `open_vault` **passphrase-ஐ முதலில்** கேட்டது; recovery code-ஐ அதற்குப் பிறகுதான்
   பார்த்தது. ஆகவே passphrase மறந்தவர் code-ஐக் கொண்டு உள்ளே போகவே முடியாது.
2. Recovery code **authenticator-க்கு மட்டுமே** மாற்று; vault-ஐத் திறக்கும் சாவி
   (master key) **passphrase-ஆல் மட்டுமே** மூடப்பட்டிருந்தது. code-க்கு அந்தச் சாவி
   கிடையாது.
3. `recovery_remaining()` vault பூட்டியிருக்கும்போது `0` என்று சொன்னது — "code இல்லை"
   என்று தோன்றியது.

அதனால் நான் சொன்னது "folder-ஐ rename செய்து மறுபடி setup செய்யுங்கள்" — அது data-ஐ
இழக்கச் செய்யும் ஆலோசனை. **இப்போது அந்த ஆலோசனை தேவையில்லை.**

### இப்போது எப்படி வேலை செய்கிறது

- **ஒவ்வொரு recovery code-உம் master key-ஐ தனியாக மூடி வைக்கிறது.**
  `Vault.unlock_with_recovery_code()` — passphrase இல்லாமல், authenticator இல்லாமல்,
  code மட்டும் போதும்.
- ஒரு code **ஒரு முறை** மட்டும். பயன்படுத்தியதும் அது அழிக்கப்படும் (வேறு code-கள்
  பாதிக்கப்படாது).
- Passphrase **இன்னும் வேலை செய்யும்** — code ஒரு மாற்று வழி, பூட்டை மாற்றுவது அல்ல.
- Code-ஐ command line-ல் தட்டச்சு செய்ய வேண்டாம்: `start-nila.bat recovery` அதை
  **pipe** மூலம் (stdin) அனுப்பும் — Task Manager-லும், log-லும் தெரியாது.

**இப்போது நீங்கள் செய்ய வேண்டியது (recovery code இருந்தால்):**
1. `start-nila.bat recovery` → ஒரு code தட்டச்சு → vault திறக்கும்.
2. உள்ளே வந்ததும்: `python brain.py passphrase set` → புதிய passphrase (தாளில் எழுதுங்கள்).
3. `python brain.py totp` → authenticator-ஐ மீண்டும் இணைக்கவும்.
4. `python brain.py passphrase remember` → இந்த laptop நினைவில் வைக்கும்.

> ⚠️ **முக்கியம்:** உங்கள் ஏற்கனவே உள்ள vault-ல் உள்ள code-கள் **பழைய build-ல்**
> உருவானவை — அவற்றுக்கு master key wrap **இல்லை**. எனவே அவை இப்போதும் vault-ஐத்
> திறக்காது. அவற்றைப் புதுப்பிக்க ஒரு முறை உள்ளே போக வேண்டும் (passphrase அல்லது
> remembered key மூலம்), பிறகு `python brain.py recovery --count 8` (புதிய code-கள்). இதை
> தெளிவாகச் சொல்கிறேன், ஏனெனில் "code போதும்" என்று முழுமையாகச் சொல்வது தவறாகும்.

---

## 2. மற்ற 5 பிழைகள் — சரி செய்யப்பட்டன (proof உடன்)

| # | பிழை | எப்படிச் சரி செய்தேன் | சோதனை |
|---|---|---|---|
| 1 | Dashboard: battery இல்லாத கணினியில் HTTP 500 (உங்கள் Windows 11 desktop-ல் இதுவே) | `psutil` தோல்வியை முழுமையாகப் பிடித்து, battery-ஐ விட்டுவிட்டு மற்றவற்றைத் தரும் | fake psutil 4 வகை error → 200 |
| 2 | `calculate`-ல் `9**9**9**9` → Nila உறைந்துவிடும் | `eval` நீக்கம்; AST + exponent ≤1000 + 1000 இலக்கம் + 200 ms வரம்பு | 4 தீய input → 0.5 s-க்குள் refuse; `2+3*4=14` அப்படியே |
| 3 | Windows-ல் `%VAR%`, `!VAR!`, `powershell -Command …` தடையில்லை | `%`, `!`, `^` refuse; `cmd/powershell/bash/mshta/certutil` பெயரால் refuse | 7 input refuse, சாதாரண கட்டளை approval-க்குப் போகும் |
| 4 | `.gitignore`-ல் `memory.db`, `workspace/`, vault இல்லை | எல்லாம் சேர்த்தேன் + `build/`, `*.egg-info/` | `git check-ignore` 9 path |
| 5 | README-ல் தவறான ZIP பெயர் | `NILA_V02_STAGE15.zip` / `cd Nila` | test |
| 6 | Test runner "2 failed" என்று பொய் | pytest இருந்தால் அதற்கே ஒப்படைப்பு; fallback-ல் 5 பிழை சரி | pytest இல்லாத கணினியில் **1707 passed / 0 failed** |

கூடுதலாக: **public fetch** (weather/Wikipedia/YouTube/RSS) ஒரு redirect மூலம்
`127.0.0.1:8790`-க்கு திருப்பப்பட்டு உள்ளூர் service-ஐ அழைக்க முடிந்தது — இப்போது
private/loopback முகவரிக்கு redirect refuse.

---

## 3. Audit-ல் இருந்த "critical" 5 — உண்மையான நிலை

அவை **`Nila-v0.1.0.zip`** (2026-10-27, பழைய release) பற்றியவை. இரண்டையும் ஓட்டிப்
பார்த்தேன்:

| # | பிரச்சனை | v0.1.0 ZIP-ல் | இன்று |
|---|---|---|---|
| 1 | Update server: token இல்லாமல் publish | **ஆமா** | **சரி** (token கட்டாயம், `compare_digest`) |
| 2 | Sync server: `../` path traversal | **ஆமா** — வெளியே கோப்பு வெளியேறியது (நான் reproduce செய்தேன்) | **சரி** (`open_within`) |
| 3 | API token redirect-ல் leak | இருந்தது | **சரி** (`open_once` redirect-ஐ refuse) |
| 4 | `pip install .` → `No module named 'brain.cli'` | **ஆமா** | **சரி** + இப்போது **test** (உண்மையில் install செய்து import) |
| 5 | Dashboard 500 | ஆமா | **சரி** (மேலே #1) |

---

## 4. சோதனை முடிவுகள் (இவை மட்டுமே உண்மை)

| சோதனை | முடிவு |
|---|---|
| முழு test suite (pytest) | **1541 passed · 0 failed · 0 skipped** (stage 14: 1459 → +82) |
| pytest **இல்லாத** கணினியில் `scripts/run_tests.py` | **1707 passed · 0 failed · 2 skipped** |
| `scripts/retest_everything.py` | **56 pass · 5 skip · 0 fail** |
| `scripts/nila_mandate_probe.py` | **29 pass · 0 FAIL · 0 gap · 0 error** |

**சோதிக்க முடியாதது (நேர்மையாக):** Windows-ல் இயக்கவில்லை (sandbox Linux) — DPAPI,
Credential Locker, `cmd.exe` நடத்தை, Startup shortcut ஆகியவை உங்கள் laptop-ல் ஒரு
முறை ஓட்டினால் மட்டுமே உறுதி. Voice, mic, Telegram, Google OAuth — இங்கே இல்லை.

---

## 5. நீங்கள் இப்போது என்ன செய்ய வேண்டும் (வரிசையாக)

1. `NILA_V02_STAGE15.zip`-ஐ extract செய்யுங்கள் (பழைய folder-ஐ மாற்றுங்கள்; vault
   `%LOCALAPPDATA%\Nila\vault`-ல் இருப்பதால் அது பாதிக்கப்படாது).
2. `nila-first-run.bat` → vault இருப்பதைப் பார்த்து, setup-ஐத் தவிர்த்து, laptop-ஐ
   நினைவில் வைக்கும்.
3. **Vault திறக்கவில்லை என்றால்** — screen-ல் வரும் செய்தியை அப்படியே copy செய்து
   அனுப்புங்கள். அது passphrase பிரச்சனையா, code பிரச்சனையா, Python பிரச்சனையா என்று
   சொல்ல முடியும். (நான் இப்போது யூகிக்க விரும்பவில்லை.)
4. உள்ளே போனதும்: `python brain.py lock-check` → "no-password: YES" என்று வர வேண்டும்.
5. நினைவில் இல்லாத passphrase-க்கு: `python brain.py passphrase set` (பழையது தேவையில்லை).
6. புதிய recovery code-கள்: `python brain.py recovery --count 8` (பழையவை உடனே செல்லாது).
   இதுவே பழைய code-களை vault-ஐத் திறக்கும் code-களாக மாற்றும் வழி.

**இன்னும் இல்லாதது:** phone-ஐ நேரடியாக அணுகும் வசதி (TLS + tunnel + login தேவை —
தனி வேலை), dashboard-ல் microphone (வேண்டுமென்றே மூடப்பட்டது), சொந்த TTS குரல்.
