"""Personal access tokens through the real HTTP stack and PostgreSQL
(nothing mocked but the clock): issuing (password required, value shown
once, hash stored), authenticating as the owning user, expiry, revocation
by the owner, logout, admin disable and reset, limits and log hygiene."""

from datetime import timedelta
from typing import Any

import pytest

from akasha.application.auth import MAX_ACCESS_TOKENS
from akasha.domain.identity import Role
from akasha.infrastructure.database import Database
from tests.integration.fixtures import PASSWORD
from tests.integration.test_auth_api import (
    Env,
    assert_error,
    env,
    me,
    row,
)
from tests.support import capture_json_logs

pytestmark = pytest.mark.usefixtures("clean_db")

TOKENS = "/api/v1/auth/tokens"


async def issue(
    env: Env, headers: dict[str, str], name: str = "agent", **body: Any
) -> dict[str, Any]:
    response = await env.client.post(
        TOKENS, json={"name": name, "password": PASSWORD, **body}, headers=headers
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def pat(created: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {created['token']}"}


async def test_a_token_is_shown_once_and_stored_only_as_a_hash(
    env: Env, database: Database
) -> None:
    admin = await env.admin()
    created = await issue(env, admin, "claude-laptop", expires_in_days=30)
    value = created["token"]
    assert value.startswith("akasha_pat_") and len(value) > 50
    assert created["hint"] == value[:15]
    assert created["name"] == "claude-laptop"
    assert created["last_used_at"] is None
    assert created["expires_at"] is not None

    listed = (await env.client.get(TOKENS, headers=admin)).json()["items"]
    assert [t["id"] for t in listed] == [created["id"]]
    assert "token" not in listed[0]
    assert value not in str(listed)
    stored = await row(database, "SELECT token_hash, hint FROM access_tokens")
    assert stored.token_hash != value and value not in stored.token_hash
    assert stored.hint == created["hint"]


async def test_a_token_authenticates_as_its_user_with_that_users_access(
    env: Env,
) -> None:
    admin = await env.admin()
    member = await env.member(admin, "bob")
    created = await issue(env, member, "bot")
    who = await me(env, pat(created))
    assert who.status_code == 200
    assert (who.json()["username"], who.json()["role"]) == ("bob", Role.MEMBER)
    # No more than the user: admin areas stay closed.
    response = await env.client.get("/api/v1/users", headers=pat(created))
    assert_error(response, 403, "PERMISSION_DENIED")
    workflow = await env.client.post(
        "/api/v1/workflows", json={"name": "admins"}, headers=admin
    )
    hidden = await env.client.get(
        f"/api/v1/workflows/{workflow.json()['id']}", headers=pat(created)
    )
    assert_error(hidden, 404, "WORKFLOW_NOT_FOUND")
    mine = await env.client.post(
        "/api/v1/workflows", json={"name": "bots"}, headers=pat(created)
    )
    assert mine.status_code == 201
    assert mine.json()["owner_id"] == who.json()["id"]


async def test_issuing_needs_the_account_password(env: Env) -> None:
    admin = await env.admin()
    wrong = await env.client.post(
        TOKENS,
        json={"name": "x", "password": "not the password at all"},
        headers=admin,
    )
    assert_error(wrong, 401, "INVALID_CREDENTIALS")
    # A token alone cannot mint more tokens either.
    created = await issue(env, admin)
    again = await env.client.post(
        TOKENS,
        json={"name": "y", "password": "guess guess guess"},
        headers=pat(created),
    )
    assert_error(again, 401, "INVALID_CREDENTIALS")
    assert len((await env.client.get(TOKENS, headers=admin)).json()["items"]) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "x" * 101},
        {"name": "tab\tname"},
        {"name": "ok", "expires_in_days": 0},
        {"name": "ok", "expires_in_days": 3651},
    ],
)
async def test_invalid_token_requests_are_rejected(
    env: Env, body: dict[str, Any]
) -> None:
    admin = await env.admin()
    response = await env.client.post(
        TOKENS, json={"password": PASSWORD, **body}, headers=admin
    )
    assert response.status_code == 422, response.text


async def test_tokens_expire_or_live_until_revoked(env: Env) -> None:
    admin = await env.admin()
    short = await issue(env, admin, "short", expires_in_days=1)
    forever = await issue(env, admin, "forever", expires_in_days=None)
    assert forever["expires_at"] is None
    session = await env.client.get("/api/v1/auth/session", headers=pat(forever))
    assert session.json()["expires_at"] is None
    session = await env.client.get("/api/v1/auth/session", headers=pat(short))
    assert session.json()["expires_at"] == short["expires_at"]

    env.clock.now += timedelta(days=1) - timedelta(seconds=1)
    assert (await me(env, pat(short))).status_code == 200
    env.clock.now += timedelta(seconds=1)
    assert_error(await me(env, pat(short)), 401, "AUTHENTICATION_REQUIRED")
    env.clock.now += timedelta(days=3000)
    assert (await me(env, pat(forever))).status_code == 200


async def test_revocation_takes_effect_immediately_and_only_for_the_owner(
    env: Env,
) -> None:
    admin = await env.admin()
    member = await env.member(admin, "bob")
    mine = await issue(env, member, "bot")
    others = await issue(env, admin, "admin-bot")

    # Another user's token (even an admin's) is "not found" to a member,
    # and admins cannot revoke a member's token through this route either.
    response = await env.client.delete(f"{TOKENS}/{others['id']}", headers=member)
    assert_error(response, 404, "ACCESS_TOKEN_NOT_FOUND")
    response = await env.client.delete(f"{TOKENS}/{mine['id']}", headers=admin)
    assert_error(response, 404, "ACCESS_TOKEN_NOT_FOUND")
    assert (await me(env, pat(others))).status_code == 200

    response = await env.client.delete(f"{TOKENS}/{mine['id']}", headers=member)
    assert response.status_code == 204
    assert_error(await me(env, pat(mine)), 401, "AUTHENTICATION_REQUIRED")
    assert (await env.client.get(TOKENS, headers=member)).json()["items"] == []


async def test_logout_with_a_token_revokes_that_token_only(env: Env) -> None:
    admin = await env.admin()
    first, second = await issue(env, admin, "a"), await issue(env, admin, "b")
    response = await env.client.post("/api/v1/auth/logout", headers=pat(first))
    assert response.status_code == 204
    assert_error(await me(env, pat(first)), 401, "AUTHENTICATION_REQUIRED")
    assert (await me(env, pat(second))).status_code == 200
    assert (await me(env, admin)).status_code == 200


async def test_disable_and_admin_reset_end_every_token(
    env: Env, database: Database
) -> None:
    admin = await env.admin()
    member = await env.member(admin, "bob")
    created = await issue(env, member)
    bob = (await me(env, member)).json()["id"]

    await env.client.patch(
        f"/api/v1/users/{bob}", json={"disabled": True}, headers=admin
    )
    assert_error(await me(env, pat(created)), 401, "AUTHENTICATION_REQUIRED")
    await env.client.patch(
        f"/api/v1/users/{bob}", json={"disabled": False}, headers=admin
    )
    assert_error(await me(env, pat(created)), 401, "AUTHENTICATION_REQUIRED")

    member = await env.kit.login("bob")
    created = await issue(env, member)
    reset = await env.client.patch(
        f"/api/v1/users/{bob}", json={"password": "a brand new password"}, headers=admin
    )
    assert reset.status_code == 200
    assert_error(await me(env, pat(created)), 401, "AUTHENTICATION_REQUIRED")
    assert (await row(database, "SELECT count(*) FROM access_tokens"))[0] == 0


async def test_own_password_change_keeps_tokens(env: Env) -> None:
    admin = await env.admin()
    created = await issue(env, admin)
    response = await env.client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "another long password"},
        headers=admin,
    )
    assert response.status_code == 204
    assert (await me(env, pat(created))).status_code == 200


async def test_last_use_is_recorded_hourly(env: Env) -> None:
    admin = await env.admin()
    created = await issue(env, admin, expires_in_days=None)

    async def last_used() -> str:
        # Listed with the token itself (a use, too): the admin's login
        # session does not outlive the clock jumps below.
        items = (await env.client.get(TOKENS, headers=pat(created))).json()["items"]
        value: str = items[0]["last_used_at"]
        return value

    first = await last_used()
    assert first is not None
    env.clock.now += timedelta(minutes=30)
    assert await last_used() == first
    env.clock.now += timedelta(minutes=31)
    assert await last_used() > first


async def test_a_user_holds_a_bounded_number_of_tokens(env: Env) -> None:
    admin = await env.admin()
    identity = await env.kit.auth.authenticate(admin["Authorization"][7:])
    for i in range(MAX_ACCESS_TOKENS):
        await env.kit.auth.create_access_token(
            identity, name=f"t{i}", password=PASSWORD, expires_in=None
        )
    response = await env.client.post(
        TOKENS, json={"name": "one too many", "password": PASSWORD}, headers=admin
    )
    assert_error(response, 409, "ACCESS_TOKEN_LIMIT")


async def test_unknown_and_malformed_tokens_are_401(env: Env) -> None:
    await env.admin()
    for value in ("akasha_pat_" + "x" * 43, "akasha_pat_", "akasha_pat_" + "y" * 300):
        response = await me(env, {"Authorization": f"Bearer {value}"})
        assert_error(response, 401, "AUTHENTICATION_REQUIRED")


async def test_token_values_never_reach_the_logs(env: Env) -> None:
    admin = await env.admin()
    with capture_json_logs() as logs:
        created = await issue(env, admin)
        await me(env, pat(created))
        await env.client.delete(f"{TOKENS}/{created['id']}", headers=admin)
    events = [line.get("event") for line in logs]
    assert "access_token_created" in events and "access_token_revoked" in events
    assert created["token"] not in str(logs)
