"""Web built-ins: core.http, core.web_fetch and core.web_search, all
through ``fetch`` (``akasha.nodes.fetching``) and the engine's network
policy (``context.services.network``)."""

import json
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from akasha.nodes.core import MISSING, PATH, VERSION, closed_schema, lookup
from akasha.nodes.fetching import (
    DEFAULT_MAX_CHARS,
    MAX_CHARS,
    FetchedResponse,
    decode_body,
    fetch,
    status_error,
)
from akasha.nodes.settings import core_settings
from akasha.sdk import (
    CREDENTIAL_REFERENCE_FORMAT,
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

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120
METHODS = ("DELETE", "GET", "HEAD", "PATCH", "POST", "PUT")
DEFAULT_METHOD = "GET"
DEFAULT_MAX_RESULTS = 5
DEFAULT_SEARCH_TIMEOUT_SECONDS = 15.0
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _error(message: str, kind: NodeErrorKind, code: str, **details: str) -> NodeError:
    return NodeError(message, kind=kind, code=code, details=details or None)


class HttpNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.http",
            name="HTTP Request",
            description="Sends an HTTP request and returns the response.",
            # 1.1.0: the optional ``query`` and ``variables`` inputs.
            version="1.1.0",
            category="web",
        ),
        inputs=(
            NodeInput(
                "body",
                PortType.JSON,
                required=False,
                nullable=True,
                description="Data to send with the request, e.g. for POST. Not"
                " connected: no body.",
            ),
            NodeInput(
                "query",
                PortType.OBJECT,
                required=False,
                default={},
                description="Query parameters added to the URL (lists repeat"
                " the parameter).",
            ),
            NodeInput(
                "variables",
                PortType.OBJECT,
                required=False,
                default={},
                description="Values for {{ name }} placeholders in the URL"
                " path; inserted URL-encoded.",
            ),
        ),
        outputs=(
            NodeOutput(
                "status",
                PortType.NUMBER,
                description="The response status code, e.g. 200 for success.",
            ),
            NodeOutput("headers", PortType.OBJECT, description="The response headers."),
            NodeOutput(
                "body",
                PortType.JSON,
                nullable=True,
                description="The response content: parsed data for JSON"
                " responses, text otherwise.",
            ),
        ),
        config_schema=closed_schema(
            {
                "method": {
                    "type": "string",
                    "enum": list(METHODS),
                    "default": DEFAULT_METHOD,
                    "description": "GET reads data; POST, PUT, PATCH and DELETE"
                    " send or change it.",
                },
                "url": {
                    "type": "string",
                    "pattern": "^https?://",
                    "description": "The address to call, starting with https://."
                    " May hold {{ name }} placeholders after the host, filled"
                    " from the variables input.",
                },
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Header name to value. Secret headers"
                    " belong in header credentials.",
                },
                # Header name -> credential reference, e.g. Authorization ->
                # "credential:crm-api" holding "Bearer ...". Secret headers
                # never live in ``headers`` (workflow config is secret-free).
                "header_credentials": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "string",
                        "format": CREDENTIAL_REFERENCE_FORMAT,
                    },
                    "description": "Header name to the credential holding its"
                    " value, e.g. Authorization.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                    "default": DEFAULT_TIMEOUT_SECONDS,
                    "description": "How long to wait, in seconds, before giving up.",
                },
                "follow_redirects": {
                    "type": "boolean",
                    "default": False,
                    "description": "Follow the server when it sends the request"
                    " on to another address.",
                },
                "fail_on_error_status": {
                    "type": "boolean",
                    "default": True,
                    "description": "Fail on HTTP 4xx/5xx instead of returning"
                    " the response.",
                },
            },
            "url",
        ),
        capabilities=NodeCapabilities(
            supports_cancellation=True, has_side_effects=True
        ),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        cfg = context.config
        headers = cfg.get("headers", {})
        if not all(isinstance(v, str) for v in headers.values()):
            raise _error(
                "header values must be strings",
                NodeErrorKind.CONFIGURATION,
                "HTTP_INVALID_HEADERS",
            )
        secret_headers = cfg.get("header_credentials", {})
        if secret_headers:
            if context.credentials is None:
                raise _error(
                    "credential references cannot be resolved in this engine",
                    NodeErrorKind.CONFIGURATION,
                    "HTTP_CREDENTIALS_UNAVAILABLE",
                )
            headers = dict(headers)
            for name, reference in secret_headers.items():
                headers[name] = await context.credentials.resolve(reference)
        content = None
        if "body" in context.inputs:
            content = json.dumps(context.inputs["body"]).encode()
            if not any(name.lower() == "content-type" for name in headers):
                headers = {**headers, "content-type": "application/json"}
        response = await fetch(
            cfg.get("method", DEFAULT_METHOD),
            _url(cfg["url"], context.inputs),
            policy=context.services.network,
            time_limit=cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            max_bytes=core_settings(context).http_max_response_bytes,
            headers=headers,
            content=content,
            follow_redirects=cfg.get("follow_redirects", False),
            secret_headers=frozenset(secret_headers),
        )
        if response.status >= 400 and cfg.get("fail_on_error_status", True):
            raise status_error(response.status, "HTTP_STATUS_ERROR")
        return NodeResult(
            {
                "status": response.status,
                "headers": response.headers,
                "body": decode_body(response),
            }
        )


_URL_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}")


def _fill_url(template: str, variables: dict[str, Any]) -> str:
    """Fill ``{{ path }}`` placeholders with URL-encoded values. Only after
    the host: the destination stays the author's (a value cannot add ``/``,
    ``?``, ``#`` or ``@`` either), so data — or a model calling this node
    as a tool — picks a resource, never a server."""
    if "{{" not in template:
        return template
    _, _, rest = template.partition("://")
    if "{{" in rest.split("/", 1)[0]:
        raise _error(
            "URL placeholders are only allowed after the host",
            NodeErrorKind.CONFIGURATION,
            "HTTP_INVALID_URL",
        )

    def substitute(match: re.Match[str]) -> str:
        path = match.group(1).strip()
        value = lookup(variables, path) if re.fullmatch(PATH, path) else MISSING
        if value is MISSING or isinstance(value, dict | list):
            raise _error(
                f"URL variable is missing or not a scalar: {path}",
                NodeErrorKind.VALIDATION,
                "HTTP_URL_VARIABLE_MISSING",
            )
        text = json.dumps(value) if not isinstance(value, str) else value
        return quote(text, safe="")

    return _URL_PLACEHOLDER.sub(substitute, template)


def _url(template: str, inputs: Mapping[str, Any]) -> str:
    """The configured URL with ``variables`` filled in and ``query`` added
    to (never replacing) its own query string."""
    url = _fill_url(template, inputs.get("variables", {}))

    def text(value: Any) -> str:
        return value if isinstance(value, str) else json.dumps(value)

    query = {
        name: [text(v) for v in value] if isinstance(value, list) else text(value)
        for name, value in inputs.get("query", {}).items()
        if value is not None
    }
    if not query:
        return url
    try:
        return str(httpx.URL(url).copy_merge_params(query))
    except httpx.InvalidURL:
        raise _error(
            "the URL is not valid", NodeErrorKind.CONFIGURATION, "HTTP_INVALID_URL"
        ) from None


class _TextExtractor(HTMLParser):
    """Readable text of an HTML page: no scripts, styles or markup. Text
    only, no rendering: nothing in the page is executed or followed."""

    _SKIP = frozenset({"script", "style", "noscript", "svg", "template", "head"})
    _BLOCK = frozenset(
        {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skipping = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self._SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skipping:
            self.parts.append(data)


def html_to_text(markup: str) -> tuple[str, str]:
    """``(title, text)`` with whitespace collapsed, one block per line."""
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    lines = (
        re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).split("\n")
    )
    return parser.title.strip(), "\n".join(line for line in lines if line)


class WebFetchNode(Node):
    """Fetches one page by URL and returns its readable text, e.g. a search
    result an agent wants to read. The URL is user (or model) input, so the
    full destination guard applies, on every redirect hop.

    The text is untrusted third-party data (backend AGENTS §36).
    """

    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.web_fetch",
            name="Fetch Web Page",
            description="Fetches a web page and returns its readable text.",
            version=VERSION,
            category="web",
        ),
        inputs=(
            NodeInput("url", PortType.STRING, description="http(s) URL of the page."),
        ),
        outputs=(
            NodeOutput("url", PortType.STRING, description="The requested URL."),
            NodeOutput(
                "status",
                PortType.NUMBER,
                description="The response status code, e.g. 200 for success.",
            ),
            NodeOutput("title", PortType.STRING, description="The page title."),
            NodeOutput(
                "text", PortType.STRING, description="The page's readable text."
            ),
            NodeOutput(
                "truncated",
                PortType.BOOLEAN,
                description="True when the text was cut at max_chars.",
            ),
        ),
        config_schema=closed_schema(
            {
                "max_chars": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": MAX_CHARS,
                    "default": DEFAULT_MAX_CHARS,
                    "description": "Longest text to return; longer pages are cut.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                    "default": DEFAULT_TIMEOUT_SECONDS,
                    "description": "How long to wait, in seconds, before giving up.",
                },
            }
        ),
        capabilities=NodeCapabilities(supports_cancellation=True),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        cfg = context.config
        url = context.inputs["url"].strip()
        if urlsplit(url).scheme not in _DEFAULT_PORTS:
            raise _error(
                "only http and https URLs are allowed",
                NodeErrorKind.VALIDATION,
                "HTTP_INVALID_URL",
            )
        response = await fetch(
            "GET",
            url,
            policy=context.services.network,
            time_limit=cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            max_bytes=core_settings(context).http_max_response_bytes,
            headers={"accept": "text/html,text/plain;q=0.9,*/*;q=0.5"},
            follow_redirects=True,
        )
        if response.status >= 400:
            raise status_error(response.status, "HTTP_STATUS_ERROR")
        text = response.content.decode(response.encoding or "utf-8", errors="replace")
        title = ""
        if response.content_type.split(";", 1)[0].strip().lower() in (
            "text/html",
            "application/xhtml+xml",
        ):
            title, text = html_to_text(text)
        limit = cfg.get("max_chars", DEFAULT_MAX_CHARS)
        return NodeResult(
            {
                "url": url,
                "status": response.status,
                "title": title,
                "text": text[:limit],
                "truncated": len(text) > limit,
            }
        )


class WebSearchNode(Node):
    """Searches through the operator-configured SearXNG instance
    (``/search?format=json``, setting ``web_search_url``). The endpoint is
    deployment configuration, not user input, so it may live on a private
    network.

    Results are untrusted third-party data: titles and snippets must reach
    LLM prompts as quoted data, never as instructions (backend AGENTS §36).
    """

    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.web_search",
            name="Web Search",
            description="Searches the web and returns normalized results.",
            version=VERSION,
            category="web",
        ),
        inputs=(
            NodeInput("query", PortType.STRING, description="What to search for."),
        ),
        outputs=(
            NodeOutput(
                "results",
                PortType.LIST,
                schema={
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                            "snippet": {"type": "string"},
                        },
                    },
                },
                description="The search hits, each with title, url and a short"
                " snippet.",
            ),
        ),
        config_schema=closed_schema(
            {
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": DEFAULT_MAX_RESULTS,
                    "description": "How many results to return at most.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                    "default": DEFAULT_SEARCH_TIMEOUT_SECONDS,
                    "description": "How long to wait, in seconds, before giving up.",
                },
            }
        ),
        capabilities=NodeCapabilities(supports_cancellation=True),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        endpoint = core_settings(context).web_search_url
        if not endpoint:
            raise _error(
                "no web search provider is configured for this engine",
                NodeErrorKind.CONFIGURATION,
                "SEARCH_NOT_CONFIGURED",
            )
        query = context.inputs["query"].strip()
        if not query:
            raise _error(
                "the search query is empty",
                NodeErrorKind.VALIDATION,
                "SEARCH_EMPTY_QUERY",
            )
        response = await fetch(
            "GET",
            endpoint.rstrip("/") + "/search",
            params={"q": query, "format": "json"},
            policy=NetworkPolicy(allow_private_networks=True),  # operator-trusted
            time_limit=context.config.get(
                "timeout_seconds", DEFAULT_SEARCH_TIMEOUT_SECONDS
            ),
            max_bytes=core_settings(context).http_max_response_bytes,
        )
        if response.status >= 400:
            raise status_error(response.status, "SEARCH_PROVIDER_ERROR")
        limit = context.config.get("max_results", DEFAULT_MAX_RESULTS)
        return NodeResult({"results": _normalize(response)[:limit]})


def _normalize(response: FetchedResponse) -> list[dict[str, str]]:
    try:
        doc = json.loads(response.content)
    except ValueError:
        doc = None
    items = doc.get("results") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise _error(
            "the search provider returned an unexpected response",
            NodeErrorKind.PERMANENT,
            "SEARCH_MALFORMED_RESPONSE",
        )
    results: list[dict[str, str]] = []
    for item in items:
        # Skip entries without a usable link instead of failing the search.
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or urlsplit(url).scheme not in _DEFAULT_PORTS:
            continue
        title, snippet = item.get("title"), item.get("content")
        results.append(
            {
                "title": title if isinstance(title, str) else "",
                "url": url,
                "snippet": snippet if isinstance(snippet, str) else "",
            }
        )
    return results
