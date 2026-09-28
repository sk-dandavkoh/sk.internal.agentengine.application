"""Error-handling foundation.

One base class for all engine errors with a stable machine-readable code and
an HTTP status. API error handlers map these to structured responses
(API layer), so domain/application errors never surface as bare HTTP 500s.
"""

from collections.abc import Mapping


class AkashaError(Exception):
    """Base class for all engine errors."""

    code = "INTERNAL_ERROR"
    http_status = 500

    def __init__(
        self, message: str, *, details: Mapping[str, object] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})


class ConfigurationError(AkashaError):
    code = "CONFIGURATION_ERROR"
    http_status = 500


class DomainValidationError(AkashaError, ValueError):
    """A domain invariant was violated (bad construction or invalid edge).

    Also a ``ValueError``: it *is* one, and callers that construct domain
    objects from untrusted input may catch either.
    """

    code = "DOMAIN_VALIDATION_ERROR"
    http_status = 422


class InvalidStateTransitionError(AkashaError):
    """A lifecycle state transition is not legal from the current state."""

    code = "INVALID_STATE_TRANSITION"
    http_status = 409


class NodeContractError(AkashaError):
    """A Node definition itself is malformed (developer error, not user data)."""

    code = "NODE_CONTRACT_ERROR"
    http_status = 500


class NodeConfigurationError(AkashaError):
    """A node instance's configuration violates the node's config schema."""

    code = "NODE_CONFIGURATION_ERROR"
    http_status = 422


class NodeValidationError(AkashaError):
    """Runtime values for a node's ports violate the port contract."""

    code = "NODE_VALIDATION_ERROR"
    http_status = 422
