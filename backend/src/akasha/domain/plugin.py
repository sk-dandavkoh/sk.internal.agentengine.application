"""PluginMetadata and plugin compatibility: pure rules, no filesystem or code.

A plugin declares what it provides (node types, each with an entry point)
and what it needs (engine, SDK, other plugins or node types). Everything
here is checked *before* any plugin code is imported, so malformed or
incompatible metadata never reaches the node registry. Loading lives in
``akasha.plugins``.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from akasha.domain.graph_validation import ValidationCode, ValidationIssue
from akasha.domain.node import NodeDefinition
from akasha.domain.primitives import SEMVER_RE, version_satisfies
from akasha.domain.project import Dependency, validate_dependencies
from akasha.errors import DomainValidationError

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
# "<module>[.<submodule>...]:<attribute>", relative to the plugin package.
# Identifiers only: no path separators, no "..", no leading dot.
_ENTRY_POINT_RE = re.compile(
    r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)


@dataclass(frozen=True)
class PluginNode:
    """A node type a plugin provides, declared before its code is loaded."""

    node_id: str
    entry_point: str  # e.g. "nodes:SlugifyNode"

    def __post_init__(self) -> None:
        if not _ENTRY_POINT_RE.match(self.entry_point):
            raise DomainValidationError(
                f"invalid entry point {self.entry_point!r} "
                "(expected 'module:ClassName')"
            )


@dataclass(frozen=True)
class PluginMetadata:
    """Identity, capabilities and compatibility declaration of a plugin."""

    plugin_id: str
    name: str
    version: str
    engine_compatibility: str  # e.g. ">=0.1.0,<1.0.0"
    sdk_compatibility: str
    description: str = ""
    dependencies: tuple[Dependency, ...] = field(default_factory=tuple)
    nodes: tuple[PluginNode, ...] = field(default_factory=tuple)
    documentation: str = ""  # path of the docs file inside the package
    # PEP 508 requirements of third-party libraries the plugin's code
    # imports, validated by ``plugins.manifest``; installed per plugin by
    # ``plugins.dependencies`` (ADR 0004).
    python_requirements: tuple[str, ...] = field(default_factory=tuple)

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
        constraints = [
            ("engine compatibility", self.engine_compatibility),
            ("sdk compatibility", self.sdk_compatibility),
            *(
                (f"dependency {d.name!r}", d.version_constraint)
                for d in self.dependencies
            ),
        ]
        for label, constraint in constraints:
            if not constraint.strip():
                raise DomainValidationError(f"{label} constraint must not be blank")
            version_satisfies("0.0.0", constraint)  # raises when malformed
        if any(
            d.kind == "plugin" and d.name == self.plugin_id for d in self.dependencies
        ):
            raise DomainValidationError("a plugin cannot depend on itself")
        node_ids = [n.node_id for n in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise DomainValidationError("a node type is declared more than once")
        # Namespacing keeps node ids globally unambiguous (architecture §37)
        # and stops a plugin from shadowing core.* or another plugin's nodes.
        prefix = f"{self.plugin_id}."
        for node_id in node_ids:
            if not node_id.startswith(prefix):
                raise DomainValidationError(
                    f"node type {node_id!r} must be namespaced as '{prefix}<name>'"
                )

    @property
    def plugin_dependencies(self) -> frozenset[str]:
        return frozenset(d.name for d in self.dependencies if d.kind == "plugin")


def compatibility_issues(
    metadata: PluginMetadata,
    *,
    engine_version: str,
    sdk_version: str,
    definitions: Mapping[str, NodeDefinition],
    plugins: Mapping[str, str],
) -> tuple[ValidationIssue, ...]:
    """Why ``metadata`` cannot be activated here; empty when it can.

    ``definitions`` are the available node types, ``plugins`` the enabled
    plugins (id → version). Dependencies use the project dependency rules.
    """
    issues = [
        ValidationIssue(
            ValidationCode.INCOMPATIBLE_DEPENDENCY,
            f"{label} {version} does not satisfy {constraint!r}",
        )
        for label, version, constraint in (
            ("engine", engine_version, metadata.engine_compatibility),
            ("sdk", sdk_version, metadata.sdk_compatibility),
        )
        if not version_satisfies(version, constraint)
    ]
    return (
        *issues,
        *validate_dependencies(metadata.dependencies, definitions, plugins),
    )
