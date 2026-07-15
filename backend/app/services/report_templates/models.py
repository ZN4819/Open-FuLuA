"""R0 模板取证的稳定、脱敏数据契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackageSummary(StrictModel):
    part_count: int = Field(ge=0)
    uncompressed_bytes: int = Field(ge=0)
    relationship_count: int = Field(ge=0)
    external_relationship_count: int = Field(ge=0)
    media_count: int = Field(ge=0)


class DocumentSummary(StrictModel):
    body_paragraph_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    section_count: int = Field(ge=0)
    header_part_count: int = Field(ge=0)
    footer_part_count: int = Field(ge=0)
    content_control_count: int = Field(ge=0)
    dropdown_control_count: int = Field(ge=0)
    bookmark_count: int = Field(ge=0)
    field_instruction_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)
    revision_count: int = Field(ge=0)


class ContentFlags(StrictModel):
    has_macros: bool
    has_activex: bool
    has_ole_or_embeddings: bool
    has_custom_xml: bool
    has_digital_signatures: bool
    has_external_relationships: bool
    has_attached_template: bool
    has_alt_chunk: bool


class StructuralSignature(StrictModel):
    index: int = Field(ge=0)
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnalysisIssue(StrictModel):
    code: str = Field(pattern=r"^[A-Z0-9_]{3,64}$")
    severity: Literal["info", "warning", "error"]
    part: str | None = Field(default=None, max_length=255)


class ReportTemplateForensics(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    source_role: Literal["base_template", "customer_sample", "synthetic_fixture"]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_size_bytes: int = Field(ge=0)
    package: PackageSummary
    document: DocumentSummary
    flags: ContentFlags
    section_signatures: list[StructuralSignature]
    table_signatures: list[StructuralSignature]
    issues: list[AnalysisIssue]
