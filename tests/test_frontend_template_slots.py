from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"


class FrontendTemplateSlotSourceTest(unittest.TestCase):
    def test_client_exposes_record_template_slot_api(self) -> None:
        client_source = (FRONTEND_SRC / "api" / "client.ts").read_text(encoding="utf-8")

        self.assertIn("export type RecordTemplateSlot", client_source)
        self.assertIn("export function getRecordTemplateSlots", client_source)
        self.assertIn("/api/record-template-slots", client_source)

    def test_project_page_loads_slots_for_active_section(self) -> None:
        page_source = (FRONTEND_SRC / "pages" / "ProjectPage.tsx").read_text(encoding="utf-8")

        self.assertIn("getRecordTemplateSlots", page_source)
        self.assertIn("refreshRecordTemplateSlots(activeCode)", page_source)
        self.assertIn("recordTemplateSlots={activeRecordTemplateSlots}", page_source)

    def test_assessment_table_uses_three_slot_templates_without_legacy_groups(self) -> None:
        table_source = (FRONTEND_SRC / "components" / "AssessmentTable.tsx").read_text(encoding="utf-8")

        self.assertIn("RecordTemplateSlot", table_source)
        self.assertIn("templateSlotsForUnit", table_source)
        self.assertIn("templateSlotOptionLabel", table_source)
        self.assertIn("三类模板", table_source)
        self.assertNotIn("recordTemplates", table_source)
        self.assertNotIn("optgroup", table_source)
        self.assertNotIn("onSaveRowAsTemplate", table_source)


if __name__ == "__main__":
    unittest.main()