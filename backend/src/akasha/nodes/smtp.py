"""core.smtp: send one plain-text e-mail.

Transport is stdlib ``smtplib`` in a worker thread, one connection per
send, closed right after (backend AGENTS §37). The password comes from a
credential reference resolved at execution time; it is never part of the
config, a log line or an error.

ponytail: ``smtplib`` is blocking, so cancellation and node timeouts stop
*waiting* at once, but the thread finishes its SMTP dialogue (bounded by the
socket timeout) — a mail can still go out after cancellation. Hence
``supports_cancellation=False``. Upgrade path: an asyncio SMTP client when
hard cancellation of in-flight sends is required.

Network: the host is resolved once by the destination guard and the client
connects to a vetted address (no DNS-rebinding window); TLS still verifies
the configured host name. A password is never sent over an unencrypted
connection to an untrusted network: ``security="none"`` with a username is
refused unless the operator trusts the destination (private networks
allowed, or the host is on the allowlist).

Idempotency: the Message-ID is derived from the node execution id, which a
retry reuses, so receivers can recognise a duplicate delivery.
"""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate

from akasha.nodes.core import VERSION, closed_schema
from akasha.sdk import (
    CREDENTIAL_REFERENCE_FORMAT,
    Node,
    NodeCapabilities,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)
from akasha.security.network import NetworkPolicy, check_destination

MAX_RECIPIENTS = 50
_ADDRESS = {"type": "string", "pattern": r"^[^\s@<>,]+@[^\s@<>,]+$"}
_DEFAULT_PORTS = {"none": 25, "starttls": 587, "tls": 465}
DEFAULT_SECURITY = "starttls"
DEFAULT_TIMEOUT_SECONDS = 30


def _error(message: str, kind: NodeErrorKind, code: str) -> NodeError:
    return NodeError(message, kind=kind, code=code)


class SmtpNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.smtp",
            name="Send E-Mail (SMTP)",
            description="Sends a plain-text e-mail through an SMTP server.",
            version=VERSION,
            category="communication",
        ),
        inputs=(
            NodeInput("subject", PortType.STRING),
            NodeInput("body", PortType.STRING),
        ),
        outputs=(
            NodeOutput("message_id", PortType.STRING),
            # Partial acceptance is a success (the mail went out); failing
            # would make a retry send duplicates to the accepted recipients.
            NodeOutput("rejected_recipients", PortType.LIST),
        ),
        config_schema=closed_schema(
            {
                "host": {"type": "string", "minLength": 1},
                "port": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 65535,
                    "description": "Empty: 587 for starttls, 465 for tls, 25 for none.",
                },
                "security": {
                    "type": "string",
                    "enum": sorted(_DEFAULT_PORTS),
                    "default": DEFAULT_SECURITY,
                },
                "username": {"type": "string", "minLength": 1},
                "password_credential": {
                    "type": "string",
                    "format": CREDENTIAL_REFERENCE_FORMAT,
                    "description": "Credential holding the SMTP password.",
                },
                "sender": _ADDRESS,
                "recipients": {
                    "type": "array",
                    "items": _ADDRESS,
                    "maxItems": MAX_RECIPIENTS,
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 120,
                    "default": DEFAULT_TIMEOUT_SECONDS,
                },
            },
            "host",
            "sender",
            "recipients",
        ),
        capabilities=NodeCapabilities(has_side_effects=True),
    )

    def __init__(self, policy: NetworkPolicy | None = None) -> None:
        self._policy = policy or NetworkPolicy()

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        cfg = context.config
        if not cfg["recipients"]:
            raise _error(
                "at least one recipient is required",
                NodeErrorKind.CONFIGURATION,
                "SMTP_NO_RECIPIENTS",
            )
        security = cfg.get("security", DEFAULT_SECURITY)
        host, port = cfg["host"], cfg.get("port", _DEFAULT_PORTS[security])
        # Destination first: nothing is decrypted for a host never contacted.
        addresses = await check_destination(host, port, self._policy)
        if security == "none" and "username" in cfg and not self._trusted(host):
            raise _error(
                "SMTP authentication requires TLS (security 'starttls' or 'tls')",
                NodeErrorKind.CONFIGURATION,
                "SMTP_INSECURE_AUTH",
            )
        password = await self._password(context)
        message = self._message(context)
        refused = await asyncio.to_thread(
            _send,
            message,
            host=host,
            addresses=addresses,
            port=port,
            security=security,
            username=cfg.get("username"),
            password=password,
            timeout=cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        )
        return NodeResult(
            {
                "message_id": message["Message-ID"],
                "rejected_recipients": sorted(refused),
            }
        )

    def _trusted(self, host: str) -> bool:
        return self._policy.allow_private_networks or host.lower() in {
            h.lower() for h in self._policy.allowed_hosts
        }

    @staticmethod
    async def _password(context: NodeExecutionContext) -> str | None:
        reference = context.config.get("password_credential")
        if reference is None:
            return None
        if context.credentials is None:
            raise _error(
                "credential references cannot be resolved in this engine",
                NodeErrorKind.CONFIGURATION,
                "SMTP_CREDENTIALS_UNAVAILABLE",
            )
        return await context.credentials.resolve(reference)

    @staticmethod
    def _message(context: NodeExecutionContext) -> EmailMessage:
        message = EmailMessage()
        try:
            # policy.default refuses CR/LF in header values: no header
            # injection through the subject.
            message["From"] = context.config["sender"]
            message["To"] = ", ".join(context.config["recipients"])
            message["Subject"] = context.inputs["subject"]
        except ValueError:
            raise _error(
                "the subject must be a single line",
                NodeErrorKind.VALIDATION,
                "SMTP_INVALID_MESSAGE",
            ) from None
        message["Date"] = formatdate(usegmt=True)
        message["Message-ID"] = f"<{context.node_execution_id}@akasha>"
        message.set_content(context.inputs["body"])
        return message


def _send(
    message: EmailMessage,
    *,
    host: str,
    addresses: tuple[str, ...],
    port: int,
    security: str,
    username: str | None,
    password: str | None,
    timeout: float,
) -> dict[str, tuple[int, bytes]]:
    """Blocking SMTP dialogue; returns refused recipients. Raises only
    ``NodeError``, whose messages never include server replies (they can
    echo credentials or addresses back)."""
    tls = ssl.create_default_context()
    try:
        smtp = _connect(host, addresses, port, security, timeout, tls)
        with smtp:
            if security == "starttls":
                # Never fall back to plaintext when STARTTLS is unavailable.
                smtp.starttls(context=tls)
            if username is not None:
                smtp.login(username, password or "")
            return smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise _error(
            "the SMTP server rejected the credentials",
            NodeErrorKind.PERMANENT,
            "SMTP_AUTH_FAILED",
        ) from None
    except smtplib.SMTPNotSupportedError:
        raise _error(
            "the SMTP server does not support a required extension (STARTTLS/AUTH)",
            NodeErrorKind.PERMANENT,
            "SMTP_NOT_SUPPORTED",
        ) from None
    except smtplib.SMTPRecipientsRefused:
        raise _error(
            "the SMTP server refused all recipients",
            NodeErrorKind.PERMANENT,
            "SMTP_REJECTED",
        ) from None
    except smtplib.SMTPResponseException as exc:
        temporary = 400 <= exc.smtp_code < 500
        raise _error(
            f"the SMTP server answered {exc.smtp_code}",
            NodeErrorKind.TRANSIENT if temporary else NodeErrorKind.PERMANENT,
            "SMTP_TEMPORARY_FAILURE" if temporary else "SMTP_REJECTED",
        ) from None
    except ssl.SSLError:
        raise _error(
            "TLS negotiation with the SMTP server failed",
            NodeErrorKind.PERMANENT,
            "SMTP_TLS_ERROR",
        ) from None
    except TimeoutError:
        raise _timeout(timeout) from None
    except (smtplib.SMTPException, OSError) as exc:
        # smtplib reports a read timeout as SMTPServerDisconnected raised
        # while handling the socket's TimeoutError.
        if isinstance(exc.__context__, TimeoutError):
            raise _timeout(timeout) from None
        # Refused/reset connections, unexpected disconnects.
        raise _error(
            f"SMTP connection failure ({type(exc).__name__})",
            NodeErrorKind.TRANSIENT,
            "SMTP_CONNECTION_ERROR",
        ) from None


def _connect(
    host: str,
    addresses: tuple[str, ...],
    port: int,
    security: str,
    timeout: float,
    tls: ssl.SSLContext,
) -> smtplib.SMTP:
    """Connect to the first reachable vetted address."""

    def attempt(address: str) -> smtplib.SMTP:
        smtp = (
            smtplib.SMTP_SSL(timeout=timeout, context=tls)
            if security == "tls"
            else smtplib.SMTP(timeout=timeout)
        )
        # ponytail: smtplib takes the TLS server name (SMTPS and STARTTLS)
        # from ``_host``, which only its constructor sets; connecting to the
        # address while verifying the name needs this private attribute.
        smtp._host = host  # type: ignore[attr-defined]  # see ponytail above
        try:
            smtp.connect(address, port)
        except BaseException:
            smtp.close()
            raise
        return smtp

    *fallbacks, last = addresses
    for address in fallbacks:
        try:
            return attempt(address)
        except OSError:
            continue  # the next address, like socket.create_connection
    return attempt(last)


def _timeout(seconds: float) -> NodeError:
    return _error(
        f"the SMTP server did not answer within {seconds}s",
        NodeErrorKind.TIMEOUT,
        "SMTP_TIMEOUT",
    )
