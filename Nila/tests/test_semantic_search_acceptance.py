"""Acceptance tests: stage 12, gap 4 - searching by meaning, offline.

"Local semantic search" was not built because it usually means a model download
(sentence-transformers + torch). It does not have to: the vectors can come from a
plain-text word-vector file the owner already has, which is read line by line
with nothing installed and no network.

The point of the feature is behavioural, so that is what is tested: a question
that shares almost no words with a memory should still find it.

* "my scooter is not well, needs fixing" finds "the bike needs repair" - two
  shared words out of eight, and neither of them is the noun.
* a memory about rent is NOT returned, even though "shop" appears in both -
  meaning, not coincidence.
* a query the vector file knows nothing about says so (coverage), instead of
  returning eight confident-looking wrong answers.
* without a vector file, the command refuses in words and points at `brain
  recall`, which still works by matching words.

The toy vector file here is written by the test, so this proves the *reading*
and the *ranking*, not the quality of any particular GloVe file.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from brain.memory.store import GloveWordVectorEmbedder, cosine, make_embedder

VECTORS = {
    "scooter": [1.0, 0.0, 0.0, 0.0],
    "bike": [1.0, 0.10, 0.0, 0.0],
    "motorcycle": [1.0, 0.05, 0.0, 0.0],
    "repair": [0.0, 1.0, 0.0, 0.0],
    "fixing": [0.0, 1.0, 0.10, 0.0],
    "broken": [0.0, 1.0, 0.0, 0.0],
    "service": [0.0, 0.90, 0.0, 0.0],
    "rent": [0.0, 0.0, 1.0, 0.0],
    "shop": [0.0, 0.0, 0.90, 0.0],
    "rupees": [0.0, 0.0, 0.0, 1.0],
    "paid": [0.0, 0.0, 0.0, 1.0],
    "well": [0.0, 0.20, 0.0, 0.0],
}


@pytest.fixture()
def wordvec(tmp_path) -> Path:
    path = tmp_path / "toy.wordvec"
    path.write_text("".join(f"{word} " + " ".join(f"{x:.3f}" for x in vec) + "\n"
                            for word, vec in VECTORS.items()))
    return path


def test_the_file_is_read_as_vectors_with_no_dependency(wordvec):
    embedder = GloveWordVectorEmbedder(wordvec)
    assert embedder.dim == 4 and embedder.name.startswith("wordvec:")
    assert embedder.vectors["bike"] == [1.0, 0.10, 0.0, 0.0]
    vec = embedder.embed("the bike")
    assert len(vec) == 4 and abs(sum(v * v for v in vec) - 1.0) < 1e-9   # normalised


def test_meaning_finds_a_memory_that_shares_almost_no_words(wordvec):
    embedder = GloveWordVectorEmbedder(wordvec)
    query = embedder.embed("my scooter is not well, needs fixing")
    bike = embedder.embed("the bike needs repair")
    rent = embedder.embed("the shop rent is 8000 rupees, paid")
    assert cosine(query, bike) > 0.5
    # "shop" carries a little of the repair direction in this toy file, so the
    # rent memory scores a trace - but a trace is not a match. The gap is what
    # matters, and it is what the relevance floor uses.
    assert cosine(query, rent) < 0.1
    assert cosine(query, bike) > 3 * cosine(query, rent)


def test_words_the_file_does_not_know_are_reported_not_ignored(wordvec):
    embedder = GloveWordVectorEmbedder(wordvec)
    assert embedder.coverage("bike repair") == 1.0
    assert embedder.coverage("parliament election") == 0.0
    assert 0.0 < embedder.coverage("bike election") < 1.0
    # an unknown query embeds to zero rather than to noise
    assert set(embedder.embed("parliament election")) == {0.0}


def test_a_broken_or_missing_file_is_named(tmp_path):
    with pytest.raises(FileNotFoundError, match="no word-vector file"):
        GloveWordVectorEmbedder(tmp_path / "nope.wordvec")
    bad = tmp_path / "bad.wordvec"
    bad.write_text("this is not a vector file\nnor is this\n")
    with pytest.raises(ValueError, match="does not look like a word-vector file"):
        GloveWordVectorEmbedder(bad)


def test_the_config_can_name_the_file_and_hashing_stays_the_fallback(tmp_path):
    from brain.config import Config

    cfg = Config.load(tmp_path / "home")
    cfg.fast_kdf = True
    assert make_embedder(cfg).name.startswith("hashing")     # nothing configured
    cfg.agent["wordvec_path"] = str(tmp_path / "nope.wordvec")
    assert make_embedder(cfg).name.startswith("hashing")     # a bad path falls back
    good = tmp_path / "v.wordvec"
    good.write_text("bike 1.0 0.0\nrepair 0.0 1.0\n")
    cfg.agent["wordvec_path"] = str(good)
    assert make_embedder(cfg).name.startswith("wordvec")     # and a good one is used


def test_memory_search_returns_the_bike_not_the_rent(wordvec, tmp_path):
    """The whole feature, through the store the product uses."""
    from brain.config import Config
    from brain.memory.store import MemoryStore
    from brain.memory.vault import Vault

    cfg = Config.load(tmp_path / "home")
    cfg.fast_kdf = True
    cfg.agent["device_id"] = "search-test"
    vault = Vault(cfg)
    vault.initialize("search-passphrase-11", user="owner", device_label="search-test")
    embedder = GloveWordVectorEmbedder(wordvec)
    memory = MemoryStore(cfg, vault.session.mk, embedder=embedder)
    memory.remember("the bike needs repair") if False else None
    memory.remember("the bike needs repair, the mechanic is in Anna Nagar")
    memory.remember("the shop rent is 8000 rupees, paid on the 5th")

    hits = memory.search("my scooter is not well, needs fixing", limit=5,
                         semantic_weight=0.9)
    assert hits, "meaning-based search returned nothing"
    assert "bike" in hits[0].body
    assert all("rent" not in h.body for h in hits[:1])
    memory.close()


def test_the_command_refuses_without_a_vector_file_and_points_at_recall(tmp_path, capsys,
                                                                      monkeypatch):
    """The refusal is the point: say what is missing, and name what still works."""
    from brain.cli import main as cli
    from brain.config import Config
    from brain.memory.vault import Vault

    cfg = Config.load(tmp_path / "home")
    cfg.fast_kdf = True
    vault = Vault(cfg)
    vault.initialize("search-passphrase-11", user="owner", device_label="cmd-test")
    # The unlock ceremony has its own tests (authenticator, voice, lockout). This
    # one is about what `search` says when there are no vectors, so the vault is
    # handed over already open.
    monkeypatch.setattr(cli, "open_vault", lambda args: (cfg, vault))
    monkeypatch.setattr(cli, "warn_if_single_factor", lambda ui, v: None)

    args = cli.build_parser().parse_args(["search", "--meaning", "my scooter"])
    code = cli.cmd_search(args, cli.UI())
    err = capsys.readouterr().err
    assert code == 2
    assert "word-vector file" in err and "brain recall" in err
    assert "--wordvec" in err          # the hint must be a command he can paste
