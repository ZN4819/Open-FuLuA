import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.docx_analyzer import analyze_docx  # noqa: E402
from app.services.docx_generator import generate_project_docx  # noqa: E402
from app.services.preview import create_preview_job, get_preview_job, process_preview_job  # noqa: E402
from app.services.validator import validate_project  # noqa: E402


class EndToEndWorkflowTest(unittest.TestCase):
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

    def test_author_validate_export_and_preview_workflow(self) -> None:
        project = database.create_project("端到端回归测试")
        first_image = self._create_image(project["id"], "A-1", "a1.png", "身份鉴别截图")
        second_image = self._create_image(project["id"], "A-5", "a5.png", "制度文件截图")

        database.replace_section_rows(
            project_id=project["id"],
            code="A-1",
            rows=[
                {
                    "unit": "身份鉴别",
                    "object_name": "服务器",
                    "record_text": f"检查登录策略，见 [[FIG:{first_image['id']}]]。",
                    "metric_result": {
                        "d": "√",
                        "a": "√",
                        "k": "/",
                        "object_score": "1.0000",
                        "unit_score": "1.0000",
                    },
                    "cross_references": [
                        {
                            "target_image_id": first_image["id"],
                            "token": f"[[FIG:{first_image['id']}]]",
                            "display_text": "图A-1-1",
                        }
                    ],
                }
            ],
        )
        database.replace_section_rows(
            project_id=project["id"],
            code="A-5",
            rows=[
                {
                    "unit": "管理制度",
                    "object_name": "制度文件",
                    "record_text": f"查阅制度文件，见 [[FIG:{second_image['id']}]]。",
                    "metric_result": {
                        "compliance": "符合",
                        "unit_score": "1.0000",
                    },
                    "cross_references": [
                        {
                            "target_image_id": second_image["id"],
                            "token": f"[[FIG:{second_image['id']}]]",
                            "display_text": "图A-5-1",
                        }
                    ],
                }
            ],
        )

        validation = validate_project(project["id"])
        editable_docx = generate_project_docx(project["id"], "editable")
        final_docx = generate_project_docx(project["id"], "final")
        editable = analyze_docx(editable_docx)
        final = analyze_docx(final_docx)

        self.assertEqual(validation.summary.errors, 0)
        self.assertEqual(editable.sections, 8)
        self.assertEqual(editable.tables, 8)
        self.assertEqual(editable.images, 2)
        self.assertEqual(editable.ref_fields, 2)
        self.assertGreater(editable.dropdown_controls, 0)
        self.assertEqual(final.dropdown_controls, 0)
        self.assertEqual(final.missing_ref_targets, [])

        job = create_preview_job(project["id"], "final")
        with patch("app.services.preview._render_pdf", side_effect=self._fake_render_pdf):
            process_preview_job(job.id)
        completed = get_preview_job(job.id)

        self.assertIsNotNone(completed)
        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(completed.page_count, 2)
        self.assertTrue(completed.output_pdf_url)
        self.assertTrue(completed.log_url)

    def _create_image(self, project_id: int, section_code: str, filename: str, caption: str):
        relative_path = Path("uploads") / str(project_id) / section_code / filename
        absolute_path = settings.storage_path / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (600, 300), color=(255, 255, 255)).save(absolute_path, dpi=(150, 150))
        return database.create_evidence_image(
            project_id,
            section_code,
            {
                "file_path": relative_path.as_posix(),
                "original_name": filename,
                "caption": caption,
                "alt_text": caption,
                "pixel_width": 600,
                "pixel_height": 300,
                "dpi_x": 150,
                "dpi_y": 150,
                "display_width_in": 4,
                "display_height_in": 2,
            },
        )

    @staticmethod
    def _fake_render_pdf(docx_path: Path, preview_dir: Path, log_lines: list[str], timeout_seconds: int) -> Path:
        pdf_path = preview_dir / "workflow.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(
            b"%PDF\n"
            b"1 0 obj << /Type /Page >> endobj\n"
            b"2 0 obj << /Type /Page >> endobj\n"
        )
        log_lines.append(f"fake renderer used for {docx_path.name}")
        return pdf_path


if __name__ == "__main__":
    unittest.main()
