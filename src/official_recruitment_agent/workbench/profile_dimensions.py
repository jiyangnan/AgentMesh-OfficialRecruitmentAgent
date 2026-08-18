from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


PROFILE_DIMENSION_CATALOG_VERSION = "official-profile-dimensions-v1"
# This domain payload is versioned independently from the shared interaction
# and local-resolution wire protocols that carry it.
PROFILE_FOUNDATION_CONTRACT_VERSION = "profile-foundation-v1"

ProfileDimensionPriority = Literal["essential", "recommended", "contextual"]
ProfileDimensionPrivacy = Literal["standard", "sensitive"]
ProfileDimensionInputType = Literal[
    "text",
    "email",
    "tel",
    "date",
    "month",
    "number",
]


@dataclass(frozen=True, slots=True)
class ProfileDimension:
    key: str
    label: str
    group: str
    group_label: str
    priority: ProfileDimensionPriority
    privacy: ProfileDimensionPrivacy = "standard"
    initial_prompt: bool = True
    aliases: tuple[str, ...] = ()
    options: tuple[str, ...] = ()
    placeholder: str = "请输入真实信息"
    input_type: ProfileDimensionInputType = "text"


PROFILE_DIMENSIONS: tuple[ProfileDimension, ...] = (
    ProfileDimension(
        "full_name",
        "姓名",
        "identity",
        "身份信息",
        "essential",
        aliases=("真实姓名", "姓名", "full name"),
    ),
    ProfileDimension(
        "english_name",
        "英文姓名",
        "identity",
        "身份信息",
        "recommended",
        aliases=("英文名", "英文姓名", "english name"),
    ),
    ProfileDimension(
        "gender",
        "性别",
        "identity",
        "身份信息",
        "recommended",
        options=("男", "女", "其他", "不愿透露"),
        aliases=("性别", "gender", "sex"),
    ),
    ProfileDimension(
        "birth_date",
        "出生日期",
        "identity",
        "身份信息",
        "recommended",
        privacy="sensitive",
        aliases=("出生日期", "出生年月", "生日", "birth date"),
        placeholder="YYYY-MM-DD",
        input_type="date",
    ),
    ProfileDimension(
        "nationality",
        "国籍",
        "identity",
        "身份信息",
        "recommended",
        aliases=("国籍", "国家或地区", "nationality", "citizenship"),
    ),
    ProfileDimension(
        "ethnicity",
        "民族",
        "identity",
        "身份信息",
        "recommended",
        privacy="sensitive",
        aliases=("民族", "ethnicity"),
    ),
    ProfileDimension(
        "political_status",
        "政治面貌",
        "identity",
        "身份信息",
        "recommended",
        privacy="sensitive",
        aliases=("政治面貌", "political status"),
    ),
    ProfileDimension(
        "marital_status",
        "婚姻状况",
        "identity",
        "身份信息",
        "recommended",
        privacy="sensitive",
        aliases=("婚姻状况", "婚姻状态", "marital status"),
    ),
    ProfileDimension(
        "id_type",
        "证件类型",
        "identity_document",
        "证件信息",
        "recommended",
        privacy="sensitive",
        options=("居民身份证", "护照", "港澳居民来往内地通行证", "台湾居民来往大陆通行证", "其他"),
        aliases=("证件类型", "证件类别", "identity type"),
    ),
    ProfileDimension(
        "id_number",
        "证件号码",
        "identity_document",
        "证件信息",
        "recommended",
        privacy="sensitive",
        aliases=("身份证号", "身份证号码", "证件号码", "id number"),
    ),
    ProfileDimension(
        "phone",
        "手机号码",
        "contact",
        "联系方式",
        "essential",
        privacy="sensitive",
        aliases=("手机号", "手机号码", "联系电话", "phone", "mobile"),
        input_type="tel",
    ),
    ProfileDimension(
        "email",
        "电子邮箱",
        "contact",
        "联系方式",
        "essential",
        privacy="sensitive",
        aliases=("邮箱", "电子邮箱", "email", "e-mail"),
        input_type="email",
    ),
    ProfileDimension(
        "wechat",
        "微信号",
        "contact",
        "联系方式",
        "recommended",
        privacy="sensitive",
        aliases=("微信号", "微信", "wechat"),
    ),
    ProfileDimension(
        "current_residence",
        "现居住地",
        "location",
        "所在地信息",
        "recommended",
        privacy="sensitive",
        aliases=("现居住地", "现居城市", "居住地", "current residence"),
        placeholder="省 / 州、城市",
    ),
    ProfileDimension(
        "current_address",
        "当前详细地址",
        "location",
        "所在地信息",
        "recommended",
        privacy="sensitive",
        aliases=("现居住地址", "通讯地址", "联系地址", "current address"),
    ),
    ProfileDimension(
        "household_registration",
        "户籍所在地",
        "location",
        "所在地信息",
        "recommended",
        privacy="sensitive",
        aliases=("户籍", "户口所在地", "户籍所在地", "household registration"),
    ),
    ProfileDimension(
        "native_place",
        "籍贯 / 生源地",
        "location",
        "所在地信息",
        "recommended",
        privacy="sensitive",
        aliases=("生源地", "籍贯", "native place"),
    ),
    ProfileDimension(
        "postal_code",
        "邮政编码",
        "location",
        "所在地信息",
        "recommended",
        aliases=("邮政编码", "邮编", "postal code", "zip code"),
    ),
    ProfileDimension(
        "school_name",
        "最高学历学校",
        "education",
        "主教育信息",
        "recommended",
        aliases=("毕业院校", "学校名称", "学校", "university", "school"),
    ),
    ProfileDimension(
        "college_name",
        "学院 / 院系",
        "education",
        "主教育信息",
        "recommended",
        aliases=("学院", "院系", "学院名称", "college", "department"),
    ),
    ProfileDimension(
        "major",
        "专业",
        "education",
        "主教育信息",
        "recommended",
        aliases=("专业", "所学专业", "major"),
    ),
    ProfileDimension(
        "education_level",
        "最高学历",
        "education",
        "主教育信息",
        "recommended",
        aliases=("学历", "最高学历", "education", "degree level"),
    ),
    ProfileDimension(
        "degree",
        "最高学位",
        "education",
        "主教育信息",
        "recommended",
        aliases=("学位", "最高学位", "degree"),
    ),
    ProfileDimension(
        "education_start_date",
        "入学时间",
        "education",
        "主教育信息",
        "recommended",
        aliases=("入学时间", "教育开始时间", "education start date"),
        placeholder="YYYY-MM",
        input_type="month",
    ),
    ProfileDimension(
        "graduation_date",
        "毕业时间",
        "education",
        "主教育信息",
        "recommended",
        aliases=("毕业时间", "毕业日期", "graduation date"),
        placeholder="YYYY-MM",
        input_type="month",
    ),
    ProfileDimension(
        "student_id",
        "学号",
        "education",
        "主教育信息",
        "recommended",
        privacy="sensitive",
        aliases=("学号", "student id", "student number"),
    ),
    ProfileDimension(
        "gpa",
        "GPA / 平均绩点",
        "education",
        "主教育信息",
        "recommended",
        aliases=("平均绩点", "绩点", "gpa"),
    ),
    ProfileDimension(
        "class_rank",
        "专业或班级排名",
        "education",
        "主教育信息",
        "recommended",
        aliases=("专业排名", "班级排名", "综合排名", "class rank"),
    ),
    ProfileDimension(
        "available_start_date",
        "可到岗时间",
        "employment",
        "求职基础信息",
        "recommended",
        aliases=("可到岗时间", "到岗日期", "available start date"),
        placeholder="YYYY-MM-DD 或文字说明",
    ),
    ProfileDimension(
        "work_authorization",
        "工作许可 / 签证状态",
        "employment",
        "求职基础信息",
        "recommended",
        privacy="sensitive",
        aliases=("工作许可", "签证状态", "work authorization", "visa status"),
    ),
    ProfileDimension(
        "willingness_to_relocate",
        "是否接受调动或异地工作",
        "employment",
        "求职基础信息",
        "recommended",
        options=("是", "否", "视岗位而定"),
        aliases=("是否接受调动", "是否服从调剂", "是否接受异地工作", "relocation"),
    ),
    ProfileDimension(
        "expected_salary",
        "期望薪酬",
        "employment",
        "求职基础信息",
        "recommended",
        aliases=("期望薪酬", "期望月薪", "expected salary"),
    ),
    ProfileDimension(
        "emergency_contact_name",
        "紧急联系人姓名",
        "emergency_contact",
        "紧急联系人",
        "recommended",
        privacy="sensitive",
        aliases=("紧急联系人姓名", "紧急联系人", "emergency contact name"),
    ),
    ProfileDimension(
        "emergency_contact_relationship",
        "与紧急联系人的关系",
        "emergency_contact",
        "紧急联系人",
        "recommended",
        privacy="sensitive",
        aliases=("紧急联系人关系", "与本人关系", "relationship"),
    ),
    ProfileDimension(
        "emergency_contact_phone",
        "紧急联系人电话",
        "emergency_contact",
        "紧急联系人",
        "recommended",
        privacy="sensitive",
        aliases=("紧急联系人电话", "紧急联系人号码", "emergency contact phone"),
        input_type="tel",
    ),
    ProfileDimension(
        "height_cm",
        "身高（厘米）",
        "additional",
        "其他常用信息",
        "recommended",
        aliases=("身高", "身高cm", "height"),
        input_type="number",
    ),
    ProfileDimension(
        "health_history",
        "健康或既往病史",
        "contextual_sensitive",
        "按官网要求再补充",
        "contextual",
        privacy="sensitive",
        initial_prompt=False,
        aliases=("健康状况", "既往病史", "病史", "medical history"),
    ),
    ProfileDimension(
        "disciplinary_record",
        "处分或纪律记录",
        "contextual_sensitive",
        "按官网要求再补充",
        "contextual",
        privacy="sensitive",
        initial_prompt=False,
        aliases=("处分记录", "纪律处分", "disciplinary record"),
    ),
    ProfileDimension(
        "criminal_record",
        "违法犯罪记录",
        "contextual_sensitive",
        "按官网要求再补充",
        "contextual",
        privacy="sensitive",
        initial_prompt=False,
        aliases=("违法犯罪记录", "无犯罪记录", "criminal record"),
    ),
    ProfileDimension(
        "relatives_in_company",
        "亲属在本单位任职情况",
        "contextual_sensitive",
        "按官网要求再补充",
        "contextual",
        privacy="sensitive",
        initial_prompt=False,
        aliases=("亲属在本单位任职", "亲属回避", "relatives in company"),
    ),
    ProfileDimension(
        "family_members",
        "家庭成员信息",
        "contextual_sensitive",
        "按官网要求再补充",
        "contextual",
        privacy="sensitive",
        initial_prompt=False,
        aliases=("家庭成员", "家庭关系", "family members"),
    ),
)


def profile_dimension_catalog() -> dict[str, Any]:
    return {
        "catalog_version": PROFILE_DIMENSION_CATALOG_VERSION,
        "dimensions": [asdict(item) for item in PROFILE_DIMENSIONS],
        "initial_dimension_count": sum(
            item.initial_prompt for item in PROFILE_DIMENSIONS
        ),
        "contextual_dimension_count": sum(
            not item.initial_prompt for item in PROFILE_DIMENSIONS
        ),
    }
