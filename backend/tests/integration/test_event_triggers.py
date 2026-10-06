"""EVENT triggers against real PostgreSQL (architecture §129/§130).

Projects are published through the HTTP API, source executions really run
through the ExecutionRuntime, and the dispatcher reads the persisted event
log. Time is a fake clock and ``tick()`` is driven directly: no sleeps
(backend/AGENTS §62/§63). The commit-order gap test holds a real open
transaction, because a gap is a property of PostgreSQL identity columns.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from akasha.application.executions import ExecutionService
from akasha.application.projects import ProjectService
from akasha.application.triggers import TriggerRegistry, TriggerService
from akasha.domain.event import Event, EventType
from akasha.domain.execution import ExecutionStatus
from akasha.domain.identity import Role
from akasha.domain.primitives import new_id
from akasha.infrastructure.database import Database
from akasha.runtime.event_triggers import EventTriggerDispatcher
from akasha.runtime.plan import NodeCatalog
from akasha.runtime.runtime import ExecutionRuntime
from tests.integration.fixtures import make_auth_kit
from tests.integration.test_triggers_api import (
    FakeClock,
    _Sink,
    _Source,
    db_executions,
    draft_with,
    publish_with,
    trigger_client,
    uow_of,
)

pytestmark = pytest.mark.usefixtures("clean_db")

START = datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC)
CATALOG: NodeCatalog = {"test.source": _Source, "test.sink": _Sink}


@pytest.fixture
async def api(
    database: Database, project_service: ProjectService
) -> AsyncIterator[httpx.AsyncClient]:
    async with trigger_client(database, project_service) as client:
        yield client


def dispatcher(
    database: Database, clock: FakeClock, *, max_depth: int = 8
) -> EventTriggerDispatcher:
    return EventTriggerDispatcher(
        lambda: uow_of(database),
        TriggerRegistry(lambda: uow_of(database)),
        TriggerService(ExecutionService(lambda: uow_of(database))),
        max_depth=max_depth,
        clock=clock,
    )


def on_event(event: str = "execution.completed", **config: Any) -> dict[str, Any]:
    return {
        "id": new_id("trig"),
        "trigger_type": "event",
        "config": {"event": event, **config},
    }


async def run_manual(api: httpx.AsyncClient, database: Database, pid: str) -> str:
    """Start version 1 of ``pid`` and run it to its end; returns its id."""
    started = await api.post(f"/api/v1/projects/{pid}/versions/1/execute", json={})
    assert started.status_code == 201, started.text
    runtime = ExecutionRuntime(lambda: uow_of(database), CATALOG)
    execution = await asyncio.wait_for(runtime.run(started.json()["id"]), timeout=30)
    assert execution.status is ExecutionStatus.COMPLETED
    return execution.id


async def event_runs(database: Database) -> list[dict[str, Any]]:
    return [
        r
        for r in await db_executions(database)
        if r["metadata"]["trigger_type"] == "event"
    ]


async def test_completion_of_a_source_project_starts_the_target(
    api: httpx.AsyncClient, database: Database
) -> None:
    source, _, _ = await publish_with(api, [])
    trigger = on_event(source_project_id=source)
    target, _, _ = await publish_with(api, [trigger])
    events = dispatcher(database, FakeClock(START))
    assert await events.tick() == 0  # anchors at the end of the log

    source_run = await run_manual(api, database, source)
    assert await events.tick() == 1
    assert await events.tick() == 0  # each event fires once

    [run] = await event_runs(database)
    assert run["status"] == "queued"
    assert run["metadata"]["trigger_id"] == trigger["id"]
    assert run["metadata"]["trigger_depth"] == 1
    fired_by = run["input"]["event"]
    assert fired_by["type"] == "execution.completed"
    assert fired_by["project_id"] == source
    assert fired_by["execution_id"] == source_run
    assert fired_by["project_version"] == 1
    assert fired_by["data"] == {}
    detail = await api.get(f"/api/v1/projects/{target}/executions")
    assert [e["trigger_type"] for e in detail.json()["items"]] == ["event"]


async def test_history_before_the_first_tick_is_not_replayed(
    api: httpx.AsyncClient, database: Database
) -> None:
    source, _, _ = await publish_with(api, [on_event()])
    await run_manual(api, database, source)  # the self-trigger's own event
    events = dispatcher(database, FakeClock(START))

    assert await events.tick() == 0
    assert await events.tick() == 0
    assert await event_runs(database) == []


async def test_a_self_triggering_project_stops_at_the_depth_limit(
    api: httpx.AsyncClient, database: Database
) -> None:
    pid, _, _ = await publish_with(api, [on_event()])
    events = dispatcher(database, FakeClock(START), max_depth=3)
    await events.tick()
    runtime = ExecutionRuntime(lambda: uow_of(database), CATALOG)

    await run_manual(api, database, pid)  # depth 0
    depths = []
    for _ in range(5):
        if await events.tick() == 0:
            break
        queued = [r for r in await event_runs(database) if r["status"] == "queued"]
        assert len(queued) == 1
        depths.append(queued[0]["metadata"]["trigger_depth"])
        async with uow_of(database) as uow:
            [execution_id] = await uow.executions.list_queued(
                created_before=datetime.now(UTC) + timedelta(hours=1), limit=1
            )
        await asyncio.wait_for(runtime.run(execution_id), timeout=30)

    assert depths == [1, 2, 3]  # the 4th hop is refused
    assert len(await event_runs(database)) == 3


async def test_only_matching_enabled_triggers_fire(
    api: httpx.AsyncClient, database: Database
) -> None:
    source, _, _ = await publish_with(api, [])
    on_failure = on_event("execution.failed", source_project_id=source)
    disabled = {**on_event(source_project_id=source), "enabled": False}
    await publish_with(api, [on_failure, disabled])
    events = dispatcher(database, FakeClock(START))
    await events.tick()

    await run_manual(api, database, source)  # completes; nothing listens
    assert await events.tick() == 0
    assert await event_runs(database) == []


async def test_publishing_is_an_event_too(
    api: httpx.AsyncClient, database: Database
) -> None:
    source, _, revision = await publish_with(api, [])
    trigger = on_event("project.published", source_project_id=source)
    await publish_with(api, [trigger])
    events = dispatcher(database, FakeClock(START))
    await events.tick()

    await publish_with(api, [], project_id=source, revision=revision)
    assert await events.tick() == 1
    [run] = await event_runs(database)
    assert run["input"]["event"]["type"] == "project.published"
    assert run["input"]["event"]["project_version"] == 2
    assert run["metadata"]["trigger_depth"] == 1


async def publish_as(
    api: httpx.AsyncClient, headers: dict[str, str], triggers: list[dict[str, Any]]
) -> str:
    """A project owned by the user of ``headers``, published as v1."""
    created = await api.post("/api/v1/projects", json={"name": "o"}, headers=headers)
    pid: str = created.json()["id"]
    put = await api.put(
        f"/api/v1/projects/{pid}",
        headers=headers,
        json={"expected_revision": 1, "name": "o", "draft": draft_with(triggers)},
    )
    assert put.status_code == 200, put.text
    published = await api.post(f"/api/v1/projects/{pid}/publish", headers=headers)
    assert published.status_code == 201, published.text
    return pid


async def test_a_member_cannot_point_a_trigger_at_another_users_project(
    api: httpx.AsyncClient, database: Database
) -> None:
    source, _, _ = await publish_with(api, [])  # owned by the admin
    kit = make_auth_kit(database)
    member = await kit.member_headers(await kit.login("admin"), "mallory")
    created = await api.post("/api/v1/projects", json={"name": "o"}, headers=member)
    pid = created.json()["id"]
    put = await api.put(
        f"/api/v1/projects/{pid}",
        headers=member,
        json={
            "expected_revision": 1,
            "name": "o",
            "draft": draft_with([on_event(source_project_id=source)]),
        },
    )
    assert put.status_code == 422, put.text
    assert put.json()["code"] == "TRIGGER_SOURCE_NOT_FOUND"


async def test_a_trigger_stops_when_its_owner_loses_the_source(
    api: httpx.AsyncClient, database: Database
) -> None:
    """Shared as viewer, a member may react to the source; once the share
    is removed, the published trigger no longer fires (checked at fire
    time, not only at save)."""
    source, _, _ = await publish_with(api, [])  # owned by the admin
    kit = make_auth_kit(database)
    member = await kit.member_headers(await kit.login("admin"), "mallory")
    share = f"/api/v1/projects/{source}/members/mallory"
    assert (await api.put(share, json={"role": "viewer"})).status_code == 200
    await publish_as(api, member, [on_event(source_project_id=source)])
    events = dispatcher(database, FakeClock(START))
    await events.tick()

    await run_manual(api, database, source)
    assert await events.tick() == 1
    assert len(await event_runs(database)) == 1

    assert (await api.delete(share)).status_code == 204
    await run_manual(api, database, source)
    assert await events.tick() == 0
    assert len(await event_runs(database)) == 1


async def test_an_admin_reacts_to_any_project_until_disabled(
    api: httpx.AsyncClient, database: Database
) -> None:
    kit = make_auth_kit(database)
    admin = await kit.login("admin")
    member = await kit.member_headers(admin, "bob")
    ops = await kit.member_headers(admin, "ops", role=Role.ADMIN)
    source = await publish_as(api, member, [])  # someone else's project
    target = await publish_as(api, ops, [on_event(source_project_id=source)])
    events = dispatcher(database, FakeClock(START))
    await events.tick()

    await run_manual(api, database, source)
    assert await events.tick() == 1  # same rule as the API: admins see all
    [run] = await event_runs(database)
    detail = await api.get(f"/api/v1/projects/{target}/executions", headers=ops)
    assert [e["trigger_type"] for e in detail.json()["items"]] == ["event"]
    assert run["input"]["event"]["project_id"] == source

    # Checked per fire: the disabled admin's trigger stops reacting.
    identity = await kit.auth.authenticate(admin["Authorization"][7:])
    async with uow_of(database) as uow:
        users = await uow.users.list(offset=0, limit=10)
    [ops_user] = [u for u in users if u.username == "ops"]
    await kit.users.update_user(identity, ops_user.id, disabled=True)
    await run_manual(api, database, source)
    assert await events.tick() == 0
    assert len(await event_runs(database)) == 1


async def test_an_event_committed_after_a_later_one_is_still_dispatched(
    api: httpx.AsyncClient, database: Database
) -> None:
    pid, _, _ = await publish_with(api, [on_event("project.published")])
    events = dispatcher(database, FakeClock(START))
    await events.tick()

    def published() -> Event:
        return Event(EventType.PROJECT_PUBLISHED, START, pid, project_version=1)

    slow = uow_of(database)
    await slow.__aenter__()
    try:
        await slow.events.append([published()])  # position N, not committed
        async with uow_of(database) as fast:
            await fast.events.append([published()])  # N+1, committed first
            await fast.commit()
        assert await events.tick() == 1  # N+1; N is remembered as a gap
        await slow.commit()
    finally:
        await slow.__aexit__(None, None, None)
    assert await events.tick() == 1  # N, read again once committed
    assert await events.tick() == 0
    assert len(await event_runs(database)) == 2


async def test_a_rolled_back_gap_is_given_up_after_the_timeout(
    api: httpx.AsyncClient, database: Database
) -> None:
    pid, _, _ = await publish_with(api, [on_event("project.published")])
    clock = FakeClock(START)
    events = dispatcher(database, clock)
    await events.tick()

    async with uow_of(database) as rolled_back:
        await rolled_back.events.append(
            [Event(EventType.PROJECT_PUBLISHED, START, pid, project_version=1)]
        )
        async with uow_of(database) as fast:
            await fast.events.append(
                [Event(EventType.PROJECT_PUBLISHED, START, pid, project_version=1)]
            )
            await fast.commit()
        assert await events.tick() == 1
        # Leaving without commit rolls back: the position never commits.
    assert events._gaps
    clock.advance(seconds=61)
    assert await events.tick() == 0
    assert events._gaps == {}


async def test_validation_rejects_events_that_never_fire(
    api: httpx.AsyncClient,
) -> None:
    created = await api.post("/api/v1/projects", json={"name": "v"})
    pid = created.json()["id"]
    bad_event = on_event("node.completed")
    bad_source = on_event(source_project_id="not-a-project")
    # An unknown source is refused at save, like one the caller cannot see
    # (no existence probing); validation still catches a source deleted
    # after the trigger was saved.
    refused = await api.put(
        f"/api/v1/projects/{pid}",
        json={
            "expected_revision": 1,
            "name": "v",
            "draft": {
                "graph": {"nodes": [], "edges": []},
                "triggers": [bad_source],
                "dependencies": [],
            },
        },
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "TRIGGER_SOURCE_NOT_FOUND"
    put = await api.put(
        f"/api/v1/projects/{pid}",
        json={
            "expected_revision": 1,
            "name": "v",
            "draft": {
                "graph": {"nodes": [], "edges": []},
                "triggers": [bad_event, on_event()],
                "dependencies": [],
            },
        },
    )
    assert put.status_code == 200, put.text
    result = (await api.post(f"/api/v1/projects/{pid}/validate")).json()
    issues = {
        i["trigger_id"]: i
        for i in result["issues"]
        if i["code"] == "invalid_trigger_event"
    }
    assert set(issues) == {bad_event["id"]}
    assert "node.completed" not in issues[bad_event["id"]]["message"]
