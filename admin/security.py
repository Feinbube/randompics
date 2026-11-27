"""Password hashing for the randompics admin (stdlib scrypt)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

N, R, P, DKLEN = 16384, 8, 1, 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = bytes.fromhex(digest_hex)
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                               n=int(n), r=int(r), p=int(p), dklen=len(digest))
    return hmac.compare_digest(candidate, digest)
