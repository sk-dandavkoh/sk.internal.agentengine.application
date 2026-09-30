"""Test support shared across test packages (importable, unlike conftest).

- ``RunNode`` / ``make_context``: drive a node through its contract.
- ``serve_http``: a loopback HTTP server for network nodes — never the
  public internet (AGENTS §30/§124).
"""

import json
import logging
import threading
import time
from collections.abc import Callable, Coroutine, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from testcontainers.core.container import DockerContainer
from testcontainers.core.docker_client import DockerClient

from akasha.domain.node import (
    CredentialResolver,
    Node,
    NodeExecutionContext,
    NodeResult,
)
from akasha.domain.primitives import new_id
from akasha.observability.logging import JsonFormatter

RunNode = Callable[..., Coroutine[Any, Any, NodeResult]]


class _JsonLines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.lines: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


@contextmanager
def capture_json_logs() -> Iterator[list[dict[str, Any]]]:
    """Every log line, as the production JSON formatter renders it (context
    and redaction included), parsed back into dicts."""
    handler = _JsonLines()
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler.lines
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


def make_context(
    node: Node,
    *,
    config: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
    execution_input: Mapping[str, Any] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    credentials: CredentialResolver | None = None,
) -> NodeExecutionContext:
    return NodeExecutionContext(
        execution_id=new_id("exec"),
        node_execution_id=new_id("nexec"),
        node_instance_id=new_id("node"),
        project_id=new_id("proj"),
        project_version="1",
        config=dict(config or {}),
        inputs=dict(inputs or {}),
        logger=logging.getLogger(f"akasha.nodes.{node.definition.node_id}"),
        is_cancelled=is_cancelled or (lambda: False),
        execution_input=dict(execution_input or {}),
        credentials=credentials,
    )


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


CHAT_COMPLETION: dict[str, object] = {
    "id": "chatcmpl-123",
    "object": "chat.completion",
    "created": 1_700_000_000,
    "model": "gpt-test-2026",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
}


@dataclass
class ServerState:
    requests: list[Recorded] = field(default_factory=list)
    release: threading.Event = field(default_factory=threading.Event)
    hang_entered: threading.Event = field(default_factory=threading.Event)
    # /search answers with this (SearXNG ``format=json`` shape by default).
    search_status: int = 200
    search_body: bytes = b'{"results": []}'
    search_hang: bool = False
    # /v1/chat/completions (OpenAI-compatible) answers with this.
    llm_status: int = 200
    llm_body: object = field(default_factory=lambda: dict(CHAT_COMPLETION))
    llm_hang: bool = False


class _Handler(BaseHTTPRequestHandler):
    """HTTP/1.0 (close-delimited) on purpose: lets /stream-big send a body
    without Content-Length."""

    state: ServerState  # set per server via a subclass

    def log_message(self, format: str, *args: object) -> None:
        """Silence per-request stderr lines."""

    def _send(
        self, status: int, body: bytes, content_type: str, **headers: str
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, doc: object) -> None:
        self._send(status, json.dumps(doc).encode(), "application/json")

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        self.state.requests.append(Recorded(self.command, self.path, headers, body))
        url = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        match url.path.strip("/").split("/"):
            case ["echo"]:
                self._json(
                    200,
                    {
                        "method": self.command,
                        "query": query,
                        "headers": headers,
                        "body": body.decode(),
                    },
                )
            case ["status", code]:
                self._send(int(code), f"status {code}".encode(), "text/plain")
            case ["redirect"]:
                self._send(
                    int(query.get("code", "302")),
                    b"",
                    "text/plain",
                    Location=query["to"],
                )
            case ["loop"]:
                self._send(302, b"", "text/plain", Location="/loop")
            case ["big"]:
                self._send(200, b"x" * 2048, "text/plain")
            case ["stream-big"]:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"x" * 4096)
            case ["malformed"]:
                self._send(200, b"{nope", "application/json")
            case ["text"]:
                self._send(
                    200, "café".encode("latin-1"), "text/plain; charset=iso-8859-1"
                )
            case ["hang"]:
                self.state.hang_entered.set()
                self.state.release.wait(30)
                self._send(200, b"late", "text/plain")
            case ["search"]:
                if self.state.search_hang:
                    self.state.release.wait(30)
                self._send(
                    self.state.search_status, self.state.search_body, "application/json"
                )
            case ["v1", "chat", "completions"]:
                if self.state.llm_hang:
                    self.state.hang_entered.set()
                    self.state.release.wait(30)
                self._json(self.state.llm_status, self.state.llm_body)
            case _:
                self._send(404, b"not found", "text/plain")

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _handle


@dataclass
class LocalHttpServer:
    base_url: str
    host: str
    port: int
    state: ServerState


@contextmanager
def serve_http() -> Iterator[LocalHttpServer]:
    """A fresh loopback HTTP server (isolated request log), always shut
    down — also when the test fails."""
    state = ServerState()
    handler = type("Handler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield LocalHttpServer(f"http://{host!s}:{port}", str(host), int(port), state)
    finally:
        state.release.set()  # unblock /hang handlers before shutdown
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def docker_available() -> bool:
    """A missing daemon must skip, not hang (backend/AGENTS.md §117: keep the
    real integration test, report the infrastructure limitation).

    Asks the daemon itself through the same client Testcontainers uses, so
    unix sockets, Windows named pipes and DOCKER_HOST all behave alike.
    """
    try:
        DockerClient(timeout=2).client.ping()
    except Exception:
        # Any failure to reach the daemon (missing client config, refused
        # connection, timeout) means exactly one thing here: skip.
        return False
    return True


# -- Mailpit: an ephemeral SMTP server with an inspection API (AGENTS §31) ----

MAILPIT_IMAGE = "axllent/mailpit:v1.21.8"
SMTP_LOGIN = ("akasha", "correct-horse-battery")  # test-only, container-local


@dataclass
class Mailpit:
    smtp_host: str
    smtp_port: int
    api_url: str

    def messages(self) -> list[dict[str, Any]]:
        """Summaries, newest first."""
        doc = httpx.get(f"{self.api_url}/api/v1/messages", timeout=5).json()
        messages: list[dict[str, Any]] = doc["messages"]
        return messages

    def message(self, message_id: str) -> dict[str, Any]:
        doc: dict[str, Any] = httpx.get(
            f"{self.api_url}/api/v1/message/{message_id}", timeout=5
        ).json()
        return doc

    def clear(self) -> None:
        httpx.delete(f"{self.api_url}/api/v1/messages", timeout=5).raise_for_status()


@contextmanager
def run_mailpit(*, auth: bool = True) -> Iterator[Mailpit]:
    """Mailpit, by default requiring SMTP AUTH with ``SMTP_LOGIN``
    (plaintext allowed: no TLS in tests); ``auth=False`` accepts anyone."""
    container = DockerContainer(MAILPIT_IMAGE).with_exposed_ports(1025, 8025)
    if auth:
        user, password = SMTP_LOGIN
        container = container.with_env("MP_SMTP_AUTH", f"{user}:{password}")
        container = container.with_env("MP_SMTP_AUTH_ALLOW_INSECURE", "1")
    with container:
        host = container.get_container_host_ip()
        api_url = f"http://{host}:{container.get_exposed_port(8025)}"
        deadline = time.monotonic() + 60
        while True:  # readiness: bounded polling of Mailpit's own probe
            try:
                if httpx.get(f"{api_url}/readyz", timeout=2).status_code == 200:
                    break
            except httpx.TransportError:
                pass  # not listening yet
            if time.monotonic() > deadline:
                raise TimeoutError("Mailpit did not become ready")
            time.sleep(0.2)
        yield Mailpit(host, int(container.get_exposed_port(1025)), api_url)
