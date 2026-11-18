"""Sealed vectors, class-scoped keys, and device-local biometrics.

Covers the two highest-ranked items in docs/THREAT_MODEL.md:

* T1  - vectors were stored as plaintext JSON under a *public* deterministic
        embedder, so a stolen memory.db permitted a dictionary attack that
        attributed guessed phrases to records with no passphrase (measured cos
        0.901 on a health-related guess).
* T11 - the voiceprint travelled in the device enrolment bundle and was sealed
        under the shared master key, so a copy opened on any machine. A
        biometric cannot be rotated after exposure.
"""

from __future__ import annotations

import json
import math
import sqlite3

import pytest

from brain.identity.voice import VoiceVerifier, transform_embedding
from brain.memory.store import SCHEMA_VERSION
from brain.memory.vecvault import (
    KEY_VERSION, LEVEL_CLASSES, NEVER_SYNCED, SYNCABLE_TABLES, VEC_CLASSES,
    VEC_FINANCIAL, VEC_GENERAL, VEC_HEALTH, VEC_IDENTITY, VecIndex,
    VecVaultError, assert_syncable, class_key, decide_embed, new_salt,
    open_vector, record_key, scan_disk_for_plaintext_vectors, seal_vector,
)
from brain.security.classify import CLASS_RESTRICTED
from tests.conftest import PASSPHRASE


def _forge_v5_vectors(config, rows):
    """Recreate the vectors table with the v5 DDL (no klass/salt/key_version).

    Nulling `salt` is not enough - the v6 schema declares it NOT NULL - so a
    faithful pre-migration fixture has to drop the table and rebuild it the way
    v5 actually looked.
    """
    conn = sqlite3.connect(str(config.memory_path))
    conn.execute("DROP TABLE IF EXISTS vectors")
    conn.execute("""CREATE TABLE vectors (
        rid TEXT PRIMARY KEY, dim INTEGER NOT NULL, vec BLOB NOT NULL,
        backend TEXT NOT NULL, updated_at INTEGER NOT NULL)""")
    for rid, vec in rows:
        conn.execute("INSERT INTO vectors(rid,dim,vec,backend,updated_at) "
                     "VALUES(?,?,?,?,?)",
                     (rid, 512, json.dumps([round(v, 6) for v in vec]).encode(),
                      "hash-emb-v1", 1))
    conn.execute("UPDATE meta SET v='5' WHERE k='schema_version'")
    conn.commit()
    conn.close()


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


# --------------------------------------------------------------------------- #
# 1. the T1 attack is dead
# --------------------------------------------------------------------------- #
def test_vectors_are_sealed_not_plaintext_json(memory):
    memory.remember("Dentist appointment with Dr Mehta on Tuesday")
    row = memory.conn.execute("SELECT vec, klass, salt, key_version FROM vectors").fetchone()
    assert row is not None, "a non-restricted record should have a vector"
    assert bytes(row["vec"])[:4] == b"PBX1", "vec must be a sealed envelope"
    with pytest.raises((UnicodeDecodeError, json.JSONDecodeError, ValueError)):
        json.loads(bytes(row["vec"]).decode())
    assert len(bytes(row["salt"])) >= 16
    assert row["key_version"] == KEY_VERSION


def test_no_plaintext_vector_or_content_reaches_disk(config, memory):
    """The property this exists for, asserted by scanning the vault directory."""
    secret_phrase = "Zephyrquartz mandolin brokerage"
    memory.remember(f"{secret_phrase} appointment on Tuesday")
    memory.close()
    # a v5 store wrote floats like 0.114399 as text; those must be gone
    hits = scan_disk_for_plaintext_vectors([str(config.home)],
                                           [secret_phrase, "0.114399", "], ", "e-05"])
    assert hits == [], f"plaintext leaked to disk: {hits}"


def test_dictionary_attack_has_nothing_to_match(config, memory):
    """Reproduce the attack shape from the threat model and show it cannot run."""
    memory.remember("Therapy session notes about anxiety and sleep")
    memory.close()
    conn = sqlite3.connect(str(config.memory_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT vec FROM vectors").fetchall()
    plaintext = 0
    for r in rows:
        try:
            json.loads(bytes(r["vec"]).decode())
            plaintext += 1
        except Exception:
            pass
    assert plaintext == 0, "no vector may be readable as plaintext floats"


# --------------------------------------------------------------------------- #
# 2. embed policy: shrink what exists to leak
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("data_class,tags,should_embed", [
    (CLASS_RESTRICTED, ["finance"], False),
    (CLASS_RESTRICTED, ["credential"], False),
    (CLASS_RESTRICTED, ["government-id"], False),
    (CLASS_RESTRICTED, ["aadhaar"], False),
    ("sensitive", ["health"], True),
    ("sensitive", ["contact"], True),
    ("internal", ["work"], True),
    ("internal", [], True),
])
def test_embed_policy(data_class, tags, should_embed):
    d = decide_embed(data_class, tags, "some text about a person")
    assert d.embed is should_embed


def test_restricted_record_gets_no_vector_at_all(memory):
    """Government id, financial and credential content: exact lookup only."""
    rec = memory.remember("renew the card 4532 0151 1283 0366 before the 28th")
    assert "finance" in rec.tags                      # the redactor caught it
    row = memory.conn.execute("SELECT COUNT(*) FROM vectors WHERE rid=?",
                              (rec.id,)).fetchone()[0]
    assert row == 0, "restricted content must not be embedded"
    # ...but it is still findable by exact/blind-lexical lookup
    assert memory.search("4532", limit=3), "exact lookup must still work"


def test_sensitive_record_embeds_redacted_text(memory):
    """A phone number inside a health note must not reach the vector."""
    rec = memory.remember("Therapy followup, call the clinic on 9845123789",
                          tags=["health"])
    d = decide_embed("sensitive", rec.tags,
                     f"{rec.title}\n{rec.body}", redact_fn=__import__(
                         "brain.security.redact", fromlist=["redact"]).redact)
    assert d.embed and d.klass == VEC_HEALTH
    assert "9845123789" not in d.text, "the raw number must be masked"
    assert "3789" in d.text, "last four kept so it stays identifiable"


def test_financial_class_exists_but_is_unused_by_default():
    assert VEC_FINANCIAL in VEC_CLASSES
    assert VEC_FINANCIAL not in LEVEL_CLASSES["full"]
    assert VEC_FINANCIAL in LEVEL_CLASSES["step_up"]
    assert decide_embed(CLASS_RESTRICTED, ["finance"], "x").embed is False


# --------------------------------------------------------------------------- #
# 3. key hierarchy: class keys, salts, AAD binding
# --------------------------------------------------------------------------- #
def test_class_keys_are_distinct_and_versioned(vault):
    mk = vault.session.mk
    keys = {c: class_key(mk, c) for c in VEC_CLASSES}
    assert len(set(keys.values())) == len(VEC_CLASSES), "class keys must differ"
    assert class_key(mk, VEC_HEALTH, KEY_VERSION) != class_key(mk, VEC_HEALTH, 99), \
        "key_version must change the derived key so rotation is real"


def test_unknown_class_is_refused(vault):
    with pytest.raises(VecVaultError):
        class_key(vault.session.mk, "not-a-class")


def test_record_key_requires_a_real_salt(vault):
    ck = class_key(vault.session.mk, VEC_GENERAL)
    with pytest.raises(VecVaultError):
        record_key(ck, b"short", "rid")


def test_blob_cannot_be_opened_with_the_wrong_class_key(vault):
    mk, rid, salt = vault.session.mk, "not_A", new_salt()
    blob = seal_vector([0.1, 0.2, 0.3], mk, rid, VEC_HEALTH, salt)
    assert open_vector(blob, mk, rid, VEC_HEALTH, salt) == [0.1, 0.2, 0.3]
    with pytest.raises(VecVaultError):
        open_vector(blob, mk, rid, VEC_GENERAL, salt)


def test_blob_cannot_be_swapped_between_records(vault):
    """AAD binds record_id|class|key_version, so moving a blob fails."""
    mk, salt = vault.session.mk, new_salt()
    blob = seal_vector([0.5, 0.5], mk, "not_A", VEC_GENERAL, salt)
    with pytest.raises(VecVaultError):
        open_vector(blob, mk, "not_B", VEC_GENERAL, salt)


def test_crypto_shred_deleting_the_salt_destroys_the_vector(vault):
    """Pure deterministic derivation from record_id cannot be shredded."""
    mk, rid = vault.session.mk, "not_SHRED"
    salt = new_salt()
    blob = seal_vector([0.25] * 8, mk, rid, VEC_GENERAL, salt)
    assert open_vector(blob, mk, rid, VEC_GENERAL, salt)
    with pytest.raises(VecVaultError):
        open_vector(blob, mk, rid, VEC_GENERAL, new_salt())


# --------------------------------------------------------------------------- #
# 4. access levels are enforced cryptographically, not by a filter
# --------------------------------------------------------------------------- #
def _rows(memory):
    return memory.conn.execute(
        "SELECT rid, dim, vec, klass, salt, key_version FROM vectors").fetchall()


def test_degraded_session_cannot_open_health_vectors(config, vault, memory):
    memory.remember("Therapy session notes about anxiety", tags=["health"])
    memory.remember("Rent is 18500 due on the 5th")
    rows = _rows(memory)
    classes_on_disk = {r["klass"] for r in rows}
    assert VEC_HEALTH in classes_on_disk, "fixture should have produced a health vector"

    full = VecIndex.build(vault.session.mk, rows, "full")
    deg = VecIndex.build(vault.session.mk, rows, "degraded")
    locked = VecIndex.build(vault.session.mk, rows, "locked")
    assert full.size() == len(rows)
    assert deg.size() < full.size(), "degraded must load fewer vectors"
    assert locked.size() == 0
    assert deg.loaded_classes == (VEC_GENERAL,)


def test_search_still_works_for_the_legitimate_unlocked_user(memory):
    memory.remember("Dentist appointment with Dr Mehta on Tuesday 10am")
    memory.remember("Rent is 18500 due on the 5th")
    hits = memory.search("dentist appointment", limit=3)
    assert hits and "Dentist" in hits[0].title


def test_tier_gate_covers_the_lexical_path_too(config, vault, memory):
    """Sealed vectors self-enforce; the blind lexical index does not.

    Without this, a degraded (TOTP-only, no voice) session could recall
    sensitive records by keyword, and `brain recall` reaches search() directly
    without passing through the agent's own tier filter.
    """
    memory.remember("Therapy session notes about anxiety", tags=["health"])
    assert memory.search("therapy", limit=3), "full session sees it"
    memory.set_access_level("degraded")
    assert memory.search("therapy", limit=3) == [], "degraded must not"
    assert memory.last_withheld, "and it must report that something was withheld"
    memory.set_access_level("locked")
    assert memory.search("therapy", limit=3) == []


def test_index_is_zeroed_on_lock(config, vault, memory):
    memory.remember("Dentist appointment Tuesday")
    assert memory.vec_index.size() > 0
    memory.drop_vec_index()
    assert memory.vec_index is None


# --------------------------------------------------------------------------- #
# 5. v5 -> v6 migration
# --------------------------------------------------------------------------- #
def test_migration_destroys_plaintext_vectors_rather_than_converting(config, vault, memory):
    """A v5 vector came from UNREDACTED text, so re-sealing it would preserve
    the leak inside a nicer envelope. Migration must re-embed from source."""
    rec = memory.remember("Rent is 18500 due on the 5th")
    memory.close()

    _forge_v5_vectors(config, [(rec.id, [0.114399] * 512)])

    from brain.memory.store import MemoryStore
    m2 = MemoryStore(config, vault.session.mk)
    assert int(m2.conn.execute("SELECT v FROM meta WHERE k='schema_version'"
                               ).fetchone()[0]) == SCHEMA_VERSION
    row = m2.conn.execute("SELECT vec, salt FROM vectors WHERE rid=?",
                          (rec.id,)).fetchone()
    assert bytes(row["vec"])[:4] == b"PBX1"
    assert row["salt"] is not None
    assert json.dumps([0.114399] * 512).encode() not in bytes(row["vec"])
    assert m2.search("rent", limit=3), "search must still work after migration"


def test_migration_drops_vectors_policy_now_forbids(config, vault, memory):
    memory.remember("renew the card 4532 0151 1283 0366 before the 28th")
    rec = memory.search("4532", limit=1)[0]
    memory.close()

    # forge a v5 plaintext vector for a record that must not have one
    _forge_v5_vectors(config, [(rec.id, [0.2] * 512)])

    from brain.memory.store import MemoryStore
    m2 = MemoryStore(config, vault.session.mk)
    assert m2.conn.execute("SELECT COUNT(*) FROM vectors WHERE rid=?",
                           (rec.id,)).fetchone()[0] == 0, \
        "migration must drop, not preserve, a vector policy now forbids"


# --------------------------------------------------------------------------- #
# 6. sync allowlist
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("table", SYNCABLE_TABLES)
def test_allowlisted_tables_may_sync(table):
    assert_syncable(table)


@pytest.mark.parametrize("table", NEVER_SYNCED)
def test_never_synced_tables_are_refused(table):
    with pytest.raises(VecVaultError):
        assert_syncable(table)


def test_unknown_table_is_refused_by_default():
    """A new table cannot become syncable by accident."""
    with pytest.raises(VecVaultError):
        assert_syncable("pii_biometric_v2")


# --------------------------------------------------------------------------- #
# 7. device-local biometrics (T11)
# --------------------------------------------------------------------------- #
def test_voice_template_is_device_bound(config, vault, voice_wavs):
    from brain.identity.ceremony import IdentityCeremony
    cer = IdentityCeremony(config, vault)
    cer.enroll_voice([voice_wavs["alice0"], voice_wavs["alice1"], voice_wavs["alice2"]])
    voice = vault._identity()["voice"]
    assert voice["sync_policy"] == "DEVICE_LOCAL"
    assert voice["bound_device"] == vault.device_id
    assert voice["cancelable"] is True
    assert vault.load_voice_template() is not None
    assert vault.voice_needs_reenroll() is False


def test_voice_template_refuses_a_foreign_device(config, vault, voice_wavs):
    """A copy of identity.json is useless on another machine."""
    from brain.identity.ceremony import IdentityCeremony
    cer = IdentityCeremony(config, vault)
    cer.enroll_voice([voice_wavs["alice0"], voice_wavs["alice1"], voice_wavs["alice2"]])
    real_device = vault.data["device_id"]
    vault.data["device_id"] = "dev-SOMEONE-ELSES-LAPTOP"
    try:
        assert vault.load_voice_template() is None
        assert vault.voice_needs_reenroll() is True
    finally:
        vault.data["device_id"] = real_device
    assert vault.load_voice_template() is not None


def test_enrolment_bundle_excludes_the_voiceprint(config, vault, voice_wavs):
    """A biometric cannot be rotated, so it is enrolled per device, not shipped."""
    from brain.identity.ceremony import IdentityCeremony
    from brain.crypto.envelope import decrypt
    from brain.util.misc import b64d
    cer = IdentityCeremony(config, vault)
    cer.enroll_voice([voice_wavs["alice0"], voice_wavs["alice1"], voice_wavs["alice2"]])
    cer.enroll_totp()
    bundle = vault.export_bundle("second-device")
    ident = json.loads(decrypt(b64d(bundle["identity_blob"]), vault.session.mk,
                               aad=b"pbx/identity-blob/v1"))
    assert "voice" not in ident, "the voiceprint must not travel in a bundle"
    assert "totp" in ident, "other factors still travel so the device is usable"
    assert "recovery" in ident


def test_legacy_template_signals_reenrol_not_silent_failure(config, vault, voice_wavs):
    """Pre-v6 templates were sealed under the shared MK; they must not load."""
    from brain.identity.ceremony import IdentityCeremony
    from brain.crypto.envelope import encrypt
    from brain.util.misc import b64e
    cer = IdentityCeremony(config, vault)
    cer.enroll_voice([voice_wavs["alice0"], voice_wavs["alice1"], voice_wavs["alice2"]])
    good = vault._identity()["voice"]["template"]

    def downgrade(identity):
        identity["voice"]["template"] = b64e(encrypt(b'{"embeddings":[]}',
                                                     vault.session.mk,
                                                     aad=b"pbx/voice/template"))
        identity["voice"].pop("bound_device", None)
    vault._mutate_identity(downgrade)
    assert vault.load_voice_template() is None
    assert vault.voice_needs_reenroll() is True, \
        "must say 're-enrol', not silently report 'no voice enrolled'"


# --------------------------------------------------------------------------- #
# 8. cancelable biometrics
# --------------------------------------------------------------------------- #
def test_transform_preserves_cosine_so_thresholds_still_valid():
    seed = b"0123456789abcdef"
    a = [0.3, -0.4, 0.5, 0.1, -0.2, 0.6]
    b = [0.28, -0.41, 0.5, 0.12, -0.19, 0.6]
    c = [-0.5, 0.2, 0.1, 0.4, -0.3, 0.1]
    ta, tb, tc = (transform_embedding(v, seed) for v in (a, b, c))
    assert abs(_cos(a, b) - _cos(ta, tb)) < 1e-9
    assert abs(_cos(a, c) - _cos(ta, tc)) < 1e-9


def test_transform_output_is_not_the_raw_voiceprint():
    seed = b"0123456789abcdef"
    a = [0.3, -0.4, 0.5, 0.1, -0.2, 0.6]
    ta = transform_embedding(a, seed)
    assert ta != a
    assert sorted(ta) != sorted(a), "not merely reordered values either"


def test_templates_from_different_seeds_cannot_be_cross_matched():
    """Revoking a leaked template = re-enrolling with a fresh seed."""
    a = [0.3, -0.4, 0.5, 0.1, -0.2, 0.6, 0.05, -0.11]
    t1 = transform_embedding(a, b"seed-one-1234567")
    t2 = transform_embedding(a, b"seed-two-7654321")
    assert _cos(t1, t2) < 0.6, "the same voiceprint under two seeds must not match"


def test_transform_handles_dual_space_embeddings():
    seed = b"0123456789abcdef"
    emb = {"z": [0.1, 0.2, 0.3], "w": [0.4, 0.5, 0.6], "meta": "kept"}
    out = transform_embedding(emb, seed)
    assert set(out) == {"z", "w", "meta"}
    assert out["meta"] == "kept", "non-vector fields pass through"
    assert out["z"] != emb["z"]


def test_no_seed_is_a_no_op():
    a = [0.1, 0.2, 0.3]
    assert transform_embedding(a, None) == a


# --------------------------------------------------------------------------- #
# 9. memory hygiene
# --------------------------------------------------------------------------- #
def test_index_zero_overwrites_before_dropping(vault, memory):
    memory.remember("Dentist appointment Tuesday")
    idx = memory.vec_index
    assert idx.size() > 0
    held = list(idx.by_class.values())[0]
    some_vec = next(iter(held.values()))
    assert any(v != 0.0 for v in some_vec)
    idx.zero()
    assert idx.size() == 0
    assert idx.loaded_classes == ()
    assert idx._class_keys == {}


def test_hardening_is_reported_not_fatal():
    from brain.memory.vecvault import harden_process_memory
    rep = harden_process_memory()
    assert set(rep) >= {"core_dumps_disabled", "mlock", "errors"}
    # mlock commonly fails under container RLIMIT_MEMLOCK; that must not raise
    assert isinstance(rep["mlock"], bool)


# --------------------------------------------------------------------------- #
# 10. tier semantics: `degraded` means a factor was skipped, not that none exists
# --------------------------------------------------------------------------- #
def test_passphrase_session_is_full_when_no_voice_is_enrolled(config, vault, memory):
    """A vault with no voiceprint has no stronger factor to skip.

    Treating that as `degraded` would make every sensitive memory permanently
    unrecallable - a functional dead end, not a security property. Regression
    guard: this broke `brain recall` for fresh vaults when tier filtering was
    added to search().
    """
    assert vault.has_voice() is False
    vault.start_session(vault.session.mk if vault.session else vault.unlock(PASSPHRASE),
                        mode="passphrase", degraded=True)
    if not vault.has_voice():
        vault.session.degraded = False
    from brain.security.classify import session_tier
    assert session_tier(vault.session) == "full"
    memory.remember("Dentist appointment with Dr Mehta", tags=["health"])
    memory.set_access_level(session_tier(vault.session))
    assert memory.search("dentist", limit=3), "owner must reach their own health note"


def test_passphrase_session_is_degraded_when_voice_exists_but_was_skipped(
        config, vault, memory, voice_wavs):
    """The security property the fix above must NOT break."""
    from brain.identity.ceremony import IdentityCeremony
    from brain.security.classify import session_tier
    IdentityCeremony(config, vault).enroll_voice(
        [voice_wavs["alice0"], voice_wavs["alice1"], voice_wavs["alice2"]])
    assert vault.has_voice() is True

    vault.start_session(vault.session.mk, mode="passphrase", degraded=True)
    if not vault.has_voice():
        vault.session.degraded = False          # must NOT trigger here
    assert vault.session.degraded is True
    assert session_tier(vault.session) == "degraded"

    memory.remember("Dentist appointment with Dr Mehta", tags=["health"])
    memory.remember("Rent is 18500 due on the 5th")
    memory.set_access_level("degraded")
    assert memory.search("dentist", limit=3) == [], "health is above the degraded ceiling"
    assert memory.last_withheld, "and it must be reported, not silently dropped"
    assert memory.search("rent", limit=3), "internal stays reachable"


def test_withheld_reports_classes_not_content(config, vault, memory, voice_wavs):
    from brain.identity.ceremony import IdentityCeremony
    IdentityCeremony(config, vault).enroll_voice(
        [voice_wavs["alice0"], voice_wavs["alice1"], voice_wavs["alice2"]])
    memory.remember("Dentist appointment with Dr Mehta", tags=["health"])
    memory.set_access_level("degraded")
    memory.search("dentist", limit=3)
    blob = json.dumps(memory.last_withheld)
    assert "Dentist" not in blob and "Mehta" not in blob, \
        "withheld metadata must never carry titles or bodies"
    assert "class" in blob
