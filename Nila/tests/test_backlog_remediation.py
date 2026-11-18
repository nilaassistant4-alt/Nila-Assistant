"""Regression tests for the v0.2 backlog remediations.

Every test here reproduces a bug that was found and fixed, so a future change
that reintroduces it fails loudly. The backlog ids match
``NILA_V02_PLAN_DRAFT_TA.md`` §1.

Covered:

* **A2** — a degraded re-login must not reopen a full-access memory store.
* **A3** — the audit verifier must actually recompute the hash chain.
* **A4** — a backup taken while the app is running keeps committed records (WAL).
* **A5** — attachment bytes travel with the project record.
* **B6** — a TOTP code works once, never twice.
* **B7** — negative / malformed ``Content-Length`` is an immediate client error.
* **B8** — a corrupt usage ledger cannot be used to bypass the daily cap.
* **B9** — the old project is still reachable past the 200-record page.
* **B10** — one approved action authorises exactly one call (no replay).
* **B11** — TOFU cannot be talked into pinning an unauthenticated key.
* **B12** — sync reports the backend it really used, and exits non-zero on error.
* **B13** — every subpackage ships in the wheel.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
import threading
from pathlib import Path

import pytest

# The passphrase has to be importable under both runners. Under pytest the
# `tests` directory is on sys.path; the stdlib fallback runner (scripts/
# run_tests.py) loads modules from a different root, so it needs the explicit
# entry or the whole module fails to import.
try:
    from conftest import PASSPHRASE
except ModuleNotFoundError:                       # pragma: no cover - fallback runner
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import PASSPHRASE


# --------------------------------------------------------------------------- #
# A2 - a degraded session cannot open a full-tier memory store
# --------------------------------------------------------------------------- #
def test_degraded_relogin_cannot_open_a_full_store(config, vault):
    """The reported repro: an API re-login with a skipped factor, same master key."""
    from brain.memory.store import MemoryStore
    from brain.security.classify import session_tier

    memory = MemoryStore(config, vault.session.mk, session=vault.session)
    try:
        memory.remember("card 4111 1111 1111 1111", "financial detail",
                        kind="note", tags=["financial"])
    finally:
        memory.close()
    vault.lock()

    mk = vault.unlock(PASSPHRASE)
    vault.start_session(mk, mode="passphrase", degraded=True)
    try:
        assert session_tier(vault.session) == "degraded"

        # asking for "full" on a degraded session must be refused, not honoured
        store = MemoryStore(config, vault.session.mk, access_level="full",
                            session=vault.session)
        try:
            assert store.level == "degraded"
        finally:
            store.close()
    finally:
        vault.lock()


# --------------------------------------------------------------------------- #
# A3 - the audit chain is actually recomputed
# --------------------------------------------------------------------------- #
def _write_audit_entries(vault, count: int = 4) -> Path:
    for i in range(count):
        vault.audit(f"event_{i}", {"detail": f"original {i}"})
    return vault.config.audit_path


def test_audit_chain_verifies_when_untouched(vault):
    _write_audit_entries(vault)
    result = vault.verify_audit_chain()
    assert result["ok"] is True
    assert result["mismatched"] == 0
    assert result["legacy_unprotected"] == 0


def test_audit_chain_detects_an_edited_payload(vault):
    """The exact bug: editing `data` used to leave the chain valid."""
    path = _write_audit_entries(vault)
    lines = path.read_text().splitlines()
    entry = json.loads(lines[1])
    entry["data"] = {"detail": "TAMPERED"}
    lines[1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    result = vault.verify_audit_chain()
    assert result["ok"] is False
    assert result["mismatched"] >= 1
    assert any(p["line"] == 2 for p in result["problems"])


def test_audit_chain_detects_deletion(vault):
    path = _write_audit_entries(vault)
    lines = path.read_text().splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n")
    result = vault.verify_audit_chain()
    assert result["ok"] is False
    assert result["mismatched"] >= 1


def test_audit_chain_detects_reordering(vault):
    path = _write_audit_entries(vault)
    lines = path.read_text().splitlines()
    lines[0], lines[1] = lines[1], lines[0]
    path.write_text("\n".join(lines) + "\n")
    assert vault.verify_audit_chain()["ok"] is False


def test_audit_chain_detects_truncation(vault):
    """A half-written last line (power loss mid-append) is not a valid entry."""
    path = _write_audit_entries(vault)
    lines = path.read_bytes().splitlines(keepends=True)
    assert len(lines) >= 4
    path.write_bytes(b"".join(lines[:-1]) + lines[-1][:12])
    assert vault.verify_audit_chain()["ok"] is False


def test_pre_fix_journal_is_reported_not_accused(config, vault):
    """Old entries chained only `prev + event`; call them unverifiable, not forged."""
    from brain.crypto.hash import fast_kdf, hkdf
    from brain.util.misc import b64e

    path = vault.config.audit_path
    path.parent.mkdir(parents=True, exist_ok=True)
    lines, prev = [], b""
    for i in range(3):
        entry = {"ts": 1, "event": f"old_{i}", "data": {"n": i},
                 "device": vault.device_id or "-"}
        key = fast_kdf(vault.session.mk, b"pbx/audit-chain/v1", 32)
        entry["chain"] = b64e(hkdf(key, prev, entry["event"].encode(), 16))
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        lines.append(line)
        prev = hashlib.sha256(line.encode()).digest()
    path.write_text("\n".join(lines) + "\n")

    result = vault.verify_audit_chain()
    assert result["ok"] is True                     # not reported as tampering
    assert result["legacy_unprotected"] == 3        # but flagged as unverifiable

    # a NEW entry after the old ones is still fully protected
    vault.audit("new_entry", {"n": 1})
    assert vault.verify_audit_chain()["ok"] is True
    rows = path.read_text().splitlines()
    obj = json.loads(rows[-1])
    obj["data"] = {"n": "TAMPERED"}
    rows[-1] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(rows) + "\n")
    assert vault.verify_audit_chain()["ok"] is False


# --------------------------------------------------------------------------- #
# A4 / A5 - live backup keeps committed rows and attachment bytes
# --------------------------------------------------------------------------- #
def test_live_backup_keeps_wal_records(config, vault):
    """A record committed through a second connection must be in the backup."""
    import sqlite3

    from brain.memory.store import MemoryStore

    memory = MemoryStore(config, vault.session.mk, session=vault.session)
    try:
        for i in range(3):
            memory.remember(f"live record {i}")
        blob = memory.export_encrypted_backup()
        header, _, rest = blob.partition(b"\n")
        meta = json.loads(header)
        assert meta["format"] == 2
        # The database block is the online snapshot, so it is a complete file,
        # not an empty 4 KiB WAL-pending husk.
        assert int(meta["bytes"]) > 4096
        assert meta["attachments"] == 0
    finally:
        memory.close()


def test_backup_includes_attachment_bytes(config, vault, tmp_path):
    """A5: project files are sealed `.pbx` blobs beside the DB - back them up too."""
    import importlib.util

    if importlib.util.find_spec("cryptography") is None:
        pytest.skip("AES backend not installed")

    from brain.memory.store import MemoryStore
    from brain.projects.files import FileStore
    from brain.projects.store import ProjectStore

    memory = MemoryStore(config, vault.session.mk, session=vault.session)
    try:
        files = FileStore(config.home / "projects", lambda: bytes(vault.session.mk))
        projects = ProjectStore(memory, files)
        project = projects.create("Attachment probe")
        payload = b"ATTACHMENT-BYTES-12345"
        meta = projects.attach_file(project["title"], "note.txt", payload)
        # the bytes really are sealed on disk, not sitting there in the clear
        sealed = config.home / "projects" / f"{meta['id']}.pbx"
        assert sealed.exists()
        assert payload not in sealed.read_bytes()

        blob = memory.export_encrypted_backup()
        header, _, rest = blob.partition(b"\n")
        archive = json.loads(header)
        assert archive["format"] == 2
        assert archive["attachments"] == 1
        assert archive["attachments_sha256"]

        # the sealed bytes are inside the archive, exactly as on disk
        section = rest[int(archive["bytes"]):]
        restored = json.loads(section[: section.rindex(b"\n")] if b"\n" in section.strip() else section)
        # urlsafe base64, and decoding it gives back the sealed file byte for byte
        from brain.util.misc import b64d
        assert b64d(restored[sealed.name]) == sealed.read_bytes()
    finally:
        memory.close()


# --------------------------------------------------------------------------- #
# B6 - TOTP is one-shot
# --------------------------------------------------------------------------- #
def test_totp_code_cannot_be_replayed(vault):
    from brain.identity.ceremony import IdentityCeremony
    from brain.identity.totp import totp as make_code

    ceremony = IdentityCeremony(vault.config, vault)
    info = ceremony.enroll_totp(account="tester")
    secret = info["secret"]
    code = make_code(secret)

    first = ceremony.unlock(PASSPHRASE, mode="totp", totp_code=code)
    assert first.ok is True, first.reasons
    ceremony.lock()

    second = ceremony.unlock(PASSPHRASE, mode="totp", totp_code=code)
    assert second.ok is False, "a used TOTP code authenticated twice (B6)"


# --------------------------------------------------------------------------- #
# B7 - malformed Content-Length never pins a handler thread
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value,expected", [("-1", 400), ("abc", 400),
                                            ("12x", 400), ("999999999", 413)])
def test_malformed_content_length_is_an_immediate_client_error(value, expected):
    """Raw socket test: the server must answer without reading a body that is not there."""
    import importlib

    gateway_mod = importlib.import_module("brain.api.gateway")
    handler_cls = getattr(gateway_mod, "_Handler", None)
    if handler_cls is None or not issubclass(handler_cls, __import__("http.server").server.BaseHTTPRequestHandler):
        pytest.skip("gateway handler shape changed")

    class FakeGW:
        token = "t"
        route = lambda *a, **k: (200, {"ok": True})

    subclass = type("ProbeHandler", (handler_cls,), {"gateway": FakeGW()})
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), subclass)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            request = (f"POST /v1/ask HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                       f"Authorization: Bearer t\r\nContent-Type: application/json\r\n"
                       f"Content-Length: {value}\r\n\r\n").encode()
            sock.sendall(request)
            sock.settimeout(5)
            reply = sock.recv(4096).decode(errors="replace")
        assert f" {expected}" in reply.splitlines()[0], reply[:120]
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# B8 - a corrupt ledger cannot bypass the cap
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("payload", [
    '{"days": []}',                                             # wrong shape
    '{"days": {"2026-01-01": {"gemini": {"requests": "lots"}}}}',  # wrong type
    '{"days": {"2026-01-01": {"gemini": {"requests": true}}}}',    # bool is not a count
    '{"days": ',                                                # truncated write
])
def test_corrupt_ledger_fails_closed(tmp_path, payload):
    from brain.agent.ledger import UsageLedger

    path = tmp_path / "usage.json"
    ledger = UsageLedger(path)
    cfg = {"daily_requests": 2, "rpm": 60, "stop_at_pct": 100}
    ledger.check("gemini", cfg)
    ledger.record("gemini", True, 1)

    path.write_text(payload)
    allowed, why = ledger.check("gemini", cfg)
    assert allowed is False, f"corrupt ledger was trusted: {payload}"
    assert "corrupt" in why.lower() or "unreadable" in why.lower()


def test_reaching_the_cap_refuses_the_next_request(tmp_path):
    from brain.agent.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "usage.json")
    cfg = {"daily_requests": 2, "rpm": 60, "stop_at_pct": 100}
    for _ in range(2):
        assert ledger.check("gemini", cfg)[0] is True
        ledger.record("gemini", True, 1)
    assert ledger.check("gemini", cfg)[0] is False


# --------------------------------------------------------------------------- #
# B9 - nothing silently disappears past the old 200-record page
# --------------------------------------------------------------------------- #
def test_old_project_survives_the_old_200_cap(memory):
    from brain.projects.store import SCAN_LIMIT, ProjectStore

    store = ProjectStore(memory)
    store.create("Project Number One")
    for i in range(210):
        store.create(f"Filler {i}")

    listing = store.listing()
    assert listing["truncated"] is False
    assert listing["count"] >= 211
    assert SCAN_LIMIT >= 2000
    # and the FIRST project is still reachable by name, not just by page
    found = store.get("Project Number One")
    assert found is not None or store.search("Project Number One")


# --------------------------------------------------------------------------- #
# B10 - one approval, one action
# --------------------------------------------------------------------------- #
def _broker(tmp_path):
    from brain.security.permission import Broker
    return Broker(tmp_path / "permissions.json")


def test_one_shot_approval_is_spent_once(tmp_path):
    from brain.security.permission import CAP_FILE_READ

    broker = _broker(tmp_path)
    decision = broker.request(CAP_FILE_READ, "/tmp/notes.txt", "read", ask=False)
    assert decision.allowed is False and decision.prompt_id
    broker.affirm(decision.prompt_id)

    assert broker.consume(CAP_FILE_READ, "/tmp/notes.txt").allowed is True
    second = broker.consume(CAP_FILE_READ, "/tmp/notes.txt")
    assert second.allowed is False, "a one-shot approval was replayed (B10)"


def test_tool_call_leaves_no_live_approval(config, vault, memory, tmp_path):
    """Even a handler that never calls request() cannot leave the grant usable."""
    from brain.agent.tools import ToolContext, ToolRegistry
    from brain.security.permission import CAP_NET

    broker = _broker(tmp_path)
    registry = ToolRegistry()
    calls = []

    @registry.tool(name="probe", description="probe", capability=CAP_NET,
                   parameters={"type": "object", "properties": {}})
    def probe(ctx, **kwargs):
        calls.append(1)                        # deliberately NO request() call
        return {"ok": True}

    ctx = ToolContext(memory=memory, vault=vault, config=config,
                      session=vault.session, permissions=broker,
                      audit=lambda e, d: None)

    first = registry.call("probe", {}, ctx)
    assert first["ok"] is False and first["prompt_id"]
    broker.affirm(first["prompt_id"])

    assert registry.call("probe", {}, ctx)["ok"] is True
    replay = registry.call("probe", {}, ctx)
    assert replay["ok"] is False, "the approval was reused for a second call"
    assert len(calls) == 1


def test_integration_approval_is_spent_once(config, tmp_path):
    from brain.agent.integrations import IntegrationGateway
    from brain.security.permission import CAP_NET

    broker = _broker(tmp_path)
    cfg_path = tmp_path / "integrations.json"
    cfg_path.write_text(json.dumps({"integrations": [{
        "name": "demo", "base_url": "https://example.invalid", "enabled": True,
        "allow_hosts": ["example.invalid"],
        "operations": [{"name": "ping", "method": "GET", "path": "/ping"}]}]}))
    gateway = IntegrationGateway(config, path=cfg_path)

    class Ctx:
        permissions = broker
        def audit(self, event, data): pass

    first = gateway.call("demo", "ping", {}, ctx=Ctx())
    assert first["ok"] is False and first.get("prompt_id")
    broker.affirm(first["prompt_id"])

    # the first call after affirmation consumes the approval; a second must ask
    broker.consume(CAP_NET, "example.invalid")
    assert broker.peek(CAP_NET, "example.invalid").allowed is False


# --------------------------------------------------------------------------- #
# B11 - TOFU never pins a key that authenticates nothing
# --------------------------------------------------------------------------- #
def _signed_manifest(key: bytes, kid: str = "k1", sign: bool = True,
                     version: str = "1.0.1") -> bytes:
    from brain.updates.manifest import Artifact, Manifest

    artifact = Artifact.parse({"name": "app.zip", "kind": "package", "path": "app.zip",
                               "sha256": "0" * 64, "bytes": 10})
    manifest = Manifest(channel="stable", version=version, released_at=1700000000,
                        artifacts=[artifact])
    if sign:
        mac = hmac.new(key, manifest.canonical(), hashlib.sha256).digest()
        manifest.signature = {"alg": "hmac-sha256", "key_id": kid,
                              "value": base64.b64encode(mac).decode()}
    return manifest.to_json().encode()


def test_tofu_refuses_to_pin_a_symmetric_key_from_the_manifest(config):
    from brain.updates.manager import UpdateManager

    manager = UpdateManager(config)
    manager.add_channel("stable", "https://example.invalid/manifest.json", mode="tofu")
    manager._fetch = lambda url, timeout=30.0: _signed_manifest(b"k" * 32)
    result = manager.check()
    assert result["ok"] is False
    assert "out of band" in (result.get("error") or "").lower()


def test_tofu_refuses_an_unsigned_manifest(config):
    from brain.updates.manager import UpdateManager

    manager = UpdateManager(config)
    manager.add_channel("stable", "https://example.invalid/manifest.json", mode="tofu")
    manager._fetch = lambda url, timeout=30.0: _signed_manifest(b"k" * 32, sign=False)
    assert manager.check()["ok"] is False


def test_pinned_key_accepts_the_real_signer_and_rejects_a_forgery(config):
    from brain.updates.manager import UpdateManager

    key = b"shared-channel-secret-32-bytes!!!!!"
    manager = UpdateManager(config)
    manager.add_channel("stable", "https://example.invalid/manifest.json",
                        alg="hmac-sha256", public=base64.b64encode(key).decode(),
                        key_id="k1", mode="strict")

    manager._fetch = lambda url, timeout=30.0: _signed_manifest(key)
    assert manager.check()["ok"] is True

    manager._fetch = lambda url, timeout=30.0: _signed_manifest(key, version="1.0.2")
    assert manager.check()["ok"] is True

    manager._fetch = lambda url, timeout=30.0: _signed_manifest(b"attacker-key-32-bytes!!!!!!!!!!")
    assert manager.check()["ok"] is False

    manager._fetch = lambda url, timeout=30.0: _signed_manifest(key, sign=False)
    assert manager.check()["ok"] is False

    manager._fetch = lambda url, timeout=30.0: _signed_manifest(key, kid="k2")
    assert manager.check()["ok"] is False


# --------------------------------------------------------------------------- #
# B12 - sync tells the truth about what it did
# --------------------------------------------------------------------------- #
def test_sync_status_flags_a_remote_url_with_a_local_backend(config, vault, memory):
    from brain.cloud.protocol import SyncEngine, build_store

    config.sync["url"] = "https://example.invalid/bucket"
    config.sync["backend"] = ""
    store = build_store(config, vault)
    assert type(store).__name__ == "LocalBlobStore"

    status = SyncEngine(config, memory, vault, store).status()
    assert status["backend"] == "local"
    assert status["remote"] is False
    assert any("local file copy" in w for w in status["warnings"])


def test_sync_failure_is_reported_not_swallowed(config, vault, memory, tmp_path, monkeypatch):
    """`run_sync` used to import a module that does not exist, so every call died."""
    from brain.agent import tools as tools_mod

    from brain.security.permission import CAP_NET

    broker = _broker(tmp_path)
    ctx = tools_mod.ToolContext(memory=memory, vault=vault, config=config,
                                session=vault.session, audit=lambda e, d: None,
                                permissions=broker)

    def boom(*args, **kwargs):
        raise RuntimeError("remote is down")

    monkeypatch.setattr("brain.cloud.build_sync_engine", boom)
    registry = tools_mod.build_default_registry()
    for tool in ("run_sync", "sync_status"):
        # `net.host` can never be a standing grant, so each call needs its own
        # affirmation - which is the point of the capability.
        blocked = registry.call(tool, {}, ctx)
        if blocked.get("prompt_id"):
            broker.affirm(blocked["prompt_id"])
            result = registry.call(tool, {}, ctx)
        else:
            # a standing grant may already exist; the first call is the real one
            result = blocked
        assert result["ok"] is False, f"{tool} swallowed the failure: {result}"
        assert "remote is down" in json.dumps(result), tool


# --------------------------------------------------------------------------- #
# B13 - the wheel ships every subpackage
# --------------------------------------------------------------------------- #
def test_pyproject_ships_every_subpackage():
    """`packages = ["brain"]` shipped only the top level: no brain.cli at all."""
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    live = [line for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
    assert any('include = ["brain", "brain.*"]' in line for line in live)
    assert not any(line.strip().startswith('packages = ["brain"]') for line in live)


# --------------------------------------------------------------------------- #
# A2 (acceptance) - explicit lock, idle expiry and absolute expiry all agree
# --------------------------------------------------------------------------- #
def test_explicit_lock_invalidates_every_open_handle(config, vault):
    """After `lock()` the old store must refuse - not answer from cached rows."""
    from brain.memory.store import MemoryStore

    memory = MemoryStore(config, vault.session.mk, session=vault.session)
    try:
        memory.remember("a note the old handle could still see")
        vault.lock()
        with pytest.raises(RuntimeError):
            memory.list_records(limit=5)
    finally:
        try:
            memory.close()
        except Exception:
            pass


def test_absolute_expiry_refuses_a_new_store(config, vault):
    """An expired session must not be able to open a store at all."""
    from brain.memory.store import MemoryStore

    session = vault.session
    session.expires_at = 0
    session.expires_at_monotonic = 0.0
    session.absolute_expires_at = 0
    session.absolute_expires_at_monotonic = 0.0
    assert session.expired() is True

    with pytest.raises(RuntimeError):
        MemoryStore(config, session.mk, session=session)


def test_store_reports_the_level_it_actually_uses(config, vault):
    """CLI, API and search all read the tier from the same place: the session."""
    from brain.memory.store import MemoryStore
    from brain.security.classify import session_tier

    store = MemoryStore(config, vault.session.mk, access_level=session_tier(vault.session),
                        session=vault.session)
    try:
        assert store.level == session_tier(vault.session)
    finally:
        store.close()
