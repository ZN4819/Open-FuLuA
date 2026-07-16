"""R6 迁移任务的闭集合同。"""

from __future__ import annotations


REPORT_IMPORT_TABLES = (
    "report_import_jobs",
    "report_import_issues",
    "report_import_resolutions",
    "report_field_sources",
)

REPORT_IMPORT_STATUSES = frozenset(
    {"uploaded", "parsing", "preview_ready", "confirming", "succeeded", "failed"}
)
REPORT_IMPORT_CONFIDENCES = frozenset({"exact", "high", "ambiguous", "unmapped"})
REPORT_IMPORT_SEVERITIES = frozenset({"info", "warning", "error"})
REPORT_IMPORT_ISSUE_STATUSES = frozenset({"open", "resolved", "ignored"})
REPORT_IMPORT_RESOLUTION_ACTIONS = frozenset(
    {"adopt_candidate", "keep_original", "skip"}
)
REPORT_IMPORT_APPENDIX_SOURCES = frozenset({"document", "existing_project"})
