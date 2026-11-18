"""Nila speaks Tamil and English. She can *read* about ten languages.

The rule the owner set, in one sentence: **Nila talks to me in Tamil and
English, but she must understand what other people say to her and explain it to
me in Tamil.**

That splits into two very different jobs, and treating them as one is what makes
"just let the model do it" fail:

* **Speaking** is fixed: Tamil and English only. Every answer that leaves this
  module is in one of the two, whatever came in.
* **Reading** is broad: Malayalam, Hindi, Telugu, Kannada, Bengali, Marathi,
  Gujarati, Punjabi, Urdu and Sanskrit are recognised by script and by their
  common words. Recognition is local, deterministic and instant.

The translation itself needs a brain. Nothing in this repository can translate
Malayalam into Tamil by itself, so:

* with **Gemini** configured, one prompt does it - and it is routed through the
  existing online gate, which means the owner has to choose Online for that turn,
  and the text goes out only then;
* with **nothing** configured, the honest answer is what comes back: the language
  is named, the text is echoed for confirmation, a word-by-word gloss of the
  most common words is offered when those words are known, and the owner is told
  that a real translation needs a model. No invented meaning, ever.

A false claim of understanding is worse than "I can see this is Malayalam and I
do not have a model that translates it" - so the second is what is said.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

#: The only two languages Nila may answer in. Everything else is input.
SPOKEN = ("tamil", "english")
SPOKEN_TAG = {"tamil": "ta", "english": "en"}

#: Script ranges. A script is the strongest signal and needs no statistics.
SCRIPTS: Dict[str, Tuple[int, int]] = {
    "tamil": (0x0B80, 0x0BFF),
    "malayalam": (0x0D00, 0x0D7F),
    "telugu": (0x0C00, 0x0C7F),
    "kannada": (0x0C80, 0x0CFF),
    "bengali": (0x0980, 0x09FF),
    "gujarati": (0x0A80, 0x0AFF),
    "gurmukhi": (0x0A00, 0x0A7F),     # Punjabi
    "oriya": (0x0B00, 0x0B7F),
    "devanagari": (0x0900, 0x097F),   # Hindi, Marathi, Sanskrit, Nepali
    # Urdu is Arabic script; the checker treats it with Devanagari's neighbours
    "arabic": (0x0600, 0x06FF),
}

#: The names the owner will hear, in the order the script could belong to.
SCRIPT_LANG = {
    "tamil": "Tamil",
    "malayalam": "Malayalam",
    "telugu": "Telugu",
    "kannada": "Kannada",
    "bengali": "Bengali",
    "gujarati": "Gujarati",
    "gurmukhi": "Punjabi",
    "oriya": "Odia",
    "devanagari": "Hindi/Marathi/Sanskrit",
    "arabic": "Urdu/Arabic",
}

#: Which language a Devanagari sentence most likely is, from function words.
_DEVANAGARI_HINTS = {
    "मराठी": "Marathi", "आहे": "Marathi", "नाही": "Marathi", "काय": "Marathi",
    "है": "Hindi", "नहीं": "Hindi", "क्या": "Hindi", "और": "Hindi",
    "संस्कृतम्": "Sanskrit", "अस्ति": "Sanskrit",
}

#: Enough common words in Roman script to tell the languages apart when the
#: owner's phone sends them un-transliterated.
_ROMAN_HINTS: Dict[str, Tuple[str, ...]] = {
    "malayalam": ("ente", "njan", "ningal", "enthu", "illa", "sheri", "aanu",
                  "chechi", "chettan", "venda", "kittum", "ivide", "avide",
                  "sahayam", "venam", "peru", "enikku", "nalla", "undo"),
    "hindi": ("mera", "meri", "tum", "aap", "kaise", "kya", "hai", "nahi",
              "acha", "theek", "bhai", "yaar", "karo", "matlab"),
    "telugu": ("nenu", "meeru", "enti", "cheppandi", "ledu", "avunu", "ela",
               "manchi", "gari", "andi"),
    "kannada": ("nanu", "neevu", "enu", "illa", "haudu", "hegide", "swalpa",
                "guru", "anna"),
    "tamil": ("enna", "epdi", "vanakkam", "nalla", "illa", "sollu", "venum",
              "paaru", "nandri", "thambi", "akka"),
}

#: The phrases the owner is most likely to hear, glossed honestly as a **hint**,
#: never presented as a translation of the whole sentence.
GLOSS: Dict[str, Dict[str, str]] = {
    "malayalam": {
        "എന്റെ": "my", "പേര്": "name", "നന്ദി": "thanks", "ഇല്ല": "no / not",
        "അതെ": "yes", "എന്ത്": "what", "എവിടെ": "where", "ആര്": "who",
        "എപ്പോൾ": "when", "എങ്ങനെ": "how", "വെള്ളം": "water", "ഭക്ഷണം": "food",
        "വീട്": "house", "പണം": "money", "സുഖമാണോ": "are you well",
    },
    "hindi": {
        "नमस्ते": "hello", "धन्यवाद": "thank you", "हाँ": "yes", "नहीं": "no",
        "क्या": "what", "कहाँ": "where", "कौन": "who", "कब": "when",
        "कैसे": "how", "पानी": "water", "खाना": "food", "घर": "house",
        "पैसा": "money", "आप कैसे हैं": "how are you",
    },
    "telugu": {
        "నమస్తే": "hello", "ధన్యవాదాలు": "thank you", "అవును": "yes",
        "కాదు": "no", "ఏమి": "what", "ఎక్కడ": "where", "ఎవరు": "who",
        "ఎప్పుడు": "when", "ఎలా": "how", "నీళ్ళు": "water", "అన్నం": "food",
        "ఇల్లు": "house", "డబ్బు": "money",
    },
    "kannada": {
        "ನಮಸ್ಕಾರ": "hello", "ಧನ್ಯವಾದ": "thank you", "ಹೌದು": "yes",
        "ಇಲ್ಲ": "no", "ಏನು": "what", "ಎಲ್ಲಿ": "where", "ಯಾರು": "who",
        "ಯಾವಾಗ": "when", "ಹೇಗೆ": "how", "ನೀರು": "water", "ಊಟ": "food",
        "ಮನೆ": "house", "ಹಣ": "money",
    },
    "bengali": {
        "নমস্কার": "hello", "ধন্যবাদ": "thank you", "হ্যাঁ": "yes", "না": "no",
        "কি": "what", "কোথায়": "where", "কে": "who", "কখন": "when",
        "কীভাবে": "how", "জল": "water", "খাবার": "food", "বাড়ি": "house",
        "টাকা": "money",
    },
    "marathi": {
        "नमस्कार": "hello", "धन्यवाद": "thank you", "होय": "yes", "नाही": "no",
        "काय": "what", "कुठे": "where", "कोण": "who", "केव्हा": "when",
        "कसे": "how", "पाणी": "water", "जेवण": "food", "घर": "house",
        "पैसे": "money",
    },
    "gujarati": {
        "નમસ્તે": "hello", "આભાર": "thank you", "હા": "yes", "ના": "no",
        "શું": "what", "ક્યાં": "where", "કોણ": "who", "ક્યારે": "when",
        "કેવી રીતે": "how", "પાણી": "water", "ખાવાનું": "food",
    },
    "punjabi": {
        "ਸਤ ਸ੍ਰੀ ਅਕਾਲ": "hello", "ਧੰਨਵਾਦ": "thank you", "ਹਾਂ": "yes",
        "ਨਹੀਂ": "no", "ਕੀ": "what", "ਕਿੱਥੇ": "where", "ਕੌਣ": "who",
        "ਕਦੋਂ": "when", "ਕਿਵੇਂ": "how", "ਪਾਣੀ": "water",
    },
    "urdu": {
        "السلام علیکم": "hello", "شکریہ": "thank you", "ہاں": "yes",
        "نہیں": "no", "کیا": "what", "کہاں": "where", "کون": "who",
        "کب": "when", "کیسے": "how", "پانی": "water",
    },
    "tamil": {},
}

#: What Nila says she can do, in her own two languages.
CAPABILITY = (
    "Nila talks to you in Tamil and English only. She can read Malayalam, Hindi, "
    "Telugu, Kannada, Bengali, Marathi, Gujarati, Punjabi, Urdu and Sanskrit - "
    "and explain what was said to you in Tamil. Reading other languages needs a "
    "model; she says so plainly when none is configured."
)

_TA_WORDS = ("என்ன", "எப்படி", "நல்ல", "இல்லை", "வணக்கம்", "நன்றி", "சொல்லு",
             "வேண்டும்", "எங்கே", "எப்போது", "ஏன்", "ஆமா", "சரி")


def _count_range(text: str, lo: int, hi: int) -> int:
    return sum(1 for ch in text if lo <= ord(ch) <= hi)


def detect(text: str) -> Dict[str, Any]:
    """What language is this? Script first, then common words, then nothing."""
    t = str(text or "")
    letters = [ch for ch in t if ch.isalpha()]
    if not letters:
        return {"language": "unknown", "confidence": 0.0, "by": "no letters",
                "spoken": False}

    # 1) a non-Latin script decides it on its own
    counts = {name: _count_range(t, *rng) for name, rng in SCRIPTS.items()}
    best = max(counts, key=lambda k: counts[k])
    if counts[best] > 0:
        share = counts[best] / max(1, len(letters))
        if best == "devanagari":
            words = " ".join(re.findall(r"[\u0900-\u097F]+", t))
            for hint, lang in _DEVANAGARI_HINTS.items():
                if hint in words:
                    return {"language": lang, "confidence": 0.9, "by": "script+word",
                            "spoken": lang.lower() in SPOKEN}
            return {"language": "Hindi/Marathi/Sanskrit", "confidence": 0.7,
                    "by": "script", "spoken": False}
        lang = SCRIPT_LANG[best]
        return {"language": lang, "confidence": round(min(0.95, 0.6 + 0.35 * share), 2),
                "by": "script", "spoken": lang.lower() in SPOKEN}

    # 2) Latin letters: common words, and the Tamil words written in Latin
    low = " " + " ".join(re.findall(r"[a-z']+", t.lower())) + " "
    ta_hits = sum(1 for w in _TA_WORDS if w in t) + sum(
        1 for w in _ROMAN_HINTS["tamil"] if f" {w} " in low)
    scores = {lang: sum(1 for w in words if f" {w} " in low)
              for lang, words in _ROMAN_HINTS.items() if lang != "tamil"}
    best_lang = max(scores, key=lambda k: scores[k]) if scores else None
    best_score = scores.get(best_lang, 0) if best_lang else 0
    if best_lang and best_score > ta_hits and best_score >= 2:
        return {"language": best_lang.capitalize(), "confidence": 0.6,
                "by": "common words", "spoken": False}
    if ta_hits:
        return {"language": "Tamil", "confidence": 0.5, "by": "Tamil words in Latin letters",
                "spoken": True}
    return {"language": "English (or Latin-script text)", "confidence": 0.4,
            "by": "Latin script", "spoken": True}


def gloss(text: str, language: str) -> List[Tuple[str, str]]:
    """Words from the sentence that are known, with their meaning in English.

    A hint, not a translation: it is offered as "these words mean ..." and the
    caller must present it that way. Tamil output is the owner's language, so a
    Tamil meaning is added where the glossary has one.
    """
    table = GLOSS.get(str(language).lower(), {})
    if not table:
        return []
    found: List[Tuple[str, str]] = []
    for word, meaning in table.items():
        if word and word in text:
            found.append((word, meaning))
    return found[:8]


def explain(text: str, detected: Optional[Dict[str, Any]] = None,
            translated: str = "", model: str = "") -> str:
    """The line Nila says about a foreign message, in Tamil first.

    `translated` is only passed when a model really produced a translation. The
    two branches are deliberately worded differently so a guess can never read
    like a translation.
    """
    info = detected or detect(text)
    lang = info["language"]
    if info.get("spoken"):
        return ""
    head = (f"இது {lang} மொழி என்று தெரிகிறது "
            f"({info.get('confidence', 0):.0%} நம்பிக்கை, {info.get('by', '')}).")
    if translated:
        return (f"{head}\n\n**தமிழில்:**\n{translated}\n\n"
                f"(translated by {model or 'the configured model'})")
    lines = [head, ""]
    gl = gloss(text, lang)
    if gl:
        lines.append("இந்த வார்த்தைகள் எனக்குத் தெரியும் (முழு மொழிபெயர்ப்பு அல்ல):")
        for word, meaning in gl:
            lines.append(f"  {word} = {meaning}")
        lines.append("")
    lines.append("முழு மொழிபெயர்ப்புக்கு ஒரு model தேவை. `brain models`-ல் Gemini "
                 "key இருந்தால், Online mode-ல் ஒரு முறை கேட்டால் தமிழில் "
                 "விளக்கி விடுவேன் — அப்போது அந்த text மட்டும் network-க்குப் "
                 "போகும், அதைத் தெரிந்து வைத்துக் கொள்ளுங்கள்.")
    return "\n".join(lines)


def needs_translation(text: str) -> bool:
    return not detect(text).get("spoken", True)


def prompt_for(text: str, language: str, into: str = "Tamil") -> str:
    """The instruction handed to a model, when one is configured and allowed.

    Written to forbid the two things a model does here that hurt: inventing
    content, and answering in the source language because it "feels" natural.
    """
    return (
        f"The following message is in {language}. Translate it into {into} and "
        f"explain in one short paragraph what the person is asking for.\n"
        f"Rules: if a word is unclear, keep it in the original and say so; do not "
        f"add anything that is not in the message; answer in {into} only.\n\n"
        f"--- message ---\n{text}\n--- end ---"
    )
