from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from official_recruitment_agent.workbench.profile_contract import (
    PROFILE_SCHEMA_VERSION,
    normalize_profile_fields,
)


DEFAULT_BASE_URL = "https://recruit.agentmesh360.com"


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
    profile.add_argument("--expires-in", type=int, default=3600)
    profile.add_argument("--idempotency-key")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "configure":
            result = _configure(args)
        elif args.command == "doctor":
            summary = _request(
                args,
                "GET",
                "/api/v1/workbench/summary",
            )
            profiles = _request(args, "GET", "/api/v1/profiles")
            current_profile = next(
                (
                    item
                    for item in profiles
                    if item.get("is_current") is True
                ),
                None,
            )
            result = {
                "status": (
                    "ready" if current_profile is not None else "needs_profile"
                ),
                "server_url": args.base_url.rstrip("/"),
                "api_key_configured": bool(args.api_key),
                "current_profile": (
                    {
                        "profile_version_id": current_profile[
                            "profile_version_id"
                        ],
                        "version_number": current_profile[
                            "version_number"
                        ],
                        "label": current_profile["label"],
                    }
                    if current_profile
                    else None
                ),
                "counts": summary.get("counts", {}),
                "next_action": (
                    "浏览器扩展可直接在当前招聘页面启动辅助填写。"
                    if current_profile
                    else (
                        "读取标准简历，按 profile-schema 生成字段并提交"
                        " propose-profile-import 提案。"
                    )
                ),
            }
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
        else:
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
                    "is_primary",
                ],
            },
            "preferences": [
                "target_roles",
                "preferred_locations",
                "skills",
                "certificates",
                "awards",
                "language_skills",
            ],
        },
    }


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
