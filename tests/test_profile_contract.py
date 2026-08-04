from __future__ import annotations

import pytest

from official_recruitment_agent.workbench.profile_contract import (
    PROFILE_SCHEMA_VERSION,
    dossier_aliases,
    dossier_repeat_groups,
    dossier_sensitive_fields,
    dossier_values,
    normalize_profile_fields,
)


def test_single_education_record_becomes_primary_and_is_flattened() -> None:
    normalized = normalize_profile_fields(
        {
            "full_name": "林同学",
            "education_records": [
                {
                    "school_name": "示例大学",
                    "school_city": "深圳",
                    "college_name": "计算机学院",
                    "major": "人工智能",
                    "start_date": "2022-09",
                    "graduation_date": "2026-06",
                }
            ],
        }
    )

    assert normalized["profile_schema_version"] == PROFILE_SCHEMA_VERSION
    assert normalized["education_records"][0]["is_primary"] is True
    assert normalized["school_name"] == "示例大学"
    assert normalized["school_city"] == "深圳"
    assert normalized["education_start_date"] == "2022-09"
    assert dossier_values(normalized)["school_city"] == "深圳"


def test_multiple_education_records_without_primary_are_not_guessed() -> None:
    normalized = normalize_profile_fields(
        {
            "education_records": [
                {"school_name": "本科院校", "school_city": "广州"},
                {"school_name": "硕士院校", "school_city": "北京"},
            ]
        }
    )

    assert "school_name" not in normalized
    assert "school_city" not in normalized
    assert "school_city" not in dossier_values(normalized)


def test_multiple_primary_education_records_are_rejected() -> None:
    with pytest.raises(ValueError, match="只能标记一条"):
        normalize_profile_fields(
            {
                "education_records": [
                    {"school_name": "本科院校", "is_primary": True},
                    {"school_name": "硕士院校", "is_primary": True},
                ]
            }
        )


def test_document_provenance_never_enters_fill_dossier() -> None:
    values = dossier_values(
        {
            "full_name": "林同学",
            "_source_document": {
                "sha256": "a" * 64,
                "suffix": ".pdf",
                "raw_document_uploaded": False,
            },
        }
    )

    assert values == {"full_name": "林同学"}


def test_confirmed_lists_expose_fill_safe_summary_values() -> None:
    values = dossier_values(
        {
            "full_name": "林同学",
            "certificates": ["示例专业证书", "示例技能证书"],
            "language_skills": ["英语熟练"],
            "skills": ["需求分析", "原型设计"],
        }
    )

    assert values["primary_certificate"] == "示例专业证书"
    assert values["certificates_summary"] == (
        "示例专业证书、示例技能证书"
    )
    assert values["language_skills_summary"] == "英语熟练"
    assert values["skills_summary"] == "需求分析、原型设计"
    assert values["personal_strengths"] == "需求分析、原型设计"


def test_structured_resume_records_expose_primary_fill_values() -> None:
    normalized = normalize_profile_fields(
        {
            "experience_records": [
                {
                    "kind": "internship",
                    "organization_name": "示例科技",
                    "role_title": "产品实习生",
                    "start_date": "2025-01",
                    "end_date": "2025-06",
                    "description": "负责需求分析与原型设计",
                },
                {
                    "kind": "campus_role",
                    "role_title": "学生会负责人",
                    "start_date": "2023-09-01",
                    "end_date": "2024-06-30",
                    "description": "组织校内活动",
                },
            ],
            "certificate_records": [
                {
                    "name": "示例专业证书",
                    "acquired_date": "2025-05-20",
                }
            ],
            "skill_records": [
                {"name": "需求分析", "proficiency": "熟练"}
            ],
            "language_records": [
                {"language": "英语", "score": "600", "level": "六级"}
            ],
        }
    )
    values = dossier_values(normalized)

    assert normalized["experience_records"][0]["is_primary"] is True
    assert normalized["experience_records"][1]["is_primary"] is True
    assert values["internship_organization_name"] == "示例科技"
    assert values["internship_role_title"] == "产品实习生"
    assert values["campus_role_title"] == "学生会负责人"
    assert values["certificate_acquired_date"] == "2025-05-20"
    assert values["primary_skill_proficiency"] == "熟练"
    assert values["primary_language"] == "英语"
    assert values["primary_language_level"] == "六级"


def test_structured_resume_records_expose_every_repeatable_record() -> None:
    groups = dossier_repeat_groups(
        {
            "education_records": [
                {"school_name": "本科院校", "major": "计算机"},
                {"school_name": "硕士院校", "major": "人工智能"},
            ],
            "experience_records": [
                {"kind": "work", "organization_name": "甲公司"},
                {"kind": "work", "organization_name": "乙公司"},
            ],
            "skill_records": [
                {"name": "需求分析"},
                {"name": "数据分析"},
            ],
            "language_records": [
                {"language": "英语", "level": "六级"},
                {"language": "日语", "level": "N2"},
            ],
        }
    )

    assert [item["school_name"] for item in groups["education"]] == [
        "本科院校",
        "硕士院校",
    ]
    assert [item["work_organization_name"] for item in groups["work"]] == [
        "甲公司",
        "乙公司",
    ]
    assert [item["primary_skill_name"] for item in groups["skill"]] == [
        "需求分析",
        "数据分析",
    ]
    assert groups["language"][1]["primary_language_level"] == "N2"


def test_repeatable_record_can_use_confirmed_supplemental_fact() -> None:
    groups = dossier_repeat_groups(
        {
            "experience_records": [
                {"kind": "work", "organization_name": "甲公司"},
                {"kind": "work", "organization_name": "乙公司"},
            ],
            "supplemental_facts": [
                {
                    "key": "repeat.work.1.work_end_date",
                    "label": "第二段工作经历结束时间",
                    "value": "2026-06",
                    "scope": "account",
                    "privacy": "standard",
                    "aliases": [],
                    "provenance": "user_confirmed",
                    "source_question_id": "pq_0123456789abcdef01234567",
                }
            ],
        }
    )

    assert groups["work"][1]["work_end_date"] == "2026-06"


def test_multiple_same_kind_experiences_without_primary_are_not_guessed() -> None:
    normalized = normalize_profile_fields(
        {
            "experience_records": [
                {"kind": "work", "organization_name": "甲公司"},
                {"kind": "work", "organization_name": "乙公司"},
            ]
        }
    )

    values = dossier_values(normalized)
    assert "work_organization_name" not in values


def test_multiple_primary_records_of_same_kind_are_rejected() -> None:
    with pytest.raises(ValueError, match="同一类型只能有一条主记录"):
        normalize_profile_fields(
            {
                "experience_records": [
                    {
                        "kind": "work",
                        "organization_name": "甲公司",
                        "is_primary": True,
                    },
                    {
                        "kind": "work",
                        "organization_name": "乙公司",
                        "is_primary": True,
                    },
                ]
            }
        )


@pytest.mark.parametrize(
    "forbidden_key",
    ["document_path", "raw_document", "resume_text", "filename"],
)
def test_raw_resume_and_local_path_fields_are_rejected(
    forbidden_key: str,
) -> None:
    with pytest.raises(ValueError, match="不得包含"):
        normalize_profile_fields(
            {
                "full_name": "林同学",
                forbidden_key: "private resume material",
            }
        )


def test_source_document_metadata_rejects_uploaded_raw_document() -> None:
    with pytest.raises(ValueError, match="不得上传"):
        normalize_profile_fields(
            {
                "full_name": "林同学",
                "_source_document": {
                    "sha256": "a" * 64,
                    "raw_document_uploaded": True,
                },
            }
        )


def test_supplemental_facts_apply_from_broad_to_narrow_scope() -> None:
    profile = normalize_profile_fields(
        {
            "full_name": "林同学",
            "supplemental_facts": [
                {
                    "key": "custom.relocation",
                    "label": "是否接受调动",
                    "value": "否",
                    "scope": "account",
                    "privacy": "standard",
                    "aliases": ["是否接受调动"],
                },
                {
                    "key": "custom.relocation",
                    "label": "是否接受调动",
                    "value": "是",
                    "scope": "site",
                    "scope_ref": "campus.example.com",
                    "privacy": "standard",
                    "aliases": ["是否服从调动"],
                },
                {
                    "key": "custom.relocation",
                    "label": "是否接受调动",
                    "value": "仅华南地区",
                    "scope": "application",
                    "scope_ref": "app_example001",
                    "privacy": "standard",
                    "aliases": ["调动意愿"],
                },
            ],
        }
    )

    assert dossier_values(profile)["custom.relocation"] == "否"
    assert dossier_values(
        profile,
        site_domain="campus.example.com",
    )["custom.relocation"] == "是"
    assert dossier_values(
        profile,
        site_domain="campus.example.com",
        application_id="app_example001",
    )["custom.relocation"] == "仅华南地区"
    assert dossier_aliases(
        profile,
        site_domain="campus.example.com",
        application_id="app_example001",
    )["custom.relocation"] == (
        "是否接受调动",
        "是否服从调动",
        "调动意愿",
    )


def test_sensitive_supplemental_fact_remains_review_only_metadata() -> None:
    profile = normalize_profile_fields(
        {
            "full_name": "林同学",
            "supplemental_facts": [
                {
                    "key": "custom.health_history",
                    "label": "既往病史",
                    "value": "无",
                    "scope": "application",
                    "scope_ref": "app_example001",
                    "privacy": "sensitive",
                    "aliases": ["既往病史"],
                    "source_question_id": "pq_" + "a" * 24,
                }
            ],
        }
    )

    assert dossier_sensitive_fields(
        profile,
        application_id="app_example001",
    ) == frozenset({"custom.health_history"})
    assert dossier_sensitive_fields(profile) == frozenset()


def test_duplicate_supplemental_fact_scope_is_rejected() -> None:
    fact = {
        "key": "custom.relocation",
        "label": "是否接受调动",
        "value": "是",
        "scope": "account",
        "privacy": "standard",
        "aliases": ["是否接受调动"],
    }
    with pytest.raises(ValueError, match="不能重复"):
        normalize_profile_fields(
            {
                "full_name": "林同学",
                "supplemental_facts": [fact, fact],
            }
        )
