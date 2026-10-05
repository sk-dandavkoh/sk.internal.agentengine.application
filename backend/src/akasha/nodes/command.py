"""core.command: run one operator-allowed program inside the project workspace.

Security model (ADR 0005). This is *not* a sandbox: an allowed program runs
with the engine's OS user and can do whatever that program can do —
allowing ``python3`` or ``bash`` allows arbitrary code. The node therefore
guarantees only what it can enforce:

- off unless the operator lists executables (``AKASHA_COMMAND_ALLOWED``);
  the config names one of them, a project cannot widen the list;
- argv execution, never a shell: arguments are passed verbatim, so ``;``,
  ``|`` or ``$(...)`` in model-written arguments are plain text;
- a minimal environment (no engine variables, so no database URL or
  encryption key), the project workspace as working directory and HOME;
- a time limit and an output cap; on timeout or cancellation the whole
  process group is killed, so no child outlives the node attempt.
"""

import asyncio
import os
import shutil
import signal
from contextlib import suppress
from pathlib import Path
from typing import cast

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

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 600
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024
MAX_ARGS = 256
_CHUNK = 64 * 1024


def _error(message: str, kind: NodeErrorKind, code: str, **details: str) -> NodeError:
    return NodeError(message, kind=kind, code=code, details=details or None)


class CommandNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.command",
            name="Run Command",
            description="Runs an operator-allowed program in the project workspace.",
            version=VERSION,
            category="utility",
        ),
        inputs=(
            NodeInput(
                "args",
                PortType.LIST,
                required=False,
                default=[],
                schema={"type": "array", "items": {"type": "string"}},
                description="Arguments appended after the configured ones.",
            ),
            NodeInput(
                "stdin",
                PortType.STRING,
                required=False,
                default="",
                description="Text written to the program's standard input.",
            ),
        ),
        outputs=(
            NodeOutput("exit_code", PortType.NUMBER),
            NodeOutput("stdout", PortType.STRING),
            NodeOutput("stderr", PortType.STRING),
            NodeOutput(
                "truncated",
                PortType.BOOLEAN,
                description="True when output was cut at the engine's cap.",
            ),
        ),
        config_schema=closed_schema(
            {
                "executable": {
                    "type": "string",
                    "minLength": 1,
                    "description": "One of the executables the operator allows.",
                },
                "args": {
                    "type": "array",
                    "maxItems": MAX_ARGS,
                    "items": {"type": "string"},
                    "description": "Fixed leading arguments.",
                },
                "working_directory": {
                    "type": "string",
                    "default": ".",
                    "description": "Directory relative to the project workspace.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                    "default": DEFAULT_TIMEOUT_SECONDS,
                },
                "fail_on_nonzero_exit": {
                    "type": "boolean",
                    "default": True,
                    "description": "Fail on a non-zero exit code instead of"
                    " returning it (agent tools usually want false).",
                },
            },
            "executable",
        ),
        capabilities=NodeCapabilities(
            supports_cancellation=True, has_side_effects=True
        ),
    )

    def __init__(
        self,
        workspace: Workspace | None = None,
        *,
        allowed_executables: frozenset[str] = frozenset(),
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self._workspace = workspace
        self._allowed = allowed_executables
        self._max_bytes = max_output_bytes

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        cfg = context.config
        if self._workspace is None or not self._allowed:
            raise _error(
                "running commands is not enabled for this engine",
                NodeErrorKind.CONFIGURATION,
                "COMMAND_NOT_CONFIGURED",
            )
        name = cfg["executable"]
        if name not in self._allowed:
            raise _error(
                "the executable is not allowed by the operator",
                NodeErrorKind.CONFIGURATION,
                "COMMAND_NOT_ALLOWED",
                executable=name,
            )
        program = shutil.which(name)
        if program is None:
            raise _error(
                "the executable was not found",
                NodeErrorKind.CONFIGURATION,
                "COMMAND_NOT_FOUND",
                executable=name,
            )
        args = [*cfg.get("args", []), *context.inputs.get("args", [])]
        if len(args) > MAX_ARGS or not all(isinstance(a, str) for a in args):
            raise _error(
                f"at most {MAX_ARGS} string arguments are allowed",
                NodeErrorKind.VALIDATION,
                "COMMAND_INVALID_ARGS",
            )
        if any("\x00" in a for a in args):
            raise _error(
                "arguments must not contain NUL bytes",
                NodeErrorKind.VALIDATION,
                "COMMAND_INVALID_ARGS",
            )
        cwd = self._workspace.resolve(
            context.project_id, cfg.get("working_directory", ".")
        )
        if not cwd.is_dir():
            raise _error(
                "the working directory does not exist",
                NodeErrorKind.CONFIGURATION,
                "COMMAND_INVALID_WORKING_DIRECTORY",
            )
        exit_code, stdout, stderr, truncated = await self._run(
            program,
            args,
            cwd,
            context.inputs.get("stdin", "").encode(),
            cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        )
        if exit_code != 0 and cfg.get("fail_on_nonzero_exit", True):
            raise _error(
                f"the command exited with code {exit_code}",
                NodeErrorKind.PERMANENT,
                "COMMAND_FAILED",
                exit_code=str(exit_code),
            )
        return NodeResult(
            {
                "exit_code": exit_code,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "truncated": truncated,
            }
        )

    async def _run(
        self, program: str, args: list[str], cwd: Path, stdin: bytes, limit: float
    ) -> tuple[int, bytes, bytes, bool]:
        env = {
            "PATH": os.pathsep.join([str(Path(program).parent), os.defpath]),
            "HOME": str(cwd),
            "LANG": "C.UTF-8",
        }
        process = await asyncio.create_subprocess_exec(
            program,
            *args,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # own process group: killed as a whole
        )
        try:
            async with asyncio.timeout(limit):
                (out, out_cut), (err, err_cut), _ = await asyncio.gather(
                    # PIPE was requested for all three streams.
                    self._read(cast("asyncio.StreamReader", process.stdout)),
                    self._read(cast("asyncio.StreamReader", process.stderr)),
                    self._feed(cast("asyncio.StreamWriter", process.stdin), stdin),
                )
                exit_code = await process.wait()
        except TimeoutError:
            raise _error(
                f"the command did not finish within {limit}s",
                NodeErrorKind.TIMEOUT,
                "COMMAND_TIMEOUT",
            ) from None
        finally:
            if process.returncode is None:  # timeout or cancellation
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        return exit_code, out, err, out_cut or err_cut

    async def _read(self, stream: asyncio.StreamReader) -> tuple[bytes, bool]:
        """Keep the first ``max_output_bytes``; drain the rest so the program
        never blocks on a full pipe."""
        kept = bytearray()
        truncated = False
        while chunk := await stream.read(_CHUNK):
            room = self._max_bytes - len(kept)
            kept += chunk[:room]
            truncated |= len(chunk) > room
        return bytes(kept), truncated

    async def _feed(self, stream: asyncio.StreamWriter, data: bytes) -> None:
        # A program that exits without reading its input closes the pipe;
        # that is its choice, not a node failure.
        with suppress(BrokenPipeError, ConnectionResetError):
            if data:
                stream.write(data)
                await stream.drain()
            stream.close()
            await stream.wait_closed()
