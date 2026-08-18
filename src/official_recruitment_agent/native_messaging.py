from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from typing import Any, BinaryIO, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from official_recruitment_agent.extension_delivery import (
    default_extension_root,
    ensure_extension_pairing,
    load_private_extension_pairing,
)
from official_recruitment_agent.extension_identity import (
    NATIVE_MESSAGING_HOST_NAME,
    OFFICIAL_CHROME_EXTENSION_ID,
    OFFICIAL_CHROME_EXTENSION_ORIGIN,
)
from official_recruitment_agent.local_profile_handoff import (
    LOCAL_HANDOFF_URL,
)


MAX_NATIVE_MESSAGE_BYTES = 64 * 1024


class NativeMessagingError(RuntimeError):
    pass


def native_host_executable() -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    script_candidate = Path(sys.argv[0]).resolve().with_name(
        f"ora-native-host{suffix}"
    )
    if script_candidate.is_file():
        return script_candidate
    return Path(sys.executable).parent / f"ora-native-host{suffix}"


def native_host_manifest_path(
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Path:
    current_platform = platform_name or sys.platform
    environment = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    filename = f"{NATIVE_MESSAGING_HOST_NAME}.json"
    if current_platform == "win32":
        local_app_data = environment.get("LOCALAPPDATA")
        base = (
            Path(local_app_data)
            if local_app_data
            else user_home / "AppData" / "Local"
        )
        return (
            base
            / "AgentMesh360"
            / "OfficialRecruitment"
            / filename
        )
    if current_platform == "darwin":
        return (
            user_home
            / "Library"
            / "Application Support"
            / "Google"
            / "Chrome"
            / "NativeMessagingHosts"
            / filename
        )
    return (
        user_home
        / ".config"
        / "google-chrome"
        / "NativeMessagingHosts"
        / filename
    )


def _host_manifest(executable: Path) -> dict[str, Any]:
    return {
        "name": NATIVE_MESSAGING_HOST_NAME,
        "description": "AgentMesh-OfficialRecruitment local Agent bridge",
        "path": str(executable.resolve()),
        "type": "stdio",
        "allowed_origins": [f"{OFFICIAL_CHROME_EXTENSION_ORIGIN}/"],
    }


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def install_native_messaging_host(
    *,
    executable: Path | None = None,
    extension_root: Path | None = None,
    platform_name: str | None = None,
    home: Path | None = None,
    environ: dict[str, str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    current_platform = platform_name or sys.platform
    host_executable = (executable or native_host_executable()).expanduser()
    if not host_executable.is_absolute() or not host_executable.is_file():
        raise NativeMessagingError(
            "没有找到浏览器本机连接组件，请重新安装 CLI 适配器。"
        )
    root = extension_root or default_extension_root(
        platform_name=current_platform,
        home=home,
        environ=environ,
    )
    pairing = ensure_extension_pairing(root)
    manifest_path = native_host_manifest_path(
        platform_name=current_platform,
        home=home,
        environ=environ,
    )
    manifest = _host_manifest(host_executable)
    _write_private_json(manifest_path, manifest)

    if current_platform == "win32":
        registry_key = (
            "HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\"
            f"{NATIVE_MESSAGING_HOST_NAME}"
        )
        completed = runner(
            [
                "reg.exe",
                "ADD",
                registry_key,
                "/ve",
                "/t",
                "REG_SZ",
                "/d",
                str(manifest_path),
                "/f",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise NativeMessagingError(
                "Windows 无法注册浏览器本机连接组件。"
            )

    return {
        "status": "ready",
        "host_name": NATIVE_MESSAGING_HOST_NAME,
        "extension_id": OFFICIAL_CHROME_EXTENSION_ID,
        "manifest_path": str(manifest_path),
        "executable_path": str(host_executable),
        "installation_id": pairing["installation_id"],
    }


def native_messaging_host_status(
    *,
    executable: Path | None = None,
    platform_name: str | None = None,
    home: Path | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    host_executable = (executable or native_host_executable()).expanduser()
    manifest_path = native_host_manifest_path(
        platform_name=platform_name,
        home=home,
        environ=environ,
    )
    expected = _host_manifest(host_executable)
    try:
        actual = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        actual = None
    ready = (
        host_executable.is_file()
        and isinstance(actual, dict)
        and actual == expected
    )
    return {
        "status": "ready" if ready else "repair_required",
        "ready": ready,
        "host_name": NATIVE_MESSAGING_HOST_NAME,
        "extension_id": OFFICIAL_CHROME_EXTENSION_ID,
        "manifest_path": str(manifest_path),
        "executable_path": str(host_executable),
    }


def _read_message(stream: BinaryIO) -> dict[str, Any]:
    raw_length = stream.read(4)
    if len(raw_length) != 4:
        raise NativeMessagingError("浏览器没有发送完整的连接请求。")
    length = struct.unpack("<I", raw_length)[0]
    if length <= 0 or length > MAX_NATIVE_MESSAGE_BYTES:
        raise NativeMessagingError("浏览器连接请求大小无效。")
    payload = stream.read(length)
    if len(payload) != length:
        raise NativeMessagingError("浏览器连接请求不完整。")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeMessagingError("浏览器连接请求格式无效。") from exc
    if not isinstance(value, dict):
        raise NativeMessagingError("浏览器连接请求必须是结构化对象。")
    return value


def _write_message(stream: BinaryIO, value: dict[str, Any]) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_NATIVE_MESSAGE_BYTES:
        raise NativeMessagingError("本机 Agent 返回内容超过允许大小。")
    stream.write(struct.pack("<I", len(payload)))
    stream.write(payload)
    stream.flush()


def _cli_executable() -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    return Path(sys.executable).parent / f"ora-workbench{suffix}"


def _start_local_agent(
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    cli = _cli_executable()
    if not cli.is_file():
        raise NativeMessagingError(
            "没有找到本机 Agent 适配器，请重新运行官网安装指令。"
        )
    try:
        completed = runner(
            [str(cli), "profile-handoff", "start"],
            capture_output=True,
            text=True,
            timeout=35,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeMessagingError(
            "本机 Agent 暂时无法启动，请在 Agent 中运行连接诊断。"
        ) from exc
    if completed.returncode == 0:
        return
    message = "本机 Agent 尚未就绪，请先在 Agent 中配置 AgentMesh360 API Key。"
    try:
        error = json.loads(completed.stdout)
        candidate = error.get("error", {}).get("message")
        if isinstance(candidate, str) and candidate.strip():
            message = candidate.strip()
    except (AttributeError, json.JSONDecodeError, TypeError):
        pass
    raise NativeMessagingError(message)


def _connect_local_agent(
    origin: str,
    *,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    pairing = load_private_extension_pairing(default_extension_root())
    request = Request(
        f"{LOCAL_HANDOFF_URL}/v1/extension/connect",
        data=json.dumps(
            {
                "installation_id": pairing["installation_id"],
                "pairing_secret": pairing["pairing_secret"],
            }
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": origin,
        },
        method="POST",
    )
    try:
        with opener(request, timeout=5) as response:
            result = json.loads(response.read())
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read())
            message = payload.get("error", {}).get("message")
        except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
            message = None
        raise NativeMessagingError(
            message or "本机 Agent 拒绝了浏览器连接。"
        ) from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise NativeMessagingError(
            "本机 Agent 已启动，但浏览器连接失败，请运行连接诊断。"
        ) from exc
    if not isinstance(result, dict) or result.get("status") != "connected":
        raise NativeMessagingError("本机 Agent 返回了无效的浏览器连接。")
    return result


def handle_native_message(
    message: dict[str, Any],
    *,
    origin: str,
    runner: Callable[..., Any] = subprocess.run,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    normalized_origin = origin.rstrip("/")
    if normalized_origin != OFFICIAL_CHROME_EXTENSION_ORIGIN:
        raise NativeMessagingError("本机 Agent 拒绝了非官方浏览器扩展。")
    if message != {
        "contract_version": "officialrecruitment-native-v1",
        "action": "connect",
    }:
        raise NativeMessagingError("浏览器请求的本机动作不受支持。")
    _start_local_agent(runner=runner)
    return _connect_local_agent(normalized_origin, opener=opener)


def main() -> int:
    origin = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        message = _read_message(sys.stdin.buffer)
        response = handle_native_message(message, origin=origin)
    except NativeMessagingError as exc:
        response = {
            "status": "error",
            "error": {
                "code": "native_agent_connection_failed",
                "message": str(exc),
            },
        }
    _write_message(sys.stdout.buffer, response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
