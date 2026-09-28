"""Domain primitives: IDs, time, entity identity.

Pure stdlib on purpose — the domain layer stays infrastructure-free.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from akasha.errors import DomainValidationError

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*_[0-9a-f]{32}$")

# Loose semver: MAJOR.MINOR.PATCH with optional pre-release/build suffix.
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def utcnow() -> datetime:
    """Current time, always timezone-aware UTC."""
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """Generate a prefixed unique ID, e.g. ``proj_3f2a...``.

    Prefixes keep logs and debugging readable; the UUID part guarantees
    uniqueness. ``validate_id`` protects constructors against garbage IDs.
    """
    if not prefix or not prefix.isidentifier() or prefix != prefix.lower():
        msg = f"invalid id prefix: {prefix!r}"
        raise ValueError(msg)
    return f"{prefix}_{uuid.uuid4().hex}"


def validate_id(value: str) -> str:
    """Return the ID if it has the canonical ``<prefix>_<hex>`` shape."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise DomainValidationError(f"malformed domain id: {value!r}")
    return value


def validate_name(value: str) -> str:
    """Non-blank human-readable name."""
    if not value.strip():
        raise DomainValidationError("name must not be blank")
    return value


@dataclass(eq=False)
class Entity:
    """Base for domain entities: equality and hash by identity, not by fields.

    Two entities are the same iff they are the same type and share the same
    id — field values are mutable for drafts and irrelevant to identity.
    """

    id: str

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and self.id == other.id

    def __hash__(self) -> int:
        return hash((type(self).__name__, self.id))


@dataclass(frozen=True)
class Timestamps:
    """Immutable creation/update markers stored on domain objects."""

    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
