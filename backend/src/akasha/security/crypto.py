"""Authenticated encryption of secrets at rest (architecture §32).

AES-256-GCM via ``cryptography`` — no home-grown crypto. Blob layout::

    version (1 byte) | nonce (12 bytes) | ciphertext + GCM tag

``context`` (the credential name) is bound as associated data, so a
ciphertext copied onto another credential's row fails to decrypt instead of
silently resolving to someone else's secret.

The key comes from configuration only (``AKASHA_CREDENTIAL_ENCRYPTION_KEY``,
32 random bytes, base64 — URL-safe or standard). Generate one with::

    openssl rand -base64 32

ponytail: one active key. Rotation needs a key id per blob plus a re-encrypt
job; the version byte leaves room for that (Phase 26 operations).
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from akasha.errors import AkashaError, ConfigurationError

_VERSION = b"\x01"
_NONCE_BYTES = 12


class SecretDecryptionError(AkashaError):
    """Stored ciphertext cannot be decrypted: wrong key, tampering or
    corruption. Deliberately carries no detail about which."""

    code = "CREDENTIAL_DECRYPTION_FAILED"
    http_status = 500


class SecretCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ConfigurationError("credential encryption key must be 32 bytes")
        self._aead = AESGCM(key)

    @classmethod
    def from_config(cls, encoded: str) -> "SecretCipher":
        try:
            key = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except (UnicodeEncodeError, binascii.Error, ValueError):
            # No ``from exc``: the cause would carry fragments of the key.
            raise ConfigurationError(
                "credential encryption key must be URL-safe base64"
            ) from None
        return cls(key)

    def __repr__(self) -> str:
        return "SecretCipher(AES-256-GCM)"

    def encrypt(self, plaintext: str, *, context: str) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), context.encode())
        return _VERSION + nonce + sealed

    def decrypt(self, blob: bytes, *, context: str) -> str:
        if blob[:1] != _VERSION:
            raise SecretDecryptionError("The stored secret cannot be decrypted.")
        nonce, sealed = blob[1 : 1 + _NONCE_BYTES], blob[1 + _NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, sealed, context.encode()).decode()
        except (InvalidTag, ValueError):
            raise SecretDecryptionError(
                "The stored secret cannot be decrypted."
            ) from None
