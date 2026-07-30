from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_repository_contains_only_client_surfaces() -> None:
    required = [
        "skills/agentmesh-officialrecruitment/SKILL.md",
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
