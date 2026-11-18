"""Authenticated encryption envelope.

Format (v1)::

    magic  4B  "PBX1"
    ver    1B  envelope version
    suite  1B  1 = HMAC-SHA256-CTR (stdlib), 2 = AES-256-GCM (optional dep)
    kdf    1B  reserved (0)
    nonce  12B
    ct     nB
    tag    32B (suite 1) / 16B (suite 2)

Suite 1 is a SHA-256 counter-mode stream cipher with an encrypt-then-MAC tag.
It is slower than AES-GCM but has *zero* dependencies, which matters for a
distributed ZIP that must run on a bare Python install. When the optional
``cryptography`` package is present, suite 2 (AES-256-GCM) is used instead.
Both suites are decryptable regardless of which one is installed, because the
suite id travels inside the envelope.
"""
from __future__ import annotations

import hashlib
import hmac
import struct
from typing import Tuple

from ..util.misc import try_import
from .hash import hkdf, random_bytes

MAGIC = b"PBX1"
VERSION = 1
SUITE_SHA_CTR = 1
SUITE_AES_GCM = 2
HDR = struct.Struct(">4sBBB12s")

_cryptography = try_import("cryptography")
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
except Exception:  # pragma: no cover
    AESGCM = None


class CryptoError(RuntimeError):
    pass


def backend() -> str:
    return "aes-256-gcm (cryptography)" if AESGCM is not None else "hmac-sha256-ctr (stdlib)"


def _keystream(key: bytes, nonce: bytes, nbytes: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < nbytes:
        block = hmac.new(key, nonce + struct.pack(">Q", counter), hashlib.sha256).digest()
        out += block
        counter += 1
    return bytes(out[:nbytes])


def _xor(data: bytes, ks: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, ks))


def encrypt(plaintext: bytes | bytearray, key: bytes | bytearray, aad: bytes = b"") -> bytes:
    # The session master key is mutable so the vault can overwrite it on lock.
    # Convert only at the crypto backend boundary, which requires immutable
    # bytes on some optional backends (notably AESGCM).
    plaintext = bytes(plaintext)
    key = bytes(key)
    if len(key) != 32:
        raise CryptoError("key must be 32 bytes")
    nonce = random_bytes(12)
    if AESGCM is not None:
        ct = AESGCM(key).encrypt(nonce, plaintext, aad or None)
        body = ct[:-16]
        tag = ct[-16:]
        suite = SUITE_AES_GCM
    else:
        enc_key = hkdf(key, nonce, b"pbx/enc/v1" + aad, 32)
        mac_key = hkdf(key, nonce, b"pbx/mac/v1" + aad, 32)
        body = _xor(plaintext, _keystream(enc_key, nonce, len(plaintext)))
        tag = hmac.new(mac_key, HDR.pack(MAGIC, VERSION, SUITE_SHA_CTR, 0, nonce) + aad + body,
                       hashlib.sha256).digest()
        suite = SUITE_SHA_CTR
    return HDR.pack(MAGIC, VERSION, suite, 0, nonce) + body + tag


def decrypt(blob: bytes, key: bytes | bytearray, aad: bytes = b"") -> bytes:
    key = bytes(key)
    if len(blob) < HDR.size:
        raise CryptoError("ciphertext truncated")
    magic, ver, suite, _kdf, nonce = HDR.unpack(blob[:HDR.size])
    if magic != MAGIC:
        raise CryptoError("not a personal-brain envelope")
    if ver != VERSION:
        raise CryptoError(f"unsupported envelope version {ver}")
    rest = blob[HDR.size:]
    if suite == SUITE_AES_GCM:
        if AESGCM is None:
            raise CryptoError("envelope uses AES-256-GCM: pip install cryptography")
        if len(rest) < 16:
            raise CryptoError("ciphertext truncated")
        body, tag = rest[:-16], rest[-16:]
        try:
            return AESGCM(key).decrypt(nonce, body + tag, aad or None)
        except Exception as exc:
            raise CryptoError("authentication failed (wrong key or tampered data)") from exc
    if suite == SUITE_SHA_CTR:
        if len(rest) < 32:
            raise CryptoError("ciphertext truncated")
        body, tag = rest[:-32], rest[-32:]
        mac_key = hkdf(key, nonce, b"pbx/mac/v1" + aad, 32)
        expect = hmac.new(mac_key, HDR.pack(MAGIC, VERSION, SUITE_SHA_CTR, 0, nonce) + aad + body,
                          hashlib.sha256).digest()
        if not hmac.compare_digest(expect, tag):
            raise CryptoError("authentication failed (wrong key or tampered data)")
        enc_key = hkdf(key, nonce, b"pbx/enc/v1" + aad, 32)
        return _xor(body, _keystream(enc_key, nonce, len(body)))
    raise CryptoError(f"unknown cipher suite {suite}")


def seal(plaintext: bytes, root_key: bytes, domain: str, context: str = "") -> bytes:
    """Encrypt with a domain-separated subkey derived from a root key."""
    sub = hkdf(root_key, b"pbx/domain/v1", domain.encode() + b"|" + context.encode(), 32)
    return encrypt(plaintext, sub, aad=domain.encode() + b"|" + context.encode())


def open_sealed(blob: bytes, root_key: bytes, domain: str, context: str = "") -> bytes:
    sub = hkdf(root_key, b"pbx/domain/v1", domain.encode() + b"|" + context.encode(), 32)
    return decrypt(blob, sub, aad=domain.encode() + b"|" + context.encode())


def fingerprint(blob: bytes) -> bytes:
    return hashlib.sha256(blob).digest()[:8]
