from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

import pytest

import official_recruitment_agent.local_profile_handoff as handoff_module
from official_recruitment_agent.local_profile_handoff import (
    LocalHandoffError,
    LocalHandoffService,
    LocalProfileStore,
    ProductClient,
    _binding_value,
)


SENTINEL = "ORA-PRIVATE-SENTINEL-20260804"
WORKSPACE_REF = "ws_0123456789abcdef0123456789abcdef"
FILL_TASK_ID = "fill_0123456789abcdef01234567"
QUESTION_ID = "pq_aaaaaaaaaaaaaaaaaaaaaaaa"


class FakeProductClient:
    api_key = "jobagent_live_local_handoff_test_key"

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

    monkeypatch.setattr(handoff_module, "urlopen", fake_urlopen)
    client = ProductClient(
        base_url="http://127.0.0.1:8000",
        api_key="agentmesh_live_local-test",
        account_ref="acct-local-uat",
        actor_id="host-agent-uat",
    )

    assert client.access()["workspace_ref"] == WORKSPACE_REF
    request, timeout = requests[0]
    assert timeout == 8.0
    assert request.headers["Authorization"] == (
        "Bearer agentmesh_live_local-test"
    )
    assert request.headers["X-ora-surface"] == "mcp"
    assert request.headers["X-ora-account"] == "acct-local-uat"
    assert request.headers["X-ora-actor"] == "host-agent-uat"


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

    resolution = service.resolved_fields(
        fill_task_id=FILL_TASK_ID,
        presented_api_key=product.api_key,
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

    assert status["resolved_question_ids"] == [QUESTION_ID]
    assert SENTINEL not in json.dumps(status, ensure_ascii=False)
    assert "fields" not in status


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


def test_local_profile_database_permissions_are_private(tmp_path: Path) -> None:
    store = LocalProfileStore(tmp_path / "private-profile.sqlite3")
    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode == 0o600


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
