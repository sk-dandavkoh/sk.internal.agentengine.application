"""Installation backup and restore (architecture §110/§111).

A backup is one gzip-compressed JSON document: a manifest plus every row of
every engine table, read in one REPEATABLE READ transaction so it is a
consistent snapshot. It covers workflows, versions, executions, events, node logs,
users and credentials (still encrypted). It deliberately omits:

- login sessions: restored installations start logged out;
- the credential encryption key: kept separately by the operator (§110);
  without it the restored credentials cannot be decrypted;
- plugin code: the manifest records the enabled plugins, and restore
  refuses until they are installed again.

Restore validates before it writes: format, schema revision (the target is
migrated to this engine's head first and must match the backup), plugins
and the encryption key. It then writes everything in one transaction. It
refuses a non-empty installation unless ``replace`` is given (the explicit
confirmation §111 asks for); with it, existing data is replaced atomically.

The backup contains password hashes and encrypted credentials: callers must
store it like a database dump.
"""

import base64
import binascii
import gzip
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import LargeBinary, Table, func, select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.types import DateTime

from akasha import __version__ as ENGINE_VERSION
from akasha.domain.primitives import semver_key, utcnow, version_satisfies
from akasha.errors import AkashaError, DomainValidationError
from akasha.infrastructure import db_models as m
from akasha.infrastructure.database import Database
from akasha.security.crypto import SecretCipher, SecretDecryptionError

FORMAT = "akasha-backup"
FORMAT_VERSION = 1

# FK order (parents first); sessions are never backed up.
# Tables whose identity ``position`` is their order (constant names, never
# input: they are interpolated into DDL).
_ORDERED: Final = ("events", "node_logs")

TABLES: tuple[Table, ...] = tuple(
    t for t in m.metadata.sorted_tables if t.name != "sessions"
)


class BackupInvalidError(AkashaError):
    code = "BACKUP_INVALID"
    http_status = 422


class BackupIncompatibleError(AkashaError):
    code = "BACKUP_INCOMPATIBLE"
    http_status = 409


class RestoreTargetNotEmptyError(AkashaError):
    code = "RESTORE_TARGET_NOT_EMPTY"
    http_status = 409


@dataclass(frozen=True)
class BackupSummary:
    schema_revision: str
    engine_version: str
    created_at: str
    rows: Mapping[str, int]  # table name -> row count


async def create_backup(
    database: Database, *, plugins: Mapping[str, str]
) -> tuple[bytes, BackupSummary]:
    """Snapshot the installation. ``plugins``: enabled plugin id → version."""
    async with database.engine.connect() as conn:
        await conn.execution_options(isolation_level="REPEATABLE READ")
        async with conn.begin():
            revision = await _schema_revision(conn)
            tables = {}
            for table in TABLES:
                rows = await conn.execute(
                    select(table).order_by(*table.primary_key.columns)
                )
                tables[table.name] = [_encode(dict(row)) for row in rows.mappings()]
    summary = BackupSummary(
        schema_revision=revision,
        engine_version=ENGINE_VERSION,
        created_at=utcnow().isoformat(),
        rows={name: len(rows) for name, rows in tables.items()},
    )
    # ponytail: whole backup in memory; stream per table when installations
    # outgrow RAM.
    document = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "engine_version": summary.engine_version,
        "schema_revision": summary.schema_revision,
        "created_at": summary.created_at,
        "plugins": dict(sorted(plugins.items())),
        "tables": tables,
    }
    return gzip.compress(json.dumps(document).encode()), summary


async def restore_backup(
    database: Database,
    database_url: str,
    data: bytes,
    *,
    plugins: Mapping[str, str],
    cipher: SecretCipher | None,
    replace: bool = False,
) -> BackupSummary:
    """Validate ``data`` against this installation, then load it atomically."""
    document = _read(data)
    tables: dict[str, list[dict[str, Any]]] = document["tables"]
    _check_plugins(document["plugins"], plugins)
    rows = {t.name: [_decode(t, r) for r in tables.get(t.name, [])] for t in TABLES}
    _check_key(rows["credentials"], cipher)
    await database.run_migrations(database_url)
    try:
        await _load(database, document, rows, replace=replace)
    except (IntegrityError, DataError):
        # The transaction rolled back: the installation is unchanged.
        raise BackupInvalidError(
            "The backup data violates the database constraints."
        ) from None
    return BackupSummary(
        schema_revision=document["schema_revision"],
        engine_version=document["engine_version"],
        created_at=document["created_at"],
        rows={name: len(r) for name, r in rows.items()},
    )


async def _load(
    database: Database,
    document: Mapping[str, Any],
    rows: Mapping[str, list[dict[str, Any]]],
    *,
    replace: bool,
) -> None:
    async with database.engine.begin() as conn:
        revision = await _schema_revision(conn)
        if revision != document["schema_revision"]:
            raise BackupIncompatibleError(
                "The backup was taken with a different database schema; restore"
                " it with the engine version that created it, then upgrade.",
                details={
                    "backup_schema_revision": document["schema_revision"],
                    "schema_revision": revision,
                    "backup_engine_version": document["engine_version"],
                },
            )
        if replace:
            names = ", ".join(t.name for t in m.metadata.sorted_tables)
            await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        else:
            for table in m.metadata.sorted_tables:
                if await conn.scalar(select(func.count()).select_from(table)):
                    raise RestoreTargetNotEmptyError(
                        "The installation already holds data; restore with"
                        " replace to overwrite it.",
                        details={"table": table.name},
                    )
        # ``position`` is GENERATED ALWAYS; the original positions are the
        # event/log order, so they are restored as they were.
        for name in _ORDERED:
            await conn.execute(
                text(
                    f"ALTER TABLE {name} ALTER COLUMN position SET GENERATED BY DEFAULT"
                )
            )
        for table in TABLES:
            if rows[table.name]:
                await conn.execute(table.insert(), rows[table.name])
        for name in _ORDERED:
            await conn.execute(
                text(f"ALTER TABLE {name} ALTER COLUMN position SET GENERATED ALWAYS")
            )
            ordered = m.metadata.tables[name]
            await conn.execute(
                select(
                    func.setval(
                        func.pg_get_serial_sequence(name, "position"),
                        func.coalesce(func.max(ordered.c.position), 0) + 1,
                        False,
                    )
                )
            )


async def _schema_revision(conn: AsyncConnection) -> str:
    revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    return str(revision)


def _encode(row: dict[str, Any]) -> dict[str, Any]:
    for key, value in row.items():
        if isinstance(value, datetime):
            row[key] = value.isoformat()
        elif isinstance(value, bytes | memoryview):
            row[key] = base64.b64encode(bytes(value)).decode("ascii")
    return row


def _decode(table: Table, row: object) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) != set(table.columns.keys()):
        raise BackupInvalidError(
            "A backup row does not match the table schema.",
            details={"table": table.name},
        )
    decoded = dict(row)
    try:
        for column in table.columns:
            value = decoded[column.key]
            if value is None:
                continue
            if isinstance(column.type, DateTime):
                decoded[column.key] = datetime.fromisoformat(value)
            elif isinstance(column.type, LargeBinary):
                decoded[column.key] = base64.b64decode(value, validate=True)
    except (TypeError, ValueError, binascii.Error):
        raise BackupInvalidError(
            "A backup row holds a malformed value.", details={"table": table.name}
        ) from None
    return decoded


def _read(data: bytes) -> dict[str, Any]:
    try:
        document = json.loads(gzip.decompress(data))
    except (OSError, EOFError, ValueError):
        raise BackupInvalidError("The file is not an Akasha backup.") from None
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise BackupInvalidError("The file is not an Akasha backup.")
    if document.get("format_version") != FORMAT_VERSION:
        raise BackupIncompatibleError(
            "Unsupported backup format version.",
            details={"format_version": str(document.get("format_version"))},
        )
    fields = {
        "engine_version": str,
        "schema_revision": str,
        "created_at": str,
        "plugins": dict,
        "tables": dict,
    }
    for name, kind in fields.items():
        if not isinstance(document.get(name), kind):
            raise BackupInvalidError(
                "The backup manifest is malformed.", details={"field": name}
            )
    known = {t.name for t in TABLES}
    unknown = sorted(set(document["tables"]) - known)
    if unknown or not all(isinstance(r, list) for r in document["tables"].values()):
        raise BackupInvalidError(
            "The backup holds unknown or malformed tables.",
            details={"tables": unknown},
        )
    return document


def _check_plugins(required: Mapping[str, object], enabled: Mapping[str, str]) -> None:
    """Every plugin enabled at backup time must be enabled here in a
    SemVer-compatible version (same major, at least the backed-up one)."""
    problems = {}
    for plugin_id, version in sorted(required.items()):
        installed = enabled.get(plugin_id)
        if installed is None:
            problems[plugin_id] = f"not installed (backup: {version})"
            continue
        try:
            major = semver_key(str(version))[0]
            ok = version_satisfies(installed, f">={version},<{major + 1}.0.0")
        except DomainValidationError:
            ok = False
        if not ok:
            problems[plugin_id] = f"{installed} incompatible with {version}"
    if problems:
        raise BackupIncompatibleError(
            "Install the plugins the backup needs before restoring it.",
            details=problems,
        )


def _check_key(credentials: list[dict[str, Any]], cipher: SecretCipher | None) -> None:
    """Restored credentials must stay usable: the configured key must
    decrypt every one of them (the plaintext is discarded at once)."""
    if not credentials:
        return
    if cipher is None:
        raise BackupIncompatibleError(
            "The backup holds credentials; configure the encryption key they"
            " were stored with (AKASHA_CREDENTIAL_ENCRYPTION_KEY).",
        )
    try:
        for row in credentials:
            cipher.decrypt(row["ciphertext"], context=row["name"])
    except SecretDecryptionError:
        raise BackupIncompatibleError(
            "The configured encryption key cannot decrypt the backup's credentials.",
        ) from None
