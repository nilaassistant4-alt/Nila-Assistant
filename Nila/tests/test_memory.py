import json

import pytest

from brain.memory.store import HashingEmbedder, MemoryStore, cosine


def test_remember_and_get(memory):
    rec = memory.remember("Dr Meera is my cardiologist at Apollo", tags=["health", "doctor"],
                          kind="contact")
    got = memory.get(rec.id)
    assert got.body == "Dr Meera is my cardiologist at Apollo"
    assert got.kind == "contact"
    assert set(got.tags) == {"health", "doctor"}
    assert got.version == 1 and not got.deleted


def test_ciphertext_at_rest(memory, config):
    """Bodies, titles and metadata must never touch disk in the clear.

    Record ids *are* plaintext primary keys by design (they are needed for
    lookup and for the FTS index), but they carry no information about content.
    """
    rec = memory.remember("the launch code is PLUM-42-ORANGE", title="Launch codes",
                          tags=["sensitive"], meta={"note": "handle with care"})
    memory.close()
    raw = config.memory_path.read_bytes()
    for needle in (b"PLUM-42-ORANGE", b"launch code", b"Launch codes", b"handle with care"):
        assert needle not in raw, f"{needle!r} leaked to disk"
    assert rec.id.encode() in raw              # ids are plaintext keys by design


def test_update_bumps_version(memory):
    rec = memory.remember("first")
    updated = memory.update(rec.id, body="second")
    assert updated.version == 2
    assert memory.get(rec.id).body == "second"


def test_delete_tombstones_and_hard_delete(memory):
    rec = memory.remember("temporary")
    assert memory.delete(rec.id) is True
    tomb = memory.get(rec.id)
    assert tomb.deleted and tomb.body == ""
    assert rec.id not in [r.id for r in memory.list_records(limit=50)]
    assert memory.delete(rec.id, hard=True) is True
    assert memory.get(rec.id) is None


def test_search_finds_by_keyword(memory):
    memory.remember("My cardiologist is Dr Meera at Apollo Greams Road", tags=["health"])
    memory.remember("Home wifi password is in the blue notebook", tags=["home"])
    memory.remember("Monday team sync: roadmap, blockers, hiring", tags=["work"])
    hits = memory.search("cardiologist appointment")
    assert hits and "cardiologist" in hits[0].body.lower()
    hits = memory.search("wifi credentials")
    assert hits and "wifi" in hits[0].body.lower()


def test_suffix_folding_improves_recall(memory):
    memory.remember("The doctors recommended physiotherapy twice a week", tags=["health"])
    assert memory.search("doctor")            # singular query finds plural doc
    memory.remember("Standing meetings every Monday", tags=["work"])
    assert memory.search("meeting agenda")


def test_unmatched_query_returns_nothing_instead_of_noise(memory):
    """A personal brain must say 'I have nothing' rather than invent relevance."""
    memory.remember("My cardiologist is Dr Meera at Apollo Greams Road", tags=["health"])
    memory.remember("Home wifi password is in the blue notebook", tags=["home"])
    assert memory.search("zebraquartz nonexistent topic") == []
    assert memory.search("kubernetes deployment yaml") == []
    assert memory.search("passport expiry") == []


def test_scores_are_ordered_and_bounded(memory):
    for i in range(5):
        memory.remember(f"note about gardening tips number {i}", tags=["garden"])
    hits = memory.search("gardening tips", limit=5)
    assert hits
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_search_ranks_relevant_first(memory):
    memory.remember("Amma likes jasmine flowers and filter coffee without sugar", tags=["family"])
    for i in range(6):
        memory.remember(f"unrelated infrastructure note number {i} about kubernetes pods",
                        tags=["infra"])
    hits = memory.search("mothers flower and coffee preference", limit=3)
    assert "jasmine" in hits[0].body


def test_index_tokens_mode(config, vault):
    config.agent["search_index_tokens"] = "none"
    m = MemoryStore(config, vault.session.mk)
    rec = m.remember("a very distinctive phrase zebraquartz")
    assert m._lexical_search("zebraquartz", 5, None, None) == []     # nothing indexed
    assert m._semantic_search("zebraquartz phrase", 5, None, None)   # vectors still work
    m.close()


def test_tags_and_kind_filters(memory):
    memory.remember("a", tags=["work"], kind="task")
    memory.remember("b", tags=["home"], kind="note")
    assert len(memory.list_records(kind="task")) == 1
    assert len(memory.list_records(tag="home")) == 1


def test_stats(memory):
    memory.remember("one")
    memory.remember("two", kind="task")
    stats = memory.stats()
    assert stats["records"] == 2
    assert stats["kinds"]["task"] == 1
    assert stats["pending_sync"] == 2


def test_embedder_is_deterministic_and_normalised():
    e = HashingEmbedder(dim=256)
    a = e.embed("the cat sat on the mat")
    b = e.embed("the cat sat on the mat")
    assert a == b
    assert abs(sum(x * x for x in a) ** 0.5 - 1.0) < 1e-6
    assert cosine(a, e.embed("dogs running in a park")) < cosine(a, b)


def test_undecryptable_row_does_not_crash(memory, config, vault):
    rec = memory.remember("will be orphaned")
    raw = memory.conn.execute("SELECT body_enc FROM records WHERE id=?", (rec.id,)).fetchone()
    memory.conn.execute("UPDATE records SET body_enc=? WHERE id=?", (b"\x00" * 40, rec.id))
    memory.conn.commit()
    got = memory.get(rec.id)
    assert got is not None and got.meta.get("locked") is True
    assert memory.search("orphaned") is not None      # must not raise


def test_backup_is_ciphertext_and_verifiable(memory, vault):
    """The envelope is ciphertext AND its own integrity claim is checkable.

    Format 2 added a binary SQLite block after the sealed payload, so the two
    sections are split on the declared byte count rather than on a newline: the
    snapshot contains 0x0a, and partitioning on a newline silently cut it in the
    wrong place (the bug that first made attachment restore fail).
    """
    memory.remember("backup probe content")
    blob = memory.export_encrypted_backup()
    assert b"backup probe content" not in blob
    header, _, rest = blob.partition(b"\n")
    meta = json.loads(header)
    from brain.util.misc import sha256_hex

    assert meta["format"] == 2
    size = int(meta["bytes"])
    body = rest[:size]
    assert len(body) == size
    assert sha256_hex(body) == meta["sha256"]
    # and nothing plaintext leaked into the database block either
    assert b"backup probe content" not in rest[size:]


def test_lexical_overlap_survives_the_relevance_floor(memory):
    """Regression: the floor must gate semantic-only noise, not proven word overlap.

    "who is my heart doctor" shares the folded token `doctor` with a record that
    says "Dr Meera", but the combined score lands just under the floor because
    the lexical embedder has no concept of cardiology. Dropping it made Nila
    answer "no memory matched confidently" about something she *does* know.
    """
    memory.remember("My cardiologist is Dr Meera at Apollo Greams Road", tags=["health"])
    memory.remember("The doctors at the clinic recommended physiotherapy", tags=["health"])
    memory.remember("Home wifi password is in the blue notebook", tags=["home"])
    hits = memory.search("who is my heart doctor")
    assert hits, "a record sharing the token 'doctor' must not be discarded"
    bodies = " ".join(h.body.lower() for h in hits)
    assert "dr meera" in bodies or "doctors" in bodies
    assert "wifi" not in bodies, "unrelated records must still be kept out"


def test_abbreviation_folding(memory):
    memory.remember("My cardiologist is Dr Meera at Apollo Greams Road", tags=["health"])
    memory.remember("Netbanking password is in the KeePass vault under bank", tags=["finance"])
    assert memory.search("dr meera"), "dr -> doctor"
    assert memory.search("doctor meera"), "canonical form matches the abbreviation"
    assert memory.search("netbanking pwd"), "pwd -> password"


def test_relevance_floor_still_rejects_pure_noise(memory):
    """The exemption above must not reopen the noise hole."""
    memory.remember("My cardiologist is Dr Meera at Apollo Greams Road", tags=["health"])
    memory.remember("Home wifi password is in the blue notebook", tags=["home"])
    for q in ("zebraquartz nonexistent topic", "quantum baking recipes",
              "kubernetes deployment yaml", "passport expiry"):
        assert memory.search(q) == [], f"{q!r} should match nothing"
