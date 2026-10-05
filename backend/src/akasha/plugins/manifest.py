"""PluginManifest: the ``akasha-plugin.json`` file at the root of a plugin
package, parsed into ``PluginMetadata`` without importing any plugin code.

A plugin package is a directory that is also a Python package::

    text_tools/
    ├── akasha-plugin.json
    ├── README.md            (documentation, optional)
    ├── __init__.py
    └── nodes.py

Manifest (format version 1)::

    {
      "manifest_version": 1,
      "id": "text_tools",
      "name": "Text Tools",
      "version": "1.0.0",
      "description": "...",
      "engine": ">=0.1.0,<1.0.0",
      "sdk": ">=1.0.0,<2.0.0",
      "dependencies": [{"kind": "plugin", "id": "other", "version": ">=1.0.0"}],
      "components": {"nodes": [{"id": "text_tools.slugify",
                                "entry_point": "nodes:SlugifyNode"}]},
      "documentation": "README.md",
      "python_requirements": ["python-slugify==8.0.4"]
    }

Unknown fields and component kinds are rejected, not ignored: V1 plugins
provide node types only.
"""

import json
from pathlib import Path
from typing import Literal

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from akasha.domain.plugin import PluginMetadata, PluginNode
from akasha.domain.project import Dependency
from akasha.errors import AkashaError

MANIFEST_FILE = "akasha-plugin.json"
MAX_MANIFEST_BYTES = 64 * 1024
# The engine's own distribution, which provides ``akasha.sdk``.
ENGINE_DISTRIBUTION = "akasha-engine"

_Short = Field(min_length=1, max_length=128)


class PluginManifestError(AkashaError):
    code = "PLUGIN_MANIFEST_INVALID"
    http_status = 422


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Dependency(_Strict):
    kind: Literal["plugin", "node"] = "plugin"
    id: str = _Short
    version: str = Field(default="*", min_length=1, max_length=128)


class _Node(_Strict):
    id: str = _Short
    entry_point: str = _Short


class _Components(_Strict):
    nodes: list[_Node] = Field(min_length=1, max_length=100)


class PluginManifest(_Strict):
    manifest_version: Literal[1]
    id: str = _Short
    name: str = _Short
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=2000)
    engine: str = _Short
    sdk: str = _Short
    dependencies: list[_Dependency] = Field(default_factory=list, max_length=50)
    components: _Components
    documentation: str = Field(default="", max_length=256)
    python_requirements: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("python_requirements")
    @classmethod
    def _requirements(cls, value: list[str]) -> list[str]:
        """PEP 508 requirements resolved from the package index only."""
        names: set[str] = set()
        normalized = []
        for text in value:
            if len(text) > 200:
                raise ValueError("a requirement is longer than 200 characters")
            try:
                requirement = Requirement(text)
            except InvalidRequirement:
                raise ValueError("a requirement is not valid PEP 508") from None
            # A URL or path would bypass the index and the engine's
            # constraints, and could read files on the engine host.
            if requirement.url:
                raise ValueError("requirements must not be URLs or paths")
            name = canonicalize_name(requirement.name)
            if name == ENGINE_DISTRIBUTION:
                raise ValueError("the SDK is provided by the engine; do not require it")
            if name in names:
                raise ValueError("a library is required more than once")
            names.add(name)
            normalized.append(str(requirement))
        return normalized

    def to_metadata(self) -> PluginMetadata:
        return PluginMetadata(
            plugin_id=self.id,
            name=self.name,
            version=self.version,
            engine_compatibility=self.engine,
            sdk_compatibility=self.sdk,
            description=self.description,
            dependencies=tuple(
                Dependency(d.kind, d.id, d.version) for d in self.dependencies
            ),
            nodes=tuple(PluginNode(n.id, n.entry_point) for n in self.components.nodes),
            documentation=self.documentation,
            python_requirements=tuple(self.python_requirements),
        )


def read_manifest(package: Path) -> PluginMetadata:
    """Parse and validate the manifest of the plugin package at ``package``.

    Raises ``PluginManifestError``; its details carry field locations only,
    never manifest values.
    """
    path = package / MANIFEST_FILE
    if not path.is_file() or path.is_symlink():
        raise PluginManifestError(f"{MANIFEST_FILE} is missing")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise PluginManifestError(f"{MANIFEST_FILE} exceeds {MAX_MANIFEST_BYTES} bytes")
    try:
        manifest = PluginManifest.model_validate(json.loads(path.read_bytes()))
        metadata = manifest.to_metadata()
    except ValueError as exc:  # json, pydantic and domain errors all are
        details: dict[str, object] = (
            {"fields": [".".join(map(str, e["loc"])) for e in exc.errors()]}
            if isinstance(exc, ValidationError)
            else {"reason": str(exc)[:200]}
        )
        raise PluginManifestError(
            "The plugin manifest is invalid.", details=details
        ) from exc
    if not (package / "__init__.py").is_file():
        raise PluginManifestError("a plugin package needs an __init__.py")
    if metadata.documentation:
        docs = (package / metadata.documentation).resolve()
        if not docs.is_relative_to(package.resolve()) or not docs.is_file():
            raise PluginManifestError(
                "documentation must name a file inside the plugin package"
            )
    return metadata
