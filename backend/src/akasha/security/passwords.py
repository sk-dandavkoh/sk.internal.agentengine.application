"""Password hashing with scrypt (stdlib ``hashlib``, RFC 7914).

The defaults are one of OWASP's recommended scrypt settings (N=2^14, r=8,
p=5; 16 MiB per hash). Every hash records its own parameters and salt, so
verification keeps working after the defaults are raised.

Stored format: ``scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>``.
"""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

_SCHEME = "scrypt"
_SALT_BYTES = 16
_KEY_BYTES = 32
# Refuse stored parameters that would make one verify eat the server: a
# tampered or corrupt row must fail, not allocate gigabytes.
_MAX_MEMORY = 256 * 1024 * 1024


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=_MAX_MEMORY,
        dklen=_KEY_BYTES,
    )


@dataclass(frozen=True)
class PasswordHasher:
    """CPU- and memory-bound: call it off the event loop."""

    n: int = 2**14
    r: int = 8
    p: int = 5

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(_SALT_BYTES)
        key = _derive(password, salt, self.n, self.r, self.p)
        return f"{_SCHEME}${self.n}${self.r}${self.p}${_b64(salt)}${_b64(key)}"

    def verify(self, password: str, stored: str) -> bool:
        """Constant-time compare; a malformed ``stored`` value never matches."""
        try:
            scheme, n, r, p, salt, key = stored.split("$")
            if scheme != _SCHEME:
                return False
            expected = base64.b64decode(key, validate=True)
            actual = _derive(
                password, base64.b64decode(salt, validate=True), int(n), int(r), int(p)
            )
        except ValueError:  # bad split, number, base64 or scrypt parameters
            return False
        return hmac.compare_digest(actual, expected)
