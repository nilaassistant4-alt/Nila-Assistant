"""Everyday money and measurement maths, done in Python and shown with its steps.

The owner asked for a calculator that answers the questions people actually ask:
"1250-ல 18% GST எவ்வளவு", "இருநூறு ரூபாய்க்கு 15 சதவீதம்", "5000-க்கு 8% வட்டி",
"5 அடி எத்தனை சென்டிமீட்டர்". A generic expression evaluator cannot read any of
those, and a language model can get them subtly wrong - so the arithmetic here is
plain Python, and every answer carries the steps it took.

Design rules:

* **No ``eval``.** Numbers are parsed, then combined by named operations.
* **Refuse rather than guess.** If no known shape matches, ``solve`` returns
  ``None`` and the caller falls back to the ordinary calculator or the model.
* **Tamil and English are equal citizens**, since the owner asks in both.
* **Exact where money must be exact**: amounts are rounded with ``Decimal`` half
  up, the way a shop rounds, not with binary floats.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# numbers, in both languages
# --------------------------------------------------------------------------- #
_UNITS_EN = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_UNITS_TA = {
    "பூஜ்ஜியம்": 0, "சுழியம்": 0, "ஒன்று": 1, "ஒரு": 1, "இரண்டு": 2, "இரு": 2,
    "மூன்று": 3, "நான்கு": 4, "ஐந்து": 5, "ஆறு": 6, "ஏழு": 7, "எட்டு": 8,
    "ஒன்பது": 9, "பத்து": 10, "பதினொன்று": 11, "பன்னிரண்டு": 12, "பதின்மூன்று": 13,
    "பதினான்கு": 14, "பதினைந்து": 15, "பதினாறு": 16, "பதினேழு": 17, "பதினெட்டு": 18,
    "பத்தொன்பது": 19, "இருபது": 20, "முப்பது": 30, "நாற்பது": 40, "ஐம்பது": 50,
    "அறுபது": 60, "எழுபது": 70, "எண்பது": 80, "தொண்ணூறு": 90,
    "நூறு": 100, "இருநூறு": 200, "முந்நூறு": 300, "நானூறு": 400, "ஐந்நூறு": 500,
    "அறுநூறு": 600, "எழுநூறு": 700, "எண்ணூறு": 800, "தொள்ளாயிரம்": 900,
}
#: Multipliers that scale the number in front of them ("2 லட்சம்", "5 crore").
_SCALES = {
    "hundred": 100, "thousand": 1_000, "k": 1_000, "lakh": 100_000,
    "lakhs": 100_000, "lack": 100_000, "lacs": 100_000, "crore": 10_000_000,
    "crores": 10_000_000, "million": 1_000_000, "mn": 1_000_000,
    "நூறு": 100, "ஆயிரம்": 1_000, "ஆயிரத்து": 1_000, "லட்சம்": 100_000,
    "லட்ச": 100_000, "கோடி": 10_000_000,
}
_ALL_WORDS = {**_UNITS_EN, **_UNITS_TA}
_CURRENCY_WORDS = {"rupee", "rupees", "rs", "inr", "ரூபாய்", "ரூபாய்க்கு", "ரூபா",
                   "ரூபாயில்", "பைசா"}

_AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?|rupees?|ரூபாய்|ரூ\.?)?\s*"
    r"(\d[\d,]*(?:\.\d+)?|[a-z஀-௿]+(?:\s+[a-z஀-௿]+){0,3})"
    r"(?:\s*(lakhs?|lacks?|crores?|million|thousand|k|"
    r"ஆயிரம்|லட்சம்|கோடி|நூறு)(?![a-z஀-௿]))?", re.I)


def _to_number(token: str) -> Optional[float]:
    token = " ".join(str(token or "").replace(",", "").split()).lower()
    if not token:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*(lakh|lakhs|lack|crore|crores|million|"
                     r"thousand|k|ஆயிரம்|லட்சம்|கோடி|நூறு))?", token)
    if m:
        value = float(m.group(1))
        if m.group(2):
            value *= _SCALES.get(m.group(2).lower(), 1)
        return value
    total = 0.0
    seen = False
    for word in re.split(r"[\s\-]+", token):
        if word in _SCALES:
            total = (total or 1.0) * _SCALES[word]
            seen = True
        elif word in _ALL_WORDS:
            total += _ALL_WORDS[word]
            seen = True
        elif word in ("and", "மற்றும்") or word in _CURRENCY_WORDS:
            continue
        else:
            # The phrase may carry a noun the number does not need ("இருநூறு
            # ரூபாய்க்கு", "five people"). Read what came before it and stop,
            # rather than throwing away a number that was perfectly clear.
            break
    return total if seen else None


def first_amount(text: str, skip: Tuple[str, ...] = ()) -> Optional[Tuple[float, str]]:
    """The first readable number in the text, with the words that gave it."""
    for m in _AMOUNT_RE.finditer(str(text or "")):
        raw = m.group(1) or ""
        if any(s in raw.lower() for s in skip):
            continue
        value = _to_number(raw + (" " + m.group(2) if m.group(2) else ""))
        if value is not None:
            return value, m.group(0).strip()
    return None


def _pct(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent|சதவீதம்|சதவிகிதம்)", str(text), re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(?:percent|per cent|சதவீதம்|சதவிகிதம்)\s*(\d+(?:\.\d+)?)", str(text), re.I)
    return float(m.group(1)) if m else None


def _money(x: float) -> str:
    return f"{x:,.2f}"


def _round(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- #
# the four operations people ask for
# --------------------------------------------------------------------------- #
def percent_of(base: float, pct: float) -> Dict[str, Any]:
    part = base * pct / 100.0
    return {"ok": True, "kind": "percent_of", "base": base, "percent": pct,
            "result": _round(part),
            "steps": [f"{pct:g}% of {_money(base)}",
                      f"{base:g} x {pct:g} / 100"],
            "answer": f"{pct:g}% of {_money(base)} = {_money(part)}"}


def add_gst(base: float, rate: float) -> Dict[str, Any]:
    tax = base * rate / 100.0
    total = base + tax
    return {"ok": True, "kind": "gst_add", "base": base, "rate": rate,
            "tax": _round(tax), "result": _round(total),
            "steps": [f"GST = {_money(base)} x {rate:g}% = {_money(tax)}",
                      f"total = {_money(base)} + {_money(tax)}"],
            "answer": (f"{rate:g}% GST on {_money(base)} is {_money(tax)}; "
                       f"total {_money(total)}")}


def remove_gst(total: float, rate: float) -> Dict[str, Any]:
    """A price that already includes GST: what was the tax inside it?"""
    base = total * 100.0 / (100.0 + rate)
    tax = total - base
    return {"ok": True, "kind": "gst_remove", "total": total, "rate": rate,
            "tax": _round(tax), "result": _round(base),
            "steps": [f"base = {_money(total)} x 100 / (100 + {rate:g})",
                      f"GST inside = {_money(total)} - {_money(base)}"],
            "answer": (f"of {_money(total)} at {rate:g}% inclusive, the price before "
                       f"tax is {_money(base)} and the GST is {_money(tax)}")}


def discount(price: float, pct: float) -> Dict[str, Any]:
    off = price * pct / 100.0
    return {"ok": True, "kind": "discount", "price": price, "percent": pct,
            "saved": _round(off), "result": _round(price - off),
            "steps": [f"discount = {_money(price)} x {pct:g}% = {_money(off)}"],
            "answer": f"{pct:g}% off {_money(price)} saves {_money(off)}; pay {_money(price - off)}"}


def interest(principal: float, rate: float, years: float,
             compound: bool = False, per_year: int = 1) -> Dict[str, Any]:
    if years < 0:
        return {"ok": False, "error": "years cannot be negative"}
    if compound:
        amount = principal * (1 + rate / 100.0 / per_year) ** (per_year * years)
        steps = [f"amount = {_money(principal)} x (1 + {rate:g}%/{per_year})^({per_year} x {years:g})",
                 f"interest = amount - principal"]
        kind = "compound"
    else:
        amount = principal + principal * rate / 100.0 * years
        steps = [f"interest = {_money(principal)} x {rate:g}% x {years:g}",
                 f"amount = principal + interest"]
        kind = "simple"
    return {"ok": True, "kind": f"interest_{kind}", "principal": principal,
            "rate": rate, "years": years, "interest": _round(amount - principal),
            "result": _round(amount), "steps": steps,
            "answer": (f"{kind} interest on {_money(principal)} at {rate:g}% for {years:g} "
                       f"year(s) is {_money(amount - principal)}; total {_money(amount)}")}


#: name -> (factor to the metre / gram / litre base, label)
_LEN = {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0, "inch": 0.0254,
        "inches": 0.0254, "ft": 0.3048, "foot": 0.3048, "feet": 0.3048,
        "yd": 0.9144, "yard": 0.9144, "mile": 1609.344, "miles": 1609.344,
        "அடி": 0.3048, "மீட்டர்": 1.0, "கி.மீ": 1000.0, "கிலோமீட்டர்": 1000.0,
        "சென்டிமீட்டர்": 0.01, "அங்குலம்": 0.0254, "மைல்": 1609.344}
_MASS = {"mg": 0.001, "g": 1.0, "kg": 1000.0, "quintal": 100_000.0,
         "ton": 1_000_000.0, "tonne": 1_000_000.0, "lb": 453.59237,
         "lbs": 453.59237, "pound": 453.59237, "pounds": 453.59237,
         "oz": 28.349523125,
         "கிராம்": 1.0, "கிலோ": 1000.0, "கிலோகிராம்": 1000.0, "டன்": 1_000_000.0,
         "பவுண்டு": 453.59237, "பவுண்ட்": 453.59237, "அவுன்ஸ்": 28.349523125}
_VOL = {"ml": 0.001, "l": 1.0, "litre": 1.0, "liter": 1.0, "gallon": 3.785411784,
        "மில்லி": 0.001, "லிட்டர்": 1.0}
_TEMP = {"c", "f", "celsius", "fahrenheit", "செல்சியஸ்", "பாரன்ஹீட்"}

#: One pattern for every unit name, longest first so "கிலோகிராம்" is not "கிலோ".
_UNIT_TOKEN = (r"(mm|cm|km|kg|mg|ml|ft|feet|foot|inch|inches|kg|g|lb|lbs|pounds?|oz|"
               r"litre|liter|miles?|celsius|fahrenheit|km|m|"
               r"கிலோகிராம்|கிலோ|கிராம்|சென்டிமீட்டர்|மீட்டர்|கி\.மீ|லிட்டர்|மில்லி|"
               r"அடி|அங்குலம்|மைல்|பவுண்ட்|பவுண்டு|அவுன்ஸ்|டன்|செல்சியஸ்|பாரன்ஹீட்)")


def unit_convert(value: float, src: str, dst: str) -> Dict[str, Any]:
    s, d = str(src).lower().strip(), str(dst).lower().strip()
    if s in _TEMP and d in _TEMP:
        c = (value - 32) * 5 / 9 if s in ("f", "fahrenheit", "பாரன்ஹீட்") else value
        out = c * 9 / 5 + 32 if d in ("f", "fahrenheit", "பாரன்ஹீட்") else c
        return {"ok": True, "kind": "temperature", "result": _round(out),
                "steps": [f"{value:g} {src} -> {dst}"],
                "answer": f"{value:g} {src} = {out:.2f} {dst}"}
    for table, label in ((_LEN, "length"), (_MASS, "mass"), (_VOL, "volume")):
        if s in table and d in table:
            out = value * table[s] / table[d]
            return {"ok": True, "kind": f"unit_{label}", "result": _round(out),
                    "steps": [f"{value:g} {src} x {table[s]:g} / {table[d]:g}"],
                    "answer": f"{value:g} {src} = {out:,.4g} {dst}"}
    return {"ok": False, "error": f"cannot convert {src} to {dst}",
            "known": sorted(set(_LEN) | set(_MASS) | set(_VOL) | _TEMP)[:24]}


# --------------------------------------------------------------------------- #
# one entry point
# --------------------------------------------------------------------------- #
_GST_WORDS = re.compile(r"\bgst\b|ஜிஎஸ்டி", re.I)
_DISCOUNT_WORDS = re.compile(r"\bdiscount\b|தள்ளுபடி|கழிவு", re.I)
_INTEREST_WORDS = re.compile(r"\binterest\b|வட்டி", re.I)
_UNIT_WORDS = re.compile(r"\b(convert|in|to|into)\b|மாற்று|எத்தனை|எவ்வளவு", re.I)
_COMPOUND_WORDS = re.compile(r"\bcompound\b|கூட்டு", re.I)


_PERCENT_PHRASE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|percent|per\s*cent|சதவீதம்|சதவிகிதம்)|"
    r"(?:percent|per\s*cent|சதவீதம்|சதவிகிதம்)\s*\d+(?:\.\d+)?", re.I)


def _without_percent(text: str) -> str:
    """The question with its "18%" removed, so 18 is never read as the amount."""
    return " ".join(_PERCENT_PHRASE.sub(" ", str(text or "")).split())


def solve(question: str) -> Optional[Dict[str, Any]]:
    """Answer a money/measurement question, or None when it is not one of ours."""
    q = " ".join(str(question or "").split())
    if not q:
        return None
    low = q.lower()

    # ---- unit conversion: "5 feet in cm", "10 கிலோ எத்தனை பவுண்ட்" -------
    units = re.findall(_UNIT_TOKEN, low)
    if len(units) >= 2 and _UNIT_WORDS.search(low):
        found = first_amount(_without_percent(q))
        if found:
            res = unit_convert(found[0], units[0], units[1])
            if res.get("ok"):
                return res

    pct = _pct(low)
    base_q = _without_percent(q)
    # ---- GST ---------------------------------------------------------- #
    if _GST_WORDS.search(low):
        amount = first_amount(base_q)
        rate = pct if pct is not None else 18.0     # 18% is the standard slab
        if amount:
            inclusive = re.search(r"\b(inclusive|included|inside|with gst|உட்பட)\b", low)
            return remove_gst(amount[0], rate) if inclusive else add_gst(amount[0], rate)

    # ---- interest ----------------------------------------------------- #
    if _INTEREST_WORDS.search(low):
        nums = [v for v, _ in _all_amounts(q)]
        ye = re.search(r"(\d+(?:\.\d+)?)\s*(?:years?|yrs?|வருடம்|ஆண்டு)", low)
        if len(nums) >= 2:
            principal, rate = nums[0], nums[1] if nums[1] <= 100 else pct or 0
            years = float(ye.group(1)) if ye else 1.0
            if rate:
                return interest(principal, rate, years,
                                compound=bool(_COMPOUND_WORDS.search(low)))

    # ---- discount ----------------------------------------------------- #
    if _DISCOUNT_WORDS.search(low) and pct is not None:
        amount = first_amount(base_q)
        if amount:
            return discount(amount[0], pct)

    # ---- "18% of 1250", "1250-ல 18 சதவீதம்" ---------------------------- #
    if pct is not None:
        amount = first_amount(base_q)
        if amount:
            return percent_of(amount[0], pct)
    return None


def _all_amounts(text: str) -> List[Tuple[float, str]]:
    out: List[Tuple[float, str]] = []
    for m in _AMOUNT_RE.finditer(str(text or "")):
        raw = (m.group(1) or "") + (" " + m.group(2) if m.group(2) else "")
        value = _to_number(raw)
        if value is not None:
            out.append((value, m.group(0).strip()))
    return out


def help_text() -> str:
    return ("I can work out: `18% of 1250` / `1250-ல 18 சதவீதம்` / `GST on 2499` / "
            "`GST inside 2950` / `10% discount on 899` / `simple interest on 50000 at 8% "
            "for 3 years` / `5 feet in cm` / `2 kg in lb`. Plain arithmetic still works too.")
