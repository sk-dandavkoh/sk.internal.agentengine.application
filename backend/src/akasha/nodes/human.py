"""core.human: ask a person to approve, reject or fill in data, then continue.

One node covers approval, review and form input (core node portfolio): the
response schema is ``decision`` (when ``approval`` is on), an optional
``comment`` and the author's ``fields``. Routing is control flow:

    approved ── the value, when approved (or submitted with approval off)
    rejected ── the value, when rejected
    timed_out ─ the value, when nobody answered within ``timeout_seconds``
    response ── the answer itself plus who gave it (not on timeout)

Escalation is composition: wire ``timed_out`` to core.smtp, or to a second
core.human. The node never sleeps: its first attempt returns ``NodeWait``
and the runtime parks the execution WAITING in the database until
``POST /executions/{id}/inputs/{node}`` (editors of the workflow) or the
deadline resumes it; the second attempt reads ``context.resume``.

Not an agent tool (``waits_for_input``): a tool call cannot suspend.
"""

from datetime import timedelta
from typing import Any

from akasha.nodes.core import VERSION, closed_schema, value_in
from akasha.nodes.llm import TEMPLATE, render
from akasha.sdk import (
    Node,
    NodeCapabilities,
    NodeConfigSchema,
    NodeContractError,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    NodeWait,
    PortType,
)

J = PortType.JSON
MAX_PROMPT_CHARS = 10_000
MAX_FIELDS = 32
MAX_TIMEOUT_SECONDS = 365 * 24 * 3600
_DECISIONS = ["approve", "reject"]
# Field value types the response validator applies (NodeConfigSchema).
_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}


class HumanNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.human",
            name="Human Input",
            description="Waits for a person to approve, reject or fill in data.",
            version=VERSION,
            category="human_interaction",
        ),
        inputs=(value_in(required=False),),
        outputs=(
            NodeOutput("approved", J, nullable=True),
            NodeOutput("rejected", J, nullable=True),
            NodeOutput("timed_out", J, nullable=True),
            NodeOutput(
                "response",
                PortType.OBJECT,
                description="decision, comment, the fields and responded_by.",
            ),
        ),
        config_schema=closed_schema(
            {
                "prompt": {
                    **TEMPLATE,
                    "minLength": 1,
                    "description": "Shown to whoever answers. Reference the"
                    " input as {{ value }} or {{ value.subject }}.",
                },
                "approval": {
                    "type": "boolean",
                    "default": True,
                    "description": "Ask for approve/reject. Off: a form whose"
                    " submission continues on 'approved'.",
                },
                "fields": {
                    "type": "object",
                    "additionalProperties": {"type": "object"},
                    "description": "Extra inputs: field name to a JSON Schema"
                    " (string, number, integer, boolean, enum, ...).",
                },
                "required_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": MAX_FIELDS,
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                    "description": "Continue on 'timed_out' after this long."
                    " Empty: wait until the execution deadline.",
                },
            },
            "prompt",
        ),
        capabilities=NodeCapabilities(is_control_flow=True, waits_for_input=True),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult | NodeWait:
        value = context.inputs.get("value")
        resume = context.resume
        if resume is None:
            return self._ask(context, value)
        if resume["timed_out"]:
            return NodeResult({"timed_out": value})
        response: dict[str, Any] = dict(resume["response"])
        approved = (
            response.get("decision") == "approve"
            if context.config.get("approval", True)
            else True
        )
        return NodeResult(
            {
                "approved" if approved else "rejected": value,
                "response": response | {"responded_by": resume["responded_by"]},
            }
        )

    def _ask(self, context: NodeExecutionContext, value: Any) -> NodeWait:
        cfg = context.config
        prompt = render(cfg["prompt"], {"value": value})
        if len(prompt) > MAX_PROMPT_CHARS:
            prompt = prompt[:MAX_PROMPT_CHARS] + " [truncated]"
        properties: dict[str, Any] = {}
        required: list[str] = []
        if cfg.get("approval", True):
            properties["decision"] = {"type": "string", "enum": _DECISIONS}
            required.append("decision")
        properties["comment"] = {"type": "string"}
        fields = cfg.get("fields", {})
        if len(fields) > MAX_FIELDS or {"decision", "comment"} & set(fields):
            raise _config_error(
                f"at most {MAX_FIELDS} fields, none named decision or comment"
            )
        properties |= fields
        unknown = set(cfg.get("required_fields", [])) - set(fields)
        if unknown:
            raise _config_error(f"required fields are not defined: {sorted(unknown)}")
        required += cfg.get("required_fields", [])
        for name, field in fields.items():
            types = field.get("type", [])
            if not set(types if isinstance(types, list) else [types]) <= _TYPES:
                raise _config_error(f"field {name!r} has an unsupported type")
        try:
            schema = NodeConfigSchema(
                {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                }
            )
        except NodeContractError as exc:
            raise _config_error(exc.message) from None
        seconds = cfg.get("timeout_seconds")
        return NodeWait(
            prompt=prompt,
            response_schema=schema,
            timeout=None if seconds is None else timedelta(seconds=seconds),
        )


def _config_error(message: str) -> NodeError:
    return NodeError(
        message, kind=NodeErrorKind.CONFIGURATION, code="HUMAN_INPUT_INVALID_FIELDS"
    )
