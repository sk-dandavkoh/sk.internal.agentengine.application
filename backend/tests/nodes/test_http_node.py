"""core.http against a local HTTP server; SSRF guard (AGENTS §30/§34)."""

import asyncio
import functools
import socket
from typing import Any
from urllib.parse import quote

import httpcore
import pytest

from akasha.domain.node import NodeError, NodeErrorKind, NodeServices
from akasha.errors import NodeConfigurationError
from akasha.nodes.http import HttpNode
from akasha.security.network import NetworkPolicy, check_destination
from tests.support import LocalHttpServer, RunNode, make_context

LOCAL = NodeServices(network=NetworkPolicy(allow_private_networks=True))
STRICT = NodeServices()  # the default policy: public destinations only
CANARY = "sk-test-canary-value-123"  # must never appear in errors


@pytest.fixture
def run_node(run_node: RunNode) -> RunNode:
    """These tests talk to a loopback server: private networks are allowed
    unless a test passes its own ``services``."""
    return functools.partial(run_node, services=LOCAL)


def capped(run_node: RunNode, max_response_bytes: int) -> RunNode:
    return functools.partial(
        run_node, settings={"http_max_response_bytes": max_response_bytes}
    )


async def failure(run_node: RunNode, node: HttpNode, **config: Any) -> NodeError:
    with pytest.raises(NodeError) as exc:
        await run_node(node, config=config)
    return exc.value


# -- requests and responses ----------------------------------------------------


async def test_get_parses_json_and_sends_headers(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    result = await run_node(
        HttpNode(),
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
        HttpNode(),
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
        HttpNode(),
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
        HttpNode(), config={"method": "HEAD", "url": f"{http_server.base_url}/big"}
    )
    assert (head.outputs["status"], head.outputs["body"]) == (200, "")
    text = await run_node(HttpNode(), config={"url": f"{http_server.base_url}/text"})
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
        run_node, HttpNode(), url=f"{http_server.base_url}/status/{status}"
    )
    assert (error.code, error.kind) == ("HTTP_STATUS_ERROR", kind)
    assert error.details == {"status": str(status)}


async def test_error_status_can_be_returned_instead(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    result = await run_node(
        HttpNode(),
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
        HttpNode(), config={"url": f"{http_server.base_url}/redirect?to={target}"}
    )
    assert result.outputs["status"] == 302
    assert result.outputs["headers"]["location"].endswith("/echo")
    assert len(http_server.state.requests) == 1


async def test_redirects_are_followed_when_enabled(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    target = quote(f"{http_server.base_url}/echo")
    result = await run_node(
        HttpNode(),
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
        HttpNode(),
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
    node = HttpNode()
    by_host = NodeServices(
        network=NetworkPolicy(allowed_hosts=frozenset({"127.0.0.1"}))
    )
    for target in (
        "http://169.254.169.254/latest/meta-data/",
        f"http://localhost:{http_server.port}/echo",
    ):
        http_server.state.requests.clear()
        error = await failure(
            functools.partial(run_node, services=by_host),
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
        HttpNode(),
        url=f"{http_server.base_url}/redirect?to={quote('file:///etc/passwd')}",
        follow_redirects=True,
    )
    assert error.code == "HTTP_INVALID_URL"


# -- limits and failures ---------------------------------------------------------


# Body sizes of the local server's endpoints.
_BIG = {"/big": 2048, "/stream-big": 4096}


@pytest.mark.parametrize("short", [1024, 1])  # 1: one byte below the body
@pytest.mark.parametrize("path", sorted(_BIG))
async def test_oversized_responses_are_rejected(
    run_node: RunNode, http_server: LocalHttpServer, path: str, short: int
) -> None:
    """Both a declared Content-Length and a close-delimited body."""
    error = await failure(
        capped(run_node, _BIG[path] - short),
        HttpNode(),
        url=http_server.base_url + path,
    )
    assert (error.code, error.kind) == (
        "HTTP_RESPONSE_TOO_LARGE",
        NodeErrorKind.PERMANENT,
    )


@pytest.mark.parametrize("path", sorted(_BIG))
async def test_response_exactly_at_the_limit_is_accepted(
    run_node: RunNode, http_server: LocalHttpServer, path: str
) -> None:
    result = await capped(run_node, _BIG[path])(
        HttpNode(),
        config={"url": f"{http_server.base_url}{path}"},
    )
    assert len(result.outputs["body"]) == _BIG[path]


async def test_malformed_json_is_a_permanent_error(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    error = await failure(run_node, HttpNode(), url=f"{http_server.base_url}/malformed")
    assert (error.code, error.kind) == (
        "HTTP_MALFORMED_RESPONSE",
        NodeErrorKind.PERMANENT,
    )


async def test_timeout(run_node: RunNode, http_server: LocalHttpServer) -> None:
    error = await failure(
        run_node,
        HttpNode(),
        url=f"{http_server.base_url}/hang",
        timeout_seconds=0.2,
    )
    assert (error.code, error.kind) == ("HTTP_TIMEOUT", NodeErrorKind.TIMEOUT)


async def test_connection_refused_is_transient(run_node: RunNode) -> None:
    with socket.socket() as probe:  # a port that is certainly closed
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    error = await failure(run_node, HttpNode(), url=f"http://127.0.0.1:{port}/")
    assert (error.code, error.kind) == (
        "HTTP_CONNECTION_ERROR",
        NodeErrorKind.TRANSIENT,
    )


async def test_cancellation_aborts_an_in_flight_request(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    task = asyncio.create_task(
        run_node(HttpNode(), config={"url": f"{http_server.base_url}/hang"})
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
        await run_node(HttpNode(), config=config)


async def test_non_string_header_values_are_rejected() -> None:
    config = {"url": "http://127.0.0.1:1/", "headers": {"X-N": 1}}
    # The schema rejects them at publish ...
    with pytest.raises(NodeConfigurationError, match=r"config\.headers\.X-N"):
        HttpNode.definition.validate_config(config)
    # ... and the node still refuses them in versions published before the
    # schema typed header values (the runtime does not re-validate config).
    node = HttpNode()
    with pytest.raises(NodeError) as exc:
        await node.execute(make_context(node, config=config, services=LOCAL))
    assert (exc.value.code, exc.value.kind) == (
        "HTTP_INVALID_HEADERS",
        NodeErrorKind.CONFIGURATION,
    )


def test_header_credentials_must_be_references() -> None:
    schema = HttpNode.definition.config_schema
    ok = {"url": "https://x", "header_credentials": {"Authorization": "credential:crm"}}
    assert schema.issues(ok) == []
    (issue,) = schema.issues({**ok, "header_credentials": {"Authorization": CANARY}})
    assert issue.path == ("header_credentials", "Authorization")
    assert CANARY not in issue.message


async def test_credentials_in_urls_are_rejected(run_node: RunNode) -> None:
    error = await failure(
        run_node, HttpNode(), url=f"http://user:{CANARY}@127.0.0.1:1/"
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
    node = HttpNode()
    for url in blocked_urls(http_server.port):
        error = await failure(
            functools.partial(run_node, services=STRICT), node, url=url
        )
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
        error = await failure(run_node, HttpNode(), url=url, headers=headers)
        assert CANARY not in f"{error.message} {error.details} {error!r}"


# -- DNS pinning and redirect credential scoping (Phase 25) -----------------------


async def test_connection_uses_the_addresses_the_guard_vetted(
    run_node: RunNode, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rebinding resolver answers public first, loopback afterwards. The
    name is resolved once; the client connects only to vetted addresses,
    in resolver order, and never to a second answer."""
    public = ["93.184.215.14", "93.184.215.15"]
    lookups: list[str] = []

    async def rebinding(host: str, port: int, **_: Any) -> list[Any]:
        lookups.append(host)
        answer = public if len(lookups) == 1 else ["127.0.0.1"]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in answer]

    connected: list[str] = []

    async def no_network(self: Any, host: str, port: int, *_: Any, **__: Any) -> Any:
        connected.append(host)
        raise httpcore.ConnectError("test: no network")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", rebinding)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", no_network)

    error = await failure(
        functools.partial(run_node, services=STRICT),
        HttpNode(),
        url="http://rebind.test/",
    )
    assert error.code == "HTTP_CONNECTION_ERROR"
    assert lookups == ["rebind.test"]
    assert connected == public


async def test_one_private_answer_blocks_the_whole_name(
    run_node: RunNode, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def mixed(host: str, port: int, **_: Any) -> list[Any]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
            for a in ("93.184.215.14", "10.0.0.1")
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", mixed)
    error = await failure(
        functools.partial(run_node, services=STRICT),
        HttpNode(),
        url="http://mixed.test/",
    )
    assert error.code == "DESTINATION_BLOCKED"


class OneCredential:
    async def resolve(self, reference: str) -> str:
        assert reference == "credential:crm"
        return CANARY


async def test_credential_headers_do_not_follow_cross_origin_redirects(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    """``localhost`` and ``127.0.0.1`` are different origins (same server)."""
    for target, kept in (
        (f"{http_server.base_url}/echo", True),
        (f"http://localhost:{http_server.port}/echo", False),
    ):
        result = await run_node(
            HttpNode(),
            config={
                "url": f"{http_server.base_url}/redirect?to={quote(target)}",
                "follow_redirects": True,
                "header_credentials": {"X-Api-Key": "credential:crm"},
                "headers": {"X-Trace": "t1"},
            },
            credentials=OneCredential(),
        )
        echoed = result.outputs["body"]["headers"]
        assert ("x-api-key" in echoed) is kept, target
        assert echoed["x-trace"] == "t1"  # ordinary headers still follow


# -- query and URL variables (core.http 1.1.0) -------------------------------


async def test_query_input_is_merged_into_the_url(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    await run_node(
        HttpNode(),
        config={"url": f"{http_server.base_url}/echo?fixed=1"},
        inputs={
            "query": {"q": "a b&c", "n": 2, "on": True, "tag": ["x", "y"], "skip": None}
        },
    )
    (request,) = http_server.state.requests
    assert request.path == "/echo?fixed=1&q=a+b%26c&n=2&on=true&tag=x&tag=y"


async def test_url_variables_are_encoded_into_the_path(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    await run_node(
        HttpNode(),
        config={
            "url": f"{http_server.base_url}/status/{{{{ code }}}}?id={{{{ lead.id }}}}"
        },
        inputs={"variables": {"code": 204, "lead": {"id": "a/../b?x#y@evil"}}},
    )
    (request,) = http_server.state.requests
    # The value is one opaque segment: no traversal, no new query or host.
    assert request.path == "/status/204?id=a%2F..%2Fb%3Fx%23y%40evil"


@pytest.mark.parametrize(
    "url",
    [
        "http://{{ host }}/x",
        "http://127.0.0.1:{{ port }}/x",
        "https://user{{ x }}@example.com/",
    ],
)
async def test_url_variables_cannot_choose_the_server(
    run_node: RunNode, url: str
) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(
            HttpNode(),
            config={"url": url},
            inputs={"variables": {"host": "169.254.169.254", "port": "1", "x": "y"}},
        )
    assert exc.value.code == "HTTP_INVALID_URL"


@pytest.mark.parametrize("variables", [{}, {"id": {"nested": 1}}, {"id": [1]}])
async def test_missing_or_structured_url_variables_fail(
    run_node: RunNode, http_server: LocalHttpServer, variables: dict[str, Any]
) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(
            HttpNode(),
            config={"url": f"{http_server.base_url}/echo/{{{{ id }}}}"},
            inputs={"variables": variables},
        )
    assert exc.value.code == "HTTP_URL_VARIABLE_MISSING"
    assert not http_server.state.requests
