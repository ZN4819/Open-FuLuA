import os
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image
from starlette.datastructures import Headers


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.api.evidence import evidence_to_schema  # noqa: E402
from app.api.evidence import list_section_evidence  # noqa: E402
from app.api.evidence import replace_evidence_image_file as api_replace_evidence_image_file  # noqa: E402
from app.api.evidence import upload_evidence_images  # noqa: E402
from app.api.sections import build_section_detail  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.evidence import image_warnings, inspect_image  # noqa: E402


class EvidenceImagesTest(unittest.TestCase):
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

    def make_image(self, name: str, size: tuple[int, int], dpi: tuple[int, int]) -> Path:
        path = self.root / name
        image = Image.new("RGB", size, color=(255, 255, 255))
        image.save(path, dpi=dpi)
        return path

    def make_image_bytes(self, size: tuple[int, int] = (200, 100), dpi: tuple[int, int] = (150, 150)) -> bytes:
        buffer = BytesIO()
        image = Image.new("RGB", size, color=(255, 255, 255))
        image.save(buffer, format="PNG", dpi=dpi)
        return buffer.getvalue()

    def make_upload(self, filename: str, content: bytes, content_type: str = "image/png") -> UploadFile:
        return UploadFile(
            file=BytesIO(content),
            filename=filename,
            headers=Headers({"content-type": content_type}),
        )

    def create_stored_evidence_image(self, project_id: int, filename: str = "old.png"):
        relative_path = Path("uploads") / str(project_id) / "A-1" / filename
        absolute_path = settings.storage_path / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 100), color=(255, 255, 255)).save(absolute_path, dpi=(150, 150))
        return database.create_evidence_image(
            project_id,
            "A-1",
            {
                "file_path": relative_path.as_posix(),
                "original_name": filename,
                "caption": "旧题注",
                "alt_text": "旧题注",
                "pixel_width": 100,
                "pixel_height": 100,
                "dpi_x": 150,
                "dpi_y": 150,
                "display_width_in": 1,
                "display_height_in": 1,
            },
        )

    def create_evidence_row(
        self,
        project_id: int,
        section_code: str,
        filename: str,
        sort_order: int | None = None,
    ):
        image_data = {
            "file_path": f"uploads/{project_id}/{section_code}/{filename}",
            "original_name": filename,
            "caption": filename,
            "alt_text": filename,
            "pixel_width": 100,
            "pixel_height": 100,
            "dpi_x": 150,
            "dpi_y": 150,
            "display_width_in": 1,
            "display_height_in": 1,
        }
        if sort_order is not None:
            image_data["sort_order"] = sort_order
        return database.create_evidence_image(project_id, section_code, image_data)

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

        self.assertTrue(first["evidence_uuid"])
        self.assertNotEqual(first["evidence_uuid"], second["evidence_uuid"])
        self.assertEqual([row["id"] for row in reordered], [second["id"], first["id"]])
        self.assertEqual([row["sort_order"] for row in reordered], [1, 2])

    def test_batch_upload_creates_ordered_evidence_images(self) -> None:
        project = database.create_project("批量上传测试")

        payload = upload_evidence_images(
            project["id"],
            section_code="A-1",
            caption="机房照片",
            alt_text="",
            files=[
                self.make_upload("one.png", self.make_image_bytes()),
                self.make_upload("two.png", self.make_image_bytes(size=(300, 120))),
            ],
        )

        self.assertEqual([image.original_name for image in payload], ["one.png", "two.png"])
        self.assertEqual([image.caption for image in payload], ["机房照片", "机房照片"])
        self.assertEqual([image.figure_label for image in payload], ["图A-1-1", "图A-1-2"])
        self.assertEqual([image.sort_order for image in payload], [1, 2])
        self.assertEqual(len(database.list_evidence_images(project["id"], "A-1")), 2)

    def test_batch_upload_rolls_back_when_any_file_is_invalid(self) -> None:
        project = database.create_project("批量上传回滚测试")

        with self.assertRaises(HTTPException) as context:
            upload_evidence_images(
                project["id"],
                section_code="A-1",
                caption="机房照片",
                alt_text="",
                files=[
                    self.make_upload("one.png", self.make_image_bytes()),
                    self.make_upload("not-image.txt", b"not an image", "text/plain"),
                ],
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(database.list_evidence_images(project["id"], "A-1"), [])
        stored_files = [path for path in settings.storage_path.rglob("*") if path.is_file()]
        self.assertEqual(stored_files, [])

    def test_project_image_numbers_restart_per_project_and_follow_section_order(self) -> None:
        first_project = database.create_project("first project")
        second_project = database.create_project("second project")
        first_a2 = self.create_evidence_row(first_project["id"], "A-2", "first-a2.png")
        first_a1_second = self.create_evidence_row(first_project["id"], "A-1", "first-a1-second.png", sort_order=2)
        first_a1_first = self.create_evidence_row(first_project["id"], "A-1", "first-a1-first.png", sort_order=1)
        self.create_evidence_row(second_project["id"], "A-1", "second-a1.png")

        first_project_a1 = list_section_evidence(first_project["id"], "A-1")
        first_project_a2 = list_section_evidence(first_project["id"], "A-2")
        second_project_a1 = list_section_evidence(second_project["id"], "A-1")
        section_detail = build_section_detail(first_project["id"], "A-2")

        self.assertEqual([image.id for image in first_project_a1], [first_a1_first["id"], first_a1_second["id"]])
        self.assertEqual([getattr(image, "project_image_no", None) for image in first_project_a1], [1, 2])
        self.assertEqual([image.figure_label for image in first_project_a1], ["\u56feA-1-1", "\u56feA-1-2"])
        self.assertEqual([image.id for image in first_project_a2], [first_a2["id"]])
        self.assertEqual([getattr(image, "project_image_no", None) for image in first_project_a2], [3])
        self.assertEqual([image.figure_label for image in first_project_a2], ["\u56feA-2-1"])
        self.assertEqual([getattr(image, "project_image_no", None) for image in second_project_a1], [1])
        self.assertEqual([image.figure_label for image in second_project_a1], ["\u56feA-1-1"])
        self.assertEqual(getattr(section_detail.evidence_images[0], "project_image_no", None), 3)

    def test_section_evidence_reindexes_project_image_numbers_after_delete(self) -> None:
        project = database.create_project("delete reindex")
        first = self.create_evidence_row(project["id"], "A-1", "first.png")
        deleted = self.create_evidence_row(project["id"], "A-1", "deleted.png")
        third = self.create_evidence_row(project["id"], "A-1", "third.png")
        a2 = self.create_evidence_row(project["id"], "A-2", "a2.png")

        deleted_row = database.delete_evidence_image(deleted["id"])
        after_delete_a1 = list_section_evidence(project["id"], "A-1")
        after_delete_a2 = list_section_evidence(project["id"], "A-2")
        replacement = self.create_evidence_row(project["id"], "A-1", "replacement.png")
        after_insert_a1 = list_section_evidence(project["id"], "A-1")

        self.assertEqual(deleted_row["id"], deleted["id"])
        self.assertEqual([image.id for image in after_delete_a1], [first["id"], third["id"]])
        self.assertEqual([getattr(image, "project_image_no", None) for image in after_delete_a1], [1, 2])
        self.assertEqual([image.figure_label for image in after_delete_a1], ["\u56feA-1-1", "\u56feA-1-2"])
        self.assertEqual([image.id for image in after_delete_a2], [a2["id"]])
        self.assertEqual([getattr(image, "project_image_no", None) for image in after_delete_a2], [3])
        self.assertEqual([image.figure_label for image in after_delete_a2], ["\u56feA-2-1"])
        self.assertGreater(replacement["id"], deleted["id"])
        self.assertEqual([image.id for image in after_insert_a1], [first["id"], third["id"], replacement["id"]])
        self.assertEqual([getattr(image, "project_image_no", None) for image in after_insert_a1], [1, 2, 3])
        self.assertEqual(after_insert_a1[-1].id, replacement["id"])

    def test_replace_evidence_image_file_preserves_identity_caption_and_order(self) -> None:
        project = database.create_project("图片替换测试")
        image = self.create_stored_evidence_image(project["id"])
        old_path = settings.storage_path / image["file_path"]

        updated = api_replace_evidence_image_file(
            image["id"],
            file=self.make_upload("new.png", self.make_image_bytes(size=(320, 160))),
        )

        self.assertEqual(updated.id, image["id"])
        self.assertEqual(updated.evidence_uuid, image["evidence_uuid"])
        self.assertEqual(updated.figure_label, "图A-1-1")
        self.assertEqual(updated.caption, "旧题注")
        self.assertEqual(updated.sort_order, 1)
        self.assertEqual(updated.original_name, "new.png")
        self.assertEqual(updated.pixel_width, 320)
        self.assertFalse(old_path.exists())
        self.assertTrue((settings.storage_path / updated.file_path).exists())

    def test_replace_evidence_image_file_keeps_original_when_new_file_is_invalid(self) -> None:
        project = database.create_project("图片替换失败测试")
        image = self.create_stored_evidence_image(project["id"])
        old_path = settings.storage_path / image["file_path"]

        with self.assertRaises(HTTPException) as context:
            api_replace_evidence_image_file(
                image["id"],
                file=self.make_upload("bad.txt", b"not image", "text/plain"),
            )

        self.assertEqual(context.exception.status_code, 400)
        current = database.get_evidence_image(image["id"])
        self.assertEqual(current["file_path"], image["file_path"])
        self.assertEqual(current["original_name"], "old.png")
        self.assertTrue(old_path.exists())

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
