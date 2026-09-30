"""Per-node-attempt credential resolution: the runtime's access boundary.

A node may resolve only references that literally appear in its own frozen
config snapshot. A reference arriving at run time — through an input port,
the execution input or a webhook payload — is refused, so data flowing
through a graph can never pick which secret a node unlocks.

Every resolved value is registered for known-value redaction in the node's
task context: the node's logs, error messages and outputs mask it.
"""

from collections.abc import Mapping
from typing import Any, Protocol

from akasha.application.credentials import CredentialNotFoundError
from akasha.domain.credentials import Secret, credential_references
from akasha.domain.node import NodeError, NodeErrorKind
from akasha.security.crypto import SecretDecryptionError
from akasha.security.redaction import register_secret


class SecretSource(Protocol):
    async def resolve(self, reference: str) -> Secret: ...


class NodeCredentials:
    """``CredentialResolver`` for one node attempt."""

    def __init__(self, source: SecretSource, config: Mapping[str, Any]) -> None:
        self._source = source
        self._allowed = credential_references(config)

    async def resolve(self, reference: str) -> str:
        if reference not in self._allowed:
            raise NodeError(
                "the credential reference is not part of this node's configuration",
                kind=NodeErrorKind.CONFIGURATION,
                code="CREDENTIAL_NOT_REFERENCED",
            )
        try:
            secret = await self._source.resolve(reference)
        except CredentialNotFoundError:
            raise NodeError(
                f"credential not found: {reference}",
                kind=NodeErrorKind.CONFIGURATION,
                code="CREDENTIAL_NOT_FOUND",
            ) from None
        except SecretDecryptionError:
            raise NodeError(
                f"credential cannot be decrypted: {reference}",
                kind=NodeErrorKind.PERMANENT,
                code="CREDENTIAL_UNAVAILABLE",
            ) from None
        value = secret.reveal()
        register_secret(value)
        return value
