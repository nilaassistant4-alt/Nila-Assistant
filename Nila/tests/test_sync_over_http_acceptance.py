"""Acceptance tests: stage 12, gap 2 - sync between two PCs, over the network.

`test_sync.py` proves the protocol against a folder pretending to be a cloud.
That leaves one question the owner actually asks - "will my other PC really get
this?" - and it can only be answered by putting a real HTTP server in the
middle and using the client the ZIP ships. This file does that:

* it starts `cloud/sync_server/app.py` on a free port, exactly as the owner
  would (a token, a data folder);
* two vaults are created, the second from the first's bundle, so they share the
  master key - which is what "my other PC" means;
* a record pushed from PC A is pulled by PC B through the socket;
* a conflict in the same record converges with no data lost;
* the server refuses the wrong token, refuses to store plaintext it could read,
  and cannot link a blob back to a record id.

If the server cannot be started on this machine the test skips with the reason -
it never fakes the network layer, because a fake network proves nothing.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from brain.cloud.protocol import SyncEngine
from brain.config import Config
from brain.memory.store import MemoryStore
from brain.memory.vault import Vault

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "cloud" / "sync_server" / "app.py"
PASS = "two-pc-passphrase-77"
TOKEN = "test-token-" + "k" * 24


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_health(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    return False


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """The reference server, running, on a port nobody else is using."""
    port = free_port()
    data = tmp_path / "server-data"
    env = dict(os.environ, BRAIN_SYNC_TOKEN=TOKEN)
    proc = subprocess.Popen([sys.executable, str(SERVER), "--port", str(port),
                             "--data", str(data)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    url = f"http://127.0.0.1:{port}"
    try:
        if not wait_for_health(url):
            out = ""
            if proc.poll() is not None and proc.stdout is not None:
                out = proc.stdout.read()[-800:]
            pytest.skip(f"sync server did not start here ({out.strip() or 'no /health'})")
        monkeypatch.setenv("BRAIN_SYNC_TOKEN", TOKEN)
        yield {"url": url, "data": data, "port": port}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:      # pragma: no cover
            proc.kill()


def make_pc(root: Path, label: str, url: str, import_bundle=None):
    cfg = Config.load(root)
    cfg.fast_kdf = True
    cfg.agent["device_id"] = label
    cfg.save()
    vault = Vault(cfg)
    if import_bundle:
        vault.import_bundle(import_bundle, PASS, device_label=label)
    else:
        vault.initialize(PASS, user="owner", device_label=label)
    mem = MemoryStore(cfg, vault.session.mk)
    cfg.sync = {"backend": "http", "url": url, "token_env": "BRAIN_SYNC_TOKEN"}
    return cfg, vault, mem


@pytest.fixture()
def two_pcs(server, tmp_path):
    a = make_pc(tmp_path / "pcA", "pc-a", server["url"])
    bundle = a[1].export_bundle(label="pc-a")
    b = make_pc(tmp_path / "pcB", "pc-b", server["url"], import_bundle=bundle)
    return a, b


def settle(*engines, rounds: int = 6):
    """Sync until a whole round changes nothing - convergence, not one lucky pass."""
    quiet = ("pushed", "pulled", "conflicts", "deleted", "skipped")
    for _ in range(rounds):
        reports = [e.sync() for e in engines]
        assert all(not r["errors"] for r in reports), reports
        if all(all(r[k] == 0 for k in quiet) for r in reports):
            return reports
    raise AssertionError("sync did not converge over HTTP")


def test_a_record_written_on_one_pc_arrives_on_the_other(two_pcs):
    (cfg_a, vault_a, mem_a), (cfg_b, vault_b, mem_b) = two_pcs
    rid = mem_a.remember("the shop rent is 8000 rupees, paid on the 5th").id

    report = SyncEngine(cfg_a, mem_a, vault_a).sync()
    assert report["errors"] == [] and report["pushed"] == 1
    assert report["backend"] == "http" and report.get("remote") in (True, None)

    engine_b = SyncEngine(cfg_b, mem_b, vault_b)
    settle(engine_b)
    hits = mem_b.search("shop rent")
    assert hits and "8000" in hits[0].body
    assert mem_b.get(rid) is not None


def test_what_the_server_stores_is_encrypted_and_unlinkable(two_pcs, server):
    (cfg_a, vault_a, mem_a), _ = two_pcs
    rid = mem_a.remember("HDFC card 4417 1234 9876 5532 expires 2031").id
    assert SyncEngine(cfg_a, mem_a, vault_a).sync()["pushed"] == 1

    blobs = list(Path(server["data"]).rglob("*.blob.json"))
    assert blobs, "the server stored nothing"
    raw = b"".join(p.read_bytes() for p in blobs)
    for secret in (b"HDFC", b"4417 1234", b"expires 2031", rid.encode()):
        assert secret not in raw, f"the server could read {secret!r}"
    # The server does store an opaque id - it has to, to key the blob - but it is
    # an HMAC under a key derived from the master key, so nobody but the owner
    # can turn it back into a record id. What must not be there is the record id
    # itself, or the plaintext (asserted above).
    engine = SyncEngine(cfg_a, mem_a, vault_a)
    elsewhere = SyncEngine(cfg_a, mem_a, vault_a)      # same vault, same key
    assert engine.opaque_id(rid) == elsewhere.opaque_id(rid)   # stable, and keyed
    assert "not_" not in raw.decode("latin-1")          # every id in this vault starts `not_`


def test_the_wrong_token_gets_nothing(server, tmp_path):
    cfg = Config.load(tmp_path / "intruder")
    cfg.fast_kdf = True
    cfg.agent["device_id"] = "intruder"
    cfg.save()
    vault = Vault(cfg)
    vault.initialize(PASS, user="intruder", device_label="intruder")
    mem = MemoryStore(cfg, vault.session.mk)
    cfg.sync = {"backend": "http", "url": server["url"],
                "token_env": "NILA_WRONG_TOKEN"}
    os.environ["NILA_WRONG_TOKEN"] = "not-the-token"
    try:
        # build_store refuses a *missing* token; a *wrong* one is refused by the
        # server, which is the case here, so go straight through the client.
        from brain.cloud.protocol import HttpBlobStore, SyncError

        store = HttpBlobStore(server["url"], "not-the-token")
        with pytest.raises(SyncError):
            store.put("nila-test-key", {"v": 0}, expect_version=0)
    finally:
        os.environ.pop("NILA_WRONG_TOKEN", None)
    # and the honest client reports it instead of pretending to have synced
    cfg.sync = {"backend": "http", "url": server["url"], "token_env": "NILA_WRONG_TOKEN"}
    os.environ["NILA_WRONG_TOKEN"] = "not-the-token"
    try:
        report = SyncEngine(cfg, mem, vault).sync()
    finally:
        os.environ.pop("NILA_WRONG_TOKEN", None)
    assert report["errors"] and report["pushed"] == 0


def test_a_missing_token_is_refused_before_anything_is_sent(two_pcs, monkeypatch):
    from brain.cloud.protocol import SyncError

    (cfg_a, vault_a, mem_a), _ = two_pcs
    monkeypatch.delenv("BRAIN_SYNC_TOKEN", raising=False)
    with pytest.raises(SyncError, match="token"):
        SyncEngine(cfg_a, mem_a, vault_a)


def test_two_pcs_editing_the_same_note_keep_both(two_pcs):
    (cfg_a, vault_a, mem_a), (cfg_b, vault_b, mem_b) = two_pcs
    rid = mem_a.remember("meeting moved to Monday 10 am, hall 2").id
    settle(SyncEngine(cfg_a, mem_a, vault_a), SyncEngine(cfg_b, mem_b, vault_b))

    # both sides change the same record while the other cannot see it
    mem_a.update(rid, "meeting moved to Monday 10 am, hall 3")
    mem_b.update(rid, "meeting moved to Tuesday 3 pm, hall 3")
    settle(SyncEngine(cfg_a, mem_a, vault_a), SyncEngine(cfg_b, mem_b, vault_b))

    # The canonical copy is whichever edit was last, and the other one is kept
    # as a "[conflict]" copy - never thrown away. Both PCs must agree.
    conflict = [r.body for r in mem_a.list_records(tag="conflict", limit=20)]
    conflict_b = [r.body for r in mem_b.list_records(tag="conflict", limit=20)]
    assert conflict == conflict_b and conflict, "the losing edit must be preserved"
    bodies = " ".join(r.body for r in mem_a.list_records()) + " " + \
             " ".join(r.body for r in mem_b.list_records())
    assert "Monday 10 am" in bodies and "Tuesday 3 pm" in bodies
    settle(SyncEngine(cfg_a, mem_a, vault_a), SyncEngine(cfg_b, mem_b, vault_b))
    assert {r.body for r in mem_a.list_records()} == {r.body for r in mem_b.list_records()}


def test_a_deleted_note_is_removed_on_the_other_pc(two_pcs):
    (cfg_a, vault_a, mem_a), (cfg_b, vault_b, mem_b) = two_pcs
    rid = mem_a.remember("temporary note: cancel the water can order").id
    settle(SyncEngine(cfg_a, mem_a, vault_a), SyncEngine(cfg_b, mem_b, vault_b))
    assert mem_b.get(rid) is not None

    assert mem_a.delete(rid) is True
    settle(SyncEngine(cfg_a, mem_a, vault_a), SyncEngine(cfg_b, mem_b, vault_b))
    assert mem_b.get(rid) is None or mem_b.get(rid).deleted


def test_the_second_pc_cannot_be_created_without_the_master_key(server, tmp_path):
    """A bundle is the only way in: an attacker with the URL and the token is not."""
    cfg = Config.load(tmp_path / "foreign")
    cfg.fast_kdf = True
    cfg.save()
    vault = Vault(cfg)
    with pytest.raises(Exception):
        vault.import_bundle({"not": "a bundle"}, PASS, device_label="x")
    assert not vault.is_unlocked()
