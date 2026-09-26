"""Observability: structured logging, correlation IDs, metrics (Phase 12).

Provides the JSON logging foundation used by every other layer. Log records
must carry context fields (event, request_id, execution_id, ...) and must
never contain secrets.

Dependency rules:
    - may depend on: stdlib logging
    - is depended on by: all layers (leaf dependency)
"""
