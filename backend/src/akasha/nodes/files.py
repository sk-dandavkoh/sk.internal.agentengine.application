"""File built-ins: core.file_read, core.file_write, core.file_list.

All three work inside the execution's workflow directory of the
operator-configured workspace (``AKASHA_WORKSPACE_DIR``); every path goes
through ``akasha.security.paths.Workspace`` (architecture §64). Without a
workspace they fail with WORKSPACE_NOT_CONFIGURED. Contents are UTF-8 text:
binary files need artifact storage, which the engine does not have yet.
"""

import asyncio
import stat
from pathlib import Path
from typing import Any

from akasha.nodes.core import VERSION, closed_schema
from akasha.sdk import (
    Node,
    NodeCapabilities,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)
from akasha.security.paths import Workspace

DEFAULT_MAX_FILE_BYTES = 1024 * 1024
MAX_LIST_ENTRIES = 1000
WRITE_MODES = ("overwrite", "append", "create")


def _error(message: str, kind: NodeErrorKind, code: str) -> NodeError:
    return NodeError(message, kind=kind, code=code)


def _path_in(description: str, *, required: bool = True) -> NodeInput:
    if required:
        return NodeInput("path", PortType.STRING, description=description)
    return NodeInput(
        "path", PortType.STRING, required=False, default=".", description=description
    )


class _WorkspaceNode(Node):
    def __init__(
        self,
        workspace: Workspace | None = None,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        self._workspace = workspace
        self._max_bytes = max_file_bytes

    def _resolve(self, context: NodeExecutionContext) -> tuple[Workspace, Path]:
        if self._workspace is None:
            raise _error(
                "no workspace directory is configured for this engine",
                NodeErrorKind.CONFIGURATION,
                "WORKSPACE_NOT_CONFIGURED",
            )
        path = self._workspace.resolve(
            context.workflow_id, context.inputs.get("path", ".")
        )
        return self._workspace, path

    def _too_large(self) -> NodeError:
        return _error(
            f"the file exceeds the limit of {self._max_bytes} bytes",
            NodeErrorKind.PERMANENT,
            "FILE_TOO_LARGE",
        )


class FileReadNode(_WorkspaceNode):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.file_read",
            name="Read File",
            description="Reads a UTF-8 text file from the workflow workspace.",
            version=VERSION,
            category="file",
        ),
        inputs=(_path_in("Path relative to the workflow workspace."),),
        outputs=(
            NodeOutput("content", PortType.STRING),
            NodeOutput("size", PortType.NUMBER, description="Size in bytes."),
        ),
        config_schema=closed_schema({}),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        _, path = self._resolve(context)
        data = await asyncio.to_thread(self._read, path)
        return NodeResult(
            {"content": data.decode("utf-8", errors="replace"), "size": len(data)}
        )

    def _read(self, path: Path) -> bytes:
        if not path.is_file():
            raise _error(
                "the file does not exist", NodeErrorKind.PERMANENT, "FILE_NOT_FOUND"
            )
        with path.open("rb") as handle:
            data = handle.read(self._max_bytes + 1)
        if len(data) > self._max_bytes:
            raise self._too_large()
        return data


class FileWriteNode(_WorkspaceNode):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.file_write",
            name="Write File",
            description="Writes a UTF-8 text file into the workflow workspace.",
            version=VERSION,
            category="file",
        ),
        inputs=(
            _path_in("Path relative to the workflow workspace."),
            NodeInput("content", PortType.STRING, description="Text to write."),
        ),
        outputs=(
            NodeOutput("path", PortType.STRING),
            NodeOutput("size", PortType.NUMBER, description="File size afterwards."),
        ),
        config_schema=closed_schema(
            {
                "mode": {
                    "type": "string",
                    "enum": list(WRITE_MODES),
                    "default": "overwrite",
                    "description": "create fails if the file exists.",
                }
            }
        ),
        capabilities=NodeCapabilities(has_side_effects=True),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        workspace, path = self._resolve(context)
        data = context.inputs["content"].encode()
        mode = context.config.get("mode", "overwrite")
        size = await asyncio.to_thread(self._write, path, data, mode)
        return NodeResult(
            {"path": workspace.relative(context.workflow_id, path), "size": size}
        )

    def _write(self, path: Path, data: bytes, mode: str) -> int:
        existing = path.stat().st_size if path.is_file() and mode == "append" else 0
        if existing + len(data) > self._max_bytes:
            raise self._too_large()
        if path.is_dir():
            raise _error(
                "the path is a directory", NodeErrorKind.PERMANENT, "FILE_IS_DIRECTORY"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        flag = {"overwrite": "wb", "append": "ab", "create": "xb"}[mode]
        try:
            with path.open(flag) as handle:
                handle.write(data)
        except FileExistsError:
            raise _error(
                "the file already exists", NodeErrorKind.PERMANENT, "FILE_EXISTS"
            ) from None
        return existing + len(data)


class FileListNode(_WorkspaceNode):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.file_list",
            name="List Files",
            description="Lists a directory of the workflow workspace.",
            version=VERSION,
            category="file",
        ),
        inputs=(
            _path_in(
                "Directory relative to the workflow workspace; "
                "the workspace itself when empty.",
                required=False,
            ),
        ),
        outputs=(
            NodeOutput(
                "entries",
                PortType.LIST,
                schema={
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "type": {"type": "string", "enum": ["file", "directory"]},
                            "size": {"type": "integer"},
                        },
                    },
                },
            ),
        ),
        config_schema=closed_schema({}),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        workspace, path = self._resolve(context)
        if not path.is_dir():
            raise _error(
                "the directory does not exist",
                NodeErrorKind.PERMANENT,
                "DIRECTORY_NOT_FOUND",
            )
        entries = await asyncio.to_thread(
            self._list, workspace, context.workflow_id, path
        )
        return NodeResult({"entries": entries})

    def _list(
        self, workspace: Workspace, workflow_id: str, path: Path
    ) -> list[dict[str, Any]]:
        # Sorted for determinism (AGENTS §72); symlinks are listed by name
        # only, never followed, so a link cannot reveal outside sizes.
        children = sorted(path.iterdir(), key=lambda p: p.name)[:MAX_LIST_ENTRIES]
        entries = []
        for child in children:
            info = child.lstat()
            is_dir = stat.S_ISDIR(info.st_mode)
            entries.append(
                {
                    "path": workspace.relative(workflow_id, child),
                    "type": "directory" if is_dir else "file",
                    "size": 0 if is_dir else info.st_size,
                }
            )
        return entries
