"""core.http against a local HTTP server; SSRF guard (AGENTS §30/§34)."""

import asyncio
import socket
from typing import Any
from urllib.parse import quote

import pytest

from akasha.domain.node import NodeError, NodeErrorKind
from akasha.errors import NodeConfigurationError
from akasha.nodes.http import HttpNode
from akasha.security.network import NetworkPolicy, check_destination
from tests.support import LocalHttpServer, RunNode

LOCAL = NetworkPolicy(allow_private_networks=True)
CANARY = "sk-test-canary-value-123"  # must never appear in errors


def local_node(**kwargs: Any) -> HttpNode:
    return HttpNode(LOCAL, **kwargs)


async def failure(run_node: RunNode, node: HttpNode, **config: Any) -> NodeError:
    with pytest.raises(NodeError) as exc:
        await run_node(node, config=config)
    return exc.value


# -- requests and responses ----------------------------------------------------


async def test_get_parses_json_and_sends_headers(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    result = await run_node(
        local_node(),
        config={
            "url": f"{http_server.base_url}/echo?x=1",
            "headers": {"X-Trace": "t1"},
        },
    )
    out = result.outputs
    assert out["status"] == 200
    assert out["headers"]["content-type"] == "application/json"
    assert out["body"]["method"] == "GET"
    assert out["body"]["query"] == {"x": "1"}
    assert out["body"]["headers"]["x-trace"] == "t1"
    assert "content-type" not in out["body"]["headers"]  # no body, no type


async def test_post_sends_the_body_input_as_json(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    result = await run_node(
        local_node(),
        config={"method": "POST", "url": f"{http_server.base_url}/echo"},
        inputs={"body": {"lead": "Ada", "n": [1, None]}},
    )
    (request,) = http_server.state.requests
    assert request.method == "POST"
    assert request.headers["content-type"] == "application/json"
    assert result.outputs["body"]["body"] == '{"lead": "Ada", "n": [1, null]}'


async def test_explicit_content_type_is_kept(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    await run_node(
        local_node(),
        config={
            "method": "PUT",
            "url": f"{http_server.base_url}/echo",
            "headers": {"Content-Type": "application/vnd.demo+json"},
        },
        inputs={"body": None},
    )
    (request,) = http_server.state.requests
    assert request.headers["content-type"] == "application/vnd.demo+json"
    assert request.body == b"null"


async def test_head_and_text_responses(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    head = await run_node(
        local_node(), config={"method": "HEAD", "url": f"{http_server.base_url}/big"}
    )
    assert (head.outputs["status"], head.outputs["body"]) == (200, "")
    text = await run_node(local_node(), config={"url": f"{http_server.base_url}/text"})
    assert text.outputs["body"] == "café"  # declared charset honoured


# -- status handling -------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, NodeErrorKind.PERMANENT),
        (404, NodeErrorKind.PERMANENT),
        (429, NodeErrorKind.TRANSIENT),
        (500, NodeErrorKind.TRANSIENT),
        (503, NodeErrorKind.TRANSIENT),
    ],
)
async def test_error_status_is_classified(
    run_node: RunNode, http_server: LocalHttpServer, status: int, kind: NodeErrorKind
) -> None:
    error = await failure(
        run_node, local_node(), url=f"{http_server.base_url}/status/{status}"
    )
    assert (error.code, error.kind) == ("HTTP_STATUS_ERROR", kind)
    assert error.details == {"status": str(status)}


async def test_error_status_can_be_returned_instead(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    result = await run_node(
        local_node(),
        config={
            "url": f"{http_server.base_url}/status/404",
            "fail_on_error_status": False,
        },
    )
    assert (result.outputs["status"], result.outputs["body"]) == (404, "status 404")


# -- redirects -------------------------------------------------------------------


async def test_redirects_are_not_followed_by_default(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    target = quote(f"{http_server.base_url}/echo")
    result = await run_node(
        local_node(), config={"url": f"{http_server.base_url}/redirect?to={target}"}
    )
    assert result.outputs["status"] == 302
    assert result.outputs["headers"]["location"].endswith("/echo")
    assert len(http_server.state.requests) == 1


async def test_redirects_are_followed_when_enabled(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    target = quote(f"{http_server.base_url}/echo")
    result = await run_node(
        local_node(),
        config={
            "url": f"{http_server.base_url}/redirect?to={target}",
            "follow_redirects": True,
        },
    )
    assert (result.outputs["status"], result.outputs["body"]["method"]) == (200, "GET")
    assert [r.path for r in http_server.state.requests][-1] == "/echo"


async def test_redirect_loops_are_bounded(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    error = await failure(
        run_node,
        local_node(),
        url=f"{http_server.base_url}/loop",
        follow_redirects=True,
    )
    assert (error.code, error.kind) == (
        "HTTP_TOO_MANY_REDIRECTS",
        NodeErrorKind.PERMANENT,
    )
    assert len(http_server.state.requests) == 6  # first request + 5 redirects


async def test_every_redirect_hop_passes_the_destination_guard(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    """The operator trusts 127.0.0.1 only; a redirect to another private
    destination (cloud metadata, ``localhost``) is blocked before any
    connection is made."""
    node = HttpNode(NetworkPolicy(allowed_hosts=frozenset({"127.0.0.1"})))
    for target in (
        "http://169.254.169.254/latest/meta-data/",
        f"http://localhost:{http_server.port}/echo",
    ):
        http_server.state.requests.clear()
        error = await failure(
            run_node,
            node,
            url=f"{http_server.base_url}/redirect?to={quote(target)}",
            follow_redirects=True,
        )
        assert error.code == "DESTINATION_BLOCKED"
        assert len(http_server.state.requests) == 1  # only the first hop


async def test_redirect_to_a_non_http_scheme_is_refused(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    error = await failure(
        run_node,
        local_node(),
        url=f"{http_server.base_url}/redirect?to={quote('file:///etc/passwd')}",
        follow_redirects=True,
    )
    assert error.code == "HTTP_INVALID_URL"


# -- limits and failures ---------------------------------------------------------


@pytest.mark.parametrize("path", ["/big", "/stream-big"])
async def test_oversized_responses_are_rejected(
    run_node: RunNode, http_server: LocalHttpServer, path: str
) -> None:
    """Both a declared Content-Length and a close-delimited body."""
    error = await failure(
        run_node, local_node(max_response_bytes=1024), url=http_server.base_url + path
    )
    assert (error.code, error.kind) == (
        "HTTP_RESPONSE_TOO_LARGE",
        NodeErrorKind.PERMANENT,
    )


async def test_response_within_the_limit_is_accepted(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    result = await run_node(
        local_node(max_response_bytes=2048),
        config={"url": f"{http_server.base_url}/big"},
    )
    assert len(result.outputs["body"]) == 2048


async def test_malformed_json_is_a_permanent_error(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    error = await failure(
        run_node, local_node(), url=f"{http_server.base_url}/malformed"
    )
    assert (error.code, error.kind) == (
        "HTTP_MALFORMED_RESPONSE",
        NodeErrorKind.PERMANENT,
    )


async def test_timeout(run_node: RunNode, http_server: LocalHttpServer) -> None:
    error = await failure(
        run_node,
        local_node(),
        url=f"{http_server.base_url}/hang",
        timeout_seconds=0.2,
    )
    assert (error.code, error.kind) == ("HTTP_TIMEOUT", NodeErrorKind.TIMEOUT)


async def test_connection_refused_is_transient(run_node: RunNode) -> None:
    with socket.socket() as probe:  # a port that is certainly closed
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    error = await failure(run_node, local_node(), url=f"http://127.0.0.1:{port}/")
    assert (error.code, error.kind) == (
        "HTTP_CONNECTION_ERROR",
        NodeErrorKind.TRANSIENT,
    )


async def test_cancellation_aborts_an_in_flight_request(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    task = asyncio.create_task(
        run_node(local_node(), config={"url": f"{http_server.base_url}/hang"})
    )
    # The server signals when the request is in flight (bounded wait).
    assert await asyncio.to_thread(http_server.state.hang_entered.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# -- configuration ---------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"url": "file:///etc/passwd"},
        {"url": "ftp://example.test/"},
        {"url": "http://example.test", "method": "TRACE"},
        {"url": "http://example.test", "timeout_seconds": 0},
        {"url": "http://example.test", "timeout_seconds": 121},
        {"url": "http://example.test", "unknown": True},
    ],
)
async def test_invalid_config_is_rejected_at_validation(
    run_node: RunNode, config: dict[str, Any]
) -> None:
    with pytest.raises(NodeConfigurationError):
        await run_node(local_node(), config=config)


async def test_non_string_header_values_are_rejected(run_node: RunNode) -> None:
    error = await failure(
        run_node, local_node(), url="http://127.0.0.1:1/", headers={"X-N": 1}
    )
    assert (error.code, error.kind) == (
        "HTTP_INVALID_HEADERS",
        NodeErrorKind.CONFIGURATION,
    )


async def test_credentials_in_urls_are_rejected(run_node: RunNode) -> None:
    error = await failure(
        run_node, local_node(), url=f"http://user:{CANARY}@127.0.0.1:1/"
    )
    assert error.code == "HTTP_INVALID_URL"
    assert CANARY not in error.message and CANARY not in str(error.details)


# -- security --------------------------------------------------------------------


def blocked_urls(port: int) -> list[str]:
    return [
        f"http://127.0.0.1:{port}/echo",
        f"http://localhost:{port}/echo",
        f"http://[::1]:{port}/",
        f"http://[::ffff:127.0.0.1]:{port}/",
        f"http://0.0.0.0:{port}/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",
        "http://[fd00::1]/",
        "http://[fe80::1]/",
        "http://224.0.0.1/",
    ]


async def test_private_destinations_are_blocked_by_default(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    node = HttpNode()  # default policy: public destinations only
    for url in blocked_urls(http_server.port):
        error = await failure(run_node, node, url=url)
        assert (error.code, error.kind) == (
            "DESTINATION_BLOCKED",
            NodeErrorKind.PERMANENT,
        ), url
    assert http_server.state.requests == []  # nothing ever connected


async def test_guard_allows_public_addresses_without_connecting() -> None:
    for host in ("93.184.215.14", "2606:4700::1111"):
        await check_destination(host, 443, NetworkPolicy())


async def test_guard_allowlist_is_case_insensitive() -> None:
    policy = NetworkPolicy(allowed_hosts=frozenset({"LOCALHOST"}))
    await check_destination("localhost", 80, policy)
    with pytest.raises(NodeError, match="not a public address"):
        await check_destination("127.0.0.1", 80, policy)


async def test_secrets_in_headers_never_reach_errors(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    headers = {"Authorization": f"Bearer {CANARY}"}
    for url in (f"{http_server.base_url}/status/500", "http://127.0.0.1:1/"):
        error = await failure(run_node, local_node(), url=url, headers=headers)
        assert CANARY not in f"{error.message} {error.details} {error!r}"
