"""What every signed-in user may know about the installation: its limits,
its LLM providers and its credential names (for pickers), through the real
HTTP stack and PostgreSQL. Never values, URLs or descriptions."""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI

from akasha.api.app import create_app
from akasha.application.ai import AIService, LLMProvider
from akasha.application.credentials import CredentialService
from akasha.bootstrap import Container
from akasha.config import Settings
from akasha.domain.credentials import CredentialKind, Secret
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.nodes.builtins import create_builtin_registry
from akasha.providers.litellm_adapter import LiteLLMAdapter
from akasha.security.crypto import SecretCipher
from tests.integration.fixtures import AuthKit, make_auth_kit

pytestmark = pytest.mark.usefixtures("clean_db")

SECRET = "sk-live-0123456789abcdefghijklmnop"


@dataclass
class Env:
    app: FastAPI
    kit: AuthKit
    admin: dict[str, str]
    member: dict[str, str]
    credentials: CredentialService

    def client(self, headers: dict[str, str] | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
            headers=headers or {},
        )


@pytest.fixture
async def env(database: Database) -> AsyncIterator[Env]:
    settings = Settings(
        environment="test",
        log_level="CRITICAL",
        limit_execution_timeout_seconds=1800,
        limit_node_timeout_seconds=60,
        llm_timeout_seconds=45,
        limit_llm_output_tokens=2048,
    )
    credentials = CredentialService(
        lambda: SqlAlchemyUnitOfWork(database), SecretCipher(os.urandom(32))
    )
    ai = AIService(
        [
            LLMProvider("openai", LiteLLMAdapter("openai")),
            LLMProvider(
                "vllm",
                LiteLLMAdapter("openai", api_base="http://10.0.0.5:8000/v1"),
                requires_credential=False,
            ),
        ]
    )
    kit = make_auth_kit(database)
    container = Container(
        settings=settings,
        nodes=create_builtin_registry(),
        database=database,
        credentials=credentials,
        ai=ai,
        **kit.container_fields(),
    )
    app = create_app(settings=settings, container=container)
    admin = await kit.admin_headers()
    member = await kit.member_headers(admin, "mia")
    yield Env(app, kit, admin, member, credentials)


async def test_limits_are_the_configured_ones(env: Env) -> None:
    async with env.client(env.member) as api:
        body = (await api.get("/api/v1/system/limits")).json()
    assert body["execution_timeout_seconds"] == 1800
    assert body["node_timeout_seconds"] == 60
    # A test run is capped by the node timeout when that is shorter.
    assert body["node_test_timeout_seconds"] == 60
    assert body["llm_timeout_seconds"] == 45
    assert body["llm_max_output_tokens"] == 2048
    assert body["node_max_attempts"] == 5


async def test_llm_providers_are_names_and_flags_only(env: Env) -> None:
    async with env.client(env.member) as api:
        response = await api.get("/api/v1/system/llm-providers")
    assert response.json() == [
        {"name": "openai", "requires_credential": True},
        {"name": "vllm", "requires_credential": False},
    ]
    assert "10.0.0.5" not in response.text  # no endpoint URLs


async def test_credential_names_for_every_user_without_secrets(env: Env) -> None:
    await env.credentials.create(
        "crm-api",
        kind=CredentialKind.API_KEY,
        value=Secret(SECRET),
        description="prod key, ask Lea",
    )
    async with env.client(env.member) as api:
        names = await api.get("/api/v1/credential-names")
        # The full credential API stays admin-only.
        assert (await api.get("/api/v1/credentials")).status_code == 403
    assert names.status_code == 200
    assert names.json()["items"] == [{"name": "crm-api", "kind": "api_key"}]
    assert SECRET not in names.text
    assert "ask Lea" not in names.text


async def test_all_need_a_session(env: Env) -> None:
    async with env.client(None) as api:
        for path in ("system/limits", "system/llm-providers", "credential-names"):
            assert (await api.get(f"/api/v1/{path}")).status_code == 401
