"""Monitoring summary against real PostgreSQL (frontend Phase 16).

Executions are persisted through the real domain transitions (the report
tests' scenario: source -> core.llm -> sink), then aggregated by the SQL the
repository runs, including the node type lookup in the pinned version's
graph JSON, and served through the HTTP layer with its owner filter.
"""

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest

from akasha.api.app import create_app
from akasha.application.monitoring import MonitoringService
from akasha.application.reports import ReportService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.execution import ExecutionError, ExecutionStatus, RetryPolicy
from akasha.domain.node import NodeErrorKind
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from tests.integration.fixtures import AuthKit, UowFactory
from tests.integration.test_reports import T0, Scenario, at, llm_outputs

pytestmark = pytest.mark.usefixtures("clean_db")

WINDOW = {"since": T0.isoformat(), "until": (T0 + timedelta(hours=1)).isoformat()}
URL = "/api/v1/monitoring/summary"
Env = tuple[httpx.AsyncClient, AuthKit, dict[str, str]]


@pytest.fixture
async def scenario(uow: UowFactory) -> Scenario:
    s = Scenario(uow)
    await s.publish()
    return s


@pytest.fixture
async def env(database: Database, auth_kit: AuthKit) -> AsyncIterator[Env]:
    settings = Settings(environment="test", log_level="CRITICAL")
    container = Container(
        settings=settings,
        database=database,
        monitoring=MonitoringService(lambda: SqlAlchemyUnitOfWork(database)),
        **auth_kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    admin = await auth_kit.admin_headers()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=admin
    ) as client:
        yield client, auth_kit, admin


async def completed_run(scenario: Scenario, *, retried: bool = False) -> str:
    execution = scenario.execution(retry_policy=RetryPolicy(max_attempts=3))
    scenario.run_node(execution, scenario.source.id, 2, {"out": "x"})
    execution.mark_node_ready(scenario.llm.id)
    execution.start_node(scenario.llm.id, now=at(4))
    if retried:
        transient = ExecutionError(
            code="LLM_RATE_LIMITED",
            message="rate limited",
            kind=NodeErrorKind.TRANSIENT,
            node_id=scenario.llm.id,
        )
        backoff = execution.retry_node(scenario.llm.id, transient, now=at(5))
        execution.start_node(scenario.llm.id, now=at(5) + backoff)
    cost = {"amount": 0.5, "currency": "USD"}
    execution.complete_node(scenario.llm.id, outputs=llm_outputs(cost=cost), now=at(9))
    scenario.run_node(execution, scenario.sink.id, 10, {})
    execution.complete(now=at(12))
    await scenario.persist(execution)
    return execution.id


async def failed_run(scenario: Scenario) -> str:
    execution = scenario.execution()
    scenario.run_node(execution, scenario.source.id, 2, {"out": "x"})
    execution.mark_node_ready(scenario.llm.id)
    execution.start_node(scenario.llm.id, now=at(4))
    error = ExecutionError(
        code="LLM_PROVIDER_ERROR",
        message="provider rejected the request",
        kind=NodeErrorKind.PERMANENT,
        node_id=scenario.llm.id,
    )
    execution.fail_node(scenario.llm.id, error, now=at(5))
    execution.fail(error, now=at(6))
    await scenario.persist(execution)
    return execution.id


async def test_summary_aggregates_persisted_runs(
    scenario: Scenario, database: Database
) -> None:
    await completed_run(scenario)
    await completed_run(scenario, retried=True)
    await failed_run(scenario)
    service = MonitoringService(lambda: SqlAlchemyUnitOfWork(database))

    s = await service.summary(since=T0, until=T0 + timedelta(hours=1))

    assert s.executions == 3
    assert (
        s.status_counts[ExecutionStatus.COMPLETED],
        s.status_counts[ExecutionStatus.FAILED],
    ) == (2, 1)
    assert (
        s.durations[ExecutionStatus.COMPLETED].max_ms == 11_000
    )  # started at 1, done at 12
    assert s.durations[ExecutionStatus.FAILED].count == 1
    assert s.timeline[0].counts[ExecutionStatus.COMPLETED] == 2  # all created at T0
    assert [(e.code, e.kind, e.count) for e in s.errors] == [
        ("LLM_PROVIDER_ERROR", "permanent", 1)
    ]
    # Node types come from the version graph through SQL JSON path lookup.
    by_type = {n.node_type: n for n in s.nodes}
    assert set(by_type) == {"core.llm", "test.emit", "test.sink"}
    llm = by_type["core.llm"].metrics
    assert llm.node_status_counts["completed"] == 2
    assert llm.node_status_counts["failed"] == 1
    assert (llm.attempts, llm.retries, llm.failed_attempts) == (4, 1, 2)
    assert llm.usage.total_tokens == 84
    assert [(c.amount, c.currency) for c in llm.costs] == [(1.0, "USD")]
    assert llm.cost_complete
    # The failed run cancelled its sink before it started: no duration.
    assert by_type["test.sink"].metrics.node_status_counts["cancelled"] == 1
    assert by_type["test.sink"].durations is not None
    assert by_type["test.sink"].durations.count == 2
    assert s.totals.retries == 1 and s.totals.usage.total_tokens == 84


async def test_window_bounds_are_half_open(
    scenario: Scenario, database: Database
) -> None:
    await completed_run(scenario)  # created exactly at T0
    service = MonitoringService(lambda: SqlAlchemyUnitOfWork(database))

    before = await service.summary(since=T0 - timedelta(hours=1), until=T0)
    after = await service.summary(since=T0, until=T0 + timedelta(minutes=1))

    assert (before.executions, before.nodes) == (0, [])
    assert after.executions == 1 and len(after.nodes) == 3


async def test_report_and_monitoring_agree(
    scenario: Scenario, database: Database
) -> None:
    run_id = await completed_run(scenario, retried=True)

    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    report = await ReportService(uow).execution_report(run_id)
    summary = await MonitoringService(uow).summary(
        since=T0, until=T0 + timedelta(hours=1)
    )
    assert summary.totals == report.metrics


async def test_api_serves_the_summary(
    scenario: Scenario,
    env: Env,
) -> None:
    client, *_ = env
    empty = await client.get(URL, params=WINDOW)
    assert empty.status_code == 200, empty.text
    assert empty.json()["executions"] == 0
    assert empty.json()["bucket_seconds"] == 60

    await failed_run(scenario)
    body = (await client.get(URL, params=WINDOW)).json()

    assert body["executions"] == 1
    assert body["status_counts"]["failed"] == 1
    assert body["errors"][0]["code"] == "LLM_PROVIDER_ERROR"
    assert body["errors_total"] == 1
    assert len(body["timeline"]) == 60
    # Never the error message or outputs: counts, codes and timings only.
    assert "provider rejected" not in empty.text + str(body)


async def test_members_see_only_their_workflows(
    scenario: Scenario,
    env: Env,
) -> None:
    client, kit, admin = env
    await completed_run(scenario)  # a workflow without this member as owner
    member_headers = await kit.member_headers(admin, "member")
    mine = (await client.get(URL, params=WINDOW, headers=member_headers)).json()
    assert mine["executions"] == 0 and mine["nodes"] == []
    assert (await client.get(URL, params=WINDOW)).json()["executions"] == 1


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({}, 422),  # since is required
        ({"since": "yesterday"}, 422),
        ({"since": "2100-01-01T00:00:00"}, 422),  # no timezone
        ({"since": WINDOW["until"], "until": WINDOW["since"]}, 422),
        ({"since": "2100-01-01T00:00:00Z", "until": "2100-02-02T00:00:00Z"}, 422),
    ],
)
async def test_invalid_windows_are_rejected(
    env: Env,
    params: dict[str, str],
    status: int,
) -> None:
    client, *_ = env
    response = await client.get(URL, params=params)
    assert response.status_code == status, response.text
    assert "code" in response.json()


async def test_summary_requires_a_session(
    env: Env,
) -> None:
    client, *_ = env
    response = await client.get(URL, params=WINDOW, headers={"Authorization": ""})
    assert response.status_code == 401
