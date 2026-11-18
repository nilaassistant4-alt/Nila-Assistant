"""Regression tests for audit findings F5 (never-read list) and F6 (usage ledger).

F5: ``.env``, ``api_keys.json``, ``secrets.json``, ``*.pem`` and ``*.key`` were
readable with a granted capability, while the threat model claims credential
material is refused whatever the grant. ``.env.example`` must stay readable.

F6: the per-minute window lived only in memory, so a fresh ``UsageLedger`` on
the same file immediately allowed another request after the cap was hit. A
corrupt ledger was also read as "zero usage", which handed back a fresh budget.
"""
from __future__ import annotations

import json

import pytest

from brain.agent.host_tools import read_file, list_directory, forbidden_reason
from brain.agent.ledger import UsageLedger


# --------------------------------------------------------------------------- #
# F5 — never-read list
# --------------------------------------------------------------------------- #
class _Allow:
    allowed = True
    reason = "granted"
    capability = "fs.read"
    scope = "test"
    prompt_id = None


class _Broker:
    def request(self, *a, **k):
        return _Allow()


class _Ctx:
    permissions = _Broker()
    audit = staticmethod(lambda *a, **k: None)


@pytest.mark.parametrize("name", [
    ".env", ".env.local", ".env.production", "api_keys.json", "secrets.json",
    "secrets.yml", "credentials.yml", "credentials.json", "service.pem",
    "notes.key", "terraform.tfstate", ".npmrc", ".pgpass",
])
def test_credential_shaped_files_are_never_readable(tmp_path, name):
    target = tmp_path / name
    target.write_text("SYNTHETIC-SECRET")
    result = read_file(_Ctx(), str(target))
    assert result["ok"] is False, f"{name} was readable with a granted capability"
    assert "never-read" in result["error"] or "credential" in result["error"]


@pytest.mark.parametrize("name", [
    ".env.example", ".env.sample", "credentials.example.json", "README.md",
    "notes.txt", "server.crt", "config.yaml",
])
def test_templates_and_ordinary_files_stay_readable(tmp_path, name):
    target = tmp_path / name
    target.write_text("ordinary content")
    assert read_file(_Ctx(), str(target))["ok"] is True, f"{name} must stay readable"


def test_symlink_to_a_secret_is_refused_after_resolution(tmp_path):
    secret = tmp_path / "vault.kdbx"
    secret.write_text("SYNTHETIC")
    link = tmp_path / "holiday-photo.txt"
    link.symlink_to(secret)
    result = read_file(_Ctx(), str(link))
    assert result["ok"] is False
    assert "symlink" in result["error"]


def test_directory_listing_still_works(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / ".env").write_text("SECRET")
    listing = list_directory(_Ctx(), str(tmp_path))
    assert listing["ok"] is True
    names = {e["name"] for e in listing["entries"]}
    # Names are listed (they are metadata), but the content is never returned.
    assert "a.txt" in names
    assert all("content" not in e for e in listing["entries"])


def test_forbidden_reason_is_shared_with_the_dashboard():
    assert forbidden_reason(__import__("pathlib").Path("/home/x/.ssh/id_rsa"))


# --------------------------------------------------------------------------- #
# F6 — usage ledger
# --------------------------------------------------------------------------- #
class _Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_minute_window_survives_a_restart(tmp_path):
    clk = _Clock()
    path = tmp_path / "usage.json"
    cfg = {"daily_requests": 100, "rpm": 2}
    first = UsageLedger(path, clock=clk)
    first.record("gemini", True)
    first.record("gemini", True)
    assert first.check("gemini", cfg)[0] is False

    restarted = UsageLedger(path, clock=clk)          # a new process
    ok, why = restarted.check("gemini", cfg)
    assert ok is False, "restarting refunded the per-minute budget"
    assert "minute" in why

    clk.t += 61
    assert restarted.check("gemini", cfg)[0] is True


def test_daily_total_gate_survives_a_restart(tmp_path):
    clk = _Clock()
    path = tmp_path / "usage.json"
    cfg = {"daily_requests": 10, "stop_at_pct": 50, "rpm": 100}
    first = UsageLedger(path, clock=clk)
    for _ in range(5):
        first.record("gemini", True)
    ok, why = UsageLedger(path, clock=clk).check("gemini", cfg)
    assert ok is False and "daily limit" in why


@pytest.mark.parametrize("content", ["{not json", "[]", "null", "\x00\x01"])
def test_corrupt_ledger_fails_closed(tmp_path, content):
    path = tmp_path / "usage.json"
    path.write_text(content)
    ok, why = UsageLedger(path, clock=_Clock()).check("gemini", {"daily_requests": 10})
    assert ok is False, "a corrupt counter was treated as zero usage"
    assert "corrupt" in why or "unreadable" in why


def test_ledger_still_stores_no_message_text(tmp_path):
    path = tmp_path / "usage.json"
    led = UsageLedger(path, clock=_Clock())
    led.record("gemini", True, tokens=12)
    blob = json.loads(path.read_text())
    assert set(blob) == {"days", "cooldown", "recent"}
    for day in blob["days"].values():
        for counters in day.values():
            assert set(counters) == {"requests", "failures", "tokens"}
