from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4
import zipfile


EXTENSION_RELEASE_PATH = (
    "/downloads/agentmesh-officialrecruitment-extension-release.json"
)
EXTENSION_STATE_SCHEMA_VERSION = 2
EXTENSION_PAIRING_SCHEMA_VERSION = 1
EXTENSION_PAIRING_FILE = "agentmesh-installation.json"
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 256
REQUIRED_EXTENSION_FILES = frozenset(
    {"manifest.json", "popup.html", "executor.js"}
)
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
INSTALLATION_ID_PATTERN = re.compile(r"^orainstall_[0-9a-f]{32}$")
PAIRING_SECRET_PATTERN = re.compile(r"^orapair_[A-Za-z0-9_-]{32,96}$")


class ExtensionDeliveryError(ValueError):
    pass


def default_extension_root(
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    override = environment.get("ORA_EXTENSION_DIR")
    if override:
        return Path(override).expanduser()

    current_platform = platform_name or sys.platform
    user_home = Path.home() if home is None else home
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
            / "extension"
        )
    if current_platform == "darwin":
        return (
            user_home
            / "Library"
            / "Application Support"
            / "AgentMesh360"
            / "OfficialRecruitment"
            / "extension"
        )

    xdg_data_home = environment.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else user_home / ".local" / "share"
    return base / "agentmesh360" / "official-recruitment" / "extension"


def extension_state_path(extension_root: Path) -> Path:
    return extension_root.parent / "extension-install.json"


def _new_extension_pairing() -> dict[str, Any]:
    return {
        "schema_version": EXTENSION_PAIRING_SCHEMA_VERSION,
        "installation_id": f"orainstall_{secrets.token_hex(16)}",
        "pairing_secret": f"orapair_{secrets.token_urlsafe(32)}",
        "local_agent_url": "http://127.0.0.1:8765",
    }


def _validate_extension_pairing(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExtensionDeliveryError("扩展本机配对资料无效。")
    expected_keys = {
        "schema_version",
        "installation_id",
        "pairing_secret",
        "local_agent_url",
    }
    if set(value) != expected_keys:
        raise ExtensionDeliveryError("扩展本机配对资料字段无效。")
    installation_id = value.get("installation_id")
    pairing_secret = value.get("pairing_secret")
    if (
        value.get("schema_version") != EXTENSION_PAIRING_SCHEMA_VERSION
        or not isinstance(installation_id, str)
        or not INSTALLATION_ID_PATTERN.fullmatch(installation_id)
        or not isinstance(pairing_secret, str)
        or not PAIRING_SECRET_PATTERN.fullmatch(pairing_secret)
        or value.get("local_agent_url") != "http://127.0.0.1:8765"
    ):
        raise ExtensionDeliveryError("扩展本机配对资料内容无效。")
    return dict(value)


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("json_not_object")
    return payload


def load_extension_pairing(extension_root: Path) -> dict[str, Any]:
    root = extension_root.expanduser()
    try:
        state = _read_json_object(extension_state_path(root))
        if state.get("schema_version") != EXTENSION_STATE_SCHEMA_VERSION:
            raise ValueError("state_schema")
        state_pairing = _validate_extension_pairing(state.get("pairing"))
        descriptor_pairing = _validate_extension_pairing(
            _read_json_object(root / EXTENSION_PAIRING_FILE)
        )
        if state_pairing != descriptor_pairing:
            raise ValueError("pairing_mismatch")
        return state_pairing
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        ExtensionDeliveryError,
    ) as exc:
        raise ExtensionDeliveryError(
            "扩展本机配对资料缺失或已损坏，请运行 extension repair。"
        ) from exc


def _existing_extension_pairing(extension_root: Path) -> dict[str, Any]:
    try:
        state = _read_json_object(extension_state_path(extension_root))
        return _validate_extension_pairing(state.get("pairing"))
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        ExtensionDeliveryError,
    ):
        try:
            return _validate_extension_pairing(
                _read_json_object(extension_root / EXTENSION_PAIRING_FILE)
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            ExtensionDeliveryError,
        ):
            return _new_extension_pairing()


def _write_pairing_descriptor(
    extension_root: Path,
    pairing: dict[str, Any],
) -> None:
    path = extension_root / EXTENSION_PAIRING_FILE
    path.write_text(
        json.dumps(pairing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        path.chmod(0o600)


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    parsed = urlparse(normalized)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and loopback
    ):
        raise ExtensionDeliveryError(
            "扩展只能从 AgentMesh360 HTTPS 地址或本机开发服务获取。"
        )
    if not parsed.netloc or parsed.username or parsed.password:
        raise ExtensionDeliveryError("扩展工作台地址无效。")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ExtensionDeliveryError("扩展工作台地址必须是站点根地址。")
    return normalized


def _read_url(
    url: str,
    *,
    opener: Callable[..., Any] = urlopen,
    accept: str,
    maximum_bytes: int,
) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "AgentMesh-OfficialRecruitment-ExtensionDelivery/1",
        },
    )
    with opener(request, timeout=20) as response:
        payload = response.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        raise ExtensionDeliveryError("扩展下载内容超过允许大小。")
    return payload


def fetch_extension_release(
    base_url: str,
    *,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    normalized = _validate_base_url(base_url)
    manifest_url = normalized + EXTENSION_RELEASE_PATH
    payload = _read_url(
        manifest_url,
        opener=opener,
        accept="application/json",
        maximum_bytes=64 * 1024,
    )
    try:
        release = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtensionDeliveryError("扩展版本清单不是有效 JSON。") from exc
    if not isinstance(release, dict):
        raise ExtensionDeliveryError("扩展版本清单必须是 JSON 对象。")

    required = {
        "schema_version",
        "product",
        "extension_version",
        "artifact_path",
        "artifact_sha256",
        "artifact_bytes",
    }
    if not required.issubset(release):
        raise ExtensionDeliveryError("扩展版本清单缺少必要字段。")
    if release["schema_version"] != 1:
        raise ExtensionDeliveryError("扩展版本清单版本不受支持。")
    if release["product"] != "officialrecruitment":
        raise ExtensionDeliveryError("扩展版本清单产品不匹配。")
    if not (
        isinstance(release["extension_version"], str)
        and VERSION_PATTERN.fullmatch(release["extension_version"])
    ):
        raise ExtensionDeliveryError("扩展版本号无效。")
    if not _valid_sha256(release["artifact_sha256"]):
        raise ExtensionDeliveryError("扩展摘要无效。")
    if not (
        isinstance(release["artifact_bytes"], int)
        and 0 < release["artifact_bytes"] <= MAX_ARCHIVE_BYTES
    ):
        raise ExtensionDeliveryError("扩展大小无效。")

    artifact_path = release["artifact_path"]
    if not isinstance(artifact_path, str):
        raise ExtensionDeliveryError("扩展下载路径无效。")
    parsed_path = urlparse(artifact_path)
    path = PurePosixPath(parsed_path.path)
    if (
        parsed_path.scheme
        or parsed_path.netloc
        or parsed_path.query
        or parsed_path.fragment
        or not parsed_path.path.startswith("/downloads/")
        or ".." in path.parts
    ):
        raise ExtensionDeliveryError("扩展下载路径越过官方目录。")

    artifact_url = urljoin(normalized + "/", artifact_path)
    if urlparse(artifact_url).netloc != urlparse(normalized).netloc:
        raise ExtensionDeliveryError("扩展下载地址越过当前工作台。")
    return {**release, "manifest_url": manifest_url, "artifact_url": artifact_url}


def _archive_payload(
    release: dict[str, Any],
    *,
    opener: Callable[..., Any],
) -> bytes:
    payload = _read_url(
        release["artifact_url"],
        opener=opener,
        accept="application/zip",
        maximum_bytes=MAX_ARCHIVE_BYTES,
    )
    if len(payload) != release["artifact_bytes"]:
        raise ExtensionDeliveryError("扩展大小与官方版本清单不一致。")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != release["artifact_sha256"]:
        raise ExtensionDeliveryError("扩展 SHA-256 与官方版本清单不一致。")
    return payload


def _safe_extract(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if not members:
            raise ExtensionDeliveryError("扩展 ZIP 为空。")
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ExtensionDeliveryError("扩展 ZIP 文件数量超过允许上限。")
        if sum(member.file_size for member in members) > MAX_EXTRACTED_BYTES:
            raise ExtensionDeliveryError("扩展 ZIP 解压后超过允许大小。")
        seen_paths: set[str] = set()
        for member in members:
            raw_name = member.filename
            relative = PurePosixPath(raw_name)
            mode = member.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            normalized_name = relative.as_posix().rstrip("/").casefold()
            if (
                not raw_name
                or "\\" in raw_name
                or relative.is_absolute()
                or ".." in relative.parts
                or not normalized_name
                or normalized_name in seen_paths
                or member.flag_bits & 0x1
                or stat.S_ISLNK(mode)
                or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}
            ):
                raise ExtensionDeliveryError("扩展 ZIP 包含不安全路径。")
            seen_paths.add(normalized_name)
            target = destination.joinpath(*relative.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _file_inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _read_extension_version(root: Path) -> str:
    try:
        manifest = json.loads(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtensionDeliveryError("扩展 manifest.json 无法读取。") from exc
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ExtensionDeliveryError("扩展 manifest.json 版本无效。")
    return version


def _write_state(extension_root: Path, state: dict[str, Any]) -> None:
    path = extension_state_path(extension_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
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


def extension_status(extension_root: Path) -> dict[str, Any]:
    root = extension_root.expanduser()
    state_path = extension_state_path(root)
    base = {
        "platform": _platform_label(),
        "install_directory": str(root),
        "state_file": str(state_path),
    }
    if not root.is_dir() or not state_path.is_file():
        return {**base, "status": "not_installed", "healthy": False}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("state_not_object")
        if state.get("schema_version") != EXTENSION_STATE_SCHEMA_VERSION:
            raise ValueError("state_schema")
        expected_files = state.get("files")
        if not isinstance(expected_files, list):
            raise ValueError("state_files")
        version = _read_extension_version(root)
        pairing = load_extension_pairing(root)
        actual_files = _file_inventory(root)
        if actual_files != expected_files:
            raise ValueError("file_inventory")
        if version != state.get("extension_version"):
            raise ValueError("version_mismatch")
        if state.get("pairing") != pairing:
            raise ValueError("pairing_mismatch")
    except (OSError, ValueError, json.JSONDecodeError, ExtensionDeliveryError):
        return {**base, "status": "repair_required", "healthy": False}
    return {
        **base,
        "status": "ready",
        "healthy": True,
        "extension_version": version,
        "artifact_sha256": state.get("artifact_sha256"),
    }


def prepare_extension(
    base_url: str,
    *,
    extension_root: Path | None = None,
    force: bool = False,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    root = (extension_root or default_extension_root()).expanduser()
    release = fetch_extension_release(base_url, opener=opener)
    current = extension_status(root)
    pairing = _existing_extension_pairing(root)
    if (
        current.get("healthy") is True
        and _version_key(str(current["extension_version"]))
        > _version_key(release["extension_version"])
    ):
        return {
            **current,
            "changed": False,
            "latest_version": release["extension_version"],
            "downgrade_blocked": True,
        }
    if (
        not force
        and current.get("healthy") is True
        and current.get("artifact_sha256") == release["artifact_sha256"]
    ):
        return {
            **current,
            "changed": False,
            "latest_version": release["extension_version"],
        }

    payload = _archive_payload(release, opener=opener)
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{root.name}.staging-",
            dir=root.parent,
        )
    )
    backup = root.with_name(f".{root.name}.backup-{uuid4().hex}")
    archive_path = staging.parent / f".{root.name}.{uuid4().hex}.zip"
    installed_new_root = False
    try:
        archive_path.write_bytes(payload)
        _safe_extract(archive_path, staging)
        present = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file()
        }
        if not REQUIRED_EXTENSION_FILES.issubset(present):
            raise ExtensionDeliveryError("扩展 ZIP 缺少必要文件。")
        version = _read_extension_version(staging)
        if version != release["extension_version"]:
            raise ExtensionDeliveryError("扩展版本与官方版本清单不一致。")
        _write_pairing_descriptor(staging, pairing)
        inventory = _file_inventory(staging)

        if root.exists():
            os.replace(root, backup)
        os.replace(staging, root)
        installed_new_root = True
        _write_state(
            root,
            {
                "schema_version": EXTENSION_STATE_SCHEMA_VERSION,
                "extension_version": version,
                "artifact_sha256": release["artifact_sha256"],
                "artifact_bytes": release["artifact_bytes"],
                "source_manifest_url": release["manifest_url"],
                "pairing": pairing,
                "files": inventory,
            },
        )
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if installed_new_root and root.exists():
            shutil.rmtree(root)
        if backup.exists():
            os.replace(backup, root)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if archive_path.exists():
            archive_path.unlink()
        if backup.exists():
            shutil.rmtree(backup)

    return {
        **extension_status(root),
        "changed": True,
        "latest_version": release["extension_version"],
    }


def open_extension_setup(
    extension_root: Path,
    *,
    platform_name: str | None = None,
    environ: dict[str, str] | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
) -> list[dict[str, Any]]:
    current_platform = platform_name or sys.platform
    environment = os.environ if environ is None else environ
    commands: list[tuple[str, list[str] | None]] = []
    if current_platform == "darwin":
        commands = [
            ("extension_directory", ["open", str(extension_root)]),
            (
                "chrome_extensions",
                ["open", "-a", "Google Chrome", "chrome://extensions/"],
            ),
        ]
    elif current_platform == "win32":
        commands = [
            ("extension_directory", ["explorer.exe", str(extension_root)]),
            ("chrome_extensions", _windows_chrome_command(environment)),
        ]
    else:
        commands = [
            ("extension_directory", _linux_open_command(extension_root)),
            ("chrome_extensions", _linux_chrome_command()),
        ]

    results = []
    for name, command in commands:
        if command is None:
            results.append({"name": name, "opened": False})
            continue
        try:
            popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            results.append({"name": name, "opened": False})
        else:
            results.append({"name": name, "opened": True})
    return results


def _windows_chrome_command(
    environ: dict[str, str],
) -> list[str] | None:
    candidates = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        value = environ.get(variable)
        if value:
            candidates.append(
                Path(value) / "Google" / "Chrome" / "Application" / "chrome.exe"
            )
    executable = next((path for path in candidates if path.is_file()), None)
    return [str(executable), "chrome://extensions/"] if executable else None


def _linux_open_command(extension_root: Path) -> list[str] | None:
    executable = shutil.which("xdg-open")
    return [executable, str(extension_root)] if executable else None


def _linux_chrome_command() -> list[str] | None:
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        executable = shutil.which(name)
        if executable:
            return [executable, "chrome://extensions/"]
    return None


def _platform_label(platform_name: str | None = None) -> str:
    current_platform = platform_name or sys.platform
    if current_platform == "win32":
        return "windows"
    if current_platform == "darwin":
        return "macos"
    return "linux"


def _version_key(version: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in version.split("."))
