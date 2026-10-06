"""core.file_read / core.file_write / core.file_list and the workspace path
guard they share (backend AGENTS §50). Every test gets its own temporary
workspace root; nothing is written outside it."""

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from akasha.domain.node import Node, NodeError, NodeErrorKind, NodeResult
from akasha.nodes.files import FileListNode, FileReadNode, FileWriteNode
from akasha.security.paths import Workspace
from tests.support import RunNode, make_context

LIMIT = 64


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "workspace"
    root.mkdir()
    return Workspace(root)


WORKFLOW = "wf_files"


async def run(node: Node, config: dict[str, Any], inputs: dict[str, Any]) -> NodeResult:
    """Like ``run_node``, but every call runs in the same workflow."""
    node.definition.validate_config(config)
    node.definition.validate_inputs(inputs)
    context = make_context(node, config=config, inputs=inputs)
    result = await node.execute(replace(context, workflow_id=WORKFLOW))
    assert isinstance(result, NodeResult)
    node.definition.validate_outputs(result.outputs)
    return result


async def write(
    run_node: RunNode, workspace: Workspace, path: str, content: str, **config: str
) -> dict[str, object]:
    node = FileWriteNode(workspace, max_file_bytes=LIMIT)
    result = await run(node, config, {"path": path, "content": content})
    return dict(result.outputs)


async def read(run_node: RunNode, workspace: Workspace, path: str) -> str:
    node = FileReadNode(workspace, max_file_bytes=LIMIT)
    result = await run(node, {}, {"path": path})
    content: str = result.outputs["content"]
    return content


# -- path guard ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../escape.txt",
        "a/../../escape.txt",
        "..",
        "/etc/passwd",
        "//etc/passwd",
        "a\\..\\..\\escape.txt",
        "a\x00.txt",
    ],
)
def test_paths_leaving_the_workflow_are_refused(
    workspace: Workspace, path: str
) -> None:
    with pytest.raises(NodeError) as exc:
        workspace.resolve("wf_1", path)
    assert (exc.value.code, exc.value.kind) == (
        "PATH_NOT_ALLOWED",
        NodeErrorKind.VALIDATION,
    )


def test_url_encoded_dots_are_a_plain_file_name(workspace: Workspace) -> None:
    # Nothing decodes %2e: it names a file inside the workflow.
    target = workspace.resolve("wf_1", "%2e%2e/x")
    assert target.is_relative_to(workspace.root.resolve() / "wf_1")


def test_symlink_out_of_the_workspace_is_refused(
    workspace: Workspace, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace.workflow_dir("wf_1") / "link").symlink_to(outside)
    with pytest.raises(NodeError, match="leaves the workspace"):
        workspace.resolve("wf_1", "link/secret.txt")


def test_workflows_are_isolated(workspace: Workspace) -> None:
    a = workspace.resolve("wf_a", "x.txt")
    b = workspace.resolve("wf_b", "x.txt")
    assert a.parent != b.parent
    with pytest.raises(NodeError):
        workspace.resolve("wf_a", "../wf_b/x.txt")


@pytest.mark.parametrize("workflow_id", ["..", ".", "a/b", ""])
def test_malformed_workflow_ids_are_refused(
    workspace: Workspace, workflow_id: str
) -> None:
    with pytest.raises(NodeError):
        workspace.workflow_dir(workflow_id)


# -- nodes --------------------------------------------------------------------


async def test_write_then_read_round_trip(
    run_node: RunNode, workspace: Workspace
) -> None:
    out = await write(run_node, workspace, "notes/today.md", "# Hallo Wörld")
    assert out == {"path": "notes/today.md", "size": len("# Hallo Wörld".encode())}
    assert await read(run_node, workspace, "notes/today.md") == "# Hallo Wörld"


async def test_files_land_in_the_execution_workflow_directory(
    workspace: Workspace,
) -> None:
    node = FileWriteNode(workspace)
    context = make_context(node, inputs={"path": "a.txt", "content": "x"})
    await node.execute(context)
    assert (workspace.root / context.workflow_id / "a.txt").read_text() == "x"


async def test_append_and_create_modes(run_node: RunNode, workspace: Workspace) -> None:
    await write(run_node, workspace, "log.txt", "a")
    assert (await write(run_node, workspace, "log.txt", "b", mode="append"))[
        "size"
    ] == 2
    assert await read(run_node, workspace, "log.txt") == "ab"
    with pytest.raises(NodeError) as exc:
        await write(run_node, workspace, "log.txt", "c", mode="create")
    assert exc.value.code == "FILE_EXISTS"
    assert await read(run_node, workspace, "log.txt") == "ab"  # untouched


async def test_size_limits_hold_for_read_write_and_append(
    run_node: RunNode, workspace: Workspace
) -> None:
    await write(run_node, workspace, "full.txt", "x" * LIMIT)  # exactly at limit
    for content, mode in (("x" * (LIMIT + 1), "overwrite"), ("y", "append")):
        with pytest.raises(NodeError) as exc:
            await write(run_node, workspace, "full.txt", content, mode=mode)
        assert exc.value.code == "FILE_TOO_LARGE"
    node = FileReadNode(workspace, max_file_bytes=LIMIT)
    context = make_context(node, inputs={"path": "big.txt"})
    workspace.resolve(context.workflow_id, "big.txt").write_bytes(b"z" * (LIMIT + 1))
    with pytest.raises(NodeError) as read_exc:
        await node.execute(context)
    assert read_exc.value.code == "FILE_TOO_LARGE"


async def test_missing_file_and_directory_targets(
    run_node: RunNode, workspace: Workspace
) -> None:
    with pytest.raises(NodeError) as exc:
        await read(run_node, workspace, "nope.txt")
    assert (exc.value.code, exc.value.kind) == (
        "FILE_NOT_FOUND",
        NodeErrorKind.PERMANENT,
    )
    await write(run_node, workspace, "dir/a.txt", "x")
    with pytest.raises(NodeError) as dir_exc:
        await write(run_node, workspace, "dir", "x")
    assert dir_exc.value.code == "FILE_IS_DIRECTORY"


async def test_traversal_through_a_node_input_is_refused(
    run_node: RunNode, workspace: Workspace, tmp_path: Path
) -> None:
    with pytest.raises(NodeError) as exc:
        await write(run_node, workspace, "../../pwned.txt", "x")
    assert exc.value.code == "PATH_NOT_ALLOWED"
    assert not (tmp_path / "pwned.txt").exists()


async def test_list_is_sorted_and_does_not_follow_symlinks(
    workspace: Workspace, tmp_path: Path
) -> None:
    node = FileListNode(workspace)
    context = make_context(node, inputs={"path": "."})
    workflow = workspace.workflow_dir(context.workflow_id)
    (workflow / "b.txt").write_text("bb")
    (workflow / "a").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workflow / "c-link").symlink_to(outside)
    result = await node.execute(context)
    entries = result.outputs["entries"]
    assert [(e["path"], e["type"]) for e in entries] == [
        ("a", "directory"),
        ("b.txt", "file"),
        ("c-link", "file"),  # the link itself, never its target
    ]
    assert entries[1]["size"] == 2
    node.definition.validate_outputs(result.outputs)


async def test_list_defaults_to_the_workflow_root(
    run_node: RunNode, workspace: Workspace
) -> None:
    result = await run_node(FileListNode(workspace))
    assert result.outputs == {"entries": []}


async def test_without_workspace_the_nodes_are_not_configured(
    run_node: RunNode,
) -> None:
    for node, inputs in (
        (FileReadNode(), {"path": "a"}),
        (FileWriteNode(), {"path": "a", "content": "x"}),
        (FileListNode(), {}),
    ):
        with pytest.raises(NodeError) as exc:
            await run_node(node, inputs=inputs)
        assert (exc.value.code, exc.value.kind) == (
            "WORKSPACE_NOT_CONFIGURED",
            NodeErrorKind.CONFIGURATION,
        )


def test_workspace_root_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Workspace(tmp_path / "missing").workflow_dir("wf")
    assert not os.path.exists(tmp_path / "missing")
