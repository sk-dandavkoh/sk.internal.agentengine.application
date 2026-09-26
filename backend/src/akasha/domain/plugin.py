"""PluginMetadata: descriptive identity of a plugin.

Only metadata lives here — plugin discovery/loading/installation is Phase 17.
Version and compatibility strings are validated structurally so malformed
plugin metadata fails before it can reach a registry.
"""

import re
from dataclasses import dataclass, field

from akasha.domain.primitives import SEMVER_RE
from akasha.errors import DomainValidationError

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


@dataclass(frozen=True)
class PluginMetadata:
    """Identity and compatibility declaration of a plugin."""

    plugin_id: str
    name: str
    version: str
    engine_compatibility: str  # e.g. ">=0.1.0"
    sdk_compatibility: str
    description: str = ""
    dependencies: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not _PLUGIN_ID_RE.match(self.plugin_id):
            raise DomainValidationError(
                f"invalid plugin id: {self.plugin_id!r} "
                "(expected e.g. 'community.web_tools')"
            )
        if not self.name.strip():
            raise DomainValidationError("plugin name must not be blank")
        if not SEMVER_RE.match(self.version):
            raise DomainValidationError(
                f"plugin version must be semver, got {self.version!r}"
            )
        for label, compat in (
            ("engine", self.engine_compatibility),
            ("sdk", self.sdk_compatibility),
        ):
            if not compat.strip():
                raise DomainValidationError(
                    f"{label} compatibility constraint must not be blank"
                )
