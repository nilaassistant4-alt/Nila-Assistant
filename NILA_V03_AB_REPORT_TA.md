# v0.2 — ஸ்டேஜ் A + B முடிந்தது (ZIP தயார், push ஆயிற்று)

**நாள்:** 2026-11-04 · **கிளை:** `arena/3f2334a4-nila-assistant`
**ZIP:** `Nila-verification-v0.2-ab-included.zip` (`fc3fcd9b8a13…cf97`)

---

## 1. நீங்கள் கேட்டது: "என்னென்ன இருக்கு, என்னென்ன இருக்கு போக" — நான் ஒப்பிட்டது

| விஷயம் | முன்பு | இப்போது |
|---|---|---|
| #2 instruction memory | இருந்தது | ✅ (உங்கள் 20+18 டெஸ்ட் pass) |
| #3 connectors screen | API மட்டும் | ✅ + enable/disable, 423/404 |
| **A. பிழை பிடித்து issue** | **இல்லை** | ✅ **இப்போது உள்ளது** (`brain issue`) |
| **B. profile + call-me** | **இல்லை** | ✅ **இப்போது உள்ளது** (`brain profile`) |
| **B. strict mode** | **இல்லை** | ✅ **இப்போது உள்ளது** (`brain strict`) |
| Reminders + calculator | இல்லை | ❌ (அடுத்த ஸ்டேஜ்) |
| Google Calendar / Mail / YouTube | இல்லை | ❌ |
| Voice output (பேசுவது) | இல்லை | ❌ — **இது தான் voice-ல் பெரிய இடைவெளி** |
| Voice picker / push-to-talk | இல்லை | ❌ |
| Phone PWA + passkey | இல்லை | ❌ |
| Agent profiles + task router | இல்லை | ❌ |
| Storage tiers 7/90/permanent | இல்லை | ❌ |
| "Hey Nila" (ஒரு device மட்டும்) | இல்லை | ❌ |

**Voice பற்றி நேர்மை:** இப்போது உள்ளது *குரலை அடையாளம் காண்பது* (MFCC, unlock-க்கு) மட்டும்.
**Nila பேசுவது (TTS) இல்லை**, microphone-ஆல் கேட்பதும் (STT) இல்லை. அதனால் voice=பேசு
என்பது இன்னும் முழுதும் கட்டப்படவில்லை. இந்த முறை நீங்கள் கேட்ட இரண்டு விஷயங்களை
மட்டுமே கட்டினேன்.

---

## 2. A — `brain issue "எனக்கு இந்தப் பிரச்சனை"` (உண்மையில் நடந்தது)

```
brain issue "voice unlock fails"
```
1. Nila தன் சோதனைகளை (crypto, voice model, vault, sync) **ஓட்டுகிறது**.
2. உங்கள் வாக்கியம் எந்த வகை என்று முடிவு செய்கிறது (`unlock`, `memory`, `voice_output`,
   `connectors`, `sync`, `update`, `slow`, `privacy`, `other`) — தமிழ் வார்த்தைகளும் அடங்கும்.
3. **என்னென்ன சோதனை ஓடியது, எது FAIL, என்ன செய்யலாம்** என்று ஒரு அறிக்கையைக் காட்டுகிறது.
4. **எதுவும் எழுதப்படாது, எதுவும் அனுப்பப்படாது** — நீங்கள் படித்த பிறகு `--write --yes`
   கொடுத்தால் மட்டும் `<home>/reports/`-ல் ஒரு `.md` file.
5. அதே பிரச்சனையை மீண்டும் சொன்னால் **"ஏற்கனவே சொன்னீர்கள்"** (fingerprint) என்று சொல்லும்.

**உள்ளே போகாதது (உண்மையில் டெஸ்ட் செய்யப்பட்டது):** API keys, passphrases, TOTP codes,
recovery codes, memories/notes/chats, மின்னஞ்சல், தொலைபேசி எண், card/ID எண்,
உங்கள் பெயர் உள்ள file path. **இணையம்: 0 calls — இந்த code-ல் network call-ஏ இல்லை.**

## 3. B — `brain profile` + `brain strict`

**Profile (உங்கள் தகவல் — கட்டளை அல்ல):**
```
brain profile set callme "அண்ணா"
brain profile set language "Tamil"
brain profile show          # 20 field வரை, ஒரு field 200 எழுத்து
brain profile forget        # முழுவதும் அழிக்க
```
ஒவ்வொரு local பதிலிலும் apply ஆகும்; **online-க்கு அனுப்பப்படாது** (டெஸ்ட் செய்தது).
"ignore all rules"-ஐ profile fact ஆகச் சொன்னாலும் refuse ஆகும்.

**Strict mode (நீங்கள் சொன்னது மட்டும் நடக்கும்):**
```
brain strict on
brain strict deny remember     # இதை ஒருபோதும் வேண்டாம்
brain strict allow recall      # இதைக் கேட்காமல் செய்
brain strict auto remember ask # இதை எப்போதும் கேட்டு செய்
```
இது **prompt-ல் அல்ல, code-ல்** தடுக்கப்படுகிறது (tool இயங்குவதற்கு முந்தைய இடத்தில்).
- படிக்கும் tool (search/recall/list) தடையின்றி ஓடும்.
- எழுதும் tool: நீங்கள் **பெயர் சொன்னால்**, allow-list-ல் இருந்தால், அல்லது **"yes"** சொன்னால்
  (ஒரு yes = ஒரு வேலை) மட்டும் ஓடும்.
- மாடல் **network**-ல் இருந்து வந்தால், allow-list இருந்தும் கேட்கும்.
- `wipe`, `auto_approve` போன்ற supervision-ஐ நீக்கும் tool **எப்போதும் மறுக்கப்படும்**.
- Vault locked → **எதுவும் ஓடாது** (fail closed).

## 4. இந்த ஸ்டேஜில் கண்டுபிடித்த **உண்மையான பிழை** (நான் தான் கட்டினேன், நான் தான் பிடித்தேன்)

`brain issue`-ஐ ஓட்டியபோது `doctor` சொன்னது: **"audit: 7 entries edited (chain mismatch)"** —
புதிய vault-ல், நான் எதையும் தொடவில்லை. காரணம் இரண்டு:
1. Vault locked-ஆக இருக்கும்போது எழுதப்படும் audit வரி session key இல்லாமல் கட்டப்படுகிறது;
   பிறகு அதே கோப்பை verify செய்தால் "திருத்தப்பட்டது" என்று பொய் சொல்லும்.
2. doctor **locked vault**-ல் verify செய்தது, session இல்லாமல் → எல்லா வரியும் "mismatch".

சரிசெய்தேன்: `chain_mode` field, "unverifiable while locked" என்ற தனி எண்ணிக்கை, `doctor`-ல்
"unlock to verify the rest" குறிப்பு. **திருத்துதலைப் பிடிக்கும் திறன் மாறவில்லை** (டெஸ்ட்
செய்தது: ஒரு audit வரியில் தொகையை மாற்றினால் இன்றும் `mismatched` என்று சொல்லும்).

## 5. டெஸ்ட் முடிவுகள்

| ஓட்டம் | முடிவு |
|---|---|
| உங்கள் acceptance ஃபைல்கள் (2) | **38 passed** |
| என்னுடைய A+B acceptance (புதியது) | **37 passed** |
| முழு suite (`cryptography` உடன்) | **831 passed, 3 skipped** |
| முழு suite (`cryptography` **இல்லாமல்**) | **831 passed, 3 skipped** |
| ZIP-ஐ பிரித்து மீண்டும் ஓட்டியது | **831 passed, 3 skipped** |
| `nila_mandate_tests.py` | 23-ல் 12 PASS, 1 FAIL, 4 GAP, 6 "இல்லை" |

## 6. ZIP பட்டியல் (145 files)

`Nila-verification-v0.2-ab-included.zip` — sha256 `fc3fcd9b8a13…cf97` (562,075 bytes).
`Nila/` source உள்ளே உண்டு; `__pycache__`, `.git`, vault இல்லை.

## 7. நான் **செய்யாதவை** (நேர்மை)

- `brain issue` **GitHub-ல் issue உருவாக்காது** — file மட்டுமே. நீங்கள் சொன்னபடி "இஷ்யூஸ்
  அப்படியே இருக்கட்டும்"; GitHub-க்கு அனுப்பும் ஆப்ஷன் **இல்லை** (இணையம் இல்லாத sandbox).
- Profile-ல் **health தகவல்** சேர்க்கலாம் ஆனால் அது **online-க்கு போகாது**; Nila மருத்துவ
  ஆலோசனை தராது (இது ஸ்டேஜ் 1-ல் reminder-க்கும் சேர்த்து டெஸ்ட் செய்வேன்).
- Voice picker, TTS, reminders, calendar, PWA — **இல்லை**. அடுத்த ஸ்டேஜ்.
- Python **3.11**-ல் மட்டுமே ஓட்டினேன் (3.12/3.14 இந்த sandbox-ல் இல்லை).

## 8. அடுத்தது (நீங்கள் சொன்னபடி "சின்ன சின்ன upgrade")

**ஸ்டேஜ் 1: Reminders + calculator (PC, offline)** — இதற்கு acceptance:
offline-ல் அடிக்கும் · PC restart-க்குப் பிறகு தவறவிடாது · text online-க்குப் போகாது ·
200 கணக்கு சரி (`eval` இல்லை) · health reminder = reminder மட்டும், மருத்துவ ஆலோசனை இல்லை.

அதன் பிறகு: Calendar → voice (பேசுவது) → PWA/passkey → agent profiles → storage tiers →
mail/YouTube → "Hey Nila" (ஒரு device மட்டும்).
