"""Node Registry and built-in nodes.

``registry.py`` resolves node type ids to definitions and factories;
``builtins.py`` registers the core.* set (core.py, control.py, http.py,
smtp.py). Nodes implement the domain Node contract and are executable
without HTTP controllers.

Built-in nodes are written against the public SDK (``akasha.sdk``), like
custom nodes, so the SDK is exercised by every built-in; only the registry
and engine-only helpers (e.g. ``json_equal``) reach into the domain.

Dependency rules:
    - may depend on: the SDK, domain node contracts, security guards
    - must not depend on: api, fastapi, runtime internals
"""
