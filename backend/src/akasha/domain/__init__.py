"""Domain layer: meaning and invariants of the Engine.

Contains Workflow, WorkflowDraft, WorkflowVersion, Graph, NodeInstance, Edge,
Trigger, Execution, NodeExecution, CredentialReference, PluginMetadata, the
Node contract (NodeDefinition, NodeMetadata, NodeInput, NodeOutput,
NodeConfigSchema, NodeCapabilities, NodeExecutionContext, NodeResult,
NodeError) and the rules that govern them (Phase 02/03).

Dependency rules (enforced by tests/test_architecture.py):
    - may depend on: nothing outside this layer (stdlib only where practical)
    - must not import: fastapi, HTTP, sqlalchemy, redis, provider SDKs,
      filesystem implementations, or any other akasha layer
    - is depended on by: application, runtime, infrastructure
"""
