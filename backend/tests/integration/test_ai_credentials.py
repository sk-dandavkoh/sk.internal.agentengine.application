"""Provider credentials through the real chain (Phase 14):

    Credential Store (PostgreSQL, encrypted) → NodeCredentials → AIService
    → LiteLLM adapter → local OpenAI-compatible server

The key reaches the provider's Authorization header and nothing else.
"""

import os

import pytest

from akasha.application.ai import AIService, LLMProvider
from akasha.application.credentials import CredentialService
from akasha.domain.ai import LLMRequest, Message, ProviderError, ProviderErrorKind, Role
from akasha.domain.credentials import CredentialKind, Secret
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.providers.litellm_adapter import LiteLLMAdapter
from akasha.runtime.credentials import NodeCredentials
from akasha.security.crypto import SecretCipher
from akasha.security.redaction import MASK
from tests.support import LocalHttpServer

pytestmark = pytest.mark.usefixtures("clean_db")

# Shapeless on purpose: only known-value redaction can mask it.
CANARY = "canary-llm-5d1e-never-leak"
REF = "credential:openai-prod"


Setup = tuple[AIService, NodeCredentials]


@pytest.fixture
async def setup(database: Database, http_server: LocalHttpServer) -> Setup:
    store = CredentialService(
        lambda: SqlAlchemyUnitOfWork(database), SecretCipher(os.urandom(32))
    )
    await store.create(
        "openai-prod", kind=CredentialKind.PROVIDER, value=Secret(CANARY)
    )
    adapter = LiteLLMAdapter("openai", api_base=f"{http_server.base_url}/v1")
    # The node's frozen config is what authorizes the reference.
    return AIService([LLMProvider("openai", adapter)]), NodeCredentials(
        store, {"credential": REF}
    )


def request() -> LLMRequest:
    return LLMRequest("openai", "gpt-test", (Message(Role.USER, "Hi"),), credential=REF)


async def test_resolved_key_is_sent_to_the_provider(
    setup: Setup, http_server: LocalHttpServer
) -> None:
    service, resolver = setup
    response = await service.complete(request(), credentials=resolver)
    assert response.content == "Hello!"
    (recorded,) = http_server.state.requests
    assert recorded.headers["authorization"] == f"Bearer {CANARY}"
    assert CANARY not in recorded.body.decode()


async def test_provider_error_echoing_the_key_is_masked(
    setup: Setup, http_server: LocalHttpServer
) -> None:
    service, resolver = setup
    http_server.state.llm_status = 401
    http_server.state.llm_body = {
        "error": {"message": f"bad key {CANARY}", "type": "x"}
    }
    with pytest.raises(ProviderError) as exc:
        await service.complete(request(), credentials=resolver)
    assert exc.value.kind is ProviderErrorKind.AUTHENTICATION
    assert CANARY not in exc.value.message
    assert MASK in exc.value.message
