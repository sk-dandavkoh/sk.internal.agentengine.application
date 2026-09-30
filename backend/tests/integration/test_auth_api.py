"""Phase 20 end-to-end: authentication and authorization through the real
HTTP stack and PostgreSQL (nothing mocked but the clock).

Covers setup, login, invalid credentials, sessions (expiry, logout,
revocation), password handling, the brute-force throttle, default-deny
route protection, admin-only areas and the project ownership boundary
across every project-, execution- and report-scoped route.
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from akasha.api.app import create_app
from akasha.application.credentials import CredentialService
from akasha.application.executions import ExecutionService
from akasha.application.packages import ProjectPackageService
from akasha.application.projects import ProjectService
from akasha.application.reports import ReportService
from akasha.application.triggers import TriggerRegistry, TriggerService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.identity import Role
from akasha.domain.node import NodeDefinition
from akasha.domain.primitives import new_id
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.security.crypto import SecretCipher
from tests.integration.conftest import PASSWORD, AuthKit, make_auth_kit
from tests.integration.test_triggers_api import draft_with
from tests.support import capture_json_logs

pytestmark = pytest.mark.usefixtures("clean_db")

METRICS_TOKEN = "scrape-" + "s" * 32
TTL = timedelta(hours=1)


@dataclass
class Clock:
    now: datetime = field(default_factory=lambda: datetime(2026, 9, 30, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Env:
    client: httpx.AsyncClient
    kit: AuthKit
    clock: Clock
    app: Any

    async def admin(self, username: str = "admin") -> dict[str, str]:
        return await self.kit.admin_headers(username)

    async def member(self, admin: dict[str, str], name: str) -> dict[str, str]:
        return await self.kit.member_headers(admin, name)


@pytest.fixture
async def env(
    database: Database, node_definitions: dict[str, NodeDefinition]
) -> AsyncIterator[Env]:
    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    clock = Clock()
    kit = make_auth_kit(database, clock=clock, session_ttl=TTL)
    settings = Settings(
        environment="test",
        log_level="CRITICAL",
        metrics_token=SecretStr(METRICS_TOKEN),
    )
    executions = ExecutionService(uow)
    container = Container(
        settings=settings,
        database=database,
        projects=ProjectService(uow, node_definitions),
        packages=ProjectPackageService(uow, node_definitions),
        executions=executions,
        reports=ReportService(uow),
        triggers=TriggerService(executions),
        trigger_registry=TriggerRegistry(uow),
        credentials=CredentialService(uow, SecretCipher(b"k" * 32)),
        **kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield Env(c, kit, clock, app)


def assert_error(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    assert body["code"] == code, body
    assert "Traceback" not in response.text
    return body


async def login(env: Env, username: str, password: str = PASSWORD) -> httpx.Response:
    return await env.client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


def bearer(response: httpx.Response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def me(env: Env, headers: dict[str, str]) -> httpx.Response:
    return await env.client.get("/api/v1/auth/me", headers=headers)


async def row(database: Database, sql: str, **params: Any) -> Any:
    async with database.engine.connect() as conn:
        return (await conn.execute(text(sql), params)).one()


# -- setup ----------------------------------------------------------------------


async def test_setup_creates_the_first_admin_once(env: Env) -> None:
    body = {"username": "root", "password": PASSWORD}
    created = await env.client.post("/api/v1/auth/setup", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "admin"
    assert "password" not in created.text and PASSWORD not in created.text

    again = {"username": "intruder", "password": PASSWORD}
    response = await env.client.post("/api/v1/auth/setup", json=again)
    assert_error(response, 409, "SETUP_ALREADY_COMPLETED")
    assert_error(await login(env, "intruder"), 401, "INVALID_CREDENTIALS")


async def test_concurrent_setup_creates_exactly_one_admin(
    env: Env, database: Database
) -> None:
    results = await asyncio.gather(
        *(
            env.client.post(
                "/api/v1/auth/setup",
                json={"username": f"admin{i}", "password": PASSWORD},
            )
            for i in range(5)
        )
    )
    assert sorted(r.status_code for r in results) == [201, 409, 409, 409, 409]
    assert (await row(database, "SELECT count(*) FROM users"))[0] == 1


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"username": "root", "password": "short"}, "DOMAIN_VALIDATION_ERROR"),
        ({"username": "Root!", "password": PASSWORD}, "DOMAIN_VALIDATION_ERROR"),
        ({"username": "root", "password": "x" * 257}, "VALIDATION_ERROR"),
        ({"username": "root"}, "VALIDATION_ERROR"),
    ],
)
async def test_setup_validates_input(env: Env, body: dict[str, str], code: str) -> None:
    response = await env.client.post("/api/v1/auth/setup", json=body)
    assert_error(response, 422, code)
    assert "short" not in response.text  # the password is never echoed


# -- login ------------------------------------------------------------------------


async def test_login_issues_a_session_token(env: Env) -> None:
    await env.kit.auth.setup("root", PASSWORD)
    response = await login(env, "root")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["username"] == "root"
    assert datetime.fromisoformat(body["expires_at"]) == env.clock.now + TTL
    current = await me(env, bearer(response))
    assert current.status_code == 200
    assert current.json()["id"] == body["user"]["id"]


async def test_login_is_case_insensitive_on_username(env: Env) -> None:
    await env.kit.auth.setup("root", PASSWORD)
    assert (await login(env, "ROOT")).status_code == 200


async def test_invalid_credentials_do_not_reveal_which_part_was_wrong(
    env: Env,
) -> None:
    await env.kit.auth.setup("root", PASSWORD)
    wrong_password = assert_error(
        await login(env, "root", "wrong password!"), 401, "INVALID_CREDENTIALS"
    )
    unknown_user = assert_error(await login(env, "nobody"), 401, "INVALID_CREDENTIALS")
    assert wrong_password["message"] == unknown_user["message"]
    assert wrong_password["details"] == unknown_user["details"] == {}
    response = await login(env, "root", "wrong password!")
    assert response.headers["www-authenticate"] == "Bearer"


async def test_disabled_user_cannot_log_in(env: Env) -> None:
    admin = await env.admin()
    await env.member(admin, "alice")
    alice_id = await user_id(env, admin, "alice")
    await env.client.patch(
        f"/api/v1/users/{alice_id}", json={"disabled": True}, headers=admin
    )
    assert_error(await login(env, "alice"), 401, "INVALID_CREDENTIALS")


async def test_passwords_and_tokens_are_stored_only_as_hashes(
    env: Env, database: Database
) -> None:
    await env.kit.auth.setup("root", PASSWORD)
    token = (await login(env, "root")).json()["token"]
    stored = (await row(database, "SELECT password_hash FROM users"))[0]
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    session = (await row(database, "SELECT token_hash FROM sessions"))[0]
    assert session != token and token not in session


async def test_passwords_and_tokens_never_reach_the_logs(env: Env) -> None:
    with capture_json_logs() as lines:
        await env.client.post(
            "/api/v1/auth/setup", json={"username": "root", "password": PASSWORD}
        )
        token = (await login(env, "root")).json()["token"]
        await login(env, "root", "wrong-but-long-password")
        await env.client.post(
            "/api/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": PASSWORD + "2"},
            headers={"Authorization": f"Bearer {token}"},
        )
    dumped = json.dumps(lines)
    for secret in (PASSWORD, token, "wrong-but-long-password"):
        assert secret not in dumped
    events = {line.get("event") for line in lines}
    assert {"setup_completed", "login_succeeded", "login_failed"} <= events
    # The access log names the user of authenticated requests.
    password_change = next(
        line
        for line in lines
        if line.get("event") == "http_request"
        and line.get("path") == "/api/v1/auth/password"
    )
    assert password_change["user_id"].startswith("user_")


# -- brute force ------------------------------------------------------------------


async def test_repeated_failures_throttle_the_username(env: Env) -> None:
    await env.kit.auth.setup("root", PASSWORD)
    admin = await env.kit.login("root")
    await env.member(admin, "alice")
    for _ in range(5):
        response = await login(env, "root", "wrong password!")
        assert_error(response, 401, "INVALID_CREDENTIALS")
    # Locked: even the right password is refused, before it is checked.
    locked = await login(env, "root")
    assert_error(locked, 429, "LOGIN_THROTTLED")
    assert int(locked.headers["retry-after"]) == 15 * 60 + 1
    assert "token" not in locked.json()
    # Per username: other accounts are unaffected.
    assert (await login(env, "alice")).status_code == 200
    # Existing sessions keep working; the throttle guards logins only.
    assert (await me(env, admin)).status_code == 200

    env.clock.now += timedelta(minutes=15)
    assert (await login(env, "root")).status_code == 200


async def test_parallel_guesses_cannot_outrun_the_throttle(env: Env) -> None:
    await env.kit.auth.setup("root", PASSWORD)
    guesses = await asyncio.gather(
        *(login(env, "root", f"guess number {i:04}") for i in range(20))
    )
    codes = sorted(r.json()["code"] for r in guesses)
    assert codes == ["INVALID_CREDENTIALS"] * 5 + ["LOGIN_THROTTLED"] * 15


async def test_unknown_usernames_are_throttled_too(env: Env) -> None:
    for _ in range(5):
        assert_error(await login(env, "ghost"), 401, "INVALID_CREDENTIALS")
    assert_error(await login(env, "ghost"), 429, "LOGIN_THROTTLED")


# -- sessions ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic cm9vdDpwYXNzd29yZA==",
        "Bearer not-a-real-token",
        "Bearer " + "x" * 300,
        "Token abc",
    ],
)
async def test_missing_or_invalid_token_is_401(
    env: Env, authorization: str | None
) -> None:
    await env.admin()
    headers = {} if authorization is None else {"Authorization": authorization}
    response = await env.client.get("/api/v1/projects", headers=headers)
    assert_error(response, 401, "AUTHENTICATION_REQUIRED")
    assert response.headers["www-authenticate"] == "Bearer"


async def test_session_expires(env: Env) -> None:
    admin = await env.admin()
    env.clock.now += TTL - timedelta(seconds=1)
    assert (await me(env, admin)).status_code == 200
    env.clock.now += timedelta(seconds=1)
    assert_error(await me(env, admin), 401, "AUTHENTICATION_REQUIRED")


async def test_expired_sessions_are_purged_on_login(
    env: Env, database: Database
) -> None:
    await env.admin()
    env.clock.now += TTL * 2
    await env.kit.login("admin")
    assert (await row(database, "SELECT count(*) FROM sessions"))[0] == 1


async def test_logout_revokes_only_that_session(env: Env) -> None:
    first = await env.admin()
    second = await env.kit.login("admin")
    response = await env.client.post("/api/v1/auth/logout", headers=first)
    assert response.status_code == 204
    assert_error(await me(env, first), 401, "AUTHENTICATION_REQUIRED")
    assert (await me(env, second)).status_code == 200
    # Logging out again with the dead token is itself unauthenticated.
    response = await env.client.post("/api/v1/auth/logout", headers=first)
    assert_error(response, 401, "AUTHENTICATION_REQUIRED")


# -- password changes -------------------------------------------------------------


async def change_password(
    env: Env, headers: dict[str, str], current: str, new: str
) -> httpx.Response:
    return await env.client.post(
        "/api/v1/auth/password",
        json={"current_password": current, "new_password": new},
        headers=headers,
    )


async def test_change_password_revokes_other_sessions(env: Env) -> None:
    here = await env.admin()
    elsewhere = await env.kit.login("admin")
    new = "a much better passphrase"
    assert (await change_password(env, here, PASSWORD, new)).status_code == 204
    assert (await me(env, here)).status_code == 200
    assert_error(await me(env, elsewhere), 401, "AUTHENTICATION_REQUIRED")
    assert_error(await login(env, "admin"), 401, "INVALID_CREDENTIALS")
    assert (await login(env, "admin", new)).status_code == 200


async def test_change_password_requires_the_current_password(env: Env) -> None:
    admin = await env.admin()
    response = await change_password(env, admin, "not my password", PASSWORD + "!")
    assert_error(response, 401, "INVALID_CREDENTIALS")
    response = await change_password(env, admin, PASSWORD, "short")
    assert_error(response, 422, "DOMAIN_VALIDATION_ERROR")
    assert (await login(env, "admin")).status_code == 200  # unchanged


# -- user management --------------------------------------------------------------


async def user_id(env: Env, admin: dict[str, str], username: str) -> str:
    users = (await env.client.get("/api/v1/users", headers=admin)).json()["items"]
    return str(next(u["id"] for u in users if u["username"] == username))


async def test_admin_manages_users(env: Env) -> None:
    admin = await env.admin()
    created = await env.client.post(
        "/api/v1/users",
        json={"username": "alice", "password": PASSWORD, "role": "member"},
        headers=admin,
    )
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "member"
    duplicate = await env.client.post(
        "/api/v1/users",
        json={"username": "alice", "password": PASSWORD},
        headers=admin,
    )
    assert_error(duplicate, 409, "USERNAME_TAKEN")
    listed = (await env.client.get("/api/v1/users", headers=admin)).json()
    assert [u["username"] for u in listed["items"]] == ["admin", "alice"]
    assert "password" not in json.dumps(listed)


async def test_disabling_a_user_revokes_their_sessions(env: Env) -> None:
    admin = await env.admin()
    alice = await env.member(admin, "alice")
    alice_id = await user_id(env, admin, "alice")
    response = await env.client.patch(
        f"/api/v1/users/{alice_id}", json={"disabled": True}, headers=admin
    )
    assert response.status_code == 200 and response.json()["disabled"] is True
    assert_error(await me(env, alice), 401, "AUTHENTICATION_REQUIRED")
    await env.client.patch(
        f"/api/v1/users/{alice_id}", json={"disabled": False}, headers=admin
    )
    assert_error(await me(env, alice), 401, "AUTHENTICATION_REQUIRED")  # stays revoked
    assert (await login(env, "alice")).status_code == 200


async def test_password_reset_revokes_sessions(env: Env) -> None:
    admin = await env.admin()
    alice = await env.member(admin, "alice")
    alice_id = await user_id(env, admin, "alice")
    new = "reset by the admin"
    await env.client.patch(
        f"/api/v1/users/{alice_id}", json={"password": new}, headers=admin
    )
    assert_error(await me(env, alice), 401, "AUTHENTICATION_REQUIRED")
    assert (await login(env, "alice", new)).status_code == 200


async def test_last_active_admin_cannot_be_disabled(env: Env) -> None:
    admin = await env.admin()
    admin_id = await user_id(env, admin, "admin")
    url = f"/api/v1/users/{admin_id}"
    response = await env.client.patch(url, json={"disabled": True}, headers=admin)
    assert_error(response, 409, "LAST_ADMIN")

    await env.kit.member_headers(admin, "second", role=Role.ADMIN)
    response = await env.client.patch(url, json={"disabled": True}, headers=admin)
    assert response.status_code == 200
    assert_error(await me(env, admin), 401, "AUTHENTICATION_REQUIRED")


async def test_admins_disabling_each_other_concurrently_keep_one(
    env: Env, database: Database
) -> None:
    a = await env.admin("a-admin")
    b = await env.kit.member_headers(a, "b-admin", role=Role.ADMIN)
    a_id, b_id = await user_id(env, a, "a-admin"), await user_id(env, a, "b-admin")
    results = await asyncio.gather(
        env.client.patch(f"/api/v1/users/{b_id}", json={"disabled": True}, headers=a),
        env.client.patch(f"/api/v1/users/{a_id}", json={"disabled": True}, headers=b),
    )
    assert sorted(r.status_code for r in results) in ([200, 409], [200, 401])
    active = await row(
        database,
        "SELECT count(*) FROM users WHERE role = 'admin' AND NOT disabled",
    )
    assert active[0] == 1


async def test_unknown_user_is_404(env: Env) -> None:
    admin = await env.admin()
    response = await env.client.patch(
        f"/api/v1/users/user_{'0' * 32}", json={"disabled": True}, headers=admin
    )
    assert_error(response, 404, "USER_NOT_FOUND")


# -- default deny -----------------------------------------------------------------

PUBLIC_ROUTES = {
    ("POST", "/api/v1/auth/setup"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/webhooks/{project_id}/{trigger_id}"),
    ("GET", "/health"),
    ("GET", "/ready"),
}


async def test_every_non_public_route_requires_authentication(env: Env) -> None:
    """Default deny: a route added later is protected unless it is added to
    PUBLIC_ROUTES on purpose. Routes come from the OpenAPI document (FastAPI
    resolves included routers lazily); /metrics is hidden from it."""
    await env.admin()
    paths = (await env.client.get("/openapi.json")).json()["paths"]
    routes = {(m.upper(), path) for path, ops in paths.items() for m in ops}
    assert routes >= PUBLIC_ROUTES
    checked = 0
    for method, path in sorted(routes | {("GET", "/metrics")}):
        if (method, path) in PUBLIC_ROUTES:
            continue
        url = re.sub(r"\{[^}]+\}", "x", path)
        response = await env.client.request(method, url)
        assert response.status_code == 401, (method, path, response.text)
        assert response.json()["code"] == "AUTHENTICATION_REQUIRED"
        checked += 1
    assert checked >= 25


# -- admin-only areas -------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/credentials", None),
        ("POST", "/api/v1/credentials", {"name": "crm", "value": "sk-live-secret"}),
        ("GET", "/api/v1/credentials/crm", None),
        ("PATCH", "/api/v1/credentials/crm", {"value": "sk-rotated"}),
        ("DELETE", "/api/v1/credentials/crm", None),
        ("GET", "/api/v1/users", None),
        ("POST", "/api/v1/users", {"username": "eve", "password": PASSWORD}),
        ("PATCH", f"/api/v1/users/user_{'0' * 32}", {"disabled": True}),
    ],
)
async def test_members_cannot_reach_admin_areas(
    env: Env, method: str, path: str, body: dict[str, Any] | None
) -> None:
    admin = await env.admin()
    created = await env.client.post(
        "/api/v1/credentials",
        json={"name": "crm", "value": "sk-live-secret"},
        headers=admin,
    )
    assert created.status_code == 201
    member = await env.member(admin, "alice")
    response = await env.client.request(method, path, json=body, headers=member)
    assert_error(response, 403, "PERMISSION_DENIED")
    assert "sk-live-secret" not in response.text
    # Nothing changed.
    detail = await env.client.get("/api/v1/credentials/crm", headers=admin)
    assert detail.status_code == 200


async def test_metrics_access(env: Env) -> None:
    admin = await env.admin()
    member = await env.member(admin, "alice")
    scrape = {"Authorization": f"Bearer {METRICS_TOKEN}"}
    assert (await env.client.get("/metrics", headers=scrape)).status_code == 200
    assert (await env.client.get("/metrics", headers=admin)).status_code == 200
    assert_error(
        await env.client.get("/metrics", headers=member), 403, "PERMISSION_DENIED"
    )
    assert_error(await env.client.get("/metrics"), 401, "AUTHENTICATION_REQUIRED")
    wrong = {"Authorization": f"Bearer {METRICS_TOKEN}x"}
    assert_error(
        await env.client.get("/metrics", headers=wrong), 401, "AUTHENTICATION_REQUIRED"
    )
    # The scrape token is not a user session.
    assert_error(await me(env, scrape), 401, "AUTHENTICATION_REQUIRED")


# -- ownership boundary -----------------------------------------------------------


@dataclass
class Owned:
    project_id: str
    trigger_id: str
    webhook_id: str
    execution_id: str


async def owned_project(env: Env, owner: dict[str, str]) -> Owned:
    """A published project with an API and a webhook trigger, one execution."""
    trigger_id, webhook_id = new_id("trig"), new_id("trig")
    created = await env.client.post(
        "/api/v1/projects", json={"name": "private"}, headers=owner
    )
    pid = created.json()["id"]
    draft = draft_with(
        [
            {"id": trigger_id, "trigger_type": "api"},
            {"id": webhook_id, "trigger_type": "webhook"},
        ]
    )
    updated = await env.client.put(
        f"/api/v1/projects/{pid}",
        json={"expected_revision": 1, "name": "private", "draft": draft},
        headers=owner,
    )
    assert updated.status_code == 200, updated.text
    published = await env.client.post(f"/api/v1/projects/{pid}/publish", headers=owner)
    assert published.status_code == 201, published.text
    run = await env.client.post(
        f"/api/v1/projects/{pid}/versions/1/execute", json={}, headers=owner
    )
    assert run.status_code == 201, run.text
    return Owned(pid, trigger_id, webhook_id, run.json()["id"])


def scoped_requests(o: Owned) -> list[tuple[str, str, dict[str, Any]]]:
    """Every project/execution-scoped operation, as (method, url, kwargs)."""
    p = f"/api/v1/projects/{o.project_id}"
    return [
        ("GET", p, {}),
        ("PUT", p, {"json": {"expected_revision": 3, "name": "x", "draft": {}}}),
        ("DELETE", p, {}),
        ("POST", f"{p}/validate", {}),
        ("POST", f"{p}/publish", {}),
        ("GET", f"{p}/export", {}),
        ("GET", f"{p}/export?version=1", {}),
        ("GET", f"{p}/versions", {}),
        ("GET", f"{p}/versions/1", {}),
        ("POST", f"{p}/versions/1/execute", {"json": {}}),
        ("POST", f"{p}/triggers/{o.trigger_id}/invoke", {"json": {}}),
        ("GET", f"/api/v1/executions/{o.execution_id}", {}),
        ("GET", f"/api/v1/reports/executions/{o.execution_id}", {}),
    ]


async def test_members_cannot_see_or_touch_other_members_projects(
    env: Env, database: Database
) -> None:
    admin = await env.admin()
    alice = await env.member(admin, "alice")
    bob = await env.member(admin, "bob")
    owned = await owned_project(env, alice)
    before = await row(
        database,
        "SELECT revision, current_version FROM projects WHERE id = :id",
        id=owned.project_id,
    )
    executions_before = (await row(database, "SELECT count(*) FROM executions"))[0]

    for method, url, kwargs in scoped_requests(owned):
        response = await env.client.request(method, url, headers=bob, **kwargs)
        # 404, not 403: bob cannot even learn the ids exist.
        code = "EXECUTION_NOT_FOUND" if "executions/" in url else "PROJECT_NOT_FOUND"
        assert_error(response, 404, code)
        assert "private" not in response.text

    listed = (await env.client.get("/api/v1/projects", headers=bob)).json()
    assert listed["items"] == []
    after = await row(
        database,
        "SELECT revision, current_version FROM projects WHERE id = :id",
        id=owned.project_id,
    )
    assert tuple(after) == tuple(before)
    assert (await row(database, "SELECT count(*) FROM executions"))[0] == (
        executions_before
    )


async def test_owner_and_admin_have_access(env: Env) -> None:
    admin = await env.admin()
    alice = await env.member(admin, "alice")
    owned = await owned_project(env, alice)
    for headers in (alice, admin):
        for method, url, kwargs in scoped_requests(owned):
            if method in ("PUT", "DELETE", "POST") and not url.endswith(
                ("/validate", "/execute", "/invoke")
            ):
                continue  # mutations are covered by the project API tests
            response = await env.client.request(method, url, headers=headers, **kwargs)
            assert response.status_code in (200, 201), (url, response.text)
    alice_ids = [
        p["id"]
        for p in (await env.client.get("/api/v1/projects", headers=alice)).json()[
            "items"
        ]
    ]
    assert alice_ids == [owned.project_id]
    admin_projects = (await env.client.get("/api/v1/projects", headers=admin)).json()
    assert [p["owner_id"] for p in admin_projects["items"]] == [
        (await me(env, alice)).json()["id"]
    ]


async def test_imported_projects_belong_to_the_importer(env: Env) -> None:
    admin = await env.admin()
    alice = await env.member(admin, "alice")
    bob = await env.member(admin, "bob")
    owned = await owned_project(env, alice)
    package = await env.client.get(
        f"/api/v1/projects/{owned.project_id}/export", headers=alice
    )
    imported = await env.client.post(
        "/api/v1/projects/import",
        content=package.content,
        headers={**bob, "content-type": "application/zip"},
    )
    assert imported.status_code == 201, imported.text
    copy = imported.json()["project"]
    assert copy["owner_id"] == (await me(env, bob)).json()["id"]
    response = await env.client.get(f"/api/v1/projects/{copy['id']}", headers=alice)
    assert_error(response, 404, "PROJECT_NOT_FOUND")


async def test_ownerless_projects_are_admin_only(
    env: Env, node_definitions: dict[str, NodeDefinition], database: Database
) -> None:
    """Projects from before accounts existed (owner NULL after 0008)."""
    admin = await env.admin()
    alice = await env.member(admin, "alice")
    legacy = await ProjectService(
        lambda: SqlAlchemyUnitOfWork(database), node_definitions
    ).create_project("legacy")
    url = f"/api/v1/projects/{legacy.id}"
    assert_error(await env.client.get(url, headers=alice), 404, "PROJECT_NOT_FOUND")
    assert (await env.client.get(url, headers=admin)).status_code == 200


async def test_webhooks_stay_public(env: Env) -> None:
    admin = await env.admin()
    owned = await owned_project(env, admin)
    response = await env.client.post(
        f"/api/v1/webhooks/{owned.project_id}/{owned.webhook_id}", json={"a": 1}
    )
    assert response.status_code == 202, response.text
