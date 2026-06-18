import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.validator import validate_project  # noqa: E402


class ValidationServiceTest(unittest.TestCase):
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

    def test_validation_reports_rows_references_images_and_persists_results(self) -> None:
        project = database.create_project("校验问题测试")
        unused_image = self._create_image(project["id"], "A-1", "unused.png", alt_text="", dpi=72)
        database.replace_section_rows(
            project_id=project["id"],
            code="A-1",
            rows=[
                {
                    "unit": "",
                    "object_name": "服务器",
                    "record_text": "见 [[FIG:9999]]。",
                    "metric_result": {
                        "d": "非法",
                        "a": "√",
                        "k": "",
                        "object_score": "abc",
                        "unit_score": "",
                    },
                    "cross_references": [
                        {
                            "target_image_id": None,
                            "token": "[[FIG:9999]]",
                            "display_text": "图A-1-1",
                        }
                    ],
                }
            ],
        )

        result = validate_project(project["id"])
        codes = {issue.code for issue in result.issues}

        self.assertGreater(result.summary.errors, 0)
        self.assertIn("REQUIRED_FIELD_MISSING", codes)
        self.assertIn("INVALID_DROPDOWN_VALUE", codes)
        self.assertIn("METRIC_REQUIRED", codes)
        self.assertIn("INVALID_SCORE", codes)
        self.assertIn("SCORE_REQUIRED", codes)
        self.assertIn("BROKEN_IMAGE_REFERENCE", codes)
        self.assertIn("BROKEN_STORED_REFERENCE", codes)
        self.assertIn("LOW_IMAGE_DPI", codes)
        self.assertNotIn("IMAGE_ALT_MISSING", codes)
        self.assertIn("IMAGE_UNUSED", codes)
        self.assertEqual(len(database.list_validation_issues(project["id"])), len(result.issues))
        self.assertEqual(unused_image["id"], int(database.list_evidence_images(project["id"], "A-1")[0]["id"]))

    def test_validation_accepts_clean_project_without_errors(self) -> None:
        project = database.create_project("校验通过测试")
        image = self._create_image(project["id"], "A-1", "referenced.png", alt_text="登录策略截图", dpi=150)
        database.replace_section_rows(
            project_id=project["id"],
            code="A-1",
            rows=[
                {
                    "unit": "身份鉴别",
                    "object_name": "服务器",
                    "record_text": f"查看登录策略，见 [[FIG:{image['id']}]]。",
                    "metric_result": {
                        "d": "√",
                        "a": "√",
                        "k": "/",
                        "object_score": "1.0000",
                        "unit_score": "1.0000",
                    },
                    "cross_references": [
                        {
                            "target_image_id": image["id"],
                            "token": f"[[FIG:{image['id']}]]",
                            "display_text": "图A-1-1",
                        }
                    ],
                }
            ],
        )

        result = validate_project(project["id"])

        self.assertEqual(result.summary.errors, 0)
        self.assertNotIn("DOCX_REF_TARGET_MISSING", {issue.code for issue in result.issues})

    def _create_image(self, project_id: int, section_code: str, filename: str, alt_text: str, dpi: int):
        relative_path = Path("uploads") / str(project_id) / section_code / filename
        absolute_path = settings.storage_path / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 200), color=(255, 255, 255)).save(absolute_path, dpi=(dpi, dpi))
        return database.create_evidence_image(
            project_id,
            section_code,
            {
                "file_path": relative_path.as_posix(),
                "original_name": filename,
                "caption": "登录策略截图",
                "alt_text": alt_text,
                "pixel_width": 400,
                "pixel_height": 200,
                "dpi_x": dpi,
                "dpi_y": dpi,
                "display_width_in": 4,
                "display_height_in": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
