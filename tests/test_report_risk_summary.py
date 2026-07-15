from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.report_templates.risk_summary import (
    HIGH_RISK_ABSENT_TEXT,
    HIGH_RISK_PRESENT_TEXT,
    RiskAnalysisIncomplete,
    derive_high_risk_judgement,
)


class ReportRiskSummaryTests(unittest.TestCase):
    def test_positive_count_is_high_risk(self) -> None:
        self.assertEqual(derive_high_risk_judgement(high_risk_count=1), HIGH_RISK_PRESENT_TEXT)
        self.assertEqual(derive_high_risk_judgement(high_risk_count=7), HIGH_RISK_PRESENT_TEXT)

    def test_zero_count_is_not_high_risk(self) -> None:
        self.assertEqual(derive_high_risk_judgement(high_risk_count=0), HIGH_RISK_ABSENT_TEXT)

    def test_incomplete_analysis_blocks_derivation(self) -> None:
        with self.assertRaisesRegex(RiskAnalysisIncomplete, "RISK_ANALYSIS_INCOMPLETE"):
            derive_high_risk_judgement(high_risk_count=0, analysis_complete=False)

    def test_invalid_count_is_rejected(self) -> None:
        for invalid in (-1, True, 1.5, "1", None):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "HIGH_RISK_COUNT_INVALID"):
                    derive_high_risk_judgement(high_risk_count=invalid)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
