"""R0 模板取证的稳定、脱敏数据契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EXPECTED_BUSINESS_FIELD_COUNT = 26
EXPECTED_SEMANTIC_SCALAR_SLOT_COUNT = 29
EXPECTED_OOXML_CONTENT_CONTROL_COUNT = 612
EXPECTED_WORD_CONTENT_CONTROL_COUNT = 605
EXPECTED_TEMPLATE_CONTENT_CONTROL_COUNT = 583
EXPECTED_WORD_ACCEPTANCE_EVIDENCE_SHA256 = "9af7dfc44ce7bb19d30d42163ebebabceff4ee08d116026d9dcec8b286c0461b"
README_RULE_COUNTS = {1: 10, 2: 11, 3: 16, 4: 11, 5: 8, 6: 14}
REQUIRED_README_RULE_REFS = frozenset(
    f"3.6.{section}.{index:02d}"
    for section, count in README_RULE_COUNTS.items()
    for index in range(1, count + 1)
)
REQUIRED_PROJECTION_CATALOG = frozenset({
    "block:report.assessment_conclusion",
    "block:report.assessment_conclusion.risk",
    "block:report.assessment_conclusion.system_summary",
    "block:report.assessment_summary",
    "block:report.assessment_summary.risk",
    "block:report.chapter7.conclusion",
    "block:report.cover.organizations",
    "block:report.distribution",
    "block:report.improvement_suggestions",
    "block:report.narrative.organization_references",
    "block:report.overall_evaluation",
    "block:report.overall_evaluation.objects",
    "block:report.reference_standards",
    "block:report.risk_analysis.summary",
    "block:report.security_issues",
    "block:report.special_indicators",
    "block:report.template.assessment_organization",
    "export:report.appendix_a",
    "export:report.appendix_a.corrected_display",
    "export:report.filename",
    "export:report.snapshot",
    "export:report.version",
    "export:report.xlsx",
    "export:report.xlsx.numeric_display",
    "export:report.xlsx_management",
    "export:report.xlsx_technical",
    "field:report.approval.compiled_date",
    "field:report.assessment.period",
    "field:report.assessment.range",
    "field:report.organization.effective_client_name",
    "field:report.organization.operator_name",
    "field:report.system.crypto_product_total",
    "service:report.a4_application_subset_validation",
    "service:report.algorithm_export_validation",
    "service:report.algorithm_warning_validation",
    "service:report.appendix_a_authority",
    "service:report.approval_role_validation",
    "service:report.basic_information_branch_validation",
    "service:report.bidirectional_score_correction",
    "service:report.correction_relation_cardinality",
    "service:report.correction_relation_validation",
    "service:report.correction_slash_handling",
    "service:report.crypto_product_invariants",
    "service:report.final_conclusion",
    "service:report.indicator_conclusion_aggregation",
    "service:report.narrative_confirmation",
    "service:report.narrative_staleness",
    "service:report.risk_count_invariants",
    "service:report.team_qualification_validation",
    "slot:report.header.report_number",
    "slot:report.identity.date",
    "slot:report.identity.number",
    "slot:report.result.conclusion",
    "slot:report.result.overall_score",
    "slot:report.risk.high_risk_judgement",
    "slot:report.system.name",
    "table:report.appendix_a_management",
    "table:report.appendix_a_technical",
    "table:report.appendix_b2",
    "table:report.appendix_b3",
    "table:report.appendix_b4.compiler",
    "table:report.appendix_b5",
    "table:report.appendix_b6",
    "table:report.appendix_b9",
    "table:report.basic_information",
    "table:report.basic_information.approval_dates",
    "table:report.basic_information.cloud",
    "table:report.basic_information.compiler",
    "table:report.basic_information.critical_infrastructure",
    "table:report.basic_information.crypto_plan",
    "table:report.basic_information.level_assessment",
    "table:report.basic_information.level_filing",
    "table:report.basic_information.operation",
    "table:report.basic_information.reviewers",
    "table:report.basic_information.service_scope",
    "table:report.chapter3_methods",
    "table:report.chapter4",
    "table:report.not_applicable_indicators",
    "table:report.risk_analysis",
    "table:report.risk_analysis.threats",
    "table:report.table_2_3",
    "table:report.table_3_4",
    "table:report.table_3_5",
    "table:report.table_3_6",
    "table:report.table_3_7",
    "table:report.table_4_1_to_4_11",
    "table:report.table_4_5",
    "table:report.table_4_6",
    "table:report.table_4_7",
    "table:report.table_5_1",
    "table:report.table_5_2",
    "table:report.threat_catalog",
})


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
    source_kind: Literal["manual", "imported", "derived", "template_constant"]
    accepted_input_kinds: list[Literal["manual", "imported"]] = Field(default_factory=list, max_length=2)
    editable: bool
    missing_behavior: Literal[
        "allow_empty",
        "allow_draft_block_final",
        "derive_or_block_final",
        "render_empty_structure",
        "template_package_unavailable",
    ]
    conflict_behavior: Literal[
        "preserve_and_warn",
        "recompute_from_authority",
        "reject",
        "require_confirmation",
        "template_package_unavailable",
    ]
    governed_parameter_ids: list[str] = Field(min_length=1, max_length=100)
    readme_rule_refs: list[str] = Field(min_length=1, max_length=70)
    source_evidence: list[str] = Field(default_factory=list, max_length=20)
    format: dict[str, Any] = Field(default_factory=dict)
    sensitivity: Literal["public", "internal", "sensitive"]
    review_required: bool
    export_slots: list[str] = Field(default_factory=list, max_length=20)
    template_edition: Literal["2023"] = "2023"
    template_revision: Literal["2025-12-08"] = "2025-12-08"


class RuleAuthority(StrictModel):
    authority_id: str = Field(pattern=r"^[a-z][a-z0-9_.]{2,127}$")
    source_kind: Literal["manual", "imported", "derived", "template_constant"]
    accepted_input_kinds: list[Literal["manual", "imported"]] = Field(default_factory=list, max_length=2)
    editable: bool


class ReadmeRuleContract(StrictModel):
    rule_ref: str = Field(pattern=r"^3\.6\.[1-6]\.[0-9]{2}$")
    authorities: list[RuleAuthority] = Field(min_length=1, max_length=12)
    projection_ids: list[str] = Field(min_length=1, max_length=20)
    missing_behavior: Literal[
        "allow_empty",
        "allow_draft_block_final",
        "derive_or_block_final",
        "render_empty_structure",
        "template_package_unavailable",
    ]
    conflict_behavior: Literal[
        "preserve_and_warn",
        "recompute_from_authority",
        "reject",
        "require_confirmation",
        "template_package_unavailable",
    ]
    implementation_owner: Literal["R2", "R3", "R4", "R5"]


class FieldDictionary(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    package_id: Literal["report-2023-2025.12.08"]
    contract_status: Literal["frozen"]
    rule_contracts: list[ReadmeRuleContract] = Field(min_length=70, max_length=70)
    projection_catalog: list[str] = Field(min_length=92, max_length=92)
    fields: list[ReportField] = Field(min_length=26, max_length=26)


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
