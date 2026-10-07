"""Credential Store end-to-end against real PostgreSQL (Phase 13).

Service → Unit of Work → PostgreSQL; API through the real HTTP stack; the
runtime resolving credentials for built-in nodes against a local HTTP server
and Mailpit. Every test hunts for the plaintext canary in the places it
must never reach: rows, API bodies, logs, events, outputs.
"""

import json
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from akasha.api.app import create_app
from akasha.application.credentials import (
    CredentialAlreadyExistsError,
    CredentialNotFoundError,
    CredentialService,
)
from akasha.application.executions import ExecutionService
from akasha.application.workflows import WorkflowService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.credentials import CredentialKind, Secret
from akasha.domain.execution import Execution, ExecutionStatus
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import NodeServices
from akasha.domain.workflow import WorkflowDraft
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.nodes.builtins import create_builtin_registry
from akasha.nodes.registry import NodeRegistry
from akasha.runtime.runtime import ExecutionRuntime
from akasha.security.crypto import SecretCipher, SecretDecryptionError
from akasha.security.network import NetworkPolicy
from akasha.security.redaction import MASK
from tests.integration.fixtures import AuthKit
from tests.support import (
    SMTP_LOGIN,
    LocalHttpServer,
    Mailpit,
    capture_json_logs,
    docker_available,
    run_mailpit,
    serve_http,
)

pytestmark = pytest.mark.usefixtures("clean_db")

CANARY = "canary-7f3a9c-never-leak"  # shapeless: only known-value masking helps
KEY = os.urandom(32)


def uow_factory(database: Database) -> Any:
    return lambda: SqlAlchemyUnitOfWork(database)


@pytest.fixture
def credentials(database: Database) -> CredentialService:
    return CredentialService(uow_factory(database), SecretCipher(KEY))


async def dump_database(database: Database) -> str:
    """Every row of every table as text: the haystack for leak checks."""
    async with database.engine.connect() as conn:
        tables = [
            row[0]
            for row in await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        ]
        rows = [
            str(row[0])
            for table in tables
            for row in await conn.execute(text(f"SELECT t::text FROM {table} t"))  # noqa: S608  # table names from pg_catalog
        ]
    return "\n".join(rows)


# -- persistence ------------------------------------------------------------------


async def test_value_is_encrypted_at_rest(
    credentials: CredentialService, database: Database
) -> None:
    await credentials.create("crm", kind=CredentialKind.API_KEY, value=Secret(CANARY))
    async with database.engine.connect() as conn:
        blob: object = (
            await conn.execute(text("SELECT ciphertext FROM credentials"))
        ).scalar_one()
    assert isinstance(blob, bytes)
    assert CANARY.encode() not in blob
    assert CANARY not in await dump_database(database)
    assert (await credentials.resolve("credential:crm")).reveal() == CANARY


async def test_metadata_round_trip_and_paging(credentials: CredentialService) -> None:
    for name in ("b-smtp", "a-api", "c-generic"):
        await credentials.create(
            name, kind=CredentialKind.GENERIC, value=Secret(CANARY), description=name
        )
    first = await credentials.list(limit=2)
    assert [c.name for c in first.items] == ["a-api", "b-smtp"] and first.has_more
    rest = await credentials.list(offset=2, limit=2)
    assert [c.name for c in rest.items] == ["c-generic"] and not rest.has_more
    got = await credentials.get("a-api")
    assert (got.kind, got.description, got.reference) == (
        CredentialKind.GENERIC,
        "a-api",
        "credential:a-api",
    )


async def test_duplicate_name_is_a_conflict_and_keeps_the_original(
    credentials: CredentialService,
) -> None:
    await credentials.create("crm", kind=CredentialKind.API_KEY, value=Secret(CANARY))
    with pytest.raises(CredentialAlreadyExistsError):
        await credentials.create(
            "crm", kind=CredentialKind.API_KEY, value=Secret("replacement")
        )
    assert (await credentials.resolve("credential:crm")).reveal() == CANARY


async def test_rotation_and_description_update(
    credentials: CredentialService, database: Database
) -> None:
    created = await credentials.create(
        "crm", kind=CredentialKind.API_KEY, value=Secret("old-value")
    )
    await credentials.update("crm", description="docs only")
    assert (await credentials.resolve("credential:crm")).reveal() == "old-value"

    rotated = await credentials.update("crm", value=Secret(CANARY))
    assert rotated.description == "docs only"
    assert rotated.timestamps.updated_at > created.timestamps.updated_at
    assert (await credentials.resolve("credential:crm")).reveal() == CANARY
    assert "old-value" not in await dump_database(database)


async def test_delete(credentials: CredentialService) -> None:
    await credentials.create("crm", kind=CredentialKind.API_KEY, value=Secret(CANARY))
    await credentials.delete("crm")
    for call in (
        credentials.get("crm"),
        credentials.resolve("credential:crm"),
        credentials.delete("crm"),
        credentials.update("crm", description="x"),
    ):
        with pytest.raises(CredentialNotFoundError):
            await call


async def test_ciphertext_swapped_between_rows_does_not_decrypt(
    credentials: CredentialService, database: Database
) -> None:
    """Database-level tampering cannot redirect a reference to another
    credential's secret."""
    await credentials.create("crm", kind=CredentialKind.API_KEY, value=Secret(CANARY))
    await credentials.create("mail", kind=CredentialKind.SMTP, value=Secret("other"))
    async with database.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE credentials SET ciphertext = "
                "(SELECT ciphertext FROM credentials WHERE name = 'crm') "
                "WHERE name = 'mail'"
            )
        )
    with pytest.raises(SecretDecryptionError):
        await credentials.resolve("credential:mail")


async def test_a_different_key_cannot_decrypt(
    credentials: CredentialService, database: Database
) -> None:
    await credentials.create("crm", kind=CredentialKind.API_KEY, value=Secret(CANARY))
    other = CredentialService(uow_factory(database), SecretCipher(os.urandom(32)))
    with pytest.raises(SecretDecryptionError) as exc:
        await other.resolve("credential:crm")
    assert CANARY not in str(exc.value)


async def test_management_and_resolution_never_log_the_value(
    credentials: CredentialService,
) -> None:
    with capture_json_logs() as lines:
        await credentials.create(
            "crm", kind=CredentialKind.API_KEY, value=Secret(CANARY)
        )
        await credentials.update("crm", value=Secret(CANARY + "-2"))
        await credentials.resolve("credential:crm")
        await credentials.delete("crm")
    events = [line["event"] for line in lines if "event" in line]
    assert events == [
        "credential_created",
        "credential_updated",
        "credential_resolved",
        "credential_deleted",
    ]
    assert CANARY not in json.dumps(lines)


# -- API --------------------------------------------------------------------------


@pytest.fixture
async def api(
    database: Database,
    credentials: CredentialService,
    workflow_service: WorkflowService,
    auth_kit: AuthKit,
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(environment="test", log_level="CRITICAL")
    container = Container(
        settings=settings,
        database=database,
        workflows=workflow_service,
        credentials=credentials,
        **auth_kit.container_fields(),
    )
    transport = httpx.ASGITransport(
        app=create_app(settings=settings, container=container)
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=await auth_kit.admin_headers(),
    ) as client:
        yield client


URL = "/api/v1/credentials"
DETAIL_KEYS = {"name", "reference", "kind", "description", "created_at", "updated_at"}


async def test_api_lifecycle_never_returns_the_value(
    api: httpx.AsyncClient, credentials: CredentialService
) -> None:
    with capture_json_logs() as lines:
        created = await api.post(
            URL, json={"name": "crm", "kind": "api_key", "value": CANARY}
        )
        responses = [
            created,
            await api.get(URL),
            await api.get(f"{URL}/crm"),
            await api.patch(f"{URL}/crm", json={"value": CANARY + "-rotated"}),
            await api.patch(f"{URL}/crm", json={"description": "CRM key"}),
        ]
    assert created.status_code == 201, created.text
    assert set(created.json()) == DETAIL_KEYS
    assert created.json()["reference"] == "credential:crm"
    assert [r.status_code for r in responses] == [201, 200, 200, 200, 200]
    assert responses[-1].json()["description"] == "CRM key"
    for response in responses:
        assert CANARY not in response.text
    assert CANARY not in json.dumps(lines)
    rotated = await credentials.resolve("credential:crm")
    assert rotated.reveal() == CANARY + "-rotated"

    assert (await api.delete(f"{URL}/crm")).status_code == 204
    missing = await api.get(f"{URL}/crm")
    assert (missing.status_code, missing.json()["code"]) == (
        404,
        "CREDENTIAL_NOT_FOUND",
    )


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"name": "Bad Name", "value": CANARY}, 422, "DOMAIN_VALIDATION_ERROR"),
        ({"name": "crm", "kind": "nope", "value": CANARY}, 422, "VALIDATION_ERROR"),
        ({"name": "crm", "value": CANARY * 3000}, 422, "VALIDATION_ERROR"),
        ({"name": "crm", "value": ""}, 422, "VALIDATION_ERROR"),
        ({"name": "crm", "value": {"nested": CANARY}}, 422, "VALIDATION_ERROR"),
        ({"name": 123, "value": CANARY}, 422, "VALIDATION_ERROR"),
        ({"value": CANARY}, 422, "VALIDATION_ERROR"),
    ],
)
async def test_invalid_requests_never_echo_the_value(
    api: httpx.AsyncClient, body: dict[str, Any], status: int, code: str
) -> None:
    response = await api.post(URL, json=body)
    assert (response.status_code, response.json()["code"]) == (status, code)
    assert CANARY not in response.text


async def test_malformed_json_body_is_not_echoed(api: httpx.AsyncClient) -> None:
    response = await api.post(
        URL,
        content=f'{{"name": "crm", "value": "{CANARY}"',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert CANARY not in response.text


async def test_duplicate_via_api(api: httpx.AsyncClient) -> None:
    body = {"name": "crm", "value": CANARY}
    assert (await api.post(URL, json=body)).status_code == 201
    response = await api.post(URL, json=body)
    assert (response.status_code, response.json()["code"]) == (
        409,
        "CREDENTIAL_ALREADY_EXISTS",
    )


async def test_unconfigured_store_is_a_configuration_error() -> None:
    settings = Settings(environment="test", log_level="CRITICAL")
    app = create_app(settings=settings, container=Container(settings=settings))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(URL, json={"name": "crm", "value": CANARY})
    assert (response.status_code, response.json()["code"]) == (
        500,
        "CONFIGURATION_ERROR",
    )
    assert CANARY not in response.text


async def test_openapi_responses_have_no_value_field(api: httpx.AsyncClient) -> None:
    schemas = (await api.get("/openapi.json")).json()["components"]["schemas"]
    assert "value" not in schemas["CredentialDetail"]["properties"]
    assert schemas["CreateCredentialRequest"]["properties"]["value"]["writeOnly"]


# -- workflow configuration only ever holds references ----------------------------


def http_graph(config: dict[str, Any]) -> tuple[Graph, dict[str, str]]:
    graph = Graph()
    http = NodeInstance.create("core.http", config=config, label="http")
    out = NodeInstance.create("core.output", label="out")
    graph.add_node(http)
    graph.add_node(out)
    graph.add_edge(Edge(http.id, "body", out.id, "value"))
    return graph, {"http": http.id, "out": out.id}


def draft_payload(config: dict[str, Any]) -> dict[str, Any]:
    _, ids = http_graph(config)
    return {
        "graph": {
            "nodes": [
                {"id": ids["http"], "node_type": "core.http", "config": config},
                {"id": ids["out"], "node_type": "core.output"},
            ],
            "edges": [
                {
                    "source_node_id": ids["http"],
                    "source_port": "body",
                    "target_node_id": ids["out"],
                    "target_port": "value",
                }
            ],
        }
    }


@pytest.mark.parametrize(
    "config",
    [
        {"url": "http://example.test/", "headers": {"X-Api-Key": CANARY}},
        {
            "url": "http://example.test/",
            "headers": {"Authorization": f"Bearer {CANARY}"},
        },
        {"url": f"http://user:{CANARY}@example.test/"},
        {"url": "http://example.test/", "header_credentials": {"X-Api-Key": CANARY}},
    ],
)
async def test_workflow_api_rejects_plaintext_secrets(
    api: httpx.AsyncClient, database: Database, config: dict[str, Any]
) -> None:
    workflow = (await api.post("/api/v1/workflows", json={"name": "p"})).json()
    response = await api.put(
        f"/api/v1/workflows/{workflow['id']}",
        json={
            "expected_revision": workflow["revision"],
            "name": "p",
            "draft": draft_payload(config),
        },
    )
    assert (response.status_code, response.json()["code"]) == (422, "PLAINTEXT_SECRET")
    assert response.json()["details"]["paths"]
    assert CANARY not in response.text
    assert CANARY not in await dump_database(database)


async def test_workflow_stores_and_returns_only_the_reference(
    api: httpx.AsyncClient,
) -> None:
    await api.post(URL, json={"name": "crm", "value": CANARY})
    config = {
        "url": "http://example.test/",
        "header_credentials": {"X-Api-Key": "credential:crm"},
    }
    workflow = (await api.post("/api/v1/workflows", json={"name": "p"})).json()
    saved = await api.put(
        f"/api/v1/workflows/{workflow['id']}",
        json={
            "expected_revision": workflow["revision"],
            "name": "p",
            "draft": draft_payload(config),
        },
    )
    assert saved.status_code == 200, saved.text
    fetched = await api.get(f"/api/v1/workflows/{workflow['id']}")
    assert "credential:crm" in fetched.text
    assert CANARY not in fetched.text + saved.text


# -- runtime: late resolution, scoped, masked --------------------------------------


LOCAL = NodeServices(network=NetworkPolicy(allow_private_networks=True))


@pytest.fixture
def registry() -> NodeRegistry:
    return create_builtin_registry()


@pytest.fixture
def http_server() -> Iterator[LocalHttpServer]:
    with serve_http() as server:
        yield server


async def run(
    database: Database,
    registry: NodeRegistry,
    credentials: CredentialService,
    graph: Graph,
) -> Execution:
    uow = uow_factory(database)
    workflows = WorkflowService(uow, registry.definitions)
    workflow = await workflows.create_workflow("creds")
    await workflows.update_draft(
        workflow.id,
        expected_revision=workflow.revision,
        name="creds",
        draft=WorkflowDraft(graph=graph),
    )
    version = await workflows.publish_workflow(workflow.id)
    executions = ExecutionService(uow)
    queued = await executions.create_execution(workflow.id, version.version)
    runtime = ExecutionRuntime(
        uow, registry.catalog, credentials=credentials, services=LOCAL
    )
    await runtime.run(queued.id)
    return await executions.get_execution(queued.id)


async def test_http_node_sends_the_resolved_secret_but_nothing_else_keeps_it(
    database: Database,
    registry: NodeRegistry,
    credentials: CredentialService,
    http_server: LocalHttpServer,
) -> None:
    await credentials.create("crm", kind=CredentialKind.API_KEY, value=Secret(CANARY))
    graph, ids = http_graph(
        {
            "url": f"{http_server.base_url}/echo",
            "header_credentials": {"X-Api-Key": "credential:crm"},
        }
    )
    with capture_json_logs() as lines:
        execution = await run(database, registry, credentials, graph)

    assert execution.status is ExecutionStatus.COMPLETED
    (request,) = http_server.state.requests
    assert request.headers["x-api-key"] == CANARY  # resolved late, delivered
    # The echo server reflected the header; the persisted output is masked.
    outputs = execution.node(ids["out"]).outputs
    assert outputs is not None
    assert outputs["value"]["headers"]["x-api-key"] == MASK
    assert CANARY not in await dump_database(database)  # outputs, events, rows
    assert CANARY not in json.dumps(lines)


async def test_missing_credential_fails_the_node_cleanly(
    database: Database, registry: NodeRegistry, credentials: CredentialService
) -> None:
    graph, _ = http_graph(
        {
            "url": "http://127.0.0.1:1/",
            "header_credentials": {"X-Api-Key": "credential:absent"},
        }
    )
    execution = await run(database, registry, credentials, graph)
    assert execution.status is ExecutionStatus.FAILED
    assert execution.error is not None
    assert execution.error.code == "CREDENTIAL_NOT_FOUND"
    assert "credential:absent" in execution.error.message


async def test_rotation_applies_to_the_next_execution_without_republish(
    database: Database,
    registry: NodeRegistry,
    credentials: CredentialService,
    http_server: LocalHttpServer,
) -> None:
    """The snapshot pins the reference, not the value: secrets are resolved
    at run time."""
    await credentials.create("crm", kind=CredentialKind.API_KEY, value=Secret("v1-key"))
    graph, _ = http_graph(
        {
            "url": f"{http_server.base_url}/echo",
            "header_credentials": {"X-Api-Key": "credential:crm"},
        }
    )
    await run(database, registry, credentials, graph)
    await credentials.update("crm", value=Secret("v2-key"))
    await run(database, registry, credentials, graph)
    assert [r.headers["x-api-key"] for r in http_server.state.requests] == [
        "v1-key",
        "v2-key",
    ]


@pytest.fixture(scope="module")
def auth_mailpit() -> Iterator[Mailpit]:
    if not docker_available():
        pytest.skip("Docker daemon not available for Testcontainers")
    with run_mailpit(auth=True) as server:
        yield server


async def test_smtp_authenticates_with_a_stored_credential(
    database: Database,
    registry: NodeRegistry,
    credentials: CredentialService,
    auth_mailpit: Mailpit,
) -> None:
    user, password = SMTP_LOGIN
    await credentials.create("mail", kind=CredentialKind.SMTP, value=Secret(password))
    graph = Graph()
    mail = NodeInstance.create(
        "core.smtp",
        config={
            "host": auth_mailpit.smtp_host,
            "port": auth_mailpit.smtp_port,
            "security": "none",
            "username": user,
            "password_credential": "credential:mail",
            "sender": "engine@akasha.test",
            "recipients": ["ada@example.test"],
        },
    )
    subject = NodeInstance.create("core.input", config={"field": "s"})
    graph.add_node(subject)
    graph.add_node(mail)
    graph.add_edge(Edge(subject.id, "value", mail.id, "subject"))
    graph.add_edge(Edge(subject.id, "value", mail.id, "body"))
    auth_mailpit.clear()

    uow = uow_factory(database)
    workflows = WorkflowService(uow, registry.definitions)
    workflow = await workflows.create_workflow("smtp")
    await workflows.update_draft(
        workflow.id,
        expected_revision=workflow.revision,
        name="smtp",
        draft=WorkflowDraft(graph=graph),
    )
    version = await workflows.publish_workflow(workflow.id)
    executions = ExecutionService(uow)
    queued = await executions.create_execution(
        workflow.id, version.version, input={"s": "hi"}
    )
    await ExecutionRuntime(
        uow, registry.catalog, credentials=credentials, services=LOCAL
    ).run(queued.id)
    execution = await executions.get_execution(queued.id)

    assert execution.status is ExecutionStatus.COMPLETED, execution.error
    assert [m["Subject"] for m in auth_mailpit.messages()] == ["hi"]
    assert password not in await dump_database(database)


async def test_nul_in_a_description_is_a_validation_error_not_a_500(
    api: httpx.AsyncClient,
) -> None:
    create = await api.post(
        URL, json={"name": "crm", "kind": "api_key", "value": "v", "description": "a\0"}
    )
    assert (create.status_code, create.json()["code"]) == (
        422,
        "DOMAIN_VALIDATION_ERROR",
    )
    assert (
        await api.post(URL, json={"name": "crm", "kind": "api_key", "value": "v"})
    ).status_code == 201
    patch = await api.patch(f"{URL}/crm", json={"description": "b\0"})
    assert (patch.status_code, patch.json()["code"]) == (422, "DOMAIN_VALIDATION_ERROR")
    assert (await api.get(f"{URL}/crm")).json()["description"] == ""
