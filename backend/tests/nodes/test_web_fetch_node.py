"""core.web_fetch against the local fixture server: text extraction,
limits, status classification and the SSRF guard on every hop."""

import pytest

from akasha.domain.node import NodeError, NodeErrorKind, NodeServices
from akasha.nodes.http import WebFetchNode, html_to_text
from akasha.security.network import NetworkPolicy
from tests.support import LocalHttpServer, RunNode

LOCAL = NetworkPolicy(allowed_hosts=frozenset({"127.0.0.1"}))


async def fetch(run_node: RunNode, url: str, **config: object) -> dict[str, object]:
    result = await run_node(
        WebFetchNode(),
        config=config,
        inputs={"url": url},
        services=NodeServices(network=LOCAL),
    )
    return dict(result.outputs)


async def failure(
    run_node: RunNode, url: str, policy: NetworkPolicy = LOCAL
) -> NodeError:
    with pytest.raises(NodeError) as exc:
        await run_node(
            WebFetchNode(), inputs={"url": url}, services=NodeServices(network=policy)
        )
    return exc.value


async def test_html_becomes_readable_text(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    out = await fetch(run_node, f"{http_server.base_url}/page")
    assert out["status"] == 200 and out["title"] == "Akasha & Agents"
    assert out["text"] == "Home\nAgent engines\nTools <run> in a loop.\nsearch\nfetch"
    assert out["truncated"] is False


def test_extraction_drops_scripts_styles_and_markup() -> None:
    title, text = html_to_text(
        "<title>T</title><script>evil()</script><style>p{}</style>"
        "<p>a<b>b</b></p><svg><text>no</text></svg><p>c &amp; d</p>"
    )
    assert (title, text) == ("T", "ab\nc & d")


async def test_non_html_is_returned_as_decoded_text(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    out = await fetch(run_node, f"{http_server.base_url}/text")
    assert (out["title"], out["text"]) == ("", "café")


async def test_text_is_cut_at_max_chars(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    out = await fetch(run_node, f"{http_server.base_url}/big", max_chars=100)
    assert out["text"] == "x" * 100 and out["truncated"] is True


@pytest.mark.parametrize(
    ("status", "kind"),
    [(404, NodeErrorKind.PERMANENT), (503, NodeErrorKind.TRANSIENT)],
)
async def test_error_status_is_classified(
    run_node: RunNode, http_server: LocalHttpServer, status: int, kind: NodeErrorKind
) -> None:
    error = await failure(run_node, f"{http_server.base_url}/status/{status}")
    assert (error.code, error.kind) == ("HTTP_STATUS_ERROR", kind)


async def test_private_destinations_are_blocked_by_default(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    error = await failure(run_node, f"{http_server.base_url}/page", NetworkPolicy())
    assert error.code == "DESTINATION_BLOCKED"
    assert http_server.state.requests == []


async def test_redirects_are_followed_and_every_hop_is_checked(
    run_node: RunNode, http_server: LocalHttpServer
) -> None:
    base = http_server.base_url
    out = await fetch(run_node, f"{base}/redirect?to=/page")
    assert out["title"] == "Akasha & Agents"
    # localhost resolves to loopback but is not the trusted host literal.
    hop = f"http://localhost:{http_server.port}/page"
    error = await failure(run_node, f"{base}/redirect?to={hop}")
    assert error.code == "DESTINATION_BLOCKED"


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "gopher://example.test", "javascript:alert(1)"]
)
async def test_only_http_urls_are_fetched(run_node: RunNode, url: str) -> None:
    error = await failure(run_node, url)
    assert (error.code, error.kind) == ("HTTP_INVALID_URL", NodeErrorKind.VALIDATION)
