from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendXlsxExportTests(unittest.TestCase):
    def test_project_page_exposes_score_workbook_export_and_dirty_guard(self) -> None:
        source = (ROOT / "frontend" / "src" / "pages" / "ProjectPage.tsx").read_text(encoding="utf-8")
        self.assertIn("exportProjectXlsx", source)
        self.assertIn("导出打分表", source)
        self.assertIn("scoreWorkbookExportBlockReason(dirtySections.size)", source)
        helper = (ROOT / "frontend" / "src" / "exporting.ts").read_text(encoding="utf-8")
        self.assertIn("当前还有未保存的章节，请先保存后再导出。", helper)

    def test_client_downloads_xlsx_from_project_export_endpoint(self) -> None:
        source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
        self.assertIn("/exports/xlsx", source)
        self.assertIn("application", (ROOT / "backend" / "app" / "api" / "exports.py").read_text(encoding="utf-8"))

    def test_management_unit_score_is_read_only(self) -> None:
        source = (ROOT / "frontend" / "src" / "components" / "AssessmentTable.tsx").read_text(encoding="utf-8")
        self.assertNotIn("updateUnitScoreForUnit", source)
        self.assertIn('<output className="unit-score-output">{group.unitScore}</output>', source)


if __name__ == "__main__":
    unittest.main()
