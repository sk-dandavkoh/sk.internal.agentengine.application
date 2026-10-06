"""Application layer: use cases and orchestration.

Application services (e.g. CreateWorkflow, PublishWorkflow, StartExecution)
load domain objects, call domain logic, coordinate transactions and use
infrastructure through interfaces. They contain no HTTP specifics and no SQL.

Dependency rules:
    - may depend on: domain, infrastructure interfaces owned by this layer
    - must not depend on: fastapi, concrete persistence details
    - is depended on by: api, (future) cli, workers
"""
