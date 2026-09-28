"""core.smtp against an ephemeral Mailpit container (AGENTS §31/§37).

Mailpit requires SMTP AUTH, so authentication success and failure are both
exercised; every delivered message is verified through Mailpit's API.
"""

import asyncio
import logging
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from akasha.domain.node import NodeError, NodeErrorKind
from akasha.errors import NodeConfigurationError
from akasha.nodes.smtp import SmtpNode
from akasha.security.network import NetworkPolicy
from tests.support import SMTP_LOGIN, Mailpit, RunNode

LOCAL = NetworkPolicy(allow_private_networks=True)
USER, PASSWORD = SMTP_LOGIN
REF = "credential:smtp-test"


class FakeCredentials:
    """Stands in for the Phase 13 credential store."""

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def resolve(self, reference: str) -> str:
        return self._secrets[reference]


GOOD = FakeCredentials({REF: PASSWORD})
BAD = FakeCredentials({REF: "wrong-password"})


def config(mail: Mailpit, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "host": mail.smtp_host,
        "port": mail.smtp_port,
        "security": "none",
        "username": USER,
        "password_credential": REF,
        "sender": "engine@akasha.test",
        "recipients": ["ada@example.test"],
        "timeout_seconds": 10,
    }
    return {**base, **overrides}


MESSAGE = {"subject": "Lead qualified", "body": "Ada scored 0.9.\nNext: call."}


async def send(
    run_node: RunNode,
    cfg: dict[str, Any],
    *,
    credentials: FakeCredentials | None = GOOD,
    inputs: dict[str, Any] | None = None,
    node: SmtpNode | None = None,
) -> dict[str, Any]:
    result = await run_node(
        node or SmtpNode(LOCAL),
        config=cfg,
        inputs=inputs or MESSAGE,
        credentials=credentials,
    )
    return dict(result.outputs)


async def failure(run_node: RunNode, cfg: dict[str, Any], **kwargs: Any) -> NodeError:
    with pytest.raises(NodeError) as exc:
        await send(run_node, cfg, **kwargs)
    error = exc.value
    assert PASSWORD not in f"{error.message} {error.details} {error!r}"
    return error


@dataclass
class SilentServer:
    port: int
    accepted: threading.Event


@pytest.fixture
def silent_server() -> Iterator[SilentServer]:
    """Accepts one TCP connection but never sends an SMTP greeting."""
    accepted = threading.Event()
    with socket.create_server(("127.0.0.1", 0)) as server:
        conns: list[socket.socket] = []

        def accept() -> None:
            conns.append(server.accept()[0])  # held open, silently
            accepted.set()

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        try:
            yield SilentServer(server.getsockname()[1], accepted)
        finally:
            for conn in conns:  # EOF lets a still-waiting SMTP thread end
                conn.close()


async def test_sends_a_message(run_node: RunNode, mailbox: Mailpit) -> None:
    out = await send(
        run_node, config(mailbox, recipients=["ada@example.test", "bob@example.test"])
    )
    assert out["rejected_recipients"] == []
    (summary,) = mailbox.messages()
    message = mailbox.message(summary["ID"])
    assert message["Subject"] == "Lead qualified"
    assert message["From"]["Address"] == "engine@akasha.test"
    assert sorted(a["Address"] for a in message["To"]) == [
        "ada@example.test",
        "bob@example.test",
    ]
    assert message["Text"].replace("\r\n", "\n").strip() == MESSAGE["body"]
    # Deterministic per node execution: a retried send is recognisable.
    assert out["message_id"] == f"<{message['MessageID']}>"
    assert out["message_id"].startswith("<nexec_")


async def test_wrong_password_is_an_auth_failure(
    run_node: RunNode, mailbox: Mailpit
) -> None:
    error = await failure(run_node, config(mailbox), credentials=BAD)
    assert (error.code, error.kind) == ("SMTP_AUTH_FAILED", NodeErrorKind.PERMANENT)
    assert mailbox.messages() == []


async def test_server_requiring_auth_rejects_anonymous_mail(
    run_node: RunNode, mailbox: Mailpit
) -> None:
    cfg = config(mailbox)
    del cfg["username"], cfg["password_credential"]
    error = await failure(run_node, cfg)
    assert (error.code, error.kind) == ("SMTP_REJECTED", NodeErrorKind.PERMANENT)
    assert mailbox.messages() == []


async def test_starttls_is_never_downgraded_to_plaintext(
    run_node: RunNode, mailbox: Mailpit
) -> None:
    """Mailpit offers no STARTTLS here: the node must stop before AUTH."""
    error = await failure(run_node, config(mailbox, security="starttls"))
    assert (error.code, error.kind) == ("SMTP_NOT_SUPPORTED", NodeErrorKind.PERMANENT)
    assert mailbox.messages() == []


async def test_implicit_tls_against_a_plaintext_server_fails(
    run_node: RunNode, mailbox: Mailpit
) -> None:
    error = await failure(run_node, config(mailbox, security="tls"))
    assert error.code in {"SMTP_TLS_ERROR", "SMTP_CONNECTION_ERROR"}
    assert mailbox.messages() == []


async def test_connection_refused_is_transient(
    run_node: RunNode, mailbox: Mailpit
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    error = await failure(run_node, config(mailbox, host="127.0.0.1", port=port))
    assert (error.code, error.kind) == (
        "SMTP_CONNECTION_ERROR",
        NodeErrorKind.TRANSIENT,
    )


async def test_silent_server_times_out(
    run_node: RunNode, mailbox: Mailpit, silent_server: SilentServer
) -> None:
    error = await failure(
        run_node,
        config(mailbox, host="127.0.0.1", port=silent_server.port, timeout_seconds=1),
    )
    assert (error.code, error.kind) == ("SMTP_TIMEOUT", NodeErrorKind.TIMEOUT)


async def test_header_injection_through_the_subject_is_refused(
    run_node: RunNode, mailbox: Mailpit
) -> None:
    error = await failure(
        run_node,
        config(mailbox),
        inputs={"subject": "hi\r\nBcc: victim@example.test", "body": "x"},
    )
    assert (error.code, error.kind) == (
        "SMTP_INVALID_MESSAGE",
        NodeErrorKind.VALIDATION,
    )
    assert mailbox.messages() == []


async def test_credential_reference_without_a_resolver(
    run_node: RunNode, mailbox: Mailpit
) -> None:
    error = await failure(run_node, config(mailbox), credentials=None)
    assert (error.code, error.kind) == (
        "SMTP_CREDENTIALS_UNAVAILABLE",
        NodeErrorKind.CONFIGURATION,
    )


async def test_private_smtp_hosts_are_blocked_by_default(
    run_node: RunNode, mailbox: Mailpit
) -> None:
    error = await failure(run_node, config(mailbox), node=SmtpNode())
    assert (error.code, error.kind) == ("DESTINATION_BLOCKED", NodeErrorKind.PERMANENT)
    assert mailbox.messages() == []


async def test_empty_recipient_list(run_node: RunNode, mailbox: Mailpit) -> None:
    error = await failure(run_node, config(mailbox, recipients=[]))
    assert error.code == "SMTP_NO_RECIPIENTS"


@pytest.mark.parametrize(
    "overrides",
    [
        {"recipients": ["ada@example.test\r\nBcc: x@example.test"]},
        {"recipients": ["not-an-address"]},
        {"recipients": [f"u{i}@example.test" for i in range(51)]},
        {"sender": "Engine <engine@akasha.test>"},
        {"port": 0},
        {"port": 70000},
        {"security": "ssl"},
        {"password": "plaintext-in-config"},  # secrets are references only
    ],
)
async def test_invalid_config(
    run_node: RunNode, mailbox: Mailpit, overrides: dict[str, Any]
) -> None:
    with pytest.raises(NodeConfigurationError):
        await send(run_node, config(mailbox, **overrides))


async def test_password_never_reaches_logs(
    run_node: RunNode, mailbox: Mailpit, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await send(run_node, config(mailbox))
    await failure(run_node, config(mailbox), credentials=BAD)
    assert PASSWORD not in caplog.text


async def test_cancellation_stops_waiting_for_the_server(
    run_node: RunNode, mailbox: Mailpit, silent_server: SilentServer
) -> None:
    """Documented ceiling: the SMTP thread runs to its socket timeout, but
    the node itself returns control on cancellation at once."""
    task = asyncio.create_task(
        send(
            run_node,
            config(
                mailbox, host="127.0.0.1", port=silent_server.port, timeout_seconds=5
            ),
        )
    )
    # The SMTP thread is connected and blocked waiting for a greeting.
    assert await asyncio.to_thread(silent_server.accepted.wait, 5)
    task.cancel()
    async with asyncio.timeout(1):  # far below the 5 s socket timeout
        with pytest.raises(asyncio.CancelledError):
            await task
