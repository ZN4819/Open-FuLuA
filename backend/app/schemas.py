from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    app_name: str
    database_path: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class SectionRead(BaseModel):
    id: int
    project_id: int
    code: str
    title: str
    table_title: str
    sort_order: int


class ProjectRead(BaseModel):
    id: int
    name: str
    created_at: str
    updated_at: str
    sections: list[SectionRead] = []


class MetricResultRead(BaseModel):
    d: str | None = None
    a: str | None = None
    k: str | None = None
    object_score: str | None = None
    unit_score: str | None = None
    compliance: str | None = None


class MetricResultWrite(BaseModel):
    d: str | None = None
    a: str | None = None
    k: str | None = None
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
    section_id: int
    unit: str
    object_name: str
    record_text: str
    sort_order: int
    metric_result: MetricResultRead


class AssessmentRowWrite(BaseModel):
    unit: str = Field(default="", max_length=500)
    object_name: str = Field(default="", max_length=500)
    record_text: str = ""
    sort_order: int | None = None
    metric_result: MetricResultWrite = Field(default_factory=MetricResultWrite)
    cross_references: list[CrossReferenceWrite] = []


class EvidenceImageRead(BaseModel):
    id: int
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
    evidence_images: list[EvidenceImageRead]
    cross_references: list[CrossReferenceRead]


class SectionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    table_title: str | None = Field(default=None, max_length=200)
    rows: list[AssessmentRowWrite] = []


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
