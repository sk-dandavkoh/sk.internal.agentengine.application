"""AI provider adapters (Phase 14).

Adapter implementations for OpenAI, Anthropic, Ollama etc. sit behind the
provider abstraction. Domain and runtime never see provider SDKs.

Dependency rules:
    - may depend on: provider abstractions from application/domain
    - must not be imported by: domain, runtime core
"""
