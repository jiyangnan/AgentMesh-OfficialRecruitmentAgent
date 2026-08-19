from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

from official_recruitment_agent.extension_identity import (
    OFFICIAL_CHROME_EXTENSION_ID,
)


ROOT = Path(__file__).resolve().parents[1]


def _extension_id(public_key: str) -> str:
    digest = hashlib.sha256(base64.b64decode(public_key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(value, 16)) for value in digest)


def _public_adapter_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def test_extension_package_is_installable_and_contains_no_secret(
    tmp_path: Path,
) -> None:
    output = tmp_path / "official-recruitment-extension.zip"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "package_extension.py"),
            "--source",
            str(ROOT / "extension"),
            "--output",
            str(output),
            "--production",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(completed.stdout)
    source_manifest = json.loads(
        (ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
    )

    assert summary["version"] == source_manifest["version"]
    assert summary["production"] is True
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        payload = b"\n".join(archive.read(name) for name in names)
        popup_html = archive.read("popup.html").decode("utf-8")
        popup_js = archive.read("popup.js").decode("utf-8")
        protocol_js = archive.read("protocol.js").decode("utf-8")
    assert {
        "manifest.json",
        "popup.html",
        "popup.js",
        "i18n.js",
        "protocol.js",
        "executor.js",
        "_locales/zh_CN/messages.json",
        "_locales/en/messages.json",
        "_locales/ja/messages.json",
        "_locales/ko/messages.json",
    }.issubset(names)
    assert manifest["manifest_version"] == 3
    assert manifest["permissions"] == [
        "activeTab",
        "nativeMessaging",
        "scripting",
        "storage",
    ]
    assert manifest["key"]
    assert manifest["name"] == "__MSG_extensionName__"
    assert manifest["default_locale"] == "en"
    assert _extension_id(manifest["key"]) == OFFICIAL_CHROME_EXTENSION_ID
    assert manifest["host_permissions"] == [
        "http://127.0.0.1:8765/*",
        "https://agentmesh360.com/*",
        "https://*.agentmesh360.com/*",
    ]
    assert "Connect local Agent" in popup_html
    assert 'id="api-key"' not in popup_html
    assert 'type="password"' not in popup_html
    assert "currentApiKey" not in popup_js
    assert "normalizeApiKey" not in popup_js
    assert 'id="profile-gap"' in popup_html
    assert 'id="complete-profile"' in popup_html
    assert "ora_pending_execution_evidence_v1" in popup_js
    assert "validateEvidenceAcknowledgement" in protocol_js
    assert "agentmesh-installation.json" not in names
    assert b"ORA_EXTENSION_SIGNING_SECRET" not in payload
    assert b"X-Service-Token" not in payload


def test_installer_uses_stable_assets_without_credentials() -> None:
    adapter_version = _public_adapter_version()
    installer = (
        ROOT / "installer" / "install-agent.sh"
    ).read_text(encoding="utf-8")

    assert "/downloads/official-recruitment-agent.whl" in installer
    assert (
        "/downloads/agentmesh-officialrecruitment-skill/SKILL.md"
        in installer
    )
    assert "ora-workbench configure --key" in installer
    assert "Skill 与 CLI 适配器已安装" in installer
    assert "Agent 已安装" not in installer
    assert (
        'WHEEL="$WORK_DIR/'
        'official_recruitment_agent-$ADAPTER_VERSION-py3-none-any.whl"'
        in installer
    )
    assert f'ADAPTER_VERSION="{adapter_version}"' in installer
    assert 'install-finalize' in installer
    assert 'RELEASE_ROOT="$INSTALL_ROOT/releases/$ADAPTER_VERSION"' in installer
    assert "__ORA_ADAPTER_SHA256__" not in installer
    assert "__ORA_SKILL_SHA256__" not in installer
    assert '"$VENV/bin/python" -m pip install' in installer
    assert "AGENTMESH_API_KEY=" not in installer
    assert "jobagent_live_" not in installer
    assert '版本：${ADAPTER_VERSION}；Skill：${SKILL_VERSION}' in installer


def test_windows_installer_uses_native_paths_and_valid_wheel_name() -> None:
    adapter_version = _public_adapter_version()
    installer = (
        ROOT / "installer" / "install-agent.ps1"
    ).read_text(encoding="utf-8")

    assert "$env:LOCALAPPDATA" in installer
    assert "AgentMesh360\\OfficialRecruitment" in installer
    assert "Scripts\\ora-workbench.exe" in installer
    assert "ora-workbench.cmd" in installer
    assert f'$AdapterVersion = "{adapter_version}"' in installer
    assert 'install-finalize' in installer
    assert '"releases\\$AdapterVersion"' in installer
    assert "__ORA_ADAPTER_SHA256__" not in installer
    assert "__ORA_SKILL_SHA256__" not in installer
    assert (
        'official_recruitment_agent-$AdapterVersion-py3-none-any.whl'
        in installer
    )
    assert "New-Item -ItemType SymbolicLink" not in installer
    assert "name: agentmesh-officialrecruitment" in installer
    assert "AGENTMESH_API_KEY=" not in installer
    assert "jobagent_live_" not in installer
