from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .contracts import (
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)


class HealthResponse(BaseModel):
    status: str
    app_name: str
    database_path: str
    runtime_mode: str
    data_root: str
    schema_version: str
    backend_version: str


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    project_type: Literal["appendix_a", "full_report"] = "appendix_a"
    template_package_id: str | None = Field(default=None, max_length=120)
    template_edition: str | None = Field(default=None, max_length=40)
    template_revision: str | None = Field(default=None, max_length=40)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class ProjectUpgradeCopyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    template_package_id: Literal[FULL_REPORT_TEMPLATE_PACKAGE_ID]
    template_edition: Literal[FULL_REPORT_TEMPLATE_EDITION]
    template_revision: Literal[FULL_REPORT_TEMPLATE_REVISION]
    idempotency_key: UUID


class SectionRead(BaseModel):
    id: int
    project_id: int
    code: str
    title: str
    table_title: str
    sort_order: int


class ProjectRead(BaseModel):
    id: int
    project_uuid: str
    name: str
    project_type: Literal["appendix_a", "full_report"]
    workflow_status: Literal["draft", "ready_for_review", "confirmed"]
    template_package_id: str | None = None
    template_edition: str | None = None
    template_revision: str | None = None
    template_asset_set_hash: str | None = None
    source_project_uuid: str | None = None
    created_by_operation: Literal["create", "migration_import", "roundtrip_import", "upgrade_copy"]
    created_at: str
    updated_at: str
    sections: list[SectionRead] = []


class DocxImportIssue(BaseModel):
    severity: str = Field(default="info", max_length=20)
    code: str = Field(min_length=1, max_length=80)
    message: str
    section_code: str | None = Field(default=None, max_length=20)
    target: str | None = Field(default=None, max_length=120)


class DocxImportSectionPreview(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    title: str = Field(default="", max_length=120)
    table_title: str = Field(default="", max_length=200)
    table_type: str = Field(default="", max_length=30)
    row_count: int = 0
    image_count: int = 0
    reference_count: int = 0


class DocxImportJobRead(BaseModel):
    id: int
    status: str
    original_name: str
    source_docx_path: str = ""
    parsed_json_path: str | None = None
    suggested_project_name: str = ""
    created_project_id: int | None = None
    sections: list[DocxImportSectionPreview] = []
    summary: dict[str, int] = Field(default_factory=dict)
    issues: list[DocxImportIssue] = []
    can_create_project: bool = False
    error_message: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class DocxImportCreateProjectRequest(BaseModel):
    project_name: str | None = Field(default=None, max_length=120)


class MetricResultRead(BaseModel):
    d: str | None = None
    a: str | None = None
    k: str | None = None
    ra: str | None = None
    rk: str | None = None
    object_score: str | None = None
    unit_score: str | None = None
    compliance: str | None = None


class MetricResultWrite(BaseModel):
    d: Literal["√", "×", "/", ""] | None = None
    a: Literal["√", "×", "/", ""] | None = None
    k: Literal["√", "×", "/", ""] | None = None
    ra: Literal["1", "0.5", "0.2", ""] | None = None
    rk: Literal["1", "1.2", ""] | None = None
    object_score: str | None = None
    unit_score: str | None = None
    compliance: str | None = None


class CrossReferenceRead(BaseModel):
    id: int
    source_row_id: int
    target_image_id: int | None = None
    token: str
    display_text: str = ""


class CrossReferenceWrite(BaseModel):
    target_image_id: int | None = None
    token: str = Field(default="", max_length=120)
    display_text: str = Field(default="", max_length=120)


class AssessmentRowRead(BaseModel):
    id: int
    row_uuid: str
    section_id: int
    assessment_object_uuid: str | None = None
    unit: str
    object_name: str
    subsystem: str = ""
    record_text: str
    sort_order: int
    metric_result: MetricResultRead


class AssessmentRowWrite(BaseModel):
    id: int | None = Field(default=None, ge=1)
    assessment_object_uuid: str | None = Field(default=None, min_length=36, max_length=36)
    unit: str = Field(default="", max_length=500)
    object_name: str = Field(default="", max_length=500)
    subsystem: str = Field(default="", max_length=500)
    record_text: str = ""
    sort_order: int | None = None
    metric_result: MetricResultWrite = Field(default_factory=MetricResultWrite)
    cross_references: list[CrossReferenceWrite] = []


class EvidenceImageRead(BaseModel):
    id: int
    evidence_uuid: str
    project_image_no: int | None = None
    project_id: int
    section_code: str
    file_path: str
    original_name: str
    caption: str
    alt_text: str
    sort_order: int
    pixel_width: int | None = None
    pixel_height: int | None = None
    dpi_x: float | None = None
    dpi_y: float | None = None
    display_width_in: float | None = None
    display_height_in: float | None = None
    created_at: str
    updated_at: str
    file_url: str | None = None
    figure_label: str | None = None
    warnings: list[str] = []


class EvidenceImageUpdate(BaseModel):
    section_code: str | None = Field(default=None, max_length=20)
    caption: str | None = None
    alt_text: str | None = None
    sort_order: int | None = None
    display_width_in: float | None = None
    display_height_in: float | None = None


class EvidenceOrderUpdate(BaseModel):
    image_ids: list[int]


class SectionDetailRead(BaseModel):
    section: SectionRead
    rows: list[AssessmentRowRead]
    subsystems: list[str] = []
    evidence_images: list[EvidenceImageRead]
    cross_references: list[CrossReferenceRead]


class SectionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    table_title: str | None = Field(default=None, max_length=200)
    subsystems: list[str] | None = None
    rows: list[AssessmentRowWrite] = []


class SectionProjectImport(BaseModel):
    target_project_id: int


class RecordTemplateRead(BaseModel):
    id: str
    section_code: str
    table_type: str
    unit: str
    object_name: str
    title: str
    record_text: str
    source_row: int | None = None
    source_type: str = "system"
    tags: list[str] = Field(default_factory=list)
    is_enabled: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class RecordTemplateCreate(BaseModel):
    section_code: str = Field(min_length=1, max_length=20)
    table_type: str = Field(min_length=1, max_length=30)
    unit: str = Field(default="", max_length=500)
    object_name: str = Field(default="", max_length=500)
    title: str = Field(default="", max_length=500)
    record_text: str = ""
    tags: list[str] = Field(default_factory=list)


class RecordTemplateUpdate(BaseModel):
    section_code: str | None = Field(default=None, max_length=20)
    table_type: str | None = Field(default=None, max_length=30)
    unit: str | None = Field(default=None, max_length=500)
    object_name: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=500)
    record_text: str | None = None
    tags: list[str] | None = None


class RecordTemplateDelete(BaseModel):
    id: str

class RecordTemplateImportItem(BaseModel):
    id: str | None = None
    template_key: str | None = None
    section_code: str = Field(min_length=1, max_length=20)
    table_type: str = Field(min_length=1, max_length=30)
    unit: str = Field(default="", max_length=500)
    object_name: str = Field(default="", max_length=500)
    title: str = Field(default="", max_length=500)
    record_text: str = ""
    tags: list[str] = Field(default_factory=list)


class RecordTemplateImportPayload(BaseModel):
    profile_id: str | None = None
    exported_at: str | None = None
    templates: list[RecordTemplateImportItem]


class RecordTemplateImportSummary(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0


class RecordTemplateImportResultItem(BaseModel):
    index: int
    action: str
    message: str
    template_id: str | None = None
    section_code: str = ""
    unit: str = ""
    object_name: str = ""
    title: str = ""


class RecordTemplateImportResult(BaseModel):
    summary: RecordTemplateImportSummary
    items: list[RecordTemplateImportResultItem] = []


class RecordTemplateExport(BaseModel):
    profile_id: str
    exported_at: str
    templates: list[RecordTemplateImportItem] = []

class RecordTemplateSlotRead(BaseModel):
    id: int
    section_code: str
    table_type: str
    unit: str
    template_group: str
    template_group_label: str
    template_type: str
    template_type_label: str
    title: str
    record_text: str
    default_record_text: str
    tags: list[str] = Field(default_factory=list)
    is_customized: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class RecordTemplateSlotUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    record_text: str | None = None
    tags: list[str] | None = None


class RecordTemplateSlotImportItem(BaseModel):
    section_code: str = Field(min_length=1, max_length=20)
    table_type: str = Field(min_length=1, max_length=30)
    unit: str = Field(default="", max_length=500)
    template_group: str = Field(default="verification_record", max_length=40)
    template_type: str = Field(min_length=1, max_length=30)
    title: str = Field(default="", max_length=500)
    record_text: str = ""
    tags: list[str] = Field(default_factory=list)


class RecordTemplateSlotImportPayload(BaseModel):
    profile_id: str | None = None
    exported_at: str | None = None
    templates: list[RecordTemplateSlotImportItem]


class RecordTemplateSlotExport(BaseModel):
    profile_id: str
    exported_at: str
    templates: list[RecordTemplateSlotImportItem] = []


class RecordTemplateSlotImportPreviewItem(BaseModel):
    index: int
    action: str
    message: str
    slot_id: int | None = None
    section_code: str = ""
    unit: str = ""
    template_group: str = ""
    template_type: str = ""
    title: str = ""


class RecordTemplateSlotImportPreview(BaseModel):
    summary: RecordTemplateImportSummary
    items: list[RecordTemplateSlotImportPreviewItem] = []

class ValidationSummary(BaseModel):
    errors: int = 0
    warnings: int = 0
    info: int = 0


class ValidationIssueRead(BaseModel):
    id: int | None = None
    project_id: int
    severity: str
    code: str
    message: str
    target_type: str | None = None
    target_id: str | None = None
    created_at: str | None = None


class ValidationResponse(BaseModel):
    summary: ValidationSummary
    issues: list[ValidationIssueRead]


class RenderJobCreate(BaseModel):
    mode: str = Field(default="final", pattern="^(editable|final)$")


class RenderJobRead(BaseModel):
    id: int
    project_id: int
    status: str
    mode: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    output_docx_path: str | None = None
    output_pdf_path: str | None = None
    output_docx_url: str | None = None
    output_pdf_url: str | None = None
    page_count: int | None = None
    log_path: str | None = None
    log_url: str | None = None
    error_message: str | None = None
