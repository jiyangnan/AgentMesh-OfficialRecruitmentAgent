from __future__ import annotations

import pytest

from official_recruitment_agent.workbench.profile_contract import (
    PROFILE_SCHEMA_VERSION,
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
