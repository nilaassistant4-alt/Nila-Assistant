"""Acceptance tests: the calculator cannot hang, and a paper code opens the vault.

Two defects found by an external audit, plus one found while fixing them.

**BUG-1.** ``calculate`` put a character-filtered string into ``eval``. ``9**9**9**9``
passes the filter and never finishes; the filter also allowed ``e``, so
``9e999999999`` builds an integer with ~10^9 digits and kills the process on
formatting. `brain/util/calc.py` now parses the same expressions a person writes and
refuses the ones that would not finish — cheaply, in microseconds.

**The recovery-code defect.** The codes were printed "in case you forget the
passphrase" and could not do that: the master key was wrapped by the passphrase
alone, and ``open_vault`` asked for the passphrase *before* looking at a code. The
person the codes exist for was locked out holding them, and the only advice was
"rename the vault folder". A code now unwraps the master key itself.

Run:  python -m pytest tests/test_calc_and_recovery_acceptance.py -q
"""
from __future__ import annotations

import time

import pytest

from brain.util.calc import CalcError, safe_eval


# --------------------------------------------------------------------------- #
# 1. ordinary arithmetic still works, and is exact
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("expr,expected", [
    ("2+3*4", 14),
    ("(2+3)*4", 20),
    ("10/4", 2.5),
    ("7//2", 3),
    ("7%3", 1),
    ("-5+2", -3),
    ("2**10", 1024),
    ("(1+2)**3", 27),
    ("2.5*4", 10.0),
    ("9e3", 9000.0),
    ("1.5e-3", 0.0015),
    ("((2+3)*(4-1))/2", 7.5),
])
def test_a_normal_sum_is_still_exact(expr, expected):
    assert safe_eval(expr) == expected


def test_a_big_but_honest_number_is_answered():
    assert safe_eval("10**100") == 10 ** 100


# --------------------------------------------------------------------------- #
# 2. the two ways to hang it, refused in well under a millisecond
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("expr", [
    "9**9**9**9",        # the audit's case: a tower of exponents
    "2**3**4",           # the same shape, smaller
    "2**99999999",       # one huge exponent
    "(10**100)**100",    # grows past the digit ceiling
    "10**500*10**500",   # multiply two large numbers
    "9e999999999",       # the filter allowed 'e': a billion-digit integer
    "10**10000",
])
def test_a_expression_that_would_never_finish_is_refused_fast(expr):
    started = time.monotonic()
    with pytest.raises(CalcError):
        safe_eval(expr)
    assert time.monotonic() - started < 0.5, f"{expr} took too long to refuse"


@pytest.mark.parametrize("expr", [
    "__import__('os')",
    "open('/etc/passwd')",
    "9**9**9**9 if True else 1",
    "[1,2,3]",
    "'a'*999",
    "",
])
def test_anything_that_is_not_arithmetic_is_refused(expr):
    with pytest.raises(CalcError):
        safe_eval(expr)


def test_division_by_zero_is_said_not_raised_as_a_traceback():
    with pytest.raises(CalcError, match="division by zero"):
        safe_eval("2/0")


# --------------------------------------------------------------------------- #
# 3. the tool the model actually calls
# --------------------------------------------------------------------------- #
def test_the_calculate_tool_refuses_the_audit_case(vault, memory, config):
    from brain.agent.agent import Brain

    brain = Brain(config, vault, memory)
    ok = brain.registry.call("calculate", {"expression": "2+3"}, brain.tool_ctx)
    assert ok["ok"] is True and ok["result"] == 5
    # A refused sum comes back as a ToolError with a readable sentence (that is how
    # every other refusal in this registry behaves), not as a hung process.
    from brain.agent.tools import ToolError

    started = time.monotonic()
    with pytest.raises(ToolError) as caught:
        brain.registry.call("calculate", {"expression": "9**9**9**9"}, brain.tool_ctx)
    assert time.monotonic() - started < 0.5
    assert "power" in str(caught.value).lower() or "refus" in str(caught.value).lower()


# --------------------------------------------------------------------------- #
# 4. a recovery code opens the vault on its own
# --------------------------------------------------------------------------- #
@pytest.fixture()
def codes_vault(tmp_path):
    from brain.config import Config
    from brain.identity.ceremony import IdentityCeremony
    from brain.memory.vault import Vault

    config = Config.load(tmp_path / "vault")
    config.fast_kdf = True
    vault = Vault(config)
    vault.initialize("the-passphrase-i-forgot", "Owner", "dev-1")
    codes = IdentityCeremony(config, vault).enroll_recovery_codes(4)
    vault.lock()
    return config, codes


def test_a_paper_code_opens_the_vault_with_no_passphrase(codes_vault):
    """The whole point of printing them."""
    from brain.memory.vault import Vault

    config, codes = codes_vault
    vault = Vault(config)
    session = vault.unlock_with_recovery_code(codes[0])
    assert session.mode == "recovery"
    assert session.degraded is True, "a code is break-glass, not a normal unlock"
    assert session.mk is not None


def test_a_code_works_once_and_then_is_dead(codes_vault):
    from brain.memory.vault import Vault

    config, codes = codes_vault
    Vault(config).unlock_with_recovery_code(codes[0])
    again = Vault(config)
    with pytest.raises(PermissionError, match="already been used|not valid"):
        again.unlock_with_recovery_code(codes[0])
    # the others are untouched
    assert Vault(config).unlock_with_recovery_code(codes[1]).mode == "recovery"


def test_a_wrong_code_is_refused_and_does_not_burn_a_good_one(codes_vault):
    from brain.memory.vault import Vault

    config, codes = codes_vault
    with pytest.raises(PermissionError):
        Vault(config).unlock_with_recovery_code("zzzzz-zzzzz")
    vault = Vault(config)
    assert vault.recovery_remaining() == 4
    assert vault.unlock_with_recovery_code(codes[3]).mode == "recovery"


def test_the_passphrase_still_works_after_a_code_was_used(codes_vault):
    """A code is an ALTERNATIVE door, not a change of lock - so an owner who finds the
    paper with the passphrase on it later is not locked out of what that code spent."""
    from brain.memory.vault import Vault

    config, codes = codes_vault
    Vault(config).unlock_with_recovery_code(codes[0])
    assert Vault(config).verify_passphrase("the-passphrase-i-forgot") is True


def test_the_cli_opens_on_a_code_before_it_ever_asks_for_a_passphrase(codes_vault, monkeypatch):
    """The regression that made recovery useless: the passphrase prompt came first."""
    from brain.cli import main as cli

    config, codes = codes_vault
    import io
    import sys

    def must_not_prompt(*a, **k):
        raise AssertionError("the passphrase was asked for before the recovery code "
                             "was tried - that is the bug this test exists for")

    monkeypatch.setattr(cli, "read_passphrase", must_not_prompt)
    parser = cli.build_parser()
    monkeypatch.setattr(sys, "stdin", io.StringIO(codes[0] + "\n"))
    args = parser.parse_args(["--home", str(config.home), "status", "--json",
                              "--recovery-stdin"])
    _, vault = cli.open_vault(args)
    assert vault.session.mode == "recovery"
    assert "recovery code" in cli.take_recovery_warning()


def test_reissue_turns_old_style_codes_into_vault_opening_ones(codes_vault):
    """A vault made by an older build has hashes but no wraps. `reissue` is the one
    action that makes its codes able to open the vault again."""
    from brain.identity.ceremony import IdentityCeremony
    from brain.memory.vault import Vault

    config, codes = codes_vault
    vault = Vault(config)
    vault.unlock_with_recovery_code(codes[0])
    fresh = IdentityCeremony(config, vault).enroll_recovery_codes(3)
    vault.lock()
    assert Vault(config).unlock_with_recovery_code(fresh[0]).mode == "recovery"
