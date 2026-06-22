import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.api.projects import delete_project as delete_project_schema  # noqa: E402
from app.config import settings  # noqa: E402
from app.schemas import DocxImportCreateProjectRequest, DocxImportIssue, DocxImportJobRead, DocxImportSectionPreview  # noqa: E402
from app.services.docx_importer import ensure_import_job_dir, import_job_dir, parsed_json_path, source_docx_path  # noqa: E402


class DocxImportJobsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        os.environ["FULUA_DATABASE_PATH"] = str(Path(self.temp_dir.name) / "test.db")
        self.original_storage_path = settings.storage_path
        object.__setattr__(settings, "storage_path", Path(self.temp_dir.name) / "storage")
        database.init_db()

    def tearDown(self) -> None:
        object.__setattr__(settings, "storage_path", self.original_storage_path)
        os.environ.pop("FULUA_DATABASE_PATH", None)

    def test_docx_import_job_table_is_initialized(self) -> None:
        with database.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(docx_import_jobs)").fetchall()}

        self.assertIn("status", columns)
        self.assertIn("source_docx_path", columns)
        self.assertIn("parsed_json_path", columns)
        self.assertIn("created_project_id", columns)
        self.assertIn("summary_json", columns)
        self.assertIn("issues_json", columns)

    def test_docx_import_job_can_be_created_updated_and_deleted(self) -> None:
        created = database.create_docx_import_job(
            original_name="附录A导入.docx",
            source_docx_path="imports/1/source.docx",
            summary={"sections": 0},
            issues=[],
        )

        self.assertEqual(created["status"], "uploaded")
        self.assertEqual(created["original_name"], "附录A导入.docx")
        self.assertEqual(json.loads(created["summary_json"]), {"sections": 0})
        self.assertEqual(json.loads(created["issues_json"]), [])

        project = database.create_project("导入后项目")
        updated = database.update_docx_import_job(
            created["id"],
            {
                "status": "preview_ready",
                "parsed_json_path": "imports/1/parsed.json",
                "created_project_id": project["id"],
                "summary": {"sections": 8, "rows": 2},
                "issues": [
                    {
                        "severity": "warning",
                        "code": "IMPORT_IMAGE_CAPTION_MISSING",
                        "message": "图片缺少题注。",
                    }
                ],
            },
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "preview_ready")
        self.assertEqual(updated["created_project_id"], project["id"])
        self.assertEqual(json.loads(updated["summary_json"]), {"sections": 8, "rows": 2})
        self.assertEqual(json.loads(updated["issues_json"])[0]["code"], "IMPORT_IMAGE_CAPTION_MISSING")

        deleted = database.delete_docx_import_job(created["id"])

        self.assertEqual(deleted["id"], created["id"])
        self.assertIsNone(database.get_docx_import_job(created["id"]))

    def test_docx_import_storage_is_separate_from_project_runtime_cleanup(self) -> None:
        project = database.create_project("导入目录隔离测试")
        import_dir = ensure_import_job_dir(7)
        source_path = source_docx_path(7)
        parsed_path = parsed_json_path(7)
        source_path.write_bytes(b"docx placeholder")
        parsed_path.write_text("{}", encoding="utf-8")

        for relative in ("uploads", "exports", "previews", "projects"):
            path = settings.storage_path / relative / str(project["id"])
            path.mkdir(parents=True, exist_ok=True)
            (path / "sample.txt").write_text("runtime", encoding="utf-8")

        delete_project_schema(project["id"])

        self.assertTrue(import_dir.exists())
        self.assertTrue(source_path.exists())
        self.assertTrue(parsed_path.exists())
        self.assertEqual(import_job_dir(7), import_dir)

    def test_docx_import_job_project_reference_is_cleared_when_project_is_deleted(self) -> None:
        project = database.create_project("导入结果项目")
        job = database.create_docx_import_job(
            original_name="source.docx",
            source_docx_path="imports/2/source.docx",
        )
        database.update_docx_import_job(job["id"], {"created_project_id": project["id"]})

        delete_project_schema(project["id"])
        reloaded = database.get_docx_import_job(job["id"])

        self.assertIsNotNone(reloaded)
        self.assertIsNone(reloaded["created_project_id"])

    def test_docx_import_schemas_accept_preview_contract(self) -> None:
        issue = DocxImportIssue(
            severity="warning",
            code="IMPORT_MISSING_SECTION",
            message="未识别 A-8。",
            section_code="A-8",
        )
        section = DocxImportSectionPreview(
            code="A-1",
            title="物理和环境安全",
            table_title="表A-1物理和环境安全测评结果记录",
            table_type="technical",
            row_count=3,
            image_count=1,
            reference_count=1,
        )
        job = DocxImportJobRead(
            id=1,
            status="preview_ready",
            original_name="source.docx",
            suggested_project_name="source",
            sections=[section],
            summary={"sections": 1},
            issues=[issue],
            created_at="2026-06-22T00:00:00+00:00",
        )
        request = DocxImportCreateProjectRequest(project_name="导入项目")

        self.assertEqual(job.sections[0].code, "A-1")
        self.assertEqual(job.issues[0].code, "IMPORT_MISSING_SECTION")
        self.assertEqual(request.project_name, "导入项目")


if __name__ == "__main__":
    unittest.main()
