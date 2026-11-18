"""Encrypted memory vault: SQLite + FTS5 + a small vector index.

Design rules
------------
* Every record body is encrypted with a *per-record* key derived from the
  master key (``Krec = HKDF(MK, "rec", record_id)``) so one compromised
  ciphertext leaks nothing about the others, and key rotation is cheap.
* Metadata used for search (tokens, tags, embeddings) is stored in separate
  tables. Token search uses SQLite FTS5 over *keyword tokens only* - the
  plaintext body never touches disk. ``store.search_index_tokens`` controls
  how much is indexed (``none`` | ``tags`` | ``keywords`` | ``full``).
* Every mutation writes a row to ``sync_journal``; the sync engine drains it.
* Embeddings come from a pluggable backend: a deterministic stdlib
  hashing/TF-IDF vectoriser by default, or ``sentence-transformers`` when
  installed.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import string
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..config import Config
from ..crypto.envelope import CryptoError, open_sealed, seal
from ..crypto.hash import fast_kdf
from ..security.redact import redact, scan
from ..security.classify import (MAX_INSTRUCTION_CHARS, classify, filter_for_tier,
                                 instruction_safety)
from .key import MasterKey
from .vecvault import (
    KEY_VERSION, VecIndex, VecVaultError, decide_embed, harden_process_memory,
    new_salt, open_vector, seal_vector,
)
from ..util.misc import b64e, dumps, ensure_dir, loads, new_id, now_ms, sha256_hex, slugify, try_import
from ..crypto.hash import mac

SCHEMA_VERSION = 6

#: A standing instruction is applied to EVERY local turn, so its number is
#: capped: past a handful they stop being rules and start being noise the model
#: has to ignore, which is where an injection would hide.
MAX_INSTRUCTIONS = 10  # the owner's acceptance contract: at most 10 standing orders


class InstructionError(ValueError):
    """Refused: either not an owner-driven creation, or an unsafe instruction."""
STOPWORDS = frozenset("""a an the and or but if then than that this these those of to in for on with
at by from as is are was were be been being it its i you he she we they them his her their our your
my me us so not no do does did will would can could should have has had about into over after before
very just also there here what when where who whom which why how all any some such""".split())
#: Content-token alphabet. Latin, Latin Extended, and the major Indic blocks.
#: The Tamil block (\u0b80-\u0bff) was MISSING while Devanagari was present, so a
#: Tamil note stored byte-perfectly and produced zero index tokens: it could be
#: listed but never recalled by keyword. Found by the mandate probe (M4), not by
#: the test suite - no test stored Tamil. Blocks here: Latin Extended, Devanagari,
#: Bengali, Gurmukhi, Gujarati, Oriya, Tamil, Telugu, Kannada, Malayalam.
TOKEN_RE = re.compile(
    r"[a-z0-9\u00c0-\u024f"        # latin + latin extended
    r"\u0900-\u097f"               # devanagari
    r"\u0980-\u09ff"               # bengali
    r"\u0a00-\u0a7f"               # gurmukhi
    r"\u0a80-\u0aff"               # gujarati
    r"\u0b00-\u0b7f"               # oriya
    r"\u0b80-\u0bff"               # tamil
    r"\u0c00-\u0c7f"               # telugu
    r"\u0c80-\u0cff"               # kannada
    r"\u0d00-\u0d7f"               # malayalam
    r"]+"
)

# SQLite's FTS5 'porter' tokenizer is only available in some builds, so we do
# our own conservative suffix folding at index *and* query time. This is what
# makes "doctors" find "doctor" and "meetings" find "meeting".
_SUFFIXES = ("ization", "isation", "ations", "ingly", "edly", "ings", "ies", "ing",
             "edly", "edly", "ment", "ness", "ers", "er", "ed", "ly", "es", "s")


# Common abbreviations folded to their canonical form so that a query for
# "doctor" finds a record that says "Dr Meera". Applied at index AND query time,
# which is what makes the blind index (equality-only) usable for this at all.
ABBREVIATIONS = {
    "dr": "doctor", "drs": "doctor", "prof": "professor", "mr": "mister",
    "mrs": "missus", "ms": "miss", "apt": "apartment", "approx": "approximately",
    "bday": "birthday", "dob": "birthdate", "pwd": "password", "passwd": "password",
    "ph": "phone", "tel": "phone", "mob": "mobile", "med": "medicine",
    "meds": "medicine", "rx": "prescription", "doc": "document",
    "docs": "document", "info": "information", "addr": "address",
    "imp": "important", "urg": "urgent", "appt": "appointment",
    "recd": "received", "wifi": "wifi", "usr": "user", "acct": "account",
    "acc": "account", "txn": "transaction", "amt": "amount", "dob": "birthdate",
}


def fold(token: str) -> str:
    """Conservative suffix folding + abbreviation expansion.

    Never folds below 3 characters, so short real words survive intact.
    """
    if token in ABBREVIATIONS:
        return ABBREVIATIONS[token]
    t = token
    for suf in _SUFFIXES:
        if len(t) - len(suf) >= 3 and t.endswith(suf):
            t = t[: -len(suf)]
            break
    if t.endswith("i") and len(t) > 3:      # "appli" <- "application"-ish residue
        t = t[:-1] + "y"
    return t


# --------------------------------------------------------------------------- #
# embeddings
# --------------------------------------------------------------------------- #
class HashingEmbedder:
    """Deterministic, dependency-free text embedding.

    Character n-grams + word unigrams/bigrams hashed into a fixed space, then
    sublinear TF and L2 normalisation. Not as good as a neural encoder, but it
    is stable across machines (so it can be computed on device A and compared
    on device B) and needs no downloads - important for an offline brain.
    """

    name = "hashing-tfidf-v1"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _tokens(self, text: str) -> List[str]:
        t = text.lower()
        words = [w for w in TOKEN_RE.findall(t) if w not in STOPWORDS]
        grams: List[str] = list(words)
        grams += [f"{words[i]}_{words[i+1]}" for i in range(len(words) - 1)]
        for w in words:
            pad = f"#{w}#"
            for n in (3, 4):
                grams += [pad[i:i + n] for i in range(max(0, len(pad) - n + 1))]
        return grams

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in self._tokens(text or ""):
            h = int(sha256_hex(("emb1|" + tok).encode())[:16], 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign
        for i, v in enumerate(vec):
            vec[i] = math.copysign(math.log1p(abs(v)), v) if v else 0.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_many(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


class GloveWordVectorEmbedder:
    """Real word vectors from a local GloVe/word2vec text file - no download.

    The owner already has the pieces this needs: a `.wordvec` file on disk (the
    50/100/200-d GloVe `.txt` files, one word and its numbers per line). Reading
    one is a few lines of Python and a dict, and a sentence becomes the mean of
    its words' vectors, normalised. That gives *semantic* retrieval ("the bike
    broke" finds "scooter repair") where the hashing embedder only gives lexical
    overlap, without adding torch, a model download, or a network call.

    Deliberately not whisper-networked: if the file is not there, `available()`
    is false and `make_embedder` falls back to hashing, saying so.
    """

    def __init__(self, path: str | Path, dim: int = 0, max_words: int = 400_000):
        self.path = Path(path).expanduser()
        self.vectors: Dict[str, List[float]] = {}
        self.dim = 0
        if not self.path.is_file():
            raise FileNotFoundError(f"no word-vector file at {self.path}")
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if len(self.vectors) >= max_words:
                    break
                parts = line.rstrip().split(" ")
                if len(parts) < 3:
                    continue
                word = parts[0]
                try:
                    vec = [float(x) for x in parts[1:]]
                except ValueError:
                    continue
                if self.dim and len(vec) != self.dim:
                    continue
                self.dim = len(vec)
                self.vectors[word.lower()] = vec
        if not self.vectors:
            raise ValueError(f"{self.path} does not look like a word-vector file "
                             f"(one word and its numbers per line)")
        self.name = f"wordvec:{self.path.name}:{self.dim}d"

    def _words(self, text: str) -> List[str]:
        return [w for w in TOKEN_RE.findall((text or "").lower())
                if len(w) > 1 and w not in STOPWORDS]

    def coverage(self, text: str) -> float:
        """How many of these words the file knows - 0 means say so, do not guess."""
        words = self._words(text)
        if not words:
            return 0.0
        return len([w for w in words if w in self.vectors]) / len(words)

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        used = 0
        for word in self._words(text):
            found = self.vectors.get(word)
            if found is None:
                continue
            used += 1
            for i, value in enumerate(found):
                vec[i] += value
        if not used:
            return [0.0] * self.dim
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_many(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


class SentenceTransformerEmbedder:
    """Optional neural embedder (``pip install personal-brain[semantic]``)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # type: ignore

        self.model = SentenceTransformer(model_name)
        self.name = f"st:{model_name}"
        self.dim = int(self.model.get_sentence_embedding_dimension())

    def embed(self, text: str) -> List[float]:
        return [float(x) for x in self.model.encode(text, normalize_embeddings=True)]

    def embed_many(self, texts: Sequence[str]) -> List[List[float]]:
        return [[float(x) for x in row] for row in self.model.encode(list(texts), normalize_embeddings=True)]


def make_embedder(config: Config):
    want = str(config.agent.get("embedder", "auto") or "auto").strip().lower()
    # A local vector file is tried FIRST: it is the owner's own file, needs no
    # download, and gives real meaning where hashing only gives shared words.
    path = str(config.agent.get("wordvec_path") or "").strip()
    if path and want in ("auto", "wordvec", "glove", "sentence-transformers"):
        try:
            return GloveWordVectorEmbedder(path)
        except (OSError, ValueError):
            if want in ("wordvec", "glove"):
                raise                     # asked for it by name: say it is broken
    if want in ("auto", "sentence-transformers"):
        if try_import("sentence_transformers") is not None:
            try:
                return SentenceTransformerEmbedder(config.agent.get("embed_model", "all-MiniLM-L6-v2"))
            except Exception:
                pass
    return HashingEmbedder(int(config.agent.get("embed_dim", 512)))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


# --------------------------------------------------------------------------- #
# record model
# --------------------------------------------------------------------------- #
@dataclass
class Record:
    id: str
    kind: str
    title: str
    tags: List[str] = field(default_factory=list)
    body: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    created_at: int = 0
    updated_at: int = 0
    version: int = 1
    deleted: bool = False
    score: float = 0.0

    def to_sync_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "tags": self.tags,
            "body": self.body, "meta": self.meta, "created_at": self.created_at,
            "updated_at": self.updated_at, "version": self.version, "deleted": self.deleted,
        }

    @classmethod
    def from_sync_dict(cls, data: Dict[str, Any]) -> "Record":
        return cls(
            id=data["id"], kind=data.get("kind", "note"), title=data.get("title", ""),
            tags=list(data.get("tags") or []), body=data.get("body", ""),
            meta=dict(data.get("meta") or {}), created_at=int(data.get("created_at", 0)),
            updated_at=int(data.get("updated_at", 0)), version=int(data.get("version", 1)),
            deleted=bool(data.get("deleted", False)),
        )

    def short(self, width: int = 90) -> str:
        body = " ".join((self.body or self.title).split())
        return body[: width - 1] + "…" if len(body) > width else body


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS records (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL DEFAULT 'note',
  title_enc    BLOB NOT NULL,
  body_enc     BLOB NOT NULL,
  meta_enc     BLOB NOT NULL,
  tags_enc     BLOB NOT NULL DEFAULT x'',
  tag_index    TEXT NOT NULL DEFAULT '',
  version      INTEGER NOT NULL DEFAULT 1,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL,
  deleted      INTEGER NOT NULL DEFAULT 0,
  sync_state   TEXT NOT NULL DEFAULT 'dirty'
);
CREATE INDEX IF NOT EXISTS idx_records_kind ON records(kind);
CREATE INDEX IF NOT EXISTS idx_records_updated ON records(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_records_sync ON records(sync_state);

-- Blind index: tokens are HMAC(Kidx, record_id, token), never plaintext.
-- This is what makes keyword search possible WITHOUT leaking your vocabulary
-- to disk (or to anyone who steals memory.db).
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
  rid UNINDEXED, tokens, tags, tokenize='unicode61'
);

-- Vectors are SEALED (v6). `vec` holds a PBX1 envelope, never plaintext
-- floats; `salt` is the random per-record salt that makes crypto-shredding
-- possible - delete the row and the record key is underivable forever.
-- `klass` selects the class key, so a session that has not loaded that class
-- key cannot open the blob at all.
CREATE TABLE IF NOT EXISTS vectors (
  rid         TEXT PRIMARY KEY,
  dim         INTEGER NOT NULL,
  vec         BLOB NOT NULL,
  backend     TEXT NOT NULL,
  updated_at  INTEGER NOT NULL,
  klass       TEXT NOT NULL DEFAULT 'general',
  salt        BLOB NOT NULL,
  key_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sync_journal (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  rid        TEXT NOT NULL,
  op         TEXT NOT NULL,
  at         INTEGER NOT NULL,
  pushed     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_journal_pending ON sync_journal(pushed, seq);

CREATE TABLE IF NOT EXISTS sync_state (
  rid            TEXT PRIMARY KEY,
  base_version   INTEGER NOT NULL DEFAULT 0,
  base_updated   INTEGER NOT NULL DEFAULT 0,
  remote_version INTEGER NOT NULL DEFAULT 0,
  last_synced    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


class MemoryStore:
    def __init__(self, config: Config, mk: bytes | bytearray | MasterKey, embedder=None,
                 access_level: str = "full", session: Any = None):
        """``session`` is the Vault session this store is opened for.

        When given, the access level is derived FROM the session and a degraded
        session can never open a ``full`` store (audit A2). Passing bare bytes
        keeps the old behaviour, because standalone imports and tests have no
        session - but every caller inside the app passes one.
        """
        self.config = config
        # A Vault session passes its shared mutable MasterKey. Bare bytes remain
        # supported for standalone imports/tests, but then this store owns and
        # zeroises its private key copy when closed.
        self._owns_key = not isinstance(mk, MasterKey)
        self._mk = mk if isinstance(mk, MasterKey) else MasterKey(mk)
        self._closed = False
        self.embedder = embedder or make_embedder(config)
        self._lock = threading.RLock()
        self._mk.require_active()
        ensure_dir(config.home)
        self.db_path = config.memory_path
        # check_same_thread=False + an explicit lock: the localhost API server is
        # threaded, and SQLite objects are thread-bound by default. All public
        # methods take self._lock, so access is serialized (WAL still gives us
        # crash safety and a single writer).
        self.conn = sqlite3.connect(str(self.db_path), timeout=15.0,
                                    check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self._mk.register_store(self)
        except Exception:
            self.conn.close()
            self._closed = True
            raise
        self.conn.executescript(SCHEMA)
        self._migrate_tags()
        self._init_meta()
        # Sealed-vector state. Build only the classes the session may access;
        # never load full-tier vectors and then drop them after construction.
        #
        # A DEGRADED SESSION CANNOT ESCALATE. Without this, an API re-login that
        # skipped the stronger factor still built a `full` store with the same
        # master key, so restricted rows were returned regardless of the tier the
        # rest of the app believed it was enforcing.
        self.session = session
        self.level = access_level
        if session is not None:
            if getattr(session, "degraded", False) and access_level == "full":
                self.level = "degraded"
            if getattr(session, "expired", lambda: False)():
                try:
                    self.conn.close()
                finally:
                    self._closed = True
                raise RuntimeError("memory store refused: the session has expired")
        self.last_withheld: List[Dict[str, Any]] = []
        self.vec_index: Optional[VecIndex] = None
        self._migrate_vectors()
        self.mem_hardening = harden_process_memory()
        self.load_vec_index()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("memory store is closed/locked")
        self._mk.require_active()

    @property
    def mk(self) -> MasterKey:
        self._ensure_open()
        return self._mk

    def _on_master_key_lock(self) -> None:
        """Called by the shared session key when its Vault is locked."""
        self.close()

    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # blind index
    # ------------------------------------------------------------------ #
    def _blind(self, rid: str, token: str) -> str:
        """HMAC a token under a per-record key -> an indexable opaque term.

        Deterministic for the same (record, token), so equality search works,
        but it reveals nothing about the token: no plaintext, no length, no
        frequency across records. The cost is that prefix/stem matching is
        impossible - which is why ``fold()`` is applied *before* blinding.
        """
        key = fast_kdf(self.mk, f"pbx/blind-index/v1|{rid}".encode(), 32)
        return b64e(mac(key, token.encode("utf-8")))[:24]

    def blind_tokens(self, rid: str, text: str) -> List[str]:
        out = []
        for tok in self.tokenize(text):
            out.append(self._blind(rid, tok))
            folded = fold(tok)
            if folded != tok:
                out.append(self._blind(rid, folded))
        return out

    def _migrate_tags(self) -> None:
        """schema 5: plaintext ``tags`` -> encrypted ``tags_enc`` + blind index.

        Runs with the master key available so existing records can be re-sealed,
        then drops the plaintext column.
        """
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(records)")}
        if "tags_enc" not in cols:
            self.conn.execute("ALTER TABLE records ADD COLUMN tags_enc BLOB NOT NULL DEFAULT x''")
        if "tag_index" not in cols:
            self.conn.execute("ALTER TABLE records ADD COLUMN tag_index TEXT NOT NULL DEFAULT ''")
        if "tags" not in cols:
            self.conn.commit()
            return
        moved = 0
        for row in self.conn.execute("SELECT id, tags, tags_enc FROM records").fetchall():
            if row["tags_enc"]:
                continue
            try:
                tags = loads(row["tags"] or "[]")
            except Exception:
                tags = []
            self.conn.execute("UPDATE records SET tags_enc=?, tag_index=? WHERE id=?",
                              (self._enc(row["id"], "tags", dumps(tags)),
                               self.blind_tag_index(row["id"], tags), row["id"]))
            moved += 1
        self.conn.commit()
        try:
            self.conn.execute("ALTER TABLE records DROP COLUMN tags")
            self.conn.commit()
        except sqlite3.OperationalError:
            self.conn.execute("UPDATE records SET tags='[]'")   # blank it out
            self.conn.commit()
        if moved:
            print(f"[brain] migrated {moved} record(s): tags are now encrypted at rest")

    def _init_meta(self) -> None:
        with self._lock:
            cur = self.conn.execute("SELECT v FROM meta WHERE k='schema_version'")
            row = cur.fetchone()
            if row is None:
                self.conn.execute("INSERT INTO meta(k,v) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
                self.conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('created_at',?)", (str(now_ms()),))
                self.conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('embed_backend',?)", (self.embedder.name,))
                self.conn.commit()
            elif int(row["v"]) > SCHEMA_VERSION:
                raise RuntimeError(f"memory db schema {row['v']} newer than this build ({SCHEMA_VERSION})")

    def _migrate_vectors(self) -> None:
        """v5 -> v6: destroy plaintext vectors and re-embed them sealed.

        This deliberately does NOT convert the old vectors. A v5 vector was
        computed from *unredacted* text and stored as plaintext JSON under a
        public deterministic embedder, so re-sealing the same numbers would
        preserve the leak inside a nicer envelope. Re-embedding from the record
        lets the current policy decide again - which drops restricted records
        entirely and embeds sensitive ones from their redacted text.

        Rows whose class this session may not open are still migrated on disk
        (the migration runs with the MK in hand, at unlock) and simply are not
        loaded into the index.
        """
        with self._lock:
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(vectors)")}
            if "salt" not in cols:
                # a pre-v6 table: add the columns before touching any row
                self.conn.execute("ALTER TABLE vectors ADD COLUMN klass TEXT "
                                  "NOT NULL DEFAULT 'general'")
                self.conn.execute("ALTER TABLE vectors ADD COLUMN salt BLOB")
                self.conn.execute("ALTER TABLE vectors ADD COLUMN key_version "
                                  "INTEGER NOT NULL DEFAULT 1")
                cols |= {"klass", "salt", "key_version"}

            rows = self.conn.execute("SELECT rid, salt FROM vectors").fetchall()
            legacy = [r["rid"] for r in rows if not r["salt"]]
            if not legacy:
                self._set_schema_version()
                return

            # every legacy row goes: its numbers are not worth preserving
            self.conn.execute("DELETE FROM vectors WHERE salt IS NULL")
            self.conn.commit()

            resealed = dropped = missing = 0
            for rid in legacy:
                rec = self.get(rid)
                if rec is None or rec.deleted:
                    missing += 1
                    continue
                before = self.conn.execute(
                    "SELECT COUNT(*) FROM vectors WHERE rid=?", (rid,)).fetchone()[0]
                self._embed_record(rec)
                after = self.conn.execute(
                    "SELECT COUNT(*) FROM vectors WHERE rid=?", (rid,)).fetchone()[0]
                if after > before:
                    resealed += 1
                else:
                    dropped += 1     # policy now says: do not embed this at all
            self._set_schema_version()
            print(f"[brain] migrated {len(legacy)} plaintext vector(s) to sealed "
                  f"storage: {resealed} re-embedded, {dropped} dropped by policy "
                  f"(restricted content is not embedded), {missing} record(s) gone")

    def _set_schema_version(self) -> None:
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO meta(k,v) "
                              "VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            # zero the in-RAM plaintext vectors before releasing anything
            self.drop_vec_index()
            try:
                self.conn.commit()
                self.conn.close()
            except Exception:
                pass
            finally:
                self._closed = True
                self._mk.unregister_store(self)
                if self._owns_key:
                    self._mk.zeroize()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # crypto helpers
    # ------------------------------------------------------------------ #
    def _enc(self, rid: str, domain: str, plaintext: str) -> bytes:
        return seal(plaintext.encode("utf-8"), self.mk, domain, rid)

    def _dec(self, rid: str, domain: str, blob: bytes) -> str:
        return open_sealed(blob, self.mk, domain, rid).decode("utf-8")

    def _dec_tags(self, rid: str, row) -> List[str]:
        try:
            return loads(self._dec(rid, "tags", row["tags_enc"]) or "[]")
        except Exception:
            return []

    def blind_tag_index(self, rid: str, tags: Iterable[str]) -> str:
        """Opaque per-record tag index so filtering works without plaintext.

        A stolen ``memory.db`` must not reveal that you keep records tagged
        ``health`` or ``finances``. Blinding is deterministic per
        (record, tag), so equality filtering still works, and nothing else
        about the tag - name, length, or cross-record frequency - leaks.
        """
        return " ".join(dict.fromkeys(self._blind(rid, f"tag:{slugify(t)}")
                                      for t in tags if str(t).strip()))

    # ------------------------------------------------------------------ #
    # indexing helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def tokenize(text: str, fold_tokens: bool = True) -> List[str]:
        """Unique content tokens, optionally suffix-folded."""
        raw = [t for t in TOKEN_RE.findall((text or "").lower())
               if len(t) > 1 and t not in STOPWORDS]
        toks = [fold(t) if fold_tokens else t for t in raw]
        seen, out = set(), []
        for t in toks:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out[:400]

    def _index_payload(self, rec: "Record") -> str:
        """Build the *blinded* index payload for a record.

        ``search_index_tokens``:
          ``none``     no keyword index at all (semantic vectors only)
          ``tags``     blind the tags only
          ``keywords`` (default) blind title + body content tokens
          ``full``     same as keywords, but keeps duplicates so BM25 term
                       frequency is meaningful (slightly larger index)
        """
        mode = str(self.config.agent.get("search_index_tokens", "keywords")).lower()
        if mode == "none":
            return ""
        if mode == "tags":
            return " ".join(self._blind(rec.id, t) for t in rec.tags)
        text = f"{rec.title} {rec.body}"
        tokens = self.tokenize(text)
        if mode == "full":
            tokens = [t for t in TOKEN_RE.findall(text.lower())
                      if len(t) > 1 and t not in STOPWORDS]
        blinded = []
        for tok in tokens:
            blinded.append(self._blind(rec.id, tok))
            folded = fold(tok)
            if folded != tok:
                blinded.append(self._blind(rec.id, folded))
        return " ".join(dict.fromkeys(blinded))

    def _index_tag_payload(self, rec: "Record") -> str:
        return " ".join(self._blind(rec.id, t) for t in rec.tags)

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    def remember(
        self,
        body: str,
        title: str = "",
        kind: str = "note",
        tags: Optional[Iterable[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
        rid: Optional[str] = None,
        embed: bool = True,
        owner_instruction: bool = False,
    ) -> Record:
        with self._lock:
            body = (body or "").strip()
            if str(kind).strip().lower() == "instruction":
                # Instructions are OWNER-ONLY. Every writer funnels through
                # ``remember``, and the model's `remember` tool, a file, a web
                # page or a tool result all reach it too. A standing rule is
                # applied to every later local turn, so it may only be created
                # by a path a human drove: ``owner_instruction=True`` is set by
                # `brain instruct`, the dashboard panel and nothing else.
                if not owner_instruction:
                    raise InstructionError(
                        "a standing instruction can only be created by the owner "
                        "(brain instruct, or the dashboard panel). Content from "
                        "files, web pages, tools or the model is refused.")
                ok, why = instruction_safety(body or title)
                if not ok:
                    raise InstructionError(why)
                existing = self._instruction_rows()
                if len(existing) >= MAX_INSTRUCTIONS:
                    raise InstructionError(
                        f"the instruction limit is {MAX_INSTRUCTIONS}; delete one in the "
                        f"dashboard before adding another")
            if not body and not title:
                raise ValueError("nothing to remember")
            rid = rid or new_id(kind[:3])
            tags = [slugify(t) for t in (tags or []) if str(t).strip()][:32]

            # Secret detection on the WRITE path. A card number typed into an
            # untagged note would otherwise classify as `internal` and be
            # permitted to a cloud model; detecting it here escalates the record
            # to `restricted` and tags it, so the egress gate never has to guess.
            found = scan(f"{title} {body}")
            # NOTE: lexicon hits (health/legal topics) carry NO findings - they
            # tag without masking. Guarding on `found.findings` alone silently
            # dropped every keyword-only detection, so "the biopsy came back
            # benign" stayed `internal`. The guard must cover both.
            if found.findings or found.keyword_tags:
                for t in found.tags():
                    if t not in tags:
                        tags.append(t)
                tags = tags[:32]
                escalated = found.max_class
                if escalated and meta is not None:
                    meta.setdefault("data_class", escalated)
                    meta.setdefault("detected", found.names)
                elif escalated:
                    meta = {"data_class": escalated, "detected": found.names}

            title = title or self._auto_title(body)
            ts = now_ms()
            rec = Record(id=rid, kind=kind, title=title, tags=tags, body=body,
                         meta=dict(meta or {}), created_at=ts, updated_at=ts, version=1)
            self.conn.execute(
                """INSERT INTO records(id,kind,title_enc,body_enc,meta_enc,tags_enc,tag_index,
                                       version,created_at,updated_at,deleted,sync_state)
                   VALUES(?,?,?,?,?,?,?,?,?,?,0,'dirty')""",
                (rid, kind, self._enc(rid, "title", title), self._enc(rid, "body", body),
                 self._enc(rid, "meta", dumps(rec.meta)), self._enc(rid, "tags", dumps(tags)),
                 self.blind_tag_index(rid, tags), 1, ts, ts),
            )
            self._index(rec)
            if embed:
                self._embed_record(rec)
            self._journal(rid, "upsert")
            self.conn.commit()
            return rec

    def update(self, rid: str, body: Optional[str] = None, title: Optional[str] = None,
               tags: Optional[Iterable[str]] = None, meta: Optional[Dict[str, Any]] = None) -> Record:
        with self._lock:
            rec = self.get(rid)
            if rec is None:
                raise KeyError(f"no such record: {rid}")
            if body is not None:
                rec.body = body.strip()
                found = scan(f"{rec.title} {rec.body}")
                if found.findings or found.keyword_tags:
                    for t in found.tags():
                        if t not in rec.tags:
                            rec.tags.append(t)
                    if found.max_class:
                        rec.meta.setdefault("data_class", found.max_class)
                        rec.meta.setdefault("detected", found.names)
            if title is not None:
                rec.title = title
            if tags is not None:
                rec.tags = [slugify(t) for t in tags if str(t).strip()][:32]
            if meta:
                rec.meta.update(meta)
            rec.updated_at = now_ms()
            rec.version += 1
            self.conn.execute(
                """UPDATE records SET title_enc=?, body_enc=?, meta_enc=?, tags_enc=?,
                                      tag_index=?, version=?, updated_at=?,
                                      sync_state='dirty', deleted=0 WHERE id=?""",
                (self._enc(rid, "title", rec.title), self._enc(rid, "body", rec.body),
                 self._enc(rid, "meta", dumps(rec.meta)),
                 self._enc(rid, "tags", dumps(rec.tags)),
                 self.blind_tag_index(rid, rec.tags), rec.version, rec.updated_at, rid),
            )
            self._index(rec)
            self._embed_record(rec)
            self._journal(rid, "upsert")
            self.conn.commit()
            return rec

    # ------------------------------------------------------------------ #
    # standing instructions (owner-only)
    # ------------------------------------------------------------------ #
    def _instruction_rows(self) -> List[Record]:
        rows = self.list_records(kind="instruction", limit=MAX_INSTRUCTIONS + 50)
        return [r for r in rows if not getattr(r, "deleted", False)]

    def instructions(self) -> List[Record]:
        """Every live standing instruction, oldest first.

        Not a search: instructions are applied wholesale, so ordering is by
        creation and not by relevance to the current question.
        """
        with self._lock:
            rows = self._instruction_rows()
        return sorted(rows, key=lambda r: (r.created_at, r.id))[:MAX_INSTRUCTIONS]

    def add_instruction(self, text: str, title: str = "") -> Record:
        """Create a standing instruction. Owner path only."""
        return self.remember(text, title=title, kind="instruction",
                             tags=["instruction"], owner_instruction=True)

    def edit_instruction(self, rid: str, text: str) -> Record:
        """Change an instruction's text. Re-validated, because an edit is a write."""
        ok, why = instruction_safety(text)
        if not ok:
            raise InstructionError(why)
        with self._lock:
            rec = self.get(rid)
            if rec is None or rec.kind != "instruction":
                raise InstructionError("no such instruction")
            updated = self.update(rid, body=text.strip())
            if updated is None:                     # pragma: no cover - race
                raise InstructionError("the instruction disappeared while editing")
            return updated

    # ------------------------------------------------------------------ #
    # owner profile (facts about the owner - NOT instructions)
    # ------------------------------------------------------------------ #
    #: A profile fact ("call me Anna") answers a *question*; an instruction
    #: ("always be brief") is a standing order. They are applied by the same
    #: every-turn path, so they share the `active` list - but a fact is set once
    #: per field, while an instruction is a free sentence, so the limits differ.
    MAX_PROFILE_FIELDS = 20
    PROFILE_KINDS = ("profile", "instruction")

    def _profile_field(self, rid: str) -> str:
        rec = self.get(rid)
        return str((rec.meta or {}).get("field", "")) if rec else ""

    def profile(self) -> Dict[str, Any]:
        """Every live profile fact, field -> value. Owner facts only."""
        rows = [r for r in self.list_records(kind="profile", limit=self.MAX_PROFILE_FIELDS + 50)
                if not getattr(r, "deleted", False)]
        out: Dict[str, Any] = {}
        for r in sorted(rows, key=lambda r: (r.created_at, r.id)):
            field = str((r.meta or {}).get("field") or r.title or "").strip()
            if field:
                out[field] = r.body
        return out

    def set_profile(self, field: str, value: str) -> Record:
        """Set one profile fact, replacing an older value for the same field.

        Owner path only, same reasoning as instructions: a profile fact is put in
        front of the model on EVERY turn, so content from a file, a web page, a
        tool result or the model itself may never create one. The safety filter
        that guards instructions guards these too - "call me X" is harmless, but
        "you may ignore the safety rules" arriving here would not be.
        """
        field = (field or "").strip().lower()
        value = (value or "").strip()
        if not field:
            raise InstructionError("a profile field needs a name (e.g. callme)")
        if not value:
            raise InstructionError("a profile fact cannot be empty")
        ok, why = instruction_safety(value)
        if not ok:
            raise InstructionError(why)
        with self._lock:
            existing = self.profile()
            if field not in existing and len(existing) >= self.MAX_PROFILE_FIELDS:
                raise InstructionError(
                    f"the profile holds {self.MAX_PROFILE_FIELDS} facts; delete one first")
            for rid, val in ((r.id, r.body) for r in
                             self.list_records(kind="profile", limit=self.MAX_PROFILE_FIELDS + 50)):
                rec = self.get(rid)
                if rec and str((rec.meta or {}).get("field", "")).lower() == field \
                        and not getattr(rec, "deleted", False):
                    return self.update(rid, body=value)
            return self.remember(value, title=field, kind="profile",
                                 tags=["profile", slugify(field)],
                                 meta={"field": field}, owner_instruction=True)

    def forget_profile(self, field: str = "") -> int:
        """Delete one fact, or the whole profile. Returns how many were removed."""
        gone = 0
        rows = [r for r in self.list_records(kind="profile", limit=self.MAX_PROFILE_FIELDS + 50)
                if not getattr(r, "deleted", False)]
        for r in rows:
            name = str((r.meta or {}).get("field", "")).lower()
            if not field or name == field.strip().lower():
                if self.delete(r.id, hard=True):
                    gone += 1
        return gone

    def remove_instruction(self, rid: str, hard: bool = True) -> bool:
        with self._lock:
            rec = self.get(rid)
            if rec is None or rec.kind != "instruction":
                return False
            return self.delete(rid, hard=hard)

    def delete(self, rid: str, hard: bool = False) -> bool:
        with self._lock:
            rec = self.get(rid)
            if rec is None:
                return False
            if hard:
                self.conn.execute("DELETE FROM records WHERE id=?", (rid,))
                self.conn.execute("DELETE FROM records_fts WHERE rid=?", (rid,))
                self.conn.execute("DELETE FROM vectors WHERE rid=?", (rid,))
                self.conn.execute("DELETE FROM sync_state WHERE rid=?", (rid,))
                self.conn.execute("DELETE FROM sync_journal WHERE rid=?", (rid,))
            else:
                rec.deleted = True
                rec.updated_at = now_ms()
                rec.version += 1
                rec.body = ""
                rec.tags = []
                rec.title = "[deleted]"
                rec.meta["deleted_at"] = rec.updated_at
                self.conn.execute(
                    """UPDATE records SET title_enc=?, body_enc=?, meta_enc=?, tags_enc=?,
                                           tag_index='', version=?, updated_at=?, deleted=1,
                                           sync_state='dirty' WHERE id=?""",
                    (self._enc(rid, "title", rec.title), self._enc(rid, "body", ""),
                     self._enc(rid, "meta", dumps(rec.meta)), self._enc(rid, "tags", "[]"),
                     rec.version, rec.updated_at, rid),
                )
                self._index(rec)
                self.conn.execute("DELETE FROM vectors WHERE rid=?", (rid,))
                self._journal(rid, "delete")
            self.conn.commit()
            return True

    def get(self, rid: str) -> Optional[Record]:
        with self._lock:
            self._ensure_open()
            row = self.conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
            return self._row_to_record(row) if row else None

    def _row_to_record(self, row: sqlite3.Row) -> Optional[Record]:
        rid = row["id"]
        try:
            title = self._dec(rid, "title", row["title_enc"])
            body = self._dec(rid, "body", row["body_enc"])
            meta = loads(self._dec(rid, "meta", row["meta_enc"]))
        except (CryptoError, ValueError):
            # Undecryptable row (e.g. restored from another device's key): keep
            # the envelope so it can still be synced, but never crash.
            return Record(id=rid, kind=row["kind"], title="[undecryptable]",
                          tags=[], body="", meta={"locked": True},
                          created_at=row["created_at"], updated_at=row["updated_at"],
                          version=row["version"], deleted=bool(row["deleted"]))
        return Record(id=rid, kind=row["kind"], title=title, tags=self._dec_tags(rid, row),
                      body=body, meta=meta, created_at=row["created_at"],
                      updated_at=row["updated_at"], version=row["version"],
                      deleted=bool(row["deleted"]))

    def _auto_title(self, body: str) -> str:
        first = " ".join((body or "").split())
        return first[:64] + ("…" if len(first) > 64 else "") or "untitled"

    def _index(self, rec: Record) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM records_fts WHERE rid=?", (rec.id,))
            payload = self._index_payload(rec)
            if payload:
                self.conn.execute("INSERT INTO records_fts(rid,tokens,tags) VALUES(?,?,?)",
                                  (rec.id, payload, self.blind_tag_index(rec.id, rec.tags)))

    def _embed_record(self, rec: Record) -> None:
        """Compute and SEAL a vector, or deliberately create none.

        Restricted content (government id, financial, credential) is never
        embedded - those records stay reachable by exact lookup and the blind
        lexical index, but carry no vector to invert. Sensitive content is
        embedded from its REDACTED text, so a card number inside a health note
        never reaches the vector.
        """
        with self._lock:
            text = f"{rec.title}\n{rec.body}\n{' '.join(rec.tags)}"
            data_class = classify(rec.kind, rec.tags, rec.meta).klass
            decision = decide_embed(data_class, rec.tags, text, redact_fn=redact)
            if not decision.embed:
                self.conn.execute("DELETE FROM vectors WHERE rid=?", (rec.id,))
                if self.vec_index is not None:
                    self.vec_index.forget(rec.id)
                return
            vec = self.embedder.embed(decision.text)
            salt = new_salt()
            blob = seal_vector(vec, self.mk, rec.id, decision.klass, salt)
            self.conn.execute(
                "INSERT OR REPLACE INTO vectors"
                "(rid,dim,vec,backend,updated_at,klass,salt,key_version) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (rec.id, len(vec), blob, self.embedder.name, now_ms(),
                 decision.klass, salt, KEY_VERSION),
            )
            if self.vec_index is not None:
                self.vec_index.put(rec.id, decision.klass, vec)

    def set_access_level(self, level: str) -> Dict[str, Any]:
        """Rebuild the in-RAM index for a new access level.

        Dropping to a lower level zeroes the vectors that are no longer
        permitted; raising it decrypts only the classes the new level allows.
        """
        with self._lock:
            self.level = level
            return self.load_vec_index()

    def load_vec_index(self) -> Dict[str, Any]:
        """Decrypt the permitted vectors into RAM. Called at unlock."""
        with self._lock:
            if self.vec_index is not None:
                self.vec_index.zero()
            rows = self.conn.execute(
                "SELECT rid, dim, vec, klass, salt, key_version FROM vectors").fetchall()
            self.vec_index = VecIndex.build(self.mk, rows, self.level)
            return {"level": self.level,
                    "classes": list(self.vec_index.classes_loaded()),
                    "vectors_loaded": self.vec_index.size(),
                    "vectors_skipped": getattr(self.vec_index, "skipped", 0),
                    "rows_on_disk": len(rows)}

    def drop_vec_index(self) -> None:
        """Zero and release the in-RAM index. Called at lock."""
        with self._lock:
            if self.vec_index is not None:
                self.vec_index.zero()
                self.vec_index = None

    def _journal(self, rid: str, op: str) -> None:
        with self._lock:
            self.conn.execute("INSERT INTO sync_journal(rid,op,at,pushed) VALUES(?,?,?,0)",
                              (rid, op, now_ms()))

    # ------------------------------------------------------------------ #
    # retrieval
    # ------------------------------------------------------------------ #
    def list_records(self, kind: Optional[str] = None, tag: Optional[str] = None,
                     limit: int = 25, include_deleted: bool = False,
                     before: Optional[int] = None) -> List[Record]:
        with self._lock:
            self._ensure_open()
            q = "SELECT * FROM records WHERE 1=1"
            args: List[Any] = []
            if not include_deleted:
                q += " AND deleted=0"
            if isinstance(kind, (list, tuple, set)):
                kinds = [str(k) for k in kind]
                q += f" AND kind IN ({','.join('?' * len(kinds))})"
                args.extend(kinds)
            elif kind:
                q += " AND kind=?"
                args.append(kind)
            if before:
                q += " AND created_at<?"
                args.append(int(before))
            q += " ORDER BY updated_at DESC LIMIT ?"
            args.append(int(limit))
            rows = self.conn.execute(q, args).fetchall()
            out = [r for r in (self._row_to_record(x) for x in rows) if r]
            if tag:
                # tags are encrypted, so filter on the decrypted records.
                wanted = slugify(tag)
                out = [r for r in out if wanted in r.tags]
            return out

    def search(self, query: str, limit: int = 8, kind: Optional[str] = None,
               tag: Optional[str] = None, semantic_weight: float = 0.55,
               min_relevance: Optional[float] = None) -> List[Record]:
        """Hybrid lexical (FTS5/BM25) + semantic (cosine) retrieval.

        Candidates below ``min_relevance`` are dropped rather than returned as
        noise: a personal brain should say "I have nothing on that" instead of
        surfacing an unrelated memory with a plausible-looking score. The
        default floor comes from ``config.agent['min_relevance']``.
        """
        with self._lock:
            self._ensure_open()
            query = (query or "").strip()
            if not query:
                return []
            if min_relevance is None:
                min_relevance = float(self.config.agent.get("min_relevance", 0.12))
            lexical = self._lexical_search(query, limit * 4, kind, tag)
            semantic = [(rid, max(0.0, sc)) for rid, sc in
                        self._semantic_search(query, limit * 4, kind, tag)]

            # Lexical: BM25 is unbounded, so min-max normalise within the hit
            # set, then require genuine folded-token overlap with the document.
            # That gate is what stops a one-off prefix coincidence (e.g. "who*"
            # hitting "wifi") from looking like a confident match.
            lex_best = max((sc for _, sc in lexical), default=0.0) or 1.0
            scores: Dict[str, float] = {}
            order: Dict[str, int] = {}
            for rank, (rid, sc) in enumerate(lexical):
                rec = self.get(rid)
                if rec is None or rec.deleted:
                    continue
                # _lexical_search already proves genuine blinded-token overlap,
                # so no extra gate is needed here.
                scores[rid] = scores.get(rid, 0.0) + (1.0 - semantic_weight) * (
                    sc / lex_best) * (1.0 / (1 + rank * 0.05))
                order[rid] = rank

            # Semantic: cosine is already bounded and meaningful in absolute
            # terms, so it is deliberately NOT renormalised - dividing by the
            # best candidate would turn pure noise into a confident 1.0 when
            # nothing actually matches.
            for i, (rid, sc) in enumerate(semantic):
                scores[rid] = scores.get(rid, 0.0) + semantic_weight * sc * (
                    1.0 / (1 + i * 0.05))
                order.setdefault(rid, i)

            # Which records proved genuine token overlap with the query?
            lexically_matched = {rid for rid, _ in lexical}

            ranked = sorted(scores.items(), key=lambda kv: (-kv[1], order[kv[0]]))[:limit]
            out: List[Record] = []
            for rid, score in ranked:
                # The floor gates SEMANTIC-ONLY candidates - that is where pure
                # noise lives (a 0.02 cosine to everything in the store). A
                # record that shares a real folded token with the query has
                # already proven relevance, so it is kept regardless of score;
                # the agent is told separately when the score is weak.
                if score < min_relevance and rid not in lexically_matched:
                    continue
                rec = self.get(rid)
                if rec and not rec.deleted:
                    rec.score = round(score, 4)
                    out.append(rec)

            # Tier filtering must cover BOTH retrieval paths. Sealed vectors
            # already enforce it cryptographically - a session that never loaded
            # a class key cannot decrypt those rows - but the blind lexical index
            # is not key-scoped. Without this, a session with a skipped stronger
            # factor could still recall sensitive records by keyword, and
            # `brain recall` reaches this method directly without ever passing
            # through the agent's own filter.
            permitted, withheld = filter_for_tier(out, self.level)
            self.last_withheld = withheld
            return permitted

    def _lexical_search(self, query: str, limit: int, kind: Optional[str],
                        tag: Optional[str]) -> List[Tuple[str, float]]:
        """Keyword search over the *blind* index.

        Query tokens are folded and HMAC'd the same way the documents were, so
        matching is exact-term only (no prefix, no stemming at query time beyond
        ``fold``). BM25 still ranks by blinded term frequency.
        """
        terms = self.tokenize(query)
        if not terms:
            terms = [w.strip(string.punctuation).lower() for w in query.split() if w.strip()]
        if not terms:
            return []

        # a query token can only match records that share it, and the blind is
        # per-record, so we must evaluate each candidate record's key. That is
        # O(records) per query - acceptable for a personal brain (thousands of
        # records), and it is the price of not leaking a global vocabulary.
        rows = self.conn.execute(
            "SELECT id, kind, tag_index, deleted FROM records").fetchall()
        wanted = {t for t in terms if len(t) >= 2}
        scored: List[Tuple[str, float]] = []
        for row in rows:
            if row["deleted"]:
                continue
            if kind and row["kind"] != kind:
                continue
            rid = row["id"]
            if tag and self._blind(rid, f"tag:{slugify(tag)}") not in (
                    row["tag_index"] or "").split():
                continue
            index_row = self.conn.execute(
                "SELECT tokens, tags FROM records_fts WHERE rid=?", (rid,)).fetchone()
            if not index_row:
                continue
            hay = set((index_row["tokens"] or "").split()) | set((index_row["tags"] or "").split())
            if not hay:
                continue
            hits = 0
            for term in wanted:
                if self._blind(rid, term) in hay or self._blind(rid, fold(term)) in hay:
                    hits += 1
            if hits:
                scored.append((rid, hits / float(len(wanted))))
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        return scored[: int(limit)]


    def _semantic_search(self, query: str, limit: int, kind: Optional[str],
                         tag: Optional[str]) -> List[Tuple[str, float]]:
        with self._lock:
            # Search runs over the in-RAM index, never over disk. Rows whose
            # class this session may not open were never decrypted, so
            # access-level filtering here is a consequence of not holding the
            # key rather than a check a bug could skip.
            if self.vec_index is None:
                self.load_vec_index()
            if self.vec_index.size() == 0:
                return []
            qv = self.embedder.embed(query)
            scored = self.vec_index.search(qv, limit=max(limit, 25))
            out = []
            for rid, s in scored[:limit]:
                rec = self.get(rid)
                if not rec or rec.deleted:
                    continue
                if kind and rec.kind != kind:
                    continue
                if tag and slugify(tag) not in rec.tags:
                    continue
                out.append((rid, s))
            return out

    def recall(self, query: str, limit: int = 5) -> List[Record]:
        return self.search(query, limit=limit)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            self._ensure_open()
            def count(sql: str, args: Tuple = ()) -> int:
                return int(self.conn.execute(sql, args).fetchone()[0])

            return {
                "records": count("SELECT COUNT(*) FROM records WHERE deleted=0"),
                "deleted": count("SELECT COUNT(*) FROM records WHERE deleted=1"),
                "vectors": count("SELECT COUNT(*) FROM vectors"),
                "kinds": {r[0]: r[1] for r in self.conn.execute(
                    "SELECT kind, COUNT(*) FROM records WHERE deleted=0 GROUP BY kind")},
                "pending_sync": count("SELECT COUNT(*) FROM sync_journal WHERE pushed=0"),
                "embed_backend": self.embedder.name,
                "db_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            }

    # ------------------------------------------------------------------ #
    # sync-facing primitives
    # ------------------------------------------------------------------ #
    def pending_ids(self) -> List[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT rid FROM sync_journal WHERE pushed=0 ORDER BY seq").fetchall()
            return [r["rid"] for r in rows]

    def mark_synced(self, rid: str, remote_version: int) -> None:
        with self._lock:
            self.conn.execute("UPDATE sync_journal SET pushed=1 WHERE rid=? AND pushed=0", (rid,))
            self.conn.execute("UPDATE records SET sync_state='clean' WHERE id=?", (rid,))
            rec = self.get(rid)
            self.conn.execute(
                """INSERT INTO sync_state(rid,base_version,base_updated,remote_version,last_synced)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(rid) DO UPDATE SET base_version=excluded.base_version,
                     base_updated=excluded.base_updated, remote_version=excluded.remote_version,
                     last_synced=excluded.last_synced""",
                (rid, rec.version if rec else 0, rec.updated_at if rec else 0,
                 int(remote_version), now_ms()),
            )
            self.conn.commit()

    def advance_base(self, rid: str, remote_version: int) -> None:
        with self._lock:
            """Record that we have *seen and reconciled* remote version N.

            Called after a conflict copy is created, so the next sync does not
            re-detect the same divergence forever.
            """
            local = self.get(rid)
            self.conn.execute(
                """INSERT INTO sync_state(rid,base_version,base_updated,remote_version,last_synced)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(rid) DO UPDATE SET base_version=excluded.base_version,
                     base_updated=excluded.base_updated, remote_version=excluded.remote_version,
                     last_synced=excluded.last_synced""",
                (rid, local.version if local else 0, local.updated_at if local else 0,
                 int(remote_version), now_ms()),
            )
            self.conn.commit()

    def sync_base(self, rid: str) -> Dict[str, Any]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM sync_state WHERE rid=?", (rid,)).fetchone()
            if not row:
                return {"base_version": 0, "base_updated": 0, "remote_version": 0, "last_synced": 0}
            return dict(row)

    def apply_remote(self, payload: Dict[str, Any], conflict_suffix: str = "conflict") -> str:
        """Write a remote record. Returns ``applied`` | ``skipped`` | ``conflict``."""
        with self._lock:
            rid = payload["id"]
            incoming = Record.from_sync_dict(payload)
            local = self.get(rid)
            if local is None:
                self._write_full(incoming)
                return "applied"
            base = self.sync_base(rid)
            local_changed = local.version > int(base.get("base_version", 0)) and self.dirty(rid)
            if not local_changed:
                self._write_full(incoming)
                return "applied"
            if incoming.updated_at == local.updated_at and incoming.version == local.version:
                return "skipped"
            # true conflict: keep local, stash remote under a new id
            cid = f"{rid}-{conflict_suffix}-{incoming.updated_at}"
            incoming.id = cid
            incoming.tags = list(dict.fromkeys(incoming.tags + ["conflict"]))
            incoming.meta["conflict_of"] = rid
            self._write_full(incoming)
            return "conflict"

    def _write_full(self, rec: Record) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO records(id,kind,title_enc,body_enc,meta_enc,tags_enc,tag_index,
                                       version,created_at,updated_at,deleted,sync_state)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?, 'clean')
                   ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, title_enc=excluded.title_enc,
                     body_enc=excluded.body_enc, meta_enc=excluded.meta_enc,
                     tags_enc=excluded.tags_enc, tag_index=excluded.tag_index,
                     version=excluded.version, updated_at=excluded.updated_at,
                     deleted=excluded.deleted, sync_state='clean'""",
                (rec.id, rec.kind, self._enc(rec.id, "title", rec.title),
                 self._enc(rec.id, "body", rec.body), self._enc(rec.id, "meta", dumps(rec.meta)),
                 self._enc(rec.id, "tags", dumps(rec.tags)),
                 self.blind_tag_index(rec.id, rec.tags),
                 rec.version, rec.created_at, rec.updated_at, int(rec.deleted)),
            )
            self._index(rec)
            if not rec.deleted:
                self._embed_record(rec)
            self.conn.commit()

    def dirty(self, rid: str) -> bool:
        with self._lock:
            row = self.conn.execute("SELECT sync_state FROM records WHERE id=?", (rid,)).fetchone()
            return bool(row and row["sync_state"] == "dirty")

    def export_encrypted_backup(self) -> bytes:
        """Consistent DB snapshot (already ciphertext) + project attachments.

        Two defects this replaces:

        * **WAL loss (audit A4).** The old code copied ``memory.db`` while the
          connection was open. In WAL mode the committed rows can still live in
          ``memory.db-wal``, so a backup taken during normal use restored to
          **0 records** while the API had answered 201. Use SQLite's own online
          backup API, which produces a consistent single-file snapshot.
        * **Missing attachments (audit A5).** Project files are sealed ``.pbx``
          blobs beside the DB, not inside it. A restored project referenced files
          that no longer existed. They are now bundled (still ciphertext) and the
          header records how many.

        Layout: ``<json header>\n<sqlite snapshot>\n<attachments json>\n<tag>``
        """
        import sqlite3

        with self._lock:
            self._ensure_open()
            snapshot = self.db_path.with_suffix(".backup-snapshot")
            if snapshot.exists():
                snapshot.unlink()
            try:
                dest = sqlite3.connect(str(snapshot))
                try:
                    self.conn.backup(dest)
                    dest.commit()
                finally:
                    dest.close()
                data = snapshot.read_bytes()
            finally:
                snapshot.unlink(missing_ok=True)

            attachments = self._collect_attachments()
            att_blob = json.dumps(attachments, sort_keys=True).encode()
            tag = fast_kdf(self.mk, b"pbx/backup-tag/v1", 32)
            header = dumps({"schema": SCHEMA_VERSION, "sha256": sha256_hex(data),
                            "bytes": len(data), "at": now_ms(),
                            "embedder": self.embedder.name,
                            "attachments": len(attachments),
                            "attachments_sha256": sha256_hex(att_blob),
                            "format": 2})
            return (header.encode() + b"\n" + data + b"\n" + att_blob + b"\n"
                    + tag.hex().encode())

    def _collect_attachments(self) -> Dict[str, str]:
        """Every sealed project file, base64ed. Ciphertext is already opaque."""
        root = self.db_path.parent / "projects"
        out: Dict[str, str] = {}
        if not root.is_dir():
            return out
        for path in sorted(root.glob("*.pbx")):
            try:
                out[path.name] = b64e(path.read_bytes())
            except OSError:
                continue          # unreadable attachment must not abort a backup
        return out
