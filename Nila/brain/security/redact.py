"""Deterministic secret detection, for text that has no tags to classify it.

Closes the largest remaining leak in the classification model: a card number
typed into a plain ``note`` carries no ``finance`` tag, so it classifies as
``internal`` and would be permitted to a cloud provider once the user enables
``allow_cloud_context``.

Design rules:

*   **Deterministic.** Regex and checksums only - no model, no heuristics that
    drift between runs, nothing that needs a download. A detector that is wrong
    differently each time cannot be tested.
*   **Escalate, never de-escalate.** Detection can only raise a record's class.
*   **Cheap enough to run on every write.** So it runs on the write path, where
    the fix belongs, rather than on egress, where the data is already moving.

Two uses:

``scan(text)``       -> what patterns were found (drives classification)
``redact(text)``     -> the same text with matches replaced (drives egress)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple

from .classify import CLASS_RESTRICTED, CLASS_SENSITIVE

# --------------------------------------------------------------------------- #
# patterns
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Pattern:
    name: str
    regex: "re.Pattern[str]"
    klass: str
    #: extra check beyond the regex, to cut false positives
    validator: Optional[str] = None
    #: what a match is replaced with in redacted output
    mask: str = ""


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum. Rejects the overwhelming majority of random digit runs."""
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _digit_run_too_uniform(digits: str) -> bool:
    """Reject 1111111111, 1234567890123456 and similar placeholders."""
    if len(set(digits)) <= 2:
        return True
    seq = [ord(c) - 48 for c in digits]
    if all(seq[i + 1] - seq[i] == 1 for i in range(len(seq) - 1)):
        return True
    if all(seq[i + 1] - seq[i] == -1 for i in range(len(seq) - 1)):
        return True
    return False


_PHONE_LABEL_RE = re.compile(
    r"(?:\b(?:phone|mobile|cellphone|cell|telephone|contact)"
    r"(?:\s+(?:number|no\.?))?|\bcall\s+me\s+at|"
    r"(?:தொலைபேசி|கைபேசி|மொபைல்)(?:\s*(?:எண்|நம்பர்))?)"
    r"\s*(?:is|:|#|=)?\s*$", re.I)


# ORDER IS SEMANTIC: patterns are evaluated in sequence and a match is skipped
# if it falls inside an already-recorded longer match. Checksummed, highly
# specific patterns therefore come FIRST - otherwise an unvalidated 12-digit
# Aadhaar pattern swallows a Luhn-valid 16-digit card number.
PATTERNS: Tuple[Pattern, ...] = (
    # --- payment instruments (checksummed / tightly shaped) ---------------
    Pattern("card", re.compile(r"(?<![\dA-Za-z])(?:\d[ -]?){13,19}"),
            CLASS_RESTRICTED, validator="card", mask="[CARD]"),
    Pattern("cvv", re.compile(r"\b(?:cvv|cvc|cvv2|security code)\s*[:#]?\s*(\d{3,4})\b", re.I),
            CLASS_RESTRICTED, mask="[CVV]"),
    Pattern("card_expiry", re.compile(r"\b(?:exp(?:iry|ires)?|valid(?:\s+thru|\s+till)?)\s*"
                                      r"[:#]?\s*(0[1-9]|1[0-2])\s*/\s*(\d{2}|\d{4})\b", re.I),
            CLASS_RESTRICTED, mask="[EXPIRY]"),
    Pattern("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), CLASS_RESTRICTED,
            mask="[IBAN]"),
    Pattern("ssn", re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
            CLASS_RESTRICTED, mask="[SSN]"),

    # --- Indian identifiers (the deployment locale) -----------------------
    # Case-insensitive: people write "pan abcde1234f" and "passport p1234567".
    Pattern("pan", re.compile(r"\b[A-Za-z]{5}[0-9]{4}[A-Za-z]\b"), CLASS_RESTRICTED,
            validator="pan", mask="[PAN]"),
    Pattern("aadhaar", re.compile(r"(?<![\dA-Za-z])[2-9]\d{3}(?:[ -]?\d{4}){2}"),
            CLASS_RESTRICTED, validator="aadhaar", mask="[AADHAAR]"),
    Pattern("upi", re.compile(r"\b[\w.\-]{2,49}@(?:okaxis|okhdfcbank|oksbi|okicici|ybl|ibl|"
                              r"apl|paytm|phonepe|amazonpay|axisbank|barodampay|idfcbank|"
                              r"kotak|sc|sib|tap|upi|yesbank|citi|hsbc|freecharge|jupiter|"
                              r"postpaid|reliance|unionbank|vijayabank|wa)\b"),
            CLASS_RESTRICTED, mask="[UPI]"),
    Pattern("ifsc", re.compile(r"\b[A-Za-z]{4}0[A-Za-z0-9]{6}\b"), CLASS_SENSITIVE,
            validator="ifsc", mask="[IFSC]"),

    # --- government / travel documents -----------------------------------
    Pattern("passport_in", re.compile(r"\b(?:passport|pp)\s*(?:no\.?|number|#|:)?\s*"
                                      r"[A-Za-z]\d{7}\b", re.I), CLASS_RESTRICTED,
            mask="[PASSPORT]"),
    Pattern("dl_in", re.compile(r"\b(?:dl|driving licen[cs]e)\s*(?:no\.?|number|#|:)?\s*"
                                r"[A-Za-z]{2}\d{1,2}[ /-]?\d{4,13}\b", re.I),
            CLASS_RESTRICTED, mask="[DL]"),
    Pattern("voter_id", re.compile(r"\b[A-Za-z]{3}\d{7}\b"), CLASS_RESTRICTED,
            validator="voter", mask="[VOTERID]"),

    # --- credentials in free text ----------------------------------------
    Pattern("secret_assignment", re.compile(
        r"\b(?:password|passwd|passphrase|passcode|secret|api[_-]?key|apikey|access[_-]?token|"
        r"auth[_-]?token|refresh[_-]?token|private[_-]?key|client[_-]?secret|otp|pin)\b"
        r"\s*(?:=|:|is|->)\s*([^\s,;\"']{4,128})", re.I), CLASS_RESTRICTED,
        mask="[SECRET]"),
    Pattern("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{16,}={0,2}"),
            CLASS_RESTRICTED, mask="[BEARER]"),
    Pattern("aws_key", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
            CLASS_RESTRICTED, mask="[AWSKEY]"),
    Pattern("google_api", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), CLASS_RESTRICTED,
            mask="[GCPKEY]"),
    Pattern("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36,}\b"),
            CLASS_RESTRICTED, mask="[GHTOKEN]"),
    Pattern("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
            CLASS_RESTRICTED, mask="[SLACKTOKEN]"),
    Pattern("openai_key", re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}\b"), CLASS_RESTRICTED,
            mask="[OPENAIKEY]"),
    Pattern("pem_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
            CLASS_RESTRICTED, mask="[PRIVATEKEY]"),
    Pattern("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
            CLASS_RESTRICTED, mask="[JWT]"),
    Pattern("connection_string", re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)://[^\s'\"]{6,}", re.I),
        CLASS_RESTRICTED, mask="[DSN]"),

    # --- contact identifiers (sensitive, not restricted) -----------------
    Pattern("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
            CLASS_SENSITIVE, mask="[EMAIL]"),
    Pattern("phone_in", re.compile(r"(?<![\dA-Za-z])(?:\+?91[ -]?)?[6-9]\d{4}[ -]?\d{5}(?![\dA-Za-z])"),
            CLASS_SENSITIVE, validator="phone", mask="[PHONE]"),
    Pattern("phone_intl", re.compile(r"(?<!\d)\+\d{1,3}[ -]?\d{2,4}[ -]?\d{3,4}[ -]?\d{3,4}(?!\d)"),
            CLASS_SENSITIVE, mask="[PHONE]"),
    Pattern("dob", re.compile(r"\b(?:dob|date of birth|born on)\s*[:#]?\s*"
                             r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b", re.I),
            CLASS_SENSITIVE, mask="[DOB]"),
)

#: words that make a bare digit run look like an identifier rather than a quantity
_STOPWORD_HINTS = ("no", "number", "num", "#", "id", "ref", "code")

#: words that make a bare 12-digit run attributable to Aadhaar rather than to an
#: amount, an order id or a phone fragment
_AADHAAR_HINTS = ("aadhaar", "aadhar", "uid", "uidai", "biometric id")

#: words just before a 12-digit run that contradict an Aadhaar reading
_AADHAAR_VETO = ("card", "visa", "mastercard", "rupay", "rupee", "amex", "diners",
                 "debit", "credit", "cvv", "cvc", "expiry", "expires", "loan",
                 "account no", "a/c", "cheque", "utr", "txn", "invoice", "bill")


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    name: str
    klass: str
    start: int
    end: int
    masked: str
    order: int = 0


@dataclass
class ScanResult:
    findings: List[Finding] = field(default_factory=list)
    #: tags added by the lexicon detectors below. Kept separate from findings
    #: because they TAG without MASKING - see KEYWORD_DETECTORS.
    keyword_tags: List[str] = field(default_factory=list)
    keyword_class: Optional[str] = None
    keyword_hits: List[str] = field(default_factory=list)

    @property
    def names(self) -> List[str]:
        return sorted({f.name for f in self.findings})

    @property
    def max_class(self) -> Optional[str]:
        from .classify import ORDER

        best = None
        for k in [f.klass for f in self.findings] + [self.keyword_class]:
            if not k:
                continue
            if best is None or ORDER.index(k) > ORDER.index(best):
                best = k
        return best

    @property
    def restricted(self) -> bool:
        return any(f.klass == CLASS_RESTRICTED for f in self.findings)

    def tags(self) -> List[str]:
        """Tags implied by what was found - added to the record on write."""
        out = list(self.keyword_tags)
        names = set(self.names)
        if names & {"card", "iban", "cvv", "card_expiry", "ifsc", "upi"}:
            out.append("finance")
        if names & {"pan", "aadhaar", "passport_in", "dl_in", "ssn", "voter_id"}:
            out.append("government-id")
        if names & {"secret_assignment", "bearer", "aws_key", "google_api", "github_token",
                    "slack_token", "openai_key", "pem_block", "jwt", "connection_string"}:
            out.append("credential")
        if names & {"email", "phone_in", "phone_intl"}:
            out.append("contact")
        if names & {"dob"}:
            out.append("health")
        if self.restricted:
            out.append("sensitive")
        return out


def _validate(pat: Pattern, text: str, m: "re.Match[str]") -> bool:
    v = pat.validator
    if not v:
        return True
    if v == "card":
        digits = re.sub(r"\D", "", m.group(0))
        if not 13 <= len(digits) <= 19:
            return False
        # Luhn alone is the discriminator. An earlier version also rejected
        # "uniform" digit runs, which threw away 4111111111111111 - the single
        # most common real test card number. Luhn already rejects the random
        # 13-19 digit runs that uniformity was meant to catch.
        return _luhn_ok(digits)
    if v == "pan":
        # PAN is 5 letters, 4 digits, 1 letter - reject ordinary mixed words
        raw = m.group(0)
        if not re.fullmatch(r"[A-Za-z]{5}[0-9]{4}[A-Za-z]", raw):
            return False
        upper = raw.upper()
        # PAN's fourth character is the holder type (C=company, P=person,
        # H=HUF, F=firm, A=AOP, B=BOI, G=government, J=trust, L=LLP, T=trust).
        # The fifth character is a holder-name initial and may be any letter.
        return upper[3] in "CPHFABGJLT"
    if v == "ifsc":
        return m.group(0).upper()[4] == "0"
    if v == "voter":
        # EPIC is 3 letters + 7 digits. Reject a run of the same letter/digit.
        raw = m.group(0).upper()
        return len(set(raw[:3])) > 1 and len(set(raw[3:])) > 2
    if v == "aadhaar":
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) != 12 or _digit_run_too_uniform(digits):
            return False
        # Verhoeff would be the real check; without it a bare 12-digit run is
        # too ambiguous to claim (amounts, order ids, phone fragments). So
        # require EITHER an explicit separator grouping, which people only use
        # for identifiers, or a contextual keyword just before the number.
        separated = bool(re.fullmatch(r"\d{4}[ -]\d{4}[ -]\d{4}", m.group(0).strip()))
        window = text[max(0, m.start() - 48):m.start()].lower()
        # The words immediately before a 4-4-4 digit run can contradict an
        # Aadhaar reading. "card 4417 1234 9876 5532" is a malformed card
        # number, not an ID - but grouping alone implies Aadhaar. Both formats
        # group 4-4-4, so once Luhn has failed, context is the only reliable
        # discriminator left.
        if any(k in window for k in _AADHAAR_VETO):
            return False
        contextual = any(k in window for k in _AADHAAR_HINTS)
        if not (separated or contextual):
            return False
        if digits[0] in "01":
            return False
        if len(set(digits)) <= 3:
            return False
        return True
    if v == "phone":
        digits = re.sub(r"\D", "", m.group(0))
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]              # drop the country code before judging
        if len(digits) != 10:
            return False
        tail = digits
        if _digit_run_too_uniform(tail):
            # Still reject repeated placeholders, but a descending/ascending
            # demo sequence is accepted when explicitly labeled as a phone.
            # This lets us detect labeled 10-digit Indian numbers without
            # treating arbitrary sequential quantities as phone data.
            if len(set(tail)) <= 2:
                return False
            prefix = text[max(0, m.start() - 64):m.start()]
            if not _PHONE_LABEL_RE.search(prefix):
                return False
        # Reject things that are really dates or amounts embedded in prose.
        if re.search(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", m.group(0)):
            return False
        return True
    return True


# --------------------------------------------------------------------------- #
# lexicon detectors: tag WITHOUT masking
# --------------------------------------------------------------------------- #
#: A regex cannot catch "the biopsy came back benign", yet that is sensitive
#: health data which must not reach a cloud prompt. These detectors escalate
#: classification and add a tag, but deliberately do NOT mask: replacing the
#: word "biopsy" with [HEALTH] would mangle the user's own note and destroy
#: meaning they wrote on purpose. Tagging is enough, because the egress gate
#: keys off the class, not off the text.
#:
#: This is a lexicon, not a clinical NER model, and it is documented as such
#: (THREAT_MODEL T31). It trades recall for precision: the terms below are
#: specific enough that ordinary notes do not trip them.
KEYWORD_DETECTORS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("health", CLASS_SENSITIVE, (
        # --- Tamil (தமிழ்): the owner operates in Tamil/Tanglish, and an
        # --- English-only lexicon silently filed a Tamil health note as
        # --- ordinary `internal` data, i.e. NOT withheld from a cloud model.
        "நோய்", "நீரிழிவு", "இன்சுலின்", "மருந்து", "மருத்துவர்",
        "மருத்துவமனை", "அறுவை", "சிகிச்சை", "புற்றுநோய்", "பரிசோதனை",
        "இரத்த அழுத்தம்", "உடல்நிலை", "உடல்நிலை சரியில்லை", "காய்ச்சல்", "வலி", "உளவியல்", "மன அழுத்தம்",
        "கர்ப்பம்", "பயாப்சி", "ஊசி", "நோயாளி", "ஆஸ்பத்திரி",
        # --- romanized Tamil / Tanglish, the register actually typed ---
        "niram", "marundhu", "maruthuvam", "manthuvam", "doctor-oda",
        "sugar niram", "insulin", "bp-ah", "kayasal", "manavi",
        # clinical events and results; list inflections explicitly so every
        # detector still matches complete words rather than arbitrary stems.
        "biopsy", "biopsies", "benign", "malignant", "metastasis", "metastases",
        "metastatic", "metastasized", "metastasizing", "tumour", "tumours", "tumor",
        "tumors", "cancer", "cancers", "carcinoma", "carcinomas", "lymphoma",
        "lymphomas", "leukemia", "leukaemia", "diagnosis", "diagnoses", "diagnosed",
        "diagnosing", "prognosis", "prognoses",
        "surgery", "surgeries", "surgical", "operation theatre", "anaesthesia", "anesthesia",
        "radiology", "mri scan", "ct scan", "x-ray", "xray", "ultrasound",
        "ecg", "ekg", "mammogram", "colonoscopy", "endoscopy", "dialysis",
        "chemotherapy", "radiotherapy", "immunotherapy",
        # tests and measurements
        "blood test", "blood work", "blood report", "lab report", "lipid profile",
        "hba1c", "blood sugar", "blood pressure", "hypertension", "cholesterol",
        "triglyceride", "triglycerides", "thyroid", "tsh", "haemoglobin", "hemoglobin", "anaemia",
        "anemia", "vitamin d", "b12 level", "urine culture", "pathology report",
        # conditions
        "diabetes", "diabetic", "insulin", "asthma", "epilepsy", "seizure", "seizures",
        "migraine", "migraines", "arthritis", "hiv", "hepatitis", "tb treatment",
        "pregnant", "pregnancy", "pregnancies", "miscarriage", "miscarriages", "ivf",
        "fertility", "menopause",
        # mental health
        "therapy session", "therapist", "psychiatrist", "psychologist",
        "counsellor", "counselor", "counselling", "counseling", "antidepressant",
        "anxiety disorder", "panic attack", "depression", "bipolar", "ocd",
        "ptsd", "adhd", "self-harm", "suicidal",
        # treatment
        "prescription", "prescribed", "dosage", "medication", "medicine for",
        "antibiotic", "steroid", "chemo", "physiotherapy", "rehab",
        "hospital", "clinic", "doctor said", "dr.", "specialist appointment",
    )),
    ("legal", CLASS_SENSITIVE, (
        "court date", "litigation", "lawyer said", "legal notice", "affidavit",
        "settlement agreement", "custody battle", "divorce petition",
    )),
    # Personal finance is restricted, and family/relationship facts are
    # sensitive. These vocabulary terms require explicit possessive context;
    # generic business uses of the same words are not personal data.
    ("finance", CLASS_RESTRICTED, (
        "salary", "salaries", "paycheck", "paychecks", "pay cheque", "income", "earnings",
        "debt", "debts", "loan", "loans", "mortgage", "mortgages", "bank balance",
        "account balance", "bank account", "credit score", "credit card bill", "emi", "emis",
        "investment", "investments", "saving", "savings", "net worth", "tax return",
        "financial", "finance", "finances",
        "சம்பளம்", "சம்பளத்தை", "சம்பளத்தில்", "சம்பளத்தின்", "ஊதியம்", "வருமானம்",
        "சம்பாதிப்பு", "கடன்", "கடனை", "கடனில்", "கடனுக்கு", "கடனின்", "கடன்தொகை",
        "வங்கி இருப்பு", "வங்கிக் கணக்கு", "சேமிப்பு", "முதலீடு", "வட்டி", "ஈஎம்ஐ",
        "கிரெடிட் கார்டு", "sambalam", "varumanam", "sambadhiyam", "kadan", "vatti",
        "bank balance", "bank account", "emi", "selavu",
    )),
    ("family", CLASS_SENSITIVE, (
        "family", "families", "wife", "wives", "husband", "husbands", "spouse", "spouses",
        "partner", "partners", "daughter", "daughters", "son", "sons", "child", "children",
        "parent", "parents", "mother", "mothers", "father", "fathers", "brother", "brothers",
        "sister", "sisters", "sibling", "siblings", "grandmother", "grandmothers",
        "grandfather", "grandfathers", "relative", "relatives", "relationship", "relationships",
        "குடும்பம்", "குடும்பத்தினர்", "மனைவி", "மனைவிக்கும்", "மனைவியை", "மனைவியின்",
        "மனைவியுடன்", "மனைவியும்", "கணவர்", "துணைவர்", "மகள்", "மகன்", "குழந்தை",
        "குழந்தைகள்", "அம்மா", "அப்பா", "அப்பாவுக்கு", "அப்பாவை", "அப்பாவின்", "தந்தை", "தாய்", "பெற்றோர்", "அண்ணன்", "அக்கா",
        "தம்பி", "தங்கை", "உறவு", "உறவினர்", "காதலி", "காதலன்",
        "kudumbam", "manaivi", "kanavar", "thunaivar", "magal", "magan", "kuzhandhai",
        "kulanthai", "amma", "appa", "thandhai", "thai", "pettror", "annan", "akka",
        "thambi", "thangachi", "uravu",
    )),
)

# Possessive references make a finance/family term personal. Match complete
# Unicode words/phrases: Python's ``\w`` omits some combining marks used by
# Tamil, so check Unicode letter/mark/number categories at each match edge.
_PERSONAL_CONTEXT_TAGS = frozenset(("finance", "family"))
_PERSONAL_OWNER_MARKERS = (
    "my", "mine", "your", "yours", "his", "her", "hers", "their", "theirs",
    "என்", "எனது", "எனக்கு", "என்னுடைய", "எங்கள்", "எங்களின்", "எங்களுடைய", "நம்", "நமது", "நம்முடைய",
    "உன்", "உங்கள்", "உங்களுடைய", "அவன்", "அவள்", "அவரது", "அவருடைய", "அவர்களின்",
    "en", "enakku", "ennoda", "enoda", "engal", "engaloda", "namma", "nammoda", "un", "unga",
    "ungaloda", "avan", "avanoda", "aval", "avaloda", "avanga", "avangoda",
)
_FAMILY_OWNER_MARKERS = ("our", "ours")
_PERSONAL_FINANCE_ASSERTIONS = (
    re.compile(
        r"(?<!\w)(?:i|we)\s+(?:owe|owed)\s+(?:(?:the|my|our)\s+)?"
        r"(?:bank|lender|creditor|loan|debt|money)(?!\w)", re.I),
    re.compile(
        r"(?<!\w)(?:i|we)\s+(?:am|are|was|were)\s+"
        r"(?:(?:currently|deeply|heavily)\s+)*(?:in\s+debt|indebted)(?!\w)", re.I),
    re.compile(
        r"(?<!\w)(?:i|we)\s+(?:have|got|took\s+out|taken\s+out)\s+"
        r"(?:(?:a|an|my|our)\s+)?(?:(?:home|personal|student|business)\s+)?loan\b"
        r"[^\n.!?;]{0,40}\b\d+(?:[.,]\d+)?\s*"
        r"(?:k|thousand|lakhs?|lacs?|crores?|rupees?|inr|rs\.?)(?!\w)", re.I),
)


@lru_cache(maxsize=512)
def _keyword_pattern(term: str) -> "re.Pattern[str]":
    """Compile a complete word or phrase detector, allowing flexible spaces."""
    body = r"\s+".join(re.escape(part) for part in term.split())
    return re.compile(body, re.I)


def _unicode_word_char(ch: str) -> bool:
    category = unicodedata.category(ch)
    return category[0] in "LMN" or category == "Pc"


def _keyword_matches(text: str, term: str):
    """Yield term matches bounded by Unicode word edges, not substrings."""
    for match in _keyword_pattern(term).finditer(text):
        if match.start() and _unicode_word_char(text[match.start() - 1]):
            continue
        if match.end() < len(text) and _unicode_word_char(text[match.end()]):
            continue
        yield match


def _has_personal_owner_before(text: str, target: "re.Match[str]", tag: str) -> bool:
    """Require a nearby possessive marker before a finance/family term."""
    prefix = text[max(0, target.start() - 80):target.start()]
    markers = _PERSONAL_OWNER_MARKERS
    if tag == "family":
        markers += _FAMILY_OWNER_MARKERS
    for marker in markers:
        for owner in _keyword_matches(prefix, marker):
            gap = prefix[owner.end():]
            if re.search(r"[.!?;\n]", gap):
                continue
            if len(re.findall(r"\S+", gap)) <= 3:
                return True
    return False


def _scan_keywords(text: str, res: "ScanResult") -> None:
    low = (text or "").lower()
    if not low:
        return
    for tag, klass, terms in KEYWORD_DETECTORS:
        hits = []
        for term in terms:
            for match in _keyword_matches(low, term):
                if tag in _PERSONAL_CONTEXT_TAGS and not _has_personal_owner_before(low, match, tag):
                    continue
                hits.append(term)
                break
        if tag == "finance" and any(pattern.search(low) for pattern in _PERSONAL_FINANCE_ASSERTIONS):
            hits.append("first-person debt statement")
        if not hits:
            continue
        if tag not in res.keyword_tags:
            res.keyword_tags.append(tag)
        res.keyword_hits.extend(hits)
        from .classify import ORDER
        if res.keyword_class is None or ORDER.index(klass) > ORDER.index(res.keyword_class):
            res.keyword_class = klass


def scan(text: str) -> ScanResult:
    """Find every high-confidence identifier/secret in ``text``.

    Overlapping candidates are resolved by **longest validated match wins**,
    with pattern order as the tie-break. Two earlier rules were both wrong:

    *   *discard anything nested in a longer match* - suppressed a correct PAN
        inside a card candidate, and hid real findings behind greedy matches.
    *   *first pattern wins* - let an unvalidated 12-digit Aadhaar pattern
        pre-empt a Luhn-valid 16-digit card number.

    Longest-wins gets both right: for "4417 1234 9876 5532" the card candidate
    spans 16 digits and validates, so the 12-digit Aadhaar prefix loses; for
    "2345 6789 0123" the card candidate spans the same 12 digits but fails
    Luhn, so it is discarded and Aadhaar wins.
    """
    if not text:
        return ScanResult()

    candidates: List[Finding] = []
    for order, pat in enumerate(PATTERNS):
        for m in pat.regex.finditer(text):
            if not _validate(pat, text, m):
                continue
            candidates.append(Finding(name=pat.name, klass=pat.klass,
                                      start=m.start(), end=m.end(),
                                      masked=pat.mask or f"[{pat.name.upper()}]"))
            candidates[-1].order = order          # type: ignore[attr-defined]

    # longest first; equal length -> earlier pattern wins
    candidates.sort(key=lambda f: (-(f.end - f.start),
                                   getattr(f, "order", 0), f.start))
    kept: List[Finding] = []
    for cand in candidates:
        if any(not (cand.end <= k.start or cand.start >= k.end) for k in kept):
            continue                              # overlaps something longer
        kept.append(cand)
    kept.sort(key=lambda f: f.start)
    res = ScanResult(findings=kept)
    # Lexicon pass: tags sensitive *topics* that carry no matchable identifier.
    # Runs after the regex pass so a record can be both (a card number inside a
    # hospital note gets `finance` + `health`).
    _scan_keywords(text, res)
    return res


def redact(text: str, keep_last: int = 4) -> Tuple[str, List[str]]:
    """Return ``(redacted_text, pattern_names)``.

    ``keep_last`` preserves the trailing digits of a card or phone number so an
    answer can still say "the card ending 5532" without disclosing the number.
    """
    result = scan(text)
    if not result.findings:
        return text, []
    out: List[str] = []
    cursor = 0
    for f in result.findings:
        if f.start < cursor:
            continue
        out.append(text[cursor:f.start])
        original = text[f.start:f.end]
        digits = re.sub(r"\D", "", original)
        if keep_last and f.name in ("card", "phone_in", "phone_intl", "aadhaar") \
                and len(digits) > keep_last:
            out.append(f"{f.masked}-{'*' * (len(digits) - keep_last)}{digits[-keep_last:]}")
        else:
            out.append(f.masked)
        cursor = f.end
    out.append(text[cursor:])
    return "".join(out), result.names


def escalate(klass: str, found: Optional[str]) -> str:
    """Return the higher of two classes."""
    from .classify import ORDER

    if not found:
        return klass
    return found if ORDER.index(found) > ORDER.index(klass) else klass
