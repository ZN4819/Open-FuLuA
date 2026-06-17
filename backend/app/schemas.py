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


class SectionDetailRead(BaseModel):
    section: SectionRead
    rows: list[AssessmentRowRead]
    evidence_images: list[EvidenceImageRead]
    cross_references: list[CrossReferenceRead]


class SectionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    table_title: str | None = Field(default=None, max_length=200)
    rows: list[AssessmentRowWrite] = []
