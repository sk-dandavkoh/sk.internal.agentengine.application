"""core.llm against a deterministic fake ``LLMClient`` (AGENTS §27): prompt
rendering, request construction, output normalization, structured output,
tool calls, error mapping and secret safety. No provider is contacted."""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest

from akasha.domain.ai import (
    CostEstimate,
    FinishReason,
    LLMRequest,
    LLMResponse,
    ProviderError,
    ProviderErrorKind,
    Role,
    TokenUsage,
    ToolCall,
)
from akasha.domain.credentials import Secret, plaintext_secret_paths
from akasha.domain.execution import ExecutionError
from akasha.domain.node import CredentialResolver, NodeError, NodeErrorKind
from akasha.errors import NodeConfigurationError
from akasha.nodes.builtins import create_builtin_registry
from akasha.nodes.llm import MAX_PROMPT_CHARS, LlmNode, render
from akasha.runtime.credentials import NodeCredentials
from akasha.runtime.executor import execute_node
from akasha.security.redaction import MASK
from tests.support import RunNode, make_context

REF = "credential:openai-prod"
BASE = {"provider": "openai", "model": "gpt-test", "user_prompt": "Hi"}


def reply(
    content: str | None = "Hello!",
    *,
    tool_calls: tuple[ToolCall, ...] = (),
    cost: CostEstimate | None = None,
) -> LLMResponse:
    return LLMResponse(
        provider="openai",
        model="gpt-test-2026",
        content=content,
        tool_calls=tool_calls,
        usage=TokenUsage(input_tokens=12, output_tokens=3),
        finish_reason=FinishReason.TOOL_CALLS if tool_calls else FinishReason.STOP,
        request_id="req-1",
        cost=cost,
    )


@dataclass
class FakeLLM:
    """Records every request; answers with ``response`` or raises ``error``.
    With ``echo_credential`` it resolves the key like the AIService does and
    puts it into the reply (a model repeating a leaked key)."""

    response: LLMResponse = field(default_factory=reply)
    error: Exception | None = None
    echo_credential: bool = False
    requests: list[LLMRequest] = field(default_factory=list)
    resolvers: list[CredentialResolver | None] = field(default_factory=list)

    async def complete(
        self, request: LLMRequest, *, credentials: CredentialResolver | None = None
    ) -> LLMResponse:
        self.requests.append(request)
        self.resolvers.append(credentials)
        if self.echo_credential:
            assert credentials is not None and request.credential is not None
            key = await credentials.resolve(request.credential)
            return reply(f"your key is {key}")
        if self.error is not None:
            raise self.error
        return self.response


async def run(
    run_node: RunNode, llm: FakeLLM, *, inputs: dict[str, Any] | None = None, **cfg: Any
) -> dict[str, Any]:
    result = await run_node(LlmNode(llm), config={**BASE, **cfg}, inputs=inputs)
    return dict(result.outputs)


# -- prompt rendering ---------------------------------------------------------


LEAD = {"lead": {"name": "Ada", "tags": ["vip", "eu"], "score": 0.9, "x": None}}


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("Hello {{ lead.name }}!", "Hello Ada!"),
        ("{{lead.name}}/{{  lead.name  }}", "Ada/Ada"),
        ("first tag: {{ lead.tags.0 }}", "first tag: vip"),
        ("{{ lead.score }} {{ lead.x }}", "0.9 null"),
        ("{{ lead.tags }}", '["vip", "eu"]'),
        (
            "Analyze:\n{{ lead }}",
            'Analyze:\n{"name": "Ada", "tags": ["vip", "eu"], "score": 0.9, "x": null}',
        ),
        ('JSON like {"a": {"b": 1}} stays', 'JSON like {"a": {"b": 1}} stays'),
        ("no placeholders", "no placeholders"),
        # Not a template language: other syntaxes are literal text.
        ("{% for x in y %}${lead}", "{% for x in y %}${lead}"),
    ],
)
def test_render(template: str, expected: str) -> None:
    assert render(template, LEAD) == expected


@pytest.mark.parametrize(
    "path", ["missing", "lead.missing", "lead.tags.2", "lead.name.first", "lead.tags.x"]
)
def test_missing_variable_is_a_validation_error(path: str) -> None:
    with pytest.raises(NodeError) as exc:
        render(f"x {{{{ {path} }}}} y", LEAD)
    assert exc.value.code == "PROMPT_VARIABLE_MISSING"
    assert exc.value.kind is NodeErrorKind.VALIDATION
    assert exc.value.details == {"variable": path}


def test_dunder_paths_are_key_lookups_not_attribute_access() -> None:
    # Only mapping keys and list indexes resolve: no attribute traversal.
    with pytest.raises(NodeError, match="missing"):
        render("{{ lead.__class__ }}", LEAD)
    assert render("{{ __init__ }}", {"__init__": "data"}) == "data"


@pytest.mark.parametrize(
    "template",
    [
        "{{ __import__('os').system('id') }}",
        "{{ lead.name | upper }}",
        "{{ a + b }}",
        "{{ lead['name'] }}",
        "{{ lead.name() }}",
        "{{ }}",
        "{{ lead.name }",
        "{{{ lead }}}",
    ],
)
def test_malicious_or_malformed_templates_fail_at_publish(template: str) -> None:
    definition = LlmNode.definition
    with pytest.raises(NodeConfigurationError):
        definition.validate_config({**BASE, "user_prompt": template})
    with pytest.raises(NodeConfigurationError):
        definition.validate_config({**BASE, "system_prompt": template})


def test_invalid_placeholder_is_rejected_at_render_too() -> None:
    # Defense in depth for configs that bypassed publish validation.
    with pytest.raises(NodeError) as exc:
        render("{{ a + b }}", {"a": 1, "b": 2})
    assert exc.value.code == "PROMPT_TEMPLATE_INVALID"


async def test_variables_come_from_the_input_port(run_node: RunNode) -> None:
    llm = FakeLLM()
    await run(
        run_node,
        llm,
        inputs={"variables": LEAD},
        system_prompt="You score {{ lead.name }}.",
        user_prompt="Lead:\n{{ lead }}",
    )
    (request,) = llm.requests
    system, user = request.messages
    assert (system.role, system.content) == (Role.SYSTEM, "You score Ada.")
    assert user.role is Role.USER
    assert user.content is not None and user.content.startswith('Lead:\n{"name": "Ada"')


async def test_missing_variable_fails_before_the_provider_is_called(
    run_node: RunNode,
) -> None:
    llm = FakeLLM()
    with pytest.raises(NodeError, match="lead"):
        await run(run_node, llm, user_prompt="{{ lead.name }}")  # no variables
    assert llm.requests == []


async def test_oversized_rendered_prompt_is_rejected(run_node: RunNode) -> None:
    llm = FakeLLM()
    big = {"blob": "x" * MAX_PROMPT_CHARS}
    with pytest.raises(NodeError) as exc:
        await run(run_node, llm, inputs={"variables": big}, user_prompt="{{ blob }}!")
    assert exc.value.code == "PROMPT_TOO_LARGE"
    assert llm.requests == []


# -- request construction -------------------------------------------------------


async def test_provider_model_and_parameters_reach_the_request(
    run_node: RunNode,
) -> None:
    llm = FakeLLM(reply("{}"))
    resolver = NodeCredentials(_Store("k"), {"credential": REF})
    tool = {"name": "lookup", "description": "Find a lead", "parameters": _OBJ}
    await run_node(
        LlmNode(llm),
        config={
            "provider": "anthropic",
            "model": "claude-test",
            "user_prompt": "Hi",
            "credential": REF,
            "temperature": 0.2,
            "max_tokens": 64,
            "timeout_seconds": 30,
            "output_schema": _OBJ,
            "tools": [tool],
        },
        credentials=resolver,
    )
    (request,) = llm.requests
    assert (request.provider, request.model) == ("anthropic", "claude-test")
    assert (request.temperature, request.max_tokens) == (0.2, 64)
    assert request.timeout_seconds == 30
    assert request.credential == REF
    assert request.response_schema == _OBJ
    assert [(t.name, t.description) for t in request.tools] == [
        ("lookup", "Find a lead")
    ]
    assert [m.role for m in request.messages] == [Role.USER]  # no system prompt
    # The node's own resolver is what authorizes the credential reference.
    assert llm.resolvers == [resolver]


async def test_defaults_leave_optional_parameters_to_the_provider(
    run_node: RunNode,
) -> None:
    llm = FakeLLM()
    await run(run_node, llm)
    (request,) = llm.requests
    assert request.temperature is None and request.max_tokens is None
    assert request.tools == () and request.response_schema is None
    assert request.credential is None


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"tools": [{"description": "no name"}]}, "LLM_CONFIGURATION_INVALID"),
        ({"output_schema": {"type": "array"}}, "LLM_CONFIGURATION_INVALID"),
    ],
)
async def test_invalid_configuration_is_a_configuration_error(
    run_node: RunNode, override: dict[str, Any], code: str
) -> None:
    llm = FakeLLM()
    with pytest.raises(NodeError) as exc:
        await run(run_node, llm, **override)
    assert (exc.value.code, exc.value.kind) == (code, NodeErrorKind.CONFIGURATION)
    assert llm.requests == []


@pytest.mark.parametrize(
    "config",
    [
        {"provider": "openai", "user_prompt": "x"},  # no model
        {"model": "m", "user_prompt": "x"},  # no provider
        {"provider": "openai", "model": "m"},  # no prompt
        {**BASE, "user_prompt": ""},
        {**BASE, "temperature": 3},
        {**BASE, "max_tokens": 0},
        {**BASE, "credential": "sk-plaintext-key-0123456789"},
        {**BASE, "credential": "credential:Not Valid"},
        {**BASE, "tools": [{"name": "bad name!", "parameters": {}}]},
        {**BASE, "api_key": "x"},  # closed schema
    ],
)
def test_config_schema_rejects(config: dict[str, Any]) -> None:
    with pytest.raises(NodeConfigurationError):
        LlmNode.definition.validate_config(config)


async def test_without_an_llm_service_the_node_fails_as_configuration(
    run_node: RunNode,
) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(LlmNode(), config=BASE)
    assert exc.value.code == "LLM_NOT_CONFIGURED"
    assert exc.value.kind is NodeErrorKind.CONFIGURATION


# -- outputs ----------------------------------------------------------------------


async def test_text_usage_and_metadata(run_node: RunNode) -> None:
    outputs = await run(run_node, FakeLLM())
    assert outputs == {
        "text": "Hello!",
        "structured": None,
        "tool_calls": [],
        "metadata": {
            "provider": "openai",
            "model": "gpt-test-2026",
            "request_id": "req-1",
            "finish_reason": "stop",
            "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
            "cost": None,  # unknown pricing is reported as unknown, not zero
        },
    }


async def test_cost_is_reported_as_an_estimate(run_node: RunNode) -> None:
    llm = FakeLLM(reply(cost=CostEstimate(0.00042)))
    outputs = await run(run_node, llm)
    assert outputs["metadata"]["cost"] == {
        "amount": 0.00042,
        "currency": "USD",
        "estimated": True,
    }


async def test_tool_calls_are_returned_not_executed(run_node: RunNode) -> None:
    call = ToolCall("call_1", "lookup", {"email": "ada@example.com"})
    llm = FakeLLM(reply(None, tool_calls=(call,)))
    outputs = await run(
        run_node,
        llm,
        output_schema=_OBJ,  # a tool call answers the turn: nothing to parse
        tools=[{"name": "lookup", "parameters": _OBJ}],
    )
    assert outputs["text"] is None
    assert outputs["structured"] is None
    assert outputs["tool_calls"] == [
        {"id": "call_1", "name": "lookup", "arguments": {"email": "ada@example.com"}}
    ]
    assert outputs["metadata"]["finish_reason"] == "tool_calls"


# -- structured output ------------------------------------------------------------

_OBJ: dict[str, Any] = {"type": "object", "properties": {}}
SCORE = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "label": {"type": "string", "enum": ["hot", "cold"]},
    },
    "required": ["score", "label"],
}


@pytest.mark.parametrize(
    "content",
    [
        '{"score": 0.8, "label": "hot"}',
        '```json\n{"score": 0.8, "label": "hot"}\n```',
        '  ```\n{"score": 0.8, "label": "hot"}```  ',
    ],
)
async def test_structured_output_is_parsed(run_node: RunNode, content: str) -> None:
    outputs = await run(run_node, FakeLLM(reply(content)), output_schema=SCORE)
    assert outputs["structured"] == {"score": 0.8, "label": "hot"}
    assert outputs["text"] == content  # the raw reply stays available


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        None,
        '["a list"]',
        '"score"',
        '{"score": 0.8}',
        '{"score": "high", "label": "hot"}',
        '{"score": 2, "label": "hot"}',
        '{"score": 0.5, "label": "warm"}',
    ],
)
async def test_structured_output_violating_the_schema_fails(
    run_node: RunNode, content: str | None
) -> None:
    with pytest.raises(NodeError) as exc:
        await run(run_node, FakeLLM(reply(content)), output_schema=SCORE)
    assert exc.value.code == "LLM_STRUCTURED_OUTPUT_INVALID"
    assert exc.value.kind is NodeErrorKind.PERMANENT


# -- provider errors ------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider_kind", "node_kind"),
    [
        (ProviderErrorKind.RATE_LIMIT, NodeErrorKind.TRANSIENT),
        (ProviderErrorKind.UNAVAILABLE, NodeErrorKind.TRANSIENT),
        (ProviderErrorKind.TIMEOUT, NodeErrorKind.TIMEOUT),
        (ProviderErrorKind.AUTHENTICATION, NodeErrorKind.CONFIGURATION),
        (ProviderErrorKind.CONFIGURATION, NodeErrorKind.CONFIGURATION),
        (ProviderErrorKind.CONTEXT_LENGTH, NodeErrorKind.PERMANENT),
        (ProviderErrorKind.INVALID_REQUEST, NodeErrorKind.PERMANENT),
        (ProviderErrorKind.CONTENT_POLICY, NodeErrorKind.PERMANENT),
        (ProviderErrorKind.INVALID_RESPONSE, NodeErrorKind.PERMANENT),
        (ProviderErrorKind.UNKNOWN, NodeErrorKind.PERMANENT),
    ],
)
async def test_provider_errors_map_to_node_errors(
    run_node: RunNode, provider_kind: ProviderErrorKind, node_kind: NodeErrorKind
) -> None:
    error = ProviderError("upstream said no", kind=provider_kind, provider="openai")
    with pytest.raises(NodeError) as exc:
        await run(run_node, FakeLLM(error=error))
    assert exc.value.kind is node_kind
    assert exc.value.code == f"PROVIDER_{provider_kind.name}"
    assert exc.value.message == "upstream said no"
    assert exc.value.details == {"provider": "openai"}
    assert exc.value.retryable is (
        node_kind in {NodeErrorKind.TRANSIENT, NodeErrorKind.TIMEOUT}
    )


async def test_credential_errors_pass_through_unchanged(run_node: RunNode) -> None:
    denied = NodeError(
        "not referenced", kind=NodeErrorKind.CONFIGURATION, code="CREDENTIAL_X"
    )
    with pytest.raises(NodeError) as exc:
        await run(run_node, FakeLLM(error=denied))
    assert exc.value is denied


# -- secret safety -----------------------------------------------------------------

CANARY = "canary-llm-node-7f3a-never-leak"


class _Store:
    def __init__(self, value: str) -> None:
        self._value = value

    async def resolve(self, reference: str) -> Secret:
        return Secret(self._value)


async def _execute(llm: FakeLLM, config: dict[str, Any]) -> Any:
    node = LlmNode(llm)
    context = make_context(
        node, config=config, credentials=NodeCredentials(_Store(CANARY), config)
    )
    registration = create_builtin_registry(llm=llm).get("core.llm")
    return await execute_node(registration, context, limit=timedelta(seconds=5))


async def test_a_key_echoed_by_the_model_is_masked_in_outputs() -> None:
    outcome = await _execute(FakeLLM(echo_credential=True), {**BASE, "credential": REF})
    assert not isinstance(outcome, ExecutionError)
    assert CANARY not in str(outcome.outputs)
    assert outcome.outputs["text"] == f"your key is {MASK}"


async def test_a_key_echoed_in_a_provider_error_is_masked() -> None:
    class Leaky(FakeLLM):
        async def complete(
            self, request: LLMRequest, *, credentials: CredentialResolver | None = None
        ) -> LLMResponse:
            assert credentials is not None and request.credential is not None
            key = await credentials.resolve(request.credential)
            raise ProviderError(
                f"bad key {key}",
                kind=ProviderErrorKind.AUTHENTICATION,
                provider="openai",
            )

    outcome = await _execute(Leaky(), {**BASE, "credential": REF})
    assert isinstance(outcome, ExecutionError)
    assert outcome.code == "PROVIDER_AUTHENTICATION"
    assert CANARY not in outcome.message and MASK in outcome.message


async def test_a_reference_in_variables_is_neither_resolved_nor_authorized() -> None:
    # Data flowing through the graph must never pick which secret unlocks:
    # a reference in the prompt is plain text, and only config references
    # are resolvable by the node's resolver.
    llm = FakeLLM()
    node = LlmNode(llm)
    resolver = NodeCredentials(_Store(CANARY), BASE)
    context = make_context(
        node,
        config={**BASE, "user_prompt": "{{ ref }}"},
        inputs={"variables": {"ref": REF}},
        credentials=resolver,
    )
    await node.execute(context)
    assert llm.requests[0].messages[0].content == REF
    with pytest.raises(NodeError) as exc:
        await resolver.resolve(REF)
    assert exc.value.code == "CREDENTIAL_NOT_REFERENCED"


def test_plaintext_keys_in_prompts_are_caught_by_project_validation() -> None:
    config = {**BASE, "system_prompt": "use sk-live-abcdefghijklmnopqrstuvwxyz"}
    assert plaintext_secret_paths(config) == [".system_prompt"]
    assert plaintext_secret_paths({**BASE, "credential": REF}) == []


def test_registry_injects_the_llm_client() -> None:
    llm = FakeLLM()
    node = create_builtin_registry(llm=llm).get("core.llm")()
    assert isinstance(node, LlmNode)
    assert node._llm is llm
