from __future__ import annotations

import base64
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

import official_recruitment_agent.release_update as updates
from official_recruitment_agent.local_profile_migrations import (
    migrate_local_profile_database,
)


OFFICIAL_RELEASES = (
    "https://github.com/jiyangnan/AgentMesh-OfficialRecruitmentAgent/releases"
)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _manifest(private_key: Ed25519PrivateKey) -> dict[str, Any]:
    wheel_sha = "1" * 64
    payload: dict[str, Any] = {
        "product": "officialrecruitment",
        "channel": "stable",
        "protocol_version": "1.0",
        "latest_client_version": "0.1.13",
        "minimum_supported_version": "0.1.13",
        "git_tag": "v0.6.17",
        "git_commit": "a" * 40,
        "artifact_sha256": wheel_sha,
        "published_at": "2026-08-19T00:00:00Z",
        "notes_url": f"{OFFICIAL_RELEASES}/tag/v0.6.17",
        "signature_algorithm": "Ed25519",
        "key_id": updates.RELEASE_SIGNING_KEY_ID,
        "assets": [
            {
                "role": "adapter_wheel",
                "version": "0.1.13",
                "url": (
                    f"{OFFICIAL_RELEASES}/download/"
                    "v0.6.17/official_recruitment_agent-0.1.13-"
                    "py3-none-any.whl"
                ),
                "sha256": wheel_sha,
                "bytes": 1234,
            },
            {
                "role": "host_skill",
                "version": "0.3.8",
                "skill_version": "0.3.8",
                "url": f"{OFFICIAL_RELEASES}/download/v0.6.17/SKILL.md",
                "sha256": "2" * 64,
                "bytes": 2345,
            },
            {
                "role": "extension_zip",
                "version": "0.6.17",
                "url": (
                    f"{OFFICIAL_RELEASES}/download/"
                    "v0.6.17/agentmesh-officialrecruitment-extension-"
                    "0.6.17.zip"
                ),
                "sha256": "3" * 64,
                "bytes": 3456,
            },
        ],
    }
    payload["signature"] = _b64url(
        private_key.sign(updates._canonical_json_bytes(payload))
    )
    return payload


@pytest.fixture
def signed_manifest(monkeypatch) -> dict[str, Any]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setattr(
        updates,
        "RELEASE_SIGNING_PUBLIC_KEY",
        _b64url(public_key),
    )
    return _manifest(private_key)


def _managed_root(path: Path) -> Path:
    path.mkdir(parents=True)
    updates._write_private_json(
        updates.install_state_path(path),
        {
            "schema_version": 1,
            "managed": True,
            "product": "officialrecruitment",
            "install_type": "official-installer",
            "current_client_version": "0.1.12",
            "current_skill_version": "0.3.7",
        },
    )
    updates._write_private_json(
        updates.current_pointer_path(path),
        {
            "schema_version": 1,
            "product": "officialrecruitment",
            "client_version": "0.1.12",
            "cli_path": "/synthetic/old/ora-workbench",
        },
    )
    return path


def _candidate_runtime(root: Path, version: str) -> tuple[Path, Path, Path]:
    venv = root / "releases" / version / "venv"
    python, cli, native = updates._venv_paths(venv)
    for executable in (python, cli, native):
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"synthetic executable")
    return python, cli, native


def _implicit_legacy_database(path: Path) -> None:
    migrate_local_profile_database(path, client_version="0.1.12")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "DROP TABLE local_schema_meta"
        )
        connection.execute(
            "DROP TABLE local_schema_migrations"
        )
        connection.execute(
            """
            INSERT INTO local_profile_facts (
                fact_id, workspace_ref, canonical_key, label, value,
                scope, scope_ref, privacy, aliases_json,
                source_question_id, source_site_domain,
                source_application_id, created_at, updated_at
            ) VALUES (
                'fact-synthetic', 'ws_synthetic', 'student_id', '学号',
                'SYNTHETIC-ONLY', 'account', '', 'standard', '[]',
                'pq_synthetic', NULL, NULL,
                '2026-08-19T00:00:00+00:00',
                '2026-08-19T00:00:00+00:00'
            )
            """
        )


def test_signed_manifest_accepts_bound_assets_and_rejects_tampering(
    signed_manifest: dict[str, Any],
) -> None:
    verified = updates.verify_release_manifest(signed_manifest)

    assert verified["latest_client_version"] == "0.1.13"
    assert [asset["role"] for asset in verified["assets"]] == [
        "adapter_wheel",
        "host_skill",
        "extension_zip",
    ]

    tampered = json.loads(json.dumps(signed_manifest))
    tampered["assets"][0]["url"] = (
        "https://attacker.example/client.whl"
    )
    with pytest.raises(
        updates.ClientUpdateError,
        match="签名验证失败",
    ):
        updates.verify_release_manifest(tampered)


def test_manifest_rejects_encoded_parent_path_even_with_a_valid_signature(
    monkeypatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setattr(
        updates,
        "RELEASE_SIGNING_PUBLIC_KEY",
        _b64url(public_key),
    )
    manifest = _manifest(private_key)
    manifest["assets"][0]["url"] = (
        "https://recruit.agentmesh360.com/downloads/%2e%2e/client.whl"
    )
    unsigned = dict(manifest)
    unsigned.pop("signature")
    manifest["signature"] = _b64url(
        private_key.sign(updates._canonical_json_bytes(unsigned))
    )

    with pytest.raises(
        updates.ClientUpdateError,
        match="不在允许的官方地址",
    ):
        updates.verify_release_manifest(manifest)


def test_unmanaged_source_checkout_does_not_contact_release_service(
    monkeypatch,
    tmp_path: Path,
) -> None:
    contacted = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("release service must not be called")

    result = updates.check_for_update(
        root=tmp_path,
        opener=fail_if_called,
    )

    assert result == {
        "status": "unmanaged",
        "current_version": updates.__version__,
    }
    assert contacted is False


def test_installer_finalization_migrates_legacy_data_and_switches_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "managed"
    python, cli, native = _candidate_runtime(root, "0.1.13")
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "---\nname: agentmesh-officialrecruitment\n"
        "version: 0.3.8\n---\n",
        encoding="utf-8",
    )
    database = tmp_path / "private.sqlite3"
    _implicit_legacy_database(database)
    skill_target = tmp_path / "skills" / "SKILL.md"
    native_manifest = tmp_path / "native-host.json"
    extension_root = tmp_path / "extension"
    monkeypatch.setattr(updates, "__version__", "0.1.13")
    monkeypatch.setattr(updates.sys, "executable", str(python))
    monkeypatch.setattr(updates, "_skill_targets", lambda: [skill_target])
    monkeypatch.setattr(
        updates,
        "default_local_profile_path",
        lambda: database,
    )
    monkeypatch.setattr(
        updates,
        "native_host_manifest_path",
        lambda: native_manifest,
    )

    def fake_native_install(*, executable, extension_root):
        assert executable == native
        native_manifest.write_text("new native", encoding="utf-8")
        return {
            "status": "ready",
            "manifest_path": str(native_manifest),
            "extension_root": str(extension_root),
        }

    monkeypatch.setattr(
        updates,
        "install_native_messaging_host",
        fake_native_install,
    )

    result = updates.finalize_managed_install(
        skill,
        root=root,
        extension_root=extension_root,
    )

    assert result["status"] == "ready"
    assert result["local_profile"]["from_schema_version"] == 1
    assert result["local_profile"]["schema_version"] == 2
    assert updates._read_json(updates.current_pointer_path(root))[
        "cli_path"
    ] == str(cli)
    state = updates._read_json(updates.install_state_path(root))
    assert state["managed"] is True
    assert state["current_client_version"] == "0.1.13"
    assert state["current_skill_version"] == "0.3.8"
    assert "version: 0.3.8" in skill_target.read_text(encoding="utf-8")
    with sqlite3.connect(database) as connection:
        value = connection.execute(
            "SELECT value FROM local_profile_facts WHERE fact_id = ?",
            ("fact-synthetic",),
        ).fetchone()[0]
    assert value == "SYNTHETIC-ONLY"


def test_installer_finalization_failure_restores_legacy_data_and_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = _managed_root(tmp_path / "managed")
    python, _cli, _native = _candidate_runtime(root, "0.1.13")
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "---\nname: agentmesh-officialrecruitment\n"
        "version: 0.3.8\n---\n",
        encoding="utf-8",
    )
    database = tmp_path / "private.sqlite3"
    _implicit_legacy_database(database)
    skill_target = tmp_path / "skills" / "SKILL.md"
    skill_target.parent.mkdir(parents=True)
    skill_target.write_text("legacy skill", encoding="utf-8")
    native_manifest = tmp_path / "native-host.json"
    native_manifest.write_text("legacy native", encoding="utf-8")
    extension_root = tmp_path / "extension"
    pairing_state = updates.extension_pairing_state_path(extension_root)
    pairing_state.parent.mkdir(parents=True, exist_ok=True)
    pairing_state.write_text("legacy pairing", encoding="utf-8")
    old_pointer = updates.current_pointer_path(root).read_bytes()
    old_state = updates.install_state_path(root).read_bytes()
    monkeypatch.setattr(updates, "__version__", "0.1.13")
    monkeypatch.setattr(updates.sys, "executable", str(python))
    monkeypatch.setattr(updates, "_skill_targets", lambda: [skill_target])
    monkeypatch.setattr(
        updates,
        "default_local_profile_path",
        lambda: database,
    )
    monkeypatch.setattr(
        updates,
        "native_host_manifest_path",
        lambda: native_manifest,
    )

    def fail_native_install(*, executable, extension_root):
        del executable, extension_root
        native_manifest.write_text("partial new native", encoding="utf-8")
        pairing_state.write_text("partial new pairing", encoding="utf-8")
        raise OSError("synthetic native failure")

    monkeypatch.setattr(
        updates,
        "install_native_messaging_host",
        fail_native_install,
    )

    with pytest.raises(
        updates.ClientUpdateError,
        match="旧入口、Skill、浏览器连接和本机资料已恢复",
    ):
        updates.finalize_managed_install(
            skill,
            root=root,
            extension_root=extension_root,
        )

    assert updates.current_pointer_path(root).read_bytes() == old_pointer
    assert updates.install_state_path(root).read_bytes() == old_state
    assert skill_target.read_text(encoding="utf-8") == "legacy skill"
    assert native_manifest.read_text(encoding="utf-8") == "legacy native"
    assert pairing_state.read_text(encoding="utf-8") == "legacy pairing"
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        value = connection.execute(
            "SELECT value FROM local_profile_facts WHERE fact_id = ?",
            ("fact-synthetic",),
        ).fetchone()[0]
    assert "local_schema_meta" not in tables
    assert value == "SYNTHETIC-ONLY"


def test_managed_update_switches_pointer_and_preserves_managed_state(
    monkeypatch,
    tmp_path: Path,
    signed_manifest: dict[str, Any],
) -> None:
    root = _managed_root(tmp_path / "managed")
    skill_target = tmp_path / "skills" / "SKILL.md"
    monkeypatch.setattr(updates, "__version__", "0.1.12")
    monkeypatch.setattr(updates, "_skill_targets", lambda: [skill_target])
    monkeypatch.setattr(
        updates,
        "default_extension_root",
        lambda: tmp_path / "extension",
    )
    monkeypatch.setattr(
        updates,
        "native_host_manifest_path",
        lambda: tmp_path / "native-host.json",
    )
    monkeypatch.setattr(
        updates,
        "_update_existing_zip_extension",
        lambda *_args, **_kwargs: {
            "status": "current",
            "changed": False,
            "chrome_reload_required": False,
        },
    )

    def fake_download(asset, destination, *, opener):
        del opener
        if asset["role"] == "host_skill":
            destination.write_text(
                "---\nname: agentmesh-officialrecruitment\n"
                "version: 0.3.8\n---\n",
                encoding="utf-8",
            )
        else:
            destination.write_bytes(b"synthetic-wheel")
        return destination

    monkeypatch.setattr(updates, "_download_asset", fake_download)

    def fake_run(command, *, env=None, timeout=180):
        del env, timeout
        joined = " ".join(command)
        if "import official_recruitment_agent" in joined:
            return "0.1.13"
        if command[-1] == "upgrade-check":
            return json.dumps(
                {
                    "status": "ready",
                    "local_profile": {
                        "status": "ready",
                        "database_path": str(tmp_path / "private.sqlite3"),
                        "from_schema_version": 2,
                        "schema_version": 2,
                        "migrated": False,
                    },
                }
            )
        return ""

    monkeypatch.setattr(updates, "_run", fake_run)

    result = updates.apply_managed_update(
        updates.verify_release_manifest(signed_manifest),
        root=root,
    )

    assert result["status"] == "updated"
    assert result["to_version"] == "0.1.13"
    pointer = updates._read_json(updates.current_pointer_path(root))
    state = updates._read_json(updates.install_state_path(root))
    assert pointer["client_version"] == "0.1.13"
    assert state["current_client_version"] == "0.1.13"
    assert state["current_skill_version"] == "0.3.8"
    assert "version: 0.3.8" in skill_target.read_text(encoding="utf-8")


def test_failed_switch_restores_pointer_native_manifest_and_database(
    monkeypatch,
    tmp_path: Path,
    signed_manifest: dict[str, Any],
) -> None:
    root = _managed_root(tmp_path / "managed")
    skill_target = tmp_path / "skills" / "SKILL.md"
    skill_target.parent.mkdir(parents=True)
    skill_target.write_text("legacy skill", encoding="utf-8")
    native_manifest = tmp_path / "native-host.json"
    native_manifest.write_text("legacy native", encoding="utf-8")
    extension_root = tmp_path / "extension"
    pairing_state = updates.extension_pairing_state_path(extension_root)
    pairing_state.parent.mkdir(parents=True, exist_ok=True)
    pairing_state.write_text("legacy pairing", encoding="utf-8")
    database = tmp_path / "private.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE release_state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO release_state VALUES ('migrated')")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup = backup_dir / "private-schema-1.sqlite3"
    with sqlite3.connect(backup) as connection:
        connection.execute("CREATE TABLE release_state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO release_state VALUES ('legacy database')")
    old_pointer = updates._read_json(updates.current_pointer_path(root))
    monkeypatch.setattr(updates, "__version__", "0.1.12")
    monkeypatch.setattr(updates, "_skill_targets", lambda: [skill_target])
    monkeypatch.setattr(
        updates,
        "default_extension_root",
        lambda: extension_root,
    )
    monkeypatch.setattr(
        updates,
        "native_host_manifest_path",
        lambda: native_manifest,
    )

    def fake_download(asset, destination, *, opener):
        del opener
        if asset["role"] == "host_skill":
            destination.write_text(
                "---\nname: agentmesh-officialrecruitment\n"
                "version: 0.3.8\n---\n",
                encoding="utf-8",
            )
        else:
            destination.write_bytes(b"synthetic-wheel")
        return destination

    monkeypatch.setattr(updates, "_download_asset", fake_download)

    def fake_run(command, *, env=None, timeout=180):
        del env, timeout
        joined = " ".join(command)
        if "import official_recruitment_agent" in joined:
            return "0.1.13"
        if command[-1] == "upgrade-check":
            return json.dumps(
                {
                    "status": "ready",
                    "local_profile": {
                        "status": "ready",
                        "database_path": str(database),
                        "from_schema_version": 1,
                        "schema_version": 2,
                        "migrated": True,
                        "backup_path": str(backup),
                    },
                }
            )
        if command[-3:] == ["extension", "host", "install"]:
            native_manifest.write_text("new native", encoding="utf-8")
            pairing_state.write_text("new pairing", encoding="utf-8")
            raise updates.ClientUpdateError(
                "client_update_command_failed",
                "synthetic native host failure",
            )
        return ""

    monkeypatch.setattr(updates, "_run", fake_run)

    with pytest.raises(
        updates.ClientUpdateError,
        match="synthetic native host failure",
    ):
        updates.apply_managed_update(
            updates.verify_release_manifest(signed_manifest),
            root=root,
        )

    assert updates._read_json(updates.current_pointer_path(root)) == old_pointer
    assert skill_target.read_text(encoding="utf-8") == "legacy skill"
    assert native_manifest.read_text(encoding="utf-8") == "legacy native"
    assert pairing_state.read_text(encoding="utf-8") == "legacy pairing"
    with sqlite3.connect(database) as connection:
        restored = connection.execute(
            "SELECT value FROM release_state"
        ).fetchone()
    assert restored == ("legacy database",)
    assert not (root / "releases" / "0.1.13").exists()
