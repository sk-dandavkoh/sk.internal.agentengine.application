"""Project sharing end to end: the real HTTP stack and PostgreSQL.

Viewers read, editors also change, only the owner and admins share and
delete; access follows every change on the next request; listings include
shared projects; the members API keeps its own invariants; and an editor
cannot use an event trigger to read a project only the owner can see.
"""

import asyncio
import json
from typing import Any

import pytest
from sqlalchemy import text

from akasha.application import sharing
from akasha.infrastructure.database import Database
from tests.integration import test_auth_api
from tests.integration.test_auth_api import (
    Env,
    Owned,
    assert_error,
    owned_project,
    row,
    scoped_requests,
)
from tests.support import capture_json_logs

pytestmark = pytest.mark.usefixtures("clean_db")
# The auth tests' environment fixture: same app, services and clock.
env = test_auth_api.env

# The access each scoped request needs (api.auth.requires): reads view,
# writes edit, deleting manage. Keyed by (method, path suffix).
EDIT_SUFFIXES = ("/publish", "/execute", "/invoke", "/cancel")


def needed(method: str, url: str) -> str:
    if method == "DELETE":
        return "manage"
    if method == "GET" or url.endswith(("/validate", "/duplicate")):
        return "view"
    assert method == "PUT" or url.endswith(EDIT_SUFFIXES), (method, url)
    return "edit"


def members_url(project_id: str, username: str = "") -> str:
    base = f"/api/v1/projects/{project_id}/members"
    return f"{base}/{username}" if username else base


async def share(
    env: Env, owner: dict[str, str], project_id: str, username: str, role: str
) -> dict[str, Any]:
    response = await env.client.put(
        members_url(project_id, username), json={"role": role}, headers=owner
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def setup(env: Env) -> tuple[dict[str, str], dict[str, str], Owned]:
    """alice owns a published project with one run; bob is another member."""
    admin = await env.admin()
    alice = await env.member(admin, "alice")
    bob = await env.member(admin, "bob")
    return alice, bob, await owned_project(env, alice)


@pytest.mark.parametrize("role", ["viewer", "editor"])
async def test_each_role_reaches_exactly_its_level(env: Env, role: str) -> None:
    alice, bob, owned = await setup(env)
    await share(env, alice, owned.project_id, "bob", role)
    allowed = {"viewer": {"view"}, "editor": {"view", "edit"}}[role]
    for method, url, kwargs in scoped_requests(owned):
        response = await env.client.request(method, url, headers=bob, **kwargs)
        if needed(method, url) in allowed:
            # Reached the operation (its own 2xx/409/422), never refused.
            assert response.status_code not in (401, 403, 404), (method, url)
        else:
            body = assert_error(response, 403, "PERMISSION_DENIED")
            assert body["details"]["access"] == {"viewer": "view"}.get(role, "edit")


async def test_listings_include_shared_projects(env: Env) -> None:
    alice, bob, owned = await setup(env)
    assert (await env.client.get("/api/v1/projects", headers=bob)).json()["items"] == []
    await share(env, alice, owned.project_id, "bob", "viewer")
    projects = (await env.client.get("/api/v1/projects", headers=bob)).json()
    assert [p["id"] for p in projects["items"]] == [owned.project_id]
    runs = (await env.client.get("/api/v1/executions", headers=bob)).json()
    assert [r["id"] for r in runs["items"]] == [owned.execution_id]
    counts = (
        await env.client.get("/api/v1/executions/status-counts", headers=bob)
    ).json()["counts"]
    assert sum(counts.values()) == 1


async def test_access_follows_changes_on_the_next_request(env: Env) -> None:
    alice, bob, owned = await setup(env)
    url = f"/api/v1/projects/{owned.project_id}"
    publish = f"{url}/publish"
    await share(env, alice, owned.project_id, "bob", "viewer")
    assert_error(await env.client.post(publish, headers=bob), 403, "PERMISSION_DENIED")
    await share(env, alice, owned.project_id, "bob", "editor")
    assert (await env.client.post(publish, headers=bob)).status_code == 201
    removed = await env.client.delete(
        members_url(owned.project_id, "bob"), headers=alice
    )
    assert removed.status_code == 204
    # Gone entirely: not even its existence is visible again.
    assert_error(await env.client.get(url, headers=bob), 404, "PROJECT_NOT_FOUND")


async def test_members_api_shows_owner_members_and_own_access(env: Env) -> None:
    alice, bob, owned = await setup(env)
    first = await share(env, alice, owned.project_id, "bob", "viewer")
    changed = await share(env, alice, owned.project_id, "bob", "editor")
    assert changed["role"] == "editor"
    assert changed["created_at"] == first["created_at"]  # a change, not a re-add

    as_owner = (
        await env.client.get(members_url(owned.project_id), headers=alice)
    ).json()
    assert as_owner["access"] == "manage"
    assert as_owner["owner"]["username"] == "alice"
    assert [(m["username"], m["role"]) for m in as_owner["members"]] == [
        ("bob", "editor")
    ]
    as_bob = (await env.client.get(members_url(owned.project_id), headers=bob)).json()
    assert as_bob["access"] == "edit"
    admin = await env.kit.login("admin")
    as_admin = (
        await env.client.get(members_url(owned.project_id), headers=admin)
    ).json()
    assert as_admin["access"] == "manage"
    for body in (as_owner, as_bob, as_admin):
        assert "password" not in json.dumps(body)


async def test_only_the_owner_and_admins_share_and_delete(env: Env) -> None:
    alice, bob, owned = await setup(env)
    carol = await env.member(await env.kit.login("admin"), "carol")
    await share(env, alice, owned.project_id, "bob", "editor")
    for method, kwargs in (("PUT", {"json": {"role": "editor"}}), ("DELETE", {})):
        response = await env.client.request(
            method, members_url(owned.project_id, "carol"), headers=bob, **kwargs
        )
        assert_error(response, 403, "PERMISSION_DENIED")
    # Not even allowed to promote oneself.
    response = await env.client.put(
        members_url(owned.project_id, "bob"), json={"role": "editor"}, headers=carol
    )
    assert_error(response, 404, "PROJECT_NOT_FOUND")
    admin = await env.kit.login("admin")
    await share(env, admin, owned.project_id, "carol", "viewer")


async def test_member_invariants(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    alice, _, owned = await setup(env)
    await env.member(await env.kit.login("admin"), "carol")
    put = members_url(owned.project_id, "nobody")
    assert_error(
        await env.client.put(put, json={"role": "viewer"}, headers=alice),
        404,
        "USER_NOT_FOUND",
    )
    assert_error(
        await env.client.put(
            members_url(owned.project_id, "alice"),
            json={"role": "viewer"},
            headers=alice,
        ),
        409,
        "PROJECT_OWNER",
    )
    assert_error(
        await env.client.delete(members_url(owned.project_id, "carol"), headers=alice),
        404,
        "MEMBER_NOT_FOUND",
    )
    invalid = await env.client.put(
        members_url(owned.project_id, "carol"), json={"role": "owner"}, headers=alice
    )
    assert invalid.status_code == 422
    monkeypatch.setattr(sharing, "MAX_PROJECT_MEMBERS", 1)
    await share(env, alice, owned.project_id, "bob", "viewer")
    await share(env, alice, owned.project_id, "bob", "editor")  # a change still fits
    assert_error(
        await env.client.put(
            members_url(owned.project_id, "carol"),
            json={"role": "viewer"},
            headers=alice,
        ),
        409,
        "MEMBER_LIMIT",
    )


async def test_memberships_go_with_their_project(env: Env, database: Database) -> None:
    admin = await env.admin()
    alice = await env.member(admin, "alice")
    await env.member(admin, "bob")
    created = await env.client.post(
        "/api/v1/projects", json={"name": "draft only"}, headers=alice
    )
    pid = created.json()["id"]
    await share(env, alice, pid, "bob", "editor")
    deleted = await env.client.delete(f"/api/v1/projects/{pid}", headers=alice)
    assert deleted.status_code == 204
    left = await row(
        database, "SELECT count(*) FROM project_members WHERE project_id = :p", p=pid
    )
    assert left[0] == 0


async def test_an_editor_cannot_trigger_on_a_project_only_the_owner_sees(
    env: Env,
) -> None:
    alice, bob, owned = await setup(env)
    private = (
        await env.client.post("/api/v1/projects", json={"name": "b"}, headers=alice)
    ).json()["id"]
    await share(env, alice, owned.project_id, "bob", "editor")
    url = f"/api/v1/projects/{owned.project_id}"
    current = (await env.client.get(url, headers=bob)).json()
    trigger = {
        "id": "trig_" + "1" * 32,
        "trigger_type": "event",
        "config": {"event": "execution.completed", "source_project_id": private},
    }
    draft = {**current["draft"], "triggers": [*current["draft"]["triggers"], trigger]}
    body = {"expected_revision": current["revision"], "name": "x", "draft": draft}
    refused = await env.client.put(url, json=body, headers=bob)
    assert_error(refused, 422, "TRIGGER_SOURCE_NOT_FOUND")

    # The owner may; the editor's later saves keep that source untouched.
    saved = await env.client.put(url, json=body, headers=alice)
    assert saved.status_code == 200, saved.text
    again = saved.json()
    keep = {
        "expected_revision": again["revision"],
        "name": "y",
        "draft": again["draft"],
    }
    assert (await env.client.put(url, json=keep, headers=bob)).status_code == 200


async def test_sharing_and_refusals_are_audited(env: Env) -> None:
    alice, bob, owned = await setup(env)
    with capture_json_logs() as records:
        await share(env, alice, owned.project_id, "bob", "viewer")
        await env.client.post(
            f"/api/v1/projects/{owned.project_id}/publish", headers=bob
        )
    events = {r.get("event"): r for r in records}
    shared = events["project_member_set"]
    assert shared["project_id"] == owned.project_id
    assert shared["role"] == "viewer" and shared["previous"] is None
    denied = events["project_access_denied"]
    assert (denied["access"], denied["need"]) == ("view", "edit")


async def test_project_members_table_constrains_roles(database: Database) -> None:
    async with database.engine.connect() as conn:
        check = await conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_project_members_role'"
            )
        )
        assert "viewer" in check.scalar_one()


# -- ownership transfer and leaving ------------------------------------------------


def owner_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/owner"


def leave_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/membership"


async def test_transfer_makes_the_new_owner_manage_and_keeps_the_old_one(
    env: Env,
) -> None:
    alice, bob, owned = await setup(env)
    await share(env, alice, owned.project_id, "bob", "viewer")
    moved = await env.client.put(
        owner_url(owned.project_id), json={"username": "bob"}, headers=alice
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()
    # As alice sees it now: an editor, no longer the owner.
    assert body["access"] == "edit"
    assert body["owner"]["username"] == "bob"
    assert [(m["username"], m["role"]) for m in body["members"]] == [
        ("alice", "editor")
    ]
    project = (
        await env.client.get(f"/api/v1/projects/{owned.project_id}", headers=bob)
    ).json()
    assert project["owner_id"] == body["owner"]["user_id"]
    # bob now manages: he can share and delete; alice cannot.
    assert_error(
        await env.client.delete(members_url(owned.project_id, "bob"), headers=alice),
        403,
        "PERMISSION_DENIED",
    )
    removed = await env.client.delete(
        members_url(owned.project_id, "alice"), headers=bob
    )
    assert removed.status_code == 204
    assert_error(
        await env.client.get(f"/api/v1/projects/{owned.project_id}", headers=alice),
        404,
        "PROJECT_NOT_FOUND",
    )


async def test_transfer_rules(env: Env) -> None:
    alice, bob, owned = await setup(env)
    admin = await env.kit.login("admin")
    carol = await env.member(admin, "carol")
    url = owner_url(owned.project_id)
    await share(env, alice, owned.project_id, "bob", "editor")
    # Editors cannot give the project away; strangers cannot see it.
    assert_error(
        await env.client.put(url, json={"username": "bob"}, headers=bob),
        403,
        "PERMISSION_DENIED",
    )
    assert_error(
        await env.client.put(url, json={"username": "carol"}, headers=carol),
        404,
        "PROJECT_NOT_FOUND",
    )
    for username, status, code in (
        ("alice", 409, "PROJECT_OWNER"),
        ("nobody", 404, "USER_NOT_FOUND"),
    ):
        response = await env.client.put(url, json={"username": username}, headers=alice)
        assert_error(response, status, code)
    carol_id = (await env.client.get("/api/v1/auth/me", headers=carol)).json()["id"]
    await env.client.patch(
        f"/api/v1/users/{carol_id}", json={"disabled": True}, headers=admin
    )
    assert_error(
        await env.client.put(url, json={"username": "carol"}, headers=alice),
        409,
        "USER_DISABLED",
    )
    # An admin may transfer any project, keeping the owner on as editor.
    moved = await env.client.put(url, json={"username": "bob"}, headers=admin)
    assert moved.status_code == 200
    assert moved.json()["access"] == "manage"
    assert {m["username"] for m in moved.json()["members"]} == {"alice"}


async def test_concurrent_transfers_lose_nobody(env: Env, database: Database) -> None:
    """Two transfers at once serialize on the project row: whoever owned
    the project in between stays on as a member."""
    alice, _, owned = await setup(env)
    admin = await env.kit.login("admin")
    await env.member(admin, "carol")
    results = await asyncio.gather(
        env.client.put(
            owner_url(owned.project_id), json={"username": "bob"}, headers=alice
        ),
        env.client.put(
            owner_url(owned.project_id), json={"username": "carol"}, headers=admin
        ),
    )
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    members = await row(
        database,
        "SELECT count(*) FROM project_members WHERE project_id = :p",
        p=owned.project_id,
    )
    # Three people involved, one owner: the other two are members.
    assert members[0] == 2


async def test_a_member_can_leave_but_the_owner_cannot(env: Env) -> None:
    alice, bob, owned = await setup(env)
    url = leave_url(owned.project_id)
    await share(env, alice, owned.project_id, "bob", "viewer")
    assert (await env.client.delete(url, headers=bob)).status_code == 204
    assert_error(
        await env.client.get(f"/api/v1/projects/{owned.project_id}", headers=bob),
        404,
        "PROJECT_NOT_FOUND",
    )
    # Gone means gone: leaving again is a stranger's request.
    assert_error(await env.client.delete(url, headers=bob), 404, "PROJECT_NOT_FOUND")
    assert_error(await env.client.delete(url, headers=alice), 409, "PROJECT_OWNER")
    admin = await env.kit.login("admin")
    assert_error(await env.client.delete(url, headers=admin), 404, "MEMBER_NOT_FOUND")


async def test_transfer_and_leave_are_audited(env: Env) -> None:
    alice, _, owned = await setup(env)
    with capture_json_logs() as records:
        await env.client.put(
            owner_url(owned.project_id), json={"username": "bob"}, headers=alice
        )
        await env.client.delete(leave_url(owned.project_id), headers=alice)
    events = {r.get("event"): r for r in records}
    moved = events["project_owner_transferred"]
    assert moved["project_id"] == owned.project_id
    assert moved["previous_owner_id"] != moved["owner_id"]
    assert events["project_member_left"]["actor_id"] == moved["previous_owner_id"]
