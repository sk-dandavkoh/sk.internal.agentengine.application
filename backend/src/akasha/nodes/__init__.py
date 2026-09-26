"""Built-in nodes.

Implementations of the Node contract (core.input, core.http, ...) live here
(Phase 09). Nodes depend on the node contracts defined by the domain/node
layers and are executable without HTTP controllers.

Dependency rules:
    - may depend on: domain node contracts
    - must not depend on: api, fastapi
"""
