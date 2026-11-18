"""Session expiry must be exact, non-resurrectable, and tied to user activity."""
from __future__ import annotations

import json
import time

from brain.api.gateway import Gateway
from brain.agent.agent import Brain
from brain.agent.providers import Completion
from brain.config import Config, SESSION_ABSOLUTE_TIMEOUT_SECONDS, SESSION_TIMEOUT_SECONDS
from brain.memory.vault import Session
from brain.util.misc import now_ms


def test_session_expires_at_the_exact_idle_or_absolute_deadline():
    idle_limited = Session(mk=b"k" * 32, session_id="s", started_at=0, expires_at=100)
    assert not idle_limited.expired(now=99)
    assert idle_limited.expired(now=100), "expires_at is an exclusive upper bound"

    absolute_limited = Session(mk=b"k" * 32, session_id="s", started_at=0,
                               expires_at=200, absolute_expires_at=100)
    assert not absolute_limited.expired(now=99)
    assert absolute_limited.expired(now=100), "absolute maximum is also exclusive"


def test_monotonic_deadline_cannot_be_extended_by_wall_clock_rollback(monkeypatch):
    session = Session(mk=b"k" * 32, session_id="s", started_at=0, expires_at=10_000,
                      absolute_expires_at=20_000, expires_at_monotonic=30.0,
                      absolute_expires_at_monotonic=20.0)
    monkeypatch.setattr("brain.memory.vault.now_ms", lambda: 0)
    monkeypatch.setattr("brain.memory.vault.time.monotonic", lambda: 20.0)
    assert session.expired(), "absolute monotonic deadline must win if wall time moves back"


def test_wall_clock_rollback_still_locks_vault_and_store(vault, memory, monkeypatch):
    session = vault.session
    key = session.mk
    wall_now = now_ms()
    mono_now = time.monotonic()
    session.expires_at = wall_now + 60_000
    session.absolute_expires_at = wall_now + 120_000
    session.expires_at_monotonic = mono_now - 1
    session.absolute_expires_at_monotonic = mono_now + 120
    monkeypatch.setattr("brain.memory.vault.now_ms", lambda: wall_now - 10_000)

    assert vault.is_unlocked() is False
    assert vault._session is None
    assert not any(key)
    assert memory._closed


def test_v02_session_timeout_defaults_and_config_round_trip(tmp_path):
    config = Config.load(tmp_path)
    # The owner's v0.2 rule: 60 minutes idle. 2 h was an interim default.
    assert config.session_timeout == SESSION_TIMEOUT_SECONDS == 60 * 60
    assert config.session_absolute_timeout == SESSION_ABSOLUTE_TIMEOUT_SECONDS == 12 * 60 * 60

    config.session_timeout = 90
    config.session_absolute_timeout = 600
    config.save()
    loaded = Config.load(tmp_path)
    assert loaded.session_timeout == 90
    assert loaded.session_absolute_timeout == 600


def test_legacy_default_migrates_but_custom_idle_timeout_is_preserved(tmp_path):
    config = Config.load(tmp_path)
    config.config_path.write_text(
        '{"config_version": 1, "session_timeout": 900}', encoding="utf-8")
    migrated = Config.load(tmp_path)
    assert migrated.session_timeout == SESSION_TIMEOUT_SECONDS
    assert migrated.session_absolute_timeout == SESSION_ABSOLUTE_TIMEOUT_SECONDS

    config.config_path.write_text(
        '{"config_version": 1, "session_timeout": 600}', encoding="utf-8")
    custom = Config.load(tmp_path)
    assert custom.session_timeout == 600

    # The interim 2 h default is also a retired BUILT-IN value, so it migrates
    # down to the confirmed 60 minutes rather than pinning the old behaviour.
    config.config_path.write_text(
        '{"config_version": 1, "session_timeout": 7200}', encoding="utf-8")
    assert Config.load(tmp_path).session_timeout == SESSION_TIMEOUT_SECONDS


def test_touch_does_not_resurrect_an_expired_session(vault, memory):
    session = vault.session
    key = session.mk
    session.expires_at = now_ms() - 1

    touched = vault.touch_session()

    assert touched is False
    assert vault._session is None
    assert not any(key), "expired key must be zeroized"
    assert memory._closed, "expiry must invalidate open memory handles"


def test_idle_touch_is_capped_by_the_absolute_maximum(config, vault, monkeypatch):
    config.session_timeout = 60
    config.session_absolute_timeout = 120
    vault.lock()
    session = vault.start_session(vault.unlock("test-passphrase-42"))
    key = session.mk
    started = session.started_at
    assert session.expires_at == started + 60_000
    assert session.absolute_expires_at == started + 120_000

    now = [started + 50_000]
    monkeypatch.setattr("brain.memory.vault.now_ms", lambda: now[0])
    assert vault.touch_session()
    assert session.expires_at == started + 110_000

    now[0] = started + 100_000
    assert vault.touch_session()
    assert session.expires_at == session.absolute_expires_at

    now[0] = session.absolute_expires_at
    assert not vault.touch_session(), "activity cannot move the hard deadline"
    assert vault._session is None
    assert not any(key)


def test_reconfiguring_the_same_key_cannot_reset_absolute_lifetime(vault):
    session = vault.session
    key = session.mk
    started = session.started_at
    absolute_deadline = session.absolute_expires_at
    sid = session.session_id

    same_session = vault.start_session(key, mode="passphrase", degraded=True)

    assert same_session is session
    assert same_session.session_id == sid
    assert same_session.started_at == started
    assert same_session.absolute_expires_at == absolute_deadline
    assert same_session.degraded is True


def test_provisional_authentication_is_not_an_unlocked_session(vault, brain):
    vault.lock()
    provisional_key = vault.unlock("test-passphrase-42")
    vault.begin_provisional(provisional_key)

    assert vault.session is None
    assert vault.is_unlocked() is False
    result = brain.handle("do not serve during second-factor verification")
    assert result.ok is False

    vault.discard_provisional()


def test_provisional_authentication_zeroizes_at_its_short_deadline(vault, monkeypatch):
    vault.lock()
    provisional = vault.begin_provisional(vault.unlock("test-passphrase-42"))
    key = provisional.mk
    monkeypatch.setattr("brain.memory.vault.now_ms", lambda: provisional.expires_at)

    assert vault.session is None
    assert vault._session is None
    assert not any(key)


def test_brain_slash_commands_refresh_idle_expiry(config, vault, brain, monkeypatch):
    fake_now = now_ms() + 1_000
    session = vault.session
    session.expires_at = fake_now + 1_000
    monkeypatch.setattr("brain.memory.vault.now_ms", lambda: fake_now)

    result = brain.handle("/help")

    assert result.ok
    assert session.expires_at == fake_now + config.session_timeout * 1_000


def test_expiry_during_a_long_turn_does_not_revive_or_return_answer(config, vault, memory):
    class ExpiringProvider:
        name = "expiring-test"
        needs_network = False

        def available(self):
            return True

        def complete(self, system, messages, tools=None, tools_mode="auto"):
            vault._session.expires_at = now_ms() - 1
            return Completion(text="must not be returned after expiry")

    agent = Brain(config, vault, memory, provider=ExpiringProvider())
    turn = agent.handle("test expiry after provider finishes")

    assert not turn.ok
    assert "expired" in turn.error.lower()
    assert "must not be returned" not in turn.answer
    assert vault._session is None


def test_api_user_memory_activity_touches_but_dashboard_polling_does_not(
        config, vault, memory, monkeypatch):
    gateway = Gateway(config, vault, memory)
    fake_now = now_ms() + 1_000
    session = vault.session
    session.expires_at = fake_now + 1_000
    fake_mono = time.monotonic()
    monkeypatch.setattr("brain.memory.vault.now_ms", lambda: fake_now)
    monkeypatch.setattr("brain.api.gateway.now_ms", lambda: fake_now)
    monkeypatch.setattr("brain.memory.vault.time.monotonic", lambda: fake_mono)

    status, payload = gateway.route("GET", "/v1/status", {}, {})
    assert status == 200
    assert session.expires_at == fake_now + 1_000, "status polling is not user activity"
    assert payload["session"]["absolute_expires_at"] == session.absolute_expires_at
    assert payload["session"]["absolute_expires_in_s"] == int(
        session.absolute_expires_at_monotonic - fake_mono)

    status, _ = gateway.route("GET", "/v1/memory/search", {"q": "nothing"}, {})
    assert status == 200
    assert session.expires_at == fake_now + config.session_timeout * 1_000


# --------------------------------------------------------------------------- #
# the v0.2 idle rule: 30 minutes warn, 60 minutes lock
# --------------------------------------------------------------------------- #
def test_the_idle_rule_is_thirty_minutes_warn_and_sixty_minutes_lock(tmp_path):
    from brain.config import WARN_AFTER_SECONDS

    config = Config.load(tmp_path)
    assert config.session_timeout == 60 * 60
    assert WARN_AFTER_SECONDS == 30 * 60
    assert config.warn_after == 30 * 60


def test_the_warning_always_lands_before_the_lock(tmp_path):
    config = Config.load(tmp_path)
    config.session_timeout = 20 * 60                # shorter than the default warn
    assert config.warn_after < config.session_timeout
    config.session_timeout = 4 * 60 * 60
    config.agent["session_warn_after"] = 1          # a nonsense value
    assert config.warn_after >= 60


def test_only_an_owner_action_moves_the_idle_window(vault, brain):
    """A heartbeat is not presence: it may extend expiry, never ``last_activity``."""
    session = vault.session
    session.last_activity = now_ms() - 40 * 60_000
    assert vault.idle_seconds() >= 40 * 60

    # what the dashboard poll does - refreshes the deadline, not "last seen"
    assert vault.touch_session() is True
    assert vault.idle_seconds() < 5

    session.last_activity = now_ms() - 40 * 60_000
    vault.note_activity()
    assert vault.idle_seconds() < 5


def test_a_message_refreshes_the_idle_window(config, vault, brain):
    vault.session.last_activity = now_ms() - 45 * 60_000
    assert vault.idle_seconds() > 30 * 60
    brain.handle("hello")
    assert vault.idle_seconds() < 5


def test_the_gateway_ui_poll_does_not_refresh_the_idle_window(config, vault, memory):
    """GET /v1/status is what the dashboard polls every 2 s. It must not extend."""
    from brain.api.gateway import Gateway

    gw = Gateway(config, vault, memory, token="t")
    vault.session.last_activity = now_ms() - 45 * 60_000
    before = vault.session.last_activity
    gw.route("GET", "/v1/status", {}, {})
    gw.route("GET", "/v1/metrics", {}, {})
    assert vault.session.last_activity == before


def test_an_idle_session_locks_and_says_why(config, vault, memory):
    from brain.api.gateway import Gateway

    gw = Gateway(config, vault, memory, token="t")
    vault.session.last_activity = now_ms() - (config.session_timeout + 5) * 1000
    gw._idle_lock_check()
    assert vault.is_unlocked() is False
    events = [json.loads(line)["event"]
              for line in config.audit_path.read_text().splitlines()]
    assert "session_idle_lock" in events


def test_the_explicit_still_here_endpoint_is_the_owner_click(config, vault, memory):
    from brain.api.gateway import Gateway

    gw = Gateway(config, vault, memory, token="t")
    vault.session.last_activity = now_ms() - 40 * 60_000
    status, body = gw.route("POST", "/v1/session/touch", {}, {})
    assert status == 200 and body["ok"] is True
    assert body["idle_s"] < 5


def test_the_status_payload_carries_the_warning(config, vault, memory):
    from brain.api.gateway import Gateway

    gw = Gateway(config, vault, memory, token="t")
    vault.session.last_activity = now_ms() - 31 * 60_000
    _, body = gw.route("GET", "/v1/status", {}, {})
    assert body["session"]["idle_warning"] is True
    assert body["session"]["warn_after_s"] == config.warn_after


def test_the_cli_session_command_reports_the_rule(tmp_path):
    from brain.cli.main import build_parser

    args = build_parser().parse_args(["session", "--touch"])
    assert args.func.__name__ == "cmd_session" and args.touch is True


def test_the_status_shape_survives_a_lock(config, vault, memory):
    """The screen must still be told the rule after the vault locked itself."""
    from brain.api.gateway import Gateway

    gw = Gateway(config, vault, memory, token="t")
    vault.lock(reason="test")
    _, body = gw.route("GET", "/v1/status", {}, {})
    assert body["unlocked"] is False
    assert body["session"]["active"] is False
    assert body["session"]["warn_after_s"] == config.warn_after
    assert body["session"]["lock_after_s"] == int(config.session_timeout)


def test_an_open_session_cannot_be_silently_replaced(config, vault, memory):
    from brain.api.gateway import Gateway, ApiError

    gw = Gateway(config, vault, memory, token="t")
    try:
        gw.route("POST", "/v1/auth/verify", {}, {"passphrase": "x"})
    except ApiError as exc:
        assert exc.status == 409            # not 401: the token must survive
    else:                                   # pragma: no cover
        raise AssertionError("replacing a live session must be refused")


def test_a_locked_vault_can_be_unlocked_again_through_the_api(config, vault, memory):
    """The dashboard's "Unlock again" path: same process, no restart.

    The test vault has no second factor enrolled, so the policy check is the
    thing being avoided here, not the unlock itself: what must hold is that a
    lock inside a live gateway can be reversed without rebuilding anything.
    """
    from brain.api.gateway import Gateway, ApiError

    gw = Gateway(config, vault, memory, token="t")
    vault.lock(reason="test")
    try:
        gw.route("POST", "/v1/auth/verify", {}, {"passphrase": "test-passphrase-42"})
    except ApiError as exc:
        # expected on a vault with no TOTP: the refusal is the second factor,
        # not "already unlocked" and not a missing route
        assert "second factor" in str(exc)
    gw.vault.begin_provisional(gw.vault.unlock("test-passphrase-42"))
    assert gw.vault.is_unlocked() is False


def test_the_deadline_that_has_just_passed_still_gets_a_checkpoint(config, vault, memory):
    """The regression that made the checkpoint dead code.

    ``Vault.session`` locks and returns None as soon as the deadline passes, so
    the old check saw "no session" and skipped the checkpoint entirely - the 423
    was the only thing that ever happened. Advance the clock past the deadline
    *without* touching the vault and assert the checkpoint really ran.
    """
    from brain.api.gateway import Gateway

    from brain.api.gateway import ApiError

    gw = Gateway(config, vault, memory, token="t")
    # 60 minutes without an owner action: the idle rule, not the absolute one
    vault.session.last_activity = now_ms() - (config.session_timeout + 5) * 1000
    try:
        gw.route("POST", "/v1/chat", {}, {"message": "hello"})
    except ApiError as exc:
        assert exc.status == 423
    else:                                   # pragma: no cover
        raise AssertionError("an idle session must not answer a chat message")

    entries = [json.loads(line) for line in config.audit_path.read_text().splitlines()]
    lock = [e for e in entries if e["event"] == "session_idle_lock"]
    assert lock, "the idle lock must be journalled, with its checkpoint"
    assert lock[-1]["data"]["checkpoint"]["memory"] == "flushed"
    assert "projects_open" in lock[-1]["data"]["checkpoint"]


def test_the_poll_notices_the_deadline_even_if_nobody_chats(config, vault, memory):
    """GET /v1/status is the only thing a left-open dashboard sends."""
    from brain.api.gateway import Gateway

    gw = Gateway(config, vault, memory, token="t")
    vault.session.last_activity = now_ms() - (config.session_timeout + 5) * 1000
    status, body = gw.route("GET", "/v1/status", {}, {})
    assert status == 200 and body["unlocked"] is False
    assert body["session"]["active"] is False
    entries = [json.loads(line)["event"] for line in config.audit_path.read_text().splitlines()]
    assert "session_idle_lock" in entries
