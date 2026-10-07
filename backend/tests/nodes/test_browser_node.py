"""core.browser with real headless Chromium against a local test server.

Skipped (with the reason) when the optional ``browser`` extra or its
Chromium build is not installed; nothing here is simulated. The server
records every request it receives, so the tests prove what the browser
could *not* reach as well as what it did.
"""

import asyncio
import functools
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from akasha.domain.node import NodeError, NodeErrorKind, NodeServices
from akasha.nodes.agent import tool_parameters
from akasha.nodes.browser import BrowserNode
from akasha.security.network import NetworkPolicy
from tests.support import RunNode, make_context

GUARD = 30

FORM = """<!doctype html><html><head><title>Form</title></head><body>
<input id="name"><select id="color"><option>red</option><option>blue</option></select>
<button id="go" onclick="go()">Go</button><p id="out"></p><ul id="items"></ul>
<a class="link" href="/a">A</a><a class="link" href="/b">B</a>
<script>
function go() {
  const name = document.getElementById('name').value;
  const color = document.getElementById('color').value;
  fetch('/api/items').then(r => r.json()).then(items => {
    document.getElementById('out').textContent = 'Hello ' + name + ' (' + color + ')';
    for (const i of items) {
      const li = document.createElement('li');
      li.textContent = i;
      items_el.appendChild(li);
    }
  });
}
const items_el = document.getElementById('items');
</script></body></html>"""


@dataclass
class Site:
    base: str
    port: int
    paths: list[str] = field(default_factory=list)
    release: threading.Event = field(default_factory=threading.Event)
    hang_entered: threading.Event = field(default_factory=threading.Event)


def _handler(site: Site) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            """Quiet."""

        def send(
            self, body: str, ctype: str = "text/html", status: int = 200, **headers: str
        ) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers.items():
                self.send_header(k.replace("_", "-"), v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            site.paths.append(self.path)
            match self.path:
                case "/form":
                    self.send(FORM)
                case "/api/items":
                    self.send(json.dumps(["one", "two"]), "application/json")
                case "/cookie":
                    self.send("<p>set</p>", Set_Cookie="sid=abc; Path=/")
                case "/show-cookie":
                    self.send(
                        "<p id='c'></p>"
                        "<script>c.textContent='['+document.cookie+']'</script>"
                    )
                case "/private-sub":
                    # A same-page request to a host the policy does not trust.
                    self.send(
                        f"<p>main</p><img src='http://localhost:{site.port}/secret'>"
                        f"<script>fetch('http://localhost:{site.port}/secret')</script>"
                    )
                case "/redirect-out":
                    self.send(
                        "", status=302, Location=f"http://localhost:{site.port}/secret"
                    )
                case "/hang":
                    site.hang_entered.set()
                    site.release.wait(30)
                    self.send("late")
                case "/missing":
                    self.send("nope", status=404)
                case _:
                    self.send("<p>other</p>")

    return Handler


@pytest.fixture
def site() -> Iterator[Site]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_address[1]
    state = Site(f"http://127.0.0.1:{port}", int(port))
    server.RequestHandlerClass = _handler(state)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()


@functools.cache
def _chromium_missing() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "the optional 'browser' extra (playwright) is not installed"
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:  # any launch failure means: no usable browser
        return f"Chromium is not usable: {type(exc).__name__}"
    return None


@pytest.fixture(autouse=True)
def _requires_chromium() -> None:
    if reason := _chromium_missing():
        pytest.skip(reason)


def services(tmp_path: Path | None = None) -> NodeServices:
    """Trusts exactly 127.0.0.1 (the test site); every other private or
    loopback destination — ``localhost`` included — stays blocked."""
    return NodeServices(
        network=NetworkPolicy(allowed_hosts=frozenset({"127.0.0.1"})),
        workspace_dir=tmp_path,
    )


@pytest.fixture
def run_node(run_node: RunNode) -> RunNode:
    return functools.partial(run_node, services=services())


async def test_fills_a_form_runs_its_javascript_and_extracts(
    run_node: RunNode, site: Site
) -> None:
    result = await run_node(
        BrowserNode(),
        config={
            "steps": [
                {"action": "fill", "selector": "#name", "value": "{{ values.name }}"},
                {"action": "select", "selector": "#color", "value": "blue"},
                {"action": "click", "selector": "#go"},
                {"action": "wait_for", "selector": "#items li"},
                {"action": "extract", "selector": "#out", "name": "greeting"},
                {
                    "action": "extract",
                    "selector": "#items li",
                    "name": "items",
                    "all": True,
                },
                {
                    "action": "extract",
                    "selector": "a.link",
                    "name": "links",
                    "attribute": "href",
                    "all": True,
                },
            ]
        },
        inputs={"url": f"{site.base}/form", "values": {"name": "Ada"}},
    )
    out = result.outputs
    assert out["title"] == "Form"
    assert out["url"] == f"{site.base}/form"
    assert out["extracted"] == {
        "greeting": "Hello Ada (blue)",
        "items": ["one", "two"],
        "links": ["/a", "/b"],
    }
    assert "Hello Ada (blue)" in out["text"]
    # The page's own fetch went through the engine to the real server.
    assert site.paths == ["/form", "/api/items"]


async def test_screenshot_is_written_into_the_workspace(
    run_node: RunNode, site: Site, tmp_path: Path
) -> None:
    browser = BrowserNode()
    context = make_context(
        browser,
        config={"steps": [{"action": "screenshot", "path": "shots/page.png"}]},
        inputs={"url": f"{site.base}/form"},
        services=services(tmp_path),
    )
    await browser.execute(context)
    image = (tmp_path / context.workflow_id / "shots" / "page.png").read_bytes()
    assert image.startswith(b"\x89PNG")


async def test_screenshot_path_cannot_leave_the_workspace(
    run_node: RunNode, site: Site, tmp_path: Path
) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(
            BrowserNode(),
            config={"steps": [{"action": "screenshot", "path": "../../escape.png"}]},
            inputs={"url": f"{site.base}/form"},
            services=services(tmp_path),
        )
    assert exc.value.code == "PATH_NOT_ALLOWED"


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://localhost:{port}/secret",  # loopback by name
        "http://10.0.0.1/",  # private network
    ],
)
async def test_blocked_destinations_are_never_requested(
    run_node: RunNode, site: Site, url: str
) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(BrowserNode(), inputs={"url": url.format(port=site.port)})
    assert exc.value.code == "DESTINATION_BLOCKED"
    assert site.paths == []


async def test_page_subrequests_to_blocked_hosts_fail(
    run_node: RunNode, site: Site
) -> None:
    result = await run_node(BrowserNode(), inputs={"url": f"{site.base}/private-sub"})
    assert result.outputs["text"] == "main"
    assert "/secret" not in site.paths  # neither the image nor the fetch


async def test_every_redirect_hop_is_checked(run_node: RunNode, site: Site) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(BrowserNode(), inputs={"url": f"{site.base}/redirect-out"})
    assert exc.value.code == "DESTINATION_BLOCKED"
    assert site.paths == ["/redirect-out"]


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://x/"]
)
async def test_only_http_urls_open(run_node: RunNode, url: str) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(BrowserNode(), inputs={"url": url})
    assert exc.value.code == "HTTP_INVALID_URL"


async def test_attempts_share_no_cookies(run_node: RunNode, site: Site) -> None:
    await run_node(BrowserNode(), inputs={"url": f"{site.base}/cookie"})
    shown = await run_node(
        BrowserNode(),
        config={"steps": [{"action": "extract", "selector": "#c", "name": "c"}]},
        inputs={"url": f"{site.base}/show-cookie"},
    )
    assert shown.outputs["extracted"]["c"] == "[]"


async def test_error_status_and_missing_selector_fail_clearly(
    run_node: RunNode, site: Site
) -> None:
    with pytest.raises(NodeError) as status:
        await run_node(BrowserNode(), inputs={"url": f"{site.base}/missing"})
    assert (status.value.code, status.value.kind) == (
        "HTTP_STATUS_ERROR",
        NodeErrorKind.PERMANENT,
    )
    with pytest.raises(NodeError) as step:
        await run_node(
            BrowserNode(),
            config={
                "steps": [{"action": "click", "selector": "#nope"}],
                "timeout_seconds": 2,
            },
            inputs={"url": f"{site.base}/form"},
        )
    assert step.value.code in ("BROWSER_STEP_FAILED", "BROWSER_TIMEOUT")


async def test_step_without_its_parameter_is_a_configuration_error(
    run_node: RunNode, site: Site
) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(
            BrowserNode(),
            config={"steps": [{"action": "fill", "selector": "#name"}]},
            inputs={"url": f"{site.base}/form"},
        )
    assert exc.value.code == "BROWSER_STEP_INVALID"


async def test_hanging_page_times_out(run_node: RunNode, site: Site) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(
            BrowserNode(),
            config={"timeout_seconds": 1},
            inputs={"url": f"{site.base}/hang"},
        )
    assert (exc.value.code, exc.value.kind) == (
        "BROWSER_TIMEOUT",
        NodeErrorKind.TIMEOUT,
    )


async def test_cancellation_closes_the_browser(site: Site) -> None:
    browser = BrowserNode()
    task = asyncio.create_task(
        browser.execute(
            make_context(
                browser, inputs={"url": f"{site.base}/hang"}, services=services()
            )
        )
    )
    async with asyncio.timeout(GUARD):
        await asyncio.to_thread(site.hang_entered.wait, GUARD)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_agents_supply_only_the_url_and_values() -> None:
    params: dict[str, Any] = tool_parameters(BrowserNode.definition)
    assert set(params["properties"]) == {"url", "values"}
    assert params["required"] == ["url"]
