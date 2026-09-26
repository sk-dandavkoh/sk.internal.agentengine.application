"""CredentialReference: the only secret-adjacent object allowed in projects.

Projects store references, never values (architecture §32). Resolution happens
in a later phase via the Credential Store (Phase 13); the domain only defines
what a valid reference looks like.
"""

from dataclasses import dataclass

from akasha.errors import DomainValidationError


@dataclass(frozen=True)
class CredentialReference:
    """An opaque pointer to a credential in the Credential Store."""

    ref: str

    def __post_init__(self) -> None:
        if not self.ref or not self.ref.strip():
            raise DomainValidationError("credential reference must not be blank")
        if any(c.isspace() for c in self.ref):
            raise DomainValidationError(
                "credential reference must not contain whitespace"
            )
