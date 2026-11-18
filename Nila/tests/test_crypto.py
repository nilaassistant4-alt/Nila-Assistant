import os

import pytest

from brain.crypto.envelope import CryptoError, backend, decrypt, encrypt, open_sealed, seal
from brain.crypto.hash import constant_time_eq, derive_key, fast_kdf, hkdf, mac, random_bytes


def test_roundtrip_and_backend():
    key = random_bytes(32)
    ct = encrypt(b"top secret", key, aad=b"ctx")
    assert decrypt(ct, key, aad=b"ctx") == b"top secret"
    assert isinstance(backend(), str) and backend()


def test_wrong_key_rejected():
    ct = encrypt(b"x", random_bytes(32), aad=b"")
    with pytest.raises(CryptoError):
        decrypt(ct, random_bytes(32), aad=b"")


def test_aad_is_bound():
    key = random_bytes(32)
    ct = encrypt(b"x", key, aad=b"record-1")
    with pytest.raises(CryptoError):
        decrypt(ct, key, aad=b"record-2")


@pytest.mark.parametrize("position", [0, 10, -1, -5])
def test_tamper_detected(position):
    key = random_bytes(32)
    ct = bytearray(encrypt(b"a" * 200, key, aad=b""))
    ct[position] ^= 0x01
    with pytest.raises(CryptoError):
        decrypt(bytes(ct), key, aad=b"")


def test_nonce_is_unique_per_call():
    key = random_bytes(32)
    a = encrypt(b"same", key)
    b = encrypt(b"same", key)
    assert a != b                       # random nonce -> different ciphertext
    assert decrypt(a, key) == decrypt(b, key) == b"same"


def test_truncated_and_foreign_blobs():
    key = random_bytes(32)
    ct = encrypt(b"hello", key)
    with pytest.raises(CryptoError):
        decrypt(ct[:8], key)
    with pytest.raises(CryptoError):
        decrypt(b"NOT_PBX1" + b"\x00" * 40, key)


def test_domain_separation():
    root = random_bytes(32)
    a = seal(b"payload", root, "rec", "id-1")
    b = seal(b"payload", root, "rec", "id-2")
    assert a != b
    assert open_sealed(a, root, "rec", "id-1") == b"payload"
    with pytest.raises(CryptoError):
        open_sealed(a, root, "rec", "id-2")     # wrong context
    with pytest.raises(CryptoError):
        open_sealed(a, root, "title", "id-1")   # wrong domain


def test_hkdf_matches_rfc_shape_and_is_deterministic():
    a = hkdf(b"ikm", b"salt", b"info", 32)
    b = hkdf(b"ikm", b"salt", b"info", 32)
    assert a == b and len(a) == 32
    assert hkdf(b"ikm", b"salt", b"info2", 32) != a
    assert len(hkdf(b"ikm", b"salt", b"info", 64)) == 64


def test_kdf_is_salt_and_input_sensitive():
    a = derive_key(b"pass", b"salt1", b"i", 32, n=2 ** 12)
    b = derive_key(b"pass", b"salt2", b"i", 32, n=2 ** 12)
    c = derive_key(b"pass2", b"salt1", b"i", 32, n=2 ** 12)
    assert len({a, b, c}) == 3
    assert derive_key(b"pass", b"salt1", b"i", 32, n=2 ** 12) == a


def test_subkeys_are_domain_separated():
    mk = random_bytes(32)
    keys = {fast_kdf(mk, d, 32) for d in
            (b"pbx/rec", b"pbx/sync/opaque-id/v1", b"pbx/sync/envelope/v1", b"pbx/audit")}
    assert len(keys) == 4


def test_constant_time_eq():
    assert constant_time_eq(b"abc", b"abc")
    assert not constant_time_eq(b"abc", b"abd")
    assert not constant_time_eq(b"abc", b"ab")


def test_mac_is_keyed():
    assert mac(b"k1", b"data") != mac(b"k2", b"data")
    assert mac(b"k1", b"data") == mac(b"k1", b"data")
