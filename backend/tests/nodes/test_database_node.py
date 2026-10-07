"""core.database against real engines: SQLite files in an isolated temporary
workspace, PostgreSQL in the session's Testcontainer. No database is mocked:
injection safety, single statements, read-only mode, timeouts and the file
and network guards are properties of the real drivers and servers."""

import asyncio
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from akasha.domain.node import NodeError, NodeErrorKind, NodeResult, NodeServices
from akasha.errors import NodeConfigurationError
from akasha.nodes.database import DatabaseNode
from akasha.security.network import NetworkPolicy
from akasha.security.paths import Workspace
from tests.support import RunNode, make_context


def _root(workspace: Workspace | None) -> Path | None:
    return None if workspace is None else workspace.root


INJECTION = "x'); DROP TABLE notes; --"
WORKFLOW = "wf_database"
ENDLESS = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c)"
    " SELECT count(*) FROM c"
)
GUARD = 10


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)


async def sql(
    run_node: RunNode,
    workspace: Workspace | None,
    statement: str,
    params: Any = None,
    **config: Any,
) -> dict[str, Any]:
    """Like ``run_node``, but every call runs in the same workflow (one
    workspace directory), as the nodes of one workflow do."""
    cfg = {"engine": "sqlite", "database": "data/app.db", "sql": statement} | config
    inputs = {} if params is None else {"params": params}
    node = DatabaseNode()
    node.definition.validate_config(cfg)
    node.definition.validate_inputs(inputs)
    context = replace(
        make_context(
            node,
            config=cfg,
            inputs=inputs,
            services=NodeServices(workspace_dir=_root(workspace)),
        ),
        workflow_id=WORKFLOW,
    )
    result = await node.execute(context)
    assert isinstance(result, NodeResult)
    node.definition.validate_outputs(result.outputs)
    return dict(result.outputs)


# -- SQLite -----------------------------------------------------------------


async def test_sqlite_create_insert_select_with_bound_params(
    run_node: RunNode, workspace: Workspace
) -> None:
    await sql(
        run_node,
        workspace,
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)",
    )
    inserted = await sql(
        run_node,
        workspace,
        "INSERT INTO notes (body) VALUES (?), (?)",
        ["a", INJECTION],
    )
    assert inserted == {"rows": [], "row_count": 2, "truncated": False}
    named = await sql(
        run_node,
        workspace,
        "SELECT id, body FROM notes WHERE body = :body",
        {"body": INJECTION},
    )
    # The payload was stored as data; the table it tried to drop still exists.
    assert named["rows"] == [{"id": 2, "body": INJECTION}]
    count = await sql(run_node, workspace, "SELECT count(*) AS n FROM notes")
    assert count["rows"] == [{"n": 2}]


async def test_sqlite_rows_are_capped(run_node: RunNode, workspace: Workspace) -> None:
    query = (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c LIMIT 50)"
        " SELECT x FROM c"
    )
    result = await sql(run_node, workspace, query, max_rows=10)
    assert result["row_count"] == 10 and result["truncated"] is True
    assert result["rows"][-1] == {"x": 10}


async def test_sqlite_refuses_several_statements(
    run_node: RunNode, workspace: Workspace
) -> None:
    await sql(run_node, workspace, "CREATE TABLE t (x)")
    with pytest.raises(NodeError) as exc:
        await sql(run_node, workspace, "SELECT 1; DROP TABLE t")
    assert exc.value.code == "DATABASE_STATEMENT_INVALID"
    assert (await sql(run_node, workspace, "SELECT count(*) AS n FROM t"))["rows"] == [
        {"n": 0}
    ]


async def test_sqlite_failed_statement_rolls_back(
    run_node: RunNode, workspace: Workspace
) -> None:
    await sql(run_node, workspace, "CREATE TABLE t (x INTEGER UNIQUE)")
    await sql(run_node, workspace, "INSERT INTO t VALUES (1)")
    with pytest.raises(NodeError) as exc:
        await sql(run_node, workspace, "INSERT INTO t VALUES (2), (1)")
    assert exc.value.code == "DATABASE_STATEMENT_ERROR"
    assert exc.value.kind is NodeErrorKind.PERMANENT
    rows = (await sql(run_node, workspace, "SELECT x FROM t ORDER BY x"))["rows"]
    assert rows == [{"x": 1}]  # the half-applied multi-row insert is gone


async def test_sqlite_read_only_refuses_writes(
    run_node: RunNode, workspace: Workspace
) -> None:
    await sql(run_node, workspace, "CREATE TABLE t (x)")
    reads = await sql(
        run_node, workspace, "SELECT count(*) AS n FROM t", read_only=True
    )
    assert reads["rows"] == [{"n": 0}]
    with pytest.raises(NodeError) as exc:
        await sql(run_node, workspace, "INSERT INTO t VALUES (1)", read_only=True)
    assert exc.value.code == "DATABASE_STATEMENT_ERROR"
    with pytest.raises(NodeError) as missing:
        await sql(run_node, workspace, "SELECT 1", read_only=True, database="nope.db")
    assert missing.value.code == "DATABASE_NOT_FOUND"


async def test_sqlite_cannot_attach_other_files(
    run_node: RunNode, workspace: Workspace, tmp_path: Path
) -> None:
    outside = tmp_path.parent / f"outside-{os.getpid()}.db"
    with sqlite3.connect(outside) as other:
        other.execute("CREATE TABLE secrets (v)")
    statement = f"ATTACH DATABASE '{outside}' AS other"
    try:
        # Control: a plain connection attaches it, so the refusal below is
        # the node's guard, not SQLite's default.
        with sqlite3.connect(":memory:") as plain:
            plain.execute(statement)
        with pytest.raises(NodeError) as exc:
            await sql(run_node, workspace, statement)
        assert exc.value.code == "DATABASE_STATEMENT_ERROR"
    finally:
        outside.unlink()


async def test_sqlite_cannot_write_other_files(
    run_node: RunNode, workspace: Workspace, tmp_path: Path
) -> None:
    target = tmp_path.parent / f"copy-{os.getpid()}.db"
    await sql(run_node, workspace, "CREATE TABLE t (x)")
    with pytest.raises(NodeError) as exc:
        await sql(run_node, workspace, f"VACUUM INTO '{target}'")
    assert exc.value.code == "DATABASE_STATEMENT_ERROR"
    assert not target.exists()  # (a plain connection writes it: see above)


@pytest.mark.parametrize("path", ["../escape.db", "/etc/passwd", "a/../../b.db"])
async def test_sqlite_paths_stay_in_the_workspace(
    run_node: RunNode, workspace: Workspace, path: str
) -> None:
    with pytest.raises(NodeError) as exc:
        await sql(run_node, workspace, "SELECT 1", database=path)
    assert exc.value.code == "PATH_NOT_ALLOWED"


async def test_sqlite_symlink_out_of_the_workspace_is_refused(
    run_node: RunNode, workspace: Workspace, tmp_path: Path
) -> None:
    node = DatabaseNode()
    context = make_context(node, services=NodeServices(workspace_dir=_root(workspace)))
    target = tmp_path.parent / f"target-{os.getpid()}.db"
    directory = workspace.workflow_dir(context.workflow_id)
    (directory / "link.db").symlink_to(target)
    config = {"engine": "sqlite", "database": "link.db", "sql": "SELECT 1"}
    with pytest.raises(NodeError) as exc:
        await node.execute(type(context)(**{**context.__dict__, "config": config}))
    assert exc.value.code == "PATH_NOT_ALLOWED"
    assert not target.exists()


async def test_sqlite_long_statement_times_out_and_is_interrupted(
    run_node: RunNode, workspace: Workspace
) -> None:
    endless = ENDLESS
    started = time.monotonic()
    with pytest.raises(NodeError) as exc:
        await sql(run_node, workspace, endless, timeout_seconds=0.3)
    assert exc.value.code == "DATABASE_TIMEOUT"
    assert exc.value.kind is NodeErrorKind.TIMEOUT
    assert time.monotonic() - started < GUARD  # interrupted, not run to the end


async def test_sqlite_cancellation_interrupts_the_statement(
    workspace: Workspace,
) -> None:
    node = DatabaseNode()
    endless = ENDLESS
    context = make_context(
        node,
        config={"engine": "sqlite", "database": "a.db", "sql": endless},
        services=NodeServices(workspace_dir=_root(workspace)),
    )
    task = asyncio.create_task(node.execute(context))
    await asyncio.sleep(0.2)  # let the statement start in its thread
    task.cancel()
    async with asyncio.timeout(GUARD):
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_without_workspace_sqlite_is_not_configured(run_node: RunNode) -> None:
    with pytest.raises(NodeError) as exc:
        await sql(run_node, None, "SELECT 1")
    assert exc.value.code == "WORKSPACE_NOT_CONFIGURED"


def test_sql_and_engine_are_required_config() -> None:
    validate = DatabaseNode.definition.validate_config
    with pytest.raises(NodeConfigurationError):
        validate({"engine": "sqlite"})
    with pytest.raises(NodeConfigurationError):
        validate({"engine": "mysql", "sql": "SELECT 1"})
    with pytest.raises(NodeConfigurationError):  # never a plaintext URL
        validate(
            {
                "engine": "postgres",
                "sql": "SELECT 1",
                "credential": "postgres://u:p@h/d",
            }
        )


# -- PostgreSQL -------------------------------------------------------------


class _Credentials:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    async def resolve(self, reference: str) -> str:
        return self._values[reference]


async def pg(
    run_node: RunNode,
    url: str,
    statement: str,
    params: Any = None,
    *,
    policy: NetworkPolicy | None = None,
    **config: Any,
) -> dict[str, Any]:
    policy = policy or NetworkPolicy(allow_private_networks=True)
    cfg = {
        "engine": "postgres",
        "credential": "credential:pg",
        "sql": statement,
    } | config
    result = await run_node(
        DatabaseNode(),
        config=cfg,
        inputs={} if params is None else {"params": params},
        credentials=_Credentials({"credential:pg": url}),
        services=NodeServices(network=policy),
    )
    return dict(result.outputs)


@pytest.fixture
async def pg_url(db_url: str) -> str:
    """The integration PostgreSQL with an empty scratch table per test."""
    await pg_admin(db_url, "DROP TABLE IF EXISTS node_items")
    await pg_admin(
        db_url,
        "CREATE TABLE node_items (id serial PRIMARY KEY, name text, price numeric)",
    )
    return db_url


async def pg_admin(url: str, statement: str) -> None:
    import psycopg

    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute(statement)


async def test_postgres_parameterized_writes_and_reads(
    run_node: RunNode, pg_url: str
) -> None:
    inserted = await pg(
        run_node,
        pg_url,
        "INSERT INTO node_items (name, price) VALUES (%(name)s, %(price)s)",
        {"name": INJECTION, "price": "9.99"},
    )
    assert inserted["row_count"] == 1
    rows = await pg(
        run_node,
        pg_url,
        "SELECT name, price FROM node_items WHERE name = %s",
        [INJECTION],
    )
    # numeric comes back as exact text, not a rounded float.
    assert rows["rows"] == [{"name": INJECTION, "price": "9.99"}]


async def test_postgres_refuses_several_statements(
    run_node: RunNode, pg_url: str
) -> None:
    with pytest.raises(NodeError) as exc:
        await pg(run_node, pg_url, "SELECT 1; DROP TABLE node_items")
    assert exc.value.code == "DATABASE_STATEMENT_ERROR"
    assert (await pg(run_node, pg_url, "SELECT count(*) AS n FROM node_items"))[
        "rows"
    ] == [{"n": 0}]


async def test_postgres_read_only_and_rollback(run_node: RunNode, pg_url: str) -> None:
    with pytest.raises(NodeError) as exc:
        await pg(
            run_node,
            pg_url,
            "INSERT INTO node_items (name) VALUES ('x')",
            read_only=True,
        )
    assert exc.value.details["sqlstate"] == "25006"  # read_only_sql_transaction
    with pytest.raises(NodeError):
        await pg(
            run_node,
            pg_url,
            "INSERT INTO node_items (id, name) VALUES (1, 'a'), (1, 'b')",
        )
    assert (await pg(run_node, pg_url, "SELECT count(*) AS n FROM node_items"))[
        "rows"
    ] == [{"n": 0}]


async def test_postgres_timeout(run_node: RunNode, pg_url: str) -> None:
    started = time.monotonic()
    with pytest.raises(NodeError) as exc:
        await pg(run_node, pg_url, "SELECT pg_sleep(30)", timeout_seconds=0.5)
    assert exc.value.code == "DATABASE_TIMEOUT"
    assert time.monotonic() - started < GUARD


async def test_postgres_destination_guard_applies(
    run_node: RunNode, pg_url: str
) -> None:
    # The container listens on a loopback/private address: refused by default.
    with pytest.raises(NodeError) as exc:
        await pg(run_node, pg_url, "SELECT 1", policy=NetworkPolicy())
    assert exc.value.code == "DESTINATION_BLOCKED"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql:///db?host=/var/run/postgresql",  # Unix socket
        "postgresql://u@h1,h2/db",  # several hosts
        "postgresql://u@db.example/db?hostaddr=127.0.0.1",  # pinned address
        "not a url",
    ],
)
async def test_postgres_connection_url_must_name_one_network_host(
    run_node: RunNode, url: str
) -> None:
    with pytest.raises(NodeError) as exc:
        await pg(run_node, url, "SELECT 1")
    assert exc.value.code == "DATABASE_CONFIG_INVALID"


async def test_postgres_connection_errors_never_echo_the_url(
    run_node: RunNode, pg_url: str
) -> None:
    secret = "s3cret-password-for-test"
    bad = pg_url.replace("://", f"://nobody:{secret}@", 1).split("@", 1)
    url = bad[0] + "@" + bad[1].split("@", 1)[-1]
    with pytest.raises(NodeError) as exc:
        await pg(run_node, url, "SELECT 1")
    assert exc.value.code == "DATABASE_CONNECTION_ERROR"
    assert secret not in str(exc.value) and "nobody" not in str(exc.value)
