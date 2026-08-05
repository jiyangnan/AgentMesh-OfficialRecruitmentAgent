from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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

    assert summary["version"] == "0.6.4"
    assert summary["production"] is True
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        payload = b"\n".join(archive.read(name) for name in names)
    assert {
        "manifest.json",
        "popup.html",
        "popup.js",
        "protocol.js",
        "executor.js",
    }.issubset(names)
    assert manifest["manifest_version"] == 3
    assert manifest["permissions"] == [
        "activeTab",
        "scripting",
        "storage",
    ]
    assert manifest["host_permissions"] == [
        "http://127.0.0.1:8765/*",
        "https://agentmesh360.com/*",
        "https://*.agentmesh360.com/*",
    ]
    assert b"ORA_EXTENSION_SIGNING_SECRET" not in payload
    assert b"X-Service-Token" not in payload


def test_installer_uses_stable_assets_without_credentials() -> None:
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
        'official_recruitment_agent-0.1.2-py3-none-any.whl"'
        in installer
    )
    assert '"$VENV/bin/python" -m pip install' in installer
    assert "AGENTMESH_API_KEY=" not in installer
    assert "jobagent_live_" not in installer
