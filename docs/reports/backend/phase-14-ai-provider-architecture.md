# Phase 14 — AI Provider Architecture

## 1. Executive Summary

**What the phase had to achieve.** Roadmap §18 (Phase 14) asks for provider-independent LLM infrastructure:
- the types `AIService`, `LLMProvider`, `ProviderAdapter`, `LLMRequest`, `LLMResponse`, `Message`, `ToolCall`, `TokenUsage` and `ProviderError`;
- at least one real provider;
- provider-specific code kept behind the abstraction, with LiteLLM allowed as infrastructure.

The phase prompt adds two requirements: provider credentials must go through the Phase 13 credential system, and normal CI must not depend on external AI providers.

**What was implemented.**
- **Domain** (`akasha.domain.ai`): a provider-independent vocabulary of request, response, message, tool call, token usage, finish reason and error types. Nothing in it depends on a provider library.
- **Application** (`akasha.application.ai`):
  - `ProviderAdapter` is a Protocol;
  - `LLMProvider` is a configured provider entry;
  - `AIService` routes requests, resolves credentials through the caller's `CredentialResolver` and enforces timeouts.
- **Domain port** `LLMClient` (`akasha.domain.ai`): a Protocol through which the Phase 15 LLM Node will reach `AIService` without importing the application layer.
- **Providers** (`akasha.providers.litellm_adapter`): `LiteLLMAdapter`, one instance per litellm route. It maps requests, normalizes responses and classifies errors.
- **Composition** (`akasha.bootstrap.create_ai_service`, `Container.ai`): providers `openai` and `anthropic`, plus `ollama` and one generic OpenAI-compatible endpoint when configured.
- **Configuration:** five new settings: `AKASHA_LLM_TIMEOUT_SECONDS`, `AKASHA_OLLAMA_BASE_URL`, `AKASHA_OPENAI_COMPATIBLE_BASE_URL`, `AKASHA_OPENAI_COMPATIBLE_NAME` and `AKASHA_OPENAI_COMPATIBLE_REQUIRES_CREDENTIAL`.
- **Dependency:** `litellm~=1.103.1`.
- **Tests:** 53 new deterministic tests at four levels:
  - unit (domain);
  - fake-adapter (service);
  - component/contract (the real litellm stack against a loopback OpenAI-compatible server);
  - PostgreSQL integration (the credential chain).
- **Opt-in external smoke test:** 3 tests against OpenRouter. They were run once by hand and passed.

**Resulting system state.** `AIService` routes a provider-independent `LLMRequest` to the adapter selected by the provider name. No layer outside `akasha.providers` imports `litellm`, `openai` or `anthropic`, and an AST test enforces this. Nothing in the runtime or nodes consumes `Container.ai` yet; the LLM Node is Phase 15.

**Acceptance criteria.** The single roadmap criterion is satisfied: *"The domain/runtime does not depend on a specific LLM provider."* The evidence is in §14.

**Limitations.** The live smoke test used litellm's `openrouter` route. OpenRouter can now also be configured as the generic OpenAI-compatible provider, but that configuration (the `openai` route with a custom `api_base`) has only been verified against the loopback server, not live. The built-in routes `openai`, `anthropic` and `ollama` have not been exercised against their real services. Ollama's wire format is not exercised at all.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Roadmap §18 (Phase 14 — AI Provider Architecture):

- **Goal:** implement provider-independent LLM infrastructure.
- **Scope:**
  - create `AIService`, `LLMProvider`, `ProviderAdapter`, `LLMRequest`, `LLMResponse`, `Message`, `ToolCall`, `TokenUsage` and `ProviderError`;
  - support at least one real provider;
  - keep provider-specific implementation behind the abstraction;
  - LiteLLM may be used as an infrastructure abstraction.
- **Tests:** mocked provider adapters. Test request mapping, response normalization, usage extraction, error normalization and timeout behavior.
- **Acceptance:** the domain and runtime do not depend on a specific LLM provider.

The phase prompt adds:
- use the `litellm` package;
- provider credentials must use the credential system;
- tests must cover tool calls, provider configuration and credential resolution;
- normal CI must not depend on external AI providers.

**Explicitly not in this phase** (roadmap §19, Phase 15 — LLM Node):
- the LLM Node itself;
- prompt rendering and variables;
- structured output;
- token cost estimation.

Architecture §28 also lists streaming, and §134 lists provider capability and pricing metadata. The roadmap assigns neither to Phase 14.

---

## 3. Scope Implemented

### Domain (`backend/src/akasha/domain/ai.py`)
- **`Role`** (StrEnum): `system`, `user`, `assistant`, `tool`.
- **`ToolCall`** (frozen): `id`, `name`, `arguments: Mapping[str, Any]`. Arguments are already parsed from JSON.
- **`Message`** (frozen): `role`, `content`, `tool_calls`, `tool_call_id`. Invariants:
  - only assistant messages carry tool calls;
  - `tool_call_id` appears on tool messages and only there;
  - a message has content or tool calls;
  - `role` is coerced to `Role`.
- **`ToolDefinition`** (frozen): `name`, `description`, `parameters` (JSON Schema).
- **`LLMRequest`** (frozen): `provider`, `model`, `messages`, `temperature`, `max_tokens`, `tools`, `credential`, `timeout_seconds`. Validation:
  - provider and model are non-empty;
  - there is at least one message;
  - temperature is between 0 and 2;
  - `max_tokens` ≥ 1;
  - `timeout_seconds` > 0;
  - `credential` must be a credential reference (`credential:<name>`); a raw key is rejected with `DomainValidationError`.
- **`TokenUsage`** (frozen): `input_tokens`, `output_tokens`, and a `total_tokens` property.
- **`FinishReason`** (StrEnum): `stop`, `length`, `tool_calls`, `content_filter`, `other`.
- **`LLMResponse`** (frozen): `provider`, `model` (as the provider reports it), `content`, `tool_calls`, `usage`, `finish_reason`, `request_id`.
- **`ProviderErrorKind`** (StrEnum), 10 categories: `authentication`, `rate_limit`, `timeout`, `context_length`, `invalid_request`, `content_policy`, `unavailable`, `invalid_response`, `configuration`, `unknown`.
- **`RETRYABLE_PROVIDER_ERRORS`**: `rate_limit`, `timeout`, `unavailable`.
- **`ProviderError(AkashaError)`**:
  - `code = PROVIDER_<KIND>`, `http_status = 502`;
  - `details = {"provider", "kind"}`;
  - attributes `kind`, `provider` and `retryable`.

- **`LLMClient`** (Protocol): `async complete(request, *, credentials: CredentialResolver | None = None) -> LLMResponse`. This is the domain port for nodes (§5.4). `domain/ai.py` now imports `CredentialResolver` from `akasha.domain.node`; there is no import cycle.

### Application (`backend/src/akasha/application/ai.py`)
- **`ProviderAdapter`** (Protocol): `async complete(request, *, api_key: str | None, timeout_seconds: float) -> LLMResponse`.
- **`LLMProvider`** (frozen dataclass): `name`, `adapter`, `requires_credential=True`, `timeout_seconds=60.0`.
- **`AIService(providers)`**: construction rejects duplicate names and non-positive timeouts with `ConfigurationError`. It exposes `provider_names` (sorted) and:
  - `async complete(request, *, credentials: CredentialResolver | None = None) -> LLMResponse`.

### Providers (`backend/src/akasha/providers/litellm_adapter.py`)
- **`LiteLLMAdapter(route, *, api_base=None)`**, with methods `complete(...)` and `request_kwargs(...)`.
- **`NO_API_KEY = "no-key"`**: sent instead of `None` for keyless providers, so litellm cannot fall back to environment keys (F5).
- Private helpers:
  - `_litellm()`: a cached lazy import;
  - `_message()`: domain message → OpenAI/litellm message dict;
  - `_normalize()`: litellm `ModelResponse` → `LLMResponse`;
  - `_tool_call()`: parses JSON arguments;
  - `_provider_error()` / `_classify()`: exception → `ProviderErrorKind`.

### Composition / configuration
- `backend/src/akasha/bootstrap.py`:
  - new `Container.ai: AIService | None`;
  - new `create_ai_service(settings)`;
  - `create_container` builds `ai` whether or not a database is configured;
  - `create_ai_service` registers the generic OpenAI-compatible provider when its URL is set.
- `backend/src/akasha/config.py`:
  - `llm_timeout_seconds: float = 60.0` (`gt=0`);
  - `ollama_base_url: AnyHttpUrl | None = None`;
  - `openai_compatible_base_url: AnyHttpUrl | None = None`;
  - `openai_compatible_name: str = "openai_compatible"` (pattern `^[a-z0-9][a-z0-9_-]{0,31}$`);
  - `openai_compatible_requires_credential: bool = True`.

### Testing
- New test modules: see §4 and §12.
- The test HTTP server (`tests/support.py`) gained an OpenAI-compatible `/v1/chat/completions` route.
- The architecture test forbids provider libraries outside `akasha.providers`.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/ai.py` | Created | Provider-independent LLM types, `ProviderError`, the `LLMClient` port |
| `backend/src/akasha/application/ai.py` | Created | `ProviderAdapter`, `LLMProvider`, `AIService` |
| `backend/src/akasha/providers/litellm_adapter.py` | Created | LiteLLM-backed adapter, `NO_API_KEY` |
| `backend/src/akasha/bootstrap.py` | Modified | `Container.ai`, `create_ai_service()`, OpenAI-compatible provider |
| `backend/src/akasha/config.py` | Modified | `llm_timeout_seconds`, `ollama_base_url`, `openai_compatible_*` |
| `backend/pyproject.toml` | Modified | Added `litellm~=1.103.1` |
| `backend/uv.lock` | Modified | Resolved litellm and its transitive dependencies (61 → 97 locked packages) |
| `backend/tests/domain/test_ai.py` | Created | Domain invariants and error codes (17 tests incl. parametrized) |
| `backend/tests/application/test_ai_service.py` | Created | `AIService` with `FakeAdapter`, provider configuration, the `LLMClient` port (13 tests) |
| `backend/tests/providers/__init__.py` | Created (empty) | Makes the existing, previously empty `tests/providers/` directory a package |
| `backend/tests/providers/test_litellm_adapter.py` | Created | Real adapter against the loopback server (21 tests incl. parametrized) |
| `backend/tests/integration/test_ai_credentials.py` | Created | Credential Store (PostgreSQL) → AIService → adapter (2 tests) |
| `backend/tests/external/__init__.py`, `backend/tests/external/test_openrouter_smoke.py` | Created (new directory) | Opt-in real-provider smoke tests (3 tests, skipped without an env var) |
| `backend/tests/support.py` | Modified | `CHAT_COMPLETION` fixture body; `ServerState.llm_status/llm_body/llm_hang`; `/v1/chat/completions` route |
| `backend/tests/test_architecture.py` | Modified | `PROVIDER_LIBRARIES` forbidden in domain, application, runtime and nodes |
| `docs/reports/backend/phase-14-ai-provider-architecture.md` | Created | This report |

- No migrations were added, and no files were deleted.
- `backend/src/akasha/providers/__init__.py` was not changed; its existing docstring already describes the layer.

---

## 5. Architecture Changes

The documented architecture (§27: *LLM Node → AI Application Service → Provider Abstraction → Provider Adapter → External LLM*) was implemented without changing it. The following clarifications were made in code.

### 5.1 Split between `LLMProvider` and `ProviderAdapter`
- **Decision:** `ProviderAdapter` is the abstraction, a Protocol that speaks one provider library. `LLMProvider` is an operator-configured entry: name, adapter, whether a credential is required, and a timeout ceiling.
- **Context:** the roadmap names both types but does not define how they differ.
- **Chosen approach:** `AIService` holds a `dict[name, LLMProvider]`. A request selects a provider by name.
- **Alternatives considered:** one class for both, or a single global LiteLLM adapter that takes the provider name as a parameter.
- **Reason:** configuration (credential requirement, timeout, route, endpoint) is separate from the transport. This makes several providers, and several adapters, possible without changing the service.
- **Consequences:** adding a provider means adding an `LLMProvider` entry in `create_ai_service`. A non-LiteLLM adapter only needs to satisfy the Protocol.

### 5.2 Credentials are resolved through the caller's `CredentialResolver`
- **Decision:** `LLMRequest.credential` holds a reference. `AIService.complete(..., credentials=resolver)` resolves it.
- **Context:** Phase 13 established `NodeCredentials`, which only allows references found in the node's frozen config.
- **Chosen approach:** the service does not own a secret source. The LLM Node (Phase 15) will pass `context.credentials`.
- **Alternative considered:** operator-level default credentials per provider, resolved by the service directly through `CredentialService`.
- **Reason:** that alternative would bypass the per-node access boundary. It was rejected.
- **Consequences:** every hosted-provider call needs a credential reference in the calling node's config. Operator-default keys do not exist.

### 5.3 Provider library confined to `akasha.providers`
- **Decision:** `litellm`, `openai` and `anthropic` imports are forbidden in `domain`, `application`, `runtime` and `nodes`.
- **Chosen approach:** an extension of the existing AST dependency guard in `tests/test_architecture.py`.
- **Consequences:** the Phase 15 LLM Node must go through `AIService` and cannot call litellm directly.

### 5.4 `LLMClient`: a domain port for nodes
- **Decision:** nodes reach LLMs through `akasha.domain.ai.LLMClient`, a Protocol with the same signature as `AIService.complete`. The LLM Node receives it through its factory or constructor.
- **Context:** `INTERNAL_RULES["nodes"]` forbids nodes from importing `api`, `application`, `infrastructure` and `runtime`, so the LLM Node cannot import `AIService`.
- **Chosen approach:**
  - the Protocol lives in the domain, next to the types it speaks;
  - `AIService` satisfies it structurally, with no inheritance and no adapter class;
  - in Phase 15, `create_builtin_registry(..., llm=container.ai)` injects it, the same way `core.http` receives `NetworkPolicy`.
  - `test_ai_service_is_the_nodes_llm_port` assigns `AIService` to an `LLMClient` variable, so strict mypy fails if the signatures drift.
- **Alternatives considered:**
  - (a) Put `AIService` into `NodeExecutionContext`. Rejected: this widens the context every node sees, including ones that never call an LLM, and changes the Node Contract.
  - (b) Relax the import rule for `nodes → application`. Rejected: it breaks the documented layering, and plugins (Phase 16/17) would then couple to application internals.
  - (c) Inject a bare `Callable`. Rejected: a named Protocol documents the contract, including `credentials`, and is easier to fake.
- **Reason:** this repeats the established pattern (`CredentialResolver` is a domain Protocol implemented by the runtime's `NodeCredentials`). It is the smallest change that keeps nodes testable with a fake.
- **Consequences:**
  - Phase 15 must build the node registry after `create_ai_service` in `create_container`, or pass the `AIService` in; today `create_node_registry` runs first.
  - The Python Node SDK (Phase 16) can expose the same port to custom nodes.

### 5.5 One generic OpenAI-compatible provider
- **Decision:** an operator can register one OpenAI-compatible endpoint (vLLM, OpenRouter, LM Studio, …) through settings: the URL, a provider name (default `openai_compatible`) and whether a credential is required (default `true`).
- **Chosen approach:** `LiteLLMAdapter("openai", api_base=<url>)`. litellm's `openai` route with a custom base speaks the OpenAI Chat Completions wire format that these servers implement.
- **Alternatives considered:**
  - a list of endpoints as JSON settings: YAGNI until someone needs more than one;
  - a dedicated `openrouter` route: provider-specific, whereas the generic route covers it.
- **Consequences:**
  - a name that collides with a built-in (`openai`, `anthropic`, `ollama`) fails startup with `ConfigurationError`, via `AIService`'s duplicate check;
  - multiple compatible endpoints need a settings change later.

---

## 6. Domain / Data Model Changes

- **New value objects and enums:** listed in §3. They are all frozen dataclasses or StrEnums, with no persistence representation.
- **Database:** no tables, indexes, constraints or migrations.
- **Serialization:** none. The types are not yet exposed through the API or events.
- **State transitions:** none.
- **Compatibility:** purely additive.

---

## 7. API / Contract Changes

- **HTTP API:** no new, changed or removed endpoints. No schemas or WebSocket events changed.
- **New internal contracts:**
  - **`ProviderAdapter.complete(request, *, api_key, timeout_seconds) -> LLMResponse`**. It must raise only `ProviderError` for provider failures and must map library exceptions itself.
  - **`AIService.complete(request, *, credentials=None) -> LLMResponse`**. It raises:
    - `ProviderError`, either passed through from the adapter or raised by the service for `CONFIGURATION` / `TIMEOUT`;
    - `NodeError` from the resolver (`CREDENTIAL_NOT_REFERENCED`, `CREDENTIAL_NOT_FOUND`, `CREDENTIAL_UNAVAILABLE`), passed through unchanged;
    - `asyncio.CancelledError` on external cancellation.
  - **Error codes:** `PROVIDER_AUTHENTICATION`, `PROVIDER_RATE_LIMIT`, `PROVIDER_TIMEOUT`, `PROVIDER_CONTEXT_LENGTH`, `PROVIDER_INVALID_REQUEST`, `PROVIDER_CONTENT_POLICY`, `PROVIDER_UNAVAILABLE`, `PROVIDER_INVALID_RESPONSE`, `PROVIDER_CONFIGURATION`, `PROVIDER_UNKNOWN`. These should be treated as stable: Phase 15 will map them to `NodeErrorKind`.
- **New port for nodes:** `LLMClient.complete(request, *, credentials=None) -> LLMResponse` in `akasha.domain.ai`. Signature compatibility with `AIService` is checked by mypy through a test.
- **Node contract, plugin contract, SDK:** unchanged. `NodeExecutionContext` was deliberately left alone (§5.4).

---

## 8. Implementation Details

### `AIService.complete` control flow
1. Look up the provider by `request.provider`. If it is unknown, raise `ProviderError(CONFIGURATION)`.
2. Determine the key in `_api_key`:
   - no reference and `requires_credential` → `CONFIGURATION`;
   - no reference and a keyless provider → `None`;
   - a reference but no resolver → `CONFIGURATION`;
   - otherwise `await credentials.resolve(ref)`, then `register_secret(key)`, so provider error text that echoes the key is masked by `redact_text`.
3. Compute the effective timeout: `min(request.timeout_seconds or provider.timeout_seconds, provider.timeout_seconds)`. A request can shorten the operator ceiling but never extend it.
4. Run `async with asyncio.timeout(timeout)` around `adapter.complete(..., timeout_seconds=timeout)`:
   - `TimeoutError` → `ProviderError(TIMEOUT)`;
   - `ProviderError` → logged, then re-raised;
   - external cancellation propagates as `CancelledError`.
5. Log `llm_request_completed` with provider, model, input/output tokens, finish reason and `duration_ms` (monotonic clock). Failures log `llm_request_failed` with `error_code`. Prompts, completions and keys are never logged.

### `LiteLLMAdapter`
- **Request mapping:**
  - `model = f"{route}/{request.model}"`; the route is fixed per adapter instance;
  - messages use the OpenAI shape (assistant `tool_calls` with JSON-encoded `arguments`, tool `tool_call_id`);
  - tools use `{"type": "function", "function": {...}}`;
  - `temperature` and `max_tokens` are sent only when set;
  - `api_base` comes only from the constructor, which is operator configuration;
  - `api_key` is the resolved store key, or `NO_API_KEY` for keyless providers, never `None`;
  - `num_retries=0` and `max_retries=0`.
- **Response normalization:**
  - content, tool calls and finish reason come from the first choice;
  - usage comes from `prompt_tokens` / `completion_tokens` and defaults to 0 when absent;
  - finish reasons are mapped (`function_call` → `tool_calls`, unknown values → `other`);
  - `request_id` is the response `id`.
- **Invalid responses:** empty `choices`, non-JSON tool arguments and non-object tool arguments all raise `INVALID_RESPONSE`.
- **Error classification (`_classify`)** matches litellm exception classes most-specific first:
  1. `ContextWindowExceededError` → `CONTEXT_LENGTH`
  2. `ContentPolicyViolationError` → `CONTENT_POLICY`
  3. `AuthenticationError` / `PermissionDeniedError` → `AUTHENTICATION`
  4. `RateLimitError` → `RATE_LIMIT`
  5. `Timeout` → `TIMEOUT`
  6. `BadRequestError` / `NotFoundError` / `UnprocessableEntityError` → `INVALID_REQUEST`
  7. `APIResponseValidationError` → `INVALID_RESPONSE`
  8. `APIConnectionError` / `ServiceUnavailableError` / `InternalServerError` / `BadGatewayError` → `UNAVAILABLE`

  If no class matches, it falls back to `status_code`: 401/403 → `AUTHENTICATION`, other 4xx → `INVALID_REQUEST`, 5xx → `UNAVAILABLE`, anything else → `UNKNOWN`.
- **Which exceptions are caught:** only `openai.OpenAIError`, the common base of litellm's provider exceptions. Other exceptions, such as bugs, propagate. The message is `redact_text(str(exc))[:500]`, and the original exception is chained with `from exc`.
- **Lazy import:** `_litellm()` is `functools.cache`d. It runs `os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")` before `import litellm` and sets `litellm.suppress_debug_info = True`.

### Dependency injection
`AIService` receives its providers through the constructor. `create_ai_service` is the only place that builds them. Tests construct `AIService` directly with fakes, or with `LiteLLMAdapter` pointed at a loopback server.

---

## 9. Findings

### F1 — Importing litellm makes a network call by default
- **Finding:** `litellm/litellm_core_utils/get_model_cost_map.py` downloads the model cost map from GitHub at import time unless `LITELLM_LOCAL_MODEL_COST_MAP=true`.
- **Impact:** MEDIUM. It is a hidden network call (AGENTS §124), leaks the fact that the engine is running, and makes startup nondeterministic.
- **Resolution:** WORKAROUND. `_litellm()` sets the variable (`setdefault`, so operators can still opt in) before importing.
- **Notes:** this is the only `os.environ` access outside `config.py`. It is justified in a comment. No test asserts that the variable is set before import.

### F2 — The litellm import is slow
- **Finding:** measured on this machine, `import litellm` takes about 6.5 s. `import akasha.bootstrap` takes about 1.1 s including the adapter module, which does not import litellm at module level.
- **Impact:** MEDIUM for startup time and test-collection time.
- **Resolution:** FIXED by a lazy import on first `complete()`.
- **Notes:** the first LLM call in a process pays the import cost. It counts against that call's timeout.

### F3 — litellm reports HTTP 403 as a bare `APIError`
- **Finding:** an OpenAI-compatible 403 response became `litellm.APIError` (status 403) rather than `PermissionDeniedError`, which classified as `UNKNOWN`.
- **Impact:** MEDIUM. Misclassified authentication failures.
- **Resolution:** FIXED with the `status_code` fallback in `_classify`. It is covered by `test_provider_errors_are_normalized[403-...]`.

### F4 — Some litellm exceptions are not subclasses of `litellm.exceptions.OpenAIError`
- **Finding:** `litellm.exceptions.OpenAIError` is litellm's own subclass. Its `APIError`, `Timeout`, `RateLimitError` and others derive from `openai.OpenAIError` directly.
- **Impact:** HIGH if unhandled: provider errors would have escaped un-normalized.
- **Resolution:** FIXED. The adapter catches `openai.OpenAIError` (lazy import).
- **Notes:** found by inspecting the MRO before the tests ran.

### F5 — Without an `api_key`, litellm/openai fall back to ambient environment keys
- **Finding:** a test that called the `openai` route with `api_key=None` failed with *"Missing credentials… set the OPENAI_API_KEY environment variable"*. If such an environment variable were set, it would silently be used, bypassing the Credential Store.
- **Impact:** MEDIUM for security and credential isolation. This was proven, not just theoretical: with the fix reverted, `test_keyless_provider_never_uses_ambient_env_keys` observed the ambient `OPENAI_API_KEY` in the outgoing `Authorization` header.
- **Resolution:** FIXED.
  - The first mitigation was that hosted providers require a credential.
  - The root-cause fix, added with the OpenAI-compatible provider: the adapter never passes `api_key=None`, and keyless providers send the inert `NO_API_KEY`, so litellm's env fallback cannot trigger on any route.
- **Notes:** the regression test sets `OPENAI_API_KEY` through `monkeypatch` and asserts `Authorization: Bearer no-key`. It was mutation-checked: it fails when the fix is reverted. Ollama ignores the bearer header.

### F6 — litellm/openai retry by default
- **Finding:** the openai SDK retries (default `max_retries=2`), and litellm has `num_retries`.
- **Impact:** MEDIUM. Hidden duplicate billed requests and slow failures.
- **Resolution:** FIXED. Both are set to 0. The error-mapping tests assert exactly one upstream request.

### F7 — Free-tier upstream rate limits during the real-provider smoke run
- **Finding:** OpenRouter `qwen/qwen3.8-27b:free` returned 429 (`upstream_provider_shared_pool`) on the first attempts.
- **Impact:** LOW. The smoke test is flaky against free models, not the code.
- **Resolution:** OPEN. The 429 was correctly normalized to `PROVIDER_RATE_LIMIT`, and the third attempt passed. The smoke test has no retry, by design.

### F8 — Docker availability probe intermittently times out (pre-existing, environmental)
- **Finding:** during report verification, one full `pytest` run returned `908 passed, 212 skipped`. The immediate rerun returned `1117 passed, 3 skipped`. `tests/support.docker_available()` pings the daemon with `timeout=2`. When Docker Desktop is slow to answer, all Testcontainers-backed tests skip. The same thing happened once earlier in the phase (the 2 integration tests skipped, then passed on rerun).
- **Impact:** MEDIUM. A green run can hide skipped integration coverage.
- **Resolution:** OPEN (pre-existing Phase 05-era infrastructure; not changed in this phase).
- **Notes:** it happened again during the follow-up change (`912 passed, 212 skipped`, then `1121 passed, 3 skipped` once Docker was warm). Results should always be read with `-rs`. See Open Issues.

---

## 10. Bugs / Problems Found

| Problem | Root cause | Fix | Regression test | Origin |
|---|---|---|---|---|
| 403 classified as `UNKNOWN` | litellm maps 403 to bare `APIError` (F3) | `status_code` fallback | `test_provider_errors_are_normalized[403-…]` | Introduced this phase; found by its own tests |
| Provider exceptions could escape un-normalized | Wrong base class (F4) | Catch `openai.OpenAIError` | All `test_provider_errors_are_normalized` cases | Introduced this phase; found before the first test run |
| End-to-end test without a key failed with an env-var hint | Ambient-key fallback (F5) | Test replaced by a credential-backed integration test; hosted providers require credentials | `tests/integration/test_ai_credentials.py` | Design issue found this phase |
| `ruff` ASYNC109 on `timeout` parameters | An async function with a parameter named `timeout` | Renamed to `timeout_seconds` in the Protocol, adapter and fake | Lint gate | Introduced this phase |
| mypy error: `Settings(ollama_base_url="…")` | pydantic field typed `AnyHttpUrl` | `Settings.model_validate({...})` in the test | Type gate | Introduced this phase (test code) |
| Ambient `OPENAI_API_KEY` sent for keyless providers | litellm env fallback when `api_key=None` (F5) | `NO_API_KEY` placeholder | `test_keyless_provider_never_uses_ambient_env_keys` (mutation-checked) | Introduced this phase; found during implementation, fixed in the follow-up |
| Integration tests skipped intermittently | 2 s Docker ping timeout (F8) | Not fixed | — | Pre-existing, environmental |

---

## 11. Fixes Applied

1. **403 misclassification.** Symptom: `UNKNOWN` kind for a 403. Root cause: F3. Implementation: HTTP-status fallback at the end of `_classify`. Verification: the parametrized adapter test passes.
2. **Exception base class.** Symptom (found by inspection): `litellm.exceptions.OpenAIError` is not the base of `APIError`. Implementation: `from openai import OpenAIError` inside `complete()`. Verification: all 8 status-code cases, the timeout case and the unreachable-host case normalize.
3. **Hidden retries disabled.** Implementation: `num_retries=0`, `max_retries=0`. Verification: `assert len(http_server.state.requests) == 1` in error tests, and `test_route_and_endpoint_are_operator_configuration`.
4. **Module name collision avoided.** The adapter module was first created as `providers/litellm.py` and renamed to `litellm_adapter.py` before any test ran, so the file name cannot shadow the `litellm` package in tooling.
5. **Ambient environment keys blocked.** Symptom: an environment `OPENAI_API_KEY` was used for keyless calls. Root cause: F5. Implementation: `"api_key": api_key or NO_API_KEY`. Verification: a regression test that fails with the fix reverted.
6. **Test fixture cleanup.** The integration fixture first attached the resolver to the service through a `type: ignore` attribute. It was replaced by a `(AIService, NodeCredentials)` tuple fixture.

---

## 12. Testing & Verification

Normal CI performs no external network calls. The adapter tests use the existing loopback `ThreadingHTTPServer` (`tests/support.serve_http`).

### Unit tests — `tests/domain/test_ai.py` (17, PASS)
- `Message` invariants (tool calls only on assistant, `tool_call_id` only on tool, content or tool calls required, invalid role).
- `LLMRequest` rejects: an empty provider or model, no messages, temperature 2.5, `max_tokens` 0, timeout 0, and a raw `sk-…` key as `credential`. It accepts a reference.
- `TokenUsage.total_tokens`.
- `ProviderError` code, `retryable` and `details` for 7 kinds.

### Component tests with fake providers — `tests/application/test_ai_service.py` (13, PASS)
Uses a deterministic `FakeAdapter` and `NodeCredentials` over an in-memory `FakeSource`.

| Test | Covers |
|---|---|
| `test_routes_to_the_named_provider_with_the_resolved_key` | Routing across two providers, resolved key passed, keyless provider gets `None` |
| `test_unknown_provider_is_a_configuration_error` | `PROVIDER_CONFIGURATION` |
| `test_credential_is_required_and_needs_a_resolver` | Missing reference or missing resolver → `CONFIGURATION`, adapter never called |
| `test_the_node_credential_boundary_applies` | A reference not in node config → `NodeError CREDENTIAL_NOT_REFERENCED`, adapter never called |
| `test_timeout_is_bounded_by_the_provider_ceiling` | Hanging adapter → `PROVIDER_TIMEOUT`, retryable; a request asking for 30 s gets the 0.05 s ceiling |
| `test_request_may_shorten_the_timeout` | The shorter request timeout reaches the adapter |
| `test_cancellation_is_not_turned_into_a_timeout` | External cancel → `CancelledError` |
| `test_provider_errors_pass_through_and_logs_carry_no_payload` | The same `ProviderError` instance is re-raised; `llm_request_failed` carries `error_code`; `llm_request_completed` has token counts; neither prompt nor key appears in any log record |
| `test_invalid_provider_configuration_fails_fast` | Duplicate name or zero timeout → `ConfigurationError` |
| `test_provider_configuration_from_settings` | Defaults are `anthropic` and `openai`; `ollama` is added with its URL, is keyless and uses the configured timeout |
| `test_openai_compatible_provider_from_settings` | A custom name (`vllm`) is registered, and a keyless flag is honoured; the default name `openai_compatible` requires a credential |
| `test_openai_compatible_name_must_not_shadow_a_builtin` | Name `openai` → `ConfigurationError` |
| `test_ai_service_is_the_nodes_llm_port` | `AIService` is usable as an `LLMClient`, which strict mypy checks |

### Contract/component tests: real LiteLLM adapter — `tests/providers/test_litellm_adapter.py` (21, PASS)
These run against the local OpenAI-compatible `/v1/chat/completions` route.
- **Request mapping:** system, user, assistant-with-tool-call and tool messages; tool-call JSON encoding; tools; temperature; `max_tokens`; `Authorization: Bearer <key>`. Optional parameters are omitted when unset.
- **Response normalization:** content, reported model, usage `(12, 3)`, `STOP`, `request_id`. Tool-call responses are normalized with `TOOL_CALLS`. A `length` finish with missing usage gives 0 tokens.
- **Malformed data:** tool arguments that are non-JSON or a JSON array, and empty `choices`, all raise `INVALID_RESPONSE`.
- **Error normalization:** 401 and 403 → `AUTHENTICATION`; 429 → `RATE_LIMIT`; 400 with `context_length_exceeded` → `CONTEXT_LENGTH`; 400 and 404 → `INVALID_REQUEST`; 500 and 503 → `UNAVAILABLE`. Each case sends exactly one upstream request.
- **Other failures:** a hanging server with `timeout_seconds=0.3` → `TIMEOUT`. `127.0.0.1:9` → `UNAVAILABLE`.
- **Redaction:** a 401 body that echoes the key does not leak it into `message` or `details`.
- **Ambient keys:** `test_keyless_provider_never_uses_ambient_env_keys` checks that, with `OPENAI_API_KEY` set in the environment and `api_key=None`, the request carries `Bearer no-key`.
- **Operator configuration:** `test_route_and_endpoint_are_operator_configuration` checks that the model `ollama/evil` on the `anthropic` route stays `anthropic/ollama/evil`, that `api_base` is not user-controlled, and that retries are 0.

### Integration tests — `tests/integration/test_ai_credentials.py` (2, PASS, PostgreSQL via Testcontainers)
- **Chain:** `CredentialService` (AES-GCM, real PostgreSQL) → `NodeCredentials` → `AIService` → `LiteLLMAdapter` → loopback server.
- **Result:** the shapeless canary key appears exactly as `Authorization: Bearer <canary>` and not in the request body.
- **Error echo:** a 401 whose body echoes the canary is `AUTHENTICATION`. The message contains `MASK` and not the canary; this is known-value redaction via `register_secret`.

### Architecture tests — `tests/test_architecture.py` (2, PASS)
- The extended dependency guard passes: no provider-library imports in domain, application, runtime or nodes.

### External smoke tests (opt-in) — `tests/external/test_openrouter_smoke.py` (3)
- **Tests:** `test_completion_is_normalized`, `test_invalid_key_is_an_authentication_error` and `test_unknown_model_is_an_invalid_request`. They go through `AIService` with `LiteLLMAdapter("openrouter", api_base="https://openrouter.ai/api/v1")` and model `qwen/qwen3.8-27b:free`.
- **Default:** skipped unless `AKASHA_SMOKE_OPENROUTER_API_KEY` is set.
- **Manual run on 2026-09-30:**
  - invalid-key and unknown-model: PASS on the first run;
  - completion: failed twice with an upstream 429, which was correctly normalized to `PROVIDER_RATE_LIMIT`, then PASS on the third attempt.
- **Key handling:** during the run the key was supplied through a temporary file outside the repository, which was deleted afterwards. `git grep` finds no occurrence of the key in the repository.

### Security tests
Included in the suites above:
- raw-key rejection in `LLMRequest`;
- the per-node credential boundary;
- key redaction in provider error messages, both pattern-based and known-value;
- no prompt or key in logs;
- the model string cannot change the provider route or endpoint;
- ambient environment keys are never used;
- the provider-library import guard.

### Not performed
- API tests: no API changes.
- E2E tests.
- Performance tests: only the ad-hoc import timings in F2.
- Live tests against the real OpenAI, Anthropic or Ollama services.
- Accessibility: not applicable.

---

## 13. Quality Gates

Commands were run from `backend/`.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 131 files already formatted |
| Linter (`uv run ruff check .`) | PASS | Initially failed with 3× ASYNC109; fixed by renaming to `timeout_seconds` |
| Type Checker (`uv run mypy .`, strict) | PASS | 129 source files. Initially 1 error in test code (`AnyHttpUrl`); fixed |
| Unit + component tests | PASS | Part of the full run |
| Integration tests (Testcontainers PostgreSQL) | PASS | Final full suite: `1121 passed, 3 skipped`; the 3 skips are the opt-in external smoke tests. Two runs skipped 212 Docker tests because of F8; each rerun passed all |
| API Tests | PASS (unchanged) | Existing API suites pass; no new API |
| E2E Tests | N/A | None exist for this area |
| Build | N/A | No separate build step; `uv sync` resolved the lockfile |
| Security Checks | PASS | Security-focused tests in §12; no dependency-audit tool is configured |
| External smoke (OpenRouter) | PASS (manual, opt-in) | 3/3 after upstream 429 retries by hand |
| Warnings | 1, pre-existing | `StarletteDeprecationWarning` from `fastapi.testclient` (httpx), unrelated to this phase |

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| The domain/runtime does not depend on a specific LLM provider | PASS | `domain/ai.py` imports only stdlib and `akasha.domain.credentials` / `akasha.errors`. The runtime is untouched. `tests/test_architecture.py::test_dependency_direction` forbids `litellm`, `openai` and `anthropic` in domain, application, runtime and nodes, and passes. Two different adapters (`FakeAdapter`, `LiteLLMAdapter`) and four routes (`openai`, `anthropic`, `ollama_chat`, `openrouter`) run behind the same `AIService` without code changes |

Scope items from the roadmap and the prompt:

| Scope Item | Status | Evidence |
|---|---|---|
| `AIService`, `LLMProvider`, `ProviderAdapter` | PASS | `application/ai.py` |
| `LLMRequest`, `LLMResponse`, `Message`, `ToolCall`, `TokenUsage`, `ProviderError` | PASS | `domain/ai.py` |
| At least one real provider | PASS, with a limitation | `LiteLLMAdapter` verified live against OpenRouter (manual smoke, `openrouter` route). OpenRouter is configurable as the generic OpenAI-compatible provider (the `openai` route plus `api_base`); that configuration was verified only against the loopback server. `openai`, `anthropic` and `ollama` were not verified live (§20) |
| Provider-specific code behind the abstraction | PASS | Architecture guard; litellm is imported only in `providers/litellm_adapter.py` |
| Provider credentials use the credential system | PASS | `AIService._api_key` via `CredentialResolver`; `tests/integration/test_ai_credentials.py` |
| Tests: request mapping / construction | PASS | `test_request_mapping`, `test_optional_parameters_are_omitted` |
| Tests: response normalization | PASS | `test_response_normalization`, `test_length_finish_and_missing_usage` |
| Tests: usage extraction / token usage | PASS | Same tests, plus `TokenUsage` unit tests |
| Tests: tool calls | PASS | `test_request_mapping` (outbound), `test_tool_calls_are_normalized`, malformed-argument tests |
| Tests: error normalization / provider errors | PASS | `test_provider_errors_are_normalized` (8 cases), unreachable host, invalid response |
| Tests: timeout behavior | PASS | Service ceiling, shortening and cancellation tests; adapter `test_timeout` |
| Tests: provider configuration | PASS | `test_provider_configuration_from_settings`, `test_openai_compatible_provider_from_settings`, `test_openai_compatible_name_must_not_shadow_a_builtin`, `test_invalid_provider_configuration_fails_fast` |
| Tests: credential resolution | PASS | Service credential tests; PostgreSQL integration test |
| Normal CI independent of external AI providers | PASS | External tests skip without the env var; all others use fakes or loopback |

---

## 15. Security Considerations

- **Secrets and credentials:**
  - `LLMRequest.credential` accepts only `credential:<name>` references; raw keys are rejected at construction.
  - Keys are resolved at call time through `NodeCredentials`, so the per-node access boundary from Phase 13 applies.
  - The resolved key is registered with `register_secret` for known-value masking in the current task context.
  - A key is passed to litellm only as the `api_key` argument. It is never logged or stored, and it never appears in `LLMResponse`.
- **Ambient credentials (F5):** fixed at the root. Keys come only from the Credential Store, and keyless providers send `NO_API_KEY`. A regression test covers it.
- **Error disclosure:** provider messages are passed through `redact_text` (known secrets plus `sk-…`/Bearer patterns) and truncated to 500 characters. Raw exceptions are chained for server-side debugging but are not part of `details`.
- **Logging:** no prompts, completions or keys; only counts and identifiers (AGENTS §76). This is verified by test.
- **SSRF / endpoint control:** `api_base` comes only from operator configuration (`AKASHA_OLLAMA_BASE_URL`, `AKASHA_OPENAI_COMPATIBLE_BASE_URL`, or the litellm defaults). The OpenAI-compatible provider requires a credential by default; operators must opt out explicitly for keyless servers. A user-controlled `model` is always prefixed with the configured route, so it cannot select another provider. Operator endpoints are trusted and not checked against `NetworkPolicy`, the same treatment as `web_search_url`.
- **Supply chain:** litellm had compromised PyPI releases (1.82.7 and 1.82.8, March 2026). The pinned range `~=1.103.1` excludes them, and `uv.lock` pins exact hashes. The dependency adds 36 locked packages. No automated dependency audit is configured.
- **Privacy:** the lazy import sets `LITELLM_LOCAL_MODEL_COST_MAP`, which prevents an outbound fetch to GitHub at import (F1).
- **API exposure:** no new endpoints.
- **Real key in the conversation:** the OpenRouter key used for the manual smoke run was pasted into the development chat. It is not in the repository, but it should be rotated.

---

## 16. Performance Considerations

- **Measured:** `import litellm` takes about 6.5 s; `import akasha.bootstrap` takes about 1.1 s. These are single measurements on the development machine.
- **Lazy import:** it keeps litellm off the startup path. The first LLM call per process includes the import time inside its timeout budget; with the default 60 s this is not an issue.
- **Retries:** there are no retries inside the adapter, so a failure costs exactly one upstream request.
- **Not measured:** request overhead, memory usage of litellm, or concurrency limits. No provider-level concurrency limit exists.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/reports/backend/phase-14-ai-provider-architecture.md` | Phase report | This document |
| Module docstrings in `domain/ai.py`, `application/ai.py`, `providers/litellm_adapter.py` | In-code design documentation | Layering, credential flow, timeout semantics, the ambient-key caveat, the lazy-import reason |
| `config.py` comments | Configuration documentation | New settings |

- `docs/architecture.md` and `docs/roadmap-backend.md` were not changed, because nothing contradicted them.
- **Should be updated in a future phase:**
  - `backend/README.md` or the operator docs should mention `AKASHA_LLM_TIMEOUT_SECONDS`, `AKASHA_OLLAMA_BASE_URL`, `AKASHA_OPENAI_COMPATIBLE_*` and the external smoke-test variables (`AKASHA_SMOKE_OPENROUTER_API_KEY`, `_MODEL`, `_BASE_URL`);
  - architecture §28/§134 could note which request fields are implemented.
- No ADR was created. The decisions, including the `LLMClient` port, refine §27 and repeat the existing `CredentialResolver` pattern rather than change the architecture. If the team treats node-facing ports as architecturally significant, §5.4 is the basis for an ADR.

---

## 18. Dependencies / Infrastructure Changes

- **New runtime dependency:** `litellm~=1.103.1` (resolved 1.103.1). The phase prompt required it, and roadmap §18 permits it. It brings a large transitive tree, including `openai` 2.54.0, `tiktoken`, `tokenizers`, `jsonschema`, `aiohttp`-family packages and `boto`-family packages; locked packages grew from 61 to 97.
- **New settings / environment variables:**
  - `AKASHA_LLM_TIMEOUT_SECONDS` (default 60, > 0);
  - `AKASHA_OLLAMA_BASE_URL` (optional);
  - `AKASHA_OPENAI_COMPATIBLE_BASE_URL` (optional), `AKASHA_OPENAI_COMPATIBLE_NAME` (default `openai_compatible`), `AKASHA_OPENAI_COMPATIBLE_REQUIRES_CREDENTIAL` (default `true`).
- **Test-only environment variables:** `AKASHA_SMOKE_OPENROUTER_API_KEY` (enables the external tests), `AKASHA_SMOKE_OPENROUTER_MODEL` and `AKASHA_SMOKE_OPENROUTER_BASE_URL`.
- **Environment variable set by code:** `LITELLM_LOCAL_MODEL_COST_MAP=True` (`setdefault`) on first LLM use.
- **Test infrastructure:** the loopback test server gained an OpenAI-compatible completions route with a configurable status, body and hang. There is a new `tests/external/` directory.
- **Unchanged:** Docker, database, migrations and Redis.

---

## 19. Deferred Work

| Item | Why deferred | Intended phase | Affects completion? |
|---|---|---|---|
| LLM Node, prompt rendering and variables | Out of scope | Phase 15 | No |
| Structured output (`response_format` / JSON Schema) | Roadmap assigns it to Phase 15 | Phase 15 | No |
| Token cost estimation | Roadmap Phase 15 | Phase 15 | No |
| LLM metrics (`llm_tokens_total`, provider error counters) | No consumer until the LLM Node; `AIService` has no metrics injected | Phase 15, or runtime wiring in Phases 21/22 | No |
| Streaming | Not in Phase 14 scope (architecture §28 only) | Not scheduled | No |
| Provider capability and pricing metadata (§134) | Not in roadmap scope | Not scheduled | No |
| Embeddings | Not in scope | Not scheduled | No |
| Wiring `LLMClient` into the node registry | No consumer before the LLM Node | Phase 15 | No |
| Multiple OpenAI-compatible endpoints | One covers the known need (YAGNI) | When needed | No |
| Retry of retryable `ProviderError`s | Retry policy belongs to the runtime | Phase 23 (Reliability) | No |

---

## 20. Known Limitations

- **Live verification:** only the `openrouter` route is live-verified. `openai`, `anthropic` and `ollama_chat` rely on litellm's routing and are untested against real services. Ollama's native wire format (`/api/chat`) is not exercised by any test.
- **Configured providers:** `openai` and `anthropic` are fixed; `ollama` and exactly one OpenAI-compatible endpoint are optional. There is no API for managing providers at runtime.
- **Request model subset:** there is no `stop` sequences, `top_p`, `tool_choice`, streaming, metadata or structured output. A system prompt is expressed as a `Role.SYSTEM` message rather than a dedicated field.
- **Response model:** only the first choice is used. There is no raw-response reference, and no provider-authoritative cost.
- **`TokenUsage.total_tokens`:** computed as input + output. The provider-reported `total_tokens` (which may include reasoning tokens for some providers) is ignored.
- **First-call latency:** the first LLM call per process pays the ~6.5 s litellm import cost inside its timeout.
- **Unused wiring:** `Container.ai` and the `LLMClient` port exist, but nothing consumes them yet.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| Docker probe `timeout=2` intermittently skips all 212 Testcontainers tests (F8) | Green runs can hide skipped integration coverage | MEDIUM | Test infrastructure (`tests/support.docker_available`) | Increase the timeout or retry the ping, and/or fail instead of skip in CI (for example `AKASHA_REQUIRE_DOCKER=1`) | Next infra-touching phase |
| No test proves `LITELLM_LOCAL_MODEL_COST_MAP` is set before import (F1) | A refactor could reintroduce the import-time network call | LOW | `providers/litellm_adapter.py` | Add a test that asserts the env var after `_litellm()`, or block sockets during import | Phase 15 |
| Configured providers `openai`, `anthropic`, `ollama` and the OpenAI-compatible route not live-verified | Routing bugs would surface only in production | LOW | Providers | Extend the opt-in smoke tests per provider when keys or a local Ollama are available | Phase 15 |
| OpenRouter key exposed in the development chat | Credential exposure outside the repository | MEDIUM | Operations | Rotate the key in the OpenRouter dashboard | Immediately (user) |
| No dependency vulnerability audit in CI | Supply-chain risk grew with litellm's transitive tree | MEDIUM | CI | Add `pip-audit` / `uv` audit to CI | CI hardening phase |

---

## 22. Architectural / Product Decisions

1. **No operator-default provider keys.** Credentials always come from the calling node's config, through its `CredentialResolver`. This keeps the Phase 13 per-node access boundary intact for LLM calls.
2. **The provider timeout is a ceiling.** Requests (node config) can shorten it but not extend it, so user configuration cannot hold a worker for longer than the operator allows.
3. **No retries below the runtime.** Adapters must not retry; `ProviderError.retryable` tells the runtime what it may retry. This avoids hidden, duplicate, billed requests.
4. **Route and endpoint are operator configuration; the model is user data.** The adapter prefixes the model with its fixed route, so a model string can never re-route a request.
5. **Lazy litellm import with the local cost map.** This avoids a startup penalty of about 6.5 s and an import-time network fetch.
6. **Catch `openai.OpenAIError`, not `Exception`.** Only provider failures are normalized; programming errors still surface.
7. **Normalized categories plus an HTTP-status fallback.** Litellm's exception mapping is incomplete (403), so the status code is the second classification source.
8. **Nodes reach LLMs through the domain port `LLMClient`**, injected by the factory. It is not part of `NodeExecutionContext`, and nodes never import the application layer (§5.4).
9. **Keys come only from the Credential Store.** Keyless providers send `NO_API_KEY`, so no environment key can be used implicitly.
10. **One OpenAI-compatible endpoint, credential required by default.**
11. **External tests are opt-in and shape-only.** They skip without an env var and never assert exact LLM output (AGENTS §26/§27).

---

## 23. Repository / Git State

- **Branch:** `main`. **Base commit:** `b6c73f3 backnd phase 13 - credentials and secrets`.
- **Working tree:** uncommitted Phase 14 changes. Nothing has been committed for this phase.
  - **Modified:** `backend/pyproject.toml`, `backend/uv.lock`, `backend/src/akasha/bootstrap.py`, `backend/src/akasha/config.py`, `backend/tests/support.py`, `backend/tests/test_architecture.py`.
  - **Untracked:** `backend/src/akasha/domain/ai.py`, `backend/src/akasha/application/ai.py`, `backend/src/akasha/providers/litellm_adapter.py`, `backend/tests/domain/test_ai.py`, `backend/tests/application/test_ai_service.py`, `backend/tests/providers/` (`__init__.py`, `test_litellm_adapter.py`), `backend/tests/integration/test_ai_credentials.py`, `backend/tests/external/` (`__init__.py`, `test_openrouter_smoke.py`), `docs/reports/backend/phase-14-ai-provider-architecture.md`.
- **Unrelated changes:** none detected.
- **Secret check:** `git grep` for the OpenRouter key returns no matches, and the temporary key file was deleted.

---

## 24. Phase Completion Assessment

### Implemented
All nine roadmap types, plus the `LLMClient` node port. The generic OpenAI-compatible provider. The LiteLLM adapter with request mapping, response and tool-call normalization, error classification and redaction. Timeout enforcement. Credential resolution through the credential system. Provider configuration from settings. Container wiring. The architecture import guard.

### Verified
1121 deterministic tests pass (53 new plus the existing suite), including a PostgreSQL-backed credential-chain integration test. Ruff lint and format pass. Strict mypy passes. A live OpenRouter smoke run passed 3/3 (manual, opt-in).

### Not Implemented
Streaming, structured output, cost estimation, LLM metrics, provider capability metadata and embeddings. All are outside Phase 14's roadmap scope.

### Deferred
See §19.

### Open
Docker probe flakiness (pre-existing). No test guards the cost-map env var. Configured providers, including the OpenAI-compatible route, are not live-verified. The key exposed in chat needs rotation. No dependency audit exists.

### Blocking Issues
None.

**Phase Status:** COMPLETE WITH LIMITATIONS

The acceptance criterion and all scope items pass. The limitation is that the configured engine providers are not live-verified; only the adapter is, through litellm's `openrouter` route.

---

## 25. Handoff to Next Phase (Phase 15 — LLM Node)

**The next phase can assume:**
- **The layering question is settled (§5.4).** The LLM Node depends on `akasha.domain.ai.LLMClient` and calls `await llm.complete(request, credentials=context.credentials)`. To wire it up:
  - add an `llm: LLMClient | None` parameter to `create_builtin_registry`;
  - in `create_container`, build `create_ai_service(settings)` before `create_node_registry` and pass `llm=ai`;
  - if no client is configured, the node should fail with a `CONFIGURATION` `NodeError`, like `core.web_search` without `web_search_url`;
  - node tests pass a fake `LLMClient`.
- `LLMRequest` fields: `provider`, `model`, `messages`, `temperature`, `max_tokens`, `tools`, `credential`, `timeout_seconds`. The node config should hold a `credential` reference, so that `NodeCredentials` authorizes it.
- `LLMResponse` gives `content`, `tool_calls`, `usage` (`input_tokens`, `output_tokens`, `total_tokens`), `finish_reason` and `request_id`.
- `ProviderError.kind` / `.retryable` / `.code` should be mapped to `NodeError`, for example:
  - `RATE_LIMIT` / `UNAVAILABLE` → `TRANSIENT`;
  - `TIMEOUT` → `TIMEOUT`;
  - `CONFIGURATION` / `AUTHENTICATION` → `CONFIGURATION`;
  - everything else → `PERMANENT`;
  - keep `code` as the node error code.
- Credential `NodeError`s from the resolver already have node semantics and pass through `AIService` unchanged.

**Must not be changed casually:**
- the `PROVIDER_*` error codes;
- `num_retries=0` / `max_retries=0`;
- the rule that the model is prefixed by the route;
- `requires_credential=True` for hosted providers;
- the lazy import and `LITELLM_LOCAL_MODEL_COST_MAP`.

**Known constraints:**
- The first call per process adds about 6.5 s of import time.
- Structured output needs a new `LLMRequest` field and a `response_format` mapping in `LiteLLMAdapter.request_kwargs`.
- Cost estimation should say explicitly that it is an estimate (architecture §29). `litellm.completion_cost` exists, but its use must stay inside `akasha.providers`.

**Prerequisites:**
- None blocking.
- Rotate the exposed OpenRouter key.
- To use OpenRouter in the engine, set `AKASHA_OPENAI_COMPATIBLE_BASE_URL=https://openrouter.ai/api/v1` and `AKASHA_OPENAI_COMPATIBLE_NAME=openrouter`, and give each LLM node a credential reference to a stored OpenRouter key.
