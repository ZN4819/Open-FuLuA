import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.api.evidence import evidence_to_schema  # noqa: E402
from app.services.evidence import image_warnings, inspect_image  # noqa: E402


class EvidenceImagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        os.environ["FULUA_DATABASE_PATH"] = str(self.root / "test.db")
        database.init_db()

    def tearDown(self) -> None:
        os.environ.pop("FULUA_DATABASE_PATH", None)

    def make_image(self, name: str, size: tuple[int, int], dpi: tuple[int, int]) -> Path:
        path = self.root / name
        image = Image.new("RGB", size, color=(255, 255, 255))
        image.save(path, dpi=dpi)
        return path

    def test_inspect_image_reads_dimensions_dpi_and_display_size(self) -> None:
        image_path = self.make_image("sample.png", (1200, 600), (300, 300))

        metadata = inspect_image(image_path)

        self.assertEqual(metadata["pixel_width"], 1200)
        self.assertEqual(metadata["pixel_height"], 600)
        self.assertAlmostEqual(metadata["dpi_x"], 300, delta=1)
        self.assertAlmostEqual(metadata["display_width_in"], 4.0, delta=0.1)

    def test_evidence_image_can_be_created_and_reordered(self) -> None:
        project = database.create_project("图片测试")
        first = database.create_evidence_image(
            project["id"],
            "A-1",
            {
                "file_path": "uploads/1/a-1/one.png",
                "original_name": "one.png",
                "caption": "第一张图",
                "alt_text": "第一张图",
                "pixel_width": 100,
                "pixel_height": 100,
                "dpi_x": 150,
                "dpi_y": 150,
                "display_width_in": 1,
                "display_height_in": 1,
            },
        )
        second = database.create_evidence_image(
            project["id"],
            "A-1",
            {
                "file_path": "uploads/1/a-1/two.png",
                "original_name": "two.png",
                "caption": "第二张图",
                "alt_text": "第二张图",
                "pixel_width": 100,
                "pixel_height": 100,
                "dpi_x": 150,
                "dpi_y": 150,
                "display_width_in": 1,
                "display_height_in": 1,
            },
        )

        reordered = database.reorder_evidence_images(project["id"], "A-1", [second["id"], first["id"]])

        self.assertEqual([row["id"] for row in reordered], [second["id"], first["id"]])
        self.assertEqual([row["sort_order"] for row in reordered], [1, 2])

    def test_image_warnings_report_low_dpi_without_requiring_alt_text(self) -> None:
        warnings = image_warnings(
            {
                "pixel_width": 1200,
                "dpi_x": 72,
                "dpi_y": 72,
                "display_width_in": 3,
                "alt_text": "",
            }
        )

        self.assertTrue(any("DPI" in warning for warning in warnings))
        self.assertFalse(any("alt" in warning for warning in warnings))

    def test_image_warnings_report_auto_scaled_width(self) -> None:
        warnings = image_warnings(
            {
                "pixel_width": 3000,
                "dpi_x": 150,
                "dpi_y": 150,
                "display_width_in": 9.69,
                "alt_text": "宽图",
            }
        )

        self.assertTrue(any("自动缩放" in warning for warning in warnings))

    def test_evidence_schema_uses_section_scoped_figure_label(self) -> None:
        project = database.create_project("图号测试")
        image = database.create_evidence_image(
            project["id"],
            "A-3",
            {
                "file_path": "uploads/1/a-3/four.png",
                "original_name": "four.png",
                "caption": "第四张图",
                "alt_text": "第四张图",
                "pixel_width": 100,
                "pixel_height": 100,
                "dpi_x": 150,
                "dpi_y": 150,
                "display_width_in": 1,
                "display_height_in": 1,
            },
        )

        schema = evidence_to_schema(image, section_index=4)

        self.assertEqual(schema.figure_label, "图A-3-4")


if __name__ == "__main__":
    unittest.main()
