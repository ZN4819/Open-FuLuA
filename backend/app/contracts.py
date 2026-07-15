"""跨后端、前端和桌面端共享的 R1 稳定契约。"""

from __future__ import annotations

from typing import Literal


ProjectType = Literal["appendix_a", "full_report"]
WorkflowStatus = Literal["draft", "ready_for_review", "confirmed"]
ReportExportMode = Literal["draft", "final"]
ReportImportMode = Literal["migration", "roundtrip"]
ProjectCreationOperation = Literal["create", "migration_import", "roundtrip_import", "upgrade_copy"]

PROJECT_TYPES: tuple[ProjectType, ...] = ("appendix_a", "full_report")
WORKFLOW_STATUSES: tuple[WorkflowStatus, ...] = ("draft", "ready_for_review", "confirmed")
REPORT_EXPORT_MODES: tuple[ReportExportMode, ...] = ("draft", "final")
REPORT_IMPORT_MODES: tuple[ReportImportMode, ...] = ("migration", "roundtrip")
PROJECT_CREATION_OPERATIONS: tuple[ProjectCreationOperation, ...] = (
    "create",
    "migration_import",
    "roundtrip_import",
    "upgrade_copy",
)

FULL_REPORT_TEMPLATE_PACKAGE_ID = "report-2023-2025.12.08"
FULL_REPORT_TEMPLATE_EDITION = "2023"
FULL_REPORT_TEMPLATE_REVISION = "2025-12-08"
FULL_REPORT_TEMPLATE_ASSET_SET_HASH = (
    "9017b86afd44a9ba05c55e3eb880d60b4dd6e45fbf87dd1b020bb5bc130d1484"
)

# R0 manifest 中的 data_schema_compatibility 描述报告模板数据契约，
# 与应用 SQLite schema 独立。R1 只提升数据库 schema，不改动冻结母版资产。
REPORT_TEMPLATE_DATA_SCHEMA_VERSION = "3"
