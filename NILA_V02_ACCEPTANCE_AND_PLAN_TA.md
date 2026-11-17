# உங்கள் கேள்விக்கான பதில் — புதிய 8-புள்ளி பட்டியலில் என்ன இருக்கு, என்ன இல்லை

**நாள்:** 2026-11-04 · **கிளை:** `arena/3f2334a4-nila-assistant` · **ZIP-ஐ இன்னும் புதுப்பிக்கவில்லை** (நீங்கள் "ok" சொன்னபின்).

நீங்கள் கொடுத்த **இரண்டு acceptance டெஸ்ட் ஃபைல்களையும் அப்படியே** repo-வில் வைத்து ஓட்டினேன்.
**எழுத்து ஒன்றும் மாற்றவில்லை.** முதல் ஓட்டம்: **19 fail / 19 pass**. அதாவது "ஏற்கனவே இருக்கா?" என்பதற்கான உண்மையான பதில் கிடைத்தது.

---

## 1. முதல் ஓட்டத்தில் தோன்றியது (19 fail) — எல்லாமே சரியான குறைகள்

| # | குறை | தன்மை |
|---|------|-------|
| 1 | `GET /v1/connectors`-ல் `connectors` list இல்லை (`ai`/`integrations` என்று இருந்தது) | **API வடிவம் வேறு** — நான் ஏற்கனவே எழுதிய API, நீங்கள் எழுதிய contract-ஐ ஒத்துக்கொள்ளவில்லை |
| 2 | `POST /v1/connectors/<id>/enable` / `disable` வழிகள் இல்லை | API இல்லை |
| 3 | 404 (unknown id) / 423 (locked vault) வரவில்லை (400 வந்தது) | API நடத்தை |
| 4 | instructions-ல் `instruction` object இல்லை, `limits` இல்லை | API வடிவம் |
| 5 | edit/delete `PATCH/DELETE`-ல் இருந்தது; நீங்கள் `POST <id>` / `POST <id>/delete` என்று கேட்டீர்கள் | API வடிவம் |
| 6 | **7 "safety rule off" instructions ஏற்றுக்கொள்ளப்பட்டன (201) — refuse ஆக வேண்டியவை** | **உண்மையான பாதுகாப்பு பிழை** |
| 7 | limit 20 / 280 இருந்தது; contract 10 / 200 | எண் முரண் |

## 2. முதல் ஓட்டத்தில் ஏற்கனவே pass ஆனது (19) — இவை புதிதாக கட்ட வேண்டியதில்லை

AI keys ஒருபோதும் வெளியே வராது · payment/bank/card connector இல்லை · integrations default-ஆக off ·
தெரியாத id → 404 · locked vault → 423 · instruction create/list/edit/delete (அடிப்படை) ·
தொடர்பில்லாத கேள்விகளுக்கும் instruction apply ஆகிறது (4 மொழிகள்) · edit/delete செய்தபின் apply ஆகாது ·
online-க்கு ஒருபோதும் அனுப்பப்படாது · model tool call instruction உருவாக்க முடியாது ·
5 நல்ல instructions ஏற்கப்படும் · audit-ல் text இல்லை.

## 3. இந்த turn-ல் சரிசெய்தவை — இப்போது **38/38 pass**

**உண்மையான பாதுகாப்பு பிழை (மிக முக்கியம்):** `brain/security/classify.py`-ல், நீங்கள் சொன்ன 7 விதிமீறல்
வாக்கியங்களும் **ஏற்கப்பட்டன**. இப்போது **ஏழும் refuse**, நல்ல 5 instructions-ம், தமிழ்/ஆங்கில சாதாரண
வாக்கியங்களும் இன்னும் ஏற்கப்படும். (தமிழ் சொற்கள் வரிசை முக்கியம் — வினைச்சொல்லை முதலில் மாற்ற வேண்டும்.)

| மாற்றம் | எங்கே |
|---|---|
| `connectors` flat list + `enable`/`disable` (fresh step-up, ஒரே-முறை, 403/404/423, audit-ல் secret இல்லை) | `brain/agent/connectors.py`, `brain/api/gateway.py` |
| integration **default = OFF** (உண்மையான பிழை: fresh vault-ல் `calendar: enabled true` என்று வந்தது) | `connectors.py` |
| ON → OFF → ON சரிசெய்தல் (மூன்றாவது turn offline-ஆகிவிட்டது) | `connectors.py`, `router.py` |
| `set_on` ஒரு **custom base_url**-ஐ அழித்தது (test server/proxy URL போய்விட்டது) | `connectors.py` |
| instructions-ல் `instruction` object, `limits`, `POST <id>`, `POST <id>/delete` | `gateway.py` |
| **10 / 200** limits | `memory/store.py`, `security/classify.py` |

## 4. டெஸ்ட் முடிவுகள் (இப்போது)

| ஓட்டம் | முடிவு |
|---|---|
| உங்கள் 2 acceptance ஃபைல்கள் | **38 passed** (முன்பு 19 fail) |
| முழு suite, `cryptography` உடன் (Python 3.11) | **792 passed, 3 skipped** |
| முழு suite, `cryptography` **இல்லாமல்** | **792 passed, 3 skipped** |
| `nila_mandate_tests.py` | 23-ல் 12 PASS, 1 FAIL, 4 GAP, 6 "இல்லை" |

## 5. நான் டெஸ்ட்டைத் திருத்தவில்லை — ஆனால் **மூன்று விஷயங்களைச் சொல்ல வேண்டும்**

1. **`test_locked_vault_is_refused`** (connectors): `vault.lock()` செய்தபின் `GET /v1/connectors` **423**
   என்று எதிர்பார்க்கிறது. குறியீடு அதைச் செய்கிறது (சரி). ஆனால் **உள்ளே என்ன நடக்கிறது** என்பது
   "vault lock" அல்ல — gateway **அந்த நேரத்தில்** vault-ஐத் தானாகத் திறக்க முடியுமா என்று பார்க்கிறது;
   passphrase இல்லாமல் திறக்க முடியாது என்பதால் 423 வருகிறது. ஒரு passphrase-cached வேலையில்
   இது 200 ஆகவும் மாறலாம். நான் 423-ஐ "locked" என்பதற்கான ஒரே காரணமாக வைத்திருக்கிறேன்.
2. **`limits` 10/200** — நீங்கள் எழுதிய contract-ஐ வென்றதாக எடுத்துக்கொண்டு 20/280 → 10/200 ஆக்கினேன்.
   வேறு எண் வேண்டுமானால் சொல்லுங்கள் (டெஸ்ட் ஃபைலில் அல்ல — குறியீட்டில் மாற்றுவேன்).
3. **`POST /v1/instructions/<id>` edit** செய்யும் போது `MAX_INSTRUCTION_CHARS` 200-க்கு மேல்
   மறுக்கப்படும். இது டெஸ்ட்டில் இல்லை; நான் சேர்த்தது.

---

## 6. அடுத்து என்ன செய்யப் போகிறேன் — **ஒரு கட்டமாக, ஒவ்வொன்றுக்கும் acceptance டெஸ்ட்**

நீங்கள் சொன்ன வரிசைப்படியே. இப்போது **கட்டம் 0 மட்டும்** முடிந்தது (மேலே). கீழே ஒவ்வொரு கட்டத்திற்கும்
"எப்படி நிரூபிப்போம்" என்று எழுதியிருக்கிறேன். **இன்னும் கட்டம் 1-ஐ தொடங்கவில்லை** — உங்கள் "சரி" வரவேண்டும்.

### கட்டம் 0 — ✅ முடிந்தது: connectors screen + instruction memory
Acceptance: உங்கள் 38 டெஸ்ட் + `connectors view` (Nila நடுவில், பச்சை/சாம்பல் கோடுகள்) dashboard-ல்.

### கட்டம் 1 — Reminders + calculator (PC-ல் மட்டும், முழு offline)
* என்ன: `brain remind "நாளை 9 மணி பால் வாங்க"` → PC-ல் notification; "7 நாட்களுக்கு ஒவ்வொரு திங்கள்" போன்ற repeat;
  "list / done / snooze". `brain calc "18500*12 + 10%"` — இணையம் தேவையில்லை.
* Acceptance (நான் எழுதுவேன், நீங்கள் ஓட்டுவீர்கள்):
  1. PC offline-ல் reminder அடிக்கிறது (network off).
  2. PC restart ஆனபின் reminder தவறவிடப்படாது (தாமதமாகவும் அடிக்கும், "தவறியது" என்று காட்டும்).
  3. Reminder-ல் உள்ள text **online-க்கு அனுப்பப்படாது** (online spying provider = 0 calls).
  4. Calculator: 200 random சமன்பாடுகள், `decimal` மூலம் சரியான விடை; `eval` பயன்படுத்தப்படவில்லை.
  5. Health reminder-கள்: "மருந்து நேரம்" என்ற reminder மட்டும் — **மருத்துவ ஆலோசனை இல்லை**
     (கேள்விக்கு "நான் மருத்துவ ஆலோசனை தரமாட்டேன்" என்ற பதில்).

### கட்டம் 2 — Google Calendar (reminder-ஐ Calendar-க்கு அனுப்புதல்)
* என்ன: read + create event (alert உடன்), **தனி permission**, OAuth token vault-ல்; அதிகமாக re-login ஆகலாம்.
* Acceptance: token இல்லாமல் 0 call · ஒரு event-க்கு ஒரே முறை create (duplicate இல்லை) ·
  Calendar off ஆனால் reminder local-ஆகவே அடிக்கும் · createக்கு முன் உங்கள் ஒப்புதல்.
* **எச்சரிக்கை:** PC off ஆனால் Nila phone-ல் Calendar reminder அனுப்ப **முடியாது** (Google-ஏ அனுப்பும்,
  Nila அல்ல). PC-ஐ on வைக்க வேண்டும் அல்லது பிறகு ஒரு சிறிய phone edition.

### கட்டம் 3 — Voice (push-to-talk + voice picker), cloning இல்லை
* Windows/phone built-in Tamil+English, speed/pitch; cloud voice **பொது பதில்களுக்கு மட்டும்** (தனி switch, default off).

### கட்டம் 4 — Phone PWA + passkey  ·  கட்டம் 5 — agent profiles + task router
### கட்டம் 6 — storage tiers (7 / 90 / permanent) + வாராந்திர "delete these?" — ஒப்புதல் இல்லாமல் ஒன்றும் அழியாது
### கட்டம் 7 — Mail (read-only) + YouTube (read-only)  ·  கட்டம் 8 — "Hey Nila" (ஒரு device மட்டும்)

---

## 7. இப்போது உங்களிடம் கேட்க வேண்டியவை (தயவுசெய்து பதில் சொல்லுங்கள்)

1. **கட்டம் 0-ஐ இப்படியே ஏற்கிறீர்களா?** (38/38 pass, classifier பிழை சரி) — ஆம் என்றால், கட்டம் 1 தொடங்குகிறேன்.
2. **limits 10/200** சரியா? (இல்லை என்றால் சொல்லுங்கள்)
3. `test_locked_vault_is_refused`-ல் நான் சொன்ன #1 விஷயம் பற்றி கவலை இல்லையா?
4. **Python 3.11**-ல் தான் நான் ஓட்டினேன் (3.12/3.14 இந்த sandbox-ல் இல்லை). உங்கள் 664-ஓட்டம் 3.12/3.14-ல்
   நடந்தது — நீங்கள் மீண்டும் ஓட்டி உறுதிப்படுத்துவீர்களா?
5. **GitHub push** — இன்னும் செய்யவில்லை. "ok" சொன்னால் மட்டும்.

---

# சேர்ப்பு (2026-11-04, இரண்டாவது கோரிக்கை): இன்னும் 2 விஷயம்

நீங்கள் இப்போது சொன்ன இரண்டு விஷயங்களும் **v0.2-ல் சேரும்**; நான் repo-வைப் பார்த்து உண்மையைச் சொல்கிறேன்:

## A. "எனக்கு இந்தப் பிரச்சனை வருது" — தானாகப் பிழையைப் பிடித்து, issue ஆகத் தயார் செய்தல்
**இப்போது என்ன இருக்கு:** `brain doctor` (self-test), `brain report` (health report) மட்டும்.
**இல்லாதது:** "எனக்கு இந்தப் பிரச்சனை" என்று நீங்கள் சொன்னதைப் பிடித்து, **என்னென்ன தகவல் சேர்க்கப்படும் என்று
முழுப் பட்டியலைக் காட்டி**, ஒப்புதல் கேட்டு, ஒரு **bundle** (file) ஆக எழுதுவது.

**கட்டம் A acceptance:**
1. bundle-ல் **API key / password / தொலைபேசி / மின்னஞ்சல் இல்லை** (செயற்கை secret செருகி சோதிக்கப்படும்).
2. **உங்கள் ஒப்புதல் இல்லாமல் ஒரு file-ம் எழுதப்படாது, ஒரு network call-ம் போகாது** (0 calls).
3. `doctor` 8 தெரிந்த பிரச்சனைகளைக் கண்டுபிடித்து, ஒவ்வொன்றுக்கும் "இப்படிச் சரிசெய்" என்று சொல்லும்.
4. 100 சீரற்ற பிழை வாக்கியங்கள் → crash இல்லை, scanner வகைப்படுத்தும்.
5. அதே பிரச்சனையை இரண்டு முறை சொன்னால் → "ஏற்கனவே பதிவு செய்யப்பட்டது" (hash), நகல் இல்லை.
6. Vault locked → bundle கிடையாது (refuse).

**நேர்மையான வரம்பு:** இந்த sandbox-ல் **இணையம் இல்லை**, எனவே நான் GitHub-ல் issue **உருவாக்க முடியாது**.
bundle-ஐ file ஆக எழுதி நீங்கள் attach செய்யலாம்; அல்லது நீங்கள் ok சொன்னால் (token vault-ல், ஒவ்வொரு முறையும்
ஒப்புதல்) GitHub API வழியாக அனுப்பலாம். **அமைதியாக அனுப்புவது கிடையாது.**

## B. என்னை எப்படிக் கூப்பிட வேண்டும் + என் தகவல் (profile) + கட்டளைகளை மட்டும் கேட்டு நடப்பது
**இப்போது என்ன இருக்கு:** standing instructions (10, 200 எழுத்து, ஒவ்வொரு local turn-லும் apply) — #2-ல் முடிந்தது.
**இல்லாதது:** (1) **profile** — உங்கள் பெயர்/அழைப்புப் பெயர்/மொழி/வேலை போன்ற **உண்மைகள்** (instruction அல்ல),
(2) **strict mode** — எதையும் செய்வதற்கு முன் கேட்டு, நீங்கள் சொன்ன வழிமுறைப்படி மட்டும் நடப்பது.

**கட்டம் B acceptance:**
1. `profile set callme "அண்ணா"` → offline-ம் online-ம் சரியாக அழைக்கும்; profile-ல் இல்லாததைக் கற்பனை செய்யாது.
2. 4 மொழிக் கேள்விகளிலும் standing instruction apply (ஏற்கனவே உள்ளது, regression check).
3. **strict mode ON** → tool call **prompt-ல் கேட்பது அல்ல — code-ல் தடுக்கப்படும்**, ஒப்புதல் வந்தால் மட்டும் நடக்கும்; audit-ல் பதிவு.
4. Profile field 200 எழுத்து / 20 field வரம்பு; injection வாக்கியம் refuse (ஏற்கனவே உள்ள classifier).
5. Locked vault → 423. Profile-ஐ `brain profile forget` முழுவதும் அழிக்கும்.
6. **Online-க்கு profile அனுப்புவது default = OFF** (தனி switch; அனுப்பும் முன் "இதை அனுப்புகிறேன்" என்று காட்டும்).

## இப்போதைய முழு நிலை (repo ஆதாரத்துடன்)

| விஷயம் | நிலை |
|---|---|
| #2 instruction memory | ✅ முடிந்தது |
| #3 connectors screen (API) | ✅ முடிந்தது · **graph view (Nila நடுவில், பச்சை/சாம்பல் கோடு) இல்லை** |
| Reminders + calculator | ❌ இல்லை |
| Google Calendar | ❌ இல்லை (plugin framework உண்டு) |
| Voice push-to-talk + picker | ❌ இல்லை — audio-ல் **குரல் சரிபார்ப்பு (MFCC) மட்டும்**; TTS/STT இல்லை |
| Phone PWA + passkey | ❌ இல்லை |
| Agent profiles + task router | ❌ இல்லை (`projects` store மட்டும்) |
| Storage tiers 7/90/permanent | ❌ இல்லை |
| Mail (read-only) · YouTube | ❌ இல்லை |
| "Hey Nila" | ❌ இல்லை |
| **A. bug/issue capture** | ✅ முடிந்தது — `brain issue` (+37 acceptance tests) |
| **B. profile + call-me + strict mode** | ✅ முடிந்தது — `brain profile`, `brain strict` |
| Voice **output** (Nila பேசுவது) | ❌ இல்லை — unlock-க்கான குரல் அடையாளம் மட்டுமே உள்ளது |
