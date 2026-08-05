from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen


LOCAL_HANDOFF_HOST = "127.0.0.1"
LOCAL_HANDOFF_PORT = 8765
LOCAL_HANDOFF_URL = f"http://{LOCAL_HANDOFF_HOST}:{LOCAL_HANDOFF_PORT}"
MAX_REQUEST_BYTES = 256 * 1024
PROPOSAL_TTL_SECONDS = 15 * 60
ALLOWED_WEB_ORIGINS = frozenset(
    {
        "https://recruit.agentmesh360.com",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8010",
        "http://localhost:8010",
    }
)
_CHROME_EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")
_QUESTION_ID = re.compile(r"^pq_[0-9a-f]{24}$")


class LocalHandoffError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def default_local_profile_path() -> Path:
    override = os.getenv("ORA_LOCAL_PROFILE_DB")
    if override:
        return Path(override).expanduser()
    return (
        Path.home()
        / ".local"
        / "share"
        / "agentmesh360"
        / "official-recruitment-private.sqlite3"
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_capability(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _secure_private_file(path: Path, *, create: bool) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if create:
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | nofollow,
                0o600,
            )
        except FileExistsError:
            descriptor = os.open(path, flags | nofollow)
    else:
        try:
            descriptor = os.open(path, flags | nofollow)
        except FileNotFoundError:
            return
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError(f"本机资料库路径不是普通文件：{path}")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _json_object(value: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalHandoffError(
            HTTPStatus.BAD_REQUEST,
            "invalid_json",
            "本机 Agent 收到的交接数据格式无效。",
        ) from error
    if not isinstance(payload, dict):
        raise LocalHandoffError(
            HTTPStatus.BAD_REQUEST,
            "invalid_json_object",
            "本机 Agent 只接受结构化交接对象。",
        )
    return payload


class LocalProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._secure_sqlite_files(create_database=True)
        self._create_schema()

    def _connect(self) -> sqlite3.Connection:
        # Pre-create SQLite sidecars privately so there is no readable window
        # between SQLite opening them and the post-connect permission repair.
        self._secure_sqlite_files(create_database=True, prepare_wal=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        self._secure_sqlite_files(create_database=False)
        return connection

    def _secure_sqlite_files(
        self,
        *,
        create_database: bool,
        prepare_wal: bool = False,
    ) -> None:
        paths = (
            (self.path, create_database),
            (Path(f"{self.path}-wal"), prepare_wal),
            (Path(f"{self.path}-shm"), prepare_wal),
            (Path(f"{self.path}-journal"), False),
        )
        for path, create in paths:
            _secure_private_file(path, create=create)

    def _create_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS profile_handoff_proposals (
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
                CREATE TABLE IF NOT EXISTS local_profile_facts (
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
                    UNIQUE (
                        workspace_ref,
                        canonical_key,
                        scope,
                        scope_ref
                    )
                );
                """
            )
        self._secure_sqlite_files(create_database=True)

    def create_proposal(
        self,
        resolved: dict[str, Any],
        answers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = _normalize_answers(resolved, answers)
        handoff_jti = str(resolved["handoff_jti"])
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM profile_handoff_proposals
                WHERE handoff_jti = ?
                """,
                (handoff_jti,),
            ).fetchone()
            if existing is not None:
                return self._proposal_payload(existing, replayed=True)
            proposal_id = f"localproposal_{secrets.token_hex(12)}"
            capability = f"oralocal_{secrets.token_urlsafe(32)}"
            created_at = _utc_now()
            expires_at = min(
                int(resolved["expires_at_epoch"]),
                int(time.time()) + PROPOSAL_TTL_SECONDS,
            )
            connection.execute(
                """
                INSERT INTO profile_handoff_proposals (
                    proposal_id,
                    workspace_ref,
                    fill_task_id,
                    interaction_id,
                    handoff_jti,
                    status,
                    answers_json,
                    questions_json,
                    proposal_capability,
                    proposal_capability_hash,
                    created_at,
                    expires_at_epoch
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    resolved["workspace_ref"],
                    resolved["fill_task_id"],
                    resolved["interaction_id"],
                    handoff_jti,
                    json.dumps(normalized, ensure_ascii=False),
                    json.dumps(resolved["questions"], ensure_ascii=False),
                    capability,
                    _hash_capability(capability),
                    created_at,
                    expires_at,
                ),
            )
            record = connection.execute(
                """
                SELECT * FROM profile_handoff_proposals
                WHERE proposal_id = ?
                """,
                (proposal_id,),
            ).fetchone()
            assert record is not None
            return self._proposal_payload(record, replayed=False)

    def confirm_proposal(
        self,
        proposal_id: str,
        capability: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = connection.execute(
                """
                SELECT * FROM profile_handoff_proposals
                WHERE proposal_id = ?
                """,
                (proposal_id,),
            ).fetchone()
            if record is None:
                raise LocalHandoffError(
                    HTTPStatus.NOT_FOUND,
                    "local_proposal_not_found",
                    "本机 Agent 找不到这份待确认资料。",
                )
            if not hmac.compare_digest(
                str(record["proposal_capability_hash"]),
                _hash_capability(capability),
            ):
                raise LocalHandoffError(
                    HTTPStatus.FORBIDDEN,
                    "invalid_local_proposal_capability",
                    "这份本机资料确认凭证无效。",
                )
            if record["status"] == "confirmed":
                return self._confirmed_payload(record, replayed=True)
            if int(record["expires_at_epoch"]) <= int(time.time()):
                raise LocalHandoffError(
                    HTTPStatus.GONE,
                    "local_proposal_expired",
                    "这份待确认资料已经过期，请重新提交。",
                )
            answers = json.loads(record["answers_json"])
            now = _utc_now()
            for fact in answers:
                scope_ref = fact.get("scope_ref") or ""
                existing = connection.execute(
                    """
                    SELECT fact_id, created_at
                    FROM local_profile_facts
                    WHERE workspace_ref = ?
                      AND canonical_key = ?
                      AND scope = ?
                      AND scope_ref = ?
                    """,
                    (
                        record["workspace_ref"],
                        fact["canonical_key"],
                        fact["scope"],
                        scope_ref,
                    ),
                ).fetchone()
                fact_id = (
                    str(existing["fact_id"])
                    if existing is not None
                    else f"localfact_{secrets.token_hex(12)}"
                )
                created_at = (
                    str(existing["created_at"])
                    if existing is not None
                    else now
                )
                connection.execute(
                    """
                    INSERT INTO local_profile_facts (
                        fact_id,
                        workspace_ref,
                        canonical_key,
                        label,
                        value,
                        scope,
                        scope_ref,
                        privacy,
                        aliases_json,
                        source_question_id,
                        source_site_domain,
                        source_application_id,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (
                        workspace_ref,
                        canonical_key,
                        scope,
                        scope_ref
                    ) DO UPDATE SET
                        label = excluded.label,
                        value = excluded.value,
                        privacy = excluded.privacy,
                        aliases_json = excluded.aliases_json,
                        source_question_id = excluded.source_question_id,
                        source_site_domain = excluded.source_site_domain,
                        source_application_id = excluded.source_application_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        fact_id,
                        record["workspace_ref"],
                        fact["canonical_key"],
                        fact["label"],
                        fact["value"],
                        fact["scope"],
                        scope_ref,
                        fact["privacy"],
                        json.dumps(fact["aliases"], ensure_ascii=False),
                        fact["question_id"],
                        fact.get("site_domain"),
                        fact.get("application_id"),
                        created_at,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE profile_handoff_proposals
                SET status = 'confirmed', confirmed_at = ?
                WHERE proposal_id = ?
                """,
                (now, proposal_id),
            )
            confirmed = connection.execute(
                """
                SELECT * FROM profile_handoff_proposals
                WHERE proposal_id = ?
                """,
                (proposal_id,),
            ).fetchone()
            assert confirmed is not None
            return self._confirmed_payload(confirmed, replayed=False)

    def resolution_for(
        self,
        resolved: dict[str, Any],
    ) -> dict[str, Any]:
        questions = resolved.get("questions") or []
        fields: list[dict[str, Any]] = []
        resolved_ids: list[str] = []
        with self._connect() as connection:
            for question in questions:
                if not isinstance(question, dict):
                    continue
                fact = self._find_fact(
                    connection,
                    workspace_ref=str(resolved["workspace_ref"]),
                    question=question,
                    application_id=resolved.get("application_id"),
                    site_domain=resolved.get("site_domain"),
                )
                if fact is None:
                    continue
                question_fields = _resolved_binding_fields(
                    question,
                    str(fact["value"]),
                )
                if not question_fields:
                    continue
                resolved_ids.append(str(question["question_id"]))
                fields.extend(question_fields)
        return {
            "contract_version": "local-profile-resolution-v1",
            "workspace_ref": resolved["workspace_ref"],
            "fill_task_id": resolved["fill_task_id"],
            "resolved_question_ids": sorted(set(resolved_ids)),
            "fields": fields,
        }

    def _find_fact(
        self,
        connection: sqlite3.Connection,
        *,
        workspace_ref: str,
        question: dict[str, Any],
        application_id: Any,
        site_domain: Any,
    ) -> sqlite3.Row | None:
        canonical_key = str(
            question.get("canonical_field")
            or question.get("suggested_profile_key")
            or ""
        )
        if not canonical_key:
            return None
        scope = str(question.get("recommended_scope") or "account")
        scope_ref = (
            ""
            if scope == "account"
            else str(site_domain or "")
            if scope == "site"
            else str(application_id or "")
        )
        return connection.execute(
            """
            SELECT * FROM local_profile_facts
            WHERE workspace_ref = ?
              AND canonical_key = ?
              AND scope = ?
              AND scope_ref = ?
            """,
            (workspace_ref, canonical_key, scope, scope_ref),
        ).fetchone()

    @staticmethod
    def _proposal_payload(
        record: sqlite3.Row,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        answers = json.loads(record["answers_json"])
        return {
            "contract_version": "local-profile-proposal-v1",
            "proposal_id": record["proposal_id"],
            "proposal_capability": record["proposal_capability"],
            "status": record["status"],
            "fill_task_id": record["fill_task_id"],
            "interaction_id": record["interaction_id"],
            "expires_at_epoch": record["expires_at_epoch"],
            "items": [
                {
                    "question_id": item["question_id"],
                    "label": item["label"],
                    "value": item["value"],
                    "scope": item["scope"],
                    "privacy": item["privacy"],
                }
                for item in answers
            ],
            "replayed": replayed,
        }

    @staticmethod
    def _confirmed_payload(
        record: sqlite3.Row,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        answers = json.loads(record["answers_json"])
        return {
            "contract_version": "local-profile-confirmation-v1",
            "proposal_id": record["proposal_id"],
            "status": "confirmed",
            "confirmed_at": record["confirmed_at"],
            "saved_fact_count": len(answers),
            "resolved_question_ids": sorted(
                {item["question_id"] for item in answers}
            ),
            "replayed": replayed,
        }


def _normalize_answers(
    resolved: dict[str, Any],
    answers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    questions = {
        item["question_id"]: item
        for item in resolved.get("questions") or []
        if isinstance(item, dict) and _QUESTION_ID.fullmatch(
            str(item.get("question_id") or "")
        )
    }
    if not isinstance(answers, list) or not 1 <= len(answers) <= 100:
        raise LocalHandoffError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "invalid_profile_answers",
            "交给本机 Agent 的资料回答数量无效。",
        )
    answer_ids = [str(item.get("question_id") or "") for item in answers]
    if len(answer_ids) != len(set(answer_ids)):
        raise LocalHandoffError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "duplicate_profile_answer",
            "同一项资料不能重复回答。",
        )
    required_ids = {
        question_id
        for question_id, question in questions.items()
        if question.get("required") is True
    }
    if not required_ids.issubset(set(answer_ids)):
        raise LocalHandoffError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "required_profile_answer_missing",
            "还有必填资料没有完成。",
        )
    if set(answer_ids) - set(questions):
        raise LocalHandoffError(
            HTTPStatus.CONFLICT,
            "profile_questions_changed",
            "部分资料问题已经变化，请刷新后重新填写。",
        )
    normalized: list[dict[str, Any]] = []
    for answer in answers:
        question = questions[str(answer.get("question_id"))]
        value = str(answer.get("value") or "").strip()
        if not value or len(value) > 4000:
            raise LocalHandoffError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_profile_answer_value",
                "资料答案不能为空且不能超过 4000 个字符。",
            )
        profile_label = str(answer.get("profile_label") or "").strip()
        if not question.get("canonical_field") and not profile_label:
            raise LocalHandoffError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "profile_label_required",
                "未识别字段需要先确认资料名称。",
            )
        scope = str(question.get("recommended_scope") or "account")
        scope_ref = (
            None
            if scope == "account"
            else resolved.get("site_domain")
            if scope == "site"
            else resolved.get("application_id")
        )
        normalized.append(
            {
                "question_id": question["question_id"],
                "canonical_key": str(
                    question.get("canonical_field")
                    or question.get("suggested_profile_key")
                ),
                "label": profile_label or str(question["site_label"]),
                "value": value,
                "scope": scope,
                "scope_ref": scope_ref,
                "privacy": str(question.get("privacy") or "standard"),
                "aliases": list(question.get("aliases") or [])[:20],
                "site_domain": resolved.get("site_domain"),
                "application_id": resolved.get("application_id"),
            }
        )
    return normalized


def _resolved_binding_fields(
    question: dict[str, Any],
    answer_value: str,
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for binding in question.get("bindings") or []:
        if not isinstance(binding, dict):
            continue
        signature = binding.get("field_signature")
        selector = binding.get("selector")
        control_type = binding.get("control_type")
        if not all(
            isinstance(item, str) and item
            for item in (signature, selector, control_type)
        ):
            continue
        value = _binding_value(
            answer_value,
            binding.get("options") or [],
            str(control_type),
        )
        if value is None:
            continue
        fields.append(
            {
                "field_signature": signature,
                "selector": selector,
                "control_type": control_type,
                "site_label": question.get("site_label"),
                "profile_field": (
                    question.get("canonical_field")
                    or question.get("suggested_profile_key")
                ),
                "value": value,
                "display_value": answer_value,
                "reason": "用户已在本机确认该报名资料",
                "source": "local_confirmed_profile_fact",
                "question_id": question.get("question_id"),
                **(
                    {"interaction_kind": binding["interaction_kind"]}
                    if binding.get("interaction_kind")
                    else {}
                ),
            }
        )
    return fields


def _binding_value(
    answer: str,
    options: list[Any],
    control_type: str,
) -> str | None:
    normalized_answer = "".join(answer.split()).casefold()
    candidates = [
        option
        for option in options
        if isinstance(option, dict)
        and isinstance(option.get("value"), str)
        and isinstance(option.get("label"), str)
        and option["value"].strip()
        and option["label"].strip()
        and "请选择" not in option["label"]
    ]
    if candidates:
        for option in candidates:
            if normalized_answer in {
                "".join(option["value"].split()).casefold(),
                "".join(option["label"].split()).casefold(),
            }:
                return option["value"]
        segments = {
            "".join(segment.split()).casefold()
            for segment in re.split(r"[/／、,，;；|]", answer)
            if segment.strip()
        }
        for option in candidates:
            if (
                "".join(option["value"].split()).casefold() in segments
                or "".join(option["label"].split()).casefold() in segments
            ):
                return option["value"]
        return None if control_type in {"select", "radio", "checkbox"} else answer
    return answer


@dataclass(slots=True)
class ProductClient:
    base_url: str
    api_key: str
    account_ref: str | None = None
    actor_id: str = "agent-local"
    timeout: float = 8.0

    def access(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/workbench/access")

    def resolve_handoff(self, handoff_token: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/agent/profile-handoffs/resolve",
            {"handoff_token": handoff_token},
        )

    def profile_questions(self, fill_task_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/agent/profile-questions"
            f"?fill_task_id={quote(fill_task_id)}",
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-ORA-Actor": self.actor_id,
            "X-ORA-Surface": "mcp",
        }
        if self.account_ref:
            headers["X-ORA-Account"] = self.account_ref
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url.rstrip("/") + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
        except HTTPError as error:
            try:
                payload_error = json.loads(error.read())
                message = payload_error.get("error", {}).get("message")
            except (json.JSONDecodeError, UnicodeDecodeError):
                message = None
            raise LocalHandoffError(
                error.code,
                "product_handoff_rejected",
                message or "产品服务拒绝了本机资料交接。",
            ) from error
        except (URLError, OSError) as error:
            raise LocalHandoffError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "product_service_unreachable",
                "本机 Agent 暂时无法验证 AgentMesh360 工作区。",
            ) from error
        if not isinstance(result, dict):
            raise LocalHandoffError(
                HTTPStatus.BAD_GATEWAY,
                "invalid_product_response",
                "产品服务返回了无效的交接响应。",
            )
        return result


@dataclass(slots=True)
class LocalHandoffService:
    store: LocalProfileStore
    product: ProductClient
    configured_workspace_ref: str

    def status(self, workspace_ref: str) -> dict[str, Any]:
        return {
            "contract_version": "local-profile-handoff-v1",
            "status": (
                "ready"
                if self.configured_workspace_ref == workspace_ref
                else "workspace_mismatch"
            ),
            "workspace_ref": workspace_ref,
            "workspace_match": (
                self.configured_workspace_ref == workspace_ref
            ),
            "local_store": "ready",
            "answer_residency": "local_device",
        }

    def submit(
        self,
        *,
        handoff_token: str,
        answers: list[dict[str, Any]],
        origin: str,
    ) -> dict[str, Any]:
        resolved = self.product.resolve_handoff(handoff_token)
        if resolved.get("web_origin") != origin:
            raise LocalHandoffError(
                HTTPStatus.FORBIDDEN,
                "profile_handoff_origin_mismatch",
                "资料交接来源与工作台不一致。",
            )
        return self.store.create_proposal(resolved, answers)

    def resolution_status(
        self,
        *,
        handoff_token: str,
        origin: str,
    ) -> dict[str, Any]:
        resolved = self.product.resolve_handoff(handoff_token)
        if resolved.get("web_origin") != origin:
            raise LocalHandoffError(
                HTTPStatus.FORBIDDEN,
                "profile_handoff_origin_mismatch",
                "资料交接来源与工作台不一致。",
            )
        resolution = self.store.resolution_for(resolved)
        return {
            "contract_version": resolution["contract_version"],
            "workspace_ref": resolution["workspace_ref"],
            "fill_task_id": resolution["fill_task_id"],
            "resolved_question_ids": resolution[
                "resolved_question_ids"
            ],
        }

    def resolved_fields(
        self,
        *,
        fill_task_id: str,
        presented_api_key: str,
    ) -> dict[str, Any]:
        if not hmac.compare_digest(
            presented_api_key,
            self.product.api_key,
        ):
            raise LocalHandoffError(
                HTTPStatus.FORBIDDEN,
                "local_agent_account_mismatch",
                "浏览器扩展与本机 Agent 使用的账户不一致。",
            )
        questions = self.product.profile_questions(fill_task_id)
        access = self.product.access()
        resolved = {
            **questions,
            "workspace_ref": access.get("workspace_ref"),
        }
        return self.store.resolution_for(resolved)


def create_handler(
    service: LocalHandoffService,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentMeshLocalHandoff/1"

        def log_message(self, _: str, *args: object) -> None:
            # Never let request bodies or private values enter process logs.
            return

        def do_OPTIONS(self) -> None:
            try:
                self._validate_host()
                origin = self._origin(required=True)
                self.send_response(HTTPStatus.NO_CONTENT)
                self._cors(origin)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except LocalHandoffError as error:
                self._send_error(error)

        def do_GET(self) -> None:
            try:
                self._validate_host()
                parsed = urlsplit(self.path)
                if parsed.path != "/v1/status":
                    raise LocalHandoffError(
                        HTTPStatus.NOT_FOUND,
                        "local_route_not_found",
                        "本机 Agent 不支持该请求。",
                    )
                origin = self._origin(required=True)
                workspace_ref = (
                    parse_qs(parsed.query).get("workspace_ref") or [""]
                )[0]
                if not re.fullmatch(r"ws_[0-9a-f]{32}", workspace_ref):
                    raise LocalHandoffError(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_workspace_ref",
                        "工作台编号格式无效。",
                    )
                self._send_json(
                    HTTPStatus.OK,
                    service.status(workspace_ref),
                    origin=origin,
                )
            except LocalHandoffError as error:
                self._send_error(error)

        def do_POST(self) -> None:
            try:
                self._validate_host()
                origin = self._origin(required=True)
                payload = self._read_body()
                path = urlsplit(self.path).path
                if path == "/v1/profile-handoffs/submit":
                    result = service.submit(
                        handoff_token=str(
                            payload.get("handoff_token") or ""
                        ),
                        answers=payload.get("answers"),
                        origin=origin,
                    )
                elif path == "/v1/profile-handoffs/status":
                    result = service.resolution_status(
                        handoff_token=str(
                            payload.get("handoff_token") or ""
                        ),
                        origin=origin,
                    )
                elif path == "/v1/profile-handoffs/confirm":
                    result = service.store.confirm_proposal(
                        str(payload.get("proposal_id") or ""),
                        str(payload.get("proposal_capability") or ""),
                    )
                elif path == "/v1/fill-tasks/resolved-fields":
                    result = service.resolved_fields(
                        fill_task_id=str(
                            payload.get("fill_task_id") or ""
                        ),
                        presented_api_key=self._bearer(),
                    )
                else:
                    raise LocalHandoffError(
                        HTTPStatus.NOT_FOUND,
                        "local_route_not_found",
                        "本机 Agent 不支持该请求。",
                    )
                self._send_json(HTTPStatus.OK, result, origin=origin)
            except LocalHandoffError as error:
                self._send_error(error)

        def _validate_host(self) -> None:
            if self.headers.get("Host") not in {
                f"127.0.0.1:{LOCAL_HANDOFF_PORT}",
                f"localhost:{LOCAL_HANDOFF_PORT}",
            }:
                raise LocalHandoffError(
                    HTTPStatus.FORBIDDEN,
                    "invalid_local_host",
                    "本机 Agent 拒绝了未知主机来源。",
                )

        def _origin(self, *, required: bool) -> str:
            origin = (self.headers.get("Origin") or "").strip()
            allowed = (
                origin in ALLOWED_WEB_ORIGINS
                or bool(_CHROME_EXTENSION_ORIGIN.fullmatch(origin))
            )
            if required and not allowed:
                raise LocalHandoffError(
                    HTTPStatus.FORBIDDEN,
                    "local_origin_denied",
                    "本机 Agent 拒绝了非官方页面的资料请求。",
                )
            return origin

        def _read_body(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length") or "0"
            try:
                length = int(raw_length)
            except ValueError as error:
                raise LocalHandoffError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_content_length",
                    "本机 Agent 收到的请求长度无效。",
                ) from error
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise LocalHandoffError(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "local_request_too_large",
                    "交给本机 Agent 的资料超过大小限制。",
                )
            return _json_object(self.rfile.read(length))

        def _bearer(self) -> str:
            authorization = self.headers.get("Authorization") or ""
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token:
                raise LocalHandoffError(
                    HTTPStatus.UNAUTHORIZED,
                    "local_agent_authentication_required",
                    "浏览器扩展尚未连接本机 Agent。",
                )
            return token

        def _cors(self, origin: str) -> None:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header(
                "Access-Control-Allow-Methods",
                "GET, POST, OPTIONS",
            )
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type",
            )
            self.send_header(
                "Access-Control-Allow-Private-Network",
                "true",
            )

        def _send_json(
            self,
            status: int,
            payload: dict[str, Any],
            *,
            origin: str,
        ) -> None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(status)
            self._cors(origin)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # The caller may have closed a read-only status request while
                # the Agent was still starting. Never turn that into a noisy
                # traceback or expose response data in process logs.
                return

        def _send_error(self, error: LocalHandoffError) -> None:
            origin = (self.headers.get("Origin") or "").strip()
            if not (
                origin in ALLOWED_WEB_ORIGINS
                or _CHROME_EXTENSION_ORIGIN.fullmatch(origin)
            ):
                origin = "https://recruit.agentmesh360.com"
            self._send_json(
                error.status,
                {
                    "error": {
                        "code": error.code,
                        "message": error.message,
                    }
                },
                origin=origin,
            )

    return Handler


def serve_local_handoff(
    service: LocalHandoffService,
    *,
    host: str = LOCAL_HANDOFF_HOST,
    port: int = LOCAL_HANDOFF_PORT,
) -> None:
    if host != LOCAL_HANDOFF_HOST or port != LOCAL_HANDOFF_PORT:
        raise ValueError(
            "本机资料交接只能监听 127.0.0.1:8765。"
        )
    server = ThreadingHTTPServer((host, port), create_handler(service))
    server.serve_forever()
