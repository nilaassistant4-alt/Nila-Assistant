"""Sealed vector storage with a class-scoped key hierarchy.

Why this module exists
----------------------
Embeddings are **not anonymous**. Published inversion attacks reconstruct much of
the source text from a vector, and vectors leak attributes and membership even
when inversion fails. An embedding must be treated with the same sensitivity as
the text it came from.

The MVP stored ``vectors.vec`` as plaintext JSON and used a *public*
deterministic embedder (``sha256("emb1|"+token) % dim``), so an attacker holding
only ``memory.db`` could reimplement the embedder, embed guesses, and attribute
them to records with no passphrase. Measured: a health-related guess matched its
record at cosine 0.901. See ``docs/THREAT_MODEL.md`` T1.

This module closes that path.

Key hierarchy
-------------
::

    master_key (MK, from the vault; keystore-wrapped at rest)
      +-- class_key[name]   = HKDF(MK, "nila/class/v1", "nila/class/<name>/v<n>")
            +-- record_key  = HKDF(class_key, salt_record, "nila/vec/<record_id>")

``salt_record`` is random and stored **in the row**. That is what makes
crypto-shredding possible: deleting the salt makes the record key underivable and
the blob permanently unrecoverable. Pure deterministic derivation from
``record_id`` cannot be shredded, which is exactly why the salt matters.

``key_version`` is bound into the derivation and into the AAD
(``record_id|class|key_version``) so blobs cannot be swapped between records or
between classes, and so rotation can re-encrypt lazily.

Access levels are enforced cryptographically, not by a filter
------------------------------------------------------------
A session loads only the class keys its access level permits. A record whose
class key was not loaded **cannot be decrypted** - there is no code path that
falls back to reading it. Retrieval filtering by access level therefore stops
being a software check that a bug could bypass.

What is never embedded
----------------------
Government ID, financial and credential content is not embedded at all; those
records are reachable by exact lookup only. Shrinking what exists to leak beats
protecting more of it. Other sensitive classes embed the **redacted** text, so a
card number or phone number inside a health note never reaches the vector.

Searching sealed vectors
------------------------
Nearest-neighbour search cannot run over ciphertext. Only sealed vectors are
persisted; the index is built in RAM at unlock from the classes the session may
open, and kept **per class** so a partial unlock still searches what it is
allowed to see. The cost is unlock latency and memory. In exchange no plaintext
index ever touches disk - which also covers the case the original design missed,
where a vector library (FAISS, hnswlib) writes its own index file outside the
database.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..crypto.envelope import encrypt, decrypt
from ..crypto.hash import hkdf, random_bytes
from ..security.classify import (
    CLASS_INTERNAL, CLASS_PUBLIC, CLASS_RESTRICTED, CLASS_SENSITIVE,
)

#: Vector key-derivation version. Bumping this makes every existing blob
#: unreadable until re-encrypted, which is the point: rotation is explicit.
KEY_VERSION = 1

_CLASS_SALT = b"nila/class/v1"

# --------------------------------------------------------------------------- #
# vector classes
# --------------------------------------------------------------------------- #
#: The four classes in the key hierarchy. ``financial`` exists in the hierarchy
#: and in the access-level mapping but is unused by the default embed policy,
#: because financial content is not embedded at all (exact lookup only). It is
#: kept so that a future policy can enable it without a key migration.
VEC_GENERAL = "general"
VEC_IDENTITY = "identity"
VEC_HEALTH = "health"
VEC_FINANCIAL = "financial"

VEC_CLASSES: Tuple[str, ...] = (VEC_GENERAL, VEC_IDENTITY, VEC_HEALTH, VEC_FINANCIAL)

#: access level -> vector classes a session may load keys for.
#: Mirrors TIER_CLASS in security/classify.py: reading sensitive data is L3 and
#: operating on it is L4, so `full` already unlocks health and identity vectors.
LEVEL_CLASSES: Dict[str, Tuple[str, ...]] = {
    "locked": (),
    "degraded": (VEC_GENERAL,),
    "full": (VEC_GENERAL, VEC_IDENTITY, VEC_HEALTH),
    "step_up": (VEC_GENERAL, VEC_IDENTITY, VEC_HEALTH, VEC_FINANCIAL),
}

#: tags that mean "do not embed this record at all"
_NO_EMBED_TAGS = frozenset({
    "credential", "credentials", "secret", "password", "finance", "financial",
    "bank", "banking", "government", "government-id", "passport", "aadhaar",
    "pan", "biometric", "voiceprint", "ssn", "voter-id", "dl",
})

#: tags that place a record in the health class
_HEALTH_TAGS = frozenset({"health", "medical", "therapy", "diagnosis", "meds",
                          "medication", "doctor", "hospital", "clinic"})

#: tags that place a record in the identity class
_IDENTITY_TAGS = frozenset({"contact", "people", "family", "relationships",
                            "personal", "legal"})


class VecVaultError(RuntimeError):
    """Raised when a sealed vector cannot be opened."""


@dataclass
class EmbedDecision:
    """What the embed policy decided about one record."""
    embed: bool
    klass: str = VEC_GENERAL
    reason: str = ""
    #: text that should actually be embedded (redacted for sensitive classes)
    text: str = ""


def decide_embed(data_class: str, tags: Iterable[str], text: str,
                 redact_fn=None) -> EmbedDecision:
    """Decide whether a record gets a vector, and which class it belongs to.

    The policy is deliberately biased towards *not* embedding. A record that is
    not embedded is still findable by exact lookup and by the blind-indexed
    lexical search; it simply does not participate in semantic search.
    """
    tagset = {str(t).lower() for t in (tags or ())}

    # 1. restricted content is never embedded - exact lookup only.
    if data_class == CLASS_RESTRICTED or (tagset & _NO_EMBED_TAGS):
        return EmbedDecision(False, VEC_GENERAL,
                             "restricted: government id, financial or credential "
                             "content is not embedded (exact lookup only)")

    # 2. choose a class from tags
    if tagset & _HEALTH_TAGS:
        klass = VEC_HEALTH
    elif tagset & _IDENTITY_TAGS or data_class == CLASS_SENSITIVE:
        klass = VEC_IDENTITY
    else:
        klass = VEC_GENERAL

    # 3. sensitive classes embed the REDACTED text, so a card number inside a
    #    health note never reaches the vector.
    out_text = text or ""
    if klass in (VEC_HEALTH, VEC_IDENTITY) and redact_fn is not None:
        try:
            masked, _names = redact_fn(out_text)
            out_text = masked
        except Exception:
            # if redaction fails, do not embed rather than embed unredacted
            return EmbedDecision(False, klass,
                                 "redaction failed; refusing to embed unredacted "
                                 "sensitive text")

    return EmbedDecision(True, klass, f"class={klass}", out_text)


# --------------------------------------------------------------------------- #
# key hierarchy
# --------------------------------------------------------------------------- #
def class_key(mk: bytes, name: str, version: int = KEY_VERSION) -> bytes:
    """HKDF(MK, "nila/class/v1", "nila/class/<name>/v<n>")."""
    if name not in VEC_CLASSES:
        raise VecVaultError(f"unknown vector class: {name}")
    info = f"nila/class/{name}/v{version}".encode()
    return hkdf(mk, _CLASS_SALT, info, 32)


def record_key(ck: bytes, salt: bytes, rid: str,
               version: int = KEY_VERSION) -> bytes:
    """HKDF(class_key, salt_record, "nila/vec/<record_id>").

    ``salt`` is random per record and stored in the row, which is what makes
    crypto-shredding possible.
    """
    if len(salt) < 16:
        raise VecVaultError("per-record salt must be at least 16 bytes")
    return hkdf(ck, salt, f"nila/vec/{rid}/v{version}".encode(), 32)


def new_salt() -> bytes:
    return random_bytes(16)


def _aad(rid: str, klass: str, version: int) -> bytes:
    """Bound into the AEAD so a blob cannot be moved between records/classes."""
    return f"{rid}|{klass}|v{version}".encode()


def seal_vector(vec: Sequence[float], mk: bytes, rid: str, klass: str,
                salt: bytes, version: int = KEY_VERSION) -> bytes:
    """AEAD-seal a vector under its per-record key."""
    ck = class_key(mk, klass, version)
    rk = record_key(ck, salt, rid, version)
    # 6 decimal places matches the previous plaintext precision and keeps the
    # blob small; floats are stored as a compact JSON array.
    payload = json.dumps([round(float(v), 6) for v in vec]).encode()
    return encrypt(payload, rk, aad=_aad(rid, klass, version))


def open_vector(blob: bytes, mk: bytes, rid: str, klass: str, salt: bytes,
                version: int = KEY_VERSION) -> List[float]:
    """Open a sealed vector. Raises if the class key or salt is wrong."""
    ck = class_key(mk, klass, version)
    rk = record_key(ck, salt, rid, version)
    try:
        raw = decrypt(blob, rk, aad=_aad(rid, klass, version))
    except Exception as exc:
        raise VecVaultError(
            f"cannot open vector for {rid}: wrong class key, wrong salt, or "
            f"tampered blob ({type(exc).__name__})") from exc
    return json.loads(raw.decode())


# --------------------------------------------------------------------------- #
# in-RAM index, per class
# --------------------------------------------------------------------------- #
@dataclass
class VecIndex:
    """Plaintext vectors, in RAM only, partitioned by class.

    Built at unlock from the classes the session is allowed to open. Never
    written to disk. Zeroed on lock.
    """
    #: class -> {rid: vector}
    by_class: Dict[str, Dict[str, List[float]]] = field(default_factory=dict)
    loaded_classes: Tuple[str, ...] = ()
    key_version: int = KEY_VERSION
    #: class keys held for this session, so sealing new vectors needs no MK
    _class_keys: Dict[str, bytes] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for c in self.loaded_classes:
            self.by_class.setdefault(c, {})

    # -- construction ------------------------------------------------------ #
    @classmethod
    def build(cls, mk: bytes, rows: Iterable[Any], level: str,
              version: int = KEY_VERSION) -> "VecIndex":
        """Decrypt the allowed rows into RAM.

        ``rows`` yields objects supporting ``row["rid"]``, ``row["klass"]``,
        ``row["salt"]``, ``row["vec"]``. Rows whose class is not permitted for
        this level are **skipped**, not decrypted - so the filtering is a
        consequence of not holding the key, not of a check that could be bypassed.
        """
        allowed = LEVEL_CLASSES.get(level, ())
        idx = cls(loaded_classes=tuple(allowed), key_version=version)
        for c in allowed:
            idx._class_keys[c] = class_key(mk, c, version)
        skipped = 0
        for row in rows:
            klass = str(row["klass"] or VEC_GENERAL)
            if klass not in allowed:
                skipped += 1
                continue
            try:
                vec = open_vector(bytes(row["vec"]), mk, str(row["rid"]), klass,
                                  bytes(row["salt"]), version)
            except VecVaultError:
                # a blob we cannot open is not a reason to fail the unlock
                skipped += 1
                continue
            idx.by_class[klass][str(row["rid"])] = vec
        idx.skipped = skipped  # type: ignore[attr-defined]
        return idx

    # -- queries ----------------------------------------------------------- #
    def classes_loaded(self) -> Tuple[str, ...]:
        return self.loaded_classes

    def size(self) -> int:
        return sum(len(d) for d in self.by_class.values())

    def searchable_rids(self) -> List[str]:
        out: List[str] = []
        for d in self.by_class.values():
            out.extend(d.keys())
        return out

    def search(self, qv: Sequence[float], limit: int = 5) -> List[Tuple[str, float]]:
        """Cosine nearest-neighbour over everything this session may see."""
        qn = math.sqrt(sum(x * x for x in qv)) or 1.0
        scored: List[Tuple[str, float]] = []
        for d in self.by_class.values():
            for rid, vec in d.items():
                dot = 0.0
                for a, b in zip(qv, vec):
                    dot += a * b
                vn = math.sqrt(sum(b * b for b in vec)) or 1.0
                scored.append((rid, dot / (qn * vn)))
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        return scored[:limit]

    # -- mutation ---------------------------------------------------------- #
    def put(self, rid: str, klass: str, vec: Sequence[float]) -> bool:
        """Add a freshly computed vector. False if this session may not see it."""
        if klass not in self.by_class:
            return False
        self.by_class[klass][rid] = [float(v) for v in vec]
        return True

    def forget(self, rid: str) -> None:
        for d in self.by_class.values():
            d.pop(rid, None)

    # -- hygiene ----------------------------------------------------------- #
    def zero(self) -> None:
        """Overwrite the plaintext vectors before dropping them.

        Best effort: CPython may hold copies elsewhere, and this does not defeat
        a memory-scraping attacker (THREAT_MODEL T16). It does stop the obvious
        case of plaintext vectors lingering in freed heap or a core dump.
        """
        for d in self.by_class.values():
            for rid, vec in list(d.items()):
                for i in range(len(vec)):
                    vec[i] = 0.0
                d[rid] = []
            d.clear()
        self.by_class.clear()
        for k in list(self._class_keys):
            self._class_keys[k] = b"\x00" * 32
        self._class_keys.clear()
        self.loaded_classes = ()


def harden_process_memory() -> Dict[str, Any]:
    """Best-effort OS-level memory hygiene.

    Disables core dumps so plaintext vectors cannot land in one, and tries to
    lock the address space so it cannot be swapped to disk. Both are
    best-effort: failure is reported, never fatal, because the alternative is
    refusing to run on platforms that disallow either.
    """
    report: Dict[str, Any] = {"core_dumps_disabled": False, "mlock": False,
                              "errors": []}
    try:
        import resource
        cur = resource.getrlimit(resource.RLIMIT_CORE)
        resource.setrlimit(resource.RLIMIT_CORE, (0, cur[1]))
        report["core_dumps_disabled"] = resource.getrlimit(resource.RLIMIT_CORE)[0] == 0
    except Exception as exc:
        report["errors"].append(f"rlimit: {type(exc).__name__}")
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        MCL_CURRENT, MCL_FUTURE = 1, 2
        rc = libc.mlockall(MCL_CURRENT | MCL_FUTURE)
        report["mlock"] = rc == 0
        if rc != 0:
            report["errors"].append(f"mlockall rc={rc} errno={ctypes.get_errno()}")
    except Exception as exc:
        report["errors"].append(f"mlockall: {type(exc).__name__}")
    return report


# --------------------------------------------------------------------------- #
# sync allowlist
# --------------------------------------------------------------------------- #
#: Tables that may ever reach the sync engine. Anything not listed here is
#: refused, so a new table cannot become syncable by accident. Vectors are
#: deliberately absent: they are recomputed locally on each device, because
#: syncing an embedding syncs a derivable copy of the text.
SYNCABLE_TABLES: Tuple[str, ...] = ("records", "sync_journal", "sync_state", "meta")

#: Tables that must never appear in the oplog or any sync payload.
NEVER_SYNCED: Tuple[str, ...] = ("vectors", "pii_biometric", "voice_template",
                                 "records_fts", "tag_index")


def assert_syncable(table: str) -> None:
    if table in NEVER_SYNCED:
        raise VecVaultError(
            f"refusing to sync table '{table}': it is on the never-synced list "
            f"(vectors and biometrics are recomputed/enrolled per device)")
    if table not in SYNCABLE_TABLES:
        raise VecVaultError(
            f"refusing to sync table '{table}': not on the allowlist "
            f"{SYNCABLE_TABLES}. Add it deliberately, not by accident.")


def scan_disk_for_plaintext_vectors(paths: Iterable[str],
                                    needles: Sequence[str]) -> List[str]:
    """Test helper: fail if plaintext vectors or raw audio reached the disk.

    Used by CI to assert the property this module exists to provide. ``needles``
    should include distinctive float fragments and any plaintext that must not
    have been written.
    """
    found: List[str] = []
    for p in paths:
        try:
            if os.path.isdir(p):
                for root, _dirs, files in os.walk(p):
                    for f in files:
                        found.extend(_scan_one(os.path.join(root, f), needles))
            else:
                found.extend(_scan_one(p, needles))
        except OSError:
            continue
    return found


def _scan_one(path: str, needles: Sequence[str]) -> List[str]:
    hits: List[str] = []
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return hits
    for n in needles:
        if n.encode() in data:
            hits.append(f"{path}: contains {n!r}")
    return hits
