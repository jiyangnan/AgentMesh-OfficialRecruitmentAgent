from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any


PROFILE_SCHEMA_VERSION = "official-profile-v1"
PROFILE_IMPORT_PROPOSAL_TTL_SECONDS = 7 * 24 * 60 * 60

_SCALAR_FIELDS = {
    "full_name",
    "gender",
    "birth_date",
    "phone",
    "email",
    "id_type",
    "id_number",
    "political_status",
    "ethnicity",
    "household_registration",
    "native_place",
    "second_major",
    "personal_strengths",
    "height_cm",
    "expected_salary",
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
    "research_summary",
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
    "research_summary": "education_research_summary",
}
_EXPERIENCE_KINDS = {
    "internship",
    "work",
    "project",
    "campus_role",
    "activity",
}
_EXPERIENCE_FIELDS = {
    "kind",
    "name",
    "organization_name",
    "role_title",
    "start_date",
    "end_date",
    "location",
    "description",
    "level",
    "is_primary",
}
_CERTIFICATE_RECORD_FIELDS = {
    "name",
    "acquired_date",
    "issuer",
    "is_primary",
}
_SKILL_RECORD_FIELDS = {"name", "proficiency", "is_primary"}
_LANGUAGE_RECORD_FIELDS = {
    "language",
    "score",
    "level",
    "is_primary",
}
_REPEAT_FACT_PATTERN = re.compile(
    r"^repeat\.(education|internship|work|project|campus_role|activity|"
    r"certificate|skill|language)\.(\d{1,2})\.([a-z][a-z0-9_]*)$"
)
_REPEAT_GROUP_LABELS = {
    "education": "教育经历",
    "internship": "实习经历",
    "work": "工作经历",
    "project": "项目经历",
    "campus_role": "校内职务",
    "activity": "活动实践",
    "certificate": "证书",
    "skill": "专业技能",
    "language": "语言能力",
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
_SUPPLEMENTAL_FACT_FIELDS = {
    "key",
    "label",
    "value",
    "scope",
    "scope_ref",
    "privacy",
    "aliases",
    "provenance",
    "source_question_id",
}
_SUPPLEMENTAL_FACT_SCOPES = {"account", "site", "application"}
_SUPPLEMENTAL_FACT_PRIVACY = {"standard", "sensitive"}


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

    experience_records = normalized.get("experience_records")
    if experience_records is not None:
        normalized["experience_records"] = _normalize_experience_records(
            experience_records
        )
    for collection, allowed, required in (
        (
            "certificate_records",
            _CERTIFICATE_RECORD_FIELDS,
            {"name"},
        ),
        ("skill_records", _SKILL_RECORD_FIELDS, {"name"}),
        (
            "language_records",
            _LANGUAGE_RECORD_FIELDS,
            {"language"},
        ),
    ):
        if collection in normalized:
            normalized[collection] = _normalize_primary_records(
                normalized[collection],
                collection=collection,
                allowed_fields=allowed,
                required_fields=required,
            )

    source_document = normalized.get("_source_document")
    if source_document is not None:
        normalized["_source_document"] = _normalize_source_document(
            source_document
        )

    supplemental_facts = normalized.get("supplemental_facts")
    if supplemental_facts is not None:
        normalized["supplemental_facts"] = _normalize_supplemental_facts(
            supplemental_facts
        )

    return normalized


def dossier_values(
    fields: Mapping[str, Any],
    *,
    site_domain: str | None = None,
    application_id: str | None = None,
) -> dict[str, str]:
    normalized = normalize_profile_fields(fields)
    values = {
        key: str(value)
        for key, value in normalized.items()
        if isinstance(value, (str, int, float, bool))
        and key != "profile_schema_version"
        and str(value).strip()
    }
    certificates = normalized.get("certificates")
    if isinstance(certificates, list) and certificates:
        values["primary_certificate"] = certificates[0]
        values["certificates_summary"] = "、".join(certificates)
    language_skills = normalized.get("language_skills")
    if isinstance(language_skills, list) and language_skills:
        values["language_skills_summary"] = "、".join(language_skills)
    skills = normalized.get("skills")
    if isinstance(skills, list) and skills:
        values["skills_summary"] = "、".join(skills)
        values.setdefault("personal_strengths", values["skills_summary"])

    certificate = _primary_record(normalized.get("certificate_records"))
    if certificate is not None:
        _copy_record_values(
            values,
            certificate,
            {
                "name": "primary_certificate",
                "acquired_date": "certificate_acquired_date",
                "issuer": "certificate_issuer",
            },
        )
    skill = _primary_record(normalized.get("skill_records"))
    if skill is not None:
        _copy_record_values(
            values,
            skill,
            {
                "name": "primary_skill_name",
                "proficiency": "primary_skill_proficiency",
            },
        )
    language = _primary_record(normalized.get("language_records"))
    if language is not None:
        _copy_record_values(
            values,
            language,
            {
                "language": "primary_language",
                "score": "primary_language_score",
                "level": "primary_language_level",
            },
        )

    experience_records = normalized.get("experience_records")
    if isinstance(experience_records, list):
        for kind, prefix in (
            ("internship", "internship"),
            ("work", "work"),
            ("project", "project"),
            ("campus_role", "campus_role"),
            ("activity", "activity"),
        ):
            record = _primary_experience(experience_records, kind)
            if record is None:
                continue
            role_title_field = (
                "campus_role_title"
                if kind == "campus_role"
                else f"{prefix}_role_title"
            )
            _copy_record_values(
                values,
                record,
                {
                    "name": f"{prefix}_name",
                    "organization_name": f"{prefix}_organization_name",
                    "role_title": role_title_field,
                    "start_date": f"{prefix}_start_date",
                    "end_date": f"{prefix}_end_date",
                    "location": f"{prefix}_location",
                    "description": f"{prefix}_description",
                    "level": f"{prefix}_level",
                },
            )
    for fact in active_supplemental_facts(
        normalized,
        site_domain=site_domain,
        application_id=application_id,
    ):
        values[fact["key"]] = fact["value"]
    return values


def dossier_repeat_groups(
    fields: Mapping[str, Any],
    *,
    site_domain: str | None = None,
    application_id: str | None = None,
) -> dict[str, tuple[dict[str, str], ...]]:
    """Return ordered record values for repeatable application sections."""
    normalized = normalize_profile_fields(fields)
    groups: dict[str, list[dict[str, str]]] = {}

    education_records = normalized.get("education_records")
    if isinstance(education_records, list):
        groups["education"] = [
            _mapped_record_values(record, _EDUCATION_TO_CANONICAL)
            for record in education_records
        ]

    experience_records = normalized.get("experience_records")
    if isinstance(experience_records, list):
        experience_maps = {
            "internship": {
                "name": "internship_name",
                "organization_name": "internship_organization_name",
                "role_title": "internship_role_title",
                "start_date": "internship_start_date",
                "end_date": "internship_end_date",
                "location": "internship_location",
                "description": "internship_description",
                "level": "internship_level",
            },
            "work": {
                "name": "work_name",
                "organization_name": "work_organization_name",
                "role_title": "work_role_title",
                "start_date": "work_start_date",
                "end_date": "work_end_date",
                "location": "work_location",
                "description": "work_description",
                "level": "work_level",
            },
            "project": {
                "name": "project_name",
                "organization_name": "project_organization_name",
                "role_title": "project_role_title",
                "start_date": "project_start_date",
                "end_date": "project_end_date",
                "location": "project_location",
                "description": "project_description",
                "level": "project_level",
            },
            "campus_role": {
                "name": "campus_role_name",
                "organization_name": "campus_role_organization_name",
                "role_title": "campus_role_title",
                "start_date": "campus_role_start_date",
                "end_date": "campus_role_end_date",
                "location": "campus_role_location",
                "description": "campus_role_description",
                "level": "campus_role_level",
            },
            "activity": {
                "name": "activity_name",
                "organization_name": "activity_organization_name",
                "role_title": "activity_role_title",
                "start_date": "activity_start_date",
                "end_date": "activity_end_date",
                "location": "activity_location",
                "description": "activity_description",
                "level": "activity_level",
            },
        }
        for kind, field_map in experience_maps.items():
            records = [
                _mapped_record_values(record, field_map)
                for record in experience_records
                if record.get("kind") == kind
            ]
            if records:
                groups[kind] = records

    for collection, group, field_map in (
        (
            "certificate_records",
            "certificate",
            {
                "name": "primary_certificate",
                "acquired_date": "certificate_acquired_date",
                "issuer": "certificate_issuer",
            },
        ),
        (
            "skill_records",
            "skill",
            {
                "name": "primary_skill_name",
                "proficiency": "primary_skill_proficiency",
            },
        ),
        (
            "language_records",
            "language",
            {
                "language": "primary_language",
                "score": "primary_language_score",
                "level": "primary_language_level",
            },
        ),
    ):
        records = normalized.get(collection)
        if isinstance(records, list):
            groups[group] = [
                _mapped_record_values(record, field_map) for record in records
            ]

    for fact in active_supplemental_facts(
        normalized,
        site_domain=site_domain,
        application_id=application_id,
    ):
        match = _REPEAT_FACT_PATTERN.fullmatch(str(fact.get("key") or ""))
        if match is None:
            continue
        group, raw_index, canonical = match.groups()
        record_index = int(raw_index)
        records = groups.get(group)
        if records is None or record_index >= len(records):
            continue
        records[record_index][canonical] = fact["value"]

    return {
        group: tuple(records)
        for group, records in groups.items()
        if records
    }


def repeat_group_label(group: str) -> str:
    return _REPEAT_GROUP_LABELS.get(group, group)


def dossier_aliases(
    fields: Mapping[str, Any],
    *,
    site_domain: str | None = None,
    application_id: str | None = None,
) -> dict[str, tuple[str, ...]]:
    normalized = normalize_profile_fields(fields)
    aliases: dict[str, list[str]] = {}
    for fact in active_supplemental_facts(
        normalized,
        site_domain=site_domain,
        application_id=application_id,
    ):
        bucket = aliases.setdefault(fact["key"], [])
        for alias in fact.get("aliases", []):
            if alias not in bucket:
                bucket.append(alias)
    return {key: tuple(values) for key, values in aliases.items()}


def dossier_sensitive_fields(
    fields: Mapping[str, Any],
    *,
    site_domain: str | None = None,
    application_id: str | None = None,
) -> frozenset[str]:
    normalized = normalize_profile_fields(fields)
    return frozenset(
        fact["key"]
        for fact in active_supplemental_facts(
            normalized,
            site_domain=site_domain,
            application_id=application_id,
        )
        if fact["privacy"] == "sensitive"
    )


def active_supplemental_facts(
    fields: Mapping[str, Any],
    *,
    site_domain: str | None = None,
    application_id: str | None = None,
) -> list[dict[str, Any]]:
    facts = fields.get("supplemental_facts")
    if not isinstance(facts, list):
        return []
    normalized_site = (site_domain or "").strip().lower()
    applicable: list[tuple[int, dict[str, Any]]] = []
    precedence = {"account": 0, "site": 1, "application": 2}
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        scope = fact.get("scope")
        scope_ref = fact.get("scope_ref")
        if scope == "account":
            applicable.append((precedence[scope], fact))
        elif scope == "site" and normalized_site == scope_ref:
            applicable.append((precedence[scope], fact))
        elif scope == "application" and application_id == scope_ref:
            applicable.append((precedence[scope], fact))
    applicable.sort(key=lambda item: item[0])
    return [fact for _, fact in applicable]


def _normalize_experience_records(value: Any) -> list[dict[str, Any]]:
    records = _normalize_primary_records(
        value,
        collection="experience_records",
        allowed_fields=_EXPERIENCE_FIELDS,
        required_fields={"kind"},
        primary_group="kind",
    )
    for index, record in enumerate(records):
        kind = record["kind"]
        if kind not in _EXPERIENCE_KINDS:
            raise ValueError(
                f"第 {index + 1} 条 experience_records.kind 无效。"
            )
        required_by_kind = {
            "internship": "organization_name",
            "work": "organization_name",
            "project": "name",
            "campus_role": "role_title",
            "activity": "name",
        }[kind]
        if not record.get(required_by_kind):
            raise ValueError(
                f"第 {index + 1} 条 {kind} 经历缺少 {required_by_kind}。"
            )
    return records


def _normalize_primary_records(
    value: Any,
    *,
    collection: str,
    allowed_fields: set[str],
    required_fields: set[str],
    primary_group: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{collection} 必须是非空列表。")
    if len(value) > 50:
        raise ValueError(f"{collection} 最多包含 50 条记录。")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"{collection} 第 {index + 1} 条格式无效。")
        unknown = set(item) - allowed_fields
        if unknown:
            raise ValueError(
                f"{collection} 包含未声明字段："
                + "、".join(sorted(unknown))
            )
        record: dict[str, Any] = {}
        for key, raw in item.items():
            if key == "is_primary":
                if not isinstance(raw, bool):
                    raise ValueError("is_primary 必须是布尔值。")
                record[key] = raw
                continue
            if not isinstance(raw, (str, int, float)):
                raise ValueError(
                    f"{collection} 字段 {key} 必须是单值。"
                )
            text = str(raw).strip()
            if text:
                record[key] = text
        missing = required_fields - set(record)
        if missing:
            raise ValueError(
                f"{collection} 第 {index + 1} 条缺少："
                + "、".join(sorted(missing))
            )
        records.append(record)

    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        group = str(record.get(primary_group) or "all")
        groups.setdefault(group, []).append(record)
    for group_records in groups.values():
        primary = [item for item in group_records if item.get("is_primary")]
        if len(primary) > 1:
            raise ValueError(f"{collection} 同一类型只能有一条主记录。")
        if len(group_records) == 1 and not primary:
            group_records[0]["is_primary"] = True
    return records


def _primary_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    primary = [item for item in value if item.get("is_primary")]
    return primary[0] if len(primary) == 1 else None


def _primary_experience(
    records: list[dict[str, Any]], kind: str
) -> dict[str, Any] | None:
    matching = [item for item in records if item.get("kind") == kind]
    return _primary_record(matching)


def _copy_record_values(
    target: dict[str, str],
    record: Mapping[str, Any],
    field_map: Mapping[str, str],
) -> None:
    for source, destination in field_map.items():
        value = record.get(source)
        if value is not None and str(value).strip():
            target[destination] = str(value).strip()


def _mapped_record_values(
    record: Mapping[str, Any],
    field_map: Mapping[str, str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    _copy_record_values(values, record, field_map)
    return values


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


def _normalize_supplemental_facts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("supplemental_facts 必须是非空列表。")
    if len(value) > 200:
        raise ValueError("supplemental_facts 最多包含 200 条记录。")
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str | None]] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(
                f"supplemental_facts 第 {index + 1} 条格式无效。"
            )
        unknown = set(item) - _SUPPLEMENTAL_FACT_FIELDS
        if unknown:
            raise ValueError(
                "supplemental_facts 包含未声明字段："
                + "、".join(sorted(str(key) for key in unknown))
            )
        key = str(item.get("key") or "").strip().lower()
        if re.fullmatch(r"[a-z][a-z0-9_.]{1,119}", key) is None:
            raise ValueError(
                f"supplemental_facts 第 {index + 1} 条 key 格式无效。"
            )
        label = str(item.get("label") or "").strip()
        fact_value = str(item.get("value") or "").strip()
        if not label or len(label) > 160:
            raise ValueError(
                f"supplemental_facts 第 {index + 1} 条 label 格式无效。"
            )
        if not fact_value or len(fact_value) > 4000:
            raise ValueError(
                f"supplemental_facts 第 {index + 1} 条 value 格式无效。"
            )
        scope = str(item.get("scope") or "").strip().lower()
        if scope not in _SUPPLEMENTAL_FACT_SCOPES:
            raise ValueError(
                f"supplemental_facts 第 {index + 1} 条 scope 无效。"
            )
        raw_scope_ref = item.get("scope_ref")
        scope_ref = (
            str(raw_scope_ref).strip().lower()
            if raw_scope_ref is not None
            else None
        )
        if scope == "account" and scope_ref:
            raise ValueError("账号级补充事实不能设置 scope_ref。")
        if scope == "site" and (
            not scope_ref
            or len(scope_ref) > 253
            or re.search(r"[\s/:]", scope_ref)
        ):
            raise ValueError("官网级补充事实必须绑定有效域名。")
        if scope == "application" and (
            not scope_ref
            or len(scope_ref) > 128
            or re.search(r"\s", scope_ref)
        ):
            raise ValueError("申请级补充事实必须绑定申请记录。")
        privacy = str(item.get("privacy") or "standard").strip().lower()
        if privacy not in _SUPPLEMENTAL_FACT_PRIVACY:
            raise ValueError(
                f"supplemental_facts 第 {index + 1} 条 privacy 无效。"
            )
        raw_aliases = item.get("aliases", [])
        if not isinstance(raw_aliases, list) or len(raw_aliases) > 20:
            raise ValueError("supplemental_facts.aliases 格式无效。")
        aliases: list[str] = []
        for raw_alias in raw_aliases:
            alias = str(raw_alias).strip()
            if not alias or len(alias) > 160:
                raise ValueError("supplemental_facts.aliases 格式无效。")
            if alias not in aliases:
                aliases.append(alias)
        provenance = str(
            item.get("provenance") or "user_confirmed"
        ).strip()
        if provenance != "user_confirmed":
            raise ValueError("补充事实必须由用户确认后生效。")
        source_question_id = str(
            item.get("source_question_id") or ""
        ).strip()
        if source_question_id and re.fullmatch(
            r"pq_[0-9a-f]{24}", source_question_id
        ) is None:
            raise ValueError("source_question_id 格式无效。")
        identity = (key, scope, scope_ref)
        if identity in identities:
            raise ValueError("同一适用范围内不能重复声明同一个补充事实。")
        identities.add(identity)
        normalized.append(
            {
                "key": key,
                "label": label,
                "value": fact_value,
                "scope": scope,
                "scope_ref": scope_ref,
                "privacy": privacy,
                "aliases": aliases,
                "provenance": provenance,
                **(
                    {"source_question_id": source_question_id}
                    if source_question_id
                    else {}
                ),
            }
        )
    return normalized


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
