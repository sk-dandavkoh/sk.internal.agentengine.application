"""Security layer: credential handling, redaction, path/SSRF guards.

Cross-cutting security primitives used by other layers (credentials arrive in
Phase 13, authentication in Phase 20). Contains no business rules.

Dependency rules:
    - may depend on: stdlib, approved crypto dependencies
    - must never log or return secret material
"""
