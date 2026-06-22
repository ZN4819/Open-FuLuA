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

    def test_client_exposes_record_template_slot_update_and_reset(self) -> None:
        client_source = (FRONTEND_SRC / "api" / "client.ts").read_text(encoding="utf-8")

        self.assertIn("export type RecordTemplateSlotUpdateInput", client_source)
        self.assertIn("export function updateRecordTemplateSlot", client_source)
        self.assertIn("export function resetRecordTemplateSlot", client_source)
        self.assertIn("method: \"PUT\"", client_source)
        self.assertIn("/reset", client_source)

    def test_project_page_loads_slots_for_active_section(self) -> None:
        page_source = (FRONTEND_SRC / "pages" / "ProjectPage.tsx").read_text(encoding="utf-8")

        self.assertIn("getRecordTemplateSlots", page_source)
        self.assertIn("refreshRecordTemplateSlots(activeCode)", page_source)
        self.assertIn("recordTemplateSlots={activeRecordTemplateSlots}", page_source)

    def test_project_page_wires_template_manager_to_slot_editor(self) -> None:
        page_source = (FRONTEND_SRC / "pages" / "ProjectPage.tsx").read_text(encoding="utf-8")

        self.assertIn("updateRecordTemplateSlot", page_source)
        self.assertIn("resetRecordTemplateSlot", page_source)
        self.assertIn("recordTemplateSlots={recordTemplateSlots}", page_source)
        self.assertIn("onUpdateSlot={handleUpdateRecordTemplateSlot}", page_source)
        self.assertIn("onResetSlot={handleResetRecordTemplateSlot}", page_source)
        self.assertNotIn("handleSaveRowAsTemplate", page_source)
        self.assertNotIn("handleCreateRecordTemplate", page_source)
        self.assertNotIn("handleDeleteRecordTemplate", page_source)
        self.assertNotIn("handleCopyRecordTemplate", page_source)

    def test_assessment_table_uses_three_slot_templates_without_legacy_groups(self) -> None:
        table_source = (FRONTEND_SRC / "components" / "AssessmentTable.tsx").read_text(encoding="utf-8")

        self.assertIn("RecordTemplateSlot", table_source)
        self.assertIn("templateSlotsForUnit", table_source)
        self.assertIn("templateSlotOptionLabel", table_source)
        self.assertIn("三类模板", table_source)
        self.assertNotIn("recordTemplates", table_source)
        self.assertNotIn("optgroup", table_source)
        self.assertNotIn("onSaveRowAsTemplate", table_source)

    def test_template_manager_is_fixed_three_slot_editor(self) -> None:
        panel_source = (FRONTEND_SRC / "components" / "TemplateManagerPanel.tsx").read_text(encoding="utf-8")

        self.assertIn("recordTemplateSlots", panel_source)
        self.assertIn("onUpdateSlot", panel_source)
        self.assertIn("onResetSlot", panel_source)
        self.assertIn("每个单元 3 类模板", panel_source)
        self.assertIn("恢复默认", panel_source)
        self.assertNotIn("onCreate", panel_source)
        self.assertNotIn("onDelete", panel_source)
        self.assertNotIn("onCopy", panel_source)
        self.assertNotIn("onSaveRowAsTemplate", panel_source)
        self.assertNotIn("source_type", panel_source)
        self.assertNotIn("新建模板", panel_source)
        self.assertNotIn("删除", panel_source)
        self.assertNotIn("复制", panel_source)


if __name__ == "__main__":
    unittest.main()
