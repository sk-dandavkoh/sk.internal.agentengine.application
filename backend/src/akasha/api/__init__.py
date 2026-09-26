"""API layer: HTTP transport (FastAPI).

Thin routers only: parse requests, invoke application services, serialize
response DTOs, map errors. No business logic, no SQLAlchemy, no direct node
execution. Public endpoints live under /api/v1; operational endpoints
(/health, /ready) live at the root.

Dependency rules:
    - may depend on: application, domain (read-only DTO mapping)
    - must not depend on: infrastructure implementations, runtime internals
"""

from akasha.api.app import create_app

__all__ = ["create_app"]
