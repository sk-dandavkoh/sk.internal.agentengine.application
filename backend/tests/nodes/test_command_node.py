"""core.command: operator allowlist, argv-only execution, minimal
environment, workspace confinement, limits and process cleanup (ADR 0005).

Runs real local programs (``sh``, ``cat``, ``echo``) inside a temporary
workspace; ``sh`` is allowlisted here only to script the test scenarios."""

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from akasha.domain.node import NodeError, NodeErrorKind, NodeResult
from akasha.nodes.command import CommandNode
from akasha.security.paths import Workspace
from tests.support import make_context

PROJECT = "proj_cmd"
ALLOWED = frozenset({"sh", "cat", "echo", "definitely-not-installed-xyz"})


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir()
    return Workspace(root)


def node(workspace: Workspace | None, **kwargs: Any) -> CommandNode:
    kwargs.setdefault("allowed_executables", ALLOWED)
    return CommandNode(workspace, **kwargs)


async def run(
    command: CommandNode, config: dict[str, Any], inputs: dict[str, Any] | None = None
) -> NodeResult:
    inputs = inputs or {}
    command.definition.validate_config(config)
    command.definition.validate_inputs(inputs)
    context = replace(
        make_context(command, config=config, inputs=inputs), project_id=PROJECT
    )
    result = await command.execute(context)
    command.definition.validate_outputs(result.outputs)
    return result


def sh(script: str, **config: Any) -> dict[str, Any]:
    return {"executable": "sh", "args": ["-c", script], **config}


async def failure(command: CommandNode, config: dict[str, Any]) -> NodeError:
    with pytest.raises(NodeError) as exc:
        await run(command, config)
    return exc.value


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def gone(pid: int) -> bool:
    """The killed child is reaped by init asynchronously; give it a moment
    (event-driven wait on the condition, bounded)."""
    for _ in range(100):
        if not alive(pid):
            return True
        await asyncio.sleep(0.02)
    return False


# -- configuration and allowlist ------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        CommandNode(None, allowed_executables=ALLOWED),
        CommandNode(Workspace(Path("/nonexistent")), allowed_executables=frozenset()),
    ],
    ids=["no workspace", "empty allowlist"],
)
async def test_disabled_unless_operator_configures_it(command: CommandNode) -> None:
    error = await failure(command, {"executable": "echo"})
    assert (error.code, error.kind) == (
        "COMMAND_NOT_CONFIGURED",
        NodeErrorKind.CONFIGURATION,
    )


async def test_only_allowlisted_executables_run(workspace: Workspace) -> None:
    for executable in ("rm", "/bin/sh", "sh "):
        error = await failure(node(workspace), {"executable": executable})
        assert error.code == "COMMAND_NOT_ALLOWED"
    missing = await failure(
        node(workspace), {"executable": "definitely-not-installed-xyz"}
    )
    assert missing.code == "COMMAND_NOT_FOUND"


# -- execution ----------------------------------------------------------------------


async def test_arguments_are_passed_verbatim_never_to_a_shell(
    workspace: Workspace, tmp_path: Path
) -> None:
    hostile = f"$(touch {tmp_path}/pwned); `id` | rm -rf / && echo done"
    result = await run(
        node(workspace), {"executable": "echo", "args": ["-n"]}, {"args": [hostile]}
    )
    assert result.outputs["stdout"] == hostile
    assert result.outputs["exit_code"] == 0
    assert not (tmp_path / "pwned").exists()


async def test_stdin_reaches_the_program(workspace: Workspace) -> None:
    result = await run(node(workspace), {"executable": "cat"}, {"stdin": "hallo\n"})
    assert result.outputs == {
        "exit_code": 0,
        "stdout": "hallo\n",
        "stderr": "",
        "truncated": False,
    }


async def test_runs_in_the_project_workspace_with_a_minimal_environment(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AKASHA_CREDENTIAL_ENCRYPTION_KEY", "canary-never-inherited")
    result = await run(node(workspace), sh('pwd; echo "HOME=$HOME"; env'))
    lines = result.outputs["stdout"].splitlines()
    project = str(workspace.project_dir(PROJECT))
    assert lines[0] == project and lines[1] == f"HOME={project}"
    assert "canary-never-inherited" not in result.outputs["stdout"]
    names = {line.split("=", 1)[0] for line in lines[2:]}
    assert not any(name.startswith("AKASHA_") for name in names)


async def test_working_directory_stays_inside_the_workspace(
    workspace: Workspace,
) -> None:
    error = await failure(node(workspace), sh("pwd", working_directory="../.."))
    assert error.code == "PATH_NOT_ALLOWED"
    missing = await failure(node(workspace), sh("pwd", working_directory="nope"))
    assert missing.code == "COMMAND_INVALID_WORKING_DIRECTORY"


async def test_nonzero_exit_fails_or_is_returned(workspace: Workspace) -> None:
    error = await failure(node(workspace), sh("echo oops >&2; exit 3"))
    assert (error.code, error.kind, error.details) == (
        "COMMAND_FAILED",
        NodeErrorKind.PERMANENT,
        {"exit_code": "3"},
    )
    result = await run(
        node(workspace), sh("echo oops >&2; exit 3", fail_on_nonzero_exit=False)
    )
    assert result.outputs["exit_code"] == 3
    assert result.outputs["stderr"] == "oops\n"


async def test_output_is_capped_and_the_rest_drained(workspace: Workspace) -> None:
    # 1 MB through a 10-byte cap: the program must not block on a full pipe.
    script = "head -c 1000000 /dev/zero | tr '\\0' x; echo tail >&2"
    result = await run(node(workspace, max_output_bytes=10), sh(script))
    assert result.outputs["stdout"] == "x" * 10
    assert result.outputs["truncated"] is True
    assert result.outputs["exit_code"] == 0


async def test_timeout_kills_the_whole_process_group(workspace: Workspace) -> None:
    script = "sleep 30 & echo $! > child.pid; wait"
    error = await failure(node(workspace), sh(script, timeout_seconds=0.5))
    assert (error.code, error.kind) == ("COMMAND_TIMEOUT", NodeErrorKind.TIMEOUT)
    pid = int((workspace.project_dir(PROJECT) / "child.pid").read_text())
    assert await gone(pid), "background child survived the timeout"


async def test_cancellation_kills_the_whole_process_group(
    workspace: Workspace,
) -> None:
    pid_file = workspace.project_dir(PROJECT) / "child.pid"
    task = asyncio.create_task(
        run(node(workspace), sh("sleep 30 & echo $! > child.pid; wait"))
    )
    for _ in range(250):  # wait until the child exists
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await gone(int(pid_file.read_text()))
