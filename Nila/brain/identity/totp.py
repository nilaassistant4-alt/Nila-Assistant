"""RFC 6238 TOTP + recovery codes (the 'something you have' fallback factor)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import List, Optional

PERIOD = 30
DIGITS = 6
ALGO = hashlib.sha1


def generate_secret(length: int = 20) -> str:
    return base64.b32encode(secrets.token_bytes(length)).decode().rstrip("=")


def _b32decode(secret: str) -> bytes:
    pad = "=" * (-len(secret) % 8)
    return base64.b32decode(secret.upper() + pad)


def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    key = _b32decode(secret)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, ALGO).digest()
    off = h[-1] & 0x0F
    code = (struct.unpack(">I", h[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp(secret: str, at: Optional[float] = None, period: int = PERIOD) -> str:
    t = time.time() if at is None else at
    return hotp(secret, int(t // period))


def verify(secret: str, code: str, window: int = 1, at: Optional[float] = None,
           period: int = PERIOD, min_counter: int = 0) -> bool:
    """True when ``code`` is valid for this secret at that time.

    ``min_counter`` makes the check ONE-TIME: the caller passes the highest
    counter it has already consumed, and a code at or **before** it is refused
    even though the maths is still correct. Without this, one code authenticated
    repeatedly inside its 30-second window (audit B6, reproduced).

    The comparison is strictly greater-than: ``<=`` would let the same step
    through a second time, which is exactly the bug this exists to stop.
    """
    code = (code or "").strip()
    if not code.isdigit() or len(code) != DIGITS:
        return False
    t = time.time() if at is None else at
    base = int(t // period)
    for drift in range(-window, window + 1):
        counter = base + drift
        # One rule: a step already SPENT is refused, everything else is not.
        # Equality, not `<=` - `<=` also refused every step after the ceiling,
        # which includes the next step when the stored ceiling is a future step
        # (real time never reaches it). `Verify` above keeps `<=` because there
        # the ceiling is a hard floor from the 0 default.
        if counter <= 0 or counter == int(min_counter or 0):
            continue
        if hmac.compare_digest(hotp(secret, counter), code):
            return True
    return False


def counter_for(secret: str, code: str, window: int = 1, at: Optional[float] = None,
                period: int = PERIOD, min_counter: int = 0) -> int:
    """The counter a valid ``code`` matches, or ``-1``.

    Separate from :func:`verify` so the caller can persist *which* step was
    spent. Returns -1 for a malformed or non-matching code.
    """
    code = (code or "").strip()
    if not code.isdigit() or len(code) != DIGITS:
        return -1
    t = time.time() if at is None else at
    base = int(t // period)
    for drift in range(-window, window + 1):
        counter = base + drift
        # One rule: a step already SPENT is refused, everything else is not.
        # Equality, not `<=` - `<=` also refused every step after the ceiling,
        # which includes the next step when the stored ceiling is a future step
        # (real time never reaches it). `Verify` above keeps `<=` because there
        # the ceiling is a hard floor from the 0 default.
        if counter <= 0 or counter == int(min_counter or 0):
            continue
        if hmac.compare_digest(hotp(secret, counter), code):
            return counter
    return -1


def provisioning_uri(secret: str, account: str, issuer: str = "PersonalBrain") -> str:
    from urllib.parse import quote

    label = quote(f"{issuer}:{account}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}")


def generate_recovery_codes(n: int = 8) -> List[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no ambiguous glyphs
    out = []
    for _ in range(n):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        out.append(f"{raw[:5]}-{raw[5:]}")
    return out
