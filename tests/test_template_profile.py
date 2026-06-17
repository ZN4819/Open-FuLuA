import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.template_profile import load_template_profile  # noqa: E402


class TemplateProfileTest(unittest.TestCase):
    def test_profile_contains_eight_sections(self) -> None:
        profile = load_template_profile()

        self.assertEqual(len(profile["sections"]), 8)
        self.assertEqual([section["code"] for section in profile["sections"]], [f"A-{index}" for index in range(1, 9)])

    def test_table_schemas_match_appendix_a_forms(self) -> None:
        profile = load_template_profile()

        technical_columns = profile["tables"]["technical"]["columns"]
        management_columns = profile["tables"]["management"]["columns"]

        self.assertEqual(len(technical_columns), 8)
        self.assertEqual(len(management_columns), 5)
        self.assertEqual(technical_columns[0]["key"], "unit")
        self.assertEqual(technical_columns[2]["key"], "record_text")
        self.assertEqual(management_columns[3]["key"], "compliance")

    def test_dropdown_options_match_sample_document(self) -> None:
        profile = load_template_profile()

        self.assertEqual(profile["content_controls"]["technical_metric"]["options"], ["√", "×", "/"])
        self.assertEqual(
            profile["content_controls"]["management_compliance"]["options"],
            ["符合", "部分符合", "不符合", "不适用"],
        )

    def test_page_geometry_is_landscape_a4(self) -> None:
        profile = load_template_profile()
        page = profile["page"]

        self.assertEqual(page["size"], "A4")
        self.assertEqual(page["orientation"], "landscape")
        self.assertAlmostEqual(page["usable_width_in"], 9.69, places=2)


if __name__ == "__main__":
    unittest.main()
