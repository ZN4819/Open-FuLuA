"""Stable API models for R7 controlled Word roundtrip."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


RoundtripStatus = Literal[
    "uploaded",
    "validating",
    "invalid",
    "diff_ready",
    "conflicts_pending",
    "ready_to_commit",
    "committing",
    "succeeded",
    "failed",
    "stale",
]
ResolutionAction = Literal["keep_database", "apply_word"]


class RoundtripModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportRoundtripJobRead(BaseModel):
    id: int
    project_uuid: str
    mode: Literal["roundtrip"] = "roundtrip"
    status: RoundtripStatus
    original_name: str
    base_project_revision: int
    observed_project_revision: int
    source_snapshot_id: str | None = None
    source_docx_hash: str | None = None
    manifest_hash: str | None = None
    source_snapshot_hash: str | None = None
    writable_contract_hash: str | None = None
    diff_hash: str | None = None
    resolution_hash: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    committed_at: str | None = None


class ReportRoundtripIssueRead(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    blocks_progress: bool = False
    phase: str | None = None
    field_id: str | None = None
    field_path: str | None = None
    row_id: str | None = None
    section_code: str | None = None
    object_name: str | None = None
    remediation: str | None = None
    three_way_summary: dict[str, str | None] | None = None


class ReportRoundtripIssueCollection(BaseModel):
    job_id: int
    status: RoundtripStatus
    errors: list[ReportRoundtripIssueRead] = Field(default_factory=list)
    warnings: list[ReportRoundtripIssueRead] = Field(default_factory=list)
    info: list[ReportRoundtripIssueRead] = Field(default_factory=list)


class ReportRoundtripDiffItem(BaseModel):
    id: str
    conflict_id: str | None = None
    field_path: str
    field_label: str | None = None
    field_type: str | None = None
    section_code: str | None = None
    section_title: str | None = None
    entity_uuid: str | None = None
    object_name: str | None = None
    row_id: str | None = None
    base_value: Any = None
    database_value: Any = None
    word_value: Any = None
    disposition: Literal[
        "unchanged", "keep_database", "apply_word", "already_equal", "conflict", "ignored"
    ]
    resolution: ResolutionAction | None = None
    ignored_reason: str | None = None


class ReportRoundtripDiffGroup(BaseModel):
    group_key: str
    section_code: str | None = None
    section_title: str | None = None
    object_name: str | None = None
    items: list[ReportRoundtripDiffItem] = Field(default_factory=list)


class ReportRoundtripDiffRead(BaseModel):
    job_id: int
    status: RoundtripStatus
    diff_hash: str
    base_project_revision: int
    observed_project_revision: int
    summary: dict[str, int] = Field(default_factory=dict)
    groups: list[ReportRoundtripDiffGroup] = Field(default_factory=list)
    items: list[ReportRoundtripDiffItem] = Field(default_factory=list)
    ignored_changes: list[ReportRoundtripDiffItem] = Field(default_factory=list)


class ReportRoundtripResolutionItem(RoundtripModel):
    conflict_id: str = Field(min_length=8, max_length=80)
    action: ResolutionAction


class ReportRoundtripResolutionWrite(RoundtripModel):
    diff_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_project_revision: int = Field(ge=1)
    resolutions: list[ReportRoundtripResolutionItem] = Field(min_length=1, max_length=5000)

    @field_validator("resolutions")
    @classmethod
    def unique_conflicts(
        cls, values: list[ReportRoundtripResolutionItem]
    ) -> list[ReportRoundtripResolutionItem]:
        ids = [item.conflict_id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("同一冲突不能重复提交")
        return values


class ReportRoundtripResolutionRead(BaseModel):
    job_id: int
    status: RoundtripStatus
    diff_hash: str
    resolution_hash: str
    expected_project_revision: int
    resolved_conflicts: int


class ReportRoundtripCommitWrite(RoundtripModel):
    resolution_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_project_revision: int = Field(ge=1)


class ReportRoundtripCommitRead(BaseModel):
    job_id: int
    status: RoundtripStatus
    project_uuid: str
    before_revision: int
    after_revision: int | None = None
    resolution_hash: str
    applied_fields: int
    kept_fields: int
    ignored_changes: int
    error_code: str | None = None
    error_message: str | None = None
