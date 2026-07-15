"""报告风险摘要中的确定性派生规则。"""

from __future__ import annotations


HIGH_RISK_PRESENT_TEXT = "判定系统存在高风险"
HIGH_RISK_ABSENT_TEXT = "判定系统不存在高风险"


class RiskAnalysisIncomplete(ValueError):
    """风险分析尚未形成可用于正式报告的已确认快照。"""


def derive_high_risk_judgement(*, high_risk_count: int, analysis_complete: bool = True) -> str:
    """根据已确认风险快照中的高风险项数量生成报告用判定文字。"""

    if not analysis_complete:
        raise RiskAnalysisIncomplete("RISK_ANALYSIS_INCOMPLETE")
    if isinstance(high_risk_count, bool) or not isinstance(high_risk_count, int) or high_risk_count < 0:
        raise ValueError("HIGH_RISK_COUNT_INVALID")
    return HIGH_RISK_PRESENT_TEXT if high_risk_count > 0 else HIGH_RISK_ABSENT_TEXT
