"""KDFs and hashes built on hashlib only (works without 'cryptography')."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

from ..util.misc import try_import

_cryptography = try_import("cryptography")

SCRYPT_N_DEFAULT = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1


def hkdf(ikm: bytes, salt: bytes, info: bytes, length: int = 32, hash_name: str = "sha256") -> bytes:
    """RFC 5869 HKDF (extract-and-expand)."""
    hash_len = hashlib.new(hash_name).digest_size
    if length > 255 * hash_len:
        raise ValueError("hkdf output too long")
    prk = hmac.new(salt if salt else b"\x00" * hash_len, ikm, hash_name).digest()
    okm = b""
    t = b""
    counter = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hash_name).digest()
        okm += t
        counter += 1
    return okm[:length]


def derive_key(
    secret: bytes,
    salt: bytes,
    info: bytes = b"personal-brain/kdf/v1",
    length: int = 32,
    n: int = SCRYPT_N_DEFAULT,
    r: int = SCRYPT_R,
    p: int = SCRYPT_P,
) -> bytes:
    """scrypt-hardened domain-separated key derivation."""
    dk = hashlib.scrypt(secret, salt=salt, n=n, r=r, p=p, dklen=length, maxmem=1024 * n * r * 2 + (1 << 22))
    return hkdf(dk, salt, info, length)


def fast_kdf(secret: bytes, info: bytes, length: int = 32) -> bytes:
    """Non-password KDF for deriving subkeys from an already-random master key."""
    return hkdf(secret, b"personal-brain/subkey/v1", info, length)


def random_bytes(n: int = 32) -> bytes:
    return secrets.token_bytes(n)


def mac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def constant_time_eq(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


def argon2_available() -> bool:
    return _cryptography is not None or try_import("argon2") is not None
