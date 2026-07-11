import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.preview import create_preview_job, get_preview_job, process_preview_job  # noqa: E402


class PreviewJobsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.original_storage_path = settings.storage_path
        object.__setattr__(settings, "storage_path", self.root / "storage")
        os.environ["FULUA_DATABASE_PATH"] = str(self.root / "test.db")
        database.init_db()

    def tearDown(self) -> None:
        os.environ.pop("FULUA_DATABASE_PATH", None)
        object.__setattr__(settings, "storage_path", self.original_storage_path)

    def test_background_render_holds_runtime_write_reservation_until_task_finishes(self) -> None:
        from app.api import render_jobs
        from app.api.runtime import runtime_operations

        class Tasks:
            task = None
            args = ()

            def add_task(self, task, *args):
                self.task = task
                self.args = args

        tasks = Tasks()
        job = type("Job", (), {"id": 42})()
        with patch("app.api.render_jobs.create_preview_job", return_value=job), patch("app.api.render_jobs.process_preview_job") as process:
            render_jobs.create_render_job(1, tasks)
            self.assertGreaterEqual(runtime_operations.business_writes_active(), 1)
            tasks.task(*tasks.args)
            process.assert_called_once_with(42)
        self.assertEqual(runtime_operations.business_writes_active(), 0)

    def test_preview_job_succeeds_when_pdf_renderer_returns_file(self) -> None:
        project = database.create_project("预览成功测试")
        job = create_preview_job(project["id"], "final")

        def fake_render_pdf(docx_path: Path, preview_dir: Path, log_lines: list[str], timeout_seconds: int) -> Path:
            pdf_path = preview_dir / "preview.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(b"%PDF\n1 0 obj << /Type /Page >> endobj\n2 0 obj << /Type /Page >> endobj\n")
            log_lines.append(f"fake renderer used for {docx_path.name}")
            return pdf_path

        with patch("app.services.preview._render_pdf", side_effect=fake_render_pdf):
            process_preview_job(job.id)

        updated = get_preview_job(job.id)

        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, "succeeded")
        self.assertEqual(updated.page_count, 2)
        self.assertTrue(updated.output_docx_url)
        self.assertTrue(updated.output_pdf_url)
        self.assertTrue(updated.log_url)

    def test_preview_job_records_failure_when_renderer_is_missing(self) -> None:
        project = database.create_project("预览失败测试")
        job = create_preview_job(project["id"], "final")

        with patch("app.services.preview._find_soffice", return_value=None), patch(
            "app.services.preview._render_with_word_if_available", return_value=None
        ):
            process_preview_job(job.id)

        updated = get_preview_job(job.id)

        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, "failed")
        self.assertIn("未找到可用", updated.error_message or "")
        self.assertTrue(updated.log_url)


if __name__ == "__main__":
    unittest.main()
