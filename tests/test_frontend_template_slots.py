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

    def test_client_exposes_record_template_slot_update_reset_and_backup(self) -> None:
        client_source = (FRONTEND_SRC / "api" / "client.ts").read_text(encoding="utf-8")

        self.assertIn("export type RecordTemplateSlotUpdateInput", client_source)
        self.assertIn("export type RecordTemplateSlotImportPayload", client_source)
        self.assertIn("export function updateRecordTemplateSlot", client_source)
        self.assertIn("export function resetRecordTemplateSlot", client_source)
        self.assertIn("export function exportRecordTemplateSlots", client_source)
        self.assertIn("export function previewRecordTemplateSlotImport", client_source)
        self.assertIn("export function importRecordTemplateSlots", client_source)
        self.assertIn("method: \"PUT\"", client_source)
        self.assertIn("/reset", client_source)
        self.assertIn("/api/record-template-slots/export", client_source)
        self.assertIn("/api/record-template-slots/import-preview", client_source)
        self.assertIn("/api/record-template-slots/import", client_source)

    def test_project_page_loads_slots_for_active_section(self) -> None:
        page_source = (FRONTEND_SRC / "pages" / "ProjectPage.tsx").read_text(encoding="utf-8")

        self.assertIn("getRecordTemplateSlots", page_source)
        self.assertIn("refreshRecordTemplateSlots(activeCode)", page_source)
        self.assertIn("recordTemplateSlots={activeRecordTemplateSlots}", page_source)

    def test_project_page_wires_template_manager_to_slot_editor(self) -> None:
        page_source = (FRONTEND_SRC / "pages" / "ProjectPage.tsx").read_text(encoding="utf-8")

        self.assertIn("updateRecordTemplateSlot", page_source)
        self.assertIn("resetRecordTemplateSlot", page_source)
        self.assertIn("exportRecordTemplateSlots", page_source)
        self.assertIn("previewRecordTemplateSlotImport", page_source)
        self.assertIn("importRecordTemplateSlots", page_source)
        self.assertIn("recordTemplateSlots={recordTemplateSlots}", page_source)
        self.assertIn("onUpdateSlot={handleUpdateRecordTemplateSlot}", page_source)
        self.assertIn("onResetSlot={handleResetRecordTemplateSlot}", page_source)
        self.assertIn("onExportSlots={handleExportRecordTemplateSlots}", page_source)
        self.assertIn("onPreviewImportSlots={handlePreviewRecordTemplateSlotImport}", page_source)
        self.assertIn("onImportSlots={handleImportRecordTemplateSlots}", page_source)
        self.assertNotIn("handleSaveRowAsTemplate", page_source)
        self.assertNotIn("handleCreateRecordTemplate", page_source)
        self.assertNotIn("handleDeleteRecordTemplate", page_source)
        self.assertNotIn("handleCopyRecordTemplate", page_source)

    def test_assessment_table_uses_three_slot_templates_without_legacy_groups(self) -> None:
        table_source = (FRONTEND_SRC / "components" / "AssessmentTable.tsx").read_text(encoding="utf-8")

        self.assertIn("RecordTemplateSlot", table_source)
        self.assertIn("templateSlotsForUnit", table_source)
        self.assertIn("templateSlotOptionLabel", table_source)
        self.assertIn("calculateTechnicalUnitScore", table_source)
        self.assertIn("formatScoreToFourDecimals", table_source)
        self.assertIn("formatObjectScore", table_source)
        self.assertIn("function displayRecordText", table_source)
        self.assertIn("function storedRecordText", table_source)
        self.assertIn("function crossReferencesForRecordText", table_source)
        self.assertIn("value={displayRecordText(row, evidenceImages, sectionCode, profile)}", table_source)
        self.assertIn("const recordText = storedRecordText(event.target.value, row, evidenceImages, sectionCode, profile)", table_source)
        self.assertIn("record_text: recordText", table_source)
        self.assertIn("cross_references: crossReferencesForRecordText(", table_source)
        self.assertIn("const displayRecordTextValue = insertAtSelectionOrPlaceholder(", table_source)
        self.assertNotIn("const recordText = insertAtSelectionOrPlaceholder(row.record_text, token", table_source)
        self.assertIn("onBlur={() => formatObjectScore(index)}", table_source)
        self.assertIn("function addTechnicalSectionObject", table_source)
        self.assertIn("function removeTechnicalSectionObject", table_source)
        self.assertIn("technicalObjectNames", table_source)
        self.assertIn("save-action-row", table_source)
        self.assertIn("toolbar-object-add", table_source)
        self.assertIn("A4_OBJECT_CATEGORIES", table_source)
        self.assertIn("对象分类", table_source)
        self.assertIn("重要数据传输机密性", table_source)
        self.assertIn("重要数据传输完整性", table_source)
        self.assertIn("重要数据存储机密性", table_source)
        self.assertIn("重要数据存储完整性", table_source)
        self.assertIn("重要信息资源安全标记完整性", table_source)
        self.assertIn("targetUnitsForTechnicalObject", table_source)
        self.assertIn("isA4UnitScopedObjectUnit", table_source)
        self.assertIn("canAddObjectWithinUnit(sectionCode, group.unit, technical)", table_source)
        self.assertIn("onClick={() => removeRow(index)}", table_source)
        self.assertNotIn("deleteWholeObject ? removeTechnicalSectionObject(row.object_name) : removeRow(index)", table_source)
        self.assertIn("technical-object-toolbar", table_source)
        self.assertIn("technical-object-list", table_source)
        self.assertIn("object-delete-button", table_source)
        self.assertIn("SUBSYSTEM_SECTION_CODES", table_source)
        self.assertIn("supportsSubsystem(sectionCode)", table_source)
        self.assertIn("subsystem-filter", table_source)
        self.assertIn("technical-entry-row", table_source)
        self.assertIn("function addSubsystem", table_source)
        self.assertIn("function removeSubsystem", table_source)
        self.assertIn("function assignUnassignedObjectsToSubsystem", table_source)
        self.assertIn("function removeObjectFromActiveSubsystem", table_source)
        self.assertIn("unassignedTechnicalObjectNames", table_source)
        self.assertIn("unassigned-object-list", table_source)
        self.assertIn("归入当前子系统", table_source)
        self.assertIn("全部归入当前子系统", table_source)
        self.assertIn("移出当前子系统", table_source)
        self.assertIn("row.subsystem", table_source)
        self.assertIn("subsystem: activeSubsystemName", table_source)
        self.assertIn("object_name: objectName", table_source)
        self.assertIn("row.subsystem?.trim() !== activeSubsystemName", table_source)
        self.assertIn("row.subsystem?.trim() === activeSubsystemName", table_source)
        self.assertIn("removeTechnicalSectionObject(objectName)", table_source)
        self.assertIn("onClick={() => removeObjectFromActiveSubsystem(objectName)}", table_source)
        self.assertIn("测评对象 {objectCount}", table_source)
        self.assertIn("canAddObjectWithinUnit(sectionCode, group.unit, technical)", table_source)
        self.assertNotIn("deleteEvidence", table_source)
        self.assertIn("applyCalculatedUnitScores", table_source)
        self.assertIn("updateUnitScoreForUnit", table_source)
        self.assertIn("unit-score-cell", table_source)
        self.assertIn("rowSpan={group.entries.length}", table_source)
        self.assertIn("三类模板", table_source)
        self.assertNotIn("recordTemplates", table_source)
        self.assertNotIn("optgroup", table_source)
        self.assertNotIn("onSaveRowAsTemplate", table_source)

    def test_template_manager_is_fixed_three_slot_editor_with_config_backup(self) -> None:
        panel_source = (FRONTEND_SRC / "components" / "TemplateManagerPanel.tsx").read_text(encoding="utf-8")

        self.assertIn("recordTemplateSlots", panel_source)
        self.assertIn("onUpdateSlot", panel_source)
        self.assertIn("onResetSlot", panel_source)
        self.assertIn("onExportSlots", panel_source)
        self.assertIn("onPreviewImportSlots", panel_source)
        self.assertIn("onImportSlots", panel_source)
        self.assertIn("每个单元 3 类模板", panel_source)
        self.assertIn("恢复默认", panel_source)
        self.assertIn("三类模板配置导入导出", panel_source)
        self.assertIn("导出模板配置", panel_source)
        self.assertIn("选择配置 JSON", panel_source)
        self.assertIn("预览导入", panel_source)
        self.assertIn("确认导入", panel_source)
        self.assertNotIn("onCreate", panel_source)
        self.assertNotIn("onDelete", panel_source)
        self.assertNotIn("onCopy", panel_source)
        self.assertNotIn("onSaveRowAsTemplate", panel_source)
        self.assertNotIn("source_type", panel_source)
        self.assertNotIn("新建模板", panel_source)
        self.assertNotIn("删除", panel_source)
        self.assertNotIn("复制", panel_source)
        self.assertNotIn("我的模板", panel_source)


    def test_subsystem_state_is_preserved_above_assessment_table(self) -> None:
        page_source = (FRONTEND_SRC / "pages" / "ProjectPage.tsx").read_text(encoding="utf-8")
        table_source = (FRONTEND_SRC / "components" / "AssessmentTable.tsx").read_text(encoding="utf-8")

        self.assertIn("subsystemUiStateBySection", page_source)
        self.assertIn("setSubsystemUiStateBySection", page_source)
        self.assertIn("subsystemUiState={subsystemUiStateBySection[activeCode] ?? EMPTY_SUBSYSTEM_UI_STATE}", page_source)
        self.assertIn("handleSubsystemUiStateChange(activeCode, updater, options)", page_source)
        self.assertIn("options.dirty", page_source)
        self.assertIn("subsystemUiState", table_source)
        self.assertIn("onSubsystemUiStateChange", table_source)
        self.assertIn("setManualSubsystemNamesForSection", table_source)
        self.assertIn("setActiveSubsystemForSection", table_source)
        self.assertNotIn("setManualSubsystemNames([]);", table_source)
        self.assertNotIn("setActiveSubsystem(\"\");", table_source)

    def test_subsystem_and_object_controls_share_one_grid_row(self) -> None:
        styles_source = (ROOT / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".technical-entry-row.with-subsystem", styles_source)
        self.assertIn("grid-template-columns: minmax(140px, 1.1fr) auto minmax(140px, 1fr) minmax(110px, 0.8fr) minmax(140px, 1fr) auto", styles_source)
        self.assertIn(".technical-entry-row.with-subsystem .subsystem-controls", styles_source)
        self.assertIn(".technical-entry-row.with-subsystem .technical-object-add", styles_source)
        self.assertIn("display: contents", styles_source)


if __name__ == "__main__":
    unittest.main()
