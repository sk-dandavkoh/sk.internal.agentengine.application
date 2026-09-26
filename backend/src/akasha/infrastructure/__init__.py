"""Infrastructure layer: adapters for the outside world.

PostgreSQL repositories, Redis clients, object storage, SMTP/HTTP clients and
provider SDK adapters live here. Implements interfaces owned by the
application layer; never the other way around. Introduced in later phases
(Persistence in Phase 05).

Dependency rules:
    - may depend on: domain, application interfaces
    - must not be imported by: domain, api
"""
