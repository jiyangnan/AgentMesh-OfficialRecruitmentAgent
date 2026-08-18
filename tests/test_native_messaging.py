from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import struct
import sys

import pytest

import official_recruitment_agent.native_messaging as native_module
from official_recruitment_agent.extension_identity import (
    NATIVE_MESSAGING_HOST_NAME,
    OFFICIAL_CHROME_EXTENSION_ID,
    OFFICIAL_CHROME_EXTENSION_ORIGIN,
)
from official_recruitment_agent.native_messaging import (
    NativeMessagingError,
    _read_message,
    _write_message,
    handle_native_message,
    install_native_messaging_host,
    native_host_manifest_path,
)


class _Completed:
    returncode = 0


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_native_host_manifest_paths_cover_chrome_on_supported_platforms(
    tmp_path: Path,
) -> None:
    assert native_host_manifest_path(
        platform_name="darwin",
        home=tmp_path,
        environ={},
    ) == (
        tmp_path
        / "Library"
        / "Application Support"
        / "Google"
        / "Chrome"
        / "NativeMessagingHosts"
        / f"{NATIVE_MESSAGING_HOST_NAME}.json"
    )
    assert native_host_manifest_path(
        platform_name="linux",
        home=tmp_path,
        environ={},
    ) == (
        tmp_path
        / ".config"
        / "google-chrome"
        / "NativeMessagingHosts"
        / f"{NATIVE_MESSAGING_HOST_NAME}.json"
    )
    assert native_host_manifest_path(
        platform_name="win32",
        home=tmp_path,
        environ={"LOCALAPPDATA": str(tmp_path / "Local")},
    ) == (
        tmp_path
        / "Local"
        / "AgentMesh360"
        / "OfficialRecruitment"
        / f"{NATIVE_MESSAGING_HOST_NAME}.json"
    )


def test_install_native_host_allows_only_the_official_extension(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "bin" / "ora-native-host"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    result = install_native_messaging_host(
        executable=executable,
        extension_root=tmp_path / "extension",
        platform_name="darwin",
        home=tmp_path,
        environ={},
    )

    manifest = json.loads(
        Path(result["manifest_path"]).read_text(encoding="utf-8")
    )
    assert result["extension_id"] == OFFICIAL_CHROME_EXTENSION_ID
    assert manifest == {
        "name": NATIVE_MESSAGING_HOST_NAME,
        "description": (
            "AgentMesh-OfficialRecruitment local Agent bridge"
        ),
        "path": str(executable.resolve()),
        "type": "stdio",
        "allowed_origins": [f"{OFFICIAL_CHROME_EXTENSION_ORIGIN}/"],
    }
    assert result["installation_id"].startswith("orainstall_")


def test_windows_install_registers_native_host_for_current_user(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "ora-native-host.exe"
    executable.write_bytes(b"test")
    calls = []

    result = install_native_messaging_host(
        executable=executable,
        extension_root=tmp_path / "extension",
        platform_name="win32",
        home=tmp_path,
        environ={"LOCALAPPDATA": str(tmp_path / "Local")},
        runner=lambda *args, **kwargs: (
            calls.append((args, kwargs)) or _Completed()
        ),
    )

    command = calls[0][0][0]
    assert command[:2] == ["reg.exe", "ADD"]
    assert command[2] == (
        "HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\"
        f"{NATIVE_MESSAGING_HOST_NAME}"
    )
    assert command[-2:] == [str(Path(result["manifest_path"])), "/f"]


def test_native_message_frame_round_trip() -> None:
    stream = BytesIO()
    payload = {
        "contract_version": "officialrecruitment-native-v1",
        "action": "connect",
    }

    _write_message(stream, payload)
    stream.seek(0)

    assert struct.unpack("<I", stream.read(4))[0] > 0
    stream.seek(0)
    assert _read_message(stream) == payload


def test_native_bridge_rejects_non_official_extension() -> None:
    with pytest.raises(NativeMessagingError, match="非官方"):
        handle_native_message(
            {
                "contract_version": "officialrecruitment-native-v1",
                "action": "connect",
            },
            origin=f"chrome-extension://{'a' * 32}",
        )


def test_native_bridge_connects_without_returning_pairing_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = tmp_path / "ora-workbench"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    pairing = {
        "schema_version": 1,
        "installation_id": f"orainstall_{'b' * 32}",
        "pairing_secret": f"orapair_{'c' * 43}",
        "local_agent_url": "http://127.0.0.1:8765",
    }
    local_requests = []

    monkeypatch.setattr(native_module, "_cli_executable", lambda: cli)
    monkeypatch.setattr(
        native_module,
        "default_extension_root",
        lambda: tmp_path / "extension",
    )
    monkeypatch.setattr(
        native_module,
        "load_private_extension_pairing",
        lambda _root: pairing,
    )

    def opener(request, timeout):
        local_requests.append((request, timeout))
        return _Response(
            {
                "status": "connected",
                "installation_id": pairing["installation_id"],
                "session_token": f"oralocalsession_{'d' * 43}",
                "server_url": "https://recruit.agentmesh360.com",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        )

    result = handle_native_message(
        {
            "contract_version": "officialrecruitment-native-v1",
            "action": "connect",
        },
        origin=f"{OFFICIAL_CHROME_EXTENSION_ORIGIN}/",
        runner=lambda *args, **kwargs: _Completed(),
        opener=opener,
    )

    assert result["status"] == "connected"
    assert "pairing_secret" not in result
    request, timeout = local_requests[0]
    assert timeout == 5
    assert request.headers["Origin"] == OFFICIAL_CHROME_EXTENSION_ORIGIN
    assert json.loads(request.data) == {
        "installation_id": pairing["installation_id"],
        "pairing_secret": pairing["pairing_secret"],
    }


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX virtual environments expose the Python launcher by symlink",
)
def test_native_bridge_locates_cli_beside_virtualenv_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_python = tmp_path / "python-base" / "python"
    base_python.parent.mkdir()
    base_python.write_text("", encoding="utf-8")
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    venv_python = venv_bin / "python"
    venv_python.symlink_to(base_python)
    cli = venv_bin / "ora-workbench"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(native_module.sys, "executable", str(venv_python))

    assert native_module._cli_executable() == cli
