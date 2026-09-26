"""Error-handling foundation.

One base class for all engine errors with a stable machine-readable code and
an HTTP status. API error handlers map these to structured responses
(API layer), so domain/application errors never surface as bare HTTP 500s.
"""


class AkashaError(Exception):
    """Base class for all engine errors."""

    code = "INTERNAL_ERROR"
    http_status = 500

    def __init__(self, message: str, *, details: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(AkashaError):
    code = "CONFIGURATION_ERROR"
    http_status = 500
