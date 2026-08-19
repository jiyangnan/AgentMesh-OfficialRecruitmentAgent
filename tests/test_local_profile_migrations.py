from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from official_recruitment_agent.local_profile_migrations import (
    LOCAL_PROFILE_SCHEMA_VERSION,
    LocalProfileMigrationError,
    migrate_local_profile_database,
)


LEGACY_SCHEMA = """
CREATE TABLE profile_handoff_proposals (
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
CREATE TABLE local_profile_facts (
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
    UNIQUE (workspace_ref, canonical_key, scope, scope_ref)
);
CREATE TABLE extension_local_sessions (
    session_id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL,
    extension_origin TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at_epoch INTEGER NOT NULL,
    revoked_at TEXT
);
"""


def _legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(LEGACY_SCHEMA)
        connection.execute(
            """
            INSERT INTO local_profile_facts VALUES (
                'fact-synthetic', 'ws_synthetic', 'student_id', '学号',
                'SYNTHETIC-ONLY', 'account', '', 'standard', '[]',
                'pq_synthetic', NULL, NULL,
                '2026-08-19T00:00:00+00:00',
                '2026-08-19T00:00:00+00:00'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO extension_local_sessions VALUES (
                'session-synthetic', 'orainstall_synthetic',
                'chrome-extension://synthetic', 'hash-synthetic',
                '2026-08-19T00:00:00+00:00', 4102444800, NULL
            )
            """
        )


def test_legacy_database_migrates_without_changing_private_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-profile.sqlite3"
    _legacy_database(path)

    report = migrate_local_profile_database(
        path,
        client_version="0.1.13",
    )

    assert report["from_schema_version"] == 1
    assert report["schema_version"] == LOCAL_PROFILE_SCHEMA_VERSION
    assert report["migrated"] is True
    assert report["backup_created"] is True
    assert Path(report["backup_path"]).is_file()
    with sqlite3.connect(path) as connection:
        fact = connection.execute(
            "SELECT canonical_key, value FROM local_profile_facts"
        ).fetchone()
        session = connection.execute(
            "SELECT session_id, token_hash FROM extension_local_sessions"
        ).fetchone()
        metadata = connection.execute(
            "SELECT schema_version, installed_client_version "
            "FROM local_schema_meta WHERE singleton = 1"
        ).fetchone()
    assert fact == ("student_id", "SYNTHETIC-ONLY")
    assert session == ("session-synthetic", "hash-synthetic")
    assert metadata == (LOCAL_PROFILE_SCHEMA_VERSION, "0.1.13")


def test_repeated_open_is_idempotent_and_does_not_create_new_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-profile.sqlite3"
    first = migrate_local_profile_database(path, client_version="0.1.13")
    second = migrate_local_profile_database(path, client_version="0.1.13")

    assert first["from_schema_version"] == 0
    assert second["from_schema_version"] == LOCAL_PROFILE_SCHEMA_VERSION
    assert second["migrated"] is False
    assert second["backup_created"] is False
    with sqlite3.connect(path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM local_schema_migrations"
        ).fetchone()[0]
    assert count == 1


def test_newer_database_refuses_downgrade_without_changing_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-profile.sqlite3"
    migrate_local_profile_database(path, client_version="0.1.13")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE local_schema_meta SET schema_version = 99 WHERE singleton = 1"
        )

    with pytest.raises(
        LocalProfileMigrationError,
        match="拒绝降级",
    ) as captured:
        migrate_local_profile_database(path, client_version="0.1.12")

    assert captured.value.code == "local_profile_schema_too_new"
    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT schema_version FROM local_schema_meta WHERE singleton = 1"
        ).fetchone()[0]
    assert version == 99


def test_failed_legacy_migration_restores_the_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-profile.sqlite3"
    _legacy_database(path)

    def fail(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("synthetic migration failure")

    with pytest.raises(LocalProfileMigrationError) as captured:
        migrate_local_profile_database(
            path,
            client_version="0.1.13",
            before_commit=fail,
        )

    assert captured.value.code == "local_profile_migration_failed"
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        value = connection.execute(
            "SELECT value FROM local_profile_facts"
        ).fetchone()[0]
    assert "local_schema_meta" not in tables
    assert value == "SYNTHETIC-ONLY"


def test_failed_fresh_migration_leaves_no_partial_database(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-profile.sqlite3"

    def fail(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("synthetic fresh migration failure")

    with pytest.raises(LocalProfileMigrationError) as captured:
        migrate_local_profile_database(
            path,
            client_version="0.1.13",
            before_commit=fail,
        )

    assert captured.value.code == "local_profile_migration_failed"
    assert path.exists() is False
