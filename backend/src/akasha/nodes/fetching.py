"""``fetch``: one bounded, guarded HTTP exchange, shared by core.http,
core.web_fetch, core.web_search and core.browser.

Explicit timeouts, a response-size cap, manual redirect handling and a
stable error classification (backend AGENTS §35/§36/§73/§74). Every
connection the client opens, on every redirect hop, passes the destination
guard and connects to the address the guard vetted (``_GuardedBackend``),
never to a second DNS answer. No environment proxies (``trust_env=False``):
a proxy would turn the destination check into a check of the proxy.
"""

import asyncio
import functools
import json
import ssl
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpcore
import httpx

from akasha.sdk import (
    NetworkPolicy,
    NodeError,
    NodeErrorKind,
    check_destination,
)

MAX_REDIRECTS = 5
# Text extracted from a page (core.web_fetch, core.browser).
DEFAULT_MAX_CHARS = 20_000
MAX_CHARS = 200_000
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class FetchedResponse:
    status: int
    headers: dict[str, str]  # repeated headers joined by ", "
    content: bytes
    content_type: str
    encoding: str | None
    # One per Set-Cookie header: joined like other headers they would be
    # unparseable (Expires dates contain commas).
    set_cookies: tuple[str, ...] = ()


def _error(message: str, kind: NodeErrorKind, code: str, **details: str) -> NodeError:
    return NodeError(message, kind=kind, code=code, details=details or None)


class _GuardedBackend(httpcore.AsyncNetworkBackend):
    """Resolves each destination once, through the guard, and connects to
    the vetted addresses in resolver order. TLS still verifies the host
    name: httpcore takes the SNI/certificate name from the URL, not from the
    socket address."""

    def __init__(self, policy: NetworkPolicy) -> None:
        self._policy = policy
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore passes it by name
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        *fallbacks, last = await check_destination(host, port, self._policy)
        for address in fallbacks:
            try:
                return await self._inner.connect_tcp(
                    address, port, timeout, local_address, socket_options
                )
            except httpcore.ConnectError:
                continue  # the next address, like socket.create_connection
        return await self._inner.connect_tcp(
            last, port, timeout, local_address, socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _transport(policy: NetworkPolicy) -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(verify=_tls(), trust_env=False)
    # ponytail: httpx has no public hook for the network backend, so the
    # pool is replaced; tests/nodes/test_http_node.py proves connections
    # still go through the guard after an httpx/httpcore upgrade.
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=_tls(), network_backend=_GuardedBackend(policy)
    )
    return transport


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    return url.scheme, url.host, url.port or _DEFAULT_PORTS.get(url.scheme)


def _check_url(url: httpx.URL) -> None:
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
    params: dict[str, str | list[str]] | None = None,
    follow_redirects: bool = False,
    secret_headers: frozenset[str] = frozenset(),
) -> FetchedResponse:
    """One HTTP exchange, bounded in time (whole exchange, all hops) and
    size. Raises only ``NodeError`` (or ``CancelledError``).

    ``secret_headers`` (names, any case) are dropped when a redirect leaves
    the original origin: a credential is meant for the configured host,
    not for wherever that host redirects to."""
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
                secret_headers=secret_headers,
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
    params: dict[str, str | list[str]] | None,
    follow_redirects: bool,
    secret_headers: frozenset[str],
) -> FetchedResponse:
    async with httpx.AsyncClient(
        timeout=time_limit,
        follow_redirects=False,
        trust_env=False,
        transport=_transport(policy),
    ) as client:
        request = client.build_request(
            method, url, headers=headers, content=content, params=params
        )
        origin = _origin(request.url)
        for _ in range(MAX_REDIRECTS + 1):
            _check_url(request.url)
            if _origin(request.url) != origin:
                for name in secret_headers:
                    request.headers.pop(name, None)
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
                        set_cookies=tuple(response.headers.get_list("set-cookie")),
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
