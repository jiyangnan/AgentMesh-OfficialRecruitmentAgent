from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any


PROFILE_SCHEMA_VERSION = "official-profile-v1"

_SCALAR_FIELDS = {
    "full_name",
    "gender",
    "birth_date",
    "phone",
    "email",
    "id_number",
    "political_status",
    "ethnicity",
    "school_name",
    "school_city",
    "school_country",
    "college_name",
    "major",
    "education_level",
    "degree",
    "education_start_date",
    "graduation_date",
    "study_mode",
}
_LIST_FIELDS = {
    "target_roles",
    "skills",
    "preferred_locations",
    "certificates",
    "awards",
    "language_skills",
}
_EDUCATION_FIELDS = {
    "school_name",
    "school_city",
    "school_country",
    "college_name",
    "major",
    "education_level",
    "degree",
    "start_date",
    "graduation_date",
    "study_mode",
    "is_primary",
}
_EDUCATION_TO_CANONICAL = {
    "school_name": "school_name",
    "school_city": "school_city",
    "school_country": "school_country",
    "college_name": "college_name",
    "major": "major",
    "education_level": "education_level",
    "degree": "degree",
    "start_date": "education_start_date",
    "graduation_date": "graduation_date",
    "study_mode": "study_mode",
}
_SOURCE_DOCUMENT_FIELDS = {
    "sha256",
    "suffix",
    "raw_document_uploaded",
    "parsed_by",
    "schema_version",
}
_FORBIDDEN_DOCUMENT_KEYS = {
    "document_path",
    "file_path",
    "filename",
    "file_name",
    "raw_document",
    "resume_path",
    "resume_text",
    "resume_content",
}


def normalize_profile_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(fields, Mapping) or not fields:
        raise ValueError("个人档案字段不能为空。")
    normalized = deepcopy(dict(fields))
    normalized["profile_schema_version"] = PROFILE_SCHEMA_VERSION
    forbidden = {
        str(key).lower()
        for key in normalized
        if str(key).lower() in _FORBIDDEN_DOCUMENT_KEYS
    }
    if forbidden:
        raise ValueError(
            "个人档案不得包含原始简历或本机路径字段："
            + "、".join(sorted(forbidden))
        )

    for key in _SCALAR_FIELDS:
        if key not in normalized:
            continue
        value = normalized[key]
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"个人档案字段 {key} 必须是单值。")
        text = str(value).strip()
        if text:
            normalized[key] = text
        else:
            normalized.pop(key)

    for key in _LIST_FIELDS:
        if key not in normalized:
            continue
        value = normalized[key]
        if not isinstance(value, list):
            raise ValueError(f"个人档案字段 {key} 必须是列表。")
        items = [
            str(item).strip()
            for item in value
            if isinstance(item, (str, int, float)) and str(item).strip()
        ]
        if items:
            normalized[key] = items
        else:
            normalized.pop(key)

    education_records = normalized.get("education_records")
    if education_records is not None:
        normalized["education_records"] = _normalize_education_records(
            education_records
        )
        primary = _primary_education(normalized["education_records"])
        if primary is not None:
            for source, target in _EDUCATION_TO_CANONICAL.items():
                value = primary.get(source)
                if value and target not in normalized:
                    normalized[target] = value

    source_document = normalized.get("_source_document")
    if source_document is not None:
        normalized["_source_document"] = _normalize_source_document(
            source_document
        )

    return normalized


def dossier_values(fields: Mapping[str, Any]) -> dict[str, str]:
    normalized = normalize_profile_fields(fields)
    return {
        key: str(value)
        for key, value in normalized.items()
        if isinstance(value, (str, int, float, bool))
        and key != "profile_schema_version"
        and str(value).strip()
    }


def _normalize_education_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("education_records 必须是非空列表。")
    if len(value) > 20:
        raise ValueError("education_records 最多包含 20 条记录。")
    records: list[dict[str, Any]] = []
    primary_count = 0
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"第 {index + 1} 条教育经历格式无效。")
        unknown = set(item) - _EDUCATION_FIELDS
        if unknown:
            raise ValueError(
                "教育经历包含未声明字段：" + "、".join(sorted(unknown))
            )
        record: dict[str, Any] = {}
        for key, raw in item.items():
            if key == "is_primary":
                if not isinstance(raw, bool):
                    raise ValueError("is_primary 必须是布尔值。")
                if raw:
                    primary_count += 1
                record[key] = raw
                continue
            if not isinstance(raw, (str, int, float)):
                raise ValueError(f"教育经历字段 {key} 必须是单值。")
            text = str(raw).strip()
            if text:
                record[key] = text
        if not record.get("school_name"):
            raise ValueError(f"第 {index + 1} 条教育经历缺少学校名称。")
        records.append(record)
    if primary_count > 1:
        raise ValueError("只能标记一条主教育经历。")
    if len(records) == 1 and primary_count == 0:
        records[0]["is_primary"] = True
    return records


def _primary_education(
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    primary = [record for record in records if record.get("is_primary")]
    if len(primary) == 1:
        return primary[0]
    return None


def _normalize_source_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("_source_document 必须是对象。")
    unknown = set(value) - _SOURCE_DOCUMENT_FIELDS
    if unknown:
        raise ValueError(
            "_source_document 包含未声明字段："
            + "、".join(sorted(str(item) for item in unknown))
        )
    digest = value.get("sha256")
    if not isinstance(digest, str) or re.fullmatch(
        r"[0-9a-f]{64}", digest
    ) is None:
        raise ValueError("_source_document.sha256 格式无效。")
    suffix = value.get("suffix", "")
    if not isinstance(suffix, str) or len(suffix) > 12:
        raise ValueError("_source_document.suffix 格式无效。")
    if value.get("raw_document_uploaded") is not False:
        raise ValueError("原始简历不得上传到工作台。")
    parsed_by = value.get("parsed_by")
    if parsed_by not in {None, "host_agent"}:
        raise ValueError("_source_document.parsed_by 格式无效。")
    schema_version = value.get("schema_version")
    if schema_version not in {None, PROFILE_SCHEMA_VERSION}:
        raise ValueError("_source_document.schema_version 不兼容。")
    return {
        "sha256": digest,
        "suffix": suffix.lower(),
        "raw_document_uploaded": False,
        "parsed_by": parsed_by or "host_agent",
        "schema_version": schema_version or PROFILE_SCHEMA_VERSION,
    }
