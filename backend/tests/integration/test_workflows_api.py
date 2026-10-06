"""/api/v1/workflows end-to-end through the real HTTP stack and PostgreSQL.

App → router → WorkflowService → Unit of Work → PostgreSQL; nothing mocked
(backend/AGENTS.md §56). httpx's ASGI transport keeps the app on the
session event loop the database engine is bound to.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from akasha.api.app import create_app
from akasha.application.workflows import WorkflowService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.node import NodeDefinition
from akasha.domain.primitives import new_id
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from tests.integration.fixtures import AuthKit, make_auth_kit

pytestmark = pytest.mark.usefixtures("clean_db")

ERROR_KEYS = {"code", "message", "details", "request_id"}
UNKNOWN = "wf_" + "0" * 32


@pytest.fixture
async def api(
    database: Database, workflow_service: WorkflowService, auth_kit: AuthKit
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(environment="test", log_level="CRITICAL")
    container = Container(
        settings=settings,
        database=database,
        workflows=workflow_service,
        **auth_kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=await auth_kit.admin_headers(),
    ) as client:
        yield client


def valid_draft() -> dict[str, Any]:
    source, sink = new_id("node"), new_id("node")
    return {
        "graph": {
            "nodes": [
                {"id": source, "node_type": "test.source"},
                {"id": sink, "node_type": "test.sink", "config": {"prefix": ">"}},
            ],
            "edges": [
                {
                    "source_node_id": source,
                    "source_port": "text",
                    "target_node_id": sink,
                    "target_port": "text",
                }
            ],
        },
        "triggers": [
            {"id": new_id("trig"), "trigger_type": "manual"},
        ],
        "dependencies": [{"kind": "node", "name": "test.sink"}],
    }


async def create(api: httpx.AsyncClient, name: str = "demo") -> dict[str, Any]:
    response = await api.post("/api/v1/workflows", json={"name": name})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def put_draft(
    api: httpx.AsyncClient,
    workflow_id: str,
    draft: dict[str, Any],
    *,
    revision: int,
    name: str = "demo",
) -> httpx.Response:
    return await api.put(
        f"/api/v1/workflows/{workflow_id}",
        json={"expected_revision": revision, "name": name, "draft": draft},
    )


def assert_error(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    assert set(body) == ERROR_KEYS
    assert body["code"] == code
    assert body["request_id"]
    assert "Traceback" not in response.text
    return body


async def test_full_lifecycle_create_edit_validate_publish(
    api: httpx.AsyncClient,
) -> None:
    """Acceptance criterion: a user can create and publish a valid Workflow
    through the API — and the published version stays immutable."""
    created = await create(api)
    pid = created["id"]
    assert created["status"] == "draft"
    assert (created["revision"], created["current_version"]) == (1, 0)
    assert created["draft"] == {
        "graph": {"nodes": [], "edges": []},
        "triggers": [],
        "dependencies": [],
    }

    draft = valid_draft()
    updated = await put_draft(api, pid, draft, revision=1, name="renamed")
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert updated.json()["name"] == "renamed"

    fetched = (await api.get(f"/api/v1/workflows/{pid}")).json()
    assert fetched["draft"]["graph"]["edges"] == draft["graph"]["edges"]
    assert fetched["draft"]["triggers"][0]["trigger_type"] == "manual"

    validation = await api.post(f"/api/v1/workflows/{pid}/validate")
    assert validation.status_code == 200
    assert validation.json() == {"valid": True, "issues": []}

    published = await api.post(
        f"/api/v1/workflows/{pid}/publish", json={"expected_revision": 2}
    )
    assert published.status_code == 201, published.text
    version = published.json()
    assert version["version"] == 1
    assert version["graph"] == fetched["draft"]["graph"]
    # The author is the authenticated caller, who also owns the workflow.
    assert created["owner_id"] is not None
    author = {"id": created["owner_id"], "username": "admin"}
    assert version["published_by"] == author

    workflow = (await api.get(f"/api/v1/workflows/{pid}")).json()
    assert workflow["status"] == "published"
    assert (workflow["current_version"], workflow["revision"]) == (1, 3)

    # Draft edits after publish never reach the published version.
    emptied = await put_draft(
        api, pid, {"graph": {"nodes": [], "edges": []}}, revision=3
    )
    assert emptied.status_code == 200
    stored = (await api.get(f"/api/v1/workflows/{pid}/versions/1")).json()
    assert stored == version

    versions = (await api.get(f"/api/v1/workflows/{pid}/versions")).json()
    assert [v["version"] for v in versions["items"]] == [1]
    assert "graph" not in versions["items"][0]  # summaries only
    assert versions["items"][0]["published_by"] == author
    assert versions["page"] == {"offset": 0, "limit": 50, "has_more": False}


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
async def test_published_versions_cannot_be_modified(
    api: httpx.AsyncClient, method: str
) -> None:
    pid = (await create(api))["id"]
    assert (await put_draft(api, pid, valid_draft(), revision=1)).status_code == 200
    assert (await api.post(f"/api/v1/workflows/{pid}/publish")).status_code == 201
    response = await api.request(method.upper(), f"/api/v1/workflows/{pid}/versions/1")
    assert_error(response, 405, "METHOD_NOT_ALLOWED")


async def test_list_workflows_pagination(api: httpx.AsyncClient) -> None:
    ids = [(await create(api, f"p{i}"))["id"] for i in range(3)]
    first = (await api.get("/api/v1/workflows", params={"limit": 2})).json()
    assert [p["id"] for p in first["items"]] == ids[:2]
    assert first["page"] == {"offset": 0, "limit": 2, "has_more": True}
    assert "draft" not in first["items"][0]  # summaries only
    rest = (await api.get("/api/v1/workflows", params={"offset": 2, "limit": 2})).json()
    assert [p["id"] for p in rest["items"]] == ids[2:]
    assert rest["page"]["has_more"] is False


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "x"}]
)
async def test_list_rejects_bad_pagination(
    api: httpx.AsyncClient, params: dict[str, Any]
) -> None:
    response = await api.get("/api/v1/workflows", params=params)
    assert_error(response, 422, "VALIDATION_ERROR")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 201},
        {"name": 42},
        {"name": "ok", "unexpected": True},
    ],
)
async def test_create_rejects_malformed_bodies(
    api: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    response = await api.post("/api/v1/workflows", json=body)
    assert_error(response, 422, "VALIDATION_ERROR")


async def test_create_rejects_non_json_body(api: httpx.AsyncClient) -> None:
    response = await api.post(
        "/api/v1/workflows",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert_error(response, 422, "VALIDATION_ERROR")


async def test_unknown_workflow_is_structured_404_with_request_id(
    api: httpx.AsyncClient,
) -> None:
    for path in ("", "/versions", "/versions/1"):
        response = await api.get(
            f"/api/v1/workflows/{UNKNOWN}{path}", headers={"x-request-id": "req_7"}
        )
        # The workflow access check answers before any version lookup.
        body = assert_error(response, 404, "WORKFLOW_NOT_FOUND")
        assert body["request_id"] == "req_7"
    for method, path in (("POST", "/validate"), ("POST", "/publish"), ("DELETE", "")):
        response = await api.request(method, f"/api/v1/workflows/{UNKNOWN}{path}")
        assert_error(response, 404, "WORKFLOW_NOT_FOUND")
    response = await put_draft(api, UNKNOWN, {}, revision=1)
    assert_error(response, 404, "WORKFLOW_NOT_FOUND")


@pytest.mark.parametrize("version", ["0", "-1", "one"])
async def test_version_path_must_be_positive_integer(
    api: httpx.AsyncClient, version: str
) -> None:
    pid = (await create(api))["id"]
    response = await api.get(f"/api/v1/workflows/{pid}/versions/{version}")
    assert_error(response, 422, "VALIDATION_ERROR")


async def test_stale_revision_is_a_conflict(api: httpx.AsyncClient) -> None:
    pid = (await create(api))["id"]
    assert (await put_draft(api, pid, {}, revision=1)).status_code == 200
    body = assert_error(
        await put_draft(api, pid, {}, revision=1, name="stale"),
        409,
        "CONCURRENT_MODIFICATION",
    )
    assert body["details"]["current_revision"] == 2
    assert (await api.get(f"/api/v1/workflows/{pid}")).json()["name"] == "demo"


def _with_node(node: dict[str, Any]) -> dict[str, Any]:
    return {"graph": {"nodes": [node], "edges": []}}


@pytest.mark.parametrize(
    ("draft", "code"),
    [
        # Structural domain invariants → 422 domain error.
        (
            _with_node({"id": "not-an-id", "node_type": "test.sink"}),
            "DOMAIN_VALIDATION_ERROR",
        ),
        (
            {
                "graph": {
                    "nodes": [],
                    "edges": [
                        {
                            "source_node_id": new_id("node"),
                            "source_port": "text",
                            "target_node_id": new_id("node"),
                            "target_port": "text",
                        }
                    ],
                }
            },
            "DOMAIN_VALIDATION_ERROR",
        ),
        (
            {
                "triggers": [
                    {"id": new_id("trig"), "trigger_type": "cron", "config": {}}
                ]
            },
            "DOMAIN_VALIDATION_ERROR",
        ),
        # Schema violations → 422 request validation.
        (
            {"triggers": [{"id": new_id("trig"), "trigger_type": "telepathy"}]},
            "VALIDATION_ERROR",
        ),
        ({"dependencies": [{"kind": "service", "name": "x"}]}, "VALIDATION_ERROR"),
        (_with_node({"id": new_id("node")}), "VALIDATION_ERROR"),
        ({"graph": {"nodes": [], "edges": [], "extra": 1}}, "VALIDATION_ERROR"),
        # Positions must be finite {x, y} pairs, nothing else.
        (
            _with_node(
                {
                    "id": new_id("node"),
                    "node_type": "test.sink",
                    "position": {"x": "inf", "y": 0},
                }
            ),
            "VALIDATION_ERROR",
        ),
        (
            _with_node(
                {"id": new_id("node"), "node_type": "test.sink", "position": {"x": 1}}
            ),
            "VALIDATION_ERROR",
        ),
        (
            _with_node(
                {
                    "id": new_id("node"),
                    "node_type": "test.sink",
                    "position": {"x": 1, "y": 2, "z": 3},
                }
            ),
            "VALIDATION_ERROR",
        ),
    ],
)
async def test_update_rejects_malformed_drafts(
    api: httpx.AsyncClient, draft: dict[str, Any], code: str
) -> None:
    pid = (await create(api))["id"]
    assert_error(await put_draft(api, pid, draft, revision=1), 422, code)
    # Nothing was written.
    assert (await api.get(f"/api/v1/workflows/{pid}")).json()["revision"] == 1


async def test_node_positions_round_trip_and_survive_publish(
    api: httpx.AsyncClient,
) -> None:
    pid = (await create(api))["id"]
    draft = valid_draft()
    placed, unplaced = draft["graph"]["nodes"]
    placed["position"] = {"x": -12.5, "y": 300}
    saved = await put_draft(api, pid, draft, revision=1)
    assert saved.status_code == 200, saved.text
    nodes = {n["id"]: n for n in saved.json()["draft"]["graph"]["nodes"]}
    assert nodes[placed["id"]]["position"] == {"x": -12.5, "y": 300.0}
    assert nodes[unplaced["id"]]["position"] is None
    # The published version renders with the layout it was published with.
    published = await api.post(f"/api/v1/workflows/{pid}/publish")
    assert published.status_code == 201, published.text
    version_nodes = {n["id"]: n for n in published.json()["graph"]["nodes"]}
    assert version_nodes[placed["id"]]["position"] == {"x": -12.5, "y": 300.0}


async def test_duplicate_node_ids_rejected(api: httpx.AsyncClient) -> None:
    pid = (await create(api))["id"]
    node = {"id": new_id("node"), "node_type": "test.sink"}
    draft = {"graph": {"nodes": [node, node], "edges": []}}
    assert_error(
        await put_draft(api, pid, draft, revision=1), 422, "DOMAIN_VALIDATION_ERROR"
    )


async def test_validate_reports_structured_issues(api: httpx.AsyncClient) -> None:
    pid = (await create(api))["id"]
    source, sink, ghost = new_id("node"), new_id("node"), new_id("node")
    draft = {
        "graph": {
            "nodes": [
                {"id": source, "node_type": "test.source"},
                {"id": sink, "node_type": "test.sink"},
                {"id": ghost, "node_type": "no.such_node"},
            ],
            "edges": [
                {
                    "source_node_id": source,
                    "source_port": "nope",
                    "target_node_id": sink,
                    "target_port": "text",
                }
            ],
        }
    }
    assert (await put_draft(api, pid, draft, revision=1)).status_code == 200

    response = await api.post(f"/api/v1/workflows/{pid}/validate")
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    by_code = {i["code"]: i for i in body["issues"]}
    assert by_code["unknown_node_type"]["node_id"] == ghost
    assert by_code["unknown_port"]["port"] == "nope"
    assert by_code["unknown_port"]["edge"]["source_node_id"] == source
    assert by_code["missing_required_input"]["node_id"] == sink
    # Phase 10 added the trigger location (null for graph issues).
    assert by_code["missing_required_input"]["trigger_id"] is None
    assert set(by_code["missing_required_input"]) == {
        "code",
        "message",
        "node_id",
        "port",
        "edge",
        "trigger_id",
    }


async def test_publish_invalid_draft_is_structured_422(api: httpx.AsyncClient) -> None:
    pid = (await create(api))["id"]
    body = assert_error(
        await api.post(f"/api/v1/workflows/{pid}/publish"),
        422,
        "WORKFLOW_VALIDATION_FAILED",
    )
    assert [i["code"] for i in body["details"]["issues"]] == ["empty_graph"]
    versions = (await api.get(f"/api/v1/workflows/{pid}/versions")).json()
    assert versions["items"] == []


async def test_publish_pinned_to_stale_revision_is_a_conflict(
    api: httpx.AsyncClient,
) -> None:
    pid = (await create(api))["id"]
    assert (await put_draft(api, pid, valid_draft(), revision=1)).status_code == 200
    response = await api.post(
        f"/api/v1/workflows/{pid}/publish", json={"expected_revision": 1}
    )
    assert_error(response, 409, "CONCURRENT_MODIFICATION")


async def test_validate_pinned_to_revision(api: httpx.AsyncClient) -> None:
    """A validation result must describe the draft the client shows: a
    stale pin is a conflict, a current one validates as usual."""
    pid = (await create(api))["id"]
    assert (await put_draft(api, pid, valid_draft(), revision=1)).status_code == 200
    stale = await api.post(
        f"/api/v1/workflows/{pid}/validate", json={"expected_revision": 1}
    )
    body = assert_error(stale, 409, "CONCURRENT_MODIFICATION")
    assert body["details"]["current_revision"] == 2
    current = await api.post(
        f"/api/v1/workflows/{pid}/validate", json={"expected_revision": 2}
    )
    assert current.status_code == 200
    assert current.json() == {"valid": True, "issues": []}
    malformed = await api.post(
        f"/api/v1/workflows/{pid}/validate", json={"expected_revision": 0}
    )
    assert_error(malformed, 422, "VALIDATION_ERROR")


async def test_publish_rejects_malformed_body(api: httpx.AsyncClient) -> None:
    pid = (await create(api))["id"]
    response = await api.post(
        f"/api/v1/workflows/{pid}/publish", json={"expected_revision": "latest"}
    )
    assert_error(response, 422, "VALIDATION_ERROR")


async def test_delete(api: httpx.AsyncClient) -> None:
    pid = (await create(api))["id"]
    response = await api.delete(f"/api/v1/workflows/{pid}")
    assert response.status_code == 204
    assert response.content == b""
    assert_error(await api.get(f"/api/v1/workflows/{pid}"), 404, "WORKFLOW_NOT_FOUND")


async def test_delete_published_workflow_is_a_conflict(api: httpx.AsyncClient) -> None:
    pid = (await create(api))["id"]
    assert (await put_draft(api, pid, valid_draft(), revision=1)).status_code == 200
    assert (await api.post(f"/api/v1/workflows/{pid}/publish")).status_code == 201
    assert_error(
        await api.delete(f"/api/v1/workflows/{pid}"), 409, "WORKFLOW_HAS_VERSIONS"
    )
    assert (await api.get(f"/api/v1/workflows/{pid}/versions/1")).status_code == 200


async def test_infrastructure_failure_is_opaque_500(
    node_definitions: dict[str, NodeDefinition],
) -> None:
    """Database down mid-request: stable INTERNAL_ERROR, no driver names,
    hosts or stack traces in the response (AGENTS §8/§9)."""
    broken = Database("postgresql+asyncpg://akasha:hunter2@127.0.0.1:1/akasha")
    settings = Settings(environment="test", log_level="CRITICAL")
    container = Container(
        settings=settings,
        workflows=WorkflowService(
            lambda: SqlAlchemyUnitOfWork(broken), node_definitions
        ),
        # The session lookup hits the dead database first: an outage must
        # be a 500, never a 401 that logs every user out.
        **make_auth_kit(broken).container_fields(),
    )
    transport = httpx.ASGITransport(
        app=create_app(settings=settings, container=container),
        raise_app_exceptions=False,
    )
    headers = {"Authorization": "Bearer some-token"}
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=headers
        ) as c:
            response = await c.get("/api/v1/workflows")
    finally:
        await broken.dispose()
    body = assert_error(response, 500, "INTERNAL_ERROR")
    assert body["message"] == "An internal error occurred."
    for leak in ("asyncpg", "127.0.0.1", "hunter2", "sqlalchemy", "OSError"):
        assert leak not in response.text


async def test_openapi_documents_workflow_endpoints(api: httpx.AsyncClient) -> None:
    paths = (await api.get("/openapi.json")).json()["paths"]
    assert {
        "/api/v1/workflows",
        "/api/v1/workflows/{workflow_id}",
        "/api/v1/workflows/{workflow_id}/validate",
        "/api/v1/workflows/{workflow_id}/publish",
        "/api/v1/workflows/{workflow_id}/versions",
        "/api/v1/workflows/{workflow_id}/versions/{version}",
    } <= set(paths)
    assert set(paths["/api/v1/workflows/{workflow_id}/versions/{version}"]) == {"get"}
    # Error bodies are documented as the real ErrorResponse, not FastAPI's
    # default validation schema — frontend types must not diverge (§21).
    responses = paths["/api/v1/workflows/{workflow_id}/publish"]["post"]["responses"]
    for status in ("404", "409", "422", "500"):
        schema = responses[status]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ErrorResponse"}


# JSON the HTTP layer accepts but PostgreSQL cannot store: each used to reach
# the database and surface as a 500.
@pytest.mark.parametrize(
    ("raw", "path_prefix"),
    [
        ('{"prefix": NaN}', "nodes."),
        ('{"prefix": Infinity}', "nodes."),
        ('{"prefix": -Infinity}', "nodes."),
        ('{"prefix": 1e999}', "nodes."),
        ('{"prefix": "a\\u0000b"}', "nodes."),
        ('{"pre\\u0000fix": ">"}', "nodes."),
        ('{"prefix": "\\ud800"}', "nodes."),
    ],
)
async def test_put_rejects_unstorable_config_values(
    api: httpx.AsyncClient, raw: str, path_prefix: str
) -> None:
    workflow = await create(api)
    body = json.dumps(
        {"expected_revision": workflow["revision"], "name": "x", "draft": valid_draft()}
    ).replace('{"prefix": ">"}', raw)
    response = await api.put(
        f"/api/v1/workflows/{workflow['id']}",
        content=body,
        headers={"content-type": "application/json"},
    )
    error = assert_error(response, 422, "DOMAIN_VALIDATION_ERROR")
    assert [p[: len(path_prefix)] for p in error["details"]["paths"]] == [path_prefix]
    unchanged = (await api.get(f"/api/v1/workflows/{workflow['id']}")).json()
    assert unchanged["revision"] == workflow["revision"]


@pytest.mark.parametrize(
    "field", ["name", "description", "label", "port", "trigger", "dependency"]
)
async def test_put_rejects_nul_anywhere_in_the_workflow(
    api: httpx.AsyncClient, field: str
) -> None:
    workflow = await create(api)
    draft = valid_draft()
    body: dict[str, Any] = {
        "expected_revision": workflow["revision"],
        "name": "x",
        "draft": draft,
    }
    match field:
        case "name" | "description":
            body[field] = "a\0b"
        case "label":
            draft["graph"]["nodes"][0]["label"] = "a\0b"
        case "port":
            draft["graph"]["edges"][0]["source_port"] = "te\0xt"
        case "trigger":
            draft["triggers"][0]["config"] = {"k": "a\0b"}
        case "dependency":
            draft["dependencies"][0]["version_constraint"] = ">=1.0.0\0"
    response = await api.put(f"/api/v1/workflows/{workflow['id']}", json=body)
    error = assert_error(response, 422, "DOMAIN_VALIDATION_ERROR")
    assert "NUL" in error["message"]


async def test_create_rejects_nul_in_name_and_description(
    api: httpx.AsyncClient,
) -> None:
    for body in ({"name": "a\0b"}, {"name": "a", "description": "a\0b"}):
        response = await api.post("/api/v1/workflows", json=body)
        assert_error(response, 422, "DOMAIN_VALIDATION_ERROR")
    assert (await api.get("/api/v1/workflows")).json()["items"] == []


async def test_list_workflows_search_is_literal_and_case_insensitive(
    api: httpx.AsyncClient,
) -> None:
    alpha = (await create(api, "Alpha Agent"))["id"]
    await create(api, "beta")
    pct = (await create(api, "100% done_now"))["id"]

    async def ids(q: str) -> list[str]:
        response = await api.get("/api/v1/workflows", params={"q": q})
        assert response.status_code == 200, response.text
        return [p["id"] for p in response.json()["items"]]

    assert await ids("ALPHA") == [alpha]
    # LIKE wildcards are matched literally, not as patterns.
    assert await ids("%") == [pct]
    assert await ids("e_n") == [pct]
    assert await ids("\\") == []


async def test_list_workflows_filters_by_status(api: httpx.AsyncClient) -> None:
    draft = (await create(api, "draft"))["id"]
    published = (await create(api, "published"))["id"]
    assert (await put_draft(api, published, valid_draft(), revision=1)).status_code
    assert (await api.post(f"/api/v1/workflows/{published}/publish")).status_code == 201

    async def ids(*statuses: str) -> list[str]:
        response = await api.get("/api/v1/workflows", params={"status": statuses})
        return [p["id"] for p in response.json()["items"]]

    assert await ids("draft") == [draft]
    assert await ids("published") == [published]
    assert await ids("draft", "published") == [draft, published]


async def test_list_workflows_sorting(api: httpx.AsyncClient) -> None:
    b = (await create(api, "b"))["id"]
    a = (await create(api, "A"))["id"]
    c = (await create(api, "c"))["id"]
    assert (await put_draft(api, b, {}, revision=1, name="b")).status_code == 200

    async def ids(sort: str) -> list[str]:
        response = await api.get("/api/v1/workflows", params={"sort": sort})
        return [p["id"] for p in response.json()["items"]]

    assert await ids("created_at") == [b, a, c]
    assert await ids("-created_at") == [c, a, b]
    assert await ids("name") == [a, b, c]
    assert await ids("-name") == [c, b, a]
    assert (await ids("-updated_at"))[0] == b
    assert (await ids("updated_at"))[-1] == b


@pytest.mark.parametrize(
    "params",
    [{"sort": "owner_id"}, {"status": "deleted"}, {"q": ""}, {"q": "x" * 201}],
)
async def test_list_rejects_bad_query(
    api: httpx.AsyncClient, params: dict[str, Any]
) -> None:
    assert_error(
        await api.get("/api/v1/workflows", params=params), 422, "VALIDATION_ERROR"
    )


async def test_duplicate_copies_the_draft_into_a_new_unpublished_workflow(
    api: httpx.AsyncClient,
) -> None:
    pid = (await create(api, "source"))["id"]
    draft = valid_draft()
    assert (await put_draft(api, pid, draft, revision=1)).status_code == 200
    assert (await api.post(f"/api/v1/workflows/{pid}/publish")).status_code == 201

    response = await api.post(
        f"/api/v1/workflows/{pid}/duplicate", json={"name": "  copy  "}
    )
    assert response.status_code == 201, response.text
    copy = response.json()
    assert copy["id"] != pid
    assert copy["name"] == "copy"
    assert copy["status"] == "draft"
    assert copy["current_version"] == 0
    assert copy["revision"] == 1
    source = (await api.get(f"/api/v1/workflows/{pid}")).json()
    assert copy["draft"] == source["draft"]
    # The copy is independent: it has no history and can be deleted.
    assert (await api.get(f"/api/v1/workflows/{copy['id']}/versions")).json()[
        "items"
    ] == []
    assert (await api.delete(f"/api/v1/workflows/{copy['id']}")).status_code == 204
    assert (await api.get(f"/api/v1/workflows/{pid}")).status_code == 200


async def published_then_emptied(api: httpx.AsyncClient) -> tuple[str, dict[str, Any]]:
    """A workflow with v1 (valid_draft) whose draft has since been emptied
    (revision 4). Returns (id, v1 graph)."""
    pid = (await create(api, "source"))["id"]
    assert (await put_draft(api, pid, valid_draft(), revision=1)).status_code == 200
    assert (await api.post(f"/api/v1/workflows/{pid}/publish")).status_code == 201
    v1 = (await api.get(f"/api/v1/workflows/{pid}/versions/1")).json()
    assert (await put_draft(api, pid, {}, revision=3)).status_code == 200
    return pid, v1["graph"]


async def test_duplicate_a_published_version(api: httpx.AsyncClient) -> None:
    pid, v1_graph = await published_then_emptied(api)

    response = await api.post(
        f"/api/v1/workflows/{pid}/duplicate", json={"name": "from v1", "version": 1}
    )
    assert response.status_code == 201, response.text
    copy = response.json()
    assert copy["draft"]["graph"] == v1_graph  # the version, not the draft
    assert copy["current_version"] == 0

    assert_error(
        await api.post(
            f"/api/v1/workflows/{pid}/duplicate", json={"name": "x", "version": 9}
        ),
        404,
        "WORKFLOW_VERSION_NOT_FOUND",
    )
    bad = await api.post(
        f"/api/v1/workflows/{pid}/duplicate", json={"name": "x", "version": 0}
    )
    assert bad.status_code == 422


async def test_restore_version_replaces_only_the_draft(
    api: httpx.AsyncClient,
) -> None:
    pid, v1_graph = await published_then_emptied(api)
    before = (await api.get(f"/api/v1/workflows/{pid}/versions/1")).json()

    restored = await api.post(
        f"/api/v1/workflows/{pid}/versions/1/restore", json={"expected_revision": 4}
    )
    assert restored.status_code == 200, restored.text
    workflow = restored.json()
    assert workflow["draft"]["graph"] == v1_graph
    assert (workflow["revision"], workflow["current_version"]) == (5, 1)
    assert workflow["status"] == "published"

    # The restored draft is a copy: editing it never reaches v1.
    assert (await put_draft(api, pid, {}, revision=5)).status_code == 200
    assert (await api.get(f"/api/v1/workflows/{pid}/versions/1")).json() == before
    # ... and publishing it later is a new version, not a rewrite of v1.
    again = await api.post(
        f"/api/v1/workflows/{pid}/versions/1/restore", json={"expected_revision": 6}
    )
    assert again.status_code == 200
    published = await api.post(f"/api/v1/workflows/{pid}/publish")
    assert published.json()["version"] == 2


async def test_restore_version_errors(api: httpx.AsyncClient) -> None:
    pid, _ = await published_then_emptied(api)
    url = f"/api/v1/workflows/{pid}/versions"

    # A stale revision would overwrite unseen draft work.
    assert_error(
        await api.post(f"{url}/1/restore", json={"expected_revision": 1}),
        409,
        "CONCURRENT_MODIFICATION",
    )
    assert (await api.get(f"/api/v1/workflows/{pid}")).json()["draft"]["graph"][
        "nodes"
    ] == []
    assert_error(
        await api.post(f"{url}/9/restore", json={"expected_revision": 4}),
        404,
        "WORKFLOW_VERSION_NOT_FOUND",
    )
    assert_error(
        await api.post(
            f"/api/v1/workflows/{UNKNOWN}/versions/1/restore",
            json={"expected_revision": 1},
        ),
        404,
        "WORKFLOW_NOT_FOUND",
    )
    for body in ({}, {"expected_revision": 0}, {"expected_revision": 4, "x": 1}):
        response = await api.post(f"{url}/1/restore", json=body)
        assert response.status_code == 422, body


@pytest.mark.parametrize("body", [{}, {"name": " "}, {"name": "x", "extra": 1}])
async def test_duplicate_rejects_malformed_bodies(
    api: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    pid = (await create(api))["id"]
    response = await api.post(f"/api/v1/workflows/{pid}/duplicate", json=body)
    assert_error(response, 422, "VALIDATION_ERROR")


async def test_duplicate_unknown_workflow_is_404(api: httpx.AsyncClient) -> None:
    response = await api.post(
        f"/api/v1/workflows/{UNKNOWN}/duplicate", json={"name": "copy"}
    )
    assert_error(response, 404, "WORKFLOW_NOT_FOUND")
