"""GET /api/v1/nodes: the Node catalog (architecture §22) — every node type
this installation can run, built-in and plugin alike, with the contract a
graph editor needs (ports, config schema, capabilities). Read-only; the
registry is the one source of truth for publish and run validation."""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, JsonValue

from akasha.api.schemas import ErrorResponse
from akasha.domain.node import NodeDefinition, NodeInput, NodeOutput
from akasha.nodes.registry import NodeRegistry


class PortOut(BaseModel):
    name: str
    type: str
    required: bool
    nullable: bool
    multiple: bool
    description: str
    # "schema" would shadow a BaseModel attribute; it is the wire name.
    schema_: dict[str, JsonValue] | None = Field(
        default=None, serialization_alias="schema"
    )

    @classmethod
    def from_port(cls, port: NodeInput | NodeOutput) -> "PortOut":
        return cls(
            name=port.name,
            type=port.port_type.value,
            # Outputs are always produced unless a control-flow node omits them.
            required=isinstance(port, NodeOutput) or port.required,
            nullable=port.nullable,
            multiple=port.multiple,
            description=port.description,
            schema_=port.schema,
        )


class NodeTypeOut(BaseModel):
    node_type: str
    version: str
    name: str
    description: str
    category: str
    documentation_url: str | None
    inputs: list[PortOut]
    outputs: list[PortOut]
    config_schema: dict[str, JsonValue]
    capabilities: dict[str, bool]

    @classmethod
    def from_definition(cls, d: NodeDefinition) -> "NodeTypeOut":
        c = d.capabilities
        return cls(
            node_type=d.node_id,
            version=d.version,
            name=d.metadata.name,
            description=d.metadata.description,
            category=d.metadata.category,
            documentation_url=d.metadata.documentation_url,
            inputs=[PortOut.from_port(p) for p in d.inputs],
            outputs=[PortOut.from_port(p) for p in d.outputs],
            config_schema=d.config_schema.json_schema,
            capabilities={
                "supports_cancellation": c.supports_cancellation,
                "has_side_effects": c.has_side_effects,
                "is_control_flow": c.is_control_flow,
                "streams_output": c.streams_output,
            },
        )


class ConfigValidationRequest(BaseModel):
    config: dict[str, JsonValue]


class ConfigIssueOut(BaseModel):
    # Keys and list indexes from the config root; [] is the config itself.
    path: list[str | int]
    message: str


class ConfigValidationOut(BaseModel):
    valid: bool
    issues: list[ConfigIssueOut]


class NodeCatalog(BaseModel):
    # ponytail: unpaginated — the catalog is bounded by installed code (tens
    # of types), not by user data. Paginate if plugin catalogs grow large.
    items: list[NodeTypeOut]


def create_nodes_router() -> APIRouter:
    errors: dict[int | str, dict[str, Any]] = {500: {"model": ErrorResponse}}
    router = APIRouter(prefix="/nodes", tags=["nodes"], responses=errors)

    @router.get("")
    async def list_node_types(request: Request) -> NodeCatalog:
        registry: NodeRegistry = request.app.state.container.nodes
        definitions = sorted(registry.list_nodes(), key=lambda d: d.node_id)
        return NodeCatalog(items=[NodeTypeOut.from_definition(d) for d in definitions])

    @router.post(
        "/{node_type}/config/validate",
        responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    async def validate_node_config(
        node_type: str, body: ConfigValidationRequest, request: Request
    ) -> ConfigValidationOut:
        """Every issue publish would find in one node's config, located for
        form fields. Stateless: nothing is stored, and no value is echoed."""
        registry: NodeRegistry = request.app.state.container.nodes
        issues = registry.get(node_type).definition.config_issues(body.config)
        return ConfigValidationOut(
            valid=not issues,
            issues=[
                ConfigIssueOut(path=list(i.path), message=i.message) for i in issues
            ],
        )

    return router
