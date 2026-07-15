"""完整报告结构化数据域基础能力。"""

from .contracts import (
    REPORT_CORE_AUXILIARY_TABLES,
    REPORT_CORE_ENTITY_TABLES,
    ReportDomainInitializationError,
)
from .initializer import initialize_report_domain

__all__ = [
    "REPORT_CORE_ENTITY_TABLES",
    "REPORT_CORE_AUXILIARY_TABLES",
    "ReportDomainInitializationError",
    "initialize_report_domain",
]
