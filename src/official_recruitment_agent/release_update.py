from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)

from official_recruitment_agent import __version__
from official_recruitment_agent.extension_delivery import (
    default_extension_root,
    extension_pairing_state_path,
    extension_status,
    prepare_extension,
)
from official_recruitment_agent.local_profile_handoff import (
    LocalProfileStore,
    default_local_profile_path,
    open_without_redirect,
)
from official_recruitment_agent.local_profile_migrations import (
    restore_local_profile_database,
)
from official_recruitment_agent.native_messaging import (
    install_native_messaging_host,
    native_host_manifest_path,
)


PRODUCT = "officialrecruitment"
CHANNEL = "stable"
PROTOCOL_VERSION = "1.0"
DEFAULT_CORE_API_BASE = "https://api.agentmesh360.com"
DEFAULT_PRODUCT_BASE = "https://recruit.agentmesh360.com"
RELEASE_ENDPOINT = "/v1/products/officialrecruitment/client-release"
RELEASE_SIGNING_KEY_ID = "officialrecruitment-release-2026-01"
# The manifest binds product, channel and every asset. This transitional public
# key shares the established AgentMesh360 release trust root; the key id remains
# product-specific so it can rotate independently later.
RELEASE_SIGNING_PUBLIC_KEY = "08rY8C6SMBqyCD4rZGiSyLJsmrzLd_l-BolAyVe20Ww"
CACHE_TTL_SECONDS = 5 * 60
MAX_MANIFEST_BYTES = 128 * 1024
MAX_CLIENT_ASSET_BYTES = 50 * 1024 * 1024
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
UPDATE_RESUME_ENV = "ORA_CLIENT_UPDATE_RESUME"
SKIP_UPDATE_ENV = "ORA_SKIP_UPDATE"

ALLOWED_ASSET_HOSTS = frozenset(
    {
        "github.com",
        "recruit.agentmesh360.com",
    }
)
REQUIRED_ASSET_ROLES = frozenset({"adapter_wheel", "host_skill"})


class ClientUpdateError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        recovery: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.recovery = recovery or {}


def _version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise ClientUpdateError(
            "invalid_client_version",
            f"无效的客户端版本：{value}",
        )
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_base64url(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(
            (value + "=" * (-len(value) % 4)).encode("ascii")
        )
    except Exception as error:
        raise ClientUpdateError(
            "release_signature_invalid",
            "正式版本清单的签名编码无效。",
        ) from error


def _valid_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value)
    )


def _validate_asset(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ClientUpdateError(
            "release_asset_invalid",
            "正式版本清单包含无效资产。",
        )
    role = value.get("role")
    version = value.get("version")
    url = value.get("url")
    digest = value.get("sha256")
    size = value.get("bytes")
    if (
        role not in {"adapter_wheel", "host_skill", "extension_zip"}
        or not isinstance(version, str)
        or not VERSION_PATTERN.fullmatch(version)
        or not isinstance(url, str)
        or not _valid_sha256(digest)
        or not isinstance(size, int)
        or size <= 0
        or size > MAX_CLIENT_ASSET_BYTES
    ):
        raise ClientUpdateError(
            "release_asset_invalid",
            "正式版本清单的资产字段无效。",
        )
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_ASSET_HOSTS
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or ".." in Path(unquote(parsed.path)).parts
    ):
        raise ClientUpdateError(
            "release_asset_origin_invalid",
            "正式版本资产不在允许的官方地址。",
        )
    if parsed.hostname == "github.com" and not parsed.path.startswith(
        "/jiyangnan/AgentMesh-OfficialRecruitmentAgent/releases/download/"
    ):
        raise ClientUpdateError(
            "release_asset_origin_invalid",
            "GitHub 资产不属于 AgentMesh-OfficialRecruitment 正式 Release。",
        )
    if parsed.hostname == "recruit.agentmesh360.com" and not parsed.path.startswith(
        "/downloads/"
    ):
        raise ClientUpdateError(
            "release_asset_origin_invalid",
            "产品站资产不在官方下载目录。",
        )
    suffixes = {
        "adapter_wheel": ".whl",
        "host_skill": "/SKILL.md",
        "extension_zip": ".zip",
    }
    if not parsed.path.endswith(suffixes[str(role)]):
        raise ClientUpdateError(
            "release_asset_type_invalid",
            "正式版本资产类型与声明用途不一致。",
        )
    return dict(value)


def verify_release_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    signed = dict(manifest)
    signature = signed.pop("signature", None)
    if not isinstance(signature, str) or not signature:
        raise ClientUpdateError(
            "release_signature_missing",
            "正式版本清单缺少签名。",
        )
    if (
        signed.get("signature_algorithm") != "Ed25519"
        or signed.get("key_id") != RELEASE_SIGNING_KEY_ID
    ):
        raise ClientUpdateError(
            "release_signature_policy_mismatch",
            "正式版本清单的签名策略不匹配。",
        )
    try:
        Ed25519PublicKey.from_public_bytes(
            _decode_base64url(RELEASE_SIGNING_PUBLIC_KEY)
        ).verify(
            _decode_base64url(signature),
            _canonical_json_bytes(signed),
        )
    except (InvalidSignature, ValueError) as error:
        raise ClientUpdateError(
            "release_signature_invalid",
            "正式版本清单签名验证失败。",
        ) from error
    if (
        signed.get("product") != PRODUCT
        or signed.get("channel") != CHANNEL
        or signed.get("protocol_version") != PROTOCOL_VERSION
    ):
        raise ClientUpdateError(
            "release_product_mismatch",
            "正式版本清单的产品、渠道或协议不匹配。",
        )
    latest = str(signed.get("latest_client_version") or "")
    minimum = str(signed.get("minimum_supported_version") or "")
    _version(latest)
    _version(minimum)
    if _version(minimum) > _version(latest):
        raise ClientUpdateError(
            "release_version_policy_invalid",
            "正式版本清单的最低版本高于最新版本。",
        )
    commit = signed.get("git_commit")
    tag = signed.get("git_tag")
    if (
        not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or not isinstance(tag, str)
        or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag)
    ):
        raise ClientUpdateError(
            "release_source_invalid",
            "正式版本清单没有固定到有效提交。",
        )
    assets_value = signed.get("assets")
    if not isinstance(assets_value, list) or not assets_value:
        raise ClientUpdateError(
            "release_assets_missing",
            "正式版本清单缺少客户端资产。",
        )
    assets = [_validate_asset(item) for item in assets_value]
    roles = [str(item["role"]) for item in assets]
    if len(roles) != len(set(roles)) or not REQUIRED_ASSET_ROLES.issubset(roles):
        raise ClientUpdateError(
            "release_assets_invalid",
            "正式版本清单缺少唯一的 Wheel 或 Skill。",
        )
    wheel = next(item for item in assets if item["role"] == "adapter_wheel")
    skill = next(item for item in assets if item["role"] == "host_skill")
    if (
        wheel["version"] != latest
        or signed.get("artifact_sha256") != wheel["sha256"]
    ):
        raise ClientUpdateError(
            "release_wheel_mismatch",
            "正式版本清单的客户端版本与 Wheel 不一致。",
        )
    if not isinstance(skill.get("skill_version"), str) or not VERSION_PATTERN.fullmatch(
        str(skill.get("skill_version"))
    ):
        raise ClientUpdateError(
            "release_skill_version_invalid",
            "正式版本清单缺少有效 Skill 版本。",
        )
    if skill["version"] != skill["skill_version"]:
        raise ClientUpdateError(
            "release_skill_version_mismatch",
            "正式版本清单的 Skill 版本字段不一致。",
        )
    return {**signed, "assets": assets}


def default_install_root(
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    override = environment.get("ORA_AGENT_HOME")
    if override:
        return Path(override).expanduser()
    user_home = Path.home() if home is None else home
    if (platform_name or sys.platform) == "win32":
        base = Path(
            environment.get("LOCALAPPDATA")
            or user_home / "AppData" / "Local"
        )
        return base / "AgentMesh360" / "OfficialRecruitment"
    return user_home / ".agentmesh360" / "official-recruitment"


def install_state_path(root: Path) -> Path:
    return root / "install-state.json"


def current_pointer_path(root: Path) -> Path:
    return root / "current.json"


def release_cache_path(root: Path) -> Path:
    return root / "update" / "release-manifest-cache.json"


def update_lock_path(root: Path) -> Path:
    return root / "update" / "update.lock"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
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
        temporary.unlink(missing_ok=True)


def _managed_state(root: Path) -> dict[str, Any] | None:
    state = _read_json(install_state_path(root))
    if (
        state
        and state.get("schema_version") == 1
        and state.get("managed") is True
        and state.get("product") == PRODUCT
        and state.get("install_type") == "official-installer"
    ):
        return state
    return None


def is_managed_runtime(root: Path | None = None) -> bool:
    install_root = root or default_install_root()
    if _managed_state(install_root) is None:
        return False
    if os.environ.get("ORA_TEST_MANAGED_RUNTIME") == "1":
        return True
    executable = Path(sys.executable).resolve()
    return install_root.resolve() in executable.parents


def _core_base() -> str:
    return os.environ.get(
        "ORA_CORE_API_BASE",
        DEFAULT_CORE_API_BASE,
    ).rstrip("/")


def fetch_release_manifest(
    *,
    root: Path | None = None,
    force: bool = False,
    opener: Callable[..., Any] = open_without_redirect,
) -> dict[str, Any] | None:
    install_root = root or default_install_root()
    cache_path = release_cache_path(install_root)
    cache = _read_json(cache_path) or {}
    cached = cache.get("manifest")
    cached_manifest = cached if isinstance(cached, dict) else None
    if cached_manifest is not None:
        cached_manifest = verify_release_manifest(cached_manifest)
    if (
        not force
        and cached_manifest is not None
        and time.time() - _cache_timestamp(cache) < CACHE_TTL_SECONDS
    ):
        return cached_manifest
    request = Request(
        _core_base() + RELEASE_ENDPOINT,
        headers={
            "Accept": "application/json",
            "User-Agent": f"official-recruitment-agent/{__version__}",
        },
    )
    try:
        with opener(request, timeout=10) as response:
            payload = response.read(MAX_MANIFEST_BYTES + 1)
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ClientUpdateError(
                "release_manifest_too_large",
                "正式版本清单超过允许大小。",
            )
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise ValueError("manifest_not_object")
        manifest = verify_release_manifest(raw)
    except (OSError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return cached_manifest
    _write_private_json(
        cache_path,
        {"fetched_at": time.time(), "manifest": raw},
    )
    return manifest


def _cache_timestamp(cache: dict[str, Any]) -> float:
    try:
        return float(cache.get("fetched_at", 0))
    except (TypeError, ValueError):
        return 0.0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _update_lock(root: Path):
    path = update_lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    while True:
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            break
        except FileExistsError as error:
            try:
                pid = int(path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                pid = 0
            if _pid_alive(pid):
                raise ClientUpdateError(
                    "client_update_in_progress",
                    "另一项客户端更新正在进行，请稍后重试。",
                ) from error
            path.unlink(missing_ok=True)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


def _download_asset(
    asset: dict[str, Any],
    destination: Path,
    *,
    opener: Callable[..., Any],
) -> Path:
    request = Request(
        str(asset["url"]),
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": f"official-recruitment-agent/{__version__}",
        },
    )
    digest = hashlib.sha256()
    size = 0
    with opener(request, timeout=30) as response, destination.open("wb") as output:
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > int(asset["bytes"]) or size > MAX_CLIENT_ASSET_BYTES:
                raise ClientUpdateError(
                    "release_asset_size_mismatch",
                    f"正式版本资产 {asset['role']} 大小不一致。",
                )
            digest.update(chunk)
            output.write(chunk)
    if size != int(asset["bytes"]) or digest.hexdigest() != asset["sha256"]:
        raise ClientUpdateError(
            "release_asset_hash_mismatch",
            f"正式版本资产 {asset['role']} 校验失败。",
        )
    return destination


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 180,
) -> str:
    completed = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise ClientUpdateError(
            "client_update_command_failed",
            completed.stderr.strip()
            or completed.stdout.strip()
            or "客户端更新命令执行失败。",
        )
    return completed.stdout.strip()


def _venv_paths(venv: Path) -> tuple[Path, Path, Path]:
    if sys.platform == "win32":
        scripts = venv / "Scripts"
        return (
            scripts / "python.exe",
            scripts / "ora-workbench.exe",
            scripts / "ora-native-host.exe",
        )
    scripts = venv / "bin"
    return (
        scripts / "python",
        scripts / "ora-workbench",
        scripts / "ora-native-host",
    )


def _skill_targets(
    *,
    home: Path | None = None,
    environ: dict[str, str] | None = None,
) -> list[Path]:
    environment = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    primary_root = Path(
        environment.get("ORA_SKILLS_DIR")
        or user_home / ".agents" / "skills"
    )
    roots = [primary_root]
    for host in (
        user_home / ".codex" / "skills",
        user_home / ".claude" / "skills",
        user_home / ".openclaw" / "workspace" / "skills",
    ):
        if host.parent.exists():
            roots.append(host)
    return [root / "agentmesh-officialrecruitment" / "SKILL.md" for root in roots]


def _skill_version(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if "name: agentmesh-officialrecruitment" not in source:
        raise ClientUpdateError(
            "release_skill_invalid",
            "正式 Skill 名称校验失败。",
        )
    match = re.search(r"(?m)^version:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", source)
    if match is None:
        raise ClientUpdateError(
            "release_skill_invalid",
            "正式 Skill 缺少版本号。",
        )
    return match.group(1)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _install_skill(skill: Path) -> list[str]:
    written = []
    for target in _skill_targets():
        if target.exists() and "name: agentmesh-officialrecruitment" not in target.read_text(
            encoding="utf-8"
        ):
            continue
        _atomic_copy(skill, target)
        written.append(str(target))
    return written


def _snapshot_files(paths: list[Path]) -> dict[Path, tuple[bytes, int] | None]:
    snapshot: dict[Path, tuple[bytes, int] | None] = {}
    for path in paths:
        try:
            snapshot[path] = (path.read_bytes(), path.stat().st_mode)
        except FileNotFoundError:
            snapshot[path] = None
    return snapshot


def _restore_files(snapshot: dict[Path, tuple[bytes, int] | None]) -> None:
    for path, previous in snapshot.items():
        if previous is None:
            path.unlink(missing_ok=True)
            continue
        content, mode = previous
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.rollback")
        try:
            temporary.write_bytes(content)
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _rollback_local_profile(migration: dict[str, Any] | None) -> None:
    if not isinstance(migration, dict) or migration.get("migrated") is not True:
        return
    database = Path(
        str(migration.get("database_path") or default_local_profile_path())
    )
    backup = migration.get("backup_path")
    if isinstance(backup, str) and backup:
        restore_local_profile_database(database, Path(backup))
        return
    if migration.get("from_schema_version") == 0:
        for path in (
            database,
            Path(f"{database}-wal"),
            Path(f"{database}-shm"),
            Path(f"{database}-journal"),
        ):
            path.unlink(missing_ok=True)


def finalize_managed_install(
    skill: Path,
    *,
    root: Path | None = None,
    extension_root: Path | None = None,
) -> dict[str, Any]:
    """Atomically adopt a fresh or historical official installation.

    The downloadable installers build the versioned virtual environment first,
    then call this function from that new environment. Only this function may
    migrate private local data and switch the stable launcher pointer.
    """

    install_root = (root or default_install_root()).expanduser().resolve()
    candidate_skill = skill.expanduser()
    skill_version = _skill_version(candidate_skill)
    release_root = install_root / "releases" / __version__
    venv = release_root / "venv"
    python, cli, native_host = _venv_paths(venv)
    current_python = Path(sys.executable).absolute()
    if (
        venv.absolute() not in current_python.parents
        or not cli.is_file()
        or not native_host.is_file()
    ):
        raise ClientUpdateError(
            "bootstrap_runtime_invalid",
            "官网安装器没有从待切换的新版本环境执行，已停止安装。",
        )

    profile_migration: dict[str, Any] | None = None
    installed_skill_targets: list[str] = []
    browser_root = extension_root or default_extension_root()
    with _update_lock(install_root):
        state_paths = [
            current_pointer_path(install_root),
            install_state_path(install_root),
        ]
        skill_targets = _skill_targets()
        native_manifest = native_host_manifest_path()
        pairing_state = extension_pairing_state_path(browser_root)
        state_snapshot = _snapshot_files(state_paths)
        skill_snapshot = _snapshot_files(skill_targets)
        native_snapshot = _snapshot_files([native_manifest, pairing_state])
        old_state = _read_json(install_state_path(install_root)) or {}
        try:
            store = LocalProfileStore(default_local_profile_path())
            profile_migration = dict(store.migration_report)
            installed_skill_targets = _install_skill(candidate_skill)
            now = int(time.time())
            _write_private_json(
                current_pointer_path(install_root),
                {
                    "schema_version": 1,
                    "product": PRODUCT,
                    "client_version": __version__,
                    "cli_path": str(cli),
                    "python_path": str(python),
                    "native_host_path": str(native_host),
                    "updated_at_epoch": now,
                },
            )
            _write_private_json(
                install_state_path(install_root),
                {
                    **old_state,
                    "schema_version": 1,
                    "managed": True,
                    "product": PRODUCT,
                    "install_type": "official-installer",
                    "current_client_version": __version__,
                    "current_skill_version": skill_version,
                    "previous_client_version": old_state.get(
                        "current_client_version"
                    ),
                    "legacy_venv_preserved": (
                        install_root / "venv"
                    ).is_dir(),
                    "updated_at_epoch": now,
                },
            )
            native = install_native_messaging_host(
                executable=native_host,
                extension_root=browser_root,
            )
        except Exception as error:
            _restore_files(state_snapshot)
            _restore_files(skill_snapshot)
            _restore_files(native_snapshot)
            _rollback_local_profile(profile_migration)
            if isinstance(error, ClientUpdateError):
                raise
            raise ClientUpdateError(
                "bootstrap_install_failed",
                "客户端安装失败，旧入口、Skill、浏览器连接和本机资料已恢复。",
            ) from error
    return {
        "status": "ready",
        "client_version": __version__,
        "skill_version": skill_version,
        "skill_targets": installed_skill_targets,
        "local_profile": profile_migration,
        "native_host": native,
        "legacy_venv_preserved": (install_root / "venv").is_dir(),
    }


def _extension_asset(manifest: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (item for item in manifest["assets"] if item["role"] == "extension_zip"),
        None,
    )


def _update_existing_zip_extension(
    manifest: dict[str, Any],
    *,
    opener: Callable[..., Any] = open_without_redirect,
) -> dict[str, Any]:
    root = default_extension_root()
    current = extension_status(root)
    asset = _extension_asset(manifest)
    if current.get("status") == "not_installed" or asset is None:
        return {
            "channel": "chrome_web_store_or_not_installed",
            "changed": False,
            "chrome_reload_required": False,
        }
    if (
        current.get("healthy") is True
        and _version(str(current["extension_version"])) >= _version(str(asset["version"]))
    ):
        return {
            "channel": "zip",
            "changed": False,
            "extension_version": current["extension_version"],
            "chrome_reload_required": False,
        }
    result = prepare_extension(
        DEFAULT_PRODUCT_BASE,
        extension_root=root,
        opener=opener,
        expected_version=str(asset["version"]),
        expected_sha256=str(asset["sha256"]),
        expected_bytes=int(asset["bytes"]),
    )
    return {
        **result,
        "channel": "zip",
        "chrome_reload_required": bool(result.get("changed")),
    }


def apply_managed_update(
    manifest: dict[str, Any],
    *,
    root: Path | None = None,
    opener: Callable[..., Any] = open_without_redirect,
) -> dict[str, Any]:
    install_root = root or default_install_root()
    state = _managed_state(install_root)
    if state is None:
        raise ClientUpdateError(
            "unmanaged_install",
            "当前客户端不是官网受管安装，不能自动修改。",
        )
    latest = str(manifest["latest_client_version"])
    wheel_asset = next(
        item for item in manifest["assets"] if item["role"] == "adapter_wheel"
    )
    skill_asset = next(
        item for item in manifest["assets"] if item["role"] == "host_skill"
    )
    release_root = install_root / "releases" / latest
    venv = release_root / "venv"
    python, cli, native_host = _venv_paths(venv)
    migration: dict[str, Any] | None = None
    installed_skill_targets: list[str] = []
    with _update_lock(install_root):
        state = _managed_state(install_root)
        if state is None:
            raise ClientUpdateError(
                "managed_install_state_changed",
                "客户端受管安装状态在更新前发生变化，已停止更新。",
            )
        old_pointer = _read_json(current_pointer_path(install_root))
        old_state = dict(state)
        skill_targets = _skill_targets()
        skill_snapshot = _snapshot_files(skill_targets)
        native_manifest = native_host_manifest_path()
        pairing_state = extension_pairing_state_path(
            default_extension_root()
        )
        native_snapshot = _snapshot_files(
            [native_manifest, pairing_state]
        )
        work_root = install_root / "update"
        work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(
            prefix="candidate-",
            dir=work_root,
        ) as temporary_name:
            temporary = Path(temporary_name)
            wheel = _download_asset(
                wheel_asset,
                temporary / "client.whl",
                opener=opener,
            )
            skill = _download_asset(
                skill_asset,
                temporary / "SKILL.md",
                opener=opener,
            )
            if _skill_version(skill) != skill_asset["skill_version"]:
                raise ClientUpdateError(
                    "release_skill_version_mismatch",
                    "Skill 内容版本与签名清单不一致。",
                )
            if release_root.exists():
                shutil.rmtree(release_root)
            release_root.mkdir(parents=True, exist_ok=True)
            try:
                _run([sys.executable, "-m", "venv", str(venv)])
                _run(
                    [
                        str(python),
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "--no-input",
                        str(wheel),
                    ]
                )
                smoke = _run(
                    [str(python), "-c", (
                        "import official_recruitment_agent as p; print(p.__version__)"
                    )]
                )
                if smoke != latest:
                    raise ClientUpdateError(
                        "client_update_smoke_failed",
                        "新客户端冒烟检查返回了错误版本。",
                    )
                migration_env = os.environ.copy()
                migration_env[SKIP_UPDATE_ENV] = "1"
                migration_raw = _run(
                    [str(cli), "upgrade-check"],
                    env=migration_env,
                )
                migration = json.loads(migration_raw)
                if migration.get("local_profile", {}).get("status") != "ready":
                    raise ClientUpdateError(
                        "local_profile_migration_failed",
                        "新客户端没有通过本机资料库迁移检查。",
                    )
                pointer = {
                    "schema_version": 1,
                    "product": PRODUCT,
                    "client_version": latest,
                    "cli_path": str(cli),
                    "python_path": str(python),
                    "native_host_path": str(native_host),
                    "updated_at_epoch": int(time.time()),
                }
                _write_private_json(current_pointer_path(install_root), pointer)
                host_env = os.environ.copy()
                host_env[SKIP_UPDATE_ENV] = "1"
                _run(
                    [str(cli), "extension", "host", "install"],
                    env=host_env,
                )
                installed_skill_targets = _install_skill(skill)
                _write_private_json(
                    install_state_path(install_root),
                    {
                        **state,
                        "current_client_version": latest,
                        "current_skill_version": skill_asset["skill_version"],
                        "previous_client_version": str(
                            state.get("current_client_version") or __version__
                        ),
                        "release_tag": manifest["git_tag"],
                        "release_commit": manifest["git_commit"],
                        "updated_at_epoch": int(time.time()),
                    },
                )
            except Exception as error:
                if old_pointer is not None:
                    _write_private_json(
                        current_pointer_path(install_root),
                        old_pointer,
                    )
                _write_private_json(install_state_path(install_root), old_state)
                _restore_files(skill_snapshot)
                _restore_files(native_snapshot)
                _rollback_local_profile(
                    migration.get("local_profile")
                    if isinstance(migration, dict)
                    else None
                )
                shutil.rmtree(release_root, ignore_errors=True)
                if isinstance(error, ClientUpdateError):
                    raise
                raise ClientUpdateError(
                    "client_update_failed",
                    "客户端更新失败，已恢复旧版本与本机数据。",
                ) from error
    try:
        extension = _update_existing_zip_extension(
            manifest,
            opener=opener,
        )
    except Exception as error:
        extension = {
            "status": "update_pending",
            "changed": False,
            "chrome_reload_required": False,
            "error_code": "extension_update_failed",
            "message": str(error),
        }
    return {
        "status": (
            "updated_extension_pending"
            if extension.get("status") == "update_pending"
            else "updated"
        ),
        "from_version": __version__,
        "to_version": latest,
        "new_cli_path": str(cli),
        "skill_version": skill_asset["skill_version"],
        "skill_targets": installed_skill_targets,
        "local_profile": migration["local_profile"],
        "extension": extension,
    }


def check_for_update(
    *,
    auto_apply: bool = True,
    force: bool = False,
    root: Path | None = None,
    opener: Callable[..., Any] = open_without_redirect,
    on_event: Callable[..., None] | None = None,
) -> dict[str, Any]:
    install_root = root or default_install_root()
    if os.environ.get(SKIP_UPDATE_ENV) == "1":
        return {"status": "skipped", "current_version": __version__}
    if not is_managed_runtime(install_root):
        return {
            "status": "unmanaged",
            "current_version": __version__,
        }
    manifest = fetch_release_manifest(
        root=install_root,
        force=force,
        opener=opener,
    )
    if manifest is None:
        return {"status": "unavailable", "current_version": __version__}
    latest = str(manifest["latest_client_version"])
    minimum = str(manifest["minimum_supported_version"])
    if _version(__version__) > _version(latest):
        return {
            "status": "ahead",
            "current_version": __version__,
            "latest_version": latest,
        }
    if _version(__version__) == _version(latest):
        try:
            extension = _update_existing_zip_extension(
                manifest,
                opener=opener,
            )
        except Exception as error:
            extension = {
                "status": "update_pending",
                "changed": False,
                "chrome_reload_required": False,
                "error_code": "extension_update_failed",
                "message": str(error),
            }
        return {
            "status": (
                "extension_update_pending"
                if extension.get("status") == "update_pending"
                else "extension_updated"
                if extension.get("changed")
                else "current"
            ),
            "current_version": __version__,
            "manifest": manifest,
            "extension": extension,
        }
    if not auto_apply:
        if on_event is not None:
            on_event(
                "client_update_detected",
                from_version=__version__,
                to_version=latest,
                required=_version(__version__) < _version(minimum),
                automatic=False,
                message="发现 AgentMesh-OfficialRecruitment 正式更新。",
            )
        return {
            "status": (
                "update_required"
                if _version(__version__) < _version(minimum)
                else "update_available"
            ),
            "current_version": __version__,
            "latest_version": latest,
            "required": _version(__version__) < _version(minimum),
        }
    event = {
        "from_version": __version__,
        "to_version": latest,
        "required": _version(__version__) < _version(minimum),
        "automatic": True,
    }
    if on_event is not None:
        on_event(
            "client_update_detected",
            message="发现 AgentMesh-OfficialRecruitment 正式更新。",
            **event,
        )
        on_event(
            "client_update_started",
            message="正在更新 CLI、Skill 与本机连接组件。",
            **event,
        )
    try:
        result = apply_managed_update(
            manifest,
            root=install_root,
            opener=opener,
        )
    except ClientUpdateError as error:
        if on_event is not None:
            on_event(
                "client_update_failed",
                **managed_update_failure(error),
                **event,
            )
        raise
    if on_event is not None:
        on_event(
            "client_update_completed",
            message=(
                "客户端已更新，扩展将在下一次命令继续重试。"
                if result["status"] == "updated_extension_pending"
                else "客户端更新已完成。"
            ),
            extension=result["extension"],
            **event,
        )
    return result


def managed_update_failure(error: ClientUpdateError) -> dict[str, Any]:
    return {
        "stage": "client_update_failed",
        "error_code": error.code,
        "message": str(error),
        "request_preserved": True,
        "next_suggested": (
            "请让宿主 Agent 执行一次官网安装器，然后自动重试原命令。"
        ),
        "recovery_commands": {
            "macos_linux": (
                "curl -fsSL https://recruit.agentmesh360.com/install-agent.sh | sh"
            ),
            "windows_powershell": (
                "powershell -NoProfile -ExecutionPolicy Bypass -Command "
                '"irm https://recruit.agentmesh360.com/install-agent.ps1 | iex"'
            ),
        },
        **error.recovery,
    }
