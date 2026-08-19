from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4


LOCAL_PROFILE_SCHEMA_VERSION = 2
LEGACY_PROFILE_SCHEMA_VERSION = 1

_DATA_TABLES = frozenset(
    {
        "profile_handoff_proposals",
        "local_profile_facts",
        "extension_local_sessions",
    }
)

_DATA_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS profile_handoff_proposals (
    proposal_id TEXT PRIMARY KEY,
    workspace_ref TEXT NOT NULL,
    fill_task_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    handoff_jti TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    answers_json TEXT NOT NULL,
    questions_json TEXT NOT NULL,
    proposal_capability TEXT NOT NULL,
    proposal_capability_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at_epoch INTEGER NOT NULL,
    confirmed_at TEXT
);
CREATE TABLE IF NOT EXISTS local_profile_facts (
    fact_id TEXT PRIMARY KEY,
    workspace_ref TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    label TEXT NOT NULL,
    value TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_ref TEXT NOT NULL,
    privacy TEXT NOT NULL,
    aliases_json TEXT NOT NULL,
    source_question_id TEXT NOT NULL,
    source_site_domain TEXT,
    source_application_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (
        workspace_ref,
        canonical_key,
        scope,
        scope_ref
    )
);
CREATE TABLE IF NOT EXISTS extension_local_sessions (
    session_id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL,
    extension_origin TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at_epoch INTEGER NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_extension_local_sessions_installation
ON extension_local_sessions (
    installation_id,
    extension_origin,
    revoked_at
);
"""

_MIGRATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS local_schema_meta (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    installed_client_version TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS local_schema_migrations (
    migration_id TEXT PRIMARY KEY,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    installed_client_version TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""


class LocalProfileMigrationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _detect_schema_version(connection: sqlite3.Connection) -> int:
    tables = _table_names(connection)
    if not tables:
        return 0
    if "local_schema_meta" in tables:
        row = connection.execute(
            "SELECT schema_version FROM local_schema_meta WHERE singleton = 1"
        ).fetchone()
        if row is None or not isinstance(row[0], int):
            raise LocalProfileMigrationError(
                "local_profile_schema_invalid",
                "本机资料库的版本记录无效，已停止打开以保护原数据。",
            )
        return int(row[0])
    if _DATA_TABLES.issubset(tables):
        return LEGACY_PROFILE_SCHEMA_VERSION
    raise LocalProfileMigrationError(
        "local_profile_schema_unknown",
        "本机资料库结构无法识别，已停止迁移以保护原数据。",
    )


def _backup_database(
    connection: sqlite3.Connection,
    path: Path,
    *,
    from_version: int,
) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_path = backup_dir / (
        f"{path.stem}-schema-{from_version}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid4().hex[:8]}.sqlite3"
    )
    backup = sqlite3.connect(backup_path)
    try:
        connection.backup(backup)
    finally:
        backup.close()
    if os.name != "nt":
        backup_path.chmod(0o600)
    return backup_path


def _restore_backup(path: Path, backup_path: Path) -> None:
    source = sqlite3.connect(
        f"file:{backup_path.as_posix()}?mode=ro",
        uri=True,
        timeout=30,
    )
    destination = sqlite3.connect(path, timeout=30)
    try:
        source.backup(destination)
        destination.commit()
        destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        integrity = destination.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]).casefold() != "ok":
            raise LocalProfileMigrationError(
                "local_profile_restore_integrity_failed",
                "本机资料库恢复后完整性检查失败，已停止使用该资料库。",
            )
    finally:
        destination.close()
        source.close()
    if os.name != "nt":
        path.chmod(0o600)


def restore_local_profile_database(path: Path, backup_path: Path) -> None:
    """Restore a migration backup after a client switch fails."""

    database_path = path.expanduser().resolve()
    candidate = backup_path.expanduser().resolve()
    expected_parent = (database_path.parent / "backups").resolve()
    if candidate.parent != expected_parent or not candidate.is_file():
        raise LocalProfileMigrationError(
            "local_profile_backup_invalid",
            "本机资料库升级备份无效，已停止自动恢复。",
        )
    _restore_backup(database_path, candidate)
    if os.name != "nt":
        database_path.chmod(0o600)


def migrate_local_profile_database(
    path: Path,
    *,
    client_version: str,
    before_commit: Callable[[sqlite3.Connection], None] | None = None,
) -> dict[str, Any]:
    """Open, migrate and verify the local private profile database."""

    database_path = path.expanduser()
    database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection: sqlite3.Connection | None = None
    backup_path: Path | None = None
    from_version = 0
    migrated = False
    try:
        connection = sqlite3.connect(database_path, timeout=5)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        from_version = _detect_schema_version(connection)
        if from_version > LOCAL_PROFILE_SCHEMA_VERSION:
            raise LocalProfileMigrationError(
                "local_profile_schema_too_new",
                "本机资料库由更高版本客户端创建，当前版本拒绝降级写入。",
            )
        if from_version not in {0, LEGACY_PROFILE_SCHEMA_VERSION, LOCAL_PROFILE_SCHEMA_VERSION}:
            raise LocalProfileMigrationError(
                "local_profile_schema_unsupported",
                "本机资料库版本不受当前客户端支持。",
            )

        if 0 < from_version < LOCAL_PROFILE_SCHEMA_VERSION:
            backup_path = _backup_database(
                connection,
                database_path,
                from_version=from_version,
            )

        # sqlite3.executescript() commits an already-open transaction before
        # running the script. Put BEGIN inside the script so schema creation,
        # metadata writes and the caller's migration hook stay atomic.
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + _DATA_SCHEMA_SQL
            + "\n"
            + _MIGRATION_SCHEMA_SQL
        )
        now = _utc_now()
        if from_version < LOCAL_PROFILE_SCHEMA_VERSION:
            connection.execute(
                """
                INSERT OR IGNORE INTO local_schema_migrations (
                    migration_id,
                    from_version,
                    to_version,
                    installed_client_version,
                    applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"local-profile-{from_version}-to-{LOCAL_PROFILE_SCHEMA_VERSION}",
                    from_version,
                    LOCAL_PROFILE_SCHEMA_VERSION,
                    client_version,
                    now,
                ),
            )
            migrated = True
        connection.execute(
            """
            INSERT INTO local_schema_meta (
                singleton,
                schema_version,
                installed_client_version,
                updated_at
            ) VALUES (1, ?, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                schema_version = excluded.schema_version,
                installed_client_version = excluded.installed_client_version,
                updated_at = excluded.updated_at
            """,
            (LOCAL_PROFILE_SCHEMA_VERSION, client_version, now),
        )
        if before_commit is not None:
            before_commit(connection)
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]).casefold() != "ok":
            raise LocalProfileMigrationError(
                "local_profile_integrity_failed",
                "本机资料库迁移后完整性检查失败，已停止使用新版。",
            )
    except LocalProfileMigrationError:
        if connection is not None:
            connection.rollback()
            connection.close()
            connection = None
        if backup_path is not None:
            _restore_backup(database_path, backup_path)
        elif from_version == 0:
            database_path.unlink(missing_ok=True)
        raise
    except sqlite3.Error as exc:
        store_never_opened = connection is None
        if connection is not None:
            connection.rollback()
            connection.close()
            connection = None
        if backup_path is not None:
            _restore_backup(database_path, backup_path)
        elif from_version == 0:
            database_path.unlink(missing_ok=True)
        raise LocalProfileMigrationError(
            (
                "local_profile_store_unavailable"
                if store_never_opened
                else "local_profile_migration_failed"
            ),
            (
                "本机资料库暂时无法访问，请让 Agent 运行连接诊断。"
                if store_never_opened
                else "本机资料库迁移失败，已保留升级前数据。"
            ),
        ) from exc
    finally:
        if connection is not None:
            connection.close()

    if os.name != "nt":
        database_path.chmod(0o600)
    return {
        "status": "ready",
        "database_path": str(database_path),
        "schema_version": LOCAL_PROFILE_SCHEMA_VERSION,
        "from_schema_version": from_version,
        "migrated": migrated,
        "backup_created": backup_path is not None,
        "backup_path": str(backup_path) if backup_path is not None else None,
        "integrity_check": "ok",
    }
