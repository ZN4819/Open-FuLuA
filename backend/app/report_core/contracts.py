"""R2 报告核心 schema 与初始化器共享契约。"""

from __future__ import annotations

from typing import Any


REPORT_CORE_SCHEMA_VERSION = 1
R2_TEMPLATE_MANIFEST_SHA256 = (
    "59df84594e01ab82b0588868b53952779b02f66800dd2ea5578a1ce213a31f0e"
)
REPORT_CORE_ENTITY_TABLES: tuple[str, ...] = (
    "report_metadata",
    "report_organizations",
    "report_members",
    "report_phase_dates",
    "report_distribution",
    "system_profiles",
    "system_crypto_products",
    "report_standards",
    "special_indicators",
    "assessment_objects",
    "assessment_object_subsystems",
    "object_relations",
    "result_correction_relations",
    "report_sections",
    "report_blocks",
)
REPORT_CORE_AUXILIARY_TABLES: tuple[str, ...] = ("report_warning_confirmations",)

REPORT_SECTION_TYPES = (
    "form",
    "blocks",
    "generated",
    "appendix_a",
    "appendix_b",
)
REPORT_BLOCK_TYPES = (
    "paragraph",
    "bullet_list",
    "numbered_list",
    "key_value_table",
    "data_table",
    "figure",
    "reference",
    "generated",
)
REPORT_EDIT_POLICIES = ("editable", "overrideable", "readonly")
REPORT_SOURCE_KINDS = ("manual", "imported", "derived", "template_constant")
REPORT_GENERATION_STATUSES = ("current", "stale", "not_generated")


class ReportDomainInitializationError(RuntimeError):
    """完整报告数据域无法安全初始化。"""

    def __init__(
        self,
        code: str,
        *,
        project_uuid: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.project_uuid = project_uuid
        self.details = details or {}
