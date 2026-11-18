"""Lock must invalidate every open memory handle before another phase proceeds."""
from __future__ import annotations

import pytest

from brain.api.gateway import Gateway
from brain.identity.ceremony import IdentityCeremony
from brain.identity.totp import totp
from brain.memory.store import MemoryStore
from tests.conftest import PASSPHRASE


def test_lock_zeroizes_the_session_key_object(config, vault, memory):
    memory.remember("lock-key-canary-581f", title="lock canary")
    session = vault.session
    key_object = session.mk
    assert any(key_object), "test requires a non-zero unlocked key"

    vault.lock()

    assert not any(key_object), "vault.lock() must overwrite the shared key buffer"


def test_lock_makes_an_already_open_memory_store_unusable(vault, memory):
    marker = "LOCKED_STORE_MUST_NOT_RECALL_6c8d"
    memory.remember(marker)
    assert any(marker in row.body for row in memory.search(marker))

    vault.lock()

    with pytest.raises(RuntimeError, match="(?i)locked|closed|invalid"):
        memory.search(marker)
    with pytest.raises(RuntimeError, match="(?i)locked|closed|invalid"):
        memory.list_records()


def test_reunlock_requires_a_fresh_store_and_does_not_revive_the_old_one(
        config, vault, memory):
    marker = "FRESH_STORE_AFTER_REAUTH_42b7"
    rec = memory.remember(marker)
    stale_store = memory
    vault.lock()

    mk = vault.unlock(PASSPHRASE)
    vault.start_session(mk, mode="passphrase", degraded=True)
    fresh_store = MemoryStore(config, vault.session.mk)
    try:
        loaded = fresh_store.get(rec.id)
        assert loaded is not None and marker in loaded.body
        with pytest.raises(RuntimeError, match="(?i)locked|closed|invalid"):
            stale_store.search(marker)
    finally:
        fresh_store.close()


def test_api_totp_reauth_reopens_fresh_store_at_the_correct_tier(config, vault, memory):
    marker = "API_REAUTH_TOTP_FRESH_STORE_971a"
    memory.remember(marker)
    ceremony = IdentityCeremony(config, vault)
    enrolled = ceremony.enroll_totp()
    gateway = Gateway(config, vault, memory)
    stale_store = memory

    status, locked = gateway.route("POST", "/v1/lock", {}, {})
    assert status == 200 and locked["locked"]
    with pytest.raises(RuntimeError, match="(?i)locked|closed|invalid"):
        stale_store.search(marker)

    status, result = gateway.route(
        "POST", "/v1/auth/verify", {},
        {"passphrase": PASSPHRASE, "totp": totp(enrolled["secret"])},
    )
    assert status == 200 and result["ok"]
    assert result["degraded"] is False, "TOTP is sufficient when no voice factor exists"
    assert gateway.memory is not stale_store
    assert gateway.memory.level == "full"
    assert any(marker in row.body for row in gateway.memory.search(marker))
    with pytest.raises(RuntimeError, match="(?i)locked|closed|invalid"):
        stale_store.search(marker)
