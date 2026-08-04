from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from official_recruitment_agent.workbench.profile_contract import (
    PROFILE_IMPORT_PROPOSAL_TTL_SECONDS,
    PROFILE_SCHEMA_VERSION,
    normalize_profile_fields,
)
from official_recruitment_agent.local_profile_handoff import (
    LOCAL_HANDOFF_URL,
    LocalHandoffService,
    LocalProfileStore,
    ProductClient,
    default_local_profile_path,
    serve_local_handoff,
)


DEFAULT_BASE_URL = "https://recruit.agentmesh360.com"
CONTINUITY_SCHEMA_VERSION = 1


def _config_path() -> Path:
    override = os.getenv("ORA_CONFIG_PATH")
    if override:
        return Path(override).expanduser()
    return (
        Path.home()
        / ".config"
        / "agentmesh360"
        / "official-recruitment.json"
    )


def _load_config() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Agent 本机配置必须是 JSON 对象。")
    return payload


def _continuity_path() -> Path:
    override = os.getenv("ORA_CONTINUITY_PATH")
    if override:
        return Path(override).expanduser()
    return _config_path().with_name(
        "official-recruitment-continuity.json"
    )


def _empty_continuity_state() -> dict[str, Any]:
    return {
        "schema_version": CONTINUITY_SCHEMA_VERSION,
        "workspaces": {},
    }


def _load_continuity_state() -> tuple[dict[str, Any] | None, str | None]:
    path = _continuity_path()
    if not path.exists():
        return _empty_continuity_state(), None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("root_not_object")
        if set(payload) != {"schema_version", "workspaces"}:
            raise ValueError("unexpected_root_fields")
        if payload.get("schema_version") != CONTINUITY_SCHEMA_VERSION:
            raise ValueError("unsupported_schema")
        workspaces = payload.get("workspaces")
        if not isinstance(workspaces, dict):
            raise ValueError("workspaces_not_object")
        for workspace_ref, entry in workspaces.items():
            if not _valid_workspace_ref(workspace_ref):
                raise ValueError("invalid_workspace_ref")
            if not isinstance(entry, dict):
                raise ValueError("invalid_workspace_entry")
            if set(entry) != {
                "confirmed_profile_seen",
                "profile_fingerprint",
            }:
                raise ValueError("unexpected_workspace_fields")
            if entry.get("confirmed_profile_seen") is not True:
                raise ValueError("invalid_profile_state")
            fingerprint = entry.get("profile_fingerprint")
            if not _valid_sha256(fingerprint):
                raise ValueError("invalid_profile_fingerprint")
        return payload, None
    except (OSError, ValueError, json.JSONDecodeError):
        return None, "continuity_marker_unreadable"


def _valid_workspace_ref(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("ws_"):
        return False
    suffix = value[3:]
    return len(suffix) == 32 and all(
        character in "0123456789abcdef" for character in suffix
    )


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _profile_fingerprint(
    workspace_ref: str,
    current_profile: dict[str, Any],
) -> str:
    source = (
        f"{workspace_ref}:"
        f"{current_profile['profile_version_id']}:"
        f"{current_profile['version_number']}"
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _write_continuity_state(
    state: dict[str, Any],
    *,
    workspace_ref: str,
    current_profile: dict[str, Any],
) -> None:
    workspaces = state.setdefault("workspaces", {})
    workspaces[workspace_ref] = {
        "confirmed_profile_seen": True,
        "profile_fingerprint": _profile_fingerprint(
            workspace_ref,
            current_profile,
        ),
    }
    path = _continuity_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _doctor(args: argparse.Namespace) -> dict[str, Any]:
    access = _request(args, "GET", "/api/v1/workbench/access")
    workspace_ref = access.get("workspace_ref")
    if (
        not isinstance(workspace_ref, str)
        or not _valid_workspace_ref(workspace_ref)
    ):
        raise ValueError(
            "工作台没有返回匿名工作区编号，请更新工作台后重试。"
        )
    summary = _request(args, "GET", "/api/v1/workbench/summary")
    profiles = _request(args, "GET", "/api/v1/profiles")
    if not isinstance(summary, dict) or not isinstance(profiles, list):
        raise ValueError("工作台返回了无法识别的健康检查数据。")
    if any(not isinstance(item, dict) for item in profiles):
        raise ValueError("工作台返回了无法识别的档案列表。")
    current_profile = next(
        (item for item in profiles if item.get("is_current") is True),
        None,
    )
    continuity_state, continuity_error = _load_continuity_state()

    if current_profile is not None:
        repaired = continuity_error is not None
        _write_continuity_state(
            continuity_state or _empty_continuity_state(),
            workspace_ref=workspace_ref,
            current_profile=current_profile,
        )
        status = "ready"
        continuity_status = (
            "marker_repaired" if repaired else "healthy"
        )
        recovery_required = False
        interaction_required = None
        next_action = (
            "浏览器扩展可直接在当前招聘页面启动辅助填写。"
        )
    else:
        prior_profile_seen = bool(
            continuity_state
            and continuity_state.get("workspaces", {})
            .get(workspace_ref, {})
            .get("confirmed_profile_seen")
            is True
        )
        if continuity_error is not None or prior_profile_seen:
            status = "workspace_recovery_required"
            continuity_status = (
                "continuity_check_failed"
                if continuity_error is not None
                else "profile_missing"
            )
            recovery_required = True
            interaction_required = {
                "kind": "resume_reselection",
                "title": "本机资料库需要恢复",
                "prompt": (
                    "当前本机资料库中找不到已确认档案。请重新选择"
                    "你的标准简历；原始简历只在本机读取，不会上传"
                    "或留存在 AgentMesh360。"
                ),
            }
            next_action = (
                "停止辅助填写和申请流程，请用户明确重新选择标准简历，"
                "再按 profile-schema 提交 propose-profile-import 提案。"
            )
        else:
            status = "needs_profile"
            continuity_status = "uninitialized"
            recovery_required = False
            interaction_required = None
            next_action = (
                "请用户明确选择标准简历，按 profile-schema 生成字段并"
                "提交 propose-profile-import 提案。"
            )

    return {
        "status": status,
        "server_url": args.base_url.rstrip("/"),
        "api_key_configured": bool(args.api_key),
        "workspace_ref": workspace_ref,
        "continuity_status": continuity_status,
        "recovery_required": recovery_required,
        "interaction_required": interaction_required,
        "current_profile": (
            {
                "profile_version_id": current_profile[
                    "profile_version_id"
                ],
                "version_number": current_profile["version_number"],
                "label": current_profile["label"],
            }
            if current_profile
            else None
        ),
        "counts": summary.get("counts", {}),
        "next_action": next_action,
    }


def build_parser() -> argparse.ArgumentParser:
    config = _load_config()
    parser = argparse.ArgumentParser(
        prog="ora-workbench",
        description="供宿主 Agent 调用的官网招聘工作台 CLI 适配器",
    )
    parser.add_argument(
        "--base-url",
        default=(
            os.getenv("ORA_WORKBENCH_URL")
            or config.get("base_url")
            or DEFAULT_BASE_URL
        ),
    )
    parser.add_argument(
        "--account",
        default=os.getenv("ORA_ACCOUNT_REF", "acct-synthetic-demo"),
    )
    parser.add_argument(
        "--actor",
        default=os.getenv("ORA_ACTOR_ID", "agent-local"),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AGENTMESH_API_KEY") or config.get("api_key"),
        help="AgentMesh360 通用 API Key；生产环境必须提供",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure")
    configure.add_argument(
        "--server-url",
        default=None,
        help=f"工作台地址，默认 {DEFAULT_BASE_URL}",
    )
    configure.add_argument(
        "--key",
        default=None,
        help="AgentMesh360 通用 API Key",
    )
    subparsers.add_parser("doctor")
    subparsers.add_parser("extension-setup")
    subparsers.add_parser("profile-schema")
    handoff = subparsers.add_parser("profile-handoff")
    handoff_commands = handoff.add_subparsers(
        dest="profile_handoff_command",
        required=True,
    )
    handoff_commands.add_parser("serve")
    handoff_commands.add_parser("start")
    handoff_commands.add_parser("status")
    subparsers.add_parser("summary")
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument(
        "resource",
        choices=[
            "sources",
            "opportunities",
            "applications",
            "proposals",
            "profiles",
            "audit",
        ],
    )
    detail = subparsers.add_parser("application")
    detail.add_argument("application_id")

    proposal = subparsers.add_parser("propose-transition")
    proposal.add_argument("application_id")
    proposal.add_argument("--expected-version", required=True, type=int)
    proposal.add_argument("--to-state", required=True)
    proposal.add_argument("--next-action")
    proposal.add_argument(
        "--evidence-ref",
        action="append",
        required=True,
        help="状态依据，可重复提供",
    )
    proposal.add_argument(
        "--risk-level",
        choices=["low", "medium", "high"],
        default="medium",
    )
    proposal.add_argument("--expires-in", type=int, default=3600)
    proposal.add_argument("--idempotency-key")

    profile = subparsers.add_parser("propose-profile-import")
    profile.add_argument("--label", required=True)
    profile.add_argument(
        "--document",
        required=True,
        help="本机标准简历路径；原始文件不会上传到工作台",
    )
    profile.add_argument(
        "--fields-json",
        required=True,
        help="宿主 Agent 按 profile-schema 生成的结构化 JSON 文件",
    )
    profile.add_argument("--expected-version", required=True, type=int)
    profile.add_argument(
        "--expires-in",
        type=int,
        default=PROFILE_IMPORT_PROPOSAL_TTL_SECONDS,
    )
    profile.add_argument("--idempotency-key")

    questions = subparsers.add_parser("profile-questions")
    questions.add_argument("--fill-task-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "configure":
            result = _configure(args)
        elif args.command == "doctor":
            result = _doctor(args)
        elif args.command == "extension-setup":
            result = {
                "server_url": args.base_url.rstrip("/"),
                "api_key_configured": bool(args.api_key),
                "download_url": (
                    args.base_url.rstrip("/")
                    + "/downloads/"
                    "agentmesh-officialrecruitment-extension.zip"
                ),
                "install_guide_url": (
                    args.base_url.rstrip("/")
                    + "/guides/install-browser-extension/"
                ),
                "instructions": [
                    "下载并解压扩展包。",
                    "在 chrome://extensions 开启开发者模式。",
                    "选择加载已解压的扩展程序。",
                    "首次打开扩展时填写同一 AgentMesh360 API Key。",
                ],
            }
        elif args.command == "profile-schema":
            result = _profile_schema()
        elif args.command == "profile-handoff":
            if args.profile_handoff_command == "serve":
                service = _local_handoff_service(args)
                print(
                    json.dumps(
                        {
                            "status": "ready",
                            "local_handoff_url": LOCAL_HANDOFF_URL,
                            "local_profile_store": str(service.store.path),
                            "answer_residency": "local_device",
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                serve_local_handoff(service)
                return 0
            result = (
                _start_profile_handoff(args)
                if args.profile_handoff_command == "start"
                else _profile_handoff_status(args)
            )
        elif args.command == "summary":
            result = _request(args, "GET", "/api/v1/workbench/summary")
        elif args.command == "list":
            result = _request(
                args,
                "GET",
                f"/api/v1/{args.resource}",
            )
        elif args.command == "application":
            result = _request(
                args,
                "GET",
                f"/api/v1/applications/{args.application_id}",
            )
        elif args.command == "propose-transition":
            payload: dict[str, Any] = {
                "to_state": args.to_state,
                "evidence_refs": args.evidence_ref,
            }
            if args.next_action:
                payload["next_action"] = args.next_action
            result = _request(
                args,
                "POST",
                "/api/v1/agent/proposals",
                {
                    "target_type": "application",
                    "target_id": args.application_id,
                    "expected_version": args.expected_version,
                    "action_type": "transition_application",
                    "payload": payload,
                    "risk_level": args.risk_level,
                    "expires_in_seconds": args.expires_in,
                },
                idempotency_key=(
                    args.idempotency_key
                    or f"agent-proposal-{uuid4().hex}"
                ),
            )
        elif args.command == "propose-profile-import":
            fields = _load_profile_fields(Path(args.fields_json))
            fields["_source_document"] = _source_document_metadata(
                Path(args.document)
            )
            result = _request(
                args,
                "POST",
                "/api/v1/agent/proposals",
                {
                    "target_type": "profile",
                    "target_id": "profile-current",
                    "expected_version": args.expected_version,
                    "action_type": "create_profile_version",
                    "payload": {
                        "label": args.label,
                        "fields": fields,
                    },
                    "risk_level": "high",
                    "expires_in_seconds": args.expires_in,
                },
                idempotency_key=(
                    args.idempotency_key
                    or f"agent-profile-import-{uuid4().hex}"
                ),
            )
        elif args.command == "profile-questions":
            path = "/api/v1/agent/profile-questions"
            if args.fill_task_id:
                path += f"?fill_task_id={quote(args.fill_task_id)}"
            result = _request(args, "GET", path)
    except (HTTPError, URLError, OSError, ValueError) as error:
        print(
            json.dumps(
                _error_payload(error),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _configure(args: argparse.Namespace) -> dict[str, Any]:
    current = _load_config()
    base_url = (
        args.server_url
        or args.base_url
        or current.get("base_url")
        or DEFAULT_BASE_URL
    ).rstrip("/")
    api_key = args.key or args.api_key or current.get("api_key")
    if not api_key:
        raise ValueError("请通过 --key 提供 AgentMesh360 API Key。")
    if not (
        base_url.startswith("https://")
        or base_url.startswith("http://127.0.0.1:")
        or base_url.startswith("http://localhost:")
    ):
        raise ValueError("工作台地址必须使用 HTTPS 或本机开发地址。")
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_url": base_url,
                "api_key": api_key,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return {
        "status": "configured",
        "config_path": str(path),
        "server_url": base_url,
        "api_key_configured": True,
        "permissions": "0600",
        "next_command": "ora-workbench doctor",
    }


def _profile_schema() -> dict[str, Any]:
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "instructions": [
            "读取用户明确指定的本机标准简历，不上传原始文件。",
            "只提取文档中有明确依据的字段，不推断缺失信息。",
            "多段教育经历必须保留在 education_records；只有一段时可自动标记为主教育经历。",
            "多段教育经历无法确认主记录时，不设置 is_primary，交给用户审阅。",
            "实习、工作、项目、校内职务和活动必须保留在 experience_records，并用 kind 区分。",
            "同类经历有多条但无法确认主记录时，不设置 is_primary，不擅自选择第一条。",
            "证书、技能和语言的日期、熟练度、分数或等级应进入对应 records，不要压成无法拆分的文本。",
        ],
        "fields": {
            "identity": [
                "full_name",
                "gender",
                "birth_date",
                "phone",
                "email",
                "id_number",
                "political_status",
                "ethnicity",
                "id_type",
                "household_registration",
                "native_place",
                "second_major",
                "personal_strengths",
                "height_cm",
            ],
            "education_records": {
                "required": ["school_name"],
                "optional": [
                    "school_city",
                    "school_country",
                    "college_name",
                    "major",
                    "education_level",
                    "degree",
                    "start_date",
                    "graduation_date",
                    "study_mode",
                    "research_summary",
                    "is_primary",
                ],
            },
            "experience_records": {
                "required": ["kind"],
                "kind": [
                    "internship",
                    "work",
                    "project",
                    "campus_role",
                    "activity",
                ],
                "optional": [
                    "name",
                    "organization_name",
                    "role_title",
                    "start_date",
                    "end_date",
                    "location",
                    "description",
                    "level",
                    "is_primary",
                ],
            },
            "certificate_records": {
                "required": ["name"],
                "optional": ["acquired_date", "issuer", "is_primary"],
            },
            "skill_records": {
                "required": ["name"],
                "optional": ["proficiency", "is_primary"],
            },
            "language_records": {
                "required": ["language"],
                "optional": ["score", "level", "is_primary"],
            },
            "preferences": [
                "target_roles",
                "preferred_locations",
                "expected_salary",
                "skills",
                "certificates",
                "awards",
                "language_skills",
            ],
            "supplemental_facts": {
                "description": (
                    "只用于保存用户针对真实报名字段明确回答并在 Web 确认的补充信息"
                ),
                "scope": ["account", "site", "application"],
                "privacy": ["standard", "sensitive"],
            },
        },
    }


def _local_handoff_service(
    args: argparse.Namespace,
) -> LocalHandoffService:
    if not args.api_key:
        raise ValueError(
            "本机 Agent 交接需要已配置的 AgentMesh360 API Key。"
        )
    return LocalHandoffService(
        store=LocalProfileStore(default_local_profile_path()),
        product=ProductClient(
            base_url=args.base_url.rstrip("/"),
            api_key=args.api_key,
            account_ref=args.account,
            actor_id=args.actor,
        ),
    )


def _profile_handoff_status(
    args: argparse.Namespace,
) -> dict[str, Any]:
    service = _local_handoff_service(args)
    access = service.product.access()
    workspace_ref = access.get("workspace_ref")
    if not isinstance(workspace_ref, str):
        raise ValueError("工作台没有返回有效的本机工作区编号。")
    request = Request(
        f"{LOCAL_HANDOFF_URL}/v1/status?workspace_ref={quote(workspace_ref)}",
        headers={
            "Accept": "application/json",
            "Origin": (
                "https://recruit.agentmesh360.com"
                if args.base_url.startswith("https://")
                else "http://127.0.0.1:8010"
            ),
        },
        method="GET",
    )
    with urlopen(request, timeout=2) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise ValueError("本机 Agent 交接服务返回了无效状态。")
    return payload


def _start_profile_handoff(
    args: argparse.Namespace,
) -> dict[str, Any]:
    try:
        current = _profile_handoff_status(args)
        return {**current, "started": False, "already_running": True}
    except (HTTPError, URLError, OSError, ValueError):
        pass
    log_path = _config_path().with_name(
        "official-recruitment-local-handoff.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "official_recruitment_agent.workbench_cli",
                "profile-handoff",
                "serve",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    log_path.chmod(0o600)
    for _ in range(30):
        if process.poll() is not None:
            break
        time.sleep(0.1)
        try:
            status = _profile_handoff_status(args)
            return {
                **status,
                "started": True,
                "already_running": False,
                "pid": process.pid,
            }
        except (HTTPError, URLError, OSError, ValueError):
            continue
    raise ValueError(
        f"本机 Agent 资料交接启动失败，请检查 {log_path}。"
    )


def _load_profile_fields(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("结构化档案 JSON 必须是对象。")
    fields = payload.get("fields", payload)
    if not isinstance(fields, dict):
        raise ValueError("结构化档案 fields 必须是对象。")
    return normalize_profile_fields(fields)


def _source_document_metadata(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "sha256": digest,
        "suffix": path.suffix.lower(),
        "raw_document_uploaded": False,
        "parsed_by": "host_agent",
        "schema_version": PROFILE_SCHEMA_VERSION,
    }


def _request(
    args: argparse.Namespace,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    idempotency_key: str | None = None,
) -> Any:
    base_url = args.base_url.rstrip("/")
    is_local = base_url.startswith(
        ("http://127.0.0.1:", "http://localhost:")
    )
    if not args.api_key and not is_local:
        raise ValueError(
            "生产工作台需要 AgentMesh360 API Key；"
            "请先运行 ora-workbench configure --key <API_KEY>。"
        )
    body = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    headers = {"Accept": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    else:
        headers.update(
            {
                "X-ORA-Account": args.account,
                "X-ORA-Actor": args.actor,
                "X-ORA-Surface": "mcp",
            }
        )
    if body is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = Request(
        base_url + path,
        data=body,
        headers=headers,
        method=method,
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _error_payload(error: Exception) -> dict[str, Any]:
    if isinstance(error, HTTPError):
        try:
            return json.loads(error.read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {
                "error": {
                    "code": "http_error",
                    "message": f"工作台返回 HTTP {error.code}",
                }
            }
    return {
        "error": {
            "code": "workbench_unreachable",
            "message": str(error),
        }
    }


if __name__ == "__main__":
    sys.exit(main())
