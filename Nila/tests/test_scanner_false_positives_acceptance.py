"""Acceptance tests: the privacy scanner must be PRECISE.

Too loose  -> private data reaches the cloud.
Too broad  -> ordinary business questions are refused and project notes are
              wrongly treated as restricted, which makes Nila unusable.

Run:  python -m pytest tests/test_scanner_false_positives_acceptance.py -q
"""
import pytest

from brain.security.redact import scan

# Ordinary, impersonal questions. None of these may be flagged.
GENERIC = [
    "give me three names for a tea shop",
    "suggest a name for a seasonal coffee menu for this season",
    "how do I improve customer relationship management for my cafe",
    "I need a business partner for my tea shop, what should I look for",
    "what is a good investment strategy for growing a small business",
    "write a pitch about our income model and pricing",
    "recommend a thai restaurant style menu",
    "explain what a loan is",
    "how do I write a persuasive reason for the person reading my ad",
    "what is the capital of France",
    "list five low-cost ways to market a small tea shop",
    "explain what a cash flow statement is",
]

# Personal statements. Every one must be flagged (sensitive or restricted).
PERSONAL = [
    "my card number is 4111 1111 1111 1111",
    "my aadhaar is 2345 6789 0123",
    "my password = hunter2hunter2",
    "I was diagnosed with diabetes and take insulin",
    "எனக்கு சர்க்கரை நோய் உள்ளது",
    "my salary is low and my loan debt is large",
    "என் சம்பளம் குறைவு, கடன் அதிகம்",
    "en sambalam kammi, kadan athigam",
    "my wife and I are fighting and her mother is sick",
    "என் மனைவிக்கும் எனக்கும் சண்டை",
    "en manaivi kooda sandai, amma udambu sari illa",
    "I owe the bank a lot of money",
    "my son is not well",
]


@pytest.mark.parametrize("text", GENERIC)
def test_generic_business_question_is_not_flagged(text):
    assert scan(text).max_class is None, f"false positive: {text!r} -> {scan(text).max_class}"


@pytest.mark.parametrize("text", PERSONAL)
def test_personal_statement_is_flagged(text):
    assert scan(text).max_class in ("sensitive", "restricted"), f"missed: {text!r}"
