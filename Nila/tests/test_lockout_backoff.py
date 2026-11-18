"""Lockout backoff must actually escalate (regression: count was reset to 0 at the
trigger, which made the exponent always 0 and every lockout a flat 30 s)."""
from __future__ import annotations

from brain.config import LOCKOUT_BASE_SECONDS, LOCKOUT_MAX_ATTEMPTS


def _sequence(vault, n):
    return [vault.register_failure("regression-probe") for _ in range(n)]


def test_no_lockout_before_the_attempt_limit(vault):
    waits = _sequence(vault, LOCKOUT_MAX_ATTEMPTS - 1)
    assert waits == [0] * (LOCKOUT_MAX_ATTEMPTS - 1)
    assert vault.locked_out() == (False, 0)


def test_backoff_escalates_on_every_subsequent_failure(vault):
    waits = _sequence(vault, 12)
    base = LOCKOUT_BASE_SECONDS
    assert waits[:LOCKOUT_MAX_ATTEMPTS - 1] == [0] * (LOCKOUT_MAX_ATTEMPTS - 1)
    assert waits[4] == base            # 5th failure  -> 30 s
    assert waits[5] == base * 2        # 6th          -> 60 s   (was 30 before the fix)
    assert waits[6] == base * 4        # 7th          -> 120 s
    assert waits[7] == base * 8        # 8th          -> 240 s
    assert waits[8] == base * 16       # 9th          -> 480 s
    assert waits[9] == base * 32       # 10th         -> 960 s
    assert waits[10] == base * 64      # 11th         -> 1920 s (2**6 cap)


def test_backoff_is_capped_and_does_not_grow_without_bound(vault):
    waits = _sequence(vault, 18)
    assert waits[10] == waits[11] == waits[17] == LOCKOUT_BASE_SECONDS * 64
    assert max(waits) == LOCKOUT_BASE_SECONDS * 64


def test_lockout_is_reported_with_a_remaining_wait(vault):
    _sequence(vault, LOCKOUT_MAX_ATTEMPTS)
    locked, retry_in_s = vault.locked_out()
    assert locked is True
    assert 0 < retry_in_s <= LOCKOUT_BASE_SECONDS


def test_success_clears_the_counter_and_the_lockout(vault):
    _sequence(vault, 8)
    vault.register_success()
    att = vault._attempts()
    assert att["count"] == 0 and att["locked_until"] == 0
    assert vault.locked_out() == (False, 0)
    assert vault.register_failure("after-success") == 0


def test_every_failure_is_audited_with_its_lockout_length(vault):
    _sequence(vault, 6)
    logged = [e for e in vault.read_audit(50) if e.get("event") == "auth_failure"]
    seconds = [e.get("data", {}).get("lockout_seconds") for e in logged]
    assert seconds[-2:] == [LOCKOUT_BASE_SECONDS, LOCKOUT_BASE_SECONDS * 2]
