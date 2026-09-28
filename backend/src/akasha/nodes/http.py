"""Web built-ins: core.http and core.web_search.

Both go through ``fetch``: one bounded HTTP exchange with explicit timeouts,
a response-size cap, manual redirect handling (so every hop passes the
destination guard) and a stable error classification (backend AGENTS
§35/§36/§73/§74). No environment proxies (``trust_env=False``): a proxy
would turn the destination check into a check of the proxy.
"""

import asyncio
import functools
import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from akasha.domain.node import (
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
from akasha.nodes.core import VERSION, closed_schema
from akasha.security.network import NetworkPolicy, check_destination

DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120
MAX_REDIRECTS = 5
METHODS = ("DELETE", "GET", "HEAD", "PATCH", "POST", "PUT")
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class FetchedResponse:
    status: int
    headers: dict[str, str]
    content: bytes
    content_type: str
    encoding: str | None


def _error(message: str, kind: NodeErrorKind, code: str, **details: str) -> NodeError:
    return NodeError(message, kind=kind, code=code, details=details or None)


async def _check_url(url: httpx.URL, policy: NetworkPolicy) -> None:
    if url.scheme not in _DEFAULT_PORTS:
        raise _error(
            "only http and https URLs are allowed",
            NodeErrorKind.CONFIGURATION,
            "HTTP_INVALID_URL",
        )
    if url.userinfo:
        # Credentials in URLs leak into logs and redirects; use headers.
        raise _error(
            "URLs must not contain credentials",
            NodeErrorKind.CONFIGURATION,
            "HTTP_INVALID_URL",
        )
    await check_destination(url.host, url.port or _DEFAULT_PORTS[url.scheme], policy)


async def _read_limited(response: httpx.Response, limit: int) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:  # a lying or absent Content-Length
            raise _too_large(limit)
        chunks.append(chunk)
    return b"".join(chunks)


def _too_large(limit: int) -> NodeError:
    return _error(
        f"response exceeds the limit of {limit} bytes",
        NodeErrorKind.PERMANENT,
        "HTTP_RESPONSE_TOO_LARGE",
        limit=str(limit),
    )


async def fetch(
    method: str,
    url: str,
    *,
    policy: NetworkPolicy,
    time_limit: float,
    max_bytes: int,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    params: dict[str, str] | None = None,
    follow_redirects: bool = False,
) -> FetchedResponse:
    """One HTTP exchange, bounded in time (whole exchange, all hops) and
    size. Raises only ``NodeError`` (or ``CancelledError``)."""
    try:
        async with asyncio.timeout(time_limit):
            return await _exchange(
                method,
                url,
                policy=policy,
                time_limit=time_limit,
                max_bytes=max_bytes,
                headers=headers,
                content=content,
                params=params,
                follow_redirects=follow_redirects,
            )
    except (TimeoutError, httpx.TimeoutException):
        raise _error(
            f"no complete response within {time_limit}s",
            NodeErrorKind.TIMEOUT,
            "HTTP_TIMEOUT",
        ) from None
    except httpx.InvalidURL:
        raise _error(
            "the URL is not valid", NodeErrorKind.CONFIGURATION, "HTTP_INVALID_URL"
        ) from None
    except httpx.TransportError as exc:
        # Connection refused/reset, TLS or protocol failures. Only the type
        # is reported: messages can echo request details.
        raise _error(
            f"HTTP transport failure ({type(exc).__name__})",
            NodeErrorKind.TRANSIENT,
            "HTTP_CONNECTION_ERROR",
        ) from None
    except httpx.HTTPError as exc:  # e.g. an undecodable content encoding
        raise _error(
            f"invalid HTTP response ({type(exc).__name__})",
            NodeErrorKind.PERMANENT,
            "HTTP_MALFORMED_RESPONSE",
        ) from None


async def _exchange(
    method: str,
    url: str,
    *,
    policy: NetworkPolicy,
    time_limit: float,
    max_bytes: int,
    headers: dict[str, str] | None,
    content: bytes | None,
    params: dict[str, str] | None,
    follow_redirects: bool,
) -> FetchedResponse:
    async with httpx.AsyncClient(
        timeout=time_limit, follow_redirects=False, trust_env=False, verify=_tls()
    ) as client:
        request = client.build_request(
            method, url, headers=headers, content=content, params=params
        )
        for _ in range(MAX_REDIRECTS + 1):
            await _check_url(request.url, policy)
            response = await client.send(request, stream=True)
            try:
                next_request = response.next_request if follow_redirects else None
                if next_request is None:
                    body = await _read_limited(response, max_bytes)
                    return FetchedResponse(
                        status=response.status_code,
                        headers=dict(response.headers.items()),
                        content=body,
                        content_type=response.headers.get("content-type", ""),
                        encoding=response.charset_encoding,
                    )
            finally:
                await response.aclose()
            request = next_request
    raise _error(
        f"more than {MAX_REDIRECTS} redirects",
        NodeErrorKind.PERMANENT,
        "HTTP_TOO_MANY_REDIRECTS",
    )


@functools.cache
def _tls() -> ssl.SSLContext:
    """One verifying TLS context (system trust store) for all requests:
    building one per client costs ~0.2s of certificate loading."""
    return ssl.create_default_context()


def _is_json(content_type: str) -> bool:
    mime = content_type.split(";", 1)[0].strip().lower()
    return mime == "application/json" or mime.endswith("+json")


def decode_body(response: FetchedResponse) -> Any:
    """Parsed JSON for JSON media types, else text (lossy for binary)."""
    if _is_json(response.content_type):
        try:
            return json.loads(response.content)
        except ValueError:
            raise _error(
                "the response claims JSON but is not valid JSON",
                NodeErrorKind.PERMANENT,
                "HTTP_MALFORMED_RESPONSE",
            ) from None
    # ponytail: non-JSON bodies become text with replacement characters;
    # binary downloads need artifact storage (Phase 24).
    return response.content.decode(response.encoding or "utf-8", errors="replace")


def status_error(status: int, code: str) -> NodeError:
    """429 and 5xx may succeed later; any other 4xx will not."""
    transient = status == 429 or status >= 500
    return _error(
        f"HTTP status {status}",
        NodeErrorKind.TRANSIENT if transient else NodeErrorKind.PERMANENT,
        code,
        status=str(status),
    )


class HttpNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.http",
            name="HTTP Request",
            description="Sends an HTTP request and returns the response.",
            version=VERSION,
            category="web",
        ),
        inputs=(NodeInput("body", PortType.JSON, required=False, nullable=True),),
        outputs=(
            NodeOutput("status", PortType.NUMBER),
            NodeOutput("headers", PortType.OBJECT),
            NodeOutput("body", PortType.JSON, nullable=True),
        ),
        config_schema=closed_schema(
            {
                "method": {"type": "string", "enum": list(METHODS)},
                "url": {"type": "string", "pattern": "^https?://"},
                "headers": {"type": "object"},
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                },
                "follow_redirects": {"type": "boolean"},
                "fail_on_error_status": {"type": "boolean"},
            },
            "url",
        ),
        capabilities=NodeCapabilities(
            supports_cancellation=True, has_side_effects=True
        ),
    )

    def __init__(
        self,
        policy: NetworkPolicy | None = None,
        *,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._policy = policy or NetworkPolicy()
        self._max_bytes = max_response_bytes

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        cfg = context.config
        headers = cfg.get("headers", {})
        if not all(isinstance(v, str) for v in headers.values()):
            raise _error(
                "header values must be strings",
                NodeErrorKind.CONFIGURATION,
                "HTTP_INVALID_HEADERS",
            )
        content = None
        if "body" in context.inputs:
            content = json.dumps(context.inputs["body"]).encode()
            if not any(name.lower() == "content-type" for name in headers):
                headers = {**headers, "content-type": "application/json"}
        response = await fetch(
            cfg.get("method", "GET"),
            cfg["url"],
            policy=self._policy,
            time_limit=cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            max_bytes=self._max_bytes,
            headers=headers,
            content=content,
            follow_redirects=cfg.get("follow_redirects", False),
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


class WebSearchNode(Node):
    """Searches through the operator-configured SearXNG instance
    (``/search?format=json``). The endpoint is deployment configuration,
    not user input, so it may live on a private network.

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
        inputs=(NodeInput("query", PortType.STRING),),
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
            ),
        ),
        config_schema=closed_schema(
            {"max_results": {"type": "integer", "minimum": 1, "maximum": 20}}
        ),
        capabilities=NodeCapabilities(supports_cancellation=True),
    )

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        timeout: float = 15.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._endpoint = endpoint
        self._timeout = timeout
        self._max_bytes = max_response_bytes

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        if not self._endpoint:
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
            self._endpoint.rstrip("/") + "/search",
            params={"q": query, "format": "json"},
            policy=NetworkPolicy(allow_private_networks=True),  # operator-trusted
            time_limit=self._timeout,
            max_bytes=self._max_bytes,
        )
        if response.status >= 400:
            raise status_error(response.status, "SEARCH_PROVIDER_ERROR")
        limit = context.config.get("max_results", 5)
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
