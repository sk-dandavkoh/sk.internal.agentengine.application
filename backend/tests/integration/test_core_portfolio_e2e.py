"""Core node portfolio end to end, through the production composition root.

    HTTP API → WorkflowService → PostgreSQL → TriggerService (manual,
    webhook) → ExecutionService → Redis queue → in-process Worker →
    ExecutionRuntime → built-in nodes → … → WAITING → POST
    /executions/{id}/inputs/{node} → queue → Worker → COMPLETED

Real: PostgreSQL, Redis, SQLite files in the workflow workspace, Mailpit,
Chromium (workflow C; skipped with its reason when not installed). Local,
deterministic stand-ins: the LLM provider and the HTTP APIs (a loopback
server), so nothing reaches the public internet (§124). Waiting is
event-driven: the test listens to the engine's own events, never sleeps.

The four workflows (core-node-portfolio.md, "E2E"):

A  HTTP → Transform → Database → Agent → Human → Transform → HTTP
B  Webhook → Transform → Agent → Human → Database → SMTP
C  Agent → Browser (extract) → Transform → Database
D  File Read → Transform (CSV) → Database → Agent
"""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from akasha.api.app import create_app
from akasha.application.triggers import webhook_signature
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.event import Event, EventType
from akasha.domain.primitives import new_id
from tests.integration.test_e2e_examples import (
    KEY,
    LLM_KEY,
    PASSWORD,
    ok,
    tool_call,
)
from tests.integration.test_llm_node_runtime import answer
from tests.support import (
    SMTP_LOGIN,
    LocalHttpServer,
    Mailpit,
    capture_json_logs,
    serve_http,
)

pytestmark = pytest.mark.usefixtures("clean_db")

GUARD = 60
HOOK_SECRET = "whsec-e2e-0123456789abcdef"
SETTLED = {
    EventType.EXECUTION_WAITING,
    EventType.EXECUTION_COMPLETED,
    EventType.EXECUTION_FAILED,
    EventType.EXECUTION_CANCELLED,
    EventType.EXECUTION_TIMED_OUT,
}
MODEL = {"provider": "local", "model": "e2e-model", "credential": "credential:openai"}


@dataclass
class Engine:
    client: httpx.AsyncClient
    container: Container
    server: LocalHttpServer
    workspace: Path
    logs: list[dict[str, Any]]
    settled: dict[str, asyncio.Queue[str]] = field(default_factory=dict)

    def queue(self, execution_id: str) -> asyncio.Queue[str]:
        return self.settled.setdefault(execution_id, asyncio.Queue())

    async def next_state(self, execution_id: str) -> str:
        """The next WAITING or terminal event the worker published."""
        async with asyncio.timeout(GUARD):
            return await self.queue(execution_id).get()


@pytest.fixture
async def engine(db_url: str, redis_url: str, tmp_path: Path) -> AsyncIterator[Engine]:
    with serve_http() as server, capture_json_logs() as logs:
        settings = Settings(
            environment="test",
            database_url=db_url,
            redis_url=SecretStr(redis_url),
            queue_prefix=new_id("e2e")[-32:],
            credential_encryption_key=SecretStr(KEY),
            openai_compatible_base_url=AnyHttpUrl(f"{server.base_url}/v1"),
            openai_compatible_name="local",
            network_allow_private=True,  # the loopback servers and Mailpit
            workspace_dir=tmp_path,
            scheduler_enabled=False,
            worker_poll_seconds=0.05,
        )
        app = create_app(settings=settings)
        container: Container = app.state.container
        engine_ref: list[Engine] = []

        async def on_settled(event: Event) -> None:
            assert event.execution_id is not None
            engine_ref[0].queue(event.execution_id).put_nowait(event.event_type.value)

        container.events.subscribe(on_settled, event_types=SETTLED)
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(app.router.lifespan_context(app))
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://e2e"
                )
            )
            engine_ref.append(Engine(client, container, server, tmp_path, logs))
            yield engine_ref[0]


# -- helpers -------------------------------------------------------------------


async def sign_in(engine: Engine) -> None:
    """Set the installation up, log in as its admin, store the credentials
    the workflows reference (as test_e2e_examples.login does)."""
    c = engine.client
    admin = {"username": "admin", "password": PASSWORD}
    ok(await c.post("/api/v1/auth/setup", json=admin), 201)
    token = ok(await c.post("/api/v1/auth/login", json=admin))["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    for name, kind, value in (
        ("openai", "provider", LLM_KEY),
        ("smtp-password", "smtp", SMTP_LOGIN[1]),
    ):
        credential = {"name": name, "kind": kind, "value": value}
        ok(await c.post("/api/v1/credentials", json=credential), 201)


@dataclass
class Spec:
    """A draft written compactly: label → (type, config); "a.out>b.in"."""

    nodes: dict[str, tuple[str, dict[str, Any]]]
    edges: list[str]
    triggers: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": new_id("trg"), "trigger_type": "manual", "config": {}}
        ]
    )
    ids: dict[str, str] = field(default_factory=dict)

    def draft(self) -> dict[str, Any]:
        self.ids = {label: new_id("node") for label in self.nodes}
        edges = []
        for spec in self.edges:
            src, dst = spec.split(">")
            (s, sp), (t, tp) = src.split("."), dst.split(".")
            edges.append(
                {
                    "source_node_id": self.ids[s],
                    "source_port": sp,
                    "target_node_id": self.ids[t],
                    "target_port": tp,
                }
            )
        nodes = [
            {"id": self.ids[label], "node_type": kind, "label": label, "config": config}
            for label, (kind, config) in self.nodes.items()
        ]
        return {
            "graph": {"nodes": nodes, "edges": edges},
            "triggers": self.triggers,
            "dependencies": [],
        }


async def publish(engine: Engine, spec: Spec, name: str) -> str:
    c = engine.client
    workflow = ok(await c.post("/api/v1/workflows", json={"name": name}), 201)
    updated = ok(
        await c.put(
            f"/api/v1/workflows/{workflow['id']}",
            json={
                "expected_revision": workflow["revision"],
                "name": name,
                "draft": spec.draft(),
            },
        )
    )
    validation = ok(await c.post(f"/api/v1/workflows/{workflow['id']}/validate"))
    assert validation == {"valid": True, "issues": []}, validation
    ok(
        await c.post(
            f"/api/v1/workflows/{workflow['id']}/publish",
            json={"expected_revision": updated["revision"]},
        ),
        201,
    )
    return str(workflow["id"])


async def run(engine: Engine, workflow_id: str, payload: dict[str, Any]) -> str:
    created = ok(
        await engine.client.post(
            f"/api/v1/workflows/{workflow_id}/versions/1/execute", json=payload
        ),
        201,
    )
    engine.queue(created["id"])
    return str(created["id"])


async def outputs(engine: Engine, execution_id: str) -> dict[str, Any]:
    doc = ok(await engine.client.get(f"/api/v1/reports/executions/{execution_id}"))
    return {n["label"]: n["outputs"] for n in doc["nodes"]}


async def approve(
    engine: Engine, execution_id: str, node_id: str, **response: Any
) -> None:
    pending = ok(await engine.client.get(f"/api/v1/executions/{execution_id}/inputs"))
    assert [p["node_id"] for p in pending["items"]] == [node_id]
    accepted = ok(
        await engine.client.post(
            f"/api/v1/executions/{execution_id}/inputs/{node_id}",
            json={"response": response},
        ),
        202,
    )
    assert accepted["status"] == "queued"


def sqlite_rows(
    engine: Engine, workflow_id: str, db: str, query: str
) -> list[tuple[Any, ...]]:
    with sqlite3.connect(engine.workspace / workflow_id / db) as conn:
        return conn.execute(query).fetchall()


async def create_table(engine: Engine, workflow_id: str, db: str, ddl: str) -> None:
    directory = engine.workspace / workflow_id
    directory.mkdir(exist_ok=True)
    with sqlite3.connect(directory / db) as conn:
        conn.execute(ddl)


def assert_no_secret_escaped(engine: Engine, *texts: str) -> None:
    logged = json.dumps(engine.logs)
    for secret in (LLM_KEY, KEY, HOOK_SECRET, SMTP_LOGIN[1]):
        assert secret not in logged
        for text in texts:
            assert secret not in text


# -- Workflow A ----------------------------------------------------------------


async def test_a_http_transform_database_agent_human_http(engine: Engine) -> None:
    await sign_in(engine)
    base = engine.server.base_url
    spec = Spec(
        {
            "leads": ("core.input", {"field": "leads"}),
            # The loopback API echoes what it receives; the leads come back
            # as JSON text inside the response, like a real API's payload.
            "fetch": ("core.http", {"method": "POST", "url": f"{base}/echo"}),
            "hot": (
                "core.transform",
                {
                    "steps": [
                        {"op": "pick", "path": "body"},
                        {"op": "parse_json"},
                        {
                            "op": "filter",
                            "field": "score",
                            "operator": "greater_than",
                            "operand": 50,
                        },
                        {"op": "to_json"},
                    ]
                },
            ),
            "row": ("core.set", {"key": "leads"}),
            "store": (
                "core.database",
                {
                    "engine": "sqlite",
                    "database": "leads.db",
                    "sql": "INSERT INTO hot (name, score) SELECT value ->> 'name',"
                    " value ->> 'score' FROM json_each(:leads)",
                },
            ),
            "count": ("core.set", {"key": "stored"}),
            "writer": (
                "core.agent",
                MODEL
                | {
                    "user_prompt": "{{ stored }} hot leads were stored."
                    " Summarize them.",
                    "tools": [
                        {
                            "name": "hot_leads",
                            "node_type": "core.database",
                            "config": {
                                "engine": "sqlite",
                                "database": "leads.db",
                                "sql": "SELECT name, score FROM hot"
                                " ORDER BY score DESC",
                                "read_only": True,
                            },
                        }
                    ],
                },
            ),
            "review": ("core.human", {"prompt": "Send this summary? {{ value }}"}),
            "body": ("core.set", {"key": "summary"}),
            "notify": ("core.http", {"method": "POST", "url": f"{base}/echo"}),
            "sent": ("core.output", {}),
        },
        [
            "leads.value>fetch.body",
            "fetch.body>hot.value",
            "hot.value>row.value",
            "row.value>store.params",
            "store.row_count>count.value",
            "count.value>writer.variables",
            "writer.text>review.value",
            "review.approved>body.value",
            "body.value>notify.body",
            "notify.status>sent.value",
        ],
    )
    workflow_id = await publish(engine, spec, "A")
    await create_table(
        engine, workflow_id, "leads.db", "CREATE TABLE hot (name TEXT, score INT)"
    )
    engine.server.state.llm_bodies = [
        tool_call("c1", "hot_leads", {}),
        answer("Ada (91) and Bo (77) are hot."),
    ]
    leads = [
        {"name": "Ada", "score": 91},
        {"name": "Cy", "score": 12},
        {"name": "Bo", "score": 77},
    ]
    execution_id = await run(engine, workflow_id, {"leads": leads})

    assert await engine.next_state(execution_id) == "execution.waiting"
    assert sqlite_rows(
        engine, workflow_id, "leads.db", "SELECT name FROM hot ORDER BY name"
    ) == [
        ("Ada",),
        ("Bo",),
    ]
    detail = ok(await engine.client.get(f"/api/v1/executions/{execution_id}"))
    assert detail["status"] == "waiting" and detail["cancellable"] is True
    pending = ok(await engine.client.get(f"/api/v1/executions/{execution_id}/inputs"))
    assert (
        pending["items"][0]["prompt"]
        == "Send this summary? Ada (91) and Bo (77) are hot."
    )
    notifications_before = [
        r for r in engine.server.state.requests if r.path == "/echo"
    ]
    assert len(notifications_before) == 1  # only the fetch: nothing sent yet

    await approve(engine, execution_id, spec.ids["review"], decision="approve")
    assert await engine.next_state(execution_id) == "execution.completed"
    out = await outputs(engine, execution_id)
    assert out["sent"] == {"value": 200}
    final = engine.server.state.requests[-1]
    assert json.loads(final.body) == {"summary": "Ada (91) and Bo (77) are hot."}
    tool_turn = json.loads(engine.server.state.requests[-2].body)["messages"][-1]
    assert json.loads(tool_turn["content"])["rows"] == [
        {"name": "Ada", "score": 91},
        {"name": "Bo", "score": 77},
    ]
    assert_no_secret_escaped(engine)


# -- Workflow B (and the HTTP rules of answering) -------------------------------


async def test_b_webhook_agent_human_database_notification(
    engine: Engine, mailpit: Mailpit
) -> None:
    mailpit.clear()
    await sign_in(engine)
    c = engine.client
    ok(
        await c.post(
            "/api/v1/credentials",
            json={"name": "hook", "kind": "generic", "value": HOOK_SECRET},
        ),
        201,
    )
    trigger_id = new_id("trg")
    spec = Spec(
        {
            "ticket": ("core.input", {}),
            # Validate/normalize: keep the fields the workflow needs, one ticket.
            "clean": (
                "core.transform",
                {
                    "steps": [
                        {"op": "pick", "path": "tickets"},
                        {
                            "op": "map",
                            "fields": {"from": "customer.email", "text": "message"},
                        },
                        {"op": "limit", "count": 1},
                    ]
                },
            ),
            "vars": ("core.set", {"key": "tickets"}),
            "triage": (
                "core.agent",
                MODEL | {"user_prompt": "Draft a reply to: {{ tickets.0.text }}"},
            ),
            "review": (
                "core.human",
                {
                    "prompt": "Send this reply? {{ value }}",
                    "fields": {"priority": {"type": "string", "enum": ["low", "high"]}},
                    "required_fields": ["priority"],
                },
            ),
            "record": (
                "core.transform",
                {"steps": [{"op": "pick", "path": "priority"}]},
            ),
            "params": ("core.set", {"key": "priority"}),
            "store": (
                "core.database",
                {
                    "engine": "sqlite",
                    "database": "tickets.db",
                    "sql": "INSERT INTO tickets (priority) VALUES (:priority)",
                },
            ),
            "subject": (
                "core.transform",
                {
                    "steps": [
                        {
                            "op": "template",
                            "template": "Ticket stored ({{ value }} row)",
                        }
                    ]
                },
            ),
            "mail": (
                "core.smtp",
                {
                    "host": mailpit.smtp_host,
                    "port": mailpit.smtp_port,
                    "security": "none",
                    "username": SMTP_LOGIN[0],
                    "password_credential": "credential:smtp-password",
                    "sender": "engine@example.com",
                    "recipients": ["ops@example.com"],
                },
            ),
        },
        [
            "ticket.value>clean.value",
            "clean.value>vars.value",
            "vars.value>triage.variables",
            "triage.text>review.value",
            "review.response>record.value",
            "record.value>params.value",
            "params.value>store.params",
            "store.row_count>subject.value",
            "subject.value>mail.subject",
            "subject.value>mail.body",
        ],
        triggers=[
            {
                "id": trigger_id,
                "trigger_type": "webhook",
                "config": {"secret_credential": "credential:hook"},
            }
        ],
    )
    workflow_id = await publish(engine, spec, "B")
    await create_table(
        engine, workflow_id, "tickets.db", "CREATE TABLE tickets (priority TEXT)"
    )
    engine.server.state.llm_bodies = [answer("Thanks, we are on it.")]

    body = json.dumps(
        {
            "tickets": [
                {"customer": {"email": "ada@example.com"}, "message": "Invoice wrong"}
            ]
        }
    ).encode()
    hook = await c.post(
        f"/api/v1/webhooks/{workflow_id}/{trigger_id}",
        content=body,
        headers={
            "content-type": "application/json",
            "x-akasha-signature": webhook_signature(HOOK_SECRET, body),
        },
    )
    assert hook.status_code == 202, hook.text
    execution_id = hook.json()["id"]
    engine.queue(execution_id)
    assert await engine.next_state(execution_id) == "execution.waiting"
    review = spec.ids["review"]
    url = f"/api/v1/executions/{execution_id}/inputs/{review}"

    # A viewer of the workflow may look but not answer (server-side check).
    viewer = await member_client(engine, workflow_id, "viewer")
    try:
        assert (
            await viewer.get(f"/api/v1/executions/{execution_id}/inputs")
        ).status_code == 200
        forbidden = await viewer.post(url, json={"response": {"decision": "approve"}})
        assert forbidden.status_code == 403, forbidden.text
    finally:
        await viewer.aclose()
    # A response that does not match the request is refused and changes nothing.
    invalid = await c.post(
        url, json={"response": {"decision": "approve", "priority": "urgent"}}
    )
    assert invalid.status_code == 422, invalid.text
    assert ok(await c.get(f"/api/v1/executions/{execution_id}"))["status"] == "waiting"

    await approve(engine, execution_id, review, decision="approve", priority="high")
    again = await c.post(
        url, json={"response": {"decision": "reject", "priority": "low"}}
    )
    assert again.status_code == 409, again.text  # no second answer
    state = await engine.next_state(execution_id)
    report = ok(await c.get(f"/api/v1/reports/executions/{execution_id}"))
    assert state == "execution.completed", report["execution"]["error"]

    assert sqlite_rows(
        engine, workflow_id, "tickets.db", "SELECT priority FROM tickets"
    ) == [("high",)]
    (message,) = mailpit.messages()
    assert message["Subject"] == "Ticket stored (1 row)"
    out = await outputs(engine, execution_id)
    assert out["review"]["response"]["priority"] == "high"
    assert out["review"]["response"]["responded_by"].startswith("user_")
    assert_no_secret_escaped(engine, json.dumps(out))


async def member_client(
    engine: Engine, workflow_id: str, role: str
) -> httpx.AsyncClient:
    c = engine.client
    user = ok(
        await c.post(
            "/api/v1/users",
            json={"username": f"{role}-user", "password": PASSWORD, "role": "member"},
        ),
        201,
    )
    ok(
        await c.put(
            f"/api/v1/workflows/{workflow_id}/members/{user['username']}",
            json={"role": role},
        )
    )
    token = ok(
        await c.post(
            "/api/v1/auth/login",
            json={"username": f"{role}-user", "password": PASSWORD},
        )
    )["token"]
    return httpx.AsyncClient(
        transport=c._transport,  # the same app
        base_url="http://e2e",
        headers={"Authorization": f"Bearer {token}"},
    )


# -- Workflow C ----------------------------------------------------------------


async def test_c_agent_browser_transform_database(engine: Engine) -> None:
    from tests.nodes.test_browser_node import _chromium_missing

    # The probe uses Playwright's sync API, which refuses a running loop.
    if reason := await asyncio.to_thread(_chromium_missing):
        pytest.skip(reason)
    await sign_in(engine)
    page = f"{engine.server.base_url}/page"  # the loopback HTML page
    spec = Spec(
        {
            "topic": ("core.input", {}),
            "planner": (
                "core.agent",
                MODEL
                | {
                    "user_prompt": "Which page lists {{ topic }}?",
                    "output_schema": {"type": "object"},
                },
            ),
            "url": ("core.pick", {"path": "url"}),
            "browse": (
                "core.browser",
                {
                    "steps": [
                        {
                            "action": "extract",
                            "selector": "li",
                            "name": "items",
                            "all": True,
                        }
                    ]
                },
            ),
            "items": (
                "core.transform",
                {"steps": [{"op": "pick", "path": "items"}, {"op": "to_json"}]},
            ),
            "row": ("core.set", {"key": "items"}),
            "store": (
                "core.database",
                {
                    "engine": "sqlite",
                    "database": "web.db",
                    "sql": "INSERT INTO items (name)"
                    " SELECT value FROM json_each(:items)",
                },
            ),
            "stored": ("core.output", {}),
        },
        [
            "topic.value>planner.variables",
            "planner.structured>url.value",
            "url.value>browse.url",
            "browse.extracted>items.value",
            "items.value>row.value",
            "row.value>store.params",
            "store.row_count>stored.value",
        ],
    )
    workflow_id = await publish(engine, spec, "C")
    await create_table(engine, workflow_id, "web.db", "CREATE TABLE items (name TEXT)")
    engine.server.state.llm_bodies = [answer(json.dumps({"url": page}))]
    execution_id = await run(engine, workflow_id, {"topic": "agent tools"})
    assert await engine.next_state(execution_id) == "execution.completed"
    assert (await outputs(engine, execution_id))["stored"] == {"value": 2}
    assert sqlite_rows(engine, workflow_id, "web.db", "SELECT name FROM items") == [
        ("search",),
        ("fetch",),
    ]


# -- Workflow D ----------------------------------------------------------------


async def test_d_file_csv_database_agent(engine: Engine) -> None:
    await sign_in(engine)
    spec = Spec(
        {
            "path": ("core.input", {"field": "path"}),
            "read": ("core.file_read", {}),
            "rows": (
                "core.transform",
                {
                    "steps": [
                        {"op": "parse_csv"},
                        {
                            "op": "filter",
                            "field": "status",
                            "operator": "equals",
                            "operand": "open",
                        },
                        {"op": "to_json"},
                    ]
                },
            ),
            "row": ("core.set", {"key": "rows"}),
            "store": (
                "core.database",
                {
                    "engine": "sqlite",
                    "database": "orders.db",
                    "sql": "INSERT INTO orders (id, total) SELECT value ->> 'id',"
                    " value ->> 'total' FROM json_each(:rows)",
                },
            ),
            "count": ("core.set", {"key": "open"}),
            "analyst": (
                "core.agent",
                MODEL
                | {
                    "user_prompt": "{{ open }} open orders were imported."
                    " What is the total?",
                    "tools": [
                        {
                            "name": "total",
                            "node_type": "core.database",
                            "config": {
                                "engine": "sqlite",
                                "database": "orders.db",
                                "sql": "SELECT sum(total) AS total FROM orders",
                                "read_only": True,
                            },
                        }
                    ],
                },
            ),
            "answer": ("core.output", {}),
        },
        [
            "path.value>read.path",
            "read.content>rows.value",
            "rows.value>row.value",
            "row.value>store.params",
            "store.row_count>count.value",
            "count.value>analyst.variables",
            "analyst.text>answer.value",
        ],
    )
    workflow_id = await publish(engine, spec, "D")
    directory = engine.workspace / workflow_id
    directory.mkdir(exist_ok=True)
    (directory / "inbox").mkdir()
    (directory / "inbox" / "orders.csv").write_text(
        "id,status,total\n1,open,10\n2,paid,99\n3,open,32\n"
    )
    await create_table(
        engine, workflow_id, "orders.db", "CREATE TABLE orders (id INT, total INT)"
    )
    engine.server.state.llm_bodies = [
        tool_call("c1", "total", {}),
        answer("42 in total."),
    ]

    execution_id = await run(engine, workflow_id, {"path": "inbox/orders.csv"})
    assert await engine.next_state(execution_id) == "execution.completed"
    assert sqlite_rows(
        engine, workflow_id, "orders.db", "SELECT id, total FROM orders"
    ) == [
        (1, 10),
        (3, 32),
    ]
    out = await outputs(engine, execution_id)
    assert out["answer"] == {"value": "42 in total."}
    assert out["analyst"]["steps"][0]["ok"] is True
    # The package export carries the SQL but never a secret.
    exported = await engine.client.get(
        f"/api/v1/workflows/{workflow_id}/export", params={"version": 1}
    )
    assert exported.status_code == 200
    for secret in (LLM_KEY, KEY):
        assert secret.encode() not in exported.content
