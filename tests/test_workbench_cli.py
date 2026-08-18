from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path

import official_recruitment_agent.workbench_cli as cli_module
import pytest


def test_cli_reconfigures_redirected_windows_output_as_utf8(
    monkeypatch,
) -> None:
    raw_output = io.BytesIO()
    redirected_output = io.TextIOWrapper(
        raw_output,
        encoding="cp1252",
    )
    monkeypatch.setattr(sys, "stdout", redirected_output)

    cli_module._ensure_utf8_standard_streams()
    print("在 Windows 输出中文")
    redirected_output.flush()

    assert raw_output.getvalue().decode("utf-8").splitlines() == [
        "在 Windows 输出中文"
    ]


def test_config_path_uses_native_windows_local_app_data(tmp_path: Path) -> None:
    local_app_data = tmp_path / "AppData" / "Local"

    assert cli_module._config_path(
        platform_name="win32",
        home=tmp_path,
        environ={"LOCALAPPDATA": str(local_app_data)},
    ) == (
        local_app_data
        / "AgentMesh360"
        / "OfficialRecruitment"
        / "config.json"
    )


def _test_workspace_ref(character: str) -> str:
    return f"ws_{character * 32}"


class _Response:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = (
            {"counts": {"sources": 0}}
            if payload is None
            else payload
        )

    def __enter__(self):
        return self

    def __exit__(self, *_: object):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_production_cli_sends_universal_key_without_spoofable_surface(
    monkeypatch,
    capsys,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response()

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)
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


def test_local_cli_never_sends_configured_api_key(
    monkeypatch,
    capsys,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response(
            {
                "contract_version": "workbench-data-inventory-v1",
                "categories": [],
                "total_record_count": 0,
            }
        )

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)
    code = cli_module.main(
        [
            "--base-url",
            "http://127.0.0.1:18013",
            "--account",
            "acct-local-test",
            "--actor",
            "agent-local-test",
            "--api-key",
            "agentmesh_live_must-not-leave-device",
            "data",
            "inventory",
        ]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["total_record_count"] == 0
    request, timeout = requests[0]
    assert timeout == 10
    assert request.headers.get("Authorization") is None
    assert request.headers["X-ora-account"] == "acct-local-test"
    assert request.headers["X-ora-actor"] == "agent-local-test"
    assert request.headers["X-ora-surface"] == "mcp"


def test_cli_rejects_plain_http_non_loopback_without_sending_key(
    monkeypatch,
    capsys,
) -> None:
    called = False

    def fake_urlopen(_request, _timeout):
        nonlocal called
        called = True
        return _Response()

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)
    code = cli_module.main(
        [
            "--base-url",
            "http://localhost.attacker.example",
            "--api-key",
            "agentmesh_live_must-not-leak",
            "summary",
        ]
    )

    assert code == 2
    assert called is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "workbench_unreachable"
    assert "必须使用 HTTPS" in payload["error"]["message"]


@pytest.mark.parametrize(
    "base_url",
    [
        "http://0177.0.0.1:8010",
        "http://127.1:8010",
        "http://2130706433:8010",
    ],
)
def test_cli_rejects_ambiguous_numeric_http_without_network(
    monkeypatch,
    capsys,
    base_url,
) -> None:
    called = False

    def fake_urlopen(_request, _timeout):
        nonlocal called
        called = True
        return _Response()

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)
    code = cli_module.main(
        [
            "--base-url",
            base_url,
            "--api-key",
            "agentmesh_live_must-not-leak",
            "summary",
        ]
    )

    assert code == 2
    assert called is False
    assert "必须使用 HTTPS" in json.loads(capsys.readouterr().out)[
        "error"
    ]["message"]


def test_data_inventory_and_preview_use_the_shared_server_contract(
    monkeypatch,
    capsys,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        if request.full_url.endswith("item_limit=25"):
            return _Response(
                {
                    "contract_version": "workbench-data-inventory-v1",
                    "categories": [],
                    "total_record_count": 0,
                }
            )
        return _Response(
            {
                "contract_version": "workbench-data-deletion-v2",
                "deletion_id": "delete-test",
                "selected_items": [
                    {
                        "category": "profiles",
                        "item_id": "profile-test",
                    }
                ],
            }
        )

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)
    common = [
        "--base-url",
        "https://recruit.agentmesh360.test",
        "--api-key",
        "agentmesh_live_test-key",
        "data",
    ]
    assert cli_module.main([*common, "inventory", "--item-limit", "25"]) == 0
    inventory = json.loads(capsys.readouterr().out)
    assert inventory["contract_version"] == "workbench-data-inventory-v1"
    assert (
        cli_module.main(
            [
                *common,
                "delete-preview",
                "--item",
                "profiles:profile-test",
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["selected_items"][0]["item_id"] == "profile-test"
    assert requests[0][0].method == "GET"
    assert requests[1][0].method == "POST"
    assert json.loads(requests[1][0].data) == {
        "items": [
            {"category": "profiles", "item_id": "profile-test"}
        ]
    }


def test_data_delete_confirm_forwards_exact_preview_binding(
    monkeypatch,
    capsys,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response(
            {
                "contract_version": "workbench-data-deletion-v2",
                "receipt_id": "receipt-test",
                "replayed": False,
            }
        )

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)
    digest = "a" * 64
    code = cli_module.main(
        [
            "--base-url",
            "https://recruit.agentmesh360.test",
            "--api-key",
            "agentmesh_live_test-key",
            "data",
            "delete-confirm",
            "--deletion-id",
            "delete_1234567890",
            "--snapshot-digest",
            digest,
            "--confirmation-code",
            "DELETE-12AB34CD",
        ]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["receipt_id"] == "receipt-test"
    request = requests[0][0]
    assert request.full_url.endswith(
        "/api/v1/workbench/data-deletions/delete_1234567890/confirm"
    )
    assert json.loads(request.data) == {
        "snapshot_digest": digest,
        "confirmation_code": "DELETE-12AB34CD",
    }


def test_data_reconcile_billing_forwards_exact_preview_binding(
    monkeypatch,
    capsys,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response(
            {
                "contract_version": (
                    "workbench-billing-reconciliation-v1"
                ),
                "deletion_id": "delete_1234567890",
                "status": "reconciled",
                "preview_invalidated": True,
            }
        )

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)
    digest = "c" * 64
    code = cli_module.main(
        [
            "--base-url",
            "https://recruit.agentmesh360.test",
            "--api-key",
            "agentmesh_live_test-key",
            "data",
            "reconcile-billing",
            "--deletion-id",
            "delete_1234567890",
            "--snapshot-digest",
            digest,
            "--confirmation-code",
            "DELETE-12AB34CD",
        ]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "reconciled"
    request = requests[0][0]
    assert request.full_url.endswith(
        "/api/v1/workbench/data-deletions/"
        "delete_1234567890/reconcile-billing"
    )
    assert json.loads(request.data) == {
        "snapshot_digest": digest,
        "confirmation_code": "DELETE-12AB34CD",
    }


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
    assert "work" in payload["fields"]["experience_records"]["kind"]
    assert "proficiency" in (
        payload["fields"]["skill_records"]["optional"]
    )
    assert payload["fields"]["supplemental_facts"]["scope"] == [
        "account",
        "site",
        "application",
    ]
    assert payload["foundation_catalog"]["initial_dimension_count"] >= 30
    assert any(
        item["key"] == "id_number"
        and item["privacy"] == "sensitive"
        for item in payload["foundation_catalog"]["dimensions"]
    )
    assert any("不上传原始文件" in item for item in payload["instructions"])


def test_profile_foundation_reads_metadata_without_answer_submission(
    monkeypatch,
    capsys,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response(
            {
                "contract_version": "profile-foundation-v1",
                "catalog_version": "official-profile-dimensions-v1",
                "profile_version_id": "profile_0123456789abcdef01234567",
                "missing_count": 2,
                "questions": [
                    {
                        "question_id": "pq_" + "a" * 24,
                        "site_label": "证件号码",
                    }
                ],
            }
        )

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)
    code = cli_module.main(
        [
            "--base-url",
            "https://recruit.agentmesh360.example",
            "--api-key",
            "agentmesh_live_test-key",
            "profile-foundation",
        ]
    )

    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["missing_count"] == 2
    request, _ = requests[0]
    assert request.method == "GET"
    assert request.full_url.endswith("/api/v1/agent/profile-foundation")
    assert request.data is None


def test_cli_help_identifies_adapter_instead_of_installed_agent(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        cli_module.build_parser().parse_args(["--help"])

    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "CLI 适配器" in output
    assert "本机 Agent CLI" not in output


def test_extension_setup_exposes_store_and_direct_download_channels(
    capsys,
) -> None:
    code = cli_module.main(
        [
            "--base-url",
            "https://recruit.agentmesh360.test",
            "extension-setup",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["chrome_web_store_url"].endswith(
        "fbgfhigphgmacnhgeomdjemfomhnjaai"
    )
    assert payload["download_url"] == (
        "https://recruit.agentmesh360.test/downloads/"
        "agentmesh-officialrecruitment-extension.zip"
    )
    assert payload["recommended_command"] == (
        "ora-workbench extension host install"
    )


def test_extension_host_install_is_a_separate_idempotent_command(
    monkeypatch,
    capsys,
) -> None:
    calls = []
    monkeypatch.setattr(
        cli_module,
        "install_native_messaging_host",
        lambda *, extension_root: (
            calls.append(extension_root)
            or {
                "status": "ready",
                "extension_id": "fbgfhigphgmacnhgeomdjemfomhnjaai",
            }
        ),
    )

    code = cli_module.main(["extension", "host", "install"])

    assert code == 0
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "ready"


def test_agent_prepares_extension_without_claiming_silent_chrome_install(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    extension_root = tmp_path / "extension"
    calls = []

    def fake_prepare(base_url, *, extension_root, force):
        calls.append((base_url, extension_root, force))
        return {
            "status": "ready",
            "healthy": True,
            "extension_version": "0.6.5",
            "install_directory": str(extension_root),
            "changed": True,
        }

    monkeypatch.setattr(cli_module, "prepare_extension", fake_prepare)
    monkeypatch.setattr(
        cli_module,
        "ensure_extension_pairing",
        lambda _root: {"installation_id": f"orainstall_{'a' * 32}"},
    )
    monkeypatch.setattr(
        cli_module,
        "install_native_messaging_host",
        lambda *, extension_root: {
            "status": "ready",
            "installation_id": f"orainstall_{'a' * 32}",
            "manifest_path": str(tmp_path / "native-host.json"),
        },
    )
    monkeypatch.setattr(
        cli_module,
        "_start_profile_handoff",
        lambda _args, *, extension_root: {
            "status": "ready",
            "extension_connection_supported": True,
            "install_directory": str(extension_root),
        },
    )

    code = cli_module.main(
        [
            "--base-url",
            "https://recruit.agentmesh360.test",
            "--api-key",
            "agentmesh_live_test-key",
            "extension",
            "prepare",
            "--install-dir",
            str(extension_root),
            "--no-open",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == [
        (
            "https://recruit.agentmesh360.test",
            extension_root,
            False,
        )
    ]
    assert payload["status"] == "ready"
    assert payload["local_agent"]["extension_connection_supported"] is True
    assert "安装完成" not in json.dumps(payload, ensure_ascii=False)
    assert payload["manual_steps"] == [
        "在 Chrome 扩展管理页开启开发者模式。",
        "点击加载已解压的扩展程序。",
        f"选择目录：{extension_root}",
    ]


def test_local_extension_prepare_starts_handoff_without_api_key(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    extension_root = tmp_path / "extension"
    starts = []
    monkeypatch.setenv(
        "ORA_CONFIG_PATH",
        str(tmp_path / "missing-config.json"),
    )
    monkeypatch.delenv("AGENTMESH_API_KEY", raising=False)
    monkeypatch.setattr(
        cli_module,
        "prepare_extension",
        lambda *_args, **_kwargs: {"status": "ready"},
    )
    monkeypatch.setattr(
        cli_module,
        "ensure_extension_pairing",
        lambda _root: {"installation_id": f"orainstall_{'a' * 32}"},
    )
    monkeypatch.setattr(
        cli_module,
        "install_native_messaging_host",
        lambda **_kwargs: {"status": "ready"},
    )
    monkeypatch.setattr(
        cli_module,
        "_start_profile_handoff",
        lambda args, *, extension_root: (
            starts.append((args.api_key, extension_root))
            or {"status": "ready"}
        ),
    )

    code = cli_module.main(
        [
            "--base-url",
            "http://localhost.:8010",
            "--account",
            "acct-local-extension",
            "extension",
            "prepare",
            "--install-dir",
            str(extension_root),
            "--no-open",
        ]
    )

    assert code == 0
    assert starts == [(None, extension_root)]
    assert json.loads(capsys.readouterr().out)["local_agent"]["status"] == (
        "ready"
    )


def test_local_extension_prepare_survives_download_only_server(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    extension_root = tmp_path / "extension"
    monkeypatch.setattr(
        cli_module,
        "prepare_extension",
        lambda *_args, **_kwargs: {"status": "ready", "healthy": True},
    )
    monkeypatch.setattr(
        cli_module,
        "ensure_extension_pairing",
        lambda _root: {"installation_id": f"orainstall_{'a' * 32}"},
    )
    monkeypatch.setattr(
        cli_module,
        "install_native_messaging_host",
        lambda **_kwargs: {"status": "ready"},
    )
    monkeypatch.setattr(
        cli_module,
        "_start_profile_handoff",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            cli_module.LocalHandoffError(
                404,
                "product_handoff_rejected",
                "download-only server",
            )
        ),
    )

    code = cli_module.main(
        [
            "--base-url",
            "http://127.0.0.1:18127",
            "extension",
            "prepare",
            "--install-dir",
            str(extension_root),
            "--no-open",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ready"
    assert payload["local_agent"] == {
        "status": "not_connected",
        "reason": "product_handoff_rejected",
        "message": (
            "扩展已准备完成；当前本机地址未运行工作台，"
            "因此没有启动本机 Agent 连接。"
        ),
    }


def test_extension_repair_forces_verified_redownload(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ORA_CONFIG_PATH", str(tmp_path / "missing.json"))
    extension_root = tmp_path / "extension"
    calls = []

    def fake_prepare(base_url, *, extension_root, force):
        calls.append((base_url, extension_root, force))
        return {"status": "ready", "healthy": True, "changed": True}

    monkeypatch.setattr(cli_module, "prepare_extension", fake_prepare)
    monkeypatch.setattr(
        cli_module,
        "ensure_extension_pairing",
        lambda _root: {"installation_id": f"orainstall_{'a' * 32}"},
    )
    monkeypatch.setattr(
        cli_module,
        "install_native_messaging_host",
        lambda *, extension_root: {
            "status": "ready",
            "installation_id": f"orainstall_{'a' * 32}",
        },
    )

    code = cli_module.main(
        [
            "extension",
            "repair",
            "--install-dir",
            str(extension_root),
            "--no-open",
        ]
    )

    assert code == 0
    assert calls[0][2] is True
    assert json.loads(capsys.readouterr().out)["status"] == "ready"


def test_host_agent_can_start_local_profile_handoff_without_user_shell_work(
    monkeypatch,
    capsys,
) -> None:
    calls = []

    def fake_start(args):
        calls.append(args.profile_handoff_command)
        return {
            "status": "ready",
            "workspace_match": True,
            "answer_residency": "local_device",
            "started": True,
        }

    monkeypatch.setattr(cli_module, "_start_profile_handoff", fake_start)
    monkeypatch.setattr(
        cli_module,
        "native_messaging_host_status",
        lambda: {"status": "ready", "ready": True},
    )

    code = cli_module.main(
        [
            "--api-key",
            "agentmesh_live_test-key",
            "profile-handoff",
            "start",
        ]
    )

    assert code == 0
    assert calls == ["start"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ready"
    assert payload["answer_residency"] == "local_device"
    assert payload["native_host"]["ready"] is True


def test_profile_handoff_start_repairs_stale_native_host_registration(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    calls = []
    extension_root = tmp_path / "extension"
    monkeypatch.setattr(
        cli_module,
        "default_extension_root",
        lambda: extension_root,
    )
    monkeypatch.setattr(
        cli_module,
        "native_messaging_host_status",
        lambda: {"status": "repair_required", "ready": False},
    )
    monkeypatch.setattr(
        cli_module,
        "install_native_messaging_host",
        lambda *, extension_root: (
            calls.append(extension_root)
            or {"status": "ready", "ready": True}
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_start_profile_handoff",
        lambda _args: {
            "status": "ready",
            "workspace_match": True,
            "answer_residency": "local_device",
            "started": False,
        },
    )

    code = cli_module.main(
        [
            "--api-key",
            "agentmesh_live_test-key",
            "profile-handoff",
            "start",
        ]
    )

    assert code == 0
    assert calls == [extension_root]
    payload = json.loads(capsys.readouterr().out)
    assert payload["native_host"] == {
        "status": "ready",
        "ready": True,
    }


def test_handoff_start_validates_cloud_once_then_polls_only_loopback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = {"cloud": 0, "local": 0, "spawn": 0}
    workspace_ref = _test_workspace_ref("a")

    def fake_product_and_workspace(_args):
        calls["cloud"] += 1
        return object(), workspace_ref

    def fake_query(_args, queried_workspace_ref):
        calls["local"] += 1
        assert queried_workspace_ref == workspace_ref
        if calls["local"] == 1:
            raise cli_module.URLError("service not running")
        return {
            "status": "ready",
            "workspace_match": True,
            "answer_residency": "local_device",
            "capabilities": ["resolved-required-answers-v1"],
            "extension_installation_id": f"orainstall_{'a' * 32}",
        }

    class FakeProcess:
        pid = 43210

        @staticmethod
        def poll():
            return None

    def fake_popen(*_args, **_kwargs):
        calls["spawn"] += 1
        return FakeProcess()

    monkeypatch.setattr(
        cli_module,
        "_product_and_workspace",
        fake_product_and_workspace,
    )
    monkeypatch.setattr(
        cli_module,
        "_query_local_handoff",
        fake_query,
    )
    monkeypatch.setattr(cli_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        cli_module,
        "_config_path",
        lambda: tmp_path / "official-recruitment.json",
    )
    monkeypatch.setattr(
        cli_module,
        "ensure_extension_pairing",
        lambda _root: {"installation_id": f"orainstall_{'a' * 32}"},
    )
    args = cli_module.argparse.Namespace(
        base_url="https://recruit.agentmesh360.com",
        api_key="agentmesh_live_test-key",
        account=None,
        actor=None,
    )

    result = cli_module._start_profile_handoff(args)

    assert result["status"] == "ready"
    assert result["started"] is True
    assert result["pid"] == 43210
    assert calls == {"cloud": 1, "local": 2, "spawn": 1}


def test_local_product_workspace_discovery_does_not_require_or_forward_key(
    monkeypatch,
) -> None:
    captured = {}

    class FakeProductClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        @staticmethod
        def access():
            return {"workspace_ref": _test_workspace_ref("1")}

    monkeypatch.setattr(cli_module, "ProductClient", FakeProductClient)
    args = cli_module.argparse.Namespace(
        base_url="http://127.0.0.1:8010",
        api_key=None,
        account="acct-local-workspace",
        actor="agent-local-workspace",
    )

    _product, workspace_ref = cli_module._product_and_workspace(args)

    assert workspace_ref == _test_workspace_ref("1")
    assert captured["api_key"] is None
    assert captured["account_ref"] == "acct-local-workspace"


def test_local_handoff_child_starts_without_key_and_scrubs_parent_key(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_ref = _test_workspace_ref("2")
    captured_environment = {}
    queries = 0

    monkeypatch.setenv("AGENTMESH_API_KEY", "parent-key-must-not-propagate")
    monkeypatch.setattr(
        cli_module,
        "_product_and_workspace",
        lambda _args: (object(), workspace_ref),
    )
    monkeypatch.setattr(
        cli_module,
        "ensure_extension_pairing",
        lambda _root: {"installation_id": f"orainstall_{'a' * 32}"},
    )

    def fake_query(_args, _workspace_ref):
        nonlocal queries
        queries += 1
        if queries == 1:
            raise OSError("not running")
        return {
            "status": "ready",
            "extension_installation_id": f"orainstall_{'a' * 32}",
            "capabilities": ["resolved-required-answers-v1"],
        }

    class FakeProcess:
        pid = 60001

        @staticmethod
        def poll():
            return None

    def fake_popen(*_args, **kwargs):
        captured_environment.update(kwargs["env"])
        return FakeProcess()

    monkeypatch.setattr(cli_module, "_query_local_handoff", fake_query)
    monkeypatch.setattr(cli_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        cli_module,
        "_config_path",
        lambda: tmp_path / "official-recruitment.json",
    )
    args = cli_module.argparse.Namespace(
        base_url="http://localhost.:8010",
        api_key=None,
        account="acct-local-no-key",
        actor="agent-local-no-key",
    )

    result = cli_module._start_profile_handoff(args)

    assert result["started"] is True
    assert "AGENTMESH_API_KEY" not in captured_environment
    assert captured_environment["ORA_WORKBENCH_URL"] == (
        "http://localhost.:8010"
    )


def test_extension_update_restarts_outdated_local_handoff(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_ref = _test_workspace_ref("b")
    installation_id = f"orainstall_{'a' * 32}"
    calls = {"query": 0, "stop": 0, "spawn": 0}

    monkeypatch.setattr(
        cli_module,
        "_product_and_workspace",
        lambda _args: (object(), workspace_ref),
    )
    monkeypatch.setattr(
        cli_module,
        "ensure_extension_pairing",
        lambda _root: {"installation_id": installation_id},
    )

    def fake_query(_args, _workspace_ref):
        calls["query"] += 1
        if calls["query"] == 1:
            return {
                "status": "ready",
                "workspace_match": True,
                "extension_connection_supported": False,
                "extension_installation_id": None,
            }
        return {
            "status": "ready",
            "workspace_match": True,
            "extension_connection_supported": True,
            "extension_installation_id": installation_id,
            "capabilities": ["resolved-required-answers-v1"],
        }

    monkeypatch.setattr(cli_module, "_query_local_handoff", fake_query)
    monkeypatch.setattr(
        cli_module,
        "_stop_outdated_local_handoff",
        lambda: calls.__setitem__("stop", calls["stop"] + 1),
    )

    class FakeProcess:
        pid = 54321

        @staticmethod
        def poll():
            return None

    def fake_popen(*_args, **_kwargs):
        calls["spawn"] += 1
        return FakeProcess()

    monkeypatch.setattr(cli_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        cli_module,
        "_config_path",
        lambda: tmp_path / "official-recruitment.json",
    )
    args = cli_module.argparse.Namespace(
        base_url="https://recruit.agentmesh360.com",
        api_key="agentmesh_live_test-key",
        account="acct-test",
        actor="agent-test",
    )

    result = cli_module._start_profile_handoff(
        args,
        extension_root=tmp_path / "extension",
    )

    assert result["started"] is True
    assert result["extension_installation_id"] == installation_id
    assert calls == {"query": 2, "stop": 1, "spawn": 1}


def test_client_update_restarts_same_extension_identity_without_capability(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_ref = _test_workspace_ref("c")
    installation_id = f"orainstall_{'a' * 32}"
    calls = {"query": 0, "stop": 0, "spawn": 0}

    monkeypatch.setattr(
        cli_module,
        "_product_and_workspace",
        lambda _args: (object(), workspace_ref),
    )
    monkeypatch.setattr(
        cli_module,
        "ensure_extension_pairing",
        lambda _root: {"installation_id": installation_id},
    )

    def fake_query(_args, _workspace_ref):
        calls["query"] += 1
        if calls["query"] == 1:
            return {
                "status": "ready",
                "workspace_match": True,
                "extension_connection_supported": True,
                "extension_installation_id": installation_id,
            }
        return {
            "status": "ready",
            "workspace_match": True,
            "extension_connection_supported": True,
            "extension_installation_id": installation_id,
            "capabilities": ["resolved-required-answers-v1"],
        }

    monkeypatch.setattr(cli_module, "_query_local_handoff", fake_query)
    monkeypatch.setattr(
        cli_module,
        "_stop_outdated_local_handoff",
        lambda: calls.__setitem__("stop", calls["stop"] + 1),
    )

    class FakeProcess:
        pid = 54322

        @staticmethod
        def poll():
            return None

    def fake_popen(*_args, **_kwargs):
        calls["spawn"] += 1
        return FakeProcess()

    monkeypatch.setattr(cli_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        cli_module,
        "_config_path",
        lambda: tmp_path / "official-recruitment.json",
    )
    args = cli_module.argparse.Namespace(
        base_url="https://recruit.agentmesh360.com",
        api_key="agentmesh_live_test-key",
        account="acct-test",
        actor="agent-test",
    )

    result = cli_module._start_profile_handoff(args)

    assert result["started"] is True
    assert result["capabilities"] == [
        "resolved-required-answers-v1"
    ]
    assert calls == {"query": 2, "stop": 1, "spawn": 1}


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
    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)

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
    assert payload["expires_in_seconds"] == 7 * 24 * 60 * 60
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


def test_agent_reads_questions_for_a_specific_fill_task(
    monkeypatch,
    capsys,
) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response(
            {
                "question_count": 1,
                "questions": [
                    {
                        "question_id": "pq_" + "a" * 24,
                        "site_label": "出生日期",
                    }
                ],
                "agent_gate": {
                    "state": "questions_required",
                    "blocking": True,
                    "must_present_questions": True,
                },
                "interaction": {
                    "event": "interaction_required",
                    "protocol": "agentmesh360.interaction_required",
                    "preferred_presentation": "card",
                    "fallback_text": "请补充出生日期。",
                },
                "host_presentations": {
                    "preferred": "native_card",
                },
            }
        )

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)
    code = cli_module.main(
        [
            "--base-url",
            "https://recruit.agentmesh360.example",
            "--api-key",
            "agentmesh_live_test-key",
            "profile-questions",
            "--fill-task-id",
            "fill_0123456789abcdef01234567",
        ]
    )

    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["agent_gate"]["blocking"] is True
    assert output["agent_gate"]["must_present_questions"] is True
    assert output["questions"][0]["site_label"] == "出生日期"
    assert output["interaction"]["preferred_presentation"] == "card"
    assert output["host_presentations"]["preferred"] == "native_card"
    request, _ = requests[0]
    assert request.method == "GET"
    assert request.full_url.endswith(
        "/api/v1/agent/profile-questions?"
        "fill_task_id=fill_0123456789abcdef01234567"
    )


def test_cli_does_not_offer_cloud_profile_answer_submission() -> None:
    parser = cli_module.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["propose-profile-completion"])


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
    response = json.loads(output)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["api_key"] == key
    assert payload["base_url"] == "https://recruit.agentmesh360.com"
    if os.name == "nt":
        assert response["permissions"] == "user_profile_acl"
    else:
        assert response["permissions"] == "0600"
        assert config_path.stat().st_mode & 0o777 == 0o600


def test_configure_local_workspace_removes_stale_cloud_key(
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
                "api_key": "agentmesh_live_stale-cloud-key",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORA_CONFIG_PATH", str(config_path))

    code = cli_module.main(
        [
            "configure",
            "--server-url",
            "http://[::1]:8010/",
        ]
    )

    assert code == 0
    response = json.loads(capsys.readouterr().out)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert response["api_key_configured"] is False
    assert payload == {
        "schema_version": 1,
        "base_url": "http://[::1]:8010",
    }


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
    continuity_path = tmp_path / "continuity.json"
    monkeypatch.setenv("ORA_CONTINUITY_PATH", str(continuity_path))
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
        if request.full_url.endswith("/api/v1/workbench/access"):
            return DoctorResponse(
                {
                    "access": "granted",
                    "workspace_ref": _test_workspace_ref("a"),
                    "profile_data_residency": "local_device",
                    "profile_input_enabled": True,
                }
            )
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

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)

    code = cli_module.main(["doctor"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ready"
    assert payload["continuity_status"] == "healthy"
    assert payload["recovery_required"] is False
    assert payload["current_profile"]["version_number"] == 3
    assert len(requests) == 3
    assert all(
        request.headers["Authorization"].startswith("Bearer ")
        for request in requests
    )
    marker_text = continuity_path.read_text(encoding="utf-8")
    marker = json.loads(marker_text)
    if os.name != "nt":
        assert continuity_path.stat().st_mode & 0o777 == 0o600
    assert marker["schema_version"] == 1
    assert marker["workspaces"][_test_workspace_ref("a")][
        "confirmed_profile_seen"
    ] is True
    assert len(
        marker["workspaces"][_test_workspace_ref("a")][
            "profile_fingerprint"
        ]
    ) == 64
    assert set(marker["workspaces"][_test_workspace_ref("a")]) == {
        "confirmed_profile_seen",
        "profile_fingerprint",
    }
    assert "agentmesh_live_0123456789abcdef" not in marker_text
    assert "profile-current" not in marker_text
    assert "标准简历" not in marker_text


def test_doctor_reports_uninitialized_for_genuine_first_use(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    continuity_path = tmp_path / "continuity.json"
    monkeypatch.setenv("ORA_CONTINUITY_PATH", str(continuity_path))
    monkeypatch.setenv("AGENTMESH_API_KEY", "agentmesh_live_test")

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/api/v1/workbench/access"):
            return _Response(
                {
                    "access": "granted",
                    "workspace_ref": _test_workspace_ref("b"),
                    "profile_data_residency": "local_device",
                    "profile_input_enabled": True,
                }
            )
        if request.full_url.endswith("/api/v1/profiles"):
            return _Response([])
        return _Response({"counts": {}})

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)

    code = cli_module.main(["doctor"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "needs_profile"
    assert payload["continuity_status"] == "uninitialized"
    assert payload["recovery_required"] is False
    assert payload["interaction_required"] is None
    assert not continuity_path.exists()


def test_doctor_detects_profile_loss_after_database_is_recreated_empty(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    continuity_path = tmp_path / "continuity.json"
    monkeypatch.setenv("ORA_CONTINUITY_PATH", str(continuity_path))
    monkeypatch.setenv("AGENTMESH_API_KEY", "agentmesh_live_test")
    profile_present = True

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/api/v1/workbench/access"):
            return _Response(
                {
                    "access": "granted",
                    "workspace_ref": _test_workspace_ref("c"),
                    "profile_data_residency": "local_device",
                    "profile_input_enabled": True,
                }
            )
        if request.full_url.endswith("/api/v1/profiles"):
            if profile_present:
                return _Response(
                    [
                        {
                            "profile_version_id": "profile-before-loss",
                            "version_number": 2,
                            "label": "已确认档案",
                            "is_current": True,
                        }
                    ]
                )
            return _Response([])
        return _Response({"counts": {}})

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)

    assert cli_module.main(["doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    profile_present = False

    assert cli_module.main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "workspace_recovery_required"
    assert payload["continuity_status"] == "profile_missing"
    assert payload["recovery_required"] is True
    assert payload["interaction_required"]["kind"] == (
        "resume_reselection"
    )
    assert "重新选择" in payload["interaction_required"]["prompt"]
    assert "停止辅助填写" in payload["next_action"]


def test_doctor_continuity_survives_api_key_rotation(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "ORA_CONTINUITY_PATH",
        str(tmp_path / "continuity.json"),
    )
    profile_present = True
    authorizations = []

    def fake_urlopen(request, timeout):
        authorizations.append(request.headers.get("Authorization"))
        if request.full_url.endswith("/api/v1/workbench/access"):
            return _Response(
                {
                    "access": "granted",
                    "workspace_ref": _test_workspace_ref("d"),
                    "profile_data_residency": "local_device",
                    "profile_input_enabled": True,
                }
            )
        if request.full_url.endswith("/api/v1/profiles"):
            return _Response(
                [
                    {
                        "profile_version_id": "profile-v1",
                        "version_number": 1,
                        "label": "档案",
                        "is_current": True,
                    }
                ]
                if profile_present
                else []
            )
        return _Response({"counts": {}})

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)

    assert cli_module.main(
        ["--api-key", "agentmesh_live_old", "doctor"]
    ) == 0
    capsys.readouterr()
    profile_present = False
    assert cli_module.main(
        ["--api-key", "agentmesh_live_new", "doctor"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "workspace_recovery_required"
    assert authorizations[-1] == "Bearer agentmesh_live_new"


def test_doctor_does_not_apply_another_accounts_continuity_marker(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    continuity_path = tmp_path / "continuity.json"
    continuity_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workspaces": {
                    _test_workspace_ref("e"): {
                        "confirmed_profile_seen": True,
                        "profile_fingerprint": "a" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORA_CONTINUITY_PATH", str(continuity_path))
    monkeypatch.setenv("AGENTMESH_API_KEY", "agentmesh_live_test")

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/api/v1/workbench/access"):
            return _Response(
                {
                    "access": "granted",
                    "workspace_ref": _test_workspace_ref("f"),
                    "profile_data_residency": "local_device",
                    "profile_input_enabled": True,
                }
            )
        if request.full_url.endswith("/api/v1/profiles"):
            return _Response([])
        return _Response({"counts": {}})

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)

    assert cli_module.main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "needs_profile"
    assert payload["continuity_status"] == "uninitialized"


def test_doctor_fails_closed_when_continuity_marker_is_corrupt(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    continuity_path = tmp_path / "continuity.json"
    continuity_path.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("ORA_CONTINUITY_PATH", str(continuity_path))
    monkeypatch.setenv("AGENTMESH_API_KEY", "agentmesh_live_test")

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/api/v1/workbench/access"):
            return _Response(
                {
                    "access": "granted",
                    "workspace_ref": _test_workspace_ref("0"),
                    "profile_data_residency": "local_device",
                    "profile_input_enabled": True,
                }
            )
        if request.full_url.endswith("/api/v1/profiles"):
            return _Response([])
        return _Response({"counts": {}})

    monkeypatch.setattr(cli_module, "open_without_redirect", fake_urlopen)

    assert cli_module.main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "workspace_recovery_required"
    assert payload["continuity_status"] == "continuity_check_failed"
    assert payload["recovery_required"] is True
    assert continuity_path.read_text(encoding="utf-8") == "not-json"
