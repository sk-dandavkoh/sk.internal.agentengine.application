"""core.browser: open a page in headless Chromium, run fixed steps, read it.

One node for browser automation (core node portfolio): navigate to ``url``,
then the author's ``steps`` in order — click, fill, select, press, wait_for,
extract, screenshot — and return the page's text plus what ``extract``
steps collected. Scraping and form automation are compositions (browser →
transform → database), not node types. Unlike core.web_fetch it runs the
page's JavaScript, so it reads rendered pages and drives forms.

Isolation. Every attempt launches its own browser and context and closes
both when it ends (also on timeout and cancellation), so no cookie,
storage or session crosses attempts, executions or workflows. The browser
has no network of its own:

- every request the page makes is intercepted and performed by the engine
  through ``fetch`` — the core.http client: destination guard on every
  request and redirect hop (SSRF, private networks, DNS rebinding: the
  vetted address is the one connected to), response size cap, timeout;
  at most ``MAX_REQUESTS`` per attempt;
- anything not interceptable (WebSockets) goes to a dead proxy and fails;
- service workers and downloads are off; there is no upload step.

Selectors and step structure are config (the author's); data enters only as
the ``url`` input and the ``values`` that ``fill``/``select`` templates
reference — the same split as core.http, so a model calling this node as a
tool chooses what to type and which page to open, never what to click.

Playwright is an optional dependency (``uv sync --extra browser`` plus
``playwright install chromium``); without it the node fails with
BROWSER_NOT_CONFIGURED.

ponytail: one browser launch per attempt (~0.5 s) buys trivial ownership
and isolation. Upgrade path: a long-lived browser with a fresh context per
attempt, once launch time matters. Multi-node sessions (log in once, act in
later nodes) need session ownership across workers and WAITING; not core.
"""

import asyncio
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from akasha.nodes.core import (
    TEMPLATE,
    VERSION,
    closed_schema,
    render,
    require_workspace,
)
from akasha.nodes.fetching import (
    DEFAULT_MAX_CHARS,
    MAX_CHARS,
    fetch,
)
from akasha.nodes.settings import core_settings
from akasha.sdk import (
    NetworkPolicy,
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

if TYPE_CHECKING:
    from playwright.async_api import Page, Route

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120
MAX_STEPS = 50
MAX_REQUESTS = 300  # per attempt: a page cannot make the engine fetch forever
MAX_EXTRACTED_ITEMS = 1000
ACTIONS = ("click", "extract", "fill", "press", "screenshot", "select", "wait_for")
# Headers Chromium sets that httpx must compute itself (or must not get).
_HOP_HEADERS = frozenset(
    {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}
)
# httpx already decoded the body; these would make Chromium decode again.
_DECODED_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding"}
)


def _error(message: str, kind: NodeErrorKind, code: str) -> NodeError:
    return NodeError(message, kind=kind, code=code)


def _config(message: str) -> NodeError:
    return _error(message, NodeErrorKind.CONFIGURATION, "BROWSER_STEP_INVALID")


class BrowserNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.browser",
            name="Browser",
            description="Opens a page in a headless browser, runs steps, reads it.",
            version=VERSION,
            category="web",
        ),
        inputs=(
            NodeInput("url", PortType.STRING, description="http(s) URL to open."),
            NodeInput(
                "values",
                PortType.OBJECT,
                required=False,
                default={},
                description="Values the fill/select templates reference.",
            ),
        ),
        outputs=(
            NodeOutput("url", PortType.STRING, description="The final page URL."),
            NodeOutput("title", PortType.STRING, description="The page title."),
            NodeOutput("text", PortType.STRING, description="The page's visible text."),
            NodeOutput(
                "extracted",
                PortType.OBJECT,
                description="What the extract steps collected, by name.",
            ),
            NodeOutput(
                "truncated",
                PortType.BOOLEAN,
                description="True when the text was cut at max_chars.",
            ),
        ),
        config_schema=closed_schema(
            {
                "steps": {
                    "type": "array",
                    "maxItems": MAX_STEPS,
                    "description": "Run in order after the page loaded.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": list(ACTIONS)},
                            "selector": {
                                "type": "string",
                                "minLength": 1,
                                "description": "CSS or Playwright selector.",
                            },
                            "value": {
                                **TEMPLATE,
                                "description": "fill/select: text, may"
                                " reference {{ values.name }}.",
                            },
                            "key": {
                                "type": "string",
                                "minLength": 1,
                                "description": "press: e.g. Enter.",
                            },
                            "name": {
                                "type": "string",
                                "pattern": "^[A-Za-z_][A-Za-z0-9_]*$",
                                "description": "extract: result name.",
                            },
                            "attribute": {
                                "type": "string",
                                "minLength": 1,
                                "description": "extract: read this attribute"
                                " instead of the text.",
                            },
                            "all": {
                                "type": "boolean",
                                "description": "extract: every match as a list.",
                            },
                            "path": {
                                "type": "string",
                                "minLength": 1,
                                "description": "screenshot: PNG path in the"
                                " workflow workspace.",
                            },
                        },
                        "required": ["action"],
                        "additionalProperties": False,
                    },
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": MAX_CHARS,
                    "default": DEFAULT_MAX_CHARS,
                    "description": "Longest text to return; longer pages are cut.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                    "default": DEFAULT_TIMEOUT_SECONDS,
                    "description": "For the whole attempt: load and every step.",
                },
            }
        ),
        # Clicks can submit forms: an interrupted run must not be repeated
        # blindly by recovery.
        capabilities=NodeCapabilities(
            supports_cancellation=True, has_side_effects=True
        ),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        url = context.inputs["url"].strip()
        if urlsplit(url).scheme not in ("http", "https"):
            raise _error(
                "only http and https URLs are allowed",
                NodeErrorKind.VALIDATION,
                "HTTP_INVALID_URL",
            )
        try:
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import async_playwright
        except ImportError:
            raise _error(
                "browser automation is not installed in this engine",
                NodeErrorKind.CONFIGURATION,
                "BROWSER_NOT_CONFIGURED",
            ) from None
        limit = context.config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        fetcher = _Fetcher(
            context.services.network,
            core_settings(context).http_max_response_bytes,
            limit,
        )
        try:
            async with asyncio.timeout(limit), async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    # Nothing may bypass the interception below: hosts do
                    # not resolve and unrouted traffic hits a dead proxy.
                    args=["--host-resolver-rules=MAP * ~NOTFOUND"],
                    proxy={"server": "http://127.0.0.1:1"},
                )
                try:
                    browser_context = await browser.new_context(
                        service_workers="block", accept_downloads=False
                    )
                    await browser_context.route("**/*", fetcher.handle)
                    browser_context.set_default_timeout(limit * 1000)
                    page = await browser_context.new_page()
                    page.on(
                        "dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss())
                    )
                    return await self._run(context, page, url)
                finally:
                    await browser.close()
        except TimeoutError:
            raise _error(
                f"the browser did not finish within {limit}s",
                NodeErrorKind.TIMEOUT,
                "BROWSER_TIMEOUT",
            ) from None
        except PlaywrightError as exc:
            if fetcher.navigation_error is not None:
                # The page itself was refused (blocked destination, too
                # large, unreachable): report why, not "navigation failed".
                raise fetcher.navigation_error from None
            # The first line names the failure (selector not found, page
            # crashed, navigation refused); the call log below can quote
            # page content, so it is dropped.
            first = str(exc).split("\n", 1)[0][:300]
            if "Executable doesn't exist" in first:
                raise _error(
                    "the browser is not installed (playwright install chromium)",
                    NodeErrorKind.CONFIGURATION,
                    "BROWSER_NOT_CONFIGURED",
                ) from None
            raise _error(
                f"the browser step failed: {first}",
                NodeErrorKind.PERMANENT,
                "BROWSER_STEP_FAILED",
            ) from None

    async def _run(
        self, context: NodeExecutionContext, page: "Page", url: str
    ) -> NodeResult:
        response = await page.goto(url)
        if response is not None and response.status >= 400:
            raise _error(
                f"HTTP status {response.status}",
                NodeErrorKind.TRANSIENT
                if response.status == 429 or response.status >= 500
                else NodeErrorKind.PERMANENT,
                "HTTP_STATUS_ERROR",
            )
        values = context.inputs.get("values", {})
        extracted: dict[str, Any] = {}
        for step in context.config.get("steps", []):
            if context.is_cancelled():
                raise _error(
                    "the browser was cancelled",
                    NodeErrorKind.CANCELLATION,
                    "BROWSER_CANCELLED",
                )
            await self._step(context, page, step, values, extracted)
        text = await page.inner_text("body")
        limit = context.config.get("max_chars", DEFAULT_MAX_CHARS)
        return NodeResult(
            {
                "url": page.url,
                "title": await page.title(),
                "text": text[:limit],
                "extracted": extracted,
                "truncated": len(text) > limit,
            }
        )

    async def _step(
        self,
        context: NodeExecutionContext,
        page: "Page",
        step: dict[str, Any],
        values: dict[str, Any],
        extracted: dict[str, Any],
    ) -> None:
        action = step["action"]

        def need(name: str) -> Any:
            if name not in step:
                raise _config(f"step {action!r} needs {name!r}")
            return step[name]

        match action:
            case "click":
                await page.click(need("selector"))
            case "fill":
                await page.fill(
                    need("selector"), render(need("value"), {"values": values})
                )
            case "select":
                await page.select_option(
                    need("selector"), render(need("value"), {"values": values})
                )
            case "press":
                await page.press(need("selector"), need("key"))
            case "wait_for":
                await page.wait_for_selector(need("selector"))
            case "extract":
                locator = page.locator(need("selector"))
                attribute = step.get("attribute")

                async def read(index: int) -> str | None:
                    item = locator.nth(index)
                    if attribute is None:
                        return await item.inner_text()
                    return await item.get_attribute(attribute)

                if step.get("all", False):
                    count = min(await locator.count(), MAX_EXTRACTED_ITEMS)
                    extracted[need("name")] = [await read(i) for i in range(count)]
                else:
                    await locator.first.wait_for()
                    extracted[need("name")] = await read(0)
            case "screenshot":
                await self._screenshot(context, page, need("path"))

    async def _screenshot(
        self, context: NodeExecutionContext, page: "Page", path: str
    ) -> None:
        target = require_workspace(context).resolve(context.workflow_id, path)
        # Viewport only (never full_page): its size bounds the image.
        image = await page.screenshot(type="png")
        if len(image) > (limit := core_settings(context).workspace_max_file_bytes):
            raise _error(
                f"the screenshot exceeds the limit of {limit} bytes",
                NodeErrorKind.PERMANENT,
                "FILE_TOO_LARGE",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, image)


class _Fetcher:
    """Performs the page's requests through the engine's guarded client."""

    def __init__(
        self, policy: NetworkPolicy, max_bytes: int, time_limit: float
    ) -> None:
        self._policy = policy
        self._max_bytes = max_bytes
        self._time_limit = time_limit
        self._requests = 0
        # Why the latest top-level navigation was refused, if it was.
        self.navigation_error: NodeError | None = None

    async def handle(self, route: "Route") -> None:
        request = route.request
        self._requests += 1
        if self._requests > MAX_REQUESTS or urlsplit(request.url).scheme not in (
            "http",
            "https",
        ):
            await route.abort("blockedbyclient")
            return
        try:
            response = await fetch(
                request.method,
                request.url,
                policy=self._policy,
                time_limit=self._time_limit,
                max_bytes=self._max_bytes,
                headers={
                    k: v
                    for k, v in request.headers.items()
                    if k.lower() not in _HOP_HEADERS
                },
                content=request.post_data_buffer,
                # Chromium does not route the next hop of a fulfilled
                # redirect (it would hit the dead proxy), so the engine
                # follows redirects itself — each hop through the guard.
                # ponytail: the page keeps its first URL, so relative links
                # after a cross-path redirect resolve against it.
                follow_redirects=True,
            )
        except NodeError as exc:
            # Blocked destination, oversize, timeout: the page sees a failed
            # request, like any network error.
            if request.is_navigation_request() and request.frame.parent_frame is None:
                self.navigation_error = exc
            await route.abort("blockedbyclient")
            return
        await route.fulfill(
            status=response.status,
            headers={
                k: v
                for k, v in response.headers.items()
                if k.lower() not in _DECODED_HEADERS
            },
            body=response.content,
        )
