from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendReportExportContractTests(unittest.TestCase):
    def test_r4_export_workspace_uses_revision_snapshot_job_and_docx_only(self) -> None:
        workspace = (ROOT / "frontend" / "src" / "components" / "ReportExportWorkspace.tsx").read_text(encoding="utf-8")
        client = (ROOT / "frontend" / "src" / "api" / "reportClient.ts").read_text(encoding="utf-8")
        derived = (ROOT / "frontend" / "src" / "components" / "ReportDerivedWorkspace.tsx").read_text(encoding="utf-8")

        self.assertIn("expected_project_revision", workspace)
        self.assertIn("validateReportExport", workspace)
        self.assertIn("getReportExportJob", workspace)
        self.assertIn("downloadReportExportDocx", workspace)
        self.assertIn("hasUnsavedChanges", workspace)
        self.assertIn("ReportExportWorkspace", derived)
        self.assertIn("/report-validations", client)
        self.assertIn("/report-export-jobs", client)
        self.assertIn("/docx", client)
        self.assertNotIn("/pdf", client.lower())
        self.assertNotIn("libreoffice", workspace.lower())


if __name__ == "__main__":
    unittest.main()
