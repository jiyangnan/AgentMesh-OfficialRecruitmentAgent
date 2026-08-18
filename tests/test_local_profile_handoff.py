from __future__ import annotations

import json
import http.client
import os
import sqlite3
import stat
import threading
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

import official_recruitment_agent.local_profile_handoff as handoff_module
from official_recruitment_agent.extension_identity import (
    OFFICIAL_CHROME_EXTENSION_ORIGIN,
)
from official_recruitment_agent.local_profile_handoff import (
    LocalHandoffError,
    LocalHandoffService,
    LocalProfileStore,
    ProductClient,
    _binding_value,
    create_handler,
    default_local_profile_path,
    is_local_product_url,
    open_without_redirect,
)


SENTINEL = "ORA-PRIVATE-SENTINEL-20260804"
WORKSPACE_REF = "ws_0123456789abcdef0123456789abcdef"
FILL_TASK_ID = "fill_0123456789abcdef01234567"
QUESTION_ID = "pq_aaaaaaaaaaaaaaaaaaaaaaaa"
EXTENSION_ORIGIN = OFFICIAL_CHROME_EXTENSION_ORIGIN
INSTALLATION_ID = f"orainstall_{'b' * 32}"
PAIRING_SECRET = f"orapair_{'c' * 43}"


def test_local_profile_path_uses_native_windows_local_app_data(
    tmp_path: Path,
) -> None:
    local_app_data = tmp_path / "AppData" / "Local"

    assert default_local_profile_path(
        platform_name="win32",
        home=tmp_path,
        environ={"LOCALAPPDATA": str(local_app_data)},
    ) == (
        local_app_data
        / "AgentMesh360"
        / "OfficialRecruitment"
        / "private-profile.sqlite3"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost.:8010",
        "http://127.0.0.1:8010",
        "http://[::1]:8010",
    ],
)
def test_local_product_url_accepts_unambiguous_loopback_aliases(url: str) -> None:
    assert is_local_product_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://0x7f000001:8010",
        "http://0177.0.0.1:8010",
        "http://127.1:8010",
        "http://2130706433:8010",
    ],
)
def test_local_product_url_rejects_ambiguous_numeric_hosts(url: str) -> None:
    assert is_local_product_url(url) is False


def test_no_redirect_opener_never_forwards_authorization() -> None:
    first_requests: list[str | None] = []
    redirected_requests: list[str | None] = []

    class RedirectHandler(handoff_module.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            first_requests.append(self.headers.get("Authorization"))
            self.send_response(302)
            self.send_header("Location", redirect_url)
            self.end_headers()

        def log_message(self, *_: object) -> None:
            return None

    class TargetHandler(handoff_module.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            redirected_requests.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_: object) -> None:
            return None

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    redirect_url = f"http://127.0.0.1:{target.server_port}/target"
    source = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    threads = [
        threading.Thread(target=target.serve_forever),
        threading.Thread(target=source.serve_forever),
    ]
    for thread in threads:
        thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{source.server_port}/start",
            headers={"Authorization": "Bearer must-not-follow"},
        )
        with pytest.raises(HTTPError) as captured:
            open_without_redirect(request, timeout=2)
        assert captured.value.code == 302
        assert first_requests == ["Bearer must-not-follow"]
        assert redirected_requests == []
    finally:
        source.shutdown()
        target.shutdown()
        source.server_close()
        target.server_close()
        for thread in threads:
            thread.join(timeout=2)


class FakeProductClient:
    api_key = "jobagent_live_local_handoff_test_key"
    base_url = "https://recruit.agentmesh360.com"

    def __init__(self, resolved: dict) -> None:
        self.resolved = resolved
        self.calls: list[tuple[str, object]] = []

    def access(self) -> dict:
        self.calls.append(("access", None))
        return {"workspace_ref": WORKSPACE_REF}

    def resolve_handoff(self, token: str) -> dict:
        self.calls.append(("resolve_handoff", token))
        return self.resolved

    def profile_questions(self, fill_task_id: str) -> dict:
        self.calls.append(("profile_questions", fill_task_id))
        return {
            key: value
            for key, value in self.resolved.items()
            if key
            in {
                "fill_task_id",
                "application_id",
                "site_domain",
                "questions",
            }
        }

    def create_assist_session(
        self,
        payload: dict,
        *,
        idempotency_key: str,
    ) -> dict:
        self.calls.append(
            ("create_assist_session", (payload, idempotency_key))
        )
        return {
            "result": {
                "task": {
                    "fill_task_id": FILL_TASK_ID,
                    "status": "awaiting_form",
                }
            },
            "extension_capability": "oraext_short-lived.capability",
        }


class _ProductResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps({"workspace_ref": WORKSPACE_REF}).encode("utf-8")


def test_product_client_identifies_local_agent_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _ProductResponse()

    monkeypatch.setattr(handoff_module, "open_without_redirect", fake_urlopen)
    client = ProductClient(
        base_url="http://127.0.0.1:8000",
        api_key="agentmesh_live_local-test",
        account_ref="acct-local-uat",
        actor_id="host-agent-uat",
    )

    assert client.access()["workspace_ref"] == WORKSPACE_REF
    request, timeout = requests[0]
    assert timeout == 8.0
    assert "Authorization" not in request.headers
    assert request.headers["X-ora-surface"] == "mcp"
    assert request.headers["X-ora-account"] == "acct-local-uat"
    assert request.headers["X-ora-actor"] == "host-agent-uat"


def test_product_client_sends_api_key_only_to_https_product_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _ProductResponse()

    monkeypatch.setattr(handoff_module, "open_without_redirect", fake_urlopen)
    client = ProductClient(
        base_url="https://recruit.agentmesh360.com",
        api_key="agentmesh_live_cloud-test",
        account_ref="acct-cloud-uat",
        actor_id="host-agent-uat",
    )

    assert client.access()["workspace_ref"] == WORKSPACE_REF
    request, _timeout = requests[0]
    assert request.headers["Authorization"] == (
        "Bearer agentmesh_live_cloud-test"
    )


def test_product_client_rejects_plain_http_non_loopback_without_sending_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fake_urlopen(_request, _timeout):
        nonlocal called
        called = True
        return _ProductResponse()

    monkeypatch.setattr(handoff_module, "open_without_redirect", fake_urlopen)
    client = ProductClient(
        base_url="http://localhost.attacker.example",
        api_key="agentmesh_live_must-not-leak",
        account_ref="acct-cloud-uat",
    )

    with pytest.raises(LocalHandoffError) as captured:
        client.access()

    assert captured.value.code == "insecure_product_url"
    assert called is False


@pytest.mark.parametrize(
    "base_url",
    [
        "http://0177.0.0.1:8010",
        "http://127.1:8010",
        "http://2130706433:8010",
    ],
)
def test_product_client_rejects_ambiguous_numeric_http_before_network(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    called = False

    def fake_urlopen(_request, _timeout):
        nonlocal called
        called = True
        return _ProductResponse()

    monkeypatch.setattr(handoff_module, "open_without_redirect", fake_urlopen)
    client = ProductClient(
        base_url=base_url,
        api_key="agentmesh_live_must-not-leak",
        account_ref="acct-cloud-uat",
    )

    with pytest.raises(LocalHandoffError) as captured:
        client.access()

    assert captured.value.code == "insecure_product_url"
    assert called is False


def test_product_client_rejects_ambiguous_hex_loopback_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fake_urlopen(_request, _timeout):
        nonlocal called
        called = True
        return _ProductResponse()

    monkeypatch.setattr(handoff_module, "open_without_redirect", fake_urlopen)
    client = ProductClient(
        base_url="https://0x7f000001:8010",
        api_key="agentmesh_live_cloud-test",
        account_ref="acct-cloud-uat",
    )

    with pytest.raises(LocalHandoffError) as captured:
        client.access()

    assert captured.value.code == "insecure_product_url"
    assert called is False


def _resolved(*, web_origin: str = "https://recruit.agentmesh360.com") -> dict:
    return {
        "workspace_ref": WORKSPACE_REF,
        "fill_task_id": FILL_TASK_ID,
        "interaction_id": "interaction-profile-test",
        "handoff_jti": "handoff-jti-test",
        "web_origin": web_origin,
        "expires_at_epoch": 4_102_444_800,
        "application_id": "app_0123456789abcdef01234567",
        "site_domain": "careers.example.com",
        "questions": [
            {
                "question_id": QUESTION_ID,
                "site_label": "紧急联系人",
                "canonical_field": "emergency_contact",
                "suggested_profile_key": "emergency_contact",
                "recommended_scope": "account",
                "privacy": "sensitive",
                "required": True,
                "aliases": ["紧急联系人"],
                "bindings": [
                    {
                        "field_signature": "a" * 64,
                        "selector": "#emergency-contact",
                        "control_type": "text",
                        "options": [],
                    }
                ],
            }
        ],
    }


def _service(tmp_path: Path) -> tuple[LocalHandoffService, FakeProductClient]:
    product = FakeProductClient(_resolved())
    service = LocalHandoffService(
        store=LocalProfileStore(tmp_path / "private-profile.sqlite3"),
        product=product,  # type: ignore[arg-type]
        configured_workspace_ref=WORKSPACE_REF,
        extension_pairing={
            "schema_version": 1,
            "installation_id": INSTALLATION_ID,
            "pairing_secret": PAIRING_SECRET,
            "local_agent_url": "http://127.0.0.1:8765",
        },
    )
    return service, product


def test_status_uses_startup_workspace_without_cloud_request(
    tmp_path: Path,
) -> None:
    service, product = _service(tmp_path)

    ready = service.status(WORKSPACE_REF)
    mismatch = service.status(
        "ws_ffffffffffffffffffffffffffffffff"
    )

    assert ready["status"] == "ready"
    assert ready["workspace_match"] is True
    assert ready["client_version"]
    assert ready["capabilities"] == ["resolved-required-answers-v1"]
    assert mismatch["status"] == "workspace_mismatch"
    assert mismatch["workspace_match"] is False
    assert product.calls == []


def test_private_answer_stays_local_and_extension_receives_confirmed_value(
    tmp_path: Path,
) -> None:
    service, product = _service(tmp_path)

    proposal = service.submit(
        handoff_token="orahandoff_synthetic-token",
        answers=[{"question_id": QUESTION_ID, "value": SENTINEL}],
        origin="https://recruit.agentmesh360.com",
    )

    assert proposal["status"] == "pending"
    assert proposal["items"][0]["value"] == SENTINEL
    assert SENTINEL not in json.dumps(product.calls, ensure_ascii=False)
    with sqlite3.connect(service.store.path) as connection:
        stored_answers = connection.execute(
            "SELECT answers_json FROM profile_handoff_proposals"
        ).fetchone()[0]
    assert SENTINEL in stored_answers

    confirmed = service.store.confirm_proposal(
        proposal["proposal_id"],
        proposal["proposal_capability"],
    )
    assert confirmed["status"] == "confirmed"
    assert SENTINEL not in json.dumps(confirmed, ensure_ascii=False)

    local_connection = service.connect_extension(
        installation_id=INSTALLATION_ID,
        pairing_secret=PAIRING_SECRET,
        origin=EXTENSION_ORIGIN,
    )
    resolution = service.resolved_fields(
        fill_task_id=FILL_TASK_ID,
        session_token=local_connection["session_token"],
        origin=EXTENSION_ORIGIN,
    )
    assert resolution["resolved_question_ids"] == [QUESTION_ID]
    assert resolution["fields"] == [
        {
            "field_signature": "a" * 64,
            "selector": "#emergency-contact",
            "control_type": "text",
            "site_label": "紧急联系人",
            "profile_field": "emergency_contact",
            "value": SENTINEL,
            "display_value": SENTINEL,
            "reason": "用户已在本机确认该报名资料",
            "source": "local_confirmed_profile_fact",
            "question_id": QUESTION_ID,
        }
    ]


def test_handoff_only_requires_answers_not_already_confirmed_locally(
    tmp_path: Path,
) -> None:
    service, product = _service(tmp_path)
    first = service.submit(
        handoff_token="orahandoff-existing-required-answer",
        answers=[{"question_id": QUESTION_ID, "value": SENTINEL}],
        origin="https://recruit.agentmesh360.com",
    )
    service.store.confirm_proposal(
        first["proposal_id"],
        first["proposal_capability"],
    )

    unresolved_question_id = "pq_bbbbbbbbbbbbbbbbbbbbbbbb"
    current = deepcopy(_resolved())
    current["handoff_jti"] = "handoff-jti-only-unresolved-required"
    current["questions"].append(
        {
            "question_id": unresolved_question_id,
            "site_label": "当前居住地",
            "canonical_field": "current_residence",
            "suggested_profile_key": "current_residence",
            "recommended_scope": "account",
            "privacy": "standard",
            "required": True,
            "aliases": ["当前居住地"],
            "bindings": [
                {
                    "field_signature": "b" * 64,
                    "selector": "#current-residence",
                    "control_type": "text",
                    "options": [],
                }
            ],
        }
    )
    product.resolved = current

    proposal = service.submit(
        handoff_token="orahandoff-only-unresolved-required",
        answers=[
            {
                "question_id": unresolved_question_id,
                "value": "示例城市",
            }
        ],
        origin="https://recruit.agentmesh360.com",
    )

    assert proposal["status"] == "pending"
    assert [item["question_id"] for item in proposal["items"]] == [
        unresolved_question_id
    ]


def test_handoff_still_rejects_required_answer_missing_locally_and_in_request(
    tmp_path: Path,
) -> None:
    service, product = _service(tmp_path)
    unresolved_question_id = "pq_bbbbbbbbbbbbbbbbbbbbbbbb"
    current = deepcopy(_resolved())
    current["handoff_jti"] = "handoff-jti-missing-required"
    current["questions"].append(
        {
            "question_id": unresolved_question_id,
            "site_label": "当前居住地",
            "canonical_field": "current_residence",
            "suggested_profile_key": "current_residence",
            "recommended_scope": "account",
            "privacy": "standard",
            "required": True,
            "aliases": ["当前居住地"],
            "bindings": [],
        }
    )
    product.resolved = current

    with pytest.raises(LocalHandoffError) as captured:
        service.submit(
            handoff_token="orahandoff-missing-required",
            answers=[{"question_id": QUESTION_ID, "value": SENTINEL}],
            origin="https://recruit.agentmesh360.com",
        )

    assert captured.value.code == "required_profile_answer_missing"


@pytest.mark.parametrize(
    ("answer", "expected_resolved"),
    [
        ("示例省", False),
        ("示例省示例市", True),
    ],
)
def test_iflytek_native_place_requires_city_level_local_fact(
    tmp_path: Path,
    answer: str,
    expected_resolved: bool,
) -> None:
    service, product = _service(tmp_path)
    resolved = _resolved()
    resolved["site_domain"] = "iflytek.zhiye.com"
    resolved["questions"][0].update(
        {
            "site_label": "籍贯",
            "canonical_field": "native_place",
            "suggested_profile_key": "native_place",
            "aliases": ["籍贯"],
            "bindings": [
                {
                    "field_signature": "b" * 64,
                    "selector": "#native-place",
                    "control_type": "text",
                    "options": [],
                }
            ],
        }
    )
    product.resolved = resolved
    proposal = service.submit(
        handoff_token="orahandoff_iflytek-native-place",
        answers=[{"question_id": QUESTION_ID, "value": answer}],
        origin="https://recruit.agentmesh360.com",
    )
    service.store.confirm_proposal(
        proposal["proposal_id"],
        proposal["proposal_capability"],
    )
    local_connection = service.connect_extension(
        installation_id=INSTALLATION_ID,
        pairing_secret=PAIRING_SECRET,
        origin=EXTENSION_ORIGIN,
    )

    resolution = service.resolved_fields(
        fill_task_id=FILL_TASK_ID,
        session_token=local_connection["session_token"],
        origin=EXTENSION_ORIGIN,
    )

    if expected_resolved:
        assert resolution["resolved_question_ids"] == [QUESTION_ID]
        assert resolution["fields"][0]["value"] == answer
    else:
        assert resolution["resolved_question_ids"] == []
        assert resolution["fields"] == []


def test_extension_pairing_proxies_cloud_session_without_exposing_api_key(
    tmp_path: Path,
) -> None:
    service, product = _service(tmp_path)

    connected = service.connect_extension(
        installation_id=INSTALLATION_ID,
        pairing_secret=PAIRING_SECRET,
        origin=EXTENSION_ORIGIN,
    )
    result = service.create_extension_assist_session(
        session_token=connected["session_token"],
        origin=EXTENSION_ORIGIN,
        payload={
            "page_url": "https://careers.example.com/apply",
            "page_title": "示例报名页",
            "idempotency_key": "assist-local-proxy-test-0001",
        },
    )

    assert connected["status"] == "connected"
    assert connected["server_url"] == product.base_url
    assert "api_key" not in connected
    assert product.api_key not in json.dumps(connected, ensure_ascii=False)
    assert result["extension_capability"].startswith("oraext_")
    assert product.calls[-1] == (
        "create_assist_session",
        (
            {
                "page_url": "https://careers.example.com/apply",
                "page_title": "示例报名页",
                "installation_id": INSTALLATION_ID,
                "expires_in_seconds": 900,
            },
            "assist-local-proxy-test-0001",
        ),
    )


def test_extension_pairing_rejects_wrong_secret_and_disconnect_revokes(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(LocalHandoffError) as rejected:
        service.connect_extension(
            installation_id=INSTALLATION_ID,
            pairing_secret=f"orapair_{'d' * 43}",
            origin=EXTENSION_ORIGIN,
        )
    assert rejected.value.code == "extension_pairing_rejected"

    connected = service.connect_extension(
        installation_id=INSTALLATION_ID,
        pairing_secret=PAIRING_SECRET,
        origin=EXTENSION_ORIGIN,
    )
    service.disconnect_extension(
        session_token=connected["session_token"],
        origin=EXTENSION_ORIGIN,
    )
    with pytest.raises(LocalHandoffError) as revoked:
        service.extension_status(
            session_token=connected["session_token"],
            origin=EXTENSION_ORIGIN,
        )
    assert revoked.value.code == "local_extension_session_invalid"


def test_extension_local_session_is_bound_to_chrome_extension_origin(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    connected = service.connect_extension(
        installation_id=INSTALLATION_ID,
        pairing_secret=PAIRING_SECRET,
        origin=EXTENSION_ORIGIN,
    )

    with pytest.raises(LocalHandoffError) as mismatch:
        service.extension_status(
            session_token=connected["session_token"],
            origin=f"chrome-extension://{'d' * 32}",
        )

    assert mismatch.value.code == "local_extension_session_invalid"


def test_loopback_http_connect_proxy_and_disconnect_round_trip(
    tmp_path: Path,
) -> None:
    service, product = _service(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(path: str, payload: dict, token: str | None = None):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=2,
        )
        headers = {
            "Host": "127.0.0.1:8765",
            "Origin": EXTENSION_ORIGIN,
            "Content-Type": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(
            "POST",
            path,
            body=json.dumps(payload),
            headers=headers,
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    try:
        status, connected = post(
            "/v1/extension/connect",
            {
                "installation_id": INSTALLATION_ID,
                "pairing_secret": PAIRING_SECRET,
            },
        )
        assert status == 200
        assert "api_key" not in connected

        status, assist = post(
            "/v1/extension/assist-sessions",
            {
                "page_url": "https://careers.example.com/apply",
                "page_title": "示例报名页",
                "idempotency_key": "assist-http-round-trip-0001",
            },
            connected["session_token"],
        )
        assert status == 200
        assert assist["extension_capability"].startswith("oraext_")
        assert product.calls[-1][0] == "create_assist_session"

        status, disconnected = post(
            "/v1/extension/disconnect",
            {},
            connected["session_token"],
        )
        assert status == 200
        assert disconnected["status"] == "disconnected"

        status, rejected = post(
            "/v1/extension/status",
            {},
            connected["session_token"],
        )
        assert status == 401
        assert rejected["error"]["code"] == (
            "local_extension_session_invalid"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_resolution_status_never_returns_private_values(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    proposal = service.submit(
        handoff_token="orahandoff_synthetic-token",
        answers=[{"question_id": QUESTION_ID, "value": SENTINEL}],
        origin="https://recruit.agentmesh360.com",
    )
    service.store.confirm_proposal(
        proposal["proposal_id"],
        proposal["proposal_capability"],
    )

    status = service.resolution_status(
        handoff_token="orahandoff_second-token",
        origin="https://recruit.agentmesh360.com",
    )

    assert status["contract_version"] == "local-profile-resolution-v1"
    assert status["resolved_question_ids"] == [QUESTION_ID]
    assert SENTINEL not in json.dumps(status, ensure_ascii=False)
    assert "fields" not in status
    assert "context_kind" not in status
    assert "context_id" not in status


def test_profile_foundation_fact_is_saved_locally_and_marks_metadata_resolved(
    tmp_path: Path,
) -> None:
    resolved = _resolved()
    resolved.update(
        {
            "context_kind": "profile_foundation",
            "context_id": "profile_foundation_0123456789abcdef01234567",
            "fill_task_id": "profile_foundation_0123456789abcdef01234567",
            "profile_version_id": "profile_0123456789abcdef01234567",
            "application_id": None,
            "site_domain": "个人档案",
        }
    )
    resolved["questions"] = [
        {
            "question_id": QUESTION_ID,
            "kind": "foundation_missing",
            "site_label": "籍贯 / 生源地",
            "canonical_field": "native_place",
            "suggested_profile_key": "native_place",
            "recommended_scope": "account",
            "privacy": "sensitive",
            "required": False,
            "aliases": ["籍贯", "生源地"],
            "bindings": [],
        }
    ]
    product = FakeProductClient(resolved)
    service = LocalHandoffService(
        store=LocalProfileStore(tmp_path / "private-profile.sqlite3"),
        product=product,  # type: ignore[arg-type]
        configured_workspace_ref=WORKSPACE_REF,
    )

    proposal = service.submit(
        handoff_token="orahandoff_foundation-token",
        answers=[{"question_id": QUESTION_ID, "value": SENTINEL}],
        origin="https://recruit.agentmesh360.com",
    )
    service.store.confirm_proposal(
        proposal["proposal_id"],
        proposal["proposal_capability"],
    )
    status = service.resolution_status(
        handoff_token="orahandoff_foundation-status-token",
        origin="https://recruit.agentmesh360.com",
    )

    assert status["contract_version"] == "local-profile-resolution-v2"
    assert status["context_kind"] == "profile_foundation"
    assert status["context_id"] == resolved["context_id"]
    assert status["resolved_question_ids"] == [QUESTION_ID]
    assert SENTINEL not in json.dumps(status, ensure_ascii=False)


def test_handoff_is_origin_bound_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    first = service.submit(
        handoff_token="orahandoff_synthetic-token",
        answers=[{"question_id": QUESTION_ID, "value": SENTINEL}],
        origin="https://recruit.agentmesh360.com",
    )
    replay = service.submit(
        handoff_token="orahandoff_synthetic-token",
        answers=[{"question_id": QUESTION_ID, "value": SENTINEL}],
        origin="https://recruit.agentmesh360.com",
    )

    assert replay["proposal_id"] == first["proposal_id"]
    assert replay["proposal_capability"] == first["proposal_capability"]
    assert replay["replayed"] is True

    with pytest.raises(LocalHandoffError, match="来源"):
        service.submit(
            handoff_token="orahandoff_synthetic-token",
            answers=[{"question_id": QUESTION_ID, "value": SENTINEL}],
            origin="https://untrusted.example.com",
        )


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows privacy is inherited from the user LocalAppData ACL.",
)
def test_local_profile_database_permissions_are_private(tmp_path: Path) -> None:
    store = LocalProfileStore(tmp_path / "private-profile.sqlite3")
    connection = store._connect()
    try:
        connection.execute("CREATE TABLE permission_probe (value TEXT)")
        connection.execute(
            "INSERT INTO permission_probe (value) VALUES ('synthetic')"
        )
        connection.commit()

        database_files = [
            store.path,
            Path(f"{store.path}-wal"),
            Path(f"{store.path}-shm"),
        ]
        assert all(path.exists() for path in database_files)
        assert {
            stat.S_IMODE(path.stat().st_mode) for path in database_files
        } == {0o600}

        for path in database_files:
            path.chmod(0o644)
        store._secure_sqlite_files(create_database=True)
        assert {
            stat.S_IMODE(path.stat().st_mode) for path in database_files
        } == {0o600}
    finally:
        connection.close()


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows privacy is inherited from the user LocalAppData ACL.",
)
def test_local_profile_prepares_private_wal_files_before_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalProfileStore(tmp_path / "private-profile.sqlite3")
    original_connect = sqlite3.connect
    observed_modes: dict[str, int] = {}

    def inspected_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        for suffix in ("-wal", "-shm"):
            path = Path(f"{store.path}{suffix}")
            observed_modes[suffix] = stat.S_IMODE(path.stat().st_mode)
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", inspected_connect)
    with store._connect() as connection:
        connection.execute("SELECT 1")

    assert observed_modes == {"-wal": 0o600, "-shm": 0o600}


def test_cascading_select_answer_maps_each_segment_to_platform_value() -> None:
    options = [
        {"value": "gd", "label": "广东省"},
        {"value": "bj", "label": "北京市"},
    ]
    city_options = [
        {"value": "sz", "label": "深圳市"},
        {"value": "gz", "label": "广州市"},
    ]

    assert _binding_value("广东省 / 深圳市", options, "select") == "gd"
    assert _binding_value(
        "广东省 / 深圳市",
        city_options,
        "select",
    ) == "sz"
    assert _binding_value("未知地区", options, "select") is None
