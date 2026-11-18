"""Regression tests for private facts the scanner must not miss.

These are additive tests. The independent scanner and online-isolation
acceptance files remain unchanged.
"""
import pytest

from brain.security.redact import scan


PERSONAL_CASES = [
    ("My Indian mobile number is 9876543210", "phone_in", "sensitive", None),
    ("My mobile number is 9845123789", "phone_in", "sensitive", None),
    ("My PAN is AABCK1234D", "pan", "restricted", None),
    ("I have a loan of 5 lakhs", None, "restricted", "finance"),
    ("I am in debt and cannot make the monthly payment", None, "restricted", "finance"),
    ("என் அப்பாவுக்கு உடல்நிலை சரியில்லை", None, "sensitive", "health"),
    ("எனக்கு கடன் தொல்லை அதிகம்", None, "restricted", "finance"),
    ("enakku kadan athigam aayiduchu", None, "restricted", "finance"),
]


@pytest.mark.parametrize("text,finding,expected_class,tag", PERSONAL_CASES)
def test_personal_patterns_are_detected(text, finding, expected_class, tag):
    result = scan(text)
    assert result.max_class == expected_class, f"missed personal content: {text!r}"
    if finding:
        assert finding in result.names, f"missing {finding} finding: {text!r}"
    if tag:
        assert tag in result.keyword_tags, f"missing {tag} tag: {text!r}"


GENERIC_BUSINESS = [
    "What is a good investment strategy for growing a small business?",
    "Write a pitch about our income model and pricing.",
    "Explain what a loan is.",
    "I need a business partner for my tea shop, what should I look for?",
    "How do I improve customer relationship management for my cafe?",
    "Suggest a seasonal coffee menu for this season.",
    "Recommend a Thai restaurant-style menu.",
    "How do I write a persuasive reason for the person reading my ad?",
    "Compare business loans for a fictional startup.",
    "Build an income statement template for a retail shop.",
    "Explain the difference between revenue, profit, and cash flow.",
    "Draft an investment pitch for a public company.",
    "Create a partnership agreement checklist for a small business.",
    "List low-cost marketing ideas for a neighborhood bakery.",
    "Estimate inventory needs for a seasonal tea menu.",
    "Explain customer relationships in a CRM plan.",
    "Suggest pricing tiers for a subscription coffee service.",
    "Write a business plan for a Thai-inspired cafe.",
    "How can a person validate a startup idea?",
    "Create a loan repayment calculator for a fictional borrower.",
]


@pytest.mark.parametrize("text", GENERIC_BUSINESS)
def test_generic_business_language_is_not_flagged(text):
    assert scan(text).max_class is None, f"false positive: {text!r} -> {scan(text).max_class}"


@pytest.mark.parametrize("text", [
    "The sample sequence 9876543210 appears in this calculation.",
    "Repeated digits 6666666666 are placeholders, not a phone.",
])
def test_phone_like_digits_need_context_or_realistic_digits(text):
    assert scan(text).max_class is None, f"false positive phone finding: {text!r}"


def test_pan_holder_status_is_validated_at_fourth_character():
    assert "pan" in scan("PAN AABCK1234D").names
    assert "pan" not in scan("PAN AABDK1234D").names
