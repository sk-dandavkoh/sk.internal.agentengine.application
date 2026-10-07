"""Node Registry and built-in nodes.

``registry.py`` resolves node type ids to definitions and node classes;
``builtins.py`` registers the core.* set. Nodes implement the domain Node
contract and are executable without HTTP controllers.

Built-in nodes get no special treatment (ADR 0011): like plugin nodes they
are constructed without arguments, receive the engine's services and
their operator settings through the execution context, and import only
the public SDK (``akasha.sdk``) and helpers of this package
(``core.py``, ``fetching.py``). ``tests/test_architecture.py`` enforces
it; only ``registry.py`` is engine code.
"""
