"""R6 完整报告迁移 API 合同。"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportImportFingerprint(StrictModel):
    sha256: str = Field(default="", max_length=64)
    table_count: int = Field(default=0, ge=0)
    section_count: int = Field(default=0, ge=0)
    top_level_table_columns: list[int] = Field(default_factory=list, max_length=100)
    heading_matches: list[str] = Field(default_factory=list, max_length=20)
    heading_indices: list[int] = Field(default_factory=list, max_length=20)
    matched: bool = False


class ReportImportIssueRead(StrictModel):
    id: int
    revision: int = Field(ge=1)
    code: str
    severity: Literal["info", "warning", "error"]
    association_id: str | None = None
    authority_field_id: str | None = None
    field_path: str = ""
    source_locator: str = ""
    original_text: str = ""
    original_text_truncated: bool = False
    source_value_hash: str | None = None
    candidate_value: Any = None
    confidence: Literal["exact", "high", "ambiguous", "unmapped"]
    status: Literal["open", "resolved", "ignored"]
    needs_confirmation: bool = False
    blocks_confirmation: bool = False
    blocks_final_export: bool = False
    created_at: str
    updated_at: str


class ReportImportResolutionRead(StrictModel):
    id: int
    issue_id: int
    issue_revision: int = Field(ge=1)
    association_id: str | None = None
    authority_field_id: str | None = None
    field_path: str = ""
    action: Literal["adopt_candidate", "keep_original", "skip"]
    resolved_value: Any = None
    resolved_by_user: bool = True
    applied: bool = False
    created_at: str
    updated_at: str


class ReportImportJobRead(StrictModel):
    id: int
    status: Literal["uploaded", "parsing", "preview_ready", "confirming", "succeeded", "failed"]
    mode: Literal["migration", "roundtrip"]
    job_revision: int = Field(ge=1)
    original_name: str
    detected_edition: str | None = None
    detected_revision: str | None = None
    fingerprint: ReportImportFingerprint = Field(default_factory=ReportImportFingerprint)
    summary: dict[str, Any] = Field(default_factory=dict)
    issues: list[ReportImportIssueRead] = Field(default_factory=list)
    resolutions: list[ReportImportResolutionRead] = Field(default_factory=list)
    appendix_a_source: Literal["document", "existing_project"] | None = None
    confirmable: bool = False
    created_project_uuid: str | None = None
    created_project_updated_at: str | None = None
    error_message: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class ReportImportResolutionWrite(StrictModel):
    issue_id: int = Field(gt=0)
    revision: int = Field(ge=1)
    action: Literal["adopt_candidate", "keep_original", "skip"]
    resolved_value: Any = None


class ReportImportResolutionsWrite(StrictModel):
    job_revision: int = Field(ge=1)
    expected_project_updated_at: str | None = Field(default=None, max_length=64)
    resolutions: list[ReportImportResolutionWrite] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_issues(self) -> "ReportImportResolutionsWrite":
        issue_ids = [item.issue_id for item in self.resolutions]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("同一批解决项不能重复指定 issue_id")
        return self


class ReportImportConfirmWrite(StrictModel):
    job_revision: int = Field(ge=1)
    project_name: str = Field(min_length=1, max_length=120)
    appendix_a_source: Literal["document", "existing_project"] = "document"
    appendix_a_project_uuid: UUID | None = None
    accepted_resolutions: list[int] = Field(default_factory=list, max_length=1000)
    keep_unresolved_original: bool = True

    @model_validator(mode="after")
    def validate_appendix_source(self) -> "ReportImportConfirmWrite":
        if self.appendix_a_source == "existing_project" and self.appendix_a_project_uuid is None:
            raise ValueError("从已有附录 A 项目复制时必须指定项目标识")
        if self.appendix_a_source == "document" and self.appendix_a_project_uuid is not None:
            raise ValueError("DOCX 附录来源不应指定已有项目")
        if len(self.accepted_resolutions) != len(set(self.accepted_resolutions)):
            raise ValueError("accepted_resolutions 不能包含重复值")
        return self


class ReportAppendixACopyWrite(StrictModel):
    source_project_uuid: UUID
    idempotency_key: UUID


class ReportAppendixACopyRead(StrictModel):
    target_project_uuid: str
    source_project_uuid: str
    idempotency_key: str
    copied_row_count: int = Field(ge=0)
    copied_image_count: int = Field(ge=0)
    repeated: bool = False
