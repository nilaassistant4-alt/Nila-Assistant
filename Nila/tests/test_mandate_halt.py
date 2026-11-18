"""Mandate 2 (halt on anomaly) and mandate 5 (plugins disabled by default).

Found by the owner's own `nila_mandate_tests.py`, not by the shipped suite:
three defects that no existing test covered.

  * `Integration.enabled` defaulted to True, so a config that OMITTED the key was
    silently live - the example JSON said false and the code disagreed with it.
  * SYSTEM_PREAMBLE rule 3 told the model to "prefer `remember`" for people and
    appointments, which is an instruction to auto-save contact details, directly
    against mandate 1 ("must NOT be saved unless Santhosh explicitly instructs").
  * A tool exception was swallowed ("never let a tool kill the session") and the
    model continued with the remaining tools, so an anomaly looked like an
    ordinary failed call.
  * The central capability gate offered a permission prompt for never-read paths
    and refused only AFTER the owner affirmed.
"""
from __future__ import annotations

import pathlib

import pytest

from brain.agent.agent import (SYSTEM_PREAMBLE, HALT_REPLY, is_access_mismatch,
                               is_pending_prompt)
from brain.agent.integrations import Integration


# --------------------------------------------------------------------------- #
# mandate 5: disabled by default
# --------------------------------------------------------------------------- #
def test_integration_defaults_to_disabled():
    assert Integration(name="x", base_url="http://127.0.0.1").enabled is False


def test_omitted_enabled_key_means_disabled(gateway_factory):
    """The example JSON sets false; omitting the key must NOT mean true."""
    gw = gateway_factory([{"name": "no_key", "base_url": "http://127.0.0.1",
                           "operations": []}])
    assert gw.integrations["no_key"].enabled is False
    assert "no_key" not in gw.names()


def test_explicit_true_still_enables(gateway_factory):
    gw = gateway_factory([{"name": "on", "base_url": "http://127.0.0.1",
                           "operations": [], "enabled": True}])
    assert gw.integrations["on"].enabled is True
    assert "on" in gw.names()


# --------------------------------------------------------------------------- #
# mandate 1: the prompt must not instruct auto-saving
# --------------------------------------------------------------------------- #
def test_system_prompt_does_not_instruct_auto_saving():
    assert "Prefer `remember` for anything the user" not in SYSTEM_PREAMBLE
    assert "Store nothing on your own initiative" in SYSTEM_PREAMBLE
    assert "explicitly asks you to save" in SYSTEM_PREAMBLE


def test_system_prompt_protects_one_off_contact_details():
    low = SYSTEM_PREAMBLE.lower()
    assert "contact number" in low
    assert "never stored" in low


def test_system_prompt_states_the_halt_rule():
    # normalise whitespace: the rule is line-wrapped in the preamble, and
    # asserting a literal phrase across the wrap would be a test bug, not a
    # product defect.
    flat = " ".join(SYSTEM_PREAMBLE.split())
    assert "STOP" in SYSTEM_PREAMBLE
    assert "wait for the user's explicit approval" in flat
    assert "Do not retry" in flat


# --------------------------------------------------------------------------- #
# mandate 2: distinguishing an anomaly from the designed permission gate
# --------------------------------------------------------------------------- #
def test_pending_prompt_is_not_an_anomaly():
    r = {"ok": False, "error": "permission required", "prompt_id": "prm_x",
         "reason": "explicit affirmation required before this action can run"}
    assert is_pending_prompt(r) is True
    assert is_access_mismatch(r) is False


def test_hard_refusal_is_an_anomaly():
    r = {"ok": False, "error": "refused: /.ssh is on the never-read list",
         "note": "this path is never readable"}
    assert is_pending_prompt(r) is False
    assert is_access_mismatch(r) is True


@pytest.mark.parametrize("result", [
    {"ok": False, "error": "refused: panic latch is set"},
    {"ok": False, "error": "integration egress blocked for host"},
    {"ok": False, "error": "step-up required for data.export"},
    {"ok": False, "error": "refused: forbidden path"},
])
def test_hard_mismatches_are_detected(result):
    assert is_access_mismatch(result) is True


@pytest.mark.parametrize("result", [
    {"ok": True},
    {"ok": True, "content": "hello"},
    {"ok": False, "error": "no such file: /tmp/x"},
    {"ok": False, "error": "path is required"},
])
def test_benign_results_do_not_halt(result):
    """Over-halting would make mandate 2 fire on normal operation."""
    assert is_access_mismatch(result) is False


# --------------------------------------------------------------------------- #
# mandate 2: end-to-end halt behaviour
# --------------------------------------------------------------------------- #
def test_never_read_path_halts_and_is_audited(config, vault, memory, brain):
    turn = brain.handle(f"read the file {pathlib.Path.home() / '.ssh/id_rsa'}")
    assert turn.ok is False, "an access mismatch must fail the turn"
    assert "HALTED" in turn.answer
    assert "did not retry" in turn.answer
    events = [e for e in vault.read_audit(80) if e.get("event") == "anomaly_halt"]
    assert events, "the halt must be written to the audit journal"
    assert "never-read" in str(events[-1]["data"]["reason"])


def test_never_read_path_offers_no_prompt(config, vault, memory, brain):
    """The gate must not ask permission for something that can never succeed."""
    r = brain.registry.call("read_file",
                            {"path": str(pathlib.Path.home() / ".ssh/id_rsa")},
                            brain.tool_ctx)
    assert r["ok"] is False
    assert r["error"].startswith("refused:")
    assert not r.get("prompt_id"), "no prompt may be issued for a never-read path"


def test_ordinary_permission_gate_still_prompts(config, vault, memory, brain):
    """The designed flow must survive: prompt, do not halt."""
    r = brain.registry.call("read_file", {"path": "/etc/hosts"}, brain.tool_ctx)
    assert r["ok"] is False
    assert r["error"] == "permission required"
    assert r.get("prompt_id"), "the owner must be able to affirm a legitimate read"
    turn = brain.handle("read the file /etc/hosts")
    assert "HALTED" not in turn.answer


def test_halt_can_be_disabled_by_config(config, vault, memory):
    config.agent["halt_on_anomaly"] = False
    from brain.agent.agent import Brain
    from brain.agent.local_brain import LocalBrain
    b = Brain(config, vault, memory, provider=LocalBrain())
    turn = b.handle(f"read the file {pathlib.Path.home() / '.ssh/id_rsa'}")
    assert "HALTED" not in turn.answer


def test_halt_reply_names_a_recovery_path():
    assert "brain permission panic" in HALT_REPLY
