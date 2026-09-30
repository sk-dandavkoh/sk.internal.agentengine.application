"""Observability: structured logging, correlation context, metrics, and the
execution observer that turns committed lifecycle events into both.

Dependency rules:
    - may depend on: stdlib logging, prometheus_client, domain (read-only),
      security.redaction
    - is depended on by: api, application, runtime
    - never logs payloads, config values or secrets; every log line is
      redacted by the formatter
"""
