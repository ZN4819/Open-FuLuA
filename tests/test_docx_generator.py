import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.docx_analyzer import analyze_docx  # noqa: E402
from app.services.docx_generator import generate_project_docx  # noqa: E402


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


class DocxGeneratorTest(unittest.TestCase):
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

    def test_editable_docx_contains_sections_tables_fields_controls_and_image(self) -> None:
        project = self._make_project_with_content()

        path = generate_project_docx(project["id"], "editable")
        analysis = analyze_docx(path)

        self.assertEqual(analysis.sections, 8)
        self.assertEqual(analysis.tables, 8)
        self.assertGreaterEqual(analysis.dropdown_controls, 4)
        self.assertGreaterEqual(analysis.seq_fields, 9)
        self.assertEqual(analysis.ref_fields, 1)
        self.assertEqual(analysis.images, 1)
        self.assertEqual(analysis.missing_ref_targets, [])
        self.assertIn("2x8", analysis.table_shapes)
        self.assertTrue(_all_tables_have_grid(path))
        self.assertIn("A1.row1.D", _dropdown_tags(path))
        self.assertIn("A5.row1.compliance", _dropdown_tags(path))

    def test_final_docx_flattens_content_controls_but_keeps_references(self) -> None:
        project = self._make_project_with_content()

        path = generate_project_docx(project["id"], "final")
        analysis = analyze_docx(path)

        self.assertEqual(analysis.sections, 8)
        self.assertEqual(analysis.tables, 8)
        self.assertEqual(analysis.dropdown_controls, 0)
        self.assertEqual(analysis.ref_fields, 1)
        self.assertEqual(analysis.missing_ref_targets, [])

    def _make_project_with_content(self):
        project = database.create_project("DOCX 生成测试")
        image = self._create_evidence_image(project["id"], "A-1")
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
        database.replace_section_rows(
            project_id=project["id"],
            code="A-5",
            rows=[
                {
                    "unit": "管理制度",
                    "object_name": "制度文件",
                    "record_text": "制度文件已建立并发布。",
                    "metric_result": {
                        "compliance": "符合",
                        "unit_score": "1.0000",
                    },
                }
            ],
        )
        return project

    def _create_evidence_image(self, project_id: int, section_code: str):
        relative_path = Path("uploads") / str(project_id) / section_code / "sample.png"
        absolute_path = settings.storage_path / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (600, 300), color=(255, 255, 255))
        image.save(absolute_path, dpi=(150, 150))
        return database.create_evidence_image(
            project_id,
            section_code,
            {
                "file_path": relative_path.as_posix(),
                "original_name": "sample.png",
                "caption": "登录策略截图",
                "alt_text": "登录策略截图",
                "pixel_width": 600,
                "pixel_height": 300,
                "dpi_x": 150,
                "dpi_y": 150,
                "display_width_in": 4,
                "display_height_in": 2,
            },
        )


def _all_tables_have_grid(path: Path) -> bool:
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    for table in document.findall(".//w:tbl", NS):
        grid = table.find("w:tblGrid", NS)
        if grid is None or not grid.findall("w:gridCol", NS):
            return False
    return True


def _dropdown_tags(path: Path) -> set[str]:
    tags: set[str] = set()
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    for tag in document.findall(".//w:sdtPr/w:tag", NS):
        value = tag.get(f"{{{NS['w']}}}val")
        if value:
            tags.add(value)
    return tags


if __name__ == "__main__":
    unittest.main()
