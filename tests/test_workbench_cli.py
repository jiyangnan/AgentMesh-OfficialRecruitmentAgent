from __future__ import annotations

import hashlib
import json
from pathlib import Path

import official_recruitment_agent.workbench_cli as cli_module
import pytest


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_: object):
        return None

    def read(self) -> bytes:
        return json.dumps({"counts": {"sources": 0}}).encode("utf-8")


def test_production_cli_sends_universal_key_without_spoofable_surface(
    monkeypatch,
    capsys,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response()

    monkeypatch.setattr(cli_module, "urlopen", fake_urlopen)
    code = cli_module.main(
        [
            "--base-url",
            "https://agentmesh360.example/official-recruitment",
            "--api-key",
            "agentmesh_live_test-key",
            "summary",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["counts"]["sources"] == 0
    request, timeout = requests[0]
    assert timeout == 10
    assert request.full_url == (
        "https://agentmesh360.example/official-recruitment/"
        "api/v1/workbench/summary"
    )
    assert request.headers["Authorization"] == (
        "Bearer agentmesh_live_test-key"
    )
    assert "X-ora-surface" not in request.headers


def test_transition_proposal_requires_at_least_one_evidence_ref() -> None:
    with pytest.raises(SystemExit) as error:
        cli_module.build_parser().parse_args(
            [
                "propose-transition",
                "app-synthetic",
                "--expected-version",
                "1",
                "--to-state",
                "submitted",
            ]
        )
    assert error.value.code == 2


def test_profile_schema_describes_resume_extraction_contract(capsys) -> None:
    code = cli_module.main(["profile-schema"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "official-profile-v1"
    assert "school_city" in (
        payload["fields"]["education_records"]["optional"]
    )
    assert any("不上传原始文件" in item for item in payload["instructions"])


def test_cli_help_identifies_adapter_instead_of_installed_agent(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        cli_module.build_parser().parse_args(["--help"])

    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "CLI 适配器" in output
    assert "本机 Agent CLI" not in output


def test_agent_proposes_structured_profile_without_uploading_resume(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response()

    resume = tmp_path / "standard-resume.pdf"
    resume.write_bytes(b"private resume bytes")
    fields = tmp_path / "profile.json"
    fields.write_text(
        json.dumps(
            {
                "education_records": [
                    {
                        "school_name": "示例大学",
                        "school_city": "深圳",
                        "major": "人工智能",
                    }
                ],
                "target_roles": ["产品经理"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "urlopen", fake_urlopen)

    code = cli_module.main(
        [
            "--base-url",
            "https://recruit.agentmesh360.example",
            "--api-key",
            "agentmesh_live_test-key",
            "propose-profile-import",
            "--label",
            "标准简历导入",
            "--document",
            str(resume),
            "--fields-json",
            str(fields),
            "--expected-version",
            "2",
        ]
    )

    assert code == 0
    capsys.readouterr()
    request, _ = requests[0]
    payload = json.loads(request.data)
    assert payload["target_type"] == "profile"
    assert payload["target_id"] == "profile-current"
    assert payload["action_type"] == "create_profile_version"
    assert payload["expected_version"] == 2
    imported = payload["payload"]["fields"]
    assert imported["school_city"] == "深圳"
    assert imported["_source_document"] == {
        "sha256": hashlib.sha256(resume.read_bytes()).hexdigest(),
        "suffix": ".pdf",
        "raw_document_uploaded": False,
        "parsed_by": "host_agent",
        "schema_version": "official-profile-v1",
    }
    serialized = request.data.decode("utf-8")
    assert str(resume) not in serialized
    assert "private resume bytes" not in serialized


def test_configure_persists_key_with_private_permissions_without_printing_it(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "official-recruitment.json"
    monkeypatch.setenv("ORA_CONFIG_PATH", str(config_path))
    key = "agentmesh_live_0123456789abcdef"

    code = cli_module.main(
        [
            "configure",
            "--server-url",
            "https://recruit.agentmesh360.com/",
            "--key",
            key,
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert key not in output
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["api_key"] == key
    assert payload["base_url"] == "https://recruit.agentmesh360.com"
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_doctor_uses_saved_account_and_reports_profile_readiness(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "official-recruitment.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_url": "https://recruit.agentmesh360.com",
                "api_key": "agentmesh_live_0123456789abcdef",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORA_CONFIG_PATH", str(config_path))
    requests = []

    class DoctorResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_: object):
            return None

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/api/v1/profiles"):
            return DoctorResponse(
                [
                    {
                        "profile_version_id": "profile-current",
                        "version_number": 3,
                        "label": "标准简历",
                        "is_current": True,
                    }
                ]
            )
        return DoctorResponse({"counts": {"active_applications": 2}})

    monkeypatch.setattr(cli_module, "urlopen", fake_urlopen)

    code = cli_module.main(["doctor"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ready"
    assert payload["current_profile"]["version_number"] == 3
    assert len(requests) == 2
    assert all(
        request.headers["Authorization"].startswith("Bearer ")
        for request in requests
    )
