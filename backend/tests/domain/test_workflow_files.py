"""Workflow files (Phase 18): path/size rules, secret gate, version snapshot."""

import pytest

from akasha.domain.credentials import PlaintextSecretError
from akasha.domain.graph import NodeInstance
from akasha.domain.workflow import (
    MAX_WORKFLOW_FILE_BYTES,
    MAX_WORKFLOW_FILES,
    MAX_WORKFLOW_FILES_TOTAL_BYTES,
    Workflow,
    WorkflowDraft,
    plaintext_secret_locations,
    validate_workflow_files,
)
from akasha.errors import DomainValidationError


@pytest.mark.parametrize(
    "path",
    [
        "docs/README.md",
        "prompts/system.txt",
        "resources/data/leads.csv",
        "assets/logo.png",
        "docs/a/b/c/d/e/f/g/h.md",  # 8 segments below the root
    ],
)
def test_valid_paths(path: str) -> None:
    validate_workflow_files({path: b"x"})


@pytest.mark.parametrize(
    "path",
    [
        "README.md",  # outside the four roots
        "docs",
        "docs/",
        "/docs/a.md",
        "docs/../workflow.json",
        "docs/./a.md",
        "docs/.hidden",
        "docs//a.md",
        "docs\\a.md",
        "docs/a b.md",
        "Docs/a.md",
        "manifest.json",
        "docs/a/b/c/d/e/f/g/h/i.md",  # too deep
        "docs/" + "a" * 101,
        "src/evil.py",
    ],
)
def test_invalid_paths_rejected(path: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_workflow_files({path: b"x"})


def test_size_and_count_limits() -> None:
    validate_workflow_files({"assets/big.bin": b"\0" * MAX_WORKFLOW_FILE_BYTES})
    with pytest.raises(DomainValidationError):
        validate_workflow_files(
            {"assets/big.bin": b"\0" * (MAX_WORKFLOW_FILE_BYTES + 1)}
        )
    many = {f"docs/{i}.md": b"" for i in range(MAX_WORKFLOW_FILES + 1)}
    with pytest.raises(DomainValidationError):
        validate_workflow_files(many)
    chunks = MAX_WORKFLOW_FILES_TOTAL_BYTES // MAX_WORKFLOW_FILE_BYTES + 1
    too_much = {
        f"assets/{i}.bin": b"\0" * MAX_WORKFLOW_FILE_BYTES for i in range(chunks)
    }
    with pytest.raises(DomainValidationError):
        validate_workflow_files(too_much)


def test_content_must_be_bytes() -> None:
    with pytest.raises(DomainValidationError):
        validate_workflow_files({"docs/a.md": "text"})  # type: ignore[dict-item]  # the point


def test_replace_draft_rejects_secret_text_files_by_location() -> None:
    workflow = Workflow.create("p")
    draft = WorkflowDraft(
        files={
            "docs/setup.md": b"Use api_key=sk-live-abcdefghijklmnopqrstuvwx here",
            "assets/blob.bin": b"\xff\xfe api_key=abc",  # binary: not inspected
            "prompts/p.txt": b"Reference credential:openai instead.",
        }
    )
    with pytest.raises(PlaintextSecretError) as info:
        workflow.replace_draft(draft, name="p")
    assert info.value.details == {"paths": ["files.docs/setup.md"]}
    assert "sk-live" not in str(info.value.details)


def test_secret_locations_cover_config_triggers_and_files() -> None:
    node = NodeInstance.create("core.http", config={"headers": {"Authorization": "x"}})
    draft = WorkflowDraft(files={"docs/a.md": b"Bearer abcdefghijkl"})
    draft.graph.add_node(node)
    assert plaintext_secret_locations(draft.graph, [], draft.files) == [
        f"nodes.{node.id}.config.headers.Authorization",
        "files.docs/a.md",
    ]


def test_publish_snapshots_files() -> None:
    workflow = Workflow.create("p")
    workflow.replace_draft(WorkflowDraft(files={"docs/a.md": b"v1"}), name="p")
    version = workflow.publish_draft()
    workflow.draft.files["docs/a.md"] = b"v2"
    workflow.draft.files["docs/b.md"] = b"new"
    assert version.files == {"docs/a.md": b"v1"}
