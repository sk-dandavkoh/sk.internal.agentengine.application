"""core.database: run one parameterized SQL statement on SQLite or PostgreSQL.

One node for select, insert, update and delete (core node portfolio): the
statement kind is SQL's business, not a node type's. Injection safety is
structural:

- the SQL text is workflow config, fixed by the author at publish time;
  data — upstream values, a model's tool arguments — arrives only as
  ``params``, bound by the driver (``?``/``:name`` for SQLite,
  ``%s``/``%(name)s`` for PostgreSQL), never spliced into the text;
- exactly one statement per attempt: SQLite's ``execute`` and PostgreSQL's
  extended protocol (forced by preparing every statement) both refuse
  ``a; b``;
- one connection per attempt, closed when it ends; the statement runs in
  one transaction, committed only on success (``read_only`` runs it in a
  read-only transaction instead).

SQLite: the file lives in the workflow's workspace directory (the file
nodes' guard: no absolute paths, no ``..``, no symlinks out). ``ATTACH`` /
``VACUUM INTO`` would reach other files, so an authorizer denies them;
extension loading stays disabled. No ``:memory:``: a database that lives
for one statement holds nothing worth querying.

PostgreSQL: the connection URL is a credential (it holds the password). Its
host passes the outbound network guard and the client connects to the
vetted address (``hostaddr``), as core.http does; Unix sockets are refused.
"""

import asyncio
import base64
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from akasha.nodes.core import VERSION, closed_schema
from akasha.sdk import (
    CREDENTIAL_REFERENCE_FORMAT,
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
from akasha.security.network import NetworkPolicy, check_destination
from akasha.security.paths import Workspace

DEFAULT_MAX_ROWS = 1000
MAX_ROWS = 10_000
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 600
ENGINES = ("postgres", "sqlite")
_DENIED_SQLITE_ACTIONS = frozenset({sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH})

Params = Sequence[Any] | Mapping[str, Any]
Rows = tuple[list[dict[str, Any]], int, bool]  # rows, row count, truncated


def _error(message: str, kind: NodeErrorKind, code: str) -> NodeError:
    return NodeError(message, kind=kind, code=code)


def _json_value(value: Any) -> Any:
    """Column values as JSON: Decimal as text (no float rounding of money),
    times as ISO 8601, bytes as base64."""
    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode()
    return value


def _rows(columns: list[str], records: list[Sequence[Any]]) -> list[dict[str, Any]]:
    return [
        {name: _json_value(v) for name, v in zip(columns, record, strict=True)}
        for record in records
    ]


class DatabaseNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="core.database",
            name="Database Query",
            description="Runs one parameterized SQL statement (SQLite or PostgreSQL).",
            version=VERSION,
            category="database",
        ),
        inputs=(
            NodeInput(
                "params",
                PortType.JSON,
                required=False,
                nullable=True,
                default=None,
                schema={"type": ["array", "object", "null"]},
                description="Values bound to the statement's placeholders:"
                " a list (positional) or an object (named).",
            ),
        ),
        outputs=(
            NodeOutput("rows", PortType.LIST, description="Result rows as objects."),
            NodeOutput(
                "row_count",
                PortType.NUMBER,
                description="Rows returned, or rows changed by a write.",
            ),
            NodeOutput(
                "truncated",
                PortType.BOOLEAN,
                description="True when more rows than max_rows matched.",
            ),
        ),
        config_schema=closed_schema(
            {
                "engine": {"type": "string", "enum": list(ENGINES)},
                "database": {
                    "type": "string",
                    "minLength": 1,
                    "description": "sqlite: file path in the workflow workspace.",
                },
                "credential": {
                    "type": "string",
                    "format": CREDENTIAL_REFERENCE_FORMAT,
                    "description": "postgres: credential holding the connection"
                    " URL (postgresql://user:password@host:5432/db).",
                },
                "sql": {
                    "type": "string",
                    "minLength": 1,
                    "description": "One statement. Data goes in params, never"
                    " into this text.",
                },
                "read_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "Run in a read-only transaction (writes fail).",
                },
                "max_rows": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_ROWS,
                    "default": DEFAULT_MAX_ROWS,
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                    "default": DEFAULT_TIMEOUT_SECONDS,
                },
            },
            "engine",
            "sql",
        ),
        capabilities=NodeCapabilities(
            supports_cancellation=True, has_side_effects=True
        ),
    )

    def __init__(
        self,
        workspace: Workspace | None = None,
        policy: NetworkPolicy | None = None,
    ) -> None:
        self._workspace = workspace
        self._policy = policy or NetworkPolicy()

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        cfg = context.config
        params: Params = context.inputs.get("params") or []
        limit = cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        try:
            async with asyncio.timeout(limit):
                if cfg["engine"] == "sqlite":
                    rows, count, truncated = await self._sqlite(context, params)
                else:
                    rows, count, truncated = await self._postgres(context, params)
        except TimeoutError:
            raise _error(
                f"the statement did not finish within {limit}s",
                NodeErrorKind.TIMEOUT,
                "DATABASE_TIMEOUT",
            ) from None
        return NodeResult({"rows": rows, "row_count": count, "truncated": truncated})

    # -- SQLite -------------------------------------------------------------

    async def _sqlite(self, context: NodeExecutionContext, params: Params) -> Rows:
        cfg = context.config
        if self._workspace is None:
            raise _error(
                "no workspace directory is configured for this engine",
                NodeErrorKind.CONFIGURATION,
                "WORKSPACE_NOT_CONFIGURED",
            )
        if "database" not in cfg:
            raise _error(
                "sqlite needs 'database'",
                NodeErrorKind.CONFIGURATION,
                "DATABASE_CONFIG_INVALID",
            )
        path = self._workspace.resolve(context.workflow_id, cfg["database"])
        read_only = cfg.get("read_only", False)
        if read_only and not path.is_file():
            raise _error(
                "the database file does not exist",
                NodeErrorKind.PERMANENT,
                "DATABASE_NOT_FOUND",
            )
        connection = await asyncio.to_thread(_sqlite_connect, path, read_only)
        work = asyncio.ensure_future(
            asyncio.to_thread(
                _sqlite_run, connection, cfg["sql"], params, _max_rows(cfg)
            )
        )
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # Cancellation or the node's timeout: stop the statement at its
            # next VM step, then wait for the thread so the connection is
            # never closed under a running statement.
            connection.interrupt()
            await asyncio.wait([work])
            raise
        finally:
            await asyncio.to_thread(connection.close)

    # -- PostgreSQL -----------------------------------------------------------

    async def _postgres(self, context: NodeExecutionContext, params: Params) -> Rows:
        cfg = context.config
        if "credential" not in cfg:
            raise _error(
                "postgres needs 'credential'",
                NodeErrorKind.CONFIGURATION,
                "DATABASE_CONFIG_INVALID",
            )
        url = await context.resolve_credential(cfg["credential"])
        try:
            conninfo = conninfo_to_dict(url)
        except psycopg.ProgrammingError:
            raise _error(
                "the connection URL is not valid",
                NodeErrorKind.CONFIGURATION,
                "DATABASE_CONFIG_INVALID",
            ) from None
        host = str(conninfo.get("host") or "")
        if not host or host.startswith("/") or "," in host or "hostaddr" in conninfo:
            raise _error(
                "the connection URL must name exactly one network host",
                NodeErrorKind.CONFIGURATION,
                "DATABASE_CONFIG_INVALID",
            )
        port = int(conninfo.get("port") or 5432)
        address, *_ = await check_destination(host, port, self._policy)
        try:
            connection = await psycopg.AsyncConnection.connect(
                make_conninfo(
                    url,
                    hostaddr=address,
                    connect_timeout=max(1, int(cfg.get("timeout_seconds", 30))),
                ),
                autocommit=False,
            )
        except psycopg.OperationalError as exc:
            # The message may echo the URL (user, host, database): type only.
            raise _error(
                f"cannot connect to the database ({type(exc).__name__})",
                NodeErrorKind.TRANSIENT,
                "DATABASE_CONNECTION_ERROR",
            ) from None
        try:
            async with connection:
                if cfg.get("read_only", False):
                    await connection.set_read_only(True)
                async with connection.transaction(), connection.cursor() as cursor:
                    try:
                        # prepare=True: the extended protocol, which takes one
                        # command only. Without it psycopg sends parameterless
                        # SQL as a simple query, where "a; b" runs both.
                        await cursor.execute(cfg["sql"], params, prepare=True)
                    except psycopg.Error as exc:
                        raise _statement_error(
                            exc.sqlstate, type(exc).__name__
                        ) from None
                    if cursor.description is None:
                        return [], max(cursor.rowcount, 0), False
                    columns = [c.name for c in cursor.description]
                    limit = _max_rows(cfg)
                    records = await cursor.fetchmany(limit + 1)
                    rows = _rows(columns, list(records[:limit]))
                    return rows, len(rows), len(records) > limit
        except psycopg.OperationalError as exc:
            raise _error(
                f"the database connection failed ({type(exc).__name__})",
                NodeErrorKind.TRANSIENT,
                "DATABASE_CONNECTION_ERROR",
            ) from None


def _max_rows(cfg: Mapping[str, Any]) -> int:
    limit: int = cfg.get("max_rows", DEFAULT_MAX_ROWS)
    return limit


def _statement_error(sqlstate: str | None, name: str) -> NodeError:
    """A statement the database refused. Its message can quote row data, so
    only the type and SQLSTATE are reported; class 40 (serialization,
    deadlock) may succeed when retried."""
    transient = (sqlstate or "").startswith("40")
    return NodeError(
        f"the database refused the statement ({name})",
        kind=NodeErrorKind.TRANSIENT if transient else NodeErrorKind.PERMANENT,
        code="DATABASE_STATEMENT_ERROR",
        details={"sqlstate": sqlstate or ""},
    )


def _deny_other_files(action: int, *_: object) -> int:
    return (
        sqlite3.SQLITE_DENY if action in _DENIED_SQLITE_ACTIONS else sqlite3.SQLITE_OK
    )


def _sqlite_connect(path: Path, read_only: bool) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "ro" if read_only else "rwc"
    connection = sqlite3.connect(
        f"file:{quote(str(path))}?mode={mode}",
        uri=True,
        autocommit=False,
        check_same_thread=False,  # opened, used and closed by worker threads
    )
    connection.set_authorizer(_deny_other_files)
    return connection


def _sqlite_run(
    connection: sqlite3.Connection, sql: str, params: Params, max_rows: int
) -> Rows:
    try:
        cursor = connection.execute(sql, params)
        if cursor.description is None:
            connection.commit()
            return [], max(cursor.rowcount, 0), False
        columns = [c[0] for c in cursor.description]
        records = cursor.fetchmany(max_rows + 1)
        connection.commit()
    except sqlite3.ProgrammingError as exc:
        # Several statements, wrong parameter count or names: author error.
        connection.rollback()
        raise _error(
            f"the statement or its parameters are invalid ({exc})",
            NodeErrorKind.CONFIGURATION,
            "DATABASE_STATEMENT_INVALID",
        ) from None
    except sqlite3.DatabaseError as exc:
        connection.rollback()
        busy = isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc)
        raise NodeError(
            f"the database refused the statement ({type(exc).__name__})",
            kind=NodeErrorKind.TRANSIENT if busy else NodeErrorKind.PERMANENT,
            code="DATABASE_STATEMENT_ERROR",
            details={"sqlite_error": getattr(exc, "sqlite_errorname", "")},
        ) from None
    rows = _rows(columns, list(records[:max_rows]))
    return rows, len(rows), len(records) > max_rows
