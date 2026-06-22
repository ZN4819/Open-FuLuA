from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"


class FrontendDocxImportSourceTest(unittest.TestCase):
    def test_client_exposes_docx_import_api(self) -> None:
        client_source = (FRONTEND_SRC / "api" / "client.ts").read_text(encoding="utf-8")

        self.assertIn("export type DocxImportJob", client_source)
        self.assertIn("export type DocxImportSectionPreview", client_source)
        self.assertIn("export type DocxImportIssue", client_source)
        self.assertIn("export async function uploadDocxImport", client_source)
        self.assertIn("export function getDocxImport", client_source)
        self.assertIn("export function createProjectFromDocxImport", client_source)
        self.assertIn("new FormData()", client_source)
        self.assertIn("/api/imports/docx", client_source)
        self.assertIn("/api/imports/${jobId}", client_source)
        self.assertIn("/api/imports/${jobId}/project", client_source)

    def test_project_home_wires_docx_import_wizard(self) -> None:
        page_source = (FRONTEND_SRC / "pages" / "ProjectPage.tsx").read_text(encoding="utf-8")

        self.assertIn("uploadDocxImport", page_source)
        self.assertIn("createProjectFromDocxImport", page_source)
        self.assertIn("handleUploadDocxImport", page_source)
        self.assertIn("handleConfirmDocxImport", page_source)
        self.assertIn("导入 DOCX 创建项目", page_source)
        self.assertIn("ImportPreviewPanel", page_source)
        self.assertIn("created_project_id", page_source)
        self.assertIn("openProject(loaded)", page_source)
        self.assertIn("setProjects((current) => [loaded", page_source)

    def test_import_wizard_styles_cover_summary_and_issues(self) -> None:
        style_source = (FRONTEND_SRC / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".home-import-panel", style_source)
        self.assertIn(".import-form", style_source)
        self.assertIn(".import-summary-grid", style_source)
        self.assertIn(".import-section-list", style_source)
        self.assertIn(".import-issue-list", style_source)
        self.assertIn(".import-confirm-button", style_source)


if __name__ == "__main__":
    unittest.main()