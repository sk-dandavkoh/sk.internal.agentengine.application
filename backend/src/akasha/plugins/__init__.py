"""Plugin system (Phase 17).

Manifest handling, compatibility checks, registration and the plugin
lifecycle (discover ... remove). Plugins extend the node registry without
changing engine core.

Dependency rules:
    - may depend on: node contracts, application interfaces
    - must not be imported by: domain
"""
