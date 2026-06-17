import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DOCX = ROOT / "附录A编写.docx"
sys.path.insert(0, str(ROOT / "backend"))

from app.services.docx_analyzer import analyze_docx  # noqa: E402
from app.services.template_profile import load_template_profile  # noqa: E402


@unittest.skipUnless(SAMPLE_DOCX.exists(), "本地未提供附录A编写.docx，跳过样本文档回归测试。")
class SampleDocxAnalysisTest(unittest.TestCase):
    def test_sample_document_matches_phase_two_baseline(self) -> None:
        profile = load_template_profile()
        baseline = profile["sample_baseline"]
        analysis = analyze_docx(SAMPLE_DOCX)

        self.assertEqual(analysis.sections, baseline["sections"])
        self.assertEqual(analysis.tables, baseline["tables"])
        self.assertEqual(analysis.content_controls, baseline["content_controls"])
        self.assertEqual(analysis.dropdown_controls, baseline["content_controls"])
        self.assertEqual(analysis.ref_fields, baseline["ref_fields"])
        self.assertEqual(analysis.seq_fields, baseline["seq_fields"])
        self.assertEqual(analysis.images, baseline["images"])
        self.assertEqual(analysis.missing_ref_targets, [])

    def test_sample_document_table_shapes_are_known(self) -> None:
        analysis = analyze_docx(SAMPLE_DOCX)

        self.assertEqual(
            analysis.table_shapes,
            ["11x8", "32x8", "50x8", "13x8", "7x5", "6x5", "11x5", "4x5"],
        )


if __name__ == "__main__":
    unittest.main()
