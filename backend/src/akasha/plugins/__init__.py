"""Plugin system (Phase 17).

Manifest handling, compatibility checks, registration and the plugin
lifecycle (discover ... remove). Plugins extend the node registry without
changing engine core.

Dependency rules:
    - may depend on: domain, node registry, SDK version
    - must not be imported by: domain, nodes

See ``registry.py`` for the lifecycle and the trust model.
"""
