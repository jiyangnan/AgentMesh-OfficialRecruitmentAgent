from __future__ import annotations

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import zipfile

import pytest

import official_recruitment_agent.extension_delivery as delivery_module
from official_recruitment_agent.extension_delivery import (
    ExtensionDeliveryError,
    default_extension_root,
    extension_status,
    fetch_extension_release,
    load_extension_pairing,
    open_extension_setup,
    prepare_extension,
)


BASE_URL = "https://recruit.agentmesh360.test"
RELEASE_URL = (
    BASE_URL
    + "/downloads/agentmesh-officialrecruitment-extension-release.json"
)
ARCHIVE_URL = (
    BASE_URL + "/downloads/agentmesh-officialrecruitment-extension.zip"
)


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, maximum: int = -1) -> bytes:
        return self.payload if maximum < 0 else self.payload[:maximum]


def _extension_archive(
    *,
    version: str = "0.6.4",
    executor: bytes = b"console.log('ready')",
    extra_files: dict[str, bytes] | None = None,
) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "manifest_version": 3,
                    "name": "AgentMesh360 官网申请填写器",
                    "version": version,
                    "default_locale": "en",
                },
                ensure_ascii=False,
            ),
        )
        archive.writestr("popup.html", "<main>ready</main>")
        archive.writestr("popup.js", "console.log('popup')")
        archive.writestr("i18n.js", "export const ready = true")
        archive.writestr("executor.js", executor)
        for locale in ("zh_CN", "en", "ja", "ko"):
            archive.writestr(
                f"_locales/{locale}/messages.json",
                json.dumps({"extensionName": {"message": "test"}}),
            )
        for name, payload in (extra_files or {}).items():
            archive.writestr(name, payload)
    return output.getvalue()


def _release(archive: bytes, *, version: str = "0.6.4") -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "product": "officialrecruitment",
            "extension_version": version,
            "artifact_path": (
                "/downloads/agentmesh-officialrecruitment-extension.zip"
            ),
            "artifact_sha256": hashlib.sha256(archive).hexdigest(),
            "artifact_bytes": len(archive),
        }
    ).encode("utf-8")


def _opener(
    release: bytes,
    archive: bytes,
    calls: list[str] | None = None,
):
    def open_request(request, timeout):
        assert timeout == 20
        if calls is not None:
            calls.append(request.full_url)
        if request.full_url == RELEASE_URL:
            return _Response(release)
        if request.full_url == ARCHIVE_URL:
            return _Response(archive)
        raise AssertionError(request.full_url)

    return open_request


def test_default_extension_root_is_native_on_macos_windows_and_linux(
    tmp_path: Path,
) -> None:
    assert default_extension_root(
        platform_name="darwin",
        home=tmp_path,
        environ={},
    ) == (
        tmp_path
        / "Library"
        / "Application Support"
        / "AgentMesh360"
        / "OfficialRecruitment"
        / "extension"
    )
    assert default_extension_root(
        platform_name="win32",
        home=tmp_path,
        environ={"LOCALAPPDATA": "C:/Users/test/AppData/Local"},
    ) == Path(
        "C:/Users/test/AppData/Local/AgentMesh360/OfficialRecruitment/extension"
    )
    assert default_extension_root(
        platform_name="linux",
        home=tmp_path,
        environ={},
    ) == tmp_path / ".local/share/agentmesh360/official-recruitment/extension"


def test_prepare_installs_verified_archive_and_reuses_healthy_files(
    tmp_path: Path,
) -> None:
    archive = _extension_archive()
    release = _release(archive)
    calls: list[str] = []
    opener = _opener(release, archive, calls)
    root = tmp_path / "extension"

    installed = prepare_extension(
        BASE_URL,
        extension_root=root,
        opener=opener,
    )
    reused = prepare_extension(
        BASE_URL,
        extension_root=root,
        opener=opener,
    )

    assert installed["status"] == "ready"
    assert installed["extension_version"] == "0.6.4"
    assert installed["changed"] is True
    assert reused["changed"] is False
    assert calls == [RELEASE_URL, ARCHIVE_URL, RELEASE_URL]
    assert (root / "executor.js").read_bytes() == b"console.log('ready')"
    pairing = load_extension_pairing(root)
    assert pairing["installation_id"].startswith("orainstall_")
    assert pairing["pairing_secret"].startswith("orapair_")
    assert pairing["local_agent_url"] == "http://127.0.0.1:8765"
    if os.name != "nt":
        assert (
            root / "agentmesh-installation.json"
        ).stat().st_mode & 0o777 == 0o600
    assert extension_status(root)["healthy"] is True


def test_update_replaces_same_directory_without_losing_install_identity(
    tmp_path: Path,
) -> None:
    first_archive = _extension_archive(version="0.6.4")
    second_archive = _extension_archive(
        version="0.6.5",
        executor=b"console.log('updated')",
    )
    root = tmp_path / "stable-extension-directory"
    prepare_extension(
        BASE_URL,
        extension_root=root,
        opener=_opener(_release(first_archive), first_archive),
    )
    original_pairing = load_extension_pairing(root)

    updated = prepare_extension(
        BASE_URL,
        extension_root=root,
        opener=_opener(
            _release(second_archive, version="0.6.5"),
            second_archive,
        ),
    )

    assert updated["extension_version"] == "0.6.5"
    assert updated["install_directory"] == str(root)
    assert (root / "executor.js").read_bytes() == b"console.log('updated')"
    assert load_extension_pairing(root) == original_pairing


def test_prepare_never_downgrades_a_healthy_install(tmp_path: Path) -> None:
    newer = _extension_archive(version="0.7.0")
    older = _extension_archive(version="0.6.4")
    root = tmp_path / "extension"
    prepare_extension(
        BASE_URL,
        extension_root=root,
        opener=_opener(_release(newer, version="0.7.0"), newer),
    )

    result = prepare_extension(
        BASE_URL,
        extension_root=root,
        force=True,
        opener=_opener(_release(older), older),
    )

    assert result["changed"] is False
    assert result["downgrade_blocked"] is True
    assert result["extension_version"] == "0.7.0"


def test_tampered_archive_is_rejected_without_touching_current_install(
    tmp_path: Path,
) -> None:
    original = _extension_archive()
    root = tmp_path / "extension"
    prepare_extension(
        BASE_URL,
        extension_root=root,
        opener=_opener(_release(original), original),
    )
    tampered = _extension_archive(executor=b"tampered")

    with pytest.raises(ExtensionDeliveryError, match="官方版本清单"):
        prepare_extension(
            BASE_URL,
            extension_root=root,
            force=True,
            opener=_opener(_release(original), tampered),
        )

    assert (root / "executor.js").read_bytes() == b"console.log('ready')"
    assert extension_status(root)["healthy"] is True


def test_repair_restores_locally_damaged_extension(tmp_path: Path) -> None:
    archive = _extension_archive()
    release = _release(archive)
    root = tmp_path / "extension"
    opener = _opener(release, archive)
    prepare_extension(BASE_URL, extension_root=root, opener=opener)
    original_pairing = load_extension_pairing(root)
    (root / "executor.js").write_text("damaged", encoding="utf-8")
    assert extension_status(root)["status"] == "repair_required"

    repaired = prepare_extension(
        BASE_URL,
        extension_root=root,
        force=True,
        opener=opener,
    )

    assert repaired["status"] == "ready"
    assert repaired["changed"] is True
    assert (root / "executor.js").read_bytes() == b"console.log('ready')"
    assert load_extension_pairing(root) == original_pairing


def test_repair_restores_pairing_descriptor_from_private_state(
    tmp_path: Path,
) -> None:
    archive = _extension_archive()
    release = _release(archive)
    root = tmp_path / "extension"
    opener = _opener(release, archive)
    prepare_extension(BASE_URL, extension_root=root, opener=opener)
    original_pairing = load_extension_pairing(root)
    (root / "agentmesh-installation.json").write_text(
        "{}\n",
        encoding="utf-8",
    )

    assert extension_status(root)["status"] == "repair_required"
    prepare_extension(
        BASE_URL,
        extension_root=root,
        force=True,
        opener=opener,
    )

    assert load_extension_pairing(root) == original_pairing


def test_zip_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive = _extension_archive(extra_files={"../outside.txt": b"bad"})

    with pytest.raises(ExtensionDeliveryError, match="不安全路径"):
        prepare_extension(
            BASE_URL,
            extension_root=tmp_path / "extension",
            opener=_opener(_release(archive), archive),
        )

    assert not (tmp_path / "outside.txt").exists()


def test_zip_with_duplicate_case_insensitive_path_is_rejected(
    tmp_path: Path,
) -> None:
    archive = _extension_archive(extra_files={"Manifest.json": b"other"})

    with pytest.raises(ExtensionDeliveryError, match="不安全路径"):
        prepare_extension(
            BASE_URL,
            extension_root=tmp_path / "extension",
            opener=_opener(_release(archive), archive),
        )


def test_zip_extracted_size_limit_is_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delivery_module, "MAX_EXTRACTED_BYTES", 64)
    archive = _extension_archive(extra_files={"large.txt": b"x" * 65})

    with pytest.raises(ExtensionDeliveryError, match="解压后"):
        prepare_extension(
            BASE_URL,
            extension_root=tmp_path / "extension",
            opener=_opener(_release(archive), archive),
        )


def test_zip_missing_required_file_is_rejected(tmp_path: Path) -> None:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"manifest_version": 3, "version": "0.6.4"}),
        )
        archive.writestr("popup.html", "ready")
    payload = output.getvalue()

    with pytest.raises(ExtensionDeliveryError, match="缺少必要文件"):
        prepare_extension(
            BASE_URL,
            extension_root=tmp_path / "extension",
            opener=_opener(_release(payload), payload),
        )


def test_release_rejects_external_artifact_url() -> None:
    archive = _extension_archive()
    release = json.loads(_release(archive))
    release["artifact_path"] = "https://attacker.example/extension.zip"

    with pytest.raises(ExtensionDeliveryError, match="官方目录"):
        fetch_extension_release(
            BASE_URL,
            opener=_opener(json.dumps(release).encode("utf-8"), archive),
        )


def test_release_rejects_non_root_workbench_url() -> None:
    with pytest.raises(ExtensionDeliveryError, match="站点根地址"):
        fetch_extension_release("https://recruit.agentmesh360.test/app")


def test_windows_setup_opens_explorer_and_detected_chrome(
    tmp_path: Path,
) -> None:
    chrome = (
        tmp_path / "Program Files/Google/Chrome/Application/chrome.exe"
    )
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"")
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return object()

    results = open_extension_setup(
        tmp_path / "extension",
        platform_name="win32",
        environ={"PROGRAMFILES": str(tmp_path / "Program Files")},
        popen=fake_popen,
    )

    assert [item["opened"] for item in results] == [True, True]
    assert calls[0][0] == ["explorer.exe", str(tmp_path / "extension")]
    assert calls[1][0] == [str(chrome), "chrome://extensions/"]
