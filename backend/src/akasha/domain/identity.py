"""Users, sessions and the access rules on them (architecture §59/§60).

Authentication (who is calling) and authorization (what they may touch) stay
separate: this module holds the identity model and the pure access rules;
``akasha.application.auth`` holds the use cases.

V1 is deliberately small: local users, two roles, owner-scoped projects.
Resources carry an owner so richer models (teams, grants, RBAC) can be added
without re-modelling the data.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from akasha.domain.primitives import Entity, Timestamps, new_id, validate_id
from akasha.errors import DomainValidationError

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
# NIST SP 800-63B: length, not composition rules. The upper bound only keeps
# a request from making the server hash megabytes.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256


class Role(StrEnum):
    """ADMIN manages the installation (users, credentials, every project);
    MEMBER works on the projects they own."""

    ADMIN = "admin"
    MEMBER = "member"


def validate_username(username: str) -> str:
    if not isinstance(username, str) or not _USERNAME_RE.match(username):
        raise DomainValidationError(
            "username must be 3-64 characters of a-z, 0-9, '.', '_' or '-'"
            " and start with a letter or digit"
        )
    return username


def validate_password(password: str) -> str:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise DomainValidationError(
            f"password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH}"
            " characters long"
        )
    return password


@dataclass(eq=False)
class User(Entity):
    """A local account. The password hash is not part of the entity: it is
    credential material and travels only between the repository and the
    authentication service."""

    username: str
    role: Role = Role.MEMBER
    disabled: bool = False
    timestamps: Timestamps = field(default_factory=Timestamps)

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        validate_username(self.username)
        self.role = Role(self.role)

    @classmethod
    def create(cls, username: str, *, role: Role = Role.MEMBER) -> "User":
        return cls(id=new_id("user"), username=username, role=role)

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


@dataclass(frozen=True)
class UserSession:
    """A login. ``token_hash`` identifies it; the token itself is shown to
    the client once and never stored."""

    token_hash: str
    user_id: str
    created_at: datetime
    expires_at: datetime


# Personal access tokens start with this, so they are recognizable: by the
# authentication service (which table to look in), by secret scanners and
# by log redaction (``security.redaction``).
PAT_MARKER = "akasha_pat_"
MAX_TOKEN_NAME_LENGTH = 100


def validate_token_name(name: str) -> str:
    if not 0 < len(name) <= MAX_TOKEN_NAME_LENGTH or not name.isprintable():
        raise DomainValidationError(
            f"token name must be 1-{MAX_TOKEN_NAME_LENGTH} printable characters"
        )
    return name


@dataclass(frozen=True)
class AccessToken:
    """A personal access token: a long-lived, named, individually revocable
    credential of one user, for scripts and AI agents (docs/mcp.md §3). It
    acts with exactly its user's role and access. Like sessions, only the
    token's SHA-256 is stored; ``hint`` is its first characters, shown so a
    user can tell tokens apart."""

    id: str
    user_id: str
    name: str
    token_hash: str
    hint: str
    created_at: datetime
    expires_at: datetime | None  # None: until revoked
    last_used_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_id(self.id)
        validate_token_name(self.name)


@dataclass(frozen=True)
class Identity:
    """The authenticated caller of one request: a browser/API login
    (``session``) or a personal access token (``access_token``), never
    both."""

    user: User
    session: UserSession | None = None
    access_token: AccessToken | None = None

    def __post_init__(self) -> None:
        if (self.session is None) == (self.access_token is None):
            raise DomainValidationError(
                "an identity has exactly one credential: a session or a token"
            )

    @property
    def expires_at(self) -> datetime | None:
        """When the credential stops working; None: until revoked."""
        if self.session is not None:
            return self.session.expires_at
        return None if self.access_token is None else self.access_token.expires_at


def may_access_project(user: User, owner_id: str | None) -> bool:
    """Admins see every project; members only their own. A project without
    an owner (created before accounts existed, or by the operator CLI) is
    admin-only, so a missing owner never widens access."""
    return user.is_admin or (owner_id is not None and owner_id == user.id)
