"""Execution inspection/control and the node catalog through the real HTTP
stack and PostgreSQL (architecture §22, Phase 27).

App → router → ExecutionService / ExecutionRuntime → Unit of Work →
PostgreSQL. Executions really run through the runtime, so the event history
the API returns is what the runtime persisted.
"""

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import uvicorn
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.typing import Origin

from akasha import __version__
from akasha.api.app import create_app
from akasha.application.executions import ExecutionService
from akasha.application.projects import ProjectService
from akasha.application.triggers import TriggerRegistry, TriggerService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.graph_validation import port_compatibility
from akasha.domain.node import Node, NodeExecutionContext, NodeResult
from akasha.errors import InvalidStateTransitionError
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.nodes.builtins import create_builtin_registry
from akasha.runtime.runtime import ExecutionRuntime
from akasha.sdk import NodeError, NodeErrorKind
from tests.integration.fixtures import (
    PASSWORD,
    SINK,
    SOURCE,
    AuthKit,
    make_auth_kit,
)
from tests.integration.test_triggers_api import assert_error, publish_with

pytestmark = pytest.mark.usefixtures("clean_db")

UNKNOWN_EXECUTION = "exec_" + "0" * 32
UNKNOWN_PROJECT = "proj_" + "0" * 32
SECRET = "sk-live-0123456789abcdefghijklmnop"


class _Source(Node):
    definition = SOURCE

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        return NodeResult({"text": "hello"})


class _Sink(Node):
    """Fails when the execution input asks for it, with a secret in the
    message: the event history must never return it in plaintext."""

    definition = SINK

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        # WARNING and up: stored lines need the logger's level to allow them.
        context.logger.warning(
            "sending with key %s", SECRET, extra={"api_key": SECRET, "rows": 2}
        )
        if context.execution_input.get("fail"):
            context.logger.error("upstream refused")
            raise NodeError(
                f"upstream rejected key {SECRET}", kind=NodeErrorKind.PERMANENT
            )
        return NodeResult({})


@dataclass
class Env:
    client: httpx.AsyncClient
    runtime: ExecutionRuntime
    kit: AuthKit
    admin: dict[str, str]
    app: Any

    def client_with(self, headers: dict[str, str]) -> httpx.AsyncClient:
        """Another caller against the same app (close it after use)."""
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
            headers=headers,
        )

    async def run(self, project_id: str, version: int, **input: Any) -> str:
        created = await self.client.post(
            f"/api/v1/projects/{project_id}/versions/{version}/execute", json=input
        )
        assert created.status_code == 201, created.text
        execution_id: str = created.json()["id"]
        await self.runtime.run(execution_id)
        return execution_id


@pytest.fixture
async def env(
    database: Database, project_service: ProjectService
) -> AsyncIterator[Env]:
    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    # A short live poll keeps the WebSocket tests fast; they wait on frames,
    # never on time.
    settings = Settings(
        environment="test", log_level="CRITICAL", live_poll_seconds=0.01
    )
    kit = make_auth_kit(database)
    executions = ExecutionService(uow)
    runtime = ExecutionRuntime(uow, {"test.source": _Source, "test.sink": _Sink})
    container = Container(
        settings=settings,
        nodes=create_builtin_registry(),
        database=database,
        projects=project_service,
        executions=executions,
        runtime=runtime,
        triggers=TriggerService(executions),
        trigger_registry=TriggerRegistry(uow),
        **kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    admin = await kit.admin_headers()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=admin,
    ) as client:
        yield Env(client, runtime, kit, admin, app)


# -- list -----------------------------------------------------------------------


async def test_list_executions_newest_first_and_paginated(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    assert (await env.client.get(f"/api/v1/projects/{pid}/executions")).json() == {
        "items": [],
        "page": {"offset": 0, "limit": 50, "has_more": False},
    }
    ids = [await env.run(pid, version) for _ in range(3)]

    first = (
        await env.client.get(f"/api/v1/projects/{pid}/executions", params={"limit": 2})
    ).json()
    second = (
        await env.client.get(
            f"/api/v1/projects/{pid}/executions", params={"offset": 2, "limit": 2}
        )
    ).json()

    listed = [e["id"] for e in first["items"] + second["items"]]
    assert listed == ids[::-1]
    assert (first["page"]["has_more"], second["page"]["has_more"]) == (True, False)
    item = first["items"][0]
    assert (item["status"], item["version"], item["trigger_type"]) == (
        "completed",
        version,
        "manual",
    )
    assert item["started_at"] is not None and item["finished_at"] is not None


async def test_list_executions_filter_by_version(env: Env) -> None:
    """Which runs used which immutable version (frontend Phase 12)."""
    pid, v1, revision = await publish_with(env.client, [])
    _, v2, _ = await publish_with(env.client, [], project_id=pid, revision=revision)
    old = await env.run(pid, v1)
    new = [await env.run(pid, v2) for _ in range(2)]

    def listed(params: dict[str, int]) -> Any:
        return env.client.get(f"/api/v1/projects/{pid}/executions", params=params)

    of_v1 = (await listed({"version": v1})).json()["items"]
    assert [(e["id"], e["version"]) for e in of_v1] == [(old, v1)]
    of_v2 = (await listed({"version": v2})).json()["items"]
    assert [e["id"] for e in of_v2] == new[::-1]
    paged = (await listed({"version": v2, "limit": 1})).json()
    assert [e["id"] for e in paged["items"]] == [new[1]]
    assert paged["page"]["has_more"] is True
    assert (await listed({"version": 99})).json()["items"] == []
    assert (await listed({"version": 0})).status_code == 422


async def test_list_executions_rejects_bad_pages_and_unknown_projects(
    env: Env,
) -> None:
    pid, _, _ = await publish_with(env.client, [])
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        response = await env.client.get(
            f"/api/v1/projects/{pid}/executions", params=params
        )
        assert response.status_code == 422, params
    assert_error(
        await env.client.get(f"/api/v1/projects/{UNKNOWN_PROJECT}/executions"),
        404,
        "PROJECT_NOT_FOUND",
    )


# -- cross-project listing and counts ------------------------------------------


async def test_recent_executions_span_projects_newest_first(env: Env) -> None:
    assert (await env.client.get("/api/v1/executions")).json() == {
        "items": [],
        "page": {"offset": 0, "limit": 50, "has_more": False},
    }
    a, va, _ = await publish_with(env.client, [])
    b, vb, _ = await publish_with(env.client, [])
    ids = [await env.run(a, va), await env.run(b, vb), await env.run(a, va)]

    first = (await env.client.get("/api/v1/executions", params={"limit": 2})).json()
    second = (
        await env.client.get("/api/v1/executions", params={"offset": 2, "limit": 2})
    ).json()

    items = first["items"] + second["items"]
    assert [e["id"] for e in items] == ids[::-1]
    assert [e["project_id"] for e in items] == [a, b, a]
    assert (first["page"]["has_more"], second["page"]["has_more"]) == (True, False)


async def test_listings_name_each_project_by_its_current_name(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)
    project = (await env.client.get(f"/api/v1/projects/{pid}")).json()
    renamed = await env.client.put(
        f"/api/v1/projects/{pid}",
        json={
            "expected_revision": project["revision"],
            "name": "Renamed after the run",
            "draft": project["draft"],
        },
    )
    assert renamed.status_code == 200, renamed.text

    for path in ("/api/v1/executions", f"/api/v1/projects/{pid}/executions"):
        (item,) = (await env.client.get(path)).json()["items"]
        assert (item["id"], item["project_name"]) == (
            execution_id,
            "Renamed after the run",
        )
    # Single-execution responses (and the public webhook) never carry it.
    detail = (await env.client.get(f"/api/v1/executions/{execution_id}")).json()
    assert "project_name" not in detail


async def test_recent_executions_filter_by_status(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    completed = await env.run(pid, version)
    failed = await env.run(pid, version, fail=True)
    queued = (
        await env.client.post(f"/api/v1/projects/{pid}/versions/{version}/execute")
    ).json()["id"]

    async def listed(*statuses: str) -> list[str]:
        response = await env.client.get(
            "/api/v1/executions", params=[("status", s) for s in statuses]
        )
        assert response.status_code == 200, response.text
        return [e["id"] for e in response.json()["items"]]

    assert await listed("failed", "timeout") == [failed]
    assert await listed("queued", "running") == [queued]
    assert await listed() == [queued, failed, completed]
    bad = await env.client.get("/api/v1/executions", params={"status": "exploded"})
    assert bad.status_code == 422
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        response = await env.client.get("/api/v1/executions", params=params)
        assert response.status_code == 422, params


async def test_status_counts_cover_every_status(env: Env) -> None:
    empty = (await env.client.get("/api/v1/executions/status-counts")).json()
    assert empty == {
        "counts": {
            s: 0
            for s in (
                "created",
                "queued",
                "running",
                "paused",
                "waiting",
                "completed",
                "failed",
                "cancelled",
                "timeout",
            )
        }
    }
    pid, version, _ = await publish_with(env.client, [])
    await env.run(pid, version)
    await env.run(pid, version)
    await env.run(pid, version, fail=True)

    counts = (await env.client.get("/api/v1/executions/status-counts")).json()
    assert counts["counts"]["completed"] == 2
    assert counts["counts"]["failed"] == 1
    assert sum(counts["counts"].values()) == 3


async def test_members_see_only_their_projects_executions(env: Env) -> None:
    admin_pid, admin_version, _ = await publish_with(env.client, [])
    await env.run(admin_pid, admin_version, fail=True)
    member_headers = await env.kit.member_headers(env.admin, "member")
    async with env.client_with(member_headers) as member:
        assert (await member.get("/api/v1/executions")).json()["items"] == []
        assert (
            sum(
                (await member.get("/api/v1/executions/status-counts"))
                .json()["counts"]
                .values()
            )
            == 0
        )
        pid, version, _ = await publish_with(member, [])
        own = await env.run(pid, version)

        listed = (await member.get("/api/v1/executions")).json()["items"]
        counts = (await member.get("/api/v1/executions/status-counts")).json()

    assert [e["id"] for e in listed] == [own]
    assert listed[0]["project_name"] == "t"
    assert counts["counts"]["completed"] == 1
    assert counts["counts"]["failed"] == 0
    # The admin still sees every project's runs.
    everything = (await env.client.get("/api/v1/executions")).json()["items"]
    assert len(everything) == 2


async def test_cross_project_routes_require_a_session(env: Env) -> None:
    async with env.client_with({}) as anonymous:
        for path in ("/api/v1/executions", "/api/v1/executions/status-counts"):
            assert_error(await anonymous.get(path), 401, "AUTHENTICATION_REQUIRED")


# -- events ---------------------------------------------------------------------


async def test_event_history_is_ordered_and_resumable(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)
    url = f"/api/v1/executions/{execution_id}/events"

    everything = (await env.client.get(url)).json()
    types = [e["event_type"] for e in everything["items"]]
    assert types[0] == "execution.created"
    assert types[-1] == "execution.completed"
    assert types.count("node.completed") == 2
    positions = [e["position"] for e in everything["items"]]
    assert positions == sorted(positions)
    assert everything["has_more"] is False
    assert everything["next_after"] == positions[-1]
    assert {e["execution_id"] for e in everything["items"]} == {execution_id}

    # Page through with the cursor: the same history, nothing twice.
    seen: list[int] = []
    after = 0
    while True:
        page = (await env.client.get(url, params={"after": after, "limit": 2})).json()
        seen += [e["position"] for e in page["items"]]
        after = page["next_after"]
        if not page["has_more"]:
            break
    assert seen == positions
    caught_up = (await env.client.get(url, params={"after": after})).json()
    assert (caught_up["items"], caught_up["next_after"]) == ([], after)


async def test_event_history_never_returns_secrets(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version, fail=True)

    response = await env.client.get(f"/api/v1/executions/{execution_id}/events")

    assert response.status_code == 200
    assert "node.failed" in {e["event_type"] for e in response.json()["items"]}
    assert SECRET not in response.text


async def test_events_of_unknown_execution_is_404(env: Env) -> None:
    assert_error(
        await env.client.get(f"/api/v1/executions/{UNKNOWN_EXECUTION}/events"),
        404,
        "EXECUTION_NOT_FOUND",
    )


# -- node logs ------------------------------------------------------------------


async def test_node_logs_are_filterable_and_resumable(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version, fail=True)
    url = f"/api/v1/executions/{execution_id}/logs"

    everything = (await env.client.get(url)).json()
    lines = everything["items"]
    assert [(line["level"], line["attempt"]) for line in lines] == [
        ("WARNING", 1),
        ("ERROR", 1),
    ]
    assert lines[1]["message"] == "upstream refused"
    assert lines[0]["fields"]["rows"] == 2
    assert (everything["has_more"], everything["next_after"]) == (
        False,
        lines[-1]["position"],
    )
    sink = lines[0]["node_id"]

    errors = (await env.client.get(url, params={"level": "ERROR"})).json()
    assert [line["message"] for line in errors["items"]] == ["upstream refused"]
    of_node = await env.client.get(url, params={"node_id": sink})
    assert len(of_node.json()["items"]) == 2
    other = await env.client.get(url, params={"node_id": "node_" + "0" * 32})
    assert other.json()["items"] == []

    # Search is literal and case-insensitive: % and _ are not wildcards.
    found = (await env.client.get(url, params={"q": "UPSTREAM"})).json()
    assert [line["message"] for line in found["items"]] == ["upstream refused"]
    for literal in ("%", "_"):
        none = (await env.client.get(url, params={"q": literal})).json()
        assert none["items"] == []

    first = (await env.client.get(url, params={"limit": 1})).json()
    assert (len(first["items"]), first["has_more"]) == (1, True)
    rest = (await env.client.get(url, params={"after": first["next_after"]})).json()
    assert [line["position"] for line in first["items"] + rest["items"]] == [
        line["position"] for line in lines
    ]


async def test_node_logs_never_return_secrets(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)

    response = await env.client.get(f"/api/v1/executions/{execution_id}/logs")

    assert response.status_code == 200
    [line] = response.json()["items"]
    assert line["message"].startswith("sending with key ")
    assert line["fields"]["api_key"] == "[REDACTED]"
    assert SECRET not in response.text


async def test_node_logs_errors_and_visibility(env: Env) -> None:
    assert_error(
        await env.client.get(f"/api/v1/executions/{UNKNOWN_EXECUTION}/logs"),
        404,
        "EXECUTION_NOT_FOUND",
    )
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)
    url = f"/api/v1/executions/{execution_id}/logs"
    for params in (
        {"level": "LOUD"},
        {"limit": 0},
        {"after": -1},
        {"q": ""},
        {"q": "x" * 201},
    ):
        assert (await env.client.get(url, params=params)).status_code == 422
    member_headers = await env.kit.member_headers(env.admin, "member")
    async with env.client_with(member_headers) as member:
        hidden = await member.get(url)
    async with env.client_with({}) as anonymous:
        anonymous_response = await anonymous.get(url)
    assert hidden.status_code == 404  # the same answer as for unknown ids
    assert anonymous_response.status_code == 401


# -- live (WebSocket) -------------------------------------------------------------


@dataclass
class LiveServer:
    url: str
    server: uvicorn.Server
    task: asyncio.Task[None]

    async def stop(self) -> None:
        self.server.should_exit = True
        await self.task


@pytest.fixture
async def live_server(env: Env) -> AsyncIterator[LiveServer]:
    """The app on a real socket: httpx's ASGI transport has no WebSocket.
    The socket is bound and listening before the server starts, so clients
    can connect at once (the backlog holds them), with no readiness wait."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(env.app, lifespan="off", log_level="critical")
    )
    live = LiveServer(
        f"ws://127.0.0.1:{port}/api/v1/executions",
        server,
        asyncio.create_task(server.serve(sockets=[sock])),
    )
    try:
        yield live
    finally:
        if not live.task.done():
            await live.stop()
        sock.close()


@pytest.fixture
def live_url(live_server: LiveServer) -> str:
    return live_server.url


def _token(headers: dict[str, str]) -> str:
    return headers["Authorization"].removeprefix("Bearer ")


async def _frame(ws: ClientConnection) -> dict[str, Any]:
    # A guard against a hang, not a wait: frames arrive when events exist.
    async with asyncio.timeout(10):
        frame: dict[str, Any] = json.loads(await ws.recv())
    return frame


async def _close_code(ws: ClientConnection) -> int:
    with pytest.raises(ConnectionClosed) as closed:
        await _frame(ws)
    assert closed.value.rcvd is not None
    return closed.value.rcvd.code


async def _subscribe(
    url: str, execution_id: str, token: str, after: int = 0
) -> ClientConnection:
    ws = await connect(f"{url}/{execution_id}/live")
    await ws.send(json.dumps({"type": "subscribe", "token": token, "after": after}))
    return ws


async def _drain(ws: ClientConnection) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every streamed event until ``end``; the connection must then close 1000."""
    events: list[dict[str, Any]] = []
    while (frame := await _frame(ws))["type"] == "events":
        events += frame["items"]
    assert frame["type"] == "end"
    assert await _close_code(ws) == 1000
    return events, frame


async def test_live_streams_a_running_execution_until_it_ends(
    env: Env, live_url: str
) -> None:
    pid, version, _ = await publish_with(env.client, [])
    created = await env.client.post(
        f"/api/v1/projects/{pid}/versions/{version}/execute", json={}
    )
    execution_id = created.json()["id"]

    ws = await _subscribe(live_url, execution_id, _token(env.admin))
    assert await _frame(ws) == {
        "type": "subscribed",
        "execution_id": execution_id,
        "after": 0,
    }
    # Still QUEUED: what is stored so far, then the stream waits.
    first = await _frame(ws)
    assert [e["event_type"] for e in first["items"]] == ["execution.created"]

    await env.runtime.run(execution_id)
    rest, end = await _drain(ws)

    streamed = first["items"] + rest
    stored = (await env.client.get(f"/api/v1/executions/{execution_id}/events")).json()
    # Exactly the persisted log: same events, same order, nothing twice.
    assert streamed == stored["items"]
    assert end == {"type": "end", "status": "completed"}


async def test_live_resumes_after_the_last_seen_position(
    env: Env, live_url: str
) -> None:
    """A reconnect passes the last position it saw: the events it missed
    while disconnected arrive, the ones it had do not come again."""
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)
    stored = (await env.client.get(f"/api/v1/executions/{execution_id}/events")).json()
    seen = stored["items"][1]["position"]

    ws = await _subscribe(live_url, execution_id, _token(env.admin), after=seen)
    assert (await _frame(ws))["after"] == seen
    missed, end = await _drain(ws)

    assert missed == stored["items"][2:]
    assert end["status"] == "completed"

    # Caught up on a finished execution: no events, just the end.
    last = stored["items"][-1]["position"]
    ws = await _subscribe(live_url, execution_id, _token(env.admin), after=last)
    await _frame(ws)
    assert await _drain(ws) == ([], {"type": "end", "status": "completed"})


async def test_live_never_streams_secrets(env: Env, live_url: str) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version, fail=True)

    ws = await _subscribe(live_url, execution_id, _token(env.admin))
    await _frame(ws)
    events, end = await _drain(ws)

    assert "node.failed" in {e["event_type"] for e in events}
    assert SECRET not in json.dumps(events)
    assert end["status"] == "failed"


async def test_live_rejects_invalid_sessions_and_hidden_executions(
    env: Env, live_url: str
) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)

    for token in ("not-a-session", ""):
        ws = await _subscribe(live_url, execution_id, token)
        assert await _close_code(ws) == 4401

    # Another member's execution answers like an unknown one.
    member = await env.kit.member_headers(env.admin, "member")
    ws = await _subscribe(live_url, execution_id, _token(member))
    assert await _close_code(ws) == 4404
    ws = await _subscribe(live_url, UNKNOWN_EXECUTION, _token(env.admin))
    assert await _close_code(ws) == 4404


@pytest.mark.parametrize(
    "first_frame",
    [
        "not json",
        json.dumps({"type": "hello", "token": "t"}),
        json.dumps({"type": "subscribe", "token": "t", "after": -1}),
        b"binary",
    ],
)
async def test_live_rejects_a_malformed_subscribe_frame(
    env: Env, live_url: str, first_frame: str | bytes
) -> None:
    ws = await connect(f"{live_url}/{UNKNOWN_EXECUTION}/live")
    await ws.send(first_frame)
    assert await _close_code(ws) == 4400


async def test_live_server_shutdown_closes_open_streams_for_reconnect(
    env: Env, live_server: LiveServer
) -> None:
    """Graceful shutdown (AGENTS §48) with a viewer attached: the server
    stops promptly instead of waiting for the execution, and the client gets
    a close code it treats as transient (it reconnects to the next server)."""
    pid, version, _ = await publish_with(env.client, [])
    created = await env.client.post(
        f"/api/v1/projects/{pid}/versions/{version}/execute", json={}
    )
    ws = await _subscribe(live_server.url, created.json()["id"], _token(env.admin))
    await _frame(ws)  # subscribed
    await _frame(ws)  # execution.created; now idle, waiting for events

    async with asyncio.timeout(10):  # a hang guard, not a wait
        await live_server.stop()

    # 1012 "service restart": not one of the terminal 4400/4401/4404.
    assert await _close_code(ws) == 1012


async def test_live_closes_when_the_session_ends_mid_stream(
    env: Env, live_url: str
) -> None:
    pid, version, _ = await publish_with(env.client, [])
    created = await env.client.post(
        f"/api/v1/projects/{pid}/versions/{version}/execute", json={}
    )
    execution_id = created.json()["id"]
    session = await env.kit.login("admin")

    ws = await _subscribe(live_url, execution_id, _token(session))
    await _frame(ws)  # subscribed
    await _frame(ws)  # execution.created
    async with env.client_with(session) as client:
        assert (await client.post("/api/v1/auth/logout")).status_code == 204

    # The next poll re-checks the session: a logged-out stream stops.
    assert await _close_code(ws) == 4401


async def _cookie_session(env: Env) -> str:
    """A browser login: the session exists only as the cookie value."""
    response = await env.client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD, "cookie": True},
        headers={"X-Akasha-CSRF": "1"},
    )
    assert response.status_code == 200, response.text
    return response.headers["set-cookie"].split(";")[0]


async def _subscribe_with_cookie(
    url: str, execution_id: str, cookie: str, origin: str | None
) -> ClientConnection:
    ws = await connect(
        f"{url}/{execution_id}/live",
        additional_headers={"Cookie": cookie},
        origin=None if origin is None else Origin(origin),
    )
    await ws.send(json.dumps({"type": "subscribe", "after": 0}))
    return ws


async def test_live_accepts_the_session_cookie_from_the_same_origin(
    env: Env, live_url: str
) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)
    cookie = await _cookie_session(env)
    host = live_url.removeprefix("ws://").split("/")[0]

    ws = await _subscribe_with_cookie(live_url, execution_id, cookie, f"http://{host}")
    assert (await _frame(ws))["type"] == "subscribed"
    _, end = await _drain(ws)
    assert end["status"] == "completed"


@pytest.mark.parametrize("origin", ["http://evil.example", None])
async def test_live_ignores_the_cookie_from_another_origin(
    env: Env, live_url: str, origin: str | None
) -> None:
    """Cross-site WebSocket hijacking: the browser attaches the cookie to a
    handshake any page opens, so only the UI's own origin may use it."""
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)
    cookie = await _cookie_session(env)

    ws = await _subscribe_with_cookie(live_url, execution_id, cookie, origin)
    assert await _close_code(ws) == 4401


async def test_live_accepts_the_proxy_forwarded_host(env: Env, live_url: str) -> None:
    """Behind a reverse proxy that rewrites Host, X-Forwarded-Host names
    the origin the browser saw."""
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)
    cookie = await _cookie_session(env)
    ws = await connect(
        f"{live_url}/{execution_id}/live",
        additional_headers={"Cookie": cookie, "X-Forwarded-Host": "akasha.example"},
        origin=Origin("https://akasha.example"),
    )
    await ws.send(json.dumps({"type": "subscribe", "after": 0}))
    assert (await _frame(ws))["type"] == "subscribed"


# -- cancel ---------------------------------------------------------------------


async def test_cancel_queued_execution_then_conflict(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    created = await env.client.post(
        f"/api/v1/projects/{pid}/versions/{version}/execute", json={}
    )
    execution_id = created.json()["id"]
    url = f"/api/v1/executions/{execution_id}/cancel"
    # Clients offer cancel from this flag; it must match what the route allows.
    assert (created.json()["status"], created.json()["cancellable"]) == (
        "queued",
        True,
    )
    listed = (await env.client.get(f"/api/v1/projects/{pid}/executions")).json()
    assert listed["items"][0]["cancellable"] is True

    cancelled = await env.client.post(url, json={"reason": "not needed"})

    assert cancelled.status_code == 202, cancelled.text
    assert (cancelled.json()["status"], cancelled.json()["cancellable"]) == (
        "cancelled",
        False,
    )
    events = (await env.client.get(f"/api/v1/executions/{execution_id}/events")).json()
    assert events["items"][-1]["event_type"] == "execution.cancelled"
    # A finished execution cannot be cancelled again; the runtime never
    # revives it either.
    assert_error(await env.client.post(url), 409, "INVALID_STATE_TRANSITION")
    with pytest.raises(InvalidStateTransitionError):
        await env.runtime.run(execution_id)


async def test_cancel_completed_or_unknown_execution(env: Env) -> None:
    pid, version, _ = await publish_with(env.client, [])
    execution_id = await env.run(pid, version)
    detail = (await env.client.get(f"/api/v1/executions/{execution_id}")).json()
    assert (detail["status"], detail["cancellable"]) == ("completed", False)
    assert_error(
        await env.client.post(f"/api/v1/executions/{execution_id}/cancel"),
        409,
        "INVALID_STATE_TRANSITION",
    )
    assert_error(
        await env.client.post(f"/api/v1/executions/{UNKNOWN_EXECUTION}/cancel"),
        404,
        "EXECUTION_NOT_FOUND",
    )
    response = await env.client.post(
        f"/api/v1/executions/{execution_id}/cancel", json={"reason": "x" * 501}
    )
    assert response.status_code == 422


# -- node catalog -----------------------------------------------------------------


async def test_node_catalog_lists_every_registered_type(env: Env) -> None:
    member = await env.kit.member_headers(env.admin, "member")

    response = await env.client.get("/api/v1/nodes", headers=member)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    node_types = [n["node_type"] for n in items]
    assert node_types == sorted(create_builtin_registry().definitions)
    llm = next(n for n in items if n["node_type"] == "core.llm")
    assert llm["category"] == "ai"
    assert {"provider", "model", "user_prompt"} <= set(llm["config_schema"]["required"])
    variables = next(p for p in llm["inputs"] if p["name"] == "variables")
    assert (variables["type"], variables["required"]) == ("object", False)
    search = next(n for n in items if n["node_type"] == "core.web_search")
    assert search["outputs"][0]["schema"]["type"] == "array"
    flags = next(n for n in items if n["node_type"] == "core.if")["capabilities"]
    assert flags["is_control_flow"] is True
    smtp = next(n for n in items if n["node_type"] == "core.smtp")["config_schema"]
    assert smtp["properties"]["password_credential"]["format"] == (
        "credential-reference"
    )
    assert smtp["properties"]["security"]["default"] == "starttls"
    compatibility = response.json()["port_compatibility"]
    assert compatibility == port_compatibility()
    assert "number" not in compatibility["string"]


async def validate_config(
    env: Env, node_type: str, config: object, **kwargs: Any
) -> httpx.Response:
    return await env.client.post(
        f"/api/v1/nodes/{node_type}/config/validate", json={"config": config}, **kwargs
    )


async def test_validate_config_reports_every_issue_with_its_path(env: Env) -> None:
    member = await env.kit.member_headers(env.admin, "member")
    config = {
        "url": "ftp://example.com",
        "headers": {"X-Count": 3},
        "header_credentials": {"Authorization": "credential:crm-api"},
        "typo": True,
    }

    response = await validate_config(env, "core.http", config, headers=member)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "valid": False,
        "issues": [
            {"path": ["url"], "message": "does not match the required format"},
            {"path": ["headers", "X-Count"], "message": "must be string"},
            {"path": ["typo"], "message": "is not allowed"},
        ],
    }
    ok = await validate_config(env, "core.http", {"url": "https://example.com"})
    assert ok.json() == {"valid": True, "issues": []}


async def test_validate_config_locates_secrets_without_echoing_them(env: Env) -> None:
    response = await validate_config(
        env,
        "core.smtp",
        {
            "host": "mail.example.com",
            "sender": "a@example.com",
            "recipients": ["b@example.com"],
            "password_credential": SECRET,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["issues"] == [
        {
            "path": ["password_credential"],
            "message": "contains a secret value; use a credential reference",
        },
        {
            "path": ["password_credential"],
            "message": "must be a credential reference (credential:<name>)",
        },
    ]
    assert SECRET not in response.text


async def test_validate_config_errors(env: Env) -> None:
    assert_error(await validate_config(env, "acme.missing", {}), 404, "NODE_NOT_FOUND")
    assert_error(
        await validate_config(env, "core.http", ["not", "an", "object"]),
        422,
        "VALIDATION_ERROR",
    )
    unauthenticated = await env.client.post(
        "/api/v1/nodes/core.http/config/validate",
        json={"config": {}},
        headers={"Authorization": ""},
    )
    assert unauthenticated.status_code == 401


# -- system status ----------------------------------------------------------------


async def test_system_status_reports_version_and_readiness(env: Env) -> None:
    member_headers = await env.kit.member_headers(env.admin, "member")
    async with env.client_with(member_headers) as member:
        ready = await member.get("/api/v1/system/status")
        assert ready.status_code == 200, ready.text
        assert ready.json() == {"version": __version__, "ready": True, "failures": {}}

        async def failing_check() -> str:
            return "connection refused"

        env.app.state.container.ready_checks["database"] = failing_check
        degraded = await member.get("/api/v1/system/status")

    # Still 200: the caller asked for the status, unlike /ready's 503.
    assert degraded.status_code == 200
    assert degraded.json() == {
        "version": __version__,
        "ready": False,
        "failures": {"database": "connection refused"},
    }
    async with env.client_with({}) as anonymous:
        assert_error(
            await anonymous.get("/api/v1/system/status"),
            401,
            "AUTHENTICATION_REQUIRED",
        )
