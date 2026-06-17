import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.record_templates import list_record_templates, load_record_template_library  # noqa: E402


class RecordTemplatesTest(unittest.TestCase):
    def test_record_template_library_covers_all_sections(self) -> None:
        templates = list_record_templates()
        section_codes = {template["section_code"] for template in templates}

        self.assertGreaterEqual(len(templates), 100)
        self.assertEqual(section_codes, {f"A-{index}" for index in range(1, 9)})

    def test_record_templates_can_be_filtered_by_section(self) -> None:
        a5_templates = list_record_templates("A-5")

        self.assertTrue(a5_templates)
        self.assertTrue(all(template["section_code"] == "A-5" for template in a5_templates))
        self.assertTrue(any("密码应用安全管理制度" in template["unit"] for template in a5_templates))

    def test_record_templates_do_not_keep_sample_figure_numbers(self) -> None:
        templates = list_record_templates()
        figure_number = re.compile(r"图\s*A\s*-\s*\d+\s*-\s*\d+")

        self.assertFalse(any(figure_number.search(template["record_text"]) for template in templates))
        self.assertTrue(any("[插入图片引用]" in template["record_text"] for template in templates))

    def test_record_template_library_metadata_is_present(self) -> None:
        library = load_record_template_library()

        self.assertEqual(library["profile_id"], "appendix_a_record_templates_v1")
        self.assertEqual(library["source_document"], "附录A编写.docx")


if __name__ == "__main__":
    unittest.main()
