"""Command-line interface (roadmap Phase 21, architecture §58).

Every command is a thin adapter: parse arguments, call the same application
service, registry or runtime the API uses, print the result. No command
holds a business rule of its own; the architecture test forbids this module
from importing SQLAlchemy or FastAPI.

Trust model: the CLI runs on the engine host with the engine's own
configuration (``AKASHA_*``: database URL, encryption key, plugin
directory). Whoever can run it can already read the database, so it acts
with operator (admin) authority and has no login. Projects it creates or
imports have no owner, which the API treats as admin-only.

Output: results go to stdout, as text or with ``--json`` as JSON (the API's
schemas where one exists); errors and logs go to stderr, redacted.

Exit codes: 0 success; 1 the operation failed (engine error, invalid
project, execution not completed, file error); 2 usage error; 70 an
unexpected internal error.
"""

import argparse
import asyncio
import contextlib
import importlib
import importlib.util
import json
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from akasha import __version__
from akasha.api.project_schemas import (
    ImportProjectResponse,
    ProjectPage,
    ProjectSummary,
    ProjectVersionSummary,
    ValidationResponse,
)
from akasha.application.executions import ExecutionService
from akasha.application.packages import ProjectPackageService
from akasha.application.plugins import remove_plugin
from akasha.application.projects import ProjectService
from akasha.application.triggers import TriggerService
from akasha.bootstrap import Container, create_container
from akasha.config import Settings, load_settings
from akasha.domain.execution import Execution, ExecutionStatus
from akasha.domain.node import Node, NodeDefinition, check_node_contract
from akasha.errors import AkashaError, ConfigurationError
from akasha.infrastructure.backup import create_backup, restore_backup
from akasha.infrastructure.database import Database
from akasha.nodes.registry import NodeNotFoundError
from akasha.observability.logging import configure_logging
from akasha.plugins.registry import PluginRecord, PluginRegistry
from akasha.runtime.runtime import ExecutionRuntime
from akasha.sdk.testing import run_node
from akasha.security.crypto import SecretCipher
from akasha.security.redaction import redact, redact_mapping, redact_text

logger = logging.getLogger("akasha.cli")

EXIT_OK, EXIT_FAILED, EXIT_INTERNAL = 0, 1, 70

Args = argparse.Namespace
Command = Callable[[Args, Container], Awaitable[int]]


# -- output ---------------------------------------------------------------


def _emit(args: Args, payload: object, text: str) -> None:
    print(json.dumps(payload, indent=2, default=str) if args.json else text)


def _table(rows: Sequence[Sequence[object]], header: Sequence[str] = ()) -> str:
    if not rows:
        return "(none)"
    rows = [header, *rows] if header else rows
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(str(v).ljust(w) for v, w in zip(r, widths, strict=True)).rstrip()
        for r in rows
    )


def _error(args: Args, code: str, message: str, details: Mapping[str, object]) -> None:
    message, details = redact_text(message), redact_mapping(details)
    if getattr(args, "json", False):
        doc = {"code": code, "message": message, "details": details}
        print(json.dumps(doc, default=str), file=sys.stderr)
        return
    print(f"error: {code}: {message}", file=sys.stderr)
    for key, value in details.items():
        print(f"  {key}: {value}", file=sys.stderr)


# -- configuration --------------------------------------------------------


def _need[T](service: T | None, setting: str) -> T:
    if service is None:
        raise ConfigurationError(
            f"This command needs {setting} to be configured.",
            details={"setting": setting},
        )
    return service


def _db(c: Container) -> Database:
    return _need(c.database, "AKASHA_DATABASE_URL")


def _projects(c: Container) -> ProjectService:
    return _need(c.projects, "AKASHA_DATABASE_URL")


def _packages(c: Container) -> ProjectPackageService:
    return _need(c.packages, "AKASHA_DATABASE_URL")


def _executions(c: Container) -> ExecutionService:
    return _need(c.executions, "AKASHA_DATABASE_URL")


def _triggers(c: Container) -> TriggerService:
    return _need(c.triggers, "AKASHA_DATABASE_URL")


def _runtime(c: Container) -> ExecutionRuntime:
    return _need(c.runtime, "AKASHA_DATABASE_URL")


def _plugins(c: Container) -> PluginRegistry:
    return _need(c.plugins, "AKASHA_PLUGINS_DIR")


def _enabled_plugins(c: Container) -> Mapping[str, str]:
    return {} if c.plugins is None else c.plugins.versions


# -- projects -------------------------------------------------------------


async def project_create(args: Args, c: Container) -> int:
    project = await _projects(c).create_project(args.name, description=args.description)
    _emit(
        args,
        ProjectSummary.from_domain(project).model_dump(mode="json"),
        f"created project {project.id} ({project.name})",
    )
    return EXIT_OK


async def project_list(args: Args, c: Container) -> int:
    page = await _projects(c).list_projects(offset=args.offset, limit=args.limit)
    rows = [(p.id, p.current_version, p.revision, p.name) for p in page.items]
    more = f"\n(more: --offset {args.offset + args.limit})" if page.has_more else ""
    _emit(
        args,
        ProjectPage.from_page(page).model_dump(mode="json"),
        _table(rows, ("ID", "VERSION", "REVISION", "NAME")) + more,
    )
    return EXIT_OK


async def project_validate(args: Args, c: Container) -> int:
    result = await _projects(c).validate_project(args.project_id)
    response = ValidationResponse.from_domain(result)
    lines = [
        f"{i.code.value}: {i.message}" + (f" (node {i.node_id})" if i.node_id else "")
        for i in response.issues
    ]
    text = "valid" if result.ok else "invalid\n" + "\n".join(lines)
    _emit(args, response.model_dump(mode="json"), text)
    return EXIT_OK if result.ok else EXIT_FAILED


async def project_publish(args: Args, c: Container) -> int:
    version = await _projects(c).publish_project(
        args.project_id, expected_revision=args.expected_revision
    )
    _emit(
        args,
        ProjectVersionSummary.from_domain(version).model_dump(mode="json"),
        f"published {args.project_id} version {version.version}",
    )
    return EXIT_OK


async def project_run(args: Args, c: Container) -> int:
    """Create a manual execution and run it here to a terminal state,
    without dispatching it to workers. Ctrl-C cancels it cooperatively."""
    version = args.version
    if version is None:
        version = (await _projects(c).get_project(args.project_id)).current_version
    execution = await _triggers(c).fire_manual(
        args.project_id, version, input=args.input, dispatch=False
    )
    if not args.json:
        print(f"running {execution.id} (version {version})", file=sys.stderr)
    runtime = _runtime(c)
    loop = asyncio.get_running_loop()
    pending: set[asyncio.Task[None]] = set()

    def interrupt() -> None:
        task = loop.create_task(runtime.cancel(execution.id, reason="interrupted"))
        pending.add(task)
        task.add_done_callback(pending.discard)

    # Unsupported on Windows (NotImplementedError) and outside the main
    # thread (RuntimeError); Ctrl-C then stops the process uncooperatively.
    with contextlib.suppress(NotImplementedError, RuntimeError):
        loop.add_signal_handler(signal.SIGINT, interrupt)
    try:
        await runtime.run(execution.id)
    finally:
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.remove_signal_handler(signal.SIGINT)
    # The persisted state, not the in-memory aggregate: outputs are shown
    # the way they were stored (redacted), like the API shows them.
    return _show_execution(args, await _executions(c).get_execution(execution.id))


async def project_import(args: Args, c: Container) -> int:
    result = await _packages(c).import_project(await _read_file(args.file))
    response = ImportProjectResponse.from_domain(result)
    lines = [f"imported project {result.project.id} ({result.project.name})"]
    lines += [f"issue {i.code.value}: {i.message}" for i in response.validation.issues]
    if result.missing_credentials:
        missing = ", ".join(result.missing_credentials)
        lines.append(f"missing credentials: {missing}")
    _emit(args, response.model_dump(mode="json"), "\n".join(lines))
    return EXIT_OK


async def project_export(args: Args, c: Container) -> int:
    data = await _packages(c).export_project(args.project_id, version=args.version)
    await asyncio.to_thread(Path(args.output).write_bytes, data)
    _emit(
        args,
        {"path": args.output, "bytes": len(data)},
        f"wrote {args.output} ({len(data)} bytes)",
    )
    return EXIT_OK


# -- executions -----------------------------------------------------------


def _execution_payload(e: Execution) -> dict[str, Any]:
    graph = e.project_version.graph
    return {
        "id": e.id,
        "project_id": e.project_id,
        "version": e.project_version.version,
        "status": e.status.value,
        "trigger_type": e.metadata.trigger_type.value,
        "correlation_id": e.metadata.correlation_id,
        "created_at": e.created_at,
        "started_at": e.started_at,
        "finished_at": e.finished_at,
        "error": None
        if e.error is None
        else {
            "code": e.error.code,
            "message": e.error.message,
            "node_id": e.error.node_id,
        },
        "nodes": [
            {
                "node_id": n.node_id,
                "label": graph.nodes[n.node_id].label
                if n.node_id in graph.nodes
                else "",
                "status": n.status.value,
                "attempt": n.attempt,
                "outputs": n.outputs,
            }
            for n in sorted(e.node_executions.values(), key=lambda n: n.node_id)
        ],
    }


def _show_execution(args: Args, e: Execution) -> int:
    rows: list[tuple[str, object]] = [
        ("execution", e.id),
        ("project", f"{e.project_id} v{e.project_version.version}"),
        ("status", e.status.value),
    ]
    if e.error is not None:
        rows.append(("error", redact_text(f"{e.error.code}: {e.error.message}")))
    graph = e.project_version.graph
    for n in sorted(e.node_executions.values(), key=lambda n: n.node_id):
        instance = graph.nodes.get(n.node_id)
        rows.append((f"  {instance.label if instance else n.node_id}", n.status.value))
    _emit(args, redact_mapping(_execution_payload(e)), _table(rows))
    return EXIT_OK if e.status is ExecutionStatus.COMPLETED else EXIT_FAILED


async def execution_get(args: Args, c: Container) -> int:
    _show_execution(args, await _executions(c).get_execution(args.execution_id))
    return EXIT_OK  # reading succeeded, whatever the execution's status


async def execution_cancel(args: Args, c: Container) -> int:
    await _runtime(c).cancel(args.execution_id, reason=args.reason)
    _show_execution(args, await _executions(c).get_execution(args.execution_id))
    return EXIT_OK


# -- workers --------------------------------------------------------------


async def worker(args: Args, c: Container) -> int:
    """Take jobs until SIGTERM/SIGINT, then stop gracefully (runs get
    AKASHA_WORKER_SHUTDOWN_GRACE_SECONDS to finish)."""
    _need(c.database, "AKASHA_DATABASE_URL")
    manager = _need(c.workers, "AKASHA_REDIS_URL")
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    signals = (signal.SIGINT, signal.SIGTERM)
    for sig in signals:
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    await manager.start()
    if not args.json:
        print(f"worker {manager.worker.worker_id} running", file=sys.stderr)
    try:
        await stop.wait()
    finally:
        for sig in signals:
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(sig)
        await manager.stop()
    return EXIT_OK


# -- nodes ----------------------------------------------------------------


def _node_payload(d: NodeDefinition) -> dict[str, Any]:
    return {
        "node_id": d.node_id,
        "version": d.version,
        "name": d.metadata.name,
        "category": d.metadata.category,
        "description": d.metadata.description,
        "inputs": [{"name": p.name, "type": p.port_type.value} for p in d.inputs],
        "outputs": [{"name": p.name, "type": p.port_type.value} for p in d.outputs],
    }


def _load_node_class(target: str) -> type[Node]:
    """``path/to/file.py:Class`` or ``package.module:Class``. Loading runs
    the module: node code is trusted, like plugin code."""
    module_ref, _, name = target.rpartition(":")
    try:
        if module_ref.endswith(".py"):
            spec = importlib.util.spec_from_file_location(
                f"akasha_cli_{Path(module_ref).stem}", module_ref
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {module_ref}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            module = importlib.import_module(module_ref)
    except (ImportError, OSError, SyntaxError) as exc:
        raise NodeNotFoundError(
            "The node module cannot be loaded.",
            details={"target": target, "reason": f"{type(exc).__name__}: {exc}"},
        ) from None
    cls = getattr(module, name, None)
    if not (isinstance(cls, type) and issubclass(cls, Node)):
        raise NodeNotFoundError(
            "The target is not a Node subclass.", details={"target": target}
        )
    return cls


def _node(target: str, c: Container) -> Node:
    """A registered node type id (built with the engine's dependencies) or
    a ``module:Class`` target."""
    if ":" in target:
        cls = _load_node_class(target)
        check_node_contract(cls)
        return cls()
    return c.nodes.get(target)()


async def node_list(args: Args, c: Container) -> int:
    nodes = c.nodes.list_nodes()
    rows = [(d.node_id, d.version, d.metadata.name) for d in nodes]
    _emit(
        args,
        [_node_payload(d) for d in nodes],
        _table(rows, ("NODE", "VERSION", "NAME")),
    )
    return EXIT_OK


async def node_validate(args: Args, c: Container) -> int:
    node = _node(args.target, c)
    check_node_contract(type(node))
    d = node.definition
    _emit(args, _node_payload(d), f"valid: {d.node_id} {d.version}")
    return EXIT_OK


async def node_test(args: Args, c: Container) -> int:
    """Run one node the way the engine does (``akasha.sdk.testing``).
    Credentials are not available: secrets never go on a command line."""
    result = await run_node(
        _node(args.target, c),
        config=args.config,
        inputs=args.inputs,
        execution_input=args.execution_input,
    )
    payload = redact(
        {"outputs": result.outputs, "events": result.events, "metrics": result.metrics}
    )
    print(json.dumps(payload, indent=2, default=str))
    return EXIT_OK


# -- plugins --------------------------------------------------------------


def _plugin_payload(r: PluginRecord) -> dict[str, Any]:
    m = r.metadata
    return {
        "plugin_id": r.plugin_id,
        "state": r.state.value,
        "version": None if m is None else m.version,
        "nodes": [] if m is None else [n.node_id for n in m.nodes],
        "error": r.error,
    }


async def plugin_list(args: Args, c: Container) -> int:
    records = [_plugin_payload(r) for r in _plugins(c).list_plugins()]
    rows = [
        (r["plugin_id"], r["state"], r["version"] or "-", r["error"] or "")
        for r in records
    ]
    _emit(args, records, _table(rows, ("PLUGIN", "STATE", "VERSION", "ERROR")))
    return EXIT_OK


async def plugin_install(args: Args, c: Container) -> int:
    record = _plugin_payload(_plugins(c).install(Path(args.path)))
    nodes = ", ".join(record["nodes"])
    text = f"installed {record['plugin_id']} {record['version']} (nodes: {nodes})"
    _emit(args, record, text)
    return EXIT_OK


async def plugin_remove(args: Args, c: Container) -> int:
    users = await remove_plugin(
        _plugins(c), _projects(c), args.plugin_id, force=args.force
    )
    text = f"removed {args.plugin_id}"
    if users:
        text += f"\nwarning: these projects can no longer run: {', '.join(users)}"
    _emit(args, {"plugin_id": args.plugin_id, "affected_projects": list(users)}, text)
    return EXIT_OK


# -- backup / restore -----------------------------------------------------


async def backup(args: Args, c: Container) -> int:
    data, summary = await create_backup(_db(c), plugins=_enabled_plugins(c))
    await asyncio.to_thread(_write_private, args.output, data)
    rows = ", ".join(f"{name} {n}" for name, n in summary.rows.items())
    _emit(
        args,
        {"path": args.output, "bytes": len(data), **vars(summary)},
        f"wrote {args.output} (schema {summary.schema_revision}; {rows})",
    )
    return EXIT_OK


async def restore(args: Args, c: Container) -> int:
    settings = c.settings
    key = settings.credential_encryption_key
    summary = await restore_backup(
        _db(c),
        _need(settings.database_url, "AKASHA_DATABASE_URL"),
        await _read_file(args.file),
        plugins=_enabled_plugins(c),
        cipher=None
        if key is None
        else SecretCipher.from_config(key.get_secret_value()),
        replace=args.replace,
    )
    rows = ", ".join(f"{name} {n}" for name, n in summary.rows.items())
    _emit(
        args,
        vars(summary),
        f"restored backup of {summary.created_at} (engine {summary.engine_version};"
        f" {rows})",
    )
    return EXIT_OK


# -- parsing --------------------------------------------------------------


async def _read_file(path: str) -> bytes:
    return await asyncio.to_thread(Path(path).read_bytes)


def _write_private(path: str, data: bytes) -> None:
    """Owner-only and never overwriting: a backup holds password hashes
    and encrypted credentials."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)


def _json_object(value: str) -> dict[str, Any]:
    """A JSON object inline, or ``@file.json``."""
    try:
        if value.startswith("@"):
            value = Path(value[1:]).read_text()
        doc = json.loads(value)
    except OSError as exc:
        raise argparse.ArgumentTypeError(f"cannot read {exc.filename}") from None
    except ValueError:
        raise argparse.ArgumentTypeError("not valid JSON") from None
    if not isinstance(doc, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return doc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="akasha",
        description="Akasha Agent Engine. Configuration comes from AKASHA_*"
        " environment variables, like the server's.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log INFO to stderr"
    )
    groups = parser.add_subparsers(dest="group", required=True, metavar="COMMAND")
    out = argparse.ArgumentParser(add_help=False)
    out.add_argument("--json", action="store_true", help="print JSON")

    def command(
        sub: Any, name: str, handler: Command, help: str
    ) -> argparse.ArgumentParser:
        p: argparse.ArgumentParser = sub.add_parser(name, help=help, parents=[out])
        p.set_defaults(handler=handler)
        return p

    groups.add_parser("serve", help="run the HTTP API server").set_defaults(
        handler=None, json=False
    )

    project = groups.add_parser("project", help="projects").add_subparsers(
        dest="command", required=True, metavar="COMMAND"
    )
    p = command(project, "create", project_create, "create an empty project")
    p.add_argument("name")
    p.add_argument("--description", default="")
    p = command(project, "list", project_list, "list projects")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=50)
    p = command(project, "validate", project_validate, "validate the draft")
    p.add_argument("project_id")
    p = command(project, "publish", project_publish, "publish the draft")
    p.add_argument("project_id")
    p.add_argument(
        "--expected-revision", type=int, help="fail if the draft changed since"
    )
    p = command(project, "run", project_run, "run a published version here")
    p.add_argument("project_id")
    p.add_argument("--version", type=int, help="default: latest published")
    p.add_argument(
        "--input", type=_json_object, default=None, help="JSON object or @file"
    )
    p = command(project, "import", project_import, "import a project package")
    p.add_argument("file")
    p = command(project, "export", project_export, "export a project package")
    p.add_argument("project_id")
    p.add_argument("--version", type=int, help="default: the draft")
    p.add_argument("-o", "--output", required=True)

    node = groups.add_parser("node", help="node types").add_subparsers(
        dest="command", required=True, metavar="COMMAND"
    )
    command(node, "list", node_list, "list registered node types")
    target_help = "registered node type id, or path/file.py:Class / module:Class"
    p = command(node, "validate", node_validate, "check a node's contract")
    p.add_argument("target", help=target_help)
    p = command(node, "test", node_test, "run one node once")
    p.add_argument("target", help=target_help)
    for flag in ("--config", "--inputs", "--execution-input"):
        p.add_argument(flag, type=_json_object, default=None, help="JSON or @file")

    plugin = groups.add_parser("plugin", help="plugins").add_subparsers(
        dest="command", required=True, metavar="COMMAND"
    )
    command(plugin, "list", plugin_list, "list installed plugins")
    p = command(plugin, "install", plugin_install, "install a plugin package")
    p.add_argument("path")
    p = command(plugin, "remove", plugin_remove, "remove a plugin")
    p.add_argument("plugin_id")
    p.add_argument(
        "--force", action="store_true", help="remove even if projects use it"
    )

    execution = groups.add_parser("execution", help="executions").add_subparsers(
        dest="command", required=True, metavar="COMMAND"
    )
    p = command(execution, "get", execution_get, "show an execution")
    p.add_argument("execution_id")
    p = command(execution, "cancel", execution_cancel, "cancel an execution")
    p.add_argument("execution_id")
    p.add_argument("--reason", default="cancelled from the CLI")

    command(groups, "worker", worker, "take queued executions until stopped")
    p = command(groups, "backup", backup, "write a backup of the installation")
    p.add_argument("-o", "--output", required=True, help="new file (mode 0600)")
    p = command(groups, "restore", restore, "restore a backup")
    p.add_argument("file")
    p.add_argument(
        "--replace", action="store_true", help="overwrite existing installation data"
    )
    return parser


# -- entry point ----------------------------------------------------------


async def _run(args: Args, settings: Settings) -> int:
    container = create_container(settings)
    try:
        code: int = await args.handler(args, container)
        return code
    finally:
        await container.shutdown()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.handler is None:  # serve: uvicorn owns the process and its loop
        from akasha.main import main as serve

        return serve()
    try:
        settings = load_settings()
    except ValidationError as exc:
        # Locations and messages only: pydantic echoes input values.
        details = {".".join(map(str, e["loc"])): e["msg"] for e in exc.errors()}
        _error(args, "CONFIGURATION_ERROR", "Invalid configuration.", details)
        return EXIT_FAILED
    # In tests, pytest owns the logging handlers (as in ``create_app``).
    if settings.environment != "test":
        level = "INFO" if args.verbose else "WARNING"
        configure_logging(level, structured=False, stream=sys.stderr)
    try:
        return asyncio.run(_run(args, settings))
    except AkashaError as exc:
        _error(args, exc.code, exc.message, exc.details)
        return EXIT_FAILED
    except OSError as exc:
        if exc.filename is None:  # a socket, not a file the operator named
            return _internal(args, exc)
        _error(args, "FILE_ERROR", exc.strerror or str(exc), {"path": exc.filename})
        return EXIT_FAILED
    except Exception as exc:
        return _internal(args, exc)


def _internal(args: Args, exc: Exception) -> int:
    # The traceback goes to the log (stderr), never into the error line.
    logger.error(
        "cli_unexpected_error", extra={"event": "cli_unexpected_error"}, exc_info=exc
    )
    _error(args, "INTERNAL_ERROR", f"unexpected {type(exc).__name__}", {})
    return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
