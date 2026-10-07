"""The operator settings of the built-in nodes, typed (ADR 0011).

The composition root fills them from the ``AKASHA_*`` settings; core.*
nodes read them with ``core_settings(context)``. A misspelt key fails at
construction instead of silently falling back to a default.
"""

from dataclasses import dataclass

from akasha.sdk import NodeExecutionContext


@dataclass(frozen=True)
class CoreSettings:
    # SearXNG base URL for core.web_search; None → SEARCH_NOT_CONFIGURED.
    web_search_url: str | None = None
    http_max_response_bytes: int = 5 * 1024 * 1024
    workspace_max_file_bytes: int = 1024 * 1024
    # Programs core.command may run; empty → the node is disabled.
    command_allowed: tuple[str, ...] = ()
    command_max_output_bytes: int = 256 * 1024


def core_settings(context: NodeExecutionContext) -> CoreSettings:
    return CoreSettings(**context.settings)
