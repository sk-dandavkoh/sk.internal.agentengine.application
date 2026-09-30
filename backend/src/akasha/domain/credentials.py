"""Credentials: named secrets held by the Credential Store (architecture §32).

Projects store ``CredentialReference``s (``credential:<name>``), never values.
The name is the credential's identity and is immutable, so references stay
valid across value rotation and survive project export/import (the target
installation provides a credential of the same name).

``Secret`` wraps plaintext so it cannot leak by accident: ``str``/``repr``
are masked, and pickling/JSON encoding fail. The value is only reachable via
``reveal()``, called as late as possible (backend/AGENTS §28).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NoReturn

from akasha.domain.primitives import Timestamps, utcnow, validate_text
from akasha.errors import AkashaError, DomainValidationError
from akasha.security.redaction import MASK, is_sensitive_key, redact_text

REFERENCE_PREFIX = "credential:"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# PEM private keys fit comfortably; anything larger is not a credential.
MAX_SECRET_LENGTH = 64 * 1024


class PlaintextSecretError(AkashaError):
    """Project configuration contains a secret value instead of a reference."""

    code = "PLAINTEXT_SECRET"
    http_status = 422


class CredentialKind(StrEnum):
    API_KEY = "api_key"
    SMTP = "smtp"
    PROVIDER = "provider"
    GENERIC = "generic"


def validate_credential_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise DomainValidationError(
            "credential name must be 1-64 characters of a-z, 0-9, '.', '_' or '-'"
            " and start with a letter or digit"
        )
    return name


@dataclass(frozen=True)
class CredentialReference:
    """An opaque pointer to a credential in the Credential Store."""

    ref: str

    def __post_init__(self) -> None:
        if not is_credential_reference(self.ref):
            raise DomainValidationError(
                f"credential reference must look like '{REFERENCE_PREFIX}<name>'"
            )

    @property
    def name(self) -> str:
        return self.ref.removeprefix(REFERENCE_PREFIX)

    @classmethod
    def to(cls, name: str) -> "CredentialReference":
        return cls(REFERENCE_PREFIX + name)


def is_credential_reference(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(REFERENCE_PREFIX)
        and _NAME_RE.match(value.removeprefix(REFERENCE_PREFIX)) is not None
    )


class Secret:
    """Plaintext secret material; masked everywhere except ``reveal()``."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise DomainValidationError("secret value must be a non-empty string")
        if len(value) > MAX_SECRET_LENGTH:
            raise DomainValidationError(
                f"secret value must be at most {MAX_SECRET_LENGTH} characters"
            )
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Secret({MASK})"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("secrets cannot be serialized")


@dataclass(eq=False)
class Credential:
    """Credential metadata. The secret value is never part of this object;
    the store keeps it encrypted next to it."""

    name: str
    kind: CredentialKind
    description: str = ""
    timestamps: Timestamps = field(default_factory=Timestamps)

    def __post_init__(self) -> None:
        validate_credential_name(self.name)
        self.kind = CredentialKind(self.kind)
        validate_text(self.description, "description")

    @property
    def reference(self) -> str:
        return CredentialReference.to(self.name).ref

    def touch(self, now: datetime | None = None) -> None:
        self.timestamps = Timestamps(
            created_at=self.timestamps.created_at, updated_at=now or utcnow()
        )


def credential_references(config: object) -> frozenset[str]:
    """Every credential reference appearing anywhere in a config document."""
    if isinstance(config, str):
        return frozenset({config}) if is_credential_reference(config) else frozenset()
    if isinstance(config, Mapping):
        values: list[object] = list(config.values())
    elif isinstance(config, list | tuple):
        values = list(config)
    else:
        return frozenset()
    return frozenset().union(*(credential_references(v) for v in values))


def plaintext_secret_paths(config: Mapping[str, object]) -> list[str]:
    """Locations (never values) of secret material in a config document.

    Structural, like redaction: a string under a sensitive key (at any depth)
    must be a credential reference; any other string must not be
    credential-shaped (``Bearer ...``, ``sk-...``, URL userinfo, ...).
    """
    found: list[str] = []

    def walk(value: object, path: str, sensitive: bool) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                walk(item, f"{path}.{key}", sensitive or is_sensitive_key(str(key)))
        elif isinstance(value, list | tuple):
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]", sensitive)
        elif isinstance(value, str) and not is_credential_reference(value):
            if sensitive or redact_text(value) != value:
                found.append(path)

    walk(config, "", False)
    return found
