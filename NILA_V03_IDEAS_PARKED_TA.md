# v3 — கருத்துகள், **தனியாகப் பாதுகாக்கப்பட்டது** (நீங்கள் சொல்லும்போது மட்டும்)

**நிலை:** இந்தக் கோப்பில் உள்ள எதையும் இப்போது கட்டவில்லை, code-ஐத் தொடவில்லை.
நீங்கள் சொன்னபடி: *"v3-ஐ அடுத்த அப்டேட்டில் பார்த்துக்கலாம், அந்த ஐடியாவைத் தனியா
வச்சுரு, நான் சொல்லும்போது மட்டும் எடுத்துக் கொடு."*

இது ஒரு **சேமிப்பு**. v0.2-ல் 70% முடிந்த நிலையில், v3-ல் என்ன செய்யலாம் என்பதற்கான
வரிசைப்படுத்தப்பட்ட பட்டியல். `brain progress`-ல் `missing` என்று உள்ள 9 வரியும்
இதுதான்.

---

## A. வரிசை 1 — "நிலா உண்மையாகக் கேட்க வேண்டும்" (மிகப்பெரிய இடைவெளி)

1. **faster-whisper** — `pip install faster-whisper`. code ஏற்கனவே உள்ளது
   (`brain ears --file`, `--listen`); install இல்லாததால் `partial`. இதுவே
   `progress next` சொல்லும் முதல் படி.
2. **mic capture** — `sounddevice` + `numpy`; push-to-talk icon ஏற்கனவே காத்திருக்கிறது.
3. **Tamil TTS** — Windows SAPI (உங்கள் PC-ல் இலவசம்) அல்லது Piper + `ta_IN` voice file.
   இரண்டும் `partial`; code உள்ளது, குரல் கோப்பு மட்டும் வேண்டும்.

## B. வரிசை 2 — போனிலிருந்து நிலாவைக் கட்டுப்படுத்தல்

4. **Telegram two-way** — நீங்கள் அனுப்பும் செய்தியை நிலா படித்துப் பதில் சொல்ல.
   இதற்கு ஒரு **நிரந்தரமாக ஓடும் process** வேண்டும் (long-poll worker). இப்போது உள்ள
   `brain telegram` ஒரு கட்டளை-க்கு-ஒரு-செய்தி; அது வேண்டுமென்றே ஒரு திசை.
   செய்ய வேண்டியது: `brain serve` உள்ளேயே ஒரு thread, allowlist = உங்கள் chat id மட்டும்,
   ஒவ்வொரு கட்டளைக்கும் அதே permission gate.
5. **Android app / PWA** — dashboard ஏற்கனவே installable (manifest + icon, stage 10).
   அடுத்தது: service worker (offline shell), passkey/Windows Hello, private tunnel.

## C. வரிசை 3 — "பார்த்து சொல்லுதல்"

6. **Screen viewing** — screenshot → உரை. Windows-ல் `mss`/`PIL`. தனி permission,
   ஒவ்வொரு முறையும் கேட்க வேண்டும் (அது உங்கள் திரை).
7. **OCR** — scanned PDF/படங்கள்: `tesseract` அல்லது `pytesseract`.
8. **ffmpeg scene detect** — வீடியோவை load செய்யாமல் காட்சி பிரிப்பு.

## D. வரிசை 4 — கணக்கு இணைப்புகள் (ஆபத்தானவை, கடைசி)

9. **Gmail / Drive / Calendar write (OAuth)** — read-only `.ics` ஏற்கனவே உள்ளது.
   write என்பது OAuth consent + token vault + ஒவ்வொரு முறையும் ஒப்புதல்.
   v0.2-ல் வேண்டாம் என்று நீங்களே சொன்னீர்கள்.
10. **Multi-user / shared PC** — இப்போது ஒரு உரிமையாளர், ஒரு device binding.

## E. வரிசை 5 — ஆராய்ச்சி

11. **Neural speaker model** (ECAPA / x-vector) — இப்போது MFCC + pitch. சரியான
    "உங்கள் குரல் மட்டும்" என்றால் neural embedding தேவை.
12. **Fine-tuned Tamil model** — Ollama-ல் தமிழ் தரம் அளந்த பிறகுதான் எதுவும் சொல்ல முடியும்.
13. **2-PC mesh** — ஒரே vault, இரண்டு கணினி, end-to-end. `brain sync` உள்ளது (local blob);
    remote backend பாதுகாப்பு tests முடியும் வரை OFF.
14. **Music player**, **always-on wake word** — இரண்டும் வேண்டுமென்றே இல்லை
    (wake word = நாள் முழுவதும் mic திறந்திருக்கும்; `brain launcher --plan` அதை அப்படியே சொல்கிறது).

---

## இந்தக் கோப்பை எப்படிப் பயன்படுத்துவது

- நீங்கள் **"v3 ஆரம்பி"** என்றால்: A வரிசையிலிருந்து (faster-whisper) தொடங்குவேன் —
  ஏனெனில் அதுவே `brain progress next` சொல்வது, மற்றும் அது ஒரு `pip install`.
- நீங்கள் **"Telegram two-way"** என்றால்: B4 — ஆனால் அது ஒரு நிரந்தர process, அதற்கு
  தனி வடிவமைப்பு (எந்தக் கட்டளைகள் அனுமதி, எது இல்லை) வேண்டும்; முதலில் உங்களிடம் கேட்பேன்.
- நீங்கள் **"ZIP"** என்றால்: v0.2-ஐ முடிக்க, அதாவது A1–A3 (install-கள்) மட்டும் தேவை —
  அவற்றை நீங்கள் உங்கள் PC-ல் ஓட்டினால் `brain progress` 70% → 79% ஆகும், அதுவே 100%-க்கு
  மிக அருகில் நான் கொண்டு வர முடிந்த இடம்.
