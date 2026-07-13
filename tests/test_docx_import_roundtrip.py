import os
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile
from PIL import Image
from starlette.datastructures import Headers


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.api.imports import create_project_from_docx_import as api_confirm_docx_import  # noqa: E402
from app.api.imports import upload_docx_import as api_upload_docx_import  # noqa: E402
from app.api.sections import build_section_detail  # noqa: E402
from app.config import settings  # noqa: E402
from app.schemas import DocxImportCreateProjectRequest  # noqa: E402
from app.services.docx_analyzer import analyze_docx  # noqa: E402
from app.services.docx_generator import generate_project_docx  # noqa: E402
from app.services.validator import validate_project  # noqa: E402


class DocxImportRoundtripTest(unittest.TestCase):
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

    def test_editable_docx_roundtrip_import_preserves_rows_images_and_references(self) -> None:
        source_project = self._make_project_with_technical_and_management_content()
        exported = generate_project_docx(source_project["id"], "editable")

        preview = api_upload_docx_import(self._upload("editable-roundtrip.docx", exported.read_bytes()))
        confirmed = api_confirm_docx_import(
            preview.id,
            DocxImportCreateProjectRequest(project_name="editable 导入回归项目"),
        )

        self.assertEqual(preview.status, "preview_ready")
        self.assertTrue(preview.can_create_project)
        self.assertEqual(preview.summary["assessment_rows"], 2)
        self.assertEqual(preview.summary["images"], 2)
        self.assertEqual(preview.summary["references"], 2)
        self.assertEqual(confirmed.status, "succeeded")
        self.assertIsNotNone(confirmed.created_project_id)

        imported_project_id = int(confirmed.created_project_id)
        self._assert_imported_a1_detail(imported_project_id)
        self._assert_imported_a5_detail(imported_project_id)

        validation = validate_project(imported_project_id)
        self.assertEqual(validation.summary.errors, 0)
        regenerated = generate_project_docx(imported_project_id, "final")
        regenerated_analysis = analyze_docx(regenerated)
        self.assertEqual(regenerated_analysis.tables, 8)
        self.assertEqual(regenerated_analysis.images, 2)
        self.assertEqual(regenerated_analysis.ref_fields, 2)
        self.assertEqual(regenerated_analysis.missing_ref_targets, [])

    def test_final_docx_roundtrip_import_preserves_core_fields_without_content_controls(self) -> None:
        source_project = self._make_project_with_technical_and_management_content()
        exported = generate_project_docx(source_project["id"], "final")
        source_analysis = analyze_docx(exported)

        preview = api_upload_docx_import(self._upload("final-roundtrip.docx", exported.read_bytes()))
        confirmed = api_confirm_docx_import(
            preview.id,
            DocxImportCreateProjectRequest(project_name="final 导入回归项目"),
        )

        self.assertEqual(source_analysis.dropdown_controls, 0)
        self.assertEqual(preview.status, "preview_ready")
        self.assertTrue(preview.can_create_project)
        self.assertEqual(preview.summary["assessment_rows"], 2)
        self.assertEqual(preview.summary["images"], 2)
        self.assertEqual(preview.summary["references"], 2)
        self.assertEqual(confirmed.status, "succeeded")
        self.assertIsNotNone(confirmed.created_project_id)

        imported_project_id = int(confirmed.created_project_id)
        self._assert_imported_a1_detail(imported_project_id)
        self._assert_imported_a5_detail(imported_project_id)

        validation = validate_project(imported_project_id)
        self.assertEqual(validation.summary.errors, 0)

    def _make_project_with_technical_and_management_content(self):
        project = database.create_project("DOCX 导入 roundtrip 源项目")
        a1_image = self._create_image(project["id"], "A-1", "a1.png", "身份鉴别截图", (230, 245, 255))
        a5_image = self._create_image(project["id"], "A-5", "a5.png", "管理制度截图", (255, 242, 220))

        database.replace_section_rows(
            project_id=project["id"],
            code="A-1",
            rows=[
                {
                    "unit": "身份鉴别",
                    "object_name": "服务器",
                    "record_text": f"查看服务器登录策略，见 [[FIG:{a1_image['id']}]]。",
                    "metric_result": {
                        "d": "√",
                        "a": "√",
                        "k": "/",
                        "object_score": "1.0000",
                        "unit_score": "1.0000",
                    },
                    "cross_references": [
                        {
                            "target_image_id": a1_image["id"],
                            "token": f"[[FIG:{a1_image['id']}]]",
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
                    "object_name": "管理制度文件",
                    "record_text": f"查阅管理制度文件，见 [[FIG:{a5_image['id']}]]。",
                    "metric_result": {
                        "compliance": "符合",
                        "unit_score": "1.0000",
                    },
                    "cross_references": [
                        {
                            "target_image_id": a5_image["id"],
                            "token": f"[[FIG:{a5_image['id']}]]",
                            "display_text": "图A-5-1",
                        }
                    ],
                }
            ],
        )
        return project

    def _assert_imported_a1_detail(self, project_id: int) -> None:
        detail = build_section_detail(project_id, "A-1")
        self.assertEqual(len(detail.rows), 1)
        self.assertEqual(len(detail.evidence_images), 1)
        self.assertEqual(len(detail.cross_references), 1)
        row = detail.rows[0]
        image = detail.evidence_images[0]
        reference = detail.cross_references[0]

        self.assertEqual(row.unit, "身份鉴别")
        self.assertEqual(row.object_name, "服务器")
        self.assertIn("查看服务器登录策略", row.record_text)
        self.assertIn(f"[[FIG:{image.id}]]", row.record_text)
        self.assertNotIn("[[FIG:import:", row.record_text)
        self.assertEqual(row.metric_result.d, "√")
        self.assertEqual(row.metric_result.a, "√")
        self.assertEqual(row.metric_result.k, "/")
        self.assertEqual(row.metric_result.ra, "1")
        self.assertEqual(row.metric_result.rk, "1")
        self.assertEqual(row.metric_result.object_score, "0.5000")
        self.assertEqual(row.metric_result.unit_score, "0.5000")
        self.assertEqual(reference.target_image_id, image.id)
        self.assertEqual(reference.token, f"[[FIG:{image.id}]]")
        self.assertEqual(reference.display_text, "图A-1-1")
        self.assertTrue((settings.storage_path / image.file_path).exists())

    def _assert_imported_a5_detail(self, project_id: int) -> None:
        detail = build_section_detail(project_id, "A-5")
        self.assertEqual(len(detail.rows), 1)
        self.assertEqual(len(detail.evidence_images), 1)
        self.assertEqual(len(detail.cross_references), 1)
        row = detail.rows[0]
        image = detail.evidence_images[0]
        reference = detail.cross_references[0]

        self.assertEqual(row.unit, "管理制度")
        self.assertEqual(row.object_name, "管理制度文件")
        self.assertIn("查阅管理制度文件", row.record_text)
        self.assertIn(f"[[FIG:{image.id}]]", row.record_text)
        self.assertNotIn("[[FIG:import:", row.record_text)
        self.assertEqual(row.metric_result.compliance, "符合")
        self.assertEqual(row.metric_result.unit_score, "1.0000")
        self.assertEqual(reference.target_image_id, image.id)
        self.assertEqual(reference.token, f"[[FIG:{image.id}]]")
        self.assertEqual(reference.display_text, "图A-5-1")
        self.assertTrue((settings.storage_path / image.file_path).exists())

    def _create_image(self, project_id: int, section_code: str, filename: str, caption: str, color: tuple[int, int, int]):
        relative_path = Path("uploads") / str(project_id) / section_code / filename
        absolute_path = settings.storage_path / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (640, 360), color=color).save(absolute_path, dpi=(150, 150))
        return database.create_evidence_image(
            project_id,
            section_code,
            {
                "file_path": relative_path.as_posix(),
                "original_name": filename,
                "caption": caption,
                "alt_text": caption,
                "pixel_width": 640,
                "pixel_height": 360,
                "dpi_x": 150,
                "dpi_y": 150,
                "display_width_in": 4.2,
                "display_height_in": 2.36,
            },
        )

    @staticmethod
    def _upload(filename: str, content: bytes) -> UploadFile:
        return UploadFile(
            file=BytesIO(content),
            filename=filename,
            headers=Headers({"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
        )


if __name__ == "__main__":
    unittest.main()
