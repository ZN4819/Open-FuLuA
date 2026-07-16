"""R2 完整报告数据域的稳定 API 契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RevisionWrite(ReportModel):
    expected_revision: int = Field(ge=0)


class ReportMetadataWrite(RevisionWrite):
    report_number: str = Field(default="", max_length=120)
    default_export_version: str = Field(default="V1.0", min_length=1, max_length=40)
    classification_level: str = Field(default="三级", max_length=40)
    confidentiality_level: str = Field(default="", max_length=40)
    compiler_member_uuid: str | None = Field(default=None, max_length=36)
    reviewer_member_uuid: str | None = Field(default=None, max_length=36)
    approver_member_uuid: str | None = Field(default=None, max_length=36)
    controlled_extension: dict[str, str | int | bool | None] = Field(default_factory=dict)


OrganizationType = Literal["assessed", "client", "vendor", "other"]


class OrganizationWrite(ReportModel):
    organization_type: OrganizationType
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(default="", max_length=500)
    postal_code: str = Field(default="", max_length=20)
    contact_name: str = Field(default="", max_length=100)
    contact_title: str = Field(default="", max_length=100)
    contact_department: str = Field(default="", max_length=100)
    office_phone: str = Field(default="", max_length=80)
    mobile_phone: str = Field(default="", max_length=80)
    email: str = Field(default="", max_length=200)
    active: bool = True
    sort_order: int = Field(default=0, ge=0)


class OrganizationUpdate(OrganizationWrite, RevisionWrite):
    pass


class MemberWrite(ReportModel):
    organization_uuid: str | None = Field(default=None, max_length=36)
    name: str = Field(min_length=1, max_length=100)
    team_role: Literal["member", "leader"] = "member"
    is_leader: bool = False
    qualification_passed_at: str | None = Field(default=None, max_length=40)
    title: str = Field(default="", max_length=100)
    department: str = Field(default="", max_length=100)
    certificate_no: str = Field(default="", max_length=120)
    office_phone: str = Field(default="", max_length=80)
    mobile_phone: str = Field(default="", max_length=80)
    email: str = Field(default="", max_length=200)
    active: bool = True
    sort_order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def align_leader_role(self) -> "MemberWrite":
        if self.is_leader != (self.team_role == "leader"):
            raise ValueError("team_role 与 is_leader 必须一致")
        return self


class MemberUpdate(MemberWrite, RevisionWrite):
    pass


class OnsiteRecord(ReportModel):
    entry_date: str = Field(min_length=1, max_length=40)
    exit_date: str = Field(min_length=1, max_length=40)
    member_uuids: list[str] = Field(min_length=1, max_length=100)


class TravelRecord(ReportModel):
    local_project: bool = False
    start_date: str | None = Field(default=None, max_length=40)
    end_date: str | None = Field(default=None, max_length=40)
    member_uuids: list[str] = Field(min_length=1, max_length=100)


class PhaseDatesWrite(RevisionWrite):
    preparation_start: str | None = Field(default=None, max_length=40)
    preparation_end: str | None = Field(default=None, max_length=40)
    plan_start: str | None = Field(default=None, max_length=40)
    plan_end: str | None = Field(default=None, max_length=40)
    onsite_start: str | None = Field(default=None, max_length=40)
    onsite_end: str | None = Field(default=None, max_length=40)
    report_start: str | None = Field(default=None, max_length=40)
    report_end: str | None = Field(default=None, max_length=40)
    travel_records: list[TravelRecord] = Field(default_factory=list, max_length=100)
    onsite_records: list[OnsiteRecord] = Field(default_factory=list, max_length=100)
    plan_review_date: str | None = Field(default=None, max_length=40)
    report_review_date: str | None = Field(default=None, max_length=40)
    approval_date: str | None = Field(default=None, max_length=40)


class DistributionWrite(RevisionWrite):
    regulator_copies: int = Field(ge=0, le=100)
    client_copies: int = Field(ge=0, le=100)
    assessment_copies: int = Field(ge=0, le=100)


class SystemProfileWrite(RevisionWrite):
    system_name: str = Field(default="", max_length=300)
    system_summary: str = Field(default="", max_length=20000)
    critical_infrastructure_status: Literal["recognized", "not_recognized", ""] = ""
    critical_infrastructure_department: str = Field(default="", max_length=300)
    level_filing_status: Literal["filed", "not_filed", ""] = ""
    filing_s: str = Field(default="", max_length=80)
    filing_a: str = Field(default="", max_length=80)
    filing_g: str = Field(default="", max_length=80)
    filing_certificate_no: str = Field(default="", max_length=120)
    filing_system_same: bool | None = None
    filing_system_name: str = Field(default="", max_length=300)
    filing_difference: str = Field(default="", max_length=2000)
    level_assessment_status: Literal["assessed", "assessing", "not_assessed", ""] = ""
    level_assessment_organization: str = Field(default="", max_length=300)
    level_assessment_date: str | None = Field(default=None, max_length=40)
    level_assessment_conclusion: str = Field(default="", max_length=1000)
    cloud_dependency: Literal["yes", "no", ""] = ""
    cloud_platform_name: str = Field(default="", max_length=300)
    cloud_assessment_status: Literal["assessed", "assessing", "not_assessed", ""] = ""
    cloud_assessment_organization: str = Field(default="", max_length=300)
    cloud_assessment_date: str | None = Field(default=None, max_length=40)
    cloud_assessment_conclusion: str = Field(default="", max_length=1000)
    crypto_plan_status: Literal["passed", "not_passed", "none", ""] = ""
    crypto_plan_passed_at: str | None = Field(default=None, max_length=40)
    crypto_plan_assessment_mode: Literal["self", "commissioned", ""] = ""
    crypto_plan_assessment_organization: str = Field(default="", max_length=300)
    operation_status: Literal["running", "not_running", ""] = ""
    operation_started_at: str | None = Field(default=None, max_length=40)
    construction_stage: str = Field(default="", max_length=300)
    service_scope: Literal["national", "cross_province", "province", "cross_city", "local", "other", ""] = ""
    service_scope_count: int | None = Field(default=None, ge=1)
    service_scope_other: str = Field(default="", max_length=300)
    no_crypto_products: bool = False
    selected_algorithms: list[str] = Field(default_factory=list, max_length=100)
    other_algorithms: list[str] = Field(default_factory=list, max_length=100)
    application_catalog: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("selected_algorithms", "other_algorithms", "application_catalog")
    @classmethod
    def unique_clean_values(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned


class CryptoProductWrite(ReportModel):
    name: str = Field(min_length=1, max_length=300)
    model: str = Field(default="", max_length=200)
    manufacturer: str = Field(default="", max_length=300)
    certificate_no: str = Field(default="", max_length=200)
    quantity_text: str = Field(min_length=1, max_length=40)
    use_mode: Literal["exclusive", "shared"]
    classification: Literal["certified", "uncertified_domestic", "foreign"]
    sort_order: int = Field(default=0, ge=0)


class CryptoProductUpdate(CryptoProductWrite, RevisionWrite):
    pass


class StandardWrite(ReportModel):
    code: str = Field(default="", max_length=100)
    name: str = Field(min_length=1, max_length=500)
    source_ref: str = Field(default="", max_length=500)
    sort_order: int = Field(default=0, ge=0)


class StandardUpdate(StandardWrite, RevisionWrite):
    pass


class SpecialIndicatorWrite(ReportModel):
    manual_standard_uuid: str = Field(min_length=36, max_length=36)
    indicator_code: str = Field(default="", max_length=100)
    indicator_name: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=5000)
    sort_order: int = Field(default=0, ge=0)


class SpecialIndicatorUpdate(SpecialIndicatorWrite, RevisionWrite):
    pass


class AssessmentObjectWrite(ReportModel):
    object_type: Literal["physical", "network", "device", "application", "data", "management", "other"]
    name_snapshot: str = Field(min_length=1, max_length=500)
    source_section_code: Literal["A-1", "A-2", "A-3", "A-4", "A-5", "A-6", "A-7", "A-8"] | None = None
    source_row_id: int | None = Field(default=None, ge=1)
    properties: dict[str, str | int | bool | None] = Field(default_factory=dict)
    active: bool = True


class AssessmentObjectUpdate(AssessmentObjectWrite, RevisionWrite):
    pass


AssessmentMethod = Literal["访谈", "文档审查", "现场检查", "配置检查", "工具测试"]


class ObjectSubsystemWrite(ReportModel):
    object_uuid: str = Field(min_length=36, max_length=36)
    subsystem_name: str = Field(min_length=1, max_length=500)
    methods: list[AssessmentMethod] = Field(default_factory=list, max_length=5)
    remark: str = Field(default="", max_length=5000)
    expected_revision: int | None = Field(default=None, ge=0)


class ObjectRelationWrite(ReportModel):
    source_object_uuid: str = Field(min_length=36, max_length=36)
    target_object_uuid: str = Field(min_length=36, max_length=36)
    relation_type: Literal["contains", "connects", "depends_on", "protects", "uses", "other"]
    properties: dict[str, str | int | bool | None] = Field(default_factory=dict)
    active: bool = True


class ObjectRelationUpdate(ObjectRelationWrite, RevisionWrite):
    pass


class CorrectionOriginalReferences(ReportModel):
    a2_row_id: int | None = Field(default=None, ge=1)
    a4_row_id: int | None = Field(default=None, ge=1)


class CorrectionRelationWrite(ReportModel):
    a2_object_uuid: str = Field(min_length=36, max_length=36)
    a4_object_uuid: str = Field(min_length=36, max_length=36)
    correction_kind: Literal["confidentiality", "integrity"]
    a2_metric_code: str = Field(min_length=1, max_length=200)
    a4_metric_code: str = Field(min_length=1, max_length=200)
    original_references: CorrectionOriginalReferences = Field(default_factory=CorrectionOriginalReferences)


class CorrectionRelationUpdate(CorrectionRelationWrite, RevisionWrite):
    pass


class BindingChoice(ReportModel):
    source_row_id: int = Field(ge=1)
    object_uuid: str = Field(min_length=36, max_length=36)


class BindingConfirmWrite(ReportModel):
    choices: list[BindingChoice] = Field(min_length=1, max_length=5000)


class ObjectMergeWrite(ReportModel):
    target_object_uuid: str = Field(min_length=36, max_length=36)
    source_expected_revision: int = Field(ge=0)
    target_expected_revision: int = Field(ge=0)


BlockType = Literal[
    "paragraph",
    "bullet_list",
    "numbered_list",
    "key_value_table",
    "data_table",
    "figure",
    "reference",
    "generated",
]


class ReportBlockCreate(ReportModel):
    block_type: BlockType
    payload: dict[str, Any]
    sort_order: int | None = Field(default=None, ge=0)


class ReportBlockPatch(RevisionWrite):
    payload: dict[str, Any]


class ReportSectionUpdate(RevisionWrite):
    completion_status: Literal["not_started", "in_progress", "complete"]


class BlockReorderItem(ReportModel):
    block_uuid: str = Field(min_length=36, max_length=36)
    sort_order: int = Field(ge=0)
    expected_revision: int = Field(ge=0)


class BlockReorderWrite(ReportModel):
    section_uuid: str = Field(min_length=36, max_length=36)
    items: list[BlockReorderItem] = Field(min_length=1, max_length=500)


class ReportValidationIssue(BaseModel):
    relation_id: str
    entity_path: str
    field: str | None = None
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    target: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class WarningConfirmationWrite(ReportModel):
    relation_id: str = Field(min_length=1, max_length=160)
    entity_path: str = Field(min_length=1, max_length=300)
    warning_code: str = Field(min_length=1, max_length=100)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationRunWrite(ReportModel):
    expected_project_revision: int = Field(ge=1)


class RiskUpdateWrite(ReportModel):
    expected_project_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    risk_level: Literal["high", "medium", "low"] | None = None
    threat_ids: list[str] = Field(default_factory=list, max_length=24)
    analysis_text: str | None = Field(default=None, max_length=10000)
    override_reason: str = Field(default="", max_length=2000)
    confirm: bool = False

    @field_validator("threat_ids")
    @classmethod
    def unique_threat_ids(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned


class DerivedBlockOverrideWrite(ReportModel):
    expected_project_revision: int = Field(ge=1)
    override: dict[str, Any]
    override_reason: str = Field(min_length=1, max_length=2000)


class DerivedBlockConfirmationWrite(ReportModel):
    expected_project_revision: int = Field(ge=1)
    action: Literal["confirm", "keep_override", "discard_override", "reset"] = "confirm"


class ConsistencyCheckWrite(ReportModel):
    expected_project_revision: int = Field(ge=1)


class ReportExportJobWrite(ReportModel):
    mode: Literal["draft", "final"]
    version: str = Field(default="V1.0", min_length=3, max_length=40)
    expected_project_revision: int = Field(ge=1)
