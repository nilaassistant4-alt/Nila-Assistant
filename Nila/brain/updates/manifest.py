"""Update manifest format + verification.

A manifest describes a release bundle. Everything is content-addressed, and the
manifest itself is signed so a compromised CDN cannot serve you a modified
agent.

::

    {
      "schema": 1,
      "channel": "stable",
      "version": "0.2.0",
      "released_at": 1792500000000,
      "min_client": "0.1.0",
      "artifacts": [
        {"name": "agent",  "kind": "package", "path": "agent-0.2.0.zip",
         "sha256": "...", "bytes": 12345, "extract_to": "runtime/agent"}
      ],
      "notes": "…",
      "rollout": {"percent": 100, "stages": ["alpha","beta","stable"]},
      "signature": {"alg": "ed25519", "key_id": "…", "value": "<b64>"}
    }

Verification order (all must pass before anything is written to disk):
  1. schema + channel match a *pinned* channel the owner configured
  2. signature verifies against the pinned public key
     - ``ed25519`` when the optional ``cryptography`` extra is installed
     - ``hmac-sha256`` with a pinned channel key otherwise (TOFU-registered)
  3. ``min_client`` <= our version
  4. every artifact's sha256 + byte length match after download
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..util.misc import b64d, b64e, try_import
from ..util.safe_paths import UnsafePath, safe_relative

SCHEMA = 1
ARTIFACT_KINDS = ("package", "model", "voice_model", "config", "prompt", "integration")

_cryptography = try_import("cryptography")
try:
    from cryptography.exceptions import InvalidSignature  # type: ignore
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # type: ignore
        Ed25519PrivateKey, Ed25519PublicKey,
    )
except Exception:  # pragma: no cover
    Ed25519PrivateKey = Ed25519PublicKey = None  # type: ignore
    InvalidSignature = Exception  # type: ignore


class ManifestError(RuntimeError):
    pass


@dataclass
class Artifact:
    name: str
    kind: str
    path: str
    sha256: str
    bytes: int
    extract_to: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: Dict[str, Any]) -> "Artifact":
        if raw.get("kind") not in ARTIFACT_KINDS:
            raise ManifestError(f"unknown artifact kind: {raw.get('kind')}")
        if not raw.get("sha256") or len(str(raw["sha256"])) != 64:
            raise ManifestError(f"artifact {raw.get('name')} has no valid sha256")
        extract_to = str(raw.get("extract_to", "") or "")
        if extract_to:
            # Validate at PARSE time, not mid-install: a bad value must stop the
            # update before a single byte is written, and must never reach
            # ``target / extract_to`` in the installer.
            try:
                extract_to = safe_relative(extract_to,
                                           what=f"artifact {raw.get('name')} extract_to").as_posix()
            except UnsafePath as exc:
                raise ManifestError(str(exc)) from exc
        return cls(name=str(raw["name"]), kind=str(raw["kind"]), path=str(raw["path"]),
                   sha256=str(raw["sha256"]).lower(), bytes=int(raw.get("bytes", 0)),
                   extract_to=extract_to, meta=dict(raw.get("meta") or {}))

    def to_dict(self) -> Dict[str, Any]:
        out = {"name": self.name, "kind": self.kind, "path": self.path,
               "sha256": self.sha256, "bytes": self.bytes}
        if self.extract_to:
            out["extract_to"] = self.extract_to
        if self.meta:
            out["meta"] = self.meta
        return out


@dataclass
class Manifest:
    channel: str
    version: str
    released_at: int
    artifacts: List[Artifact]
    min_client: str = "0.0.0"
    notes: str = ""
    rollout: Dict[str, Any] = field(default_factory=dict)
    signature: Dict[str, Any] = field(default_factory=dict)
    schema: int = SCHEMA

    @classmethod
    def parse(cls, raw: bytes | str | Dict[str, Any]) -> "Manifest":
        data = json.loads(raw) if isinstance(raw, (bytes, str)) else dict(raw)
        if int(data.get("schema", 0)) != SCHEMA:
            raise ManifestError(f"unsupported manifest schema: {data.get('schema')}")
        arts = [Artifact.parse(a) for a in data.get("artifacts", [])]
        if not arts:
            raise ManifestError("manifest has no artifacts")
        return cls(channel=str(data["channel"]), version=str(data["version"]),
                   released_at=int(data.get("released_at", 0)), artifacts=arts,
                   min_client=str(data.get("min_client", "0.0.0")),
                   notes=str(data.get("notes", "")), rollout=dict(data.get("rollout") or {}),
                   signature=dict(data.get("signature") or {}))

    def canonical(self) -> bytes:
        """The exact bytes that get signed (signature excluded, keys sorted)."""
        payload = {
            "schema": self.schema, "channel": self.channel, "version": self.version,
            "released_at": self.released_at, "min_client": self.min_client, "notes": self.notes,
            "rollout": self.rollout,
            "artifacts": [a.to_dict() for a in self.artifacts],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def to_json(self, include_signature: bool = True) -> str:
        payload = json.loads(self.canonical())
        if include_signature and self.signature:
            payload["signature"] = self.signature
        return json.dumps(payload, indent=2, sort_keys=True)

    def artifact(self, name: str) -> Optional[Artifact]:
        for a in self.artifacts:
            if a.name == name:
                return a
        return None


# --------------------------------------------------------------------------- #
# version helpers
# --------------------------------------------------------------------------- #
def parse_version(v: str) -> tuple:
    parts = []
    for chunk in str(v).split(".")[:3]:
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def version_ge(a: str, b: str) -> bool:
    return parse_version(a) >= parse_version(b)


# --------------------------------------------------------------------------- #
# signing
# --------------------------------------------------------------------------- #
def generate_signing_key() -> Dict[str, str]:
    """Return ``{private, public, key_id}`` - ed25519 if available, else HMAC."""
    if Ed25519PrivateKey is not None:
        priv = Ed25519PrivateKey.generate()
        pub = priv.public_key()
        from cryptography.hazmat.primitives import serialization  # type: ignore

        raw_priv = priv.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
        raw_pub = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        key_id = hashlib.sha256(raw_pub).hexdigest()[:16]
        return {"alg": "ed25519", "private": b64e(raw_priv), "public": b64e(raw_pub),
                "key_id": key_id}
    secret = hashlib.sha256(__import__("secrets").token_bytes(32)).digest()
    key_id = hashlib.sha256(secret).hexdigest()[:16]
    return {"alg": "hmac-sha256", "private": b64e(secret), "public": b64e(secret),
            "key_id": key_id}


def sign_manifest(manifest: Manifest, private_key_b64: str, alg: str, key_id: str) -> Manifest:
    data = manifest.canonical()
    if alg == "ed25519":
        if Ed25519PrivateKey is None:
            raise ManifestError("ed25519 signing needs the 'cryptography' extra")
        priv = Ed25519PrivateKey.from_private_bytes(b64d(private_key_b64))
        sig = priv.sign(data)
    elif alg == "hmac-sha256":
        sig = hmac.new(b64d(private_key_b64), data, hashlib.sha256).digest()
    else:
        raise ManifestError(f"unsupported signature algorithm: {alg}")
    manifest.signature = {"alg": alg, "key_id": key_id, "value": b64e(sig)}
    return manifest


def verify_signature(manifest: Manifest, pinned: Dict[str, Any]) -> Dict[str, Any]:
    """Verify a manifest signature against the channel's pinned key.

    ``pinned`` = ``{alg, public, key_id, mode}`` where mode is:

      ``strict``   a pinned key is required; unsigned or mismatched -> reject
      ``tofu``     if no key is pinned yet, the manifest is accepted *and* its
                   key_id is pinned for every future check (the first fetch is
                   trusted - see the warning the CLI prints)
      ``insecure`` signatures are ignored (hash checks still apply)
    """
    sig = manifest.signature or {}
    alg = sig.get("alg")
    mode = str(pinned.get("mode", "strict")).lower()
    result = {"ok": False, "alg": alg, "mode": mode, "reason": "", "pinned_key_id": pinned.get("key_id", "")}

    if mode == "insecure":
        result["ok"] = True
        result["reason"] = "signature verification disabled (mode=insecure)"
        return result

    if not sig:
        # STRICT DEFAULT: an unsigned manifest is refused in every mode except
        # explicit ``insecure`` (handled above). Accepting it in ``tofu`` meant
        # nothing was ever pinned, so *every* later manifest was equally
        # unauthenticated - a channel that trusts whatever it is served, forever.
        result["reason"] = ("manifest is unsigned: refusing it. Sign the manifest, or "
                            "re-add the channel with mode=insecure if you control it "
                            "end-to-end")
        return result

    if not pinned.get("key_id"):
        if mode == "tofu":
            if not sig.get("value"):
                result["reason"] = "manifest signature has no value; refusing to pin it"
                return result
            result["ok"] = True
            result["reason"] = (f"first use (mode=tofu): pinning key_id {sig.get('key_id')} - "
                                f"verify it out of band before trusting anything it ships")
            result["pin"] = {"alg": alg, "key_id": sig.get("key_id", "")}
            return result
        result["reason"] = "no key pinned for this channel and mode is strict"
        return result

    if sig.get("key_id") != pinned["key_id"]:
        result["reason"] = (f"key_id mismatch: manifest {sig.get('key_id')}, "
                            f"pinned {pinned.get('key_id')}")
        return result

    data = manifest.canonical()
    try:
        if alg == "ed25519":
            if Ed25519PublicKey is None:
                result["reason"] = "ed25519 verification needs the 'cryptography' extra"
                return result
            pub = Ed25519PublicKey.from_public_bytes(b64d(str(pinned.get("public", ""))))
            pub.verify(b64d(sig["value"]), data)
        elif alg == "hmac-sha256":
            # A TOFU pin records only the key_id for a symmetric algorithm, so
            # ``public`` can be empty here. An empty HMAC key is not "no
            # verification", it is a key everyone knows - refuse instead.
            key_b64 = str(pinned.get("key") or pinned.get("public") or "")
            if not key_b64:
                result["reason"] = ("channel pins a key_id for hmac-sha256 but has no shared "
                                    "secret; re-add the channel with --key, or use ed25519")
                return result
            key = b64d(key_b64)
            expect = hmac.new(key, data, hashlib.sha256).digest()
            if not hmac.compare_digest(expect, b64d(sig["value"])):
                result["reason"] = "HMAC does not match the pinned channel key"
                return result
        else:
            result["reason"] = f"unsupported signature algorithm: {alg}"
            return result
    except (InvalidSignature, ValueError, KeyError) as exc:
        result["reason"] = f"signature verification failed: {type(exc).__name__}"
        return result
    result["ok"] = True
    result["reason"] = "signature valid"
    return result


def verify_artifact(data: bytes, artifact: Artifact) -> Dict[str, Any]:
    digest = hashlib.sha256(data).hexdigest()
    ok = digest == artifact.sha256
    size_ok = (not artifact.bytes) or len(data) == artifact.bytes
    return {"ok": ok and size_ok, "sha256": digest, "expected": artifact.sha256,
            "bytes": len(data), "expected_bytes": artifact.bytes, "size_ok": size_ok}
