"""End-to-end encrypted sync: two devices, one cloud, deterministic convergence.

The cloud sees only opaque HMAC ids and re-wrapped ciphertext. Tests assert both
the convergence behaviour AND that no plaintext ever reaches the store.
"""
import json
import urllib.parse

import pytest

from brain.cloud.protocol import LocalBlobStore, SyncEngine, SyncError
from brain.config import Config
from brain.memory.store import MemoryStore
from brain.memory.vault import Vault
from brain.util.misc import dumps

PASS = "sync-test-passphrase-99"


def make_device(root, label, import_bundle=None):
    """Create (or import) a device vault + memory store.

    ``label`` seeds the per-device id (``dev-<hmac>``) and the agent device_id
    used for LWW tie-breaking, so each device must get a distinct label.
    """
    cfg = Config.load(root)
    cfg.fast_kdf = True
    cfg.agent["device_id"] = label
    cfg.save()
    vault = Vault(cfg)
    if import_bundle:
        vault.import_bundle(import_bundle, PASS, device_label=label)
    else:
        vault.initialize(PASS, user="tester", device_label=label)
    mem = MemoryStore(cfg, vault.session.mk)
    return cfg, vault, mem


@pytest.fixture()
def two_devices(tmp_path):
    a_root, b_root, cloud_root = tmp_path / "A", tmp_path / "B", tmp_path / "cloud"
    cfg_a, vault_a, mem_a = make_device(a_root, "device-a")
    bundle = vault_a.export_bundle(label="device-a")
    cfg_b, vault_b, mem_b = make_device(b_root, "device-b", import_bundle=bundle)
    assert vault_b.is_unlocked(), "importing a bundle must prove ownership and unlock"
    store = LocalBlobStore(cloud_root)
    return (cfg_a, vault_a, mem_a, SyncEngine(cfg_a, mem_a, vault_a, store)), \
           (cfg_b, vault_b, mem_b, SyncEngine(cfg_b, mem_b, vault_b, store)), \
           cloud_root


def test_imported_device_shares_master_key(two_devices):
    (cfg_a, _, mem_a, eng_a), (cfg_b, _, mem_b, eng_b), _ = two_devices
    mem_a.remember("shared secret: the cabin key is under the third stone")
    sync_to_fixed_point(eng_a, eng_b)
    hits = mem_b.search("cabin key")
    assert hits and "third stone" in hits[0].body


def sync_to_fixed_point(*engines, max_rounds=6):
    """Alternate engines until a full round changes nothing.

    Sync is eventually consistent by design: a device only learns about records
    another device pushed *after* its own last sync, so convergence takes a few
    rounds rather than one. Reaching a fixed point is the real invariant.
    """
    quiet = ("pushed", "pulled", "conflicts", "deleted", "skipped")
    last = None
    for _ in range(max_rounds):
        reports = [e.sync() for e in engines]
        last = reports
        if all(all(r[k] == 0 for k in quiet) and not r["errors"] for r in reports):
            return reports
    raise AssertionError(f"sync did not converge in {max_rounds} rounds: {last}")


def test_cloud_never_sees_plaintext(two_devices):
    (_, _, mem_a, eng_a), (_, _, _, eng_b), cloud_root = two_devices
    secrets = [("Passport P9988776 expires 2031", ["documents"]),
               ("HDFC card 4417 1234 9876 5532", ["finance"]),
               ("Therapist: Dr Menon, Tuesdays 6pm", ["health"])]
    for body, tags in secrets:
        mem_a.remember(body, tags=tags)
    eng_a.sync()
    blob_files = list(cloud_root.glob("*.json"))
    assert blob_files, "sync should have written blobs"
    raw = b"".join(f.read_bytes() for f in blob_files)
    for needle in (b"Passport", b"P9988776", b"HDFC", b"4417", b"Therapist", b"Menon",
                   b"documents", b"finance", b"health"):
        assert needle not in raw, f"{needle!r} leaked to the cloud store"
    for f in blob_files:
        payload = json.loads(f.read_text())
        assert set(payload) <= {"oid", "body", "hmac", "v", "ts", "tomb", "client",
                                "version", "server_ts"}, payload.keys()


def test_cloud_ids_are_opaque(two_devices):
    (_, _, mem_a, eng_a), (_, _, _, _), cloud_root = two_devices
    rec = mem_a.remember("something distinctive for the id test")
    eng_a.sync()
    names = [f.name for f in cloud_root.glob("*.json")]
    assert all(rec.id not in n for n in names)
    encoded = urllib.parse.quote(eng_a.opaque_id(rec.id), safe="") + ".json"
    assert encoded in names, names


def test_concurrent_edit_converges_deterministically(two_devices):
    (_, _, mem_a, eng_a), (_, _, mem_b, eng_b), _ = two_devices
    rec = mem_a.remember("original grocery list: rice and dal")
    eng_a.sync(); eng_b.sync()

    mem_a.update(rec.id, body="A-EDIT: rice, dal and curry leaves")
    mem_b.update(rec.id, body="B-EDIT: rice, dal and mustard seeds")
    sync_to_fixed_point(eng_a, eng_b)

    a_body = mem_a.get(rec.id).body
    b_body = mem_b.get(rec.id).body
    assert a_body == b_body, "both devices must agree on the canonical copy"
    assert "A-EDIT" in a_body or "B-EDIT" in a_body

    def conflicts(mem):
        return sorted(r.body for r in mem.list_records(tag="conflict", limit=50))
    assert conflicts(mem_a) == conflicts(mem_b), "conflict copies must match on both sides"
    assert conflicts(mem_a), "the loser must be preserved, not discarded"
    assert "A-EDIT" in a_body and any("B-EDIT" in c for c in conflicts(mem_a)) or \
           "B-EDIT" in a_body and any("A-EDIT" in c for c in conflicts(mem_a))


def test_conflict_copy_title_is_marked(two_devices):
    (_, _, mem_a, eng_a), (_, _, mem_b, eng_b), _ = two_devices
    rec = mem_a.remember("insurance policy notes")
    eng_a.sync(); eng_b.sync()
    mem_a.update(rec.id, body="policy 1")
    mem_b.update(rec.id, body="policy 2")
    sync_to_fixed_point(eng_a, eng_b)
    for mem in (mem_a, mem_b):
        for r in mem.list_records(tag="conflict", limit=10):
            assert r.title.startswith("[conflict]"), r.title
            assert r.meta.get("conflict_of") == rec.id
            assert r.meta.get("resolved_by") == "lww+preserve"


def test_sync_is_idempotent(two_devices):
    (_, _, mem_a, eng_a), (_, _, mem_b, eng_b), _ = two_devices
    mem_a.remember("one"); mem_b.remember("two")
    sync_to_fixed_point(eng_a, eng_b)
    # once converged, further rounds must be completely inert
    for _ in range(3):
        for rep in (eng_a.sync(), eng_b.sync()):
            assert rep["pushed"] == 0 and rep["pulled"] == 0
            assert rep["conflicts"] == 0 and rep["skipped"] == 0
            assert rep["errors"] == [], rep


def test_tombstone_propagates(two_devices):
    (_, _, mem_a, eng_a), (_, _, mem_b, eng_b), _ = two_devices
    rec = mem_a.remember("temporary note to delete")
    eng_a.sync(); eng_b.sync()
    assert mem_b.get(rec.id) is not None
    mem_a.delete(rec.id)
    eng_a.sync(); eng_b.sync()
    remote = mem_b.get(rec.id)
    assert remote is None or remote.deleted


def test_offline_records_from_both_sides_all_arrive(two_devices):
    (_, _, mem_a, eng_a), (_, _, mem_b, eng_b), _ = two_devices
    for i in range(4):
        mem_a.remember(f"laptop note {i}")
        mem_b.remember(f"phone note {i}")
    sync_to_fixed_point(eng_a, eng_b)
    a_bodies = {r.body for r in mem_a.list_records(limit=100)}
    b_bodies = {r.body for r in mem_b.list_records(limit=100)}
    assert a_bodies == b_bodies
    assert len(a_bodies) == 8


def test_foreign_vault_cannot_read_our_blobs(tmp_path):
    """A different master key must not be able to unwrap our cloud data."""
    a_root, cloud = tmp_path / "A", tmp_path / "cloud"
    cfg_a, vault_a, mem_a = make_device(a_root, "device-a")
    mem_a.remember("private to vault A")
    eng_a = SyncEngine(cfg_a, mem_a, vault_a, LocalBlobStore(cloud))
    eng_a.sync()

    cfg_c = Config.load(tmp_path / "C"); cfg_c.fast_kdf = True
    cfg_c.agent["device_id"] = "device-c"; cfg_c.save()
    vault_c = Vault(cfg_c)
    vault_c.initialize("a-completely-different-pass", device_label="device-c")
    mem_c = MemoryStore(cfg_c, vault_c.session.mk)
    eng_c = SyncEngine(cfg_c, mem_c, vault_c, LocalBlobStore(cloud))
    rep = eng_c.sync()
    assert rep["pulled"] == 0, "a foreign key must pull nothing"
    assert rep["errors"] == [], "someone else's vault is not an error condition"
    assert rep["refused"], "foreign blobs should be reported as refused, not silently dropped"
    assert mem_c.list_records(limit=10) == [] or all(
        r.meta.get("locked") or "private to vault A" not in r.body
        for r in mem_c.list_records(limit=10))


def test_tampered_blob_is_rejected(two_devices):
    (_, _, mem_a, eng_a), (_, _, mem_b, eng_b), cloud_root = two_devices
    mem_a.remember("integrity matters")
    eng_a.sync()
    target = next(iter(cloud_root.glob("*.json")))
    payload = json.loads(target.read_text())
    # Simulate a malicious/compromised server: same key, new store version, but
    # content that no longer matches its integrity tag. Bumping only the inner
    # ``v`` would not be enough - the client keys reconciliation off the
    # store-assigned ``version``, so a server must advance that to get the
    # client to look at the blob again.
    payload = json.loads(target.read_text())
    payload["v"] = int(payload["v"]) + 1
    payload["version"] = int(payload["version"]) + 1
    target.write_text(dumps(payload))

    rep = eng_b.sync()
    # It must be REFUSED, not applied, and must not abort the whole sync: one
    # poisoned record should not take down the other seven.
    assert rep["pulled"] == 0, f"tampered blob must not be pulled: {rep}"
    assert not mem_b.search("integrity matters"), "tampered blob must not be applied"
    assert rep["errors"] or rep["skipped"] or rep["refused"], \
        f"a rejected blob should be reported somewhere: {rep}"

    # Corrupting only the ciphertext body (HMAC left stale) must fail too.
    payload = json.loads(target.read_text())
    payload["body"] = payload["body"][:-4] + "AAAA"
    payload["version"] = int(payload["version"]) + 1
    target.write_text(dumps(payload))
    rep = eng_b.sync()
    assert rep["pulled"] == 0
    assert not mem_b.search("integrity matters")


def test_sync_requires_unlock(two_devices):
    (cfg_a, vault_a, mem_a, _), _, cloud_root = two_devices
    vault_a.lock()
    with pytest.raises(SyncError):
        SyncEngine(cfg_a, mem_a, vault_a, LocalBlobStore(cloud_root))


def test_dry_run_writes_nothing(two_devices):
    (_, _, mem_a, eng_a), (_, _, _, _), cloud_root = two_devices
    mem_a.remember("dry run record")
    before = sorted(f.name for f in cloud_root.glob("*.json"))
    rep = eng_a.sync(dry_run=True)
    after = sorted(f.name for f in cloud_root.glob("*.json"))
    assert before == after
    assert rep["dry_run"] is True
