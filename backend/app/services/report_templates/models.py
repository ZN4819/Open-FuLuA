"""R0 模板取证的稳定、脱敏数据契约。"""

from __future__ import annotations

from typing import Any, Literal

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


class Condition(StrictModel):
    operator: Literal["always", "equals", "in", "present"]
    field_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.]{2,127}$")
    value: str | bool | int | list[str] | None = None


class ReportField(StrictModel):
    field_id: str = Field(pattern=r"^[a-z][a-z0-9_.]{2,127}$")
    label: str = Field(min_length=1, max_length=100)
    data_type: Literal["string", "long_text", "date", "date_range", "boolean", "enum", "integer", "decimal", "attachment", "attachment_list", "object_list", "derived", "confirmed", "conditional_block"]
    data_domain: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    cardinality: Literal["one", "many"]
    required_when: Condition
    source_kind: list[Literal["manual", "imported", "derived", "appendix_a"]] = Field(min_length=1, max_length=4)
    source_evidence: list[str] = Field(default_factory=list, max_length=20)
    format: dict[str, Any] = Field(default_factory=dict)
    sensitivity: Literal["public", "internal", "sensitive"]
    review_required: bool
    export_slots: list[str] = Field(default_factory=list, max_length=20)
    template_edition: Literal["2023"] = "2023"
    template_revision: Literal["2025-12-08"] = "2025-12-08"


class FieldDictionary(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    fields: list[ReportField] = Field(min_length=1, max_length=1000)


class RuleHint(StrictModel):
    rule_id: str = Field(pattern=r"^hint_[0-9]{3}$")
    source_comment_id: int = Field(ge=0)
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    anchor_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    category: Literal["field_source", "consistency", "conditional", "authoring_help", "evidence", "layout", "history"]
    sanitized_summary: str = Field(min_length=1, max_length=300)
    target_fields: list[str] = Field(default_factory=list, max_length=20)
    approval_status: Literal["pending", "approved", "rejected", "deprecated"]
    runtime_behavior: Literal["none", "help", "warning"]


class RuleHintLibrary(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    rules: list[RuleHint] = Field(min_length=1, max_length=1000)


class NarrativeTemplate(StrictModel):
    template_id: str = Field(pattern=r"^narrative\.[a-z0-9_.]{3,100}$")
    section_id: str = Field(pattern=r"^[a-z0-9_.-]{2,100}$")
    conclusion: Literal["generic", "conformant", "partially_conformant", "nonconformant", "not_applicable"]
    text: str = Field(min_length=1, max_length=4000)
    variables: list[str] = Field(default_factory=list, max_length=30)
    user_confirmation_required: Literal[True] = True
    template_edition: Literal["2023"] = "2023"
    template_revision: Literal["2025-12-08"] = "2025-12-08"


class NarrativeTemplateLibrary(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    templates: list[NarrativeTemplate] = Field(min_length=1, max_length=200)
