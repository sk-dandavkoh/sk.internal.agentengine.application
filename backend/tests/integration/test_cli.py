"""The CLI against real PostgreSQL (roadmap Phase 21).

Each command runs the real entry point (``akasha.cli.main``) in a worker
thread — it builds its own container and event loop from ``AKASHA_*`` like a
real invocation — while arrangement and assertions use the application
services on the test loop. Covers the project lifecycle, executions, the
plugin-removal guard (§143) and backup → destroy → restore → run (§100).
"""

import asyncio
import base64
import gzip
import io
import json
import os
import signal
import stat
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from akasha.application.auth import AuthenticationRequiredError
from akasha.application.credentials import CredentialService
from akasha.application.executions import ExecutionService
from akasha.application.projects import ProjectService, UowFactory
from akasha.cli import EXIT_FAILED, EXIT_OK, main
from akasha.domain.credentials import CredentialKind, Secret
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import NodeDefinition, NodeInput, NodeMetadata, PortType
from akasha.domain.project import Dependency, ProjectDraft
from akasha.infrastructure.database import Database
from akasha.infrastructure.redis_queue import RedisExecutionQueue
from akasha.nodes.builtins import create_builtin_registry
from akasha.security.crypto import SecretCipher
from tests.integration.conftest import AuthKit, truncate_all
from tests.support import posix_only
from tests.test_cli import Result
from tests.test_plugins import SAMPLE

pytestmark = pytest.mark.usefixtures("clean_db")

KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()

Cli = Callable[..., Awaitable[Result]]


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, db_url: str) -> None:
    for name in list(os.environ):
        if name.startswith("AKASHA_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AKASHA_ENVIRONMENT", "test")
    monkeypatch.setenv("AKASHA_DATABASE_URL", db_url)
    monkeypatch.setenv("AKASHA_CREDENTIAL_ENCRYPTION_KEY", KEY)


@pytest.fixture
def cli(capsys: pytest.CaptureFixture[str]) -> Cli:
    async def run(*argv: str) -> Result:
        code = await asyncio.to_thread(main, list(argv))
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return run


@pytest.fixture
def projects(uow: UowFactory) -> ProjectService:
    return ProjectService(uow, create_builtin_registry().definitions)


def echo_graph() -> Graph:
    """input(title) → output: the run's result is its input."""
    graph = Graph()
    source = NodeInstance.create("core.input", config={"field": "title"}, label="in")
    sink = NodeInstance.create("core.output", config={}, label="out")
    graph.add_node(source)
    graph.add_node(sink)
    graph.add_edge(Edge(source.id, "value", sink.id, "value"))
    return graph


async def set_draft(
    projects: ProjectService, project_id: str, draft: ProjectDraft
) -> None:
    project = await projects.get_project(project_id)
    await projects.update_draft(
        project_id, expected_revision=project.revision, name=project.name, draft=draft
    )


async def published(projects: ProjectService, graph: Graph | None = None) -> str:
    project = await projects.create_project("echo")
    await set_draft(projects, project.id, ProjectDraft(graph=graph or echo_graph()))
    await projects.publish_project(project.id)
    return project.id


def doc(result: Result) -> Any:
    assert result.code == EXIT_OK, result.err
    return json.loads(result.out)


# -- projects --------------------------------------------------------------


async def test_project_lifecycle(
    cli: Cli, projects: ProjectService, tmp_path: Path
) -> None:
    project_id = doc(await cli("project", "create", "echo", "--json"))["id"]
    assert (await projects.get_project(project_id)).owner_id is None  # admin-only
    await set_draft(projects, project_id, ProjectDraft(graph=echo_graph()))

    validated = await cli("project", "validate", project_id)
    assert (validated.code, validated.out.strip()) == (EXIT_OK, "valid")
    assert doc(await cli("project", "publish", project_id, "--json"))["version"] == 1
    assert project_id in (await cli("project", "list")).out

    run = doc(
        await cli("project", "run", project_id, "--input", '{"title": "hi"}', "--json")
    )
    assert (run["status"], run["version"]) == ("completed", 1)
    assert {n["label"]: n["status"] for n in run["nodes"]} == {
        "in": "completed",
        "out": "completed",
    }
    shown = await cli("execution", "get", run["id"])
    assert shown.code == EXIT_OK
    assert "completed" in shown.out

    package = tmp_path / "echo.akasha.zip"
    exported = await cli(
        "project", "export", project_id, "--version", "1", "-o", str(package)
    )
    assert exported.code == EXIT_OK
    imported = doc(await cli("project", "import", str(package), "--json"))
    assert imported["project"]["id"] != project_id
    assert imported["validation"] == {"valid": True, "issues": []}


async def test_invalid_draft_fails_validate_and_publish(
    cli: Cli, projects: ProjectService
) -> None:
    project = await projects.create_project("broken")
    graph = echo_graph()
    graph.edges.clear()  # output's required input is now unconnected
    await set_draft(projects, project.id, ProjectDraft(graph=graph))

    validated = await cli("project", "validate", project.id)
    assert validated.code == EXIT_FAILED
    assert validated.out.startswith("invalid\n")
    published_ = await cli("project", "publish", project.id)
    assert published_.code == EXIT_FAILED
    assert "PROJECT_VALIDATION_FAILED" in published_.err
    assert (await projects.get_project(project.id)).current_version == 0


async def test_publish_with_stale_revision_conflicts(
    cli: Cli, projects: ProjectService
) -> None:
    project = await projects.create_project("echo")
    await set_draft(projects, project.id, ProjectDraft(graph=echo_graph()))
    result = await cli("project", "publish", project.id, "--expected-revision", "1")
    assert result.code == EXIT_FAILED
    assert "CONCURRENT_MODIFICATION" in result.err


async def test_run_failures(cli: Cli, projects: ProjectService) -> None:
    missing = await cli("project", "run", "proj_missing")
    assert (missing.code, "PROJECT_NOT_FOUND" in missing.err) == (EXIT_FAILED, True)

    draft_only = await projects.create_project("draft only")
    unpublished = await cli("project", "run", draft_only.id)
    assert unpublished.code == EXIT_FAILED
    assert "PROJECT_VERSION_NOT_FOUND" in unpublished.err

    # Loopback is blocked by the default network policy: the run fails.
    graph = Graph()
    http = NodeInstance.create("core.http", config={"url": "http://127.0.0.1:9/"})
    out = NodeInstance.create("core.output", config={})
    graph.add_node(http)
    graph.add_node(out)
    graph.add_edge(Edge(http.id, "status", out.id, "value"))
    failed = await cli("project", "run", await published(projects, graph), "--json")
    assert failed.code == EXIT_FAILED
    assert json.loads(failed.out)["status"] == "failed"


# -- executions ------------------------------------------------------------


async def test_execution_cancel(
    cli: Cli, projects: ProjectService, uow: UowFactory
) -> None:
    project_id = await published(projects)
    queued = await ExecutionService(uow).create_execution(project_id, 1)

    cancelled = doc(await cli("execution", "cancel", queued.id, "--json"))
    assert cancelled["status"] == "cancelled"
    stored = await ExecutionService(uow).get_execution(queued.id)
    assert stored.cancellation is not None
    assert stored.cancellation.reason == "cancelled from the CLI"

    again = await cli("execution", "cancel", queued.id)
    assert again.code == EXIT_FAILED
    assert "INVALID_STATE_TRANSITION" in again.err
    missing = await cli("execution", "get", "exec_missing")
    assert (missing.code, "EXECUTION_NOT_FOUND" in missing.err) == (EXIT_FAILED, True)


# -- plugins ---------------------------------------------------------------


SLUGIFY = NodeDefinition(
    metadata=NodeMetadata(
        node_id="text_tools.slugify", name="Slugify", description="x", version="1.0.0"
    ),
    inputs=(NodeInput("text", PortType.STRING),),
)


async def test_plugin_remove_is_blocked_while_projects_need_it(
    cli: Cli,
    uow: UowFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AKASHA_PLUGINS_DIR", str(tmp_path))
    assert (await cli("plugin", "install", str(SAMPLE))).code == EXIT_OK
    # Plugin code stays out of the test process: a definition stand-in is
    # enough to publish, and the usage check reads stored documents only.
    definitions = {**create_builtin_registry().definitions, SLUGIFY.node_id: SLUGIFY}
    projects = ProjectService(uow, definitions, plugins={"text_tools": "1.0.0"})
    graph = Graph()
    graph.add_node(NodeInstance.create("text_tools.slugify", config={}))
    in_draft = await projects.create_project("draft user")
    await set_draft(projects, in_draft.id, ProjectDraft(graph=graph))
    # Uses it only through a published version (the draft moved on) and a
    # declared plugin dependency.
    in_history = await projects.create_project("history user")
    dependency = Dependency("plugin", "text_tools", ">=1.0.0")
    await set_draft(
        projects,
        in_history.id,
        ProjectDraft(graph=echo_graph(), dependencies=[dependency]),
    )
    await projects.publish_project(in_history.id)
    await set_draft(projects, in_history.id, ProjectDraft(graph=echo_graph()))
    unrelated = await projects.create_project("unrelated")
    await set_draft(projects, unrelated.id, ProjectDraft(graph=echo_graph()))

    blocked = await cli("plugin", "remove", "text_tools", "--json")
    assert blocked.code == EXIT_FAILED
    error = json.loads(blocked.err)
    assert error["code"] == "PLUGIN_IN_USE"
    assert error["details"]["projects"] == sorted([in_draft.id, in_history.id])
    assert (tmp_path / "text_tools").is_dir()

    forced = doc(await cli("plugin", "remove", "text_tools", "--force", "--json"))
    assert sorted(forced["affected_projects"]) == sorted([in_draft.id, in_history.id])
    assert not (tmp_path / "text_tools").exists()
    assert (await cli("plugin", "list")).out.strip() == "(none)"


async def test_unused_plugin_is_removed(
    cli: Cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AKASHA_PLUGINS_DIR", str(tmp_path))
    assert (await cli("plugin", "install", str(SAMPLE))).code == EXIT_OK
    removed = await cli("plugin", "remove", "text_tools")
    assert (removed.code, removed.out.strip()) == (EXIT_OK, "removed text_tools")
    unknown = await cli("plugin", "remove", "text_tools")
    assert (unknown.code, "PLUGIN_NOT_FOUND" in unknown.err) == (EXIT_FAILED, True)


# -- backup / restore ------------------------------------------------------


async def snapshot(uow: UowFactory, project_id: str, execution_id: str) -> Any:
    async with uow() as u:
        project = await u.projects.get(project_id)
        version = await u.versions.get(project_id, 1)
        execution = await u.executions.get(execution_id)
        events = await u.events.list(project_id=project_id)
    assert project is not None and execution is not None
    return (
        (project.name, project.revision, project.current_version, project.draft),
        version,
        (execution.status, execution.input, execution.node_executions),
        [(e.event_id, e.event_type, e.occurred_at) for e in events],
    )


async def test_backup_destroy_restore_run(
    cli: Cli,
    projects: ProjectService,
    uow: UowFactory,
    database: Database,
    auth_kit: AuthKit,
    tmp_path: Path,
) -> None:
    # Arrange an installation: an admin, a credential, a project with a run.
    admin = await auth_kit.admin_headers()
    credentials = CredentialService(uow, SecretCipher.from_config(KEY))
    await credentials.create(
        "api", kind=CredentialKind.API_KEY, value=Secret("sk-backup-secret-value")
    )
    project_id = await published(projects)
    run = doc(
        await cli("project", "run", project_id, "--input", '{"title": "a"}', "--json")
    )
    before = await snapshot(uow, project_id, run["id"])

    backup = tmp_path / "engine.backup"
    written = doc(await cli("backup", "-o", str(backup), "--json"))
    assert written["rows"]["projects"] == 1
    assert "sessions" not in written["rows"]
    if os.name == "posix":  # Windows has no POSIX modes (ACLs instead)
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert b"sk-backup-secret-value" not in gzip.decompress(backup.read_bytes())
    exists = await cli("backup", "-o", str(backup))
    assert (exists.code, "FILE_ERROR" in exists.err) == (EXIT_FAILED, True)

    refused = await cli("restore", str(backup))
    assert refused.code == EXIT_FAILED
    assert "RESTORE_TARGET_NOT_EMPTY" in refused.err

    await truncate_all(database)  # the installation is gone
    restored = await cli("restore", str(backup))
    assert restored.code == EXIT_OK, restored.err

    assert await snapshot(uow, project_id, run["id"]) == before
    assert (await credentials.resolve("credential:api")).reveal() == (
        "sk-backup-secret-value"
    )
    await auth_kit.login("admin")  # the account survived ...
    with pytest.raises(AuthenticationRequiredError):  # ... its session did not
        await auth_kit.auth.authenticate(admin["Authorization"][7:])

    # The restored project runs, and new events continue after the old ones.
    rerun = doc(
        await cli("project", "run", project_id, "--input", '{"title": "b"}', "--json")
    )
    assert rerun["status"] == "completed"

    # --replace overwrites a live installation atomically, to the same state.
    replaced = await cli("restore", str(backup), "--replace")
    assert replaced.code == EXIT_OK, replaced.err
    assert await snapshot(uow, project_id, run["id"]) == before
    async with uow() as u:
        assert await u.executions.get(rerun["id"]) is None


async def test_migrate_is_idempotent(cli: Cli, database: Database) -> None:
    for _ in range(2):  # the database is already at head: a no-op
        migrated = doc(await cli("migrate", "--json"))
        assert migrated["schema_revision"] == await database.schema_revision()
    assert migrated["schema_revision"] is not None


async def test_backup_and_restore_stream_through_stdio(
    projects: ProjectService,
    uow: UowFactory,
    database: Database,
    capsysbinary: pytest.CaptureFixture[bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``backup -o -`` / ``restore -``: how Compose operators move backups
    in and out of containers (docs/deployment.md)."""
    project_id = await published(projects)
    code = await asyncio.to_thread(main, ["backup", "-o", "-"])
    captured = capsysbinary.readouterr()
    assert code == EXIT_OK, captured.err
    assert b"wrote -" in captured.err  # the summary stays off stdout
    document = json.loads(gzip.decompress(captured.out))
    assert len(document["tables"]["projects"]) == 1

    await truncate_all(database)
    stdin = io.TextIOWrapper(io.BytesIO(captured.out))
    monkeypatch.setattr(sys, "stdin", stdin)
    code = await asyncio.to_thread(main, ["restore", "-"])
    assert code == EXIT_OK, capsysbinary.readouterr().err
    async with uow() as u:
        assert await u.projects.get(project_id) is not None


def rewrite(path: Path, change: Callable[[dict[str, Any]], None]) -> Path:
    document = json.loads(gzip.decompress(path.read_bytes()))
    change(document)
    target = path.with_name(f"changed-{path.name}")
    target.write_bytes(gzip.compress(json.dumps(document).encode()))
    return target


def set_revision(d: dict[str, Any]) -> None:
    d["schema_revision"] = "0001"


def add_column(d: dict[str, Any]) -> None:
    d["tables"]["projects"][0]["extra"] = 1


def bad_status(d: dict[str, Any]) -> None:
    d["tables"]["executions"][0]["status"] = "bogus"


def need_plugin(d: dict[str, Any]) -> None:
    d["plugins"] = {"text_tools": "1.0.0"}


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (set_revision, "BACKUP_INCOMPATIBLE"),
        (need_plugin, "BACKUP_INCOMPATIBLE"),
        (add_column, "BACKUP_INVALID"),
        # Fails inside the load transaction: must roll back the TRUNCATE.
        (bad_status, "BACKUP_INVALID"),
    ],
)
async def test_restore_rejects_incompatible_backups_without_changes(
    cli: Cli,
    projects: ProjectService,
    uow: UowFactory,
    tmp_path: Path,
    change: Callable[[dict[str, Any]], None],
    code: str,
) -> None:
    project_id = await published(projects)
    run = doc(
        await cli("project", "run", project_id, "--input", '{"title": "a"}', "--json")
    )
    backup = tmp_path / "engine.backup"
    assert (await cli("backup", "-o", str(backup))).code == EXIT_OK
    before = await snapshot(uow, project_id, run["id"])

    result = await cli("restore", str(rewrite(backup, change)), "--replace")
    assert result.code == EXIT_FAILED
    assert code in result.err
    assert await snapshot(uow, project_id, run["id"]) == before


async def test_restore_checks_file_and_key(
    cli: Cli,
    uow: UowFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    garbage = tmp_path / "garbage.backup"
    garbage.write_bytes(b"not a backup")
    invalid = await cli("restore", str(garbage))
    assert (invalid.code, "BACKUP_INVALID" in invalid.err) == (EXIT_FAILED, True)

    await CredentialService(uow, SecretCipher.from_config(KEY)).create(
        "api", kind=CredentialKind.API_KEY, value=Secret("sk-value-1234567890")
    )
    backup = tmp_path / "engine.backup"
    assert (await cli("backup", "-o", str(backup))).code == EXIT_OK
    other = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("AKASHA_CREDENTIAL_ENCRYPTION_KEY", other)
    wrong = await cli("restore", str(backup), "--replace")
    assert (wrong.code, "BACKUP_INCOMPATIBLE" in wrong.err) == (EXIT_FAILED, True)
    monkeypatch.delenv("AKASHA_CREDENTIAL_ENCRYPTION_KEY")
    no_key = await cli("restore", str(backup), "--replace")
    assert (no_key.code, "BACKUP_INCOMPATIBLE" in no_key.err) == (EXIT_FAILED, True)
    missing = await cli("restore", str(tmp_path / "absent.backup"))
    assert (missing.code, "FILE_ERROR" in missing.err) == (EXIT_FAILED, True)


@posix_only
async def test_ctrl_c_cancels_the_run_cooperatively(
    projects: ProjectService, uow: UowFactory
) -> None:
    """A real process: signal handlers only exist on the main thread."""
    graph = Graph()
    wait = NodeInstance.create("core.delay", config={"seconds": 3600})
    out = NodeInstance.create("core.output", config={})
    graph.add_node(wait)
    graph.add_node(out)
    graph.add_edge(Edge(wait.id, "value", out.id, "value"))
    project_id = await published(projects, graph)

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "akasha",
        "project",
        "run",
        project_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(os.environ),
    )
    assert process.stderr is not None
    line = (await asyncio.wait_for(process.stderr.readline(), 30)).decode()
    execution_id = line.split()[1]  # "running exec_… (version 1)"
    executions = ExecutionService(uow)
    for _ in range(300):  # the delay node is RUNNING before we interrupt
        if (await executions.get_execution(execution_id)).status == "running":
            break
        await asyncio.sleep(0.05)
    process.send_signal(signal.SIGINT)
    stdout, _ = await asyncio.wait_for(process.communicate(), 30)

    assert process.returncode == EXIT_FAILED
    assert "cancelled" in stdout.decode()
    stored = await executions.get_execution(execution_id)
    assert stored.status == "cancelled"
    assert stored.cancellation is not None
    assert stored.cancellation.reason == "interrupted"


async def test_worker_needs_redis(cli: Cli) -> None:
    result = await cli("worker")
    assert result.code == EXIT_FAILED
    assert "AKASHA_REDIS_URL" in result.err


@posix_only
async def test_worker_process_registers_and_stops_gracefully_on_sigterm(
    redis_url: str,
) -> None:
    prefix = "cli-" + os.urandom(6).hex()
    env = {**os.environ, "AKASHA_REDIS_URL": redis_url, "AKASHA_QUEUE_PREFIX": prefix}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "akasha",
        "worker",
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    assert process.stderr is not None
    # Printed once the worker is registered.
    line = (await asyncio.wait_for(process.stderr.readline(), 30)).decode()
    worker_id = line.split()[1]  # "worker wrk_… running"
    queue = RedisExecutionQueue.from_url(redis_url, prefix=prefix)
    try:
        assert [w.worker_id for w in await queue.workers()] == [worker_id]

        process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.communicate(), 30)

        assert process.returncode == EXIT_OK
        assert await queue.workers() == []
    finally:
        await queue.close()
