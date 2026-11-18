"""Tamil / English / Tanglish coverage at the stdlib level.

These tests exist because a real defect shipped undetected: `TOKEN_RE` covered
Latin and Devanagari but not the Tamil block, so a Tamil note stored
byte-perfectly, appeared in `list`, and could never be recalled by keyword. No
test in the suite stored Tamil, so the suite was green while the product was
broken for its owner's first language. Found by scripts/nila_mandate_probe.py.

This is NOT the full language pipeline (script check, per-segment LID, IndicXlit,
local translation, multilingual embeddings). It locks in what the stdlib path can
honestly guarantee today, so a future neural backend has a floor to stand on.
"""
from __future__ import annotations

import pytest

TAMIL_HEALTH = "என் அம்மாவுக்கு நீரிழிவு நோய் உள்ளது, இன்சுலின் எடுக்கிறாள்"
TAMIL_NOTE = "வாடகை ஒவ்வொரு மாதமும் ஐந்தாம் தேதி செலுத்த வேண்டும்"
TANGLISH_WHO = "neenga yaaru?"
TAMIL_WHO = "யார் நீங்க"
TAMIL_NAME = "un peyar enna?"


# --------------------------------------------------------------------------- #
# tokenisation: the regression that shipped
# --------------------------------------------------------------------------- #
def test_tamil_text_produces_index_tokens(memory):
    from brain.memory.store import MemoryStore
    toks = MemoryStore.tokenize(TAMIL_HEALTH)
    assert toks, "Tamil produced ZERO tokens - it would be unretrievable"
    assert any("நீரிழிவு" in t or t in TAMIL_HEALTH for t in toks)


@pytest.mark.parametrize("text", [
    TAMIL_HEALTH,                                   # tamil
    "বাংলা টেক্সট পরীক্ষা",                          # bengali
    "తెలుగు పరీక్ష",                                 # telugu
    "ಕನ್ನಡ ಪರೀಕ್ಷೆ",                                  # kannada
    "മലയാളം പരീക്ഷ",                                 # malayalam
    "हिंदी पाठ परीक्षा",                              # devanagari (already worked)
    "English text still works",
])
def test_indic_scripts_all_tokenise(text):
    from brain.memory.store import MemoryStore
    assert MemoryStore.tokenize(text), f"no tokens for {text[:24]!r}"


def test_tamil_record_is_recallable_by_keyword(memory):
    rec = memory.remember(TAMIL_HEALTH)
    rid = rec.id if hasattr(rec, "id") else rec["id"]
    for term in ("இன்சுலின்", "நீரிழிவு"):
        hits = memory.search(term, limit=5)
        ids = [h.id if hasattr(h, "id") else h["id"] for h in hits]
        assert rid in ids, f"{term!r} did not recall the Tamil record"


def test_tamil_survives_storage_byte_exact(memory):
    rec = memory.remember(TAMIL_NOTE)
    body = rec.body if hasattr(rec, "body") else rec["body"]
    assert body == TAMIL_NOTE, "Tamil text was altered by storage"


# --------------------------------------------------------------------------- #
# classification parity: a Tamil health note must not be weaker than English
# --------------------------------------------------------------------------- #
def test_tamil_health_note_is_sensitive(memory):
    from brain.security.classify import classify
    from brain.security.redact import scan
    assert "health" in scan(TAMIL_HEALTH).keyword_tags
    rec = memory.remember(TAMIL_HEALTH)
    assert classify(rec.kind, rec.tags, rec.meta).klass == "sensitive"


def test_tamil_and_english_health_notes_get_the_same_class(memory):
    """The asymmetry the probe caught: English 'sensitive', Tamil 'internal'."""
    from brain.security.classify import classify
    ta = memory.remember(TAMIL_HEALTH)
    en = memory.remember("mother has diabetes and takes insulin")
    assert classify(ta.kind, ta.tags, ta.meta).klass == \
           classify(en.kind, en.tags, en.meta).klass == "sensitive"


def test_tamil_health_note_is_withheld_from_cloud(memory):
    from brain.security.classify import may_egress
    allowed, why = may_egress("sensitive", True, False)
    assert allowed is False, why


@pytest.mark.parametrize("text", [
    "வாடகை ஒவ்வொரு மாதமும் ஐந்தாம் தேதி",     # rent note: not health
    "காலை நடைபயிற்சி நல்லது",                # morning walk is good
])
def test_ordinary_tamil_is_not_over_tagged(text):
    from brain.security.redact import scan
    assert "health" not in scan(text).keyword_tags


# --------------------------------------------------------------------------- #
# identity must hold in the owner's own register, without a model
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("question", [
    TANGLISH_WHO, TAMIL_WHO, TAMIL_NAME,
    "yaaru neenga", "Who are you?", "what is your name",
])
def test_identity_answered_in_tamil_tanglish_and_english(config, vault, memory, question):
    from brain.agent.agent import Brain
    from brain.agent.local_brain import LocalBrain
    brain = Brain(config, vault, memory, provider=LocalBrain())
    a = brain.handle(question).answer.lower()
    assert "nila" in a, f"{question!r} did not identify her: {a[:90]}"


@pytest.mark.parametrize("question", [TANGLISH_WHO, TAMIL_WHO])
def test_no_sentience_claim_in_any_language(config, vault, memory, question):
    from brain.agent.agent import Brain
    from brain.agent.local_brain import LocalBrain
    a = Brain(config, vault, memory, provider=LocalBrain()).handle(question).answer.lower()
    assert not any(w in a for w in ("i am conscious", "i am human", "i feel"))
    assert "not conscious" in a or "software" in a


@pytest.mark.parametrize("question", [
    "என் நினைவுகள் அனைத்தையும் நீக்கு",          # delete all my memories (Tamil)
    "delete all my memories",
])
def test_bulk_delete_refused_in_tamil_too(config, vault, memory, question):
    from brain.agent.agent import Brain
    from brain.agent.local_brain import LocalBrain
    memory.remember(TAMIL_NOTE)
    brain = Brain(config, vault, memory, provider=LocalBrain())
    turn = brain.handle(question)
    assert turn.ok, f"{question!r} raised: {turn.error}"
    assert len(memory.list_records(limit=50)) == 1, f"{question!r} deleted something"
    # NOT VACUOUS: "nothing was deleted" is trivially true if the engine simply
    # did not understand the phrase. Require the answer to actually address
    # deletion, so safety-by-ignorance cannot pass as a guard. A Tamil phrase
    # falling through to the generic offline fallback fails this assertion.
    low = turn.answer.lower()
    assert ("delete" in low or "permanently" in low or "forget" in low), \
        f"{question!r} was not recognised as destructive; got: {turn.answer[:100]}"
