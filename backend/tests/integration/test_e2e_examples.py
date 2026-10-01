"""Backend V1 end to end: the official example projects (vision §58,
architecture §88) through the production composition root (Phase 27).

Nothing between the HTTP client and the infrastructure is replaced:
``create_container`` wires the engine from ``Settings`` exactly as
``akasha serve`` does, and the app's own lifespan starts and stops it.

    HTTP API → ProjectService → PostgreSQL
             → TriggerService → ExecutionService → Redis queue
             → in-process Worker → ExecutionRuntime → built-in nodes
                 → core.llm → AIService → LiteLLM → loopback OpenAI-compatible server
                 → core.web_search → loopback SearXNG-shaped server
                 → core.smtp → Mailpit (container)
             → events / logs / metrics / report → export → import → run again

The only stand-ins are the external services (an LLM provider, a search
engine, a mail server): local, deterministic, no public internet (§124).
Waiting is event-driven: the test subscribes to the engine's own event
publisher, never sleeps.

Per example, the journey is: create project → configure nodes for this
installation (provider endpoint, SMTP host) → validate → publish → the
version is immutable → trigger (manual invoke, or the cron scheduler on an
injected clock) → worker runs it → inspect state, events, logs, metrics,
report → export → import → validate → publish → execute the imported copy.
"""

import asyncio
import base64
import io
import json
import os
import zipfile
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from akasha.api.app import create_app
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.event import Event, EventType
from akasha.domain.primitives import new_id
from akasha.runtime.scheduler import TriggerScheduler
from tests.integration.test_llm_node_runtime import answer
from tests.support import (
    SMTP_LOGIN,
    LocalHttpServer,
    Mailpit,
    Recorded,
    capture_json_logs,
    run_mailpit,
    serve_http,
)

pytestmark = pytest.mark.usefixtures("clean_db")

EXAMPLES = Path(__file__).parents[2] / "examples" / "projects"
KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
# Secrets that must never leave the credential store in plaintext.
LLM_KEY = "sk-e2e-llm-0123456789abcdefghijklmnop"
SMTP_PASSWORD = SMTP_LOGIN[1]
PASSWORD = "correct horse battery staple"
GUARD = 30  # seconds: a deadlock fails the test instead of hanging it
TERMINAL = {
    EventType.EXECUTION_COMPLETED,
    EventType.EXECUTION_FAILED,
    EventType.EXECUTION_CANCELLED,
    EventType.EXECUTION_TIMED_OUT,
}
SEARCH_RESULTS = {
    "results": [
        {
            "title": "Acme Automation",
            "url": "https://acme.example/jobs",
            "content": "Acme is hiring workflow engineers.",
        },
        {"title": "no url", "content": "dropped by normalization"},
    ]
}


@dataclass
class Engine:
    client: httpx.AsyncClient
    container: Container
    server: LocalHttpServer
    mailpit: Mailpit
    logs: list[dict[str, Any]]
    finished: dict[str, asyncio.Future[str]]

    def watch(self, execution_id: str) -> asyncio.Future[str]:
        return self.finished.setdefault(
            execution_id, asyncio.get_running_loop().create_future()
        )

    async def wait(self, execution_id: str) -> str:
        """The terminal event type of the execution, as the worker
        published it."""
        async with asyncio.timeout(GUARD):
            return await self.watch(execution_id)


@pytest.fixture
async def engine(db_url: str, redis_url: str) -> AsyncIterator[Engine]:
    with (
        serve_http() as server,
        run_mailpit() as mailpit,
        capture_json_logs() as logs,
    ):
        server.state.search_body = json.dumps(SEARCH_RESULTS).encode()
        settings = Settings(
            environment="test",
            database_url=db_url,
            redis_url=SecretStr(redis_url),
            queue_prefix=new_id("e2e")[-32:],  # isolated from other tests' queues
            credential_encryption_key=SecretStr(KEY),
            openai_compatible_base_url=AnyHttpUrl(f"{server.base_url}/v1"),
            openai_compatible_name="local",
            web_search_url=AnyHttpUrl(server.base_url),
            # Mailpit and the loopback servers live on private addresses.
            network_allow_private=True,
            # The test drives cron with its own scheduler on a fake clock.
            scheduler_enabled=False,
            worker_poll_seconds=0.05,
        )
        app = create_app(settings=settings)
        container: Container = app.state.container
        finished: dict[str, asyncio.Future[str]] = {}

        async def on_terminal(event: Event) -> None:
            assert event.execution_id is not None
            future = finished.setdefault(
                event.execution_id, asyncio.get_running_loop().create_future()
            )
            if not future.done():
                future.set_result(event.event_type.value)

        container.events.subscribe(on_terminal, event_types=TERMINAL)
        async with AsyncExitStack() as stack:
            # The app's own lifespan: starts the worker, stops it gracefully.
            await stack.enter_async_context(app.router.lifespan_context(app))
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://e2e"
                )
            )
            yield Engine(client, container, server, mailpit, logs, finished)


# -- helpers -------------------------------------------------------------------


def ok(response: httpx.Response, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def example(name: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_text("utf-8"))
    return doc


def configure_for_installation(
    graph: dict[str, Any], mailpit: Mailpit
) -> dict[str, Any]:
    """What an operator edits after loading an example: the LLM provider
    and model this installation offers, and its SMTP server."""
    for node in graph["nodes"]:
        if node["node_type"] == "core.llm":
            node["config"] |= {"provider": "local", "model": "e2e-model"}
        if node["node_type"] == "core.smtp":
            node["config"] |= {
                "host": mailpit.smtp_host,
                "port": mailpit.smtp_port,
                "security": "none",
                "username": SMTP_LOGIN[0],
            }
    return graph


async def login(engine: Engine) -> None:
    c = engine.client
    ok(
        await c.post(
            "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
        ),
        201,
    )
    token = ok(
        await c.post(
            "/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}
        )
    )["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    for name, kind, value in (
        ("openai", "provider", LLM_KEY),
        ("smtp-password", "smtp", SMTP_PASSWORD),
    ):
        ok(
            await c.post(
                "/api/v1/credentials", json={"name": name, "kind": kind, "value": value}
            ),
            201,
        )


async def create_and_publish(engine: Engine, name: str) -> tuple[str, dict[str, Any]]:
    """Create → configure → validate → publish; returns (project id, the
    draft as published)."""
    c, spec = engine.client, example(name)
    project = ok(
        await c.post(
            "/api/v1/projects",
            json={"name": spec["name"], "description": spec["description"]},
        ),
        201,
    )
    draft = {
        "graph": configure_for_installation(spec["graph"], engine.mailpit),
        "triggers": spec["triggers"],
        "dependencies": spec["dependencies"],
    }
    updated = ok(
        await c.put(
            f"/api/v1/projects/{project['id']}",
            json={
                "expected_revision": project["revision"],
                "name": spec["name"],
                "description": spec["description"],
                "draft": draft,
            },
        )
    )
    validation = ok(await c.post(f"/api/v1/projects/{project['id']}/validate"))
    assert validation == {"valid": True, "issues": []}, validation
    published = ok(
        await c.post(
            f"/api/v1/projects/{project['id']}/publish",
            json={"expected_revision": updated["revision"]},
        ),
        201,
    )
    assert published["version"] == 1
    return project["id"], draft


async def assert_version_is_immutable(engine: Engine, project_id: str) -> None:
    """Editing the draft after publishing never changes version 1."""
    c = engine.client
    before = ok(await c.get(f"/api/v1/projects/{project_id}/versions/1"))
    project = ok(await c.get(f"/api/v1/projects/{project_id}"))
    ok(
        await c.put(
            f"/api/v1/projects/{project_id}",
            json={
                "expected_revision": project["revision"],
                "name": project["name"] + " (edited)",
                "description": "",
                "draft": {"graph": {"nodes": [], "edges": []}},
            },
        )
    )
    after = ok(await c.get(f"/api/v1/projects/{project_id}/versions/1"))
    assert after == before


async def invoke(
    engine: Engine, project_id: str, trigger_id: str, payload: dict[str, Any]
) -> str:
    created = ok(
        await engine.client.post(
            f"/api/v1/projects/{project_id}/triggers/{trigger_id}/invoke",
            json=payload,
        ),
        201,
    )
    # HTTP returned before the run: the execution is queued for a worker.
    assert created["status"] == "queued"
    engine.watch(created["id"])
    return str(created["id"])


async def report(engine: Engine, execution_id: str) -> dict[str, Any]:
    doc: dict[str, Any] = ok(
        await engine.client.get(f"/api/v1/reports/executions/{execution_id}")
    )
    return doc


def requests_to(server: LocalHttpServer, path: str) -> list[Recorded]:
    return [r for r in server.state.requests if urlsplit(r.path).path == path]


def outputs_by_label(doc: dict[str, Any]) -> dict[str, Any]:
    return {n["label"]: n["outputs"] for n in doc["nodes"]}


async def inspect_completed(
    engine: Engine, project_id: str, execution_id: str
) -> dict[str, Any]:
    """Execution state, listing, event history and report of a finished run."""
    c = engine.client
    assert await engine.wait(execution_id) == "execution.completed"
    detail = ok(await c.get(f"/api/v1/executions/{execution_id}"))
    assert detail["status"] == "completed"
    assert detail["finished_at"] is not None
    listed = ok(await c.get(f"/api/v1/projects/{project_id}/executions"))
    assert execution_id in [e["id"] for e in listed["items"]]
    events = ok(await c.get(f"/api/v1/executions/{execution_id}/events"))
    types = [e["event_type"] for e in events["items"]]
    assert types[0] == "execution.created"
    assert types[-1] == "execution.completed"
    assert "node.started" in types and "node.completed" in types
    doc = await report(engine, execution_id)
    assert doc["execution"]["status"] == "completed"
    assert doc["events"], "the report carries the timeline"
    return doc


async def export_import_and_rerun(
    engine: Engine, project_id: str, run_input: dict[str, Any]
) -> dict[str, Any]:
    """Export version 1 → import → validate → publish → execute the copy;
    returns the copy's report."""
    c = engine.client
    exported = await c.get(
        f"/api/v1/projects/{project_id}/export", params={"version": 1}
    )
    assert exported.status_code == 200, exported.text
    package = exported.content
    for secret in (LLM_KEY, SMTP_PASSWORD, KEY):
        assert secret.encode() not in package
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["source_project_version"] == 1

    imported = ok(
        await c.post(
            "/api/v1/projects/import",
            content=package,
            headers={"content-type": "application/zip"},
        ),
        201,
    )
    assert imported["validation"] == {"valid": True, "issues": []}
    assert imported["missing_credentials"] == []
    copy = imported["project"]
    assert copy["id"] != project_id
    ok(
        await c.post(
            f"/api/v1/projects/{copy['id']}/publish",
            json={"expected_revision": copy["revision"]},
        ),
        201,
    )
    created = ok(
        await c.post(
            f"/api/v1/projects/{copy['id']}/versions/1/execute", json=run_input
        ),
        201,
    )
    engine.watch(created["id"])
    return await inspect_completed(engine, copy["id"], created["id"])


def assert_no_secret_escaped(engine: Engine, *texts: str) -> None:
    logged = json.dumps(engine.logs)
    for secret in (LLM_KEY, SMTP_PASSWORD, KEY):
        assert secret not in logged
        for text in texts:
            assert secret not in text


# -- the examples ----------------------------------------------------------------


async def test_hello_world(engine: Engine) -> None:
    await login(engine)
    project_id, draft = await create_and_publish(engine, "hello-world")
    await assert_version_is_immutable(engine, project_id)

    execution_id = await invoke(
        engine, project_id, draft["triggers"][0]["id"], {"name": "World"}
    )

    doc = await inspect_completed(engine, project_id, execution_id)
    expected = {"value": {"greeting": "Hello", "name": "World"}}
    assert outputs_by_label(doc)["result"] == expected
    assert doc["execution"]["node_versions"]["core.set"] == "1.0.0"

    # Structured logs: the worker's lines carry the execution id.
    worker_lines = [
        line for line in engine.logs if line.get("execution_id") == execution_id
    ]
    assert {"timestamp", "level", "message"} <= set(worker_lines[0])

    # Metrics: the admin session can scrape them.
    metrics = await engine.client.get("/metrics")
    assert metrics.status_code == 200
    assert "akasha_executions_finished" in metrics.text

    copy = await export_import_and_rerun(engine, project_id, {"name": "Copy"})
    assert outputs_by_label(copy)["result"] == {
        "value": {"greeting": "Hello", "name": "Copy"}
    }
    assert_no_secret_escaped(engine)


async def test_llm(engine: Engine) -> None:
    await login(engine)
    engine.server.state.llm_body = answer("Paris.")
    project_id, draft = await create_and_publish(engine, "llm")

    execution_id = await invoke(
        engine,
        project_id,
        draft["triggers"][0]["id"],
        {"question": "What is the capital of France?"},
    )

    doc = await inspect_completed(engine, project_id, execution_id)
    assert outputs_by_label(doc)["result"] == {"value": "Paris."}
    llm = next(n for n in doc["nodes"] if n["node_type"] == "core.llm")
    assert llm["usage"] == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
    assert llm["outputs"]["metadata"]["provider"] == "local"
    # A custom endpoint has no list price: no estimate rather than a wrong one,
    # and the report says the cost total is incomplete.
    assert llm["cost"] is None
    assert doc["metrics"]["usage"]["total_tokens"] == 15
    assert doc["metrics"]["cost_complete"] is False

    # The provider got the rendered prompt and the key, in its header only.
    (request,) = requests_to(engine.server, "/v1/chat/completions")
    assert json.loads(request.body)["messages"][-1]["content"] == (
        "What is the capital of France?"
    )
    assert request.headers["authorization"] == f"Bearer {LLM_KEY}"

    await export_import_and_rerun(engine, project_id, {"question": "Again?"})
    assert_no_secret_escaped(engine, json.dumps(doc))


async def test_web_research(engine: Engine) -> None:
    await login(engine)
    engine.server.state.llm_body = answer(
        "- Acme is hiring (https://acme.example/jobs)"
    )
    project_id, draft = await create_and_publish(engine, "web-research")

    execution_id = await invoke(
        engine, project_id, draft["triggers"][0]["id"], {"topic": "workflow engines"}
    )

    doc = await inspect_completed(engine, project_id, execution_id)
    outputs = outputs_by_label(doc)
    assert outputs["brief"] == {"value": "- Acme is hiring (https://acme.example/jobs)"}
    assert outputs["search"]["results"] == [
        {
            "title": "Acme Automation",
            "url": "https://acme.example/jobs",
            "snippet": "Acme is hiring workflow engineers.",
        }
    ]
    (search,) = requests_to(engine.server, "/search")
    assert "q=workflow+engines" in search.path
    (llm_request,) = requests_to(engine.server, "/v1/chat/completions")
    prompt = json.loads(llm_request.body)["messages"][-1]["content"]
    assert "https://acme.example/jobs" in prompt

    await export_import_and_rerun(engine, project_id, {"topic": "again"})
    assert_no_secret_escaped(engine, json.dumps(doc))


async def test_lead_generation(engine: Engine) -> None:
    await login(engine)
    project_id, draft = await create_and_publish(engine, "lead-generation")
    container = engine.container
    assert container.trigger_registry is not None and container.triggers is not None
    # Monday 2026-10-05 07:59 UTC, one minute before the cron boundary.
    now = datetime(2026, 10, 5, 7, 59, tzinfo=UTC)
    scheduler = TriggerScheduler(
        container.trigger_registry, container.triggers, clock=lambda: now
    )
    assert await scheduler.tick() == 0  # arms the schedule, never fires late

    # A qualified batch: score ≥ 7 sends the e-mail.
    engine.server.state.llm_body = answer(
        json.dumps({"score": 8, "subject": "2 new leads", "summary": "Acme is hiring."})
    )
    now += timedelta(minutes=1)
    assert await scheduler.tick() == 1
    listed = ok(await engine.client.get(f"/api/v1/projects/{project_id}/executions"))
    (scheduled,) = listed["items"]
    assert (scheduled["trigger_type"], scheduled["trigger_id"]) == (
        "cron",
        draft["triggers"][0]["id"],
    )
    engine.watch(scheduled["id"])
    doc = await inspect_completed(engine, project_id, scheduled["id"])
    status = {n["label"]: n["status"] for n in doc["nodes"]}
    assert status["e-mail"] == "completed"
    assert status["below threshold"] == "skipped"  # the untaken branch
    (mail,) = engine.mailpit.messages()
    assert mail["Subject"] == "2 new leads"
    body = engine.mailpit.message(mail["ID"])["Text"]
    assert body.replace("\r\n", "\n").strip() == "Acme is hiring."

    # A weak batch a week later: the score gate routes to the log branch.
    engine.mailpit.clear()
    engine.server.state.llm_body = answer(
        json.dumps({"score": 3, "subject": "-", "summary": "nothing"})
    )
    now += timedelta(days=7)
    assert await scheduler.tick() == 1
    listed = ok(await engine.client.get(f"/api/v1/projects/{project_id}/executions"))
    weak = listed["items"][0]["id"]  # newest first
    engine.watch(weak)
    doc = await inspect_completed(engine, project_id, weak)
    status = {n["label"]: n["status"] for n in doc["nodes"]}
    assert (status["e-mail"], status["below threshold"]) == ("skipped", "completed")
    assert engine.mailpit.messages() == []

    engine.server.state.llm_body = answer(
        json.dumps({"score": 9, "subject": "imported", "summary": "copy works"})
    )
    await export_import_and_rerun(engine, project_id, {})
    (mail,) = engine.mailpit.messages()
    assert mail["Subject"] == "imported"
    assert_no_secret_escaped(engine, json.dumps(doc))
