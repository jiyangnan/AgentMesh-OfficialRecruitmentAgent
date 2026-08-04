import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_repository_contains_only_client_surfaces() -> None:
    required = [
        "skills/agentmesh-officialrecruitment/SKILL.md",
        "src/official_recruitment_agent/local_profile_handoff.py",
        "src/official_recruitment_agent/workbench_cli.py",
        "src/official_recruitment_agent/workbench/profile_contract.py",
        "extension/manifest.json",
        "installer/install-agent.sh",
    ]
    forbidden = [
        "Dockerfile",
        "migrations",
        "corpora",
        "deploy",
        "web/src",
        "src/official_recruitment_agent/workbench/api.py",
        "src/official_recruitment_agent/workbench/models.py",
        "src/official_recruitment_agent/workbench/service.py",
        "docs/evidence",
        "docs/operations",
    ]

    for relative in required:
        assert (ROOT / relative).exists(), relative
    for relative in forbidden:
        assert not (ROOT / relative).exists(), relative


def test_public_copy_names_repository_and_product_separately() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "`AgentMesh-OfficialRecruitmentAgent`" in readme
    assert "# AgentMesh-OfficialRecruitment" in readme
    assert "不会安装或创建另一个 AI Agent" in readme
    assert "我已核对以上预览" in readme
    assert "页面稳定回读" in readme

    cli = (
        ROOT
        / "src"
        / "official_recruitment_agent"
        / "workbench_cli.py"
    ).read_text(encoding="utf-8")
    assert "profile-completion-proposals" not in cli
    assert "propose-profile-completion" not in cli


def test_distributable_text_has_no_secret_or_private_path() -> None:
    roots = [
        ROOT / "extension",
        ROOT / "installer",
        ROOT / "skills",
        ROOT / "src",
        ROOT / "docs",
    ]
    files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    for root in roots:
        files.extend(path for path in root.rglob("*") if path.is_file())

    secret_patterns = [
        re.compile(r"jobagent_live_[A-Za-z0-9]{20,}"),
        re.compile(r"sk_live_[A-Za-z0-9]{20,}"),
        re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    ]
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert "/Users/" not in content, path
        for pattern in secret_patterns:
            assert pattern.search(content) is None, path
