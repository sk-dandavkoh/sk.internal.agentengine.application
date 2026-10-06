# Phase 15 — LLM Node

## 1. Executive Summary

**What the phase had to achieve.** Roadmap §19 asks for the production LLM Node. It must support:
- provider and model;
- system and user prompt, with prompt variables;
- temperature and max tokens;
- structured output and tool calls, where supported;
- controlled prompt rendering;
- token usage and cost estimation.

Acceptance: an LLM Node can be placed into a Workflow and executed through the normal runtime.

**What was implemented.**
- **Built-in node** `core.llm` (`akasha.nodes.llm.LlmNode`). It is registered in the Node Registry, validated at publish through its closed config schema, and executed by the unchanged `ExecutionRuntime`.
- **Provider access:** the node reaches providers only through the domain port `akasha.domain.ai.LLMClient`. The composition root fills it with the container's `AIService`.
- **Prompt templates:** `{{ path }}` is a dotted lookup into the node's `variables` input. Nothing is evaluated.
- **Structured output:** a new `LLMRequest.response_schema` field. The adapter maps it to `response_format`, and the node validates the reply.
- **Tool calls:** returned as structured output data.
- **Cost:** a new `CostEstimate` on `LLMResponse`, filled by `LiteLLMAdapter` from litellm's bundled price list.
- **Token usage and cost:** reported in the node's `metadata` output, which the runtime persists.

**Resulting system state.**
- A Workflow graph `core.input → core.llm → core.output` publishes and executes against PostgreSQL. The key comes from the encrypted Credential Store and the node calls the real LiteLLM adapter against a loopback OpenAI-compatible server (integration test).
- The same node ran live against OpenRouter in the opt-in smoke tests.
- Quality gates: 1205 tests pass, 5 are skipped (all opt-in external tests), and ruff, ruff format and strict mypy pass.

**Acceptance.** The single roadmap acceptance criterion is satisfied with direct evidence (§14). Limitations remain: no LLM metrics, a JSON-Schema subset for output validation, and no publish-time provider-name check. None of them blocks the criterion.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Roadmap §19 (Phase 15 — LLM Node):
- **Goal:** "Implement the production LLM Node."
- **Scope:** provider, model, system prompt, user prompt, variables, temperature, max tokens, structured output where supported, tool calls where supported. Also prompt rendering, token usage and cost estimation.
- **Tests:** prompt rendering, variable substitution, missing variables, provider errors, token usage, cost calculation, structured output.
- **Acceptance:** "An LLM Node can be placed into a Workflow and executed through the normal runtime."

The phase prompt adds four requirements:
- use deterministic fake providers;
- extend the external smoke test if feasible;
- also test provider selection, model selection, tool calls, secret safety and cost metadata;
- introduce no arbitrary Python evaluation.

**Explicitly not in scope:**
- the Python Node SDK (Phase 16) and plugins (Phase 17);
- the general Expression Engine (not scheduled as its own phase; hardened in the security pass);
- runtime retries (Phase 23);
- the worker/runtime wiring of `EngineMetrics` and credentials into a running engine (Phases 21/22);
- streaming;
- multi-turn conversations and tool-execution/agent loops (not on the roadmap).

---

## 3. Scope Implemented

### Domain (`backend/src/akasha/domain/ai.py`)
- **`LLMRequest.response_schema: Mapping[str, Any] | None`:** a JSON Schema the reply must satisfy. `__post_init__` rejects anything whose `type` is not `"object"` (`DomainValidationError`).
- **`CostEstimate(amount: float, currency: str = "USD")`:** a frozen dataclass. Its docstring states it is an estimate from list prices and never billing data.
- **`LLMResponse.cost: CostEstimate | None = None`:** a new trailing field with a default, so existing constructors are unaffected.

### Providers (`backend/src/akasha/providers/litellm_adapter.py`)
- **`request_kwargs`:** adds `response_format = {"type": "json_schema", "json_schema": {"name": "response", "schema": ...}}` when `response_schema` is set.
- **`LiteLLMAdapter._estimate_cost`:**
  - calls `litellm.cost_per_token(model=f"{route}/{model}", prompt_tokens, completion_tokens)`;
  - returns `None` when the adapter has a custom `api_base`;
  - returns `None` when litellm raises for an unmapped model.
- **`complete`:** attaches the estimate with `dataclasses.replace(response, cost=...)`.

### Node (`backend/src/akasha/nodes/llm.py`, new)
- **`LlmNode`** (`core.llm`, version `1.0.0`, category `ai`):
  - **Input:** `variables` (OBJECT, optional, default `{}`).
  - **Outputs:**
    - `text` (STRING, nullable);
    - `structured` (JSON, nullable);
    - `tool_calls` (LIST);
    - `metadata` (OBJECT).
  - **Config** (closed schema; `provider`, `model` and `user_prompt` are required):

    | Key | Type | Constraint |
    |---|---|---|
    | `provider` | string | minLength 1 |
    | `model` | string | minLength 1 |
    | `credential` | string | pattern `^credential:` |
    | `system_prompt` | template | — |
    | `user_prompt` | template | minLength 1 |
    | `temperature` | number | 0–2 |
    | `max_tokens` | integer | 1–100 000 |
    | `output_schema` | object | — |
    | `tools` | array of `{name, description, parameters}` | ≤ 32 items; `name` matches `^[A-Za-z0-9_-]{1,64}$` |
    | `timeout_seconds` | number | 1–600 |
  - **Capabilities:** `supports_cancellation=True`, `has_side_effects=False`.
- **`render(template, variables)`:** placeholder substitution (§8).
- **Private helpers:**
  - `_request`: renders the prompts, enforces the size cap and builds the `LLMRequest`;
  - `_tool`: turns a config entry into a `ToolDefinition`;
  - `_structured`: parses and validates the reply;
  - `_metadata`: builds the metadata output;
  - `_ERROR_KINDS`: maps `ProviderErrorKind` to `NodeErrorKind`.
- **Resource-limit constants:**
  - `MAX_PROMPT_CHARS = 400_000`
  - `MAX_OUTPUT_TOKENS = 100_000`
  - `MAX_TOOLS = 32`
  - `MAX_TIMEOUT_SECONDS = 600`

### Registry / composition
- **`akasha.nodes.builtins.create_builtin_registry`:** new keyword `llm: LLMClient | None = None`. It registers `LlmNode` with the factory `lambda: LlmNode(llm)`.
- **`akasha.bootstrap.create_node_registry(settings, *, llm=None)`:** passes `llm` through.
- **`create_container`:** now builds `create_ai_service(settings)` before the node registry and passes `llm=ai`. The container's `ai` and the node's client are the same object (tested).

### Testing
- **New:**
  - `tests/nodes/test_llm_node.py` (72 tests);
  - `tests/integration/test_llm_node_runtime.py` (4 tests).
- **Extended:**
  - `tests/providers/test_litellm_adapter.py` (21 → 25);
  - `tests/domain/test_ai.py` (17 → 19);
  - `tests/nodes/test_builtin_contract.py` (+2: `core.llm` in the parametrized contract suite, plus the container wiring test);
  - `tests/external/test_openrouter_smoke.py` (3 → 5).

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/nodes/llm.py` | Created | `LlmNode`, `render`, request/structured-output/metadata helpers |
| `backend/src/akasha/domain/ai.py` | Modified (+17) | `LLMRequest.response_schema`, `CostEstimate`, `LLMResponse.cost` |
| `backend/src/akasha/providers/litellm_adapter.py` | Modified (+33) | `response_format` mapping, `_estimate_cost` |
| `backend/src/akasha/nodes/builtins.py` | Modified (+7) | `llm` parameter, `core.llm` registration |
| `backend/src/akasha/bootstrap.py` | Modified (+11) | `AIService` built before the registry and injected; `create_node_registry(llm=)` |
| `backend/tests/nodes/test_llm_node.py` | Created (72 tests) | Fake-provider component tests, security tests |
| `backend/tests/integration/test_llm_node_runtime.py` | Created (4 tests) | Workflow → publish → runtime → PostgreSQL → loopback provider |
| `backend/tests/providers/test_litellm_adapter.py` | Modified (+4 tests) | `response_format`, cost estimate (priced, unpriced, custom endpoint), cost-map env guard |
| `backend/tests/domain/test_ai.py` | Modified (+2 cases) | Invalid `response_schema` rejected |
| `backend/tests/nodes/test_builtin_contract.py` | Modified (+2 tests) | `core.llm` in `ROADMAP_NODES` and the cancellation set; container wiring |
| `backend/tests/external/test_openrouter_smoke.py` | Modified (+2 tests) | Live node structured output and tool call |
| `docs/architecture.md` | Modified (+34) | §30 implementation note; §113 LLM metrics status |
| `docs/reports/backend/phase-15-llm-node.md` | Created | This report |

- No directories, migrations, configuration files or generated files were created or changed.
- `pyproject.toml` and `uv.lock` are unchanged.

---

## 5. Architecture Changes

The documented chain (architecture §27: *LLM Node → AI Application Service → Provider Abstraction → Provider Adapter → External LLM*) was implemented without structural change. The clarifications below were made in code and documented in `docs/architecture.md` §30.

### 5.1 Prompt templates are a closed path-lookup subset, not the Expression Engine
- **Decision:** `{{ a.b.0 }}` resolves mapping keys and list indexes in the `variables` input. There are no filters, operators, calls, subscripts or attribute access.
- **Context:** architecture §30/§31 and backend AGENTS §30/§31 require controlled, deterministic rendering with no `eval`/`exec`. No Expression Engine exists yet (`nodes/control.py` notes the same for conditions).
- **Chosen approach:** a regex-based substituter (`render`). The same grammar is enforced as a config-schema `pattern` on both prompt keys, so malformed templates fail at publish.
- **Alternatives considered:**
  - **Jinja2 sandbox:** a new dependency with a large feature surface (loops, filters, attribute access), exactly the area AGENTS §38 treats as a security risk.
  - **Building the general Expression Engine now:** out of phase scope.
- **Reason:** this is the smallest mechanism that satisfies the documented examples (`{{ lead }}`, `{{ lead.name }}`) and can be proven safe by construction: nothing is ever evaluated.
- **Consequences:**
  - When a general Expression Engine arrives, `core.llm` templates may migrate to it. The current grammar is a strict subset, so existing templates stay valid.
  - There is no escape for a literal `{{`.

### 5.2 Variables come from one input port
- **Decision:** templates see only the `variables` OBJECT input, not the execution input or arbitrary node outputs.
- **Context:** the Node Contract gives nodes their ports and `execution_input`. Cross-node references (`{{ nodes.search.output }}`, vision §22) are Expression Engine territory.
- **Chosen approach:** one optional input port. Users wire `core.input` (or any object-producing node) into it.
- **Alternatives considered:**
  - one port per variable, which the static Node Contract cannot express, since ports are per type (same ceiling as `core.switch`);
  - reading `execution_input` implicitly, which would hide data flow from the graph.
- **Consequences:** combining several upstream values into one object needs an upstream node that builds it. No such mapping node exists yet.

### 5.3 Cost estimation lives in the adapter and is suppressed behind custom endpoints
- **Decision:** `LiteLLMAdapter` estimates cost. The domain only carries the provider-neutral `CostEstimate`.
- **Context:** the phase 14 handoff says litellm pricing use must stay inside `akasha.providers`. Architecture §29 says cost must be marked as an estimate and never claimed as billing data.
- **Chosen approach:** `litellm.cost_per_token` against the bundled map, and `None` whenever the adapter has a custom `api_base`.
- **Alternatives considered:**
  - an operator-configured price table: more configuration, and no consumer yet;
  - `litellm.completion_cost` on the raw response: same data source, but tied to the raw object shape;
  - OpenRouter's `usage.cost`: provider-specific.
- **Reason:** behind a custom endpoint (Ollama, vLLM, the generic OpenAI-compatible provider) a model name like `gpt-4o` says nothing about price. No estimate is safer than a wrong one.
- **Consequences:**
  - The generic OpenAI-compatible provider (e.g. OpenRouter configured via `AKASHA_OPENAI_COMPATIBLE_BASE_URL`) never gets a cost estimate.
  - The litellm `openrouter` route without `api_base` does.

### 5.4 Structured output is validated by the node, with the engine's JSON-Schema subset
- **Decision:** the node sends `response_schema` to the provider and always re-validates the reply itself.
- **Context:** "where supported" means some providers or models ignore `response_format`. The engine has a JSON-Schema subset validator (`NodeConfigSchema.validate_config`, phase 03). A full validator (`jsonschema`) is only a transitive dependency of litellm.
- **Chosen approach:** reuse `NodeConfigSchema`. There is no new direct dependency.
- **Consequences:** only the subset is enforced (§20, L4). The upgrade path already documented in `NodeConfigSchema` (adopt `jsonschema`) applies here too.

### 5.5 Tool calls are returned, not executed
- **Decision:** configured tools are offered to the model. Resulting calls appear in the `tool_calls` output, and nothing executes them.
- **Reason:** tool execution means an agent loop with multi-turn messages, which is not in the roadmap scope of Phase 15. Returning calls as data keeps the node single-turn and side-effect free (`has_side_effects=False`).
- **Consequences:** a downstream node can act on `tool_calls`. A future agent/loop node would reuse `Message`/`ToolCall`.

---

## 6. Domain / Data Model Changes

- **`LLMRequest.response_schema`** (new optional field, default `None`). Validation: `type` must be `"object"`.
- **`CostEstimate`** (new value object): `amount: float`, `currency: str = "USD"`.
- **`LLMResponse.cost`** (new optional field, default `None`).
- **Compatibility:** all additions are trailing optional fields. Existing construction sites (adapter, tests, fakes) are unchanged, and the full suite passes.
- **Persistence:** no database tables, columns, migrations or indexes. LLM usage and cost are persisted as part of the existing `NodeExecution.outputs` JSON.
- **State transitions:** none changed.

---

## 7. API / Contract Changes

- **HTTP API:** no endpoints were added or changed. `core.llm` appears wherever node definitions are listed, because it is in the registry.
- **Node contract `core.llm` 1.0.0** (new):
  - **Input** `variables`: object; optional; default `{}`.
  - **Output** `text`: the reply text, or `null` when the model answered only with tool calls.
  - **Output** `structured`: the parsed object when `output_schema` is set and the model answered in text; otherwise `null`.
  - **Output** `tool_calls`: `[{"id", "name", "arguments"}]`.
  - **Output** `metadata`:
    ```json
    {"provider": "...", "model": "<as reported>", "request_id": "...|null",
     "finish_reason": "stop|length|tool_calls|content_filter|other",
     "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
     "cost": {"amount": 0.0, "currency": "USD", "estimated": true} | null}
    ```
  - **Error codes (stable):**

    | Code | Kind |
    |---|---|
    | `LLM_NOT_CONFIGURED` | CONFIGURATION |
    | `LLM_CONFIGURATION_INVALID` | CONFIGURATION |
    | `PROMPT_TEMPLATE_INVALID` | CONFIGURATION |
    | `PROMPT_VARIABLE_MISSING` | VALIDATION, details `{"variable": path}` |
    | `PROMPT_TOO_LARGE` | VALIDATION, details `{"limit"}` |
    | `LLM_STRUCTURED_OUTPUT_INVALID` | PERMANENT |
    | `PROVIDER_*` (the 10 phase 14 codes) | kind mapped per §8, details `{"provider"}` |

    Credential `NodeError`s (`CREDENTIAL_NOT_REFERENCED`, `CREDENTIAL_NOT_FOUND`, `CREDENTIAL_UNAVAILABLE`) pass through unchanged.
- **Python contracts:**
  - `create_builtin_registry(..., llm=None)` and `create_node_registry(settings, *, llm=None)`: additive keyword arguments.
  - `LLMRequest`/`LLMResponse`: additive fields (§6).
- **Frontend impact:** none yet. The node's config schema is machine-readable for a future node form.

---

## 8. Implementation Details

**Execution flow (`LlmNode.execute`):**
1. If there is no `LLMClient`, fail with `LLM_NOT_CONFIGURED`.
2. `_request`:
   - render `system_prompt` (only if non-empty) and `user_prompt` against `inputs["variables"]`;
   - reject a total above `MAX_PROMPT_CHARS`;
   - construct `NodeConfigSchema(output_schema)`, which checks that the schema's top level is an object;
   - build the `LLMRequest`, converting `DomainValidationError`/`NodeContractError` into `LLM_CONFIGURATION_INVALID`.
   - All of this happens **before** any provider call.
3. `await llm.complete(request, credentials=context.credentials)`. The `AIService` resolves the credential reference through the node's own `NodeCredentials` (only references in the node's frozen config), registers the key for redaction, and applies the provider timeout (`min(request, provider)`).
4. `ProviderError` → `NodeError`:
   - the message is the adapter's already-redacted, truncated text;
   - the code is kept;
   - the kind comes from `_ERROR_KINDS` (RATE_LIMIT/UNAVAILABLE → TRANSIENT, TIMEOUT → TIMEOUT, AUTHENTICATION/CONFIGURATION → CONFIGURATION, other → PERMANENT).
   - The provider exception stays chained (`from exc`), for server-side diagnosis only.
5. Outputs are built. `_structured`:
   - returns `None` when there is no schema, or when the model returned tool calls;
   - otherwise strips surrounding whitespace and a leading `` ```json ``/`` ``` `` and trailing `` ``` `` fence, and parses JSON;
   - requires a `dict`;
   - validates it with `NodeConfigSchema(schema).validate_config`.

**Rendering (`render`):**
- `_PLACEHOLDER = \{\{(.*?)\}\}` (DOTALL) finds placeholders.
- Each inner path must fully match `_PATH = [A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*`, otherwise `PROMPT_TEMPLATE_INVALID`.
- Traversal:
  - `Mapping` → key membership;
  - `list` → a digit segment within bounds;
  - anything else → `PROMPT_VARIABLE_MISSING`.
- There is no `getattr`, so `__class__`-style paths are ordinary key lookups.
- Strings are inserted verbatim. Other values go through `json.dumps(..., ensure_ascii=False)` (`None` → `null`), which is deterministic for a given input.
- **Publish-time grammar:** `^(?:[^{]|\{(?!\{)|\{\{\s*PATH\s*\}\})*$`. Single braces (JSON examples in prompts) are allowed. The three alternatives are disjoint on their first characters, so matching is linear.

**Validation layering:**
- The config schema runs at publish (`WorkflowService`) and in the `run_node` test harness.
- Nested `required` inside `tools` items is **not** enforced by the schema subset, so `_tool` checks `name`/`parameters` at run time.

**Dependency injection:** `LlmNode(llm)` receives the client through the registry factory, like `HttpNode(policy)`. A fresh instance is created per attempt, and the client is shared.

**Cancellation and timeouts:**
- The provider call is a plain `await`, so runtime cancellation and the node timeout propagate as `CancelledError`/`TimeoutError` into litellm/httpx.
- The config `timeout_seconds` can only shorten the provider's configured timeout (the phase 14 `AIService` rule).

**Logging:** the node logs nothing itself. `AIService` already logs `llm_request_completed`/`llm_request_failed` with provider, model, token counts, finish reason and duration, and no prompt or completion content.

**Security-sensitive behavior:** see §15.

---

## 9. Findings

### F1 — `litellm.cost_per_token` signals unmapped models with a bare `Exception`
- **Impact:** LOW
- **Resolution:** WORKAROUND
- **Notes:** probed directly. `openai/gpt-test` and `ollama_chat/llama3` raised `Exception("This model isn't mapped yet...")`. `_estimate_cost` therefore catches `Exception` and returns `None`, with a comment explaining why (AGENTS §10 requires the reason). An unknown price is not a failed completion.

### F2 — Bundled price list results
- **Impact:** LOW
- **Resolution:** documented
- **Notes:**
  - `openai/gpt-4o` → (0.0025, 0.005) USD per 1000/500 tokens;
  - `anthropic/claude-sonnet-4-5` → (0.003, 0.0075);
  - `openrouter/qwen/qwen3.8-27b:free` → (0, 0).
  - The values are frozen at the pinned litellm version (`~=1.103.1`) because `LITELLM_LOCAL_MODEL_COST_MAP=True`.

### F3 — Node-config JSON-Schema subset does not enforce nested `required` / `additionalProperties`
- **Impact:** MEDIUM for structured output; LOW for config.
- **Resolution:** WORKAROUND for tool config (`_tool` runtime check). OPEN for nested structured-output schemas.
- **Notes:** `_check_json_type` recurses into `properties` for type and constraint checks, but `required` is checked only at the top level (`validate_config`). A nested object missing required keys passes the node's check. Providers that enforce `json_schema` natively still enforce it.

### F4 — `validate_config` on a non-mapping value
- **Impact:** LOW
- **Resolution:** FIXED (in this phase's code)
- **Notes:** with a string, `validate_config` performs a substring test for `required` and then fails with `AttributeError`. `_structured` checks `isinstance(value, dict)` before validating, so a JSON string or list reply yields `LLM_STRUCTURED_OUTPUT_INVALID`. Covered by the test cases `'["a list"]'` and `'"score"'`.

### F5 — Workflow validation flags credential-shaped text in prompts
- **Impact:** LOW (desired behavior)
- **Resolution:** verified
- **Notes:** `plaintext_secret_paths` reports `.system_prompt` when the prompt contains `sk-…` (test `test_plaintext_keys_in_prompts_are_caught_by_workflow_validation`). A prompt that legitimately discusses such strings would be rejected at save.

### F6 — Free-tier upstream rate limits on OpenRouter
- **Impact:** LOW
- **Resolution:** OPEN (external)
- **Notes:**
  - Most live attempts against `qwen/qwen3.8-27b:free` returned HTTP 429 ("temporarily rate-limited upstream").
  - Each was normalized to `PROVIDER_RATE_LIMIT` → TRANSIENT, as designed.
  - Across repeated runs every one of the 5 smoke tests passed at least once, and every failure observed was a `RateLimitError`.

### F7 — Docker availability probe intermittently skips all Testcontainers tests (pre-existing)
- **Impact:** LOW
- **Resolution:** OPEN (pre-existing, phase 14 F8)
- **Notes:** in this phase two full runs reported `992 passed, 218 skipped`. Immediate reruns reported `1205 passed, 5 skipped`. Environmental; it does not concern phase 15 code.

---

## 10. Bugs / Problems Found

All problems below were introduced and fixed during this phase, before completion. No pre-existing product bugs were found.

| Problem | Root cause | Fix | Regression test | Area |
|---|---|---|---|---|
| Test expected `{% for x in y %}` to be rejected at publish | Wrong test expectation: the grammar is not Jinja, so `{%` is literal text by design | The case was moved to `test_render` as literal passthrough | `test_render[{% for x in y %}${lead}-...]` | tests |
| `test_provider_model_and_parameters_reach_the_request` failed with `LLM_STRUCTURED_OUTPUT_INVALID` | The test set `output_schema` while the fake replied `"Hello!"` | The fake now replies `"{}"` | same test | tests |
| Smoke tests could not use `run_node` | That fixture lives in `tests/nodes/conftest.py` and is not visible to `tests/external` | A local `run_llm_node` helper uses `make_context` and validates config and outputs | smoke tests | tests |
| `simple(model=...)` collided with the positional `model` argument | Helper signature in the adapter tests | `simple(model="gpt-test", **kwargs)` | adapter tests | tests |
| ruff E501 and I001 | Line length and import order | Split line; `ruff --fix` | lint gate | style |

---

## 11. Fixes Applied

1. **Non-object structured replies.**
   - **Symptom:** a JSON string or list reply would hit the substring/`AttributeError` path in `validate_config` (F4).
   - **Implementation:** an explicit `isinstance(value, dict)` check that raises `NodeConfigurationError("reply is not a JSON object")`, mapped to `LLM_STRUCTURED_OUTPUT_INVALID`.
   - **Verification:** parametrized structured-output failure tests.
2. **Prompt-injection guidance.**
   - **Symptom (review):** variables can be interpolated into `system_prompt`, while backend AGENTS §36 forbids untrusted data reaching prompts as trusted system instructions.
   - **Implementation:** the module docstring states that untrusted input belongs in `user_prompt`. Rendering cannot enforce this.
   - **Verification:** documentation only (§20, L7).
3. **Phase 14 open issue: no test guarded `LITELLM_LOCAL_MODEL_COST_MAP`.**
   - **Implementation:** `test_litellm_is_imported_with_the_local_cost_map`.
   - **Verification:** it passes.

---

## 12. Testing & Verification

### Unit tests
- **`tests/nodes/test_llm_node.py` (rendering part):**
  - 9 render cases: nested paths, whitespace variants, list index, `null`/numbers as JSON, whole objects as JSON, literal single braces, `{% %}`/`${}` literal passthrough;
  - 5 missing-variable paths (missing root, missing key, index out of range, key on a string, non-digit index on a list), asserting code, kind and details;
  - dunder paths are key lookups;
  - 8 malicious or malformed templates rejected at publish for both prompt keys: `__import__(...)`, `| upper`, `a + b`, `['name']`, `name()`, `{{ }}`, unbalanced, triple braces;
  - `PROMPT_TEMPLATE_INVALID` at render.
  - **Result:** PASS.
- **`tests/domain/test_ai.py`:** `response_schema` of `{"type": "array"}` and `{}` rejected. **Result:** PASS.

### Component tests (deterministic `FakeLLM`)
All in `tests/nodes/test_llm_node.py`. **Result:** 72/72 PASS. Coverage:
- **Variables and limits:** variables via the input port into system and user messages; a missing variable prevents the provider call; `PROMPT_TOO_LARGE` prevents the provider call.
- **Request construction:**
  - provider and model selection;
  - temperature, max_tokens, timeout, credential, tools and response_schema passthrough;
  - no system message when the system prompt is absent;
  - the node's own resolver is passed to the client;
  - defaults leave optional parameters unset.
- **Config:** invalid config → `LLM_CONFIGURATION_INVALID` (tool without a name, array output schema, invalid credential name); 9 config-schema rejections (missing required keys, empty prompt, temperature 3, max_tokens 0, plaintext credential, bad tool name, unknown key `api_key`); `LLM_NOT_CONFIGURED`.
- **Outputs:** exact outputs, including usage totals and `cost: None`; cost as `{"amount", "currency": "USD", "estimated": true}`; tool calls returned with `structured: None` and `finish_reason: tool_calls`.
- **Structured output:** 3 accepted shapes (plain and fenced); 8 rejected shapes (not JSON, `None`, list, string, missing required, wrong type, out of range, not in enum).
- **Errors:** all 10 `ProviderErrorKind`s → kind, code, message, details and `retryable`; credential `NodeError` passthrough (identity).

### Contract tests
- **`tests/nodes/test_builtin_contract.py`:**
  - `core.llm` in the exact built-in set;
  - the shared contract suite (closed schema, fresh instance per attempt, semver);
  - the truthful capability set (`supports_cancellation`);
  - the container wires `container.ai` into `core.llm`.
- **`tests/providers/test_litellm_adapter.py`** (real litellm against a loopback server):
  - `response_format` mapping, and its absence by default;
  - no cost behind a custom endpoint;
  - bundled-map estimate positive for `gpt-4o` and `None` for an unknown model;
  - the cost-map env var is set.
- **Result:** PASS.

### Integration tests (PostgreSQL 17.2 via Testcontainers)
- **`tests/integration/test_llm_node_runtime.py`:** `WorkflowService` publish → `ExecutionService` → `ExecutionRuntime(credentials=CredentialService)` → `core.llm` → `AIService` → `LiteLLMAdapter` → `LocalHttpServer`.
  1. The persisted execution is COMPLETED, with:
     - `structured` propagated to `core.output`;
     - usage `12/3/15` persisted and `cost: null`;
     - `core.llm` 1.0.0 recorded in `node_versions`;
     - the exact rendered user prompt on the wire, plus `temperature`, `max_tokens` and `response_format`;
     - the key only in the `Authorization` header and absent from the request body and the execution.
  2. A missing variable leaves the execution FAILED with `PROMPT_VARIABLE_MISSING`/VALIDATION and zero provider requests.
  3. Provider 429 with the key echoed → `PROVIDER_RATE_LIMIT`/TRANSIENT persisted, key absent from the message.
  4. A malicious template raises `WorkflowValidationFailedError` at publish, with zero provider requests.
- **Result:** 4/4 PASS.

### Security tests
All in `tests/nodes/test_llm_node.py`. **Result:** PASS.
- A key echoed by the model is masked in outputs. This goes through `execute_node` with the registry registration and real `NodeCredentials`; the output text becomes `your key is [REDACTED]`.
- A key echoed in a `ProviderError` is masked in the persisted `ExecutionError`.
- A credential reference passed through `variables` renders as plain text, and the node's resolver refuses it (`CREDENTIAL_NOT_REFERENCED`).
- A plaintext `sk-…` in a prompt is flagged by `plaintext_secret_paths`.
- The malicious-template and closed-schema tests above.

### External smoke tests (opt-in, OpenRouter, manual)
- **`tests/external/test_openrouter_smoke.py`:** 3 existing tests plus 2 new ones:
  - `test_node_structured_output`: template, system prompt, `output_schema`, usage > 0, cost `None` or estimated;
  - `test_node_tool_call`: exactly one `get_weather` call with a `city` argument.
- **Run details:** model `qwen/qwen3.8-27b:free`, key supplied via `AKASHA_SMOKE_OPENROUTER_API_KEY` only.
- **Result:** each test passed at least once across repeated runs. All failures were upstream `RateLimitError` (F6).

### Not performed
- API tests: no endpoints changed.
- E2E, performance, accessibility: not applicable to this phase.
- Live verification of the `openai`, `anthropic` and `ollama` routes, and of the OpenAI-compatible route.

---

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 134 files already formatted |
| Linter (`uv run ruff check .`) | PASS | E501 and I001 were hit during development and fixed |
| Type Checker (`uv run mypy .`, strict) | PASS | 132 source files, no issues |
| Unit + component tests | PASS | Part of the full suite |
| Integration tests (Testcontainers PostgreSQL) | PASS | Final full run: `1205 passed, 5 skipped` (skips are the opt-in external tests). Two earlier runs skipped the 218 Docker tests (F7); the reruns passed them all |
| API tests | PASS (unchanged) | Existing API tests are in the full suite; none were added |
| E2E tests | N/A | Not in phase scope |
| Build | N/A | No build step for the backend package beyond `uv` install, which succeeded |
| Security checks | PASS | Security tests in §12; no dependency audit tool exists in the repo |
| External smoke | PASS (manual, opt-in) | With free-tier 429 retries |

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| An LLM Node can be placed into a Workflow and executed through the normal runtime | PASS | `tests/integration/test_llm_node_runtime.py::test_llm_node_runs_through_the_runtime`: the graph is published through `WorkflowService` and executed by the unmodified `ExecutionRuntime` against PostgreSQL; the persisted execution is COMPLETED with the `core.llm` outputs propagated |

Roadmap scope items, checked individually:

| Scope Item | Status | Evidence |
|---|---|---|
| provider | PASS | `config.provider` → `LLMRequest.provider`; routed by `AIService`; test `test_provider_model_and_parameters_reach_the_request` |
| model | PASS | Same test; the model appears on the wire in the adapter/integration tests |
| system prompt | PASS | `test_variables_come_from_the_input_port`; integration test |
| user prompt | PASS | Same |
| variables | PASS | `render` tests; integration test (rendered prompt on the wire) |
| temperature | PASS | Passthrough test; integration wire assertion |
| max tokens | PASS | Same |
| structured output where supported | PASS with limitation | `response_format` mapping test; node validation tests; live smoke. Nested `required` is not validated by the node (F3) |
| tool calls where supported | PASS | Returned-not-executed test; adapter tool mapping (phase 14); live smoke |
| prompt rendering (controlled) | PASS | Grammar plus malicious-template tests; publish-time rejection in integration |
| token usage | PASS | Exact metadata test; persisted usage in integration |
| cost estimation | PASS | `CostEstimate` flagged `estimated: true`; bundled-map test; `null` when unknown |

Required tests: prompt rendering, variable substitution, missing variables, provider errors, token usage, cost calculation and structured output are all present (§12). The extra ones requested in the phase prompt (provider and model selection, tool calls, secret safety, cost metadata) are also present.

---

## 15. Security Considerations

- **Expression evaluation:**
  - There is no `eval`/`exec`/`getattr`. Templates are regex-parsed paths over plain data.
  - Malicious inputs (`__import__`, calls, operators, subscripts, filters) are rejected at publish and at render.
  - Dunder segments can only match dictionary keys.
- **Secrets and credentials:**
  - The config holds only `credential:` references (schema pattern, plus `LLMRequest` validation, plus workflow-level `plaintext_secret_paths`).
  - Resolution happens inside `AIService` through the node's own `NodeCredentials`, so references arriving via `variables` are refused.
  - Resolved keys are registered for known-value redaction. A key echoed in model output or provider errors is masked by the existing executor redaction (tested).
- **Logging:** the node does not log prompts, variables or completions. `AIService` logs counts and identifiers only (AGENTS §76).
- **Prompt injection (L7):** interpolated values are data but reach the model as prompt text. Rendering cannot distinguish trusted from untrusted content; the docstring directs untrusted input to `user_prompt`. Not enforceable at this layer.
- **Resource limits:**
  - rendered prompt ≤ 400k characters;
  - `max_tokens` ≤ 100 000;
  - tools ≤ 32;
  - `timeout_seconds` ≤ 600, and never longer than the provider timeout.
- **Error disclosure:** provider messages are redacted and truncated by the adapter (phase 14). The node forwards only the message, code and provider name.
- **Real key in the development chat:** the OpenRouter key used for the smoke runs was pasted into the conversation (again, as in phase 14). It was written only to a session scratchpad file outside the repository, passed via environment variable, and the file was deleted afterwards. A search of `backend/src`, `backend/tests` and `docs` finds no occurrence. The key should be rotated.
- **Not applicable:** authentication/authorization (Phase 20), XSS/CSRF, path traversal, SSRF (provider endpoints are operator configuration, not node config).

---

## 16. Performance Considerations

- Rendering is linear in the template size. The publish-time grammar regex uses disjoint alternatives, so there is no catastrophic backtracking. It was not benchmarked.
- The prompt cap bounds memory per call. Outputs are bounded by `max_tokens` and are persisted in full in `NodeExecution.outputs`. There is no artifact offloading (Phase 24).
- `cost_per_token` is a local dictionary lookup. The first LLM call per process still pays the ~6.5 s litellm import (phase 14 F2) inside its timeout.
- `NodeConfigSchema(output_schema)` is constructed twice per call. This is negligible, and no measurement was taken.
- No performance tests were run.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §30 | Record the implementation | `core.llm` config, template grammar and limits, outputs, cost-estimate policy, provider error mapping |
| `docs/architecture.md` §113 | Metrics status | `llm_tokens_total`/`llm_cost` are not emitted; usage and cost are persisted in node outputs and logged; the metrics need runtime wiring (Phases 21/22) |
| `docs/reports/backend/phase-15-llm-node.md` | Phase report | This document |
| `backend/src/akasha/nodes/llm.py` (docstring) | Maintainers | Layering, template semantics, tool calls not executed, prompt-trust guidance |

**Should be updated later:**
- `backend/README.md`/operator docs: configuring LLM providers, and using `core.llm` with a stored credential. The phase 14 recommendation to document `AKASHA_LLM_*` and smoke variables is still open.
- `backend/AGENTS.md` §22: its example built-in list does not include `core.llm`. The list is labelled exemplary, so it was left unchanged.

---

## 18. Dependencies / Infrastructure Changes

- **New or removed dependencies:** none. `pyproject.toml` and `uv.lock` are unchanged. `jsonschema` (transitively present via litellm) was deliberately not imported (§5.4).
- **Configuration and environment variables:** none new. `LITELLM_LOCAL_MODEL_COST_MAP` (set by phase 14 code) now also governs the price data used for cost estimates.
- **Docker, database, Redis:** no changes.
- **Test infrastructure:** no new fixtures. Existing ones are reused: `http_server`/`LocalHttpServer` with its `/v1/chat/completions` handler, `database`/`clean_db`, and `run_node`/`make_context`. Smoke-test environment variables are unchanged (`AKASHA_SMOKE_OPENROUTER_API_KEY`, `_MODEL`, `_BASE_URL`); the new node smoke tests do not use `_BASE_URL` (§5.3).

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Blocker | Affects completion |
|---|---|---|---|---|
| `llm_tokens_total` / `llm_cost_estimated` metrics | `AIService` and nodes have no access to `EngineMetrics`; the runtime/container metrics wiring is pending | 21/22 | Runtime wiring | No |
| Retries for TRANSIENT/TIMEOUT LLM errors | The runtime has no retries | 23 | Retry policy | No |
| General Expression Engine (`{{ nodes.x.output }}`, execution input access) | Not a Phase 15 item | Unscheduled; hardened in the security pass | — | No |
| Streaming, multi-turn messages, tool execution / agent loop | Not in Phase 15 scope | Not on the roadmap | — | No |
| Full JSON Schema for structured output | Subset reused (§5.4) | When required | Dependency decision | No |
| Publish-time validation of provider names | Node definitions are static per type; provider names are deployment config | Would need publish-time context | — | No |
| Provider capability metadata (supports tools / structured output, §134) | No consumer yet | Unscheduled | — | No |

---

## 20. Known Limitations

1. **L1:** there is no escape syntax for a literal `{{` in prompts.
2. **L2:** templates see only the `variables` input. Combining several upstream outputs needs an upstream object-building node, and none exists.
3. **L3:** an unknown provider name surfaces at execution (`PROVIDER_CONFIGURATION`), not at publish.
4. **L4:** structured-output validation enforces only the engine's JSON-Schema subset. Nested `required`/`additionalProperties`, `oneOf`/`anyOf` and formats are not checked by the node (F3).
5. **L5:** cost estimates come from litellm's bundled price list at the pinned version. Custom endpoints always report `null`. Estimates ignore discounts, caching and free tiers.
6. **L6:** the node is single-turn (system + user). Tool calls are returned, not executed.
7. **L7:** prompt-injection separation (untrusted data in `user_prompt`) is guidance, not enforcement.
8. **L8:** fence stripping handles only a leading `` ```json ``/`` ``` `` and a trailing `` ``` ``, case-sensitive.
9. **L9:** a structured-output mismatch is PERMANENT. A different reply might pass, but the runtime has no retries anyway (Phase 23).

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended action | Target |
|---|---|---|---|---|---|
| Nested `required` not validated in structured output (F3) | Invalid nested replies pass the node's check on providers that do not enforce schemas | MEDIUM | `nodes/llm.py`, `domain/node.py` | Extend `_check_json_type` with nested `required`, or adopt `jsonschema` | Security pass or next touch of the schema validator |
| OpenRouter key exposed in chat | Key compromise risk | MEDIUM | Operations | Rotate the key | Now |
| Docker probe flakiness (F7, pre-existing) | False "all skipped" runs | LOW | Test infrastructure | Investigate `docker_available()` timeout | Unassigned |
| `openai`/`anthropic`/`ollama`/OpenAI-compatible routes not live-verified | Routing or format bugs would surface only in production | LOW | Providers | Per-provider opt-in smoke tests when keys or a local Ollama are available | Unassigned |
| LLM metrics not emitted | No aggregate token or cost observability | LOW | Observability | Wire `EngineMetrics` into the LLM path | 21/22 |

---

## 22. Architectural / Product Decisions

1. **Templates are a lookup grammar, not a language.** Chosen so that safety holds by construction, and enforced at publish via the config schema. A future Expression Engine must keep this grammar valid.
2. **One `variables` object input.** Keeps data flow visible in the graph and fits the static per-type Node Contract.
3. **Cost lives in the adapter; `null` beats wrong.** No estimate behind a custom `api_base`. Every estimate is marked `estimated: true`.
4. **The node re-validates structured output** even when the provider claims support, because "where supported" is not trustworthy per model.
5. **Tool calls are data.** The LLM node stays side-effect free and single-turn. Agent behavior would be a separate node or phase.
6. **Structured-output mismatch is PERMANENT.** A model that ignores a schema tends to keep doing so, and each attempt is billed. Revisit with the Phase 23 retry policy.
7. **No new dependencies** (no Jinja2, no direct `jsonschema`). The existing `NodeConfigSchema` subset and stdlib `re`/`json` suffice.
8. **`create_container` order changed:** `AIService` is built before the node registry, as the phase 14 handoff required.

---

## 23. Repository / Git State

- **Branch:** `main`.
- **HEAD:** `b2db4c7 backend phase 14 - ai provider architecture`. Phase 15 is uncommitted.
- **Modified:**
  - `backend/src/akasha/bootstrap.py`
  - `backend/src/akasha/domain/ai.py`
  - `backend/src/akasha/nodes/builtins.py`
  - `backend/src/akasha/providers/litellm_adapter.py`
  - `backend/tests/domain/test_ai.py`
  - `backend/tests/external/test_openrouter_smoke.py`
  - `backend/tests/nodes/test_builtin_contract.py`
  - `backend/tests/providers/test_litellm_adapter.py`
  - `docs/architecture.md`
- **Untracked:**
  - `backend/src/akasha/nodes/llm.py`
  - `backend/tests/integration/test_llm_node_runtime.py`
  - `backend/tests/nodes/test_llm_node.py`
  - `docs/reports/backend/phase-15-llm-node.md`
- **Size:** tracked diff `9 files changed, 238 insertions(+), 9 deletions(-)`, plus the new files (313 + 507 + 183 lines of code/tests).
- **Unrelated changes:** none detected. The working tree was clean at phase start.

---

## 24. Phase Completion Assessment

### Implemented
The `core.llm` node, with:
- provider/model selection, credential reference, system and user templates over `variables`;
- temperature, max tokens and timeout;
- structured output (request mapping and validation);
- tool calls (offered and returned);
- token usage and cost-estimate metadata;
- provider-error mapping;
- resource limits.

Also: `response_schema`/`CostEstimate` in the domain, `response_format` and cost estimation in the adapter, and registry/container wiring.

### Verified
86 new tests (72 component/unit, 4 PostgreSQL integration, 4 adapter contract, 2 domain, 2 contract suite, 2 opt-in smoke). The full suite passes: 1205 passed, 5 opt-in skipped. Lint, format and strict mypy pass. Live OpenRouter smoke passed with retries on 429.

### Not Implemented
- LLM metrics;
- full JSON Schema validation;
- publish-time provider-name validation;
- streaming, multi-turn and tool execution.

### Deferred
See §19.

### Open
- nested-`required` validation gap (F3);
- key rotation;
- Docker probe flakiness (pre-existing);
- unverified provider routes.

### Blocking Issues
None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

**The next phase (Phase 16 — Python Node SDK) can assume:**
- `core.llm` is a regular registry node. It is a reference example of a node with an injected dependency: `LlmNode(llm)` is registered via a factory closure and depends only on the domain port `LLMClient`.
- LLM access for any node goes through `akasha.domain.ai.LLMClient.complete(request, credentials=context.credentials)`. An SDK `Credentials`/LLM facade should wrap this, not litellm.
- `LLMRequest.response_schema` and `LLMResponse.cost` exist. Cost is always an estimate or `None`.
- `render()` in `akasha.nodes.llm` is the only template mechanism. It is not a public SDK API yet. If exposed, keep the grammar (§8) stable.

**Must not be changed casually:**
- the `core.llm` output port names and the `metadata` shape (§7), since executions persist them;
- the error codes `PROMPT_*` and `LLM_*`, and the `PROVIDER_*` kind mapping;
- `"estimated": true` on cost;
- no cost behind a custom `api_base`;
- the template grammar;
- the rule that the node never evaluates template content.

**Known constraints:**
- the Node Contract has static per-type ports, which is why variables use a single object input;
- the config-schema subset does not check nested `required`;
- the runtime has no retries.

**Prerequisites:**
- none blocking;
- rotate the exposed OpenRouter key;
- the running engine still needs the Phase 21/22 wiring (`ExecutionRuntime(credentials=container.credentials, metrics=container.metrics)`) for `core.llm` to resolve credentials outside tests.
