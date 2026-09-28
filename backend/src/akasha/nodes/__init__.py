"""Node Registry and built-in nodes.

``registry.py`` resolves node type ids to definitions and factories;
``builtins.py`` registers the core.* set (core.py, control.py, http.py,
smtp.py). Nodes implement the domain Node contract and are executable
without HTTP controllers.

Dependency rules:
    - may depend on: domain node contracts, security guards
    - must not depend on: api, fastapi, runtime internals
"""
