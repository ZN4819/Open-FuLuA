import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.api.projects import list_projects as list_project_schemas  # noqa: E402
from app.api.projects import delete_project as delete_project_schema  # noqa: E402
from app.api.sections import build_section_detail  # noqa: E402
from app.api.sections import update_section_detail  # noqa: E402
from app.config import settings  # noqa: E402
from app.schemas import SectionUpdate  # noqa: E402


class StructuredDataTest(unittest.TestCase):
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

    def test_new_project_has_eight_sections(self) -> None:
        project = database.create_project("阶段三测试项目")
        sections = database.list_sections(project["id"])

        self.assertEqual(len(sections), 8)
        self.assertEqual(sections[0]["code"], "A-1")
        self.assertEqual(sections[-1]["code"], "A-8")

    def test_existing_projects_can_be_listed_for_reopening(self) -> None:
        first = database.create_project("第一个项目")
        second = database.create_project("第二个项目")

        projects = database.list_projects()
        api_projects = list_project_schemas()

        self.assertEqual([row["id"] for row in projects], [second["id"], first["id"]])
        self.assertEqual([project.id for project in api_projects], [second["id"], first["id"]])
        self.assertEqual(len(api_projects[0].sections), 8)

    def test_project_can_be_deleted_with_related_runtime_files(self) -> None:
        project = database.create_project("待删除项目")
        upload_dir = settings.storage_path / "uploads" / str(project["id"])
        export_dir = settings.storage_path / "exports" / str(project["id"])
        preview_dir = settings.storage_path / "previews" / str(project["id"])
        for path in (upload_dir, export_dir, preview_dir):
            path.mkdir(parents=True, exist_ok=True)
            (path / "sample.txt").write_text("runtime", encoding="utf-8")

        deleted = delete_project_schema(project["id"])

        self.assertEqual(deleted.id, project["id"])
        self.assertIsNone(database.get_project_by_id(project["id"]))
        self.assertEqual(database.list_sections(project["id"]), [])
        self.assertFalse(upload_dir.exists())
        self.assertFalse(export_dir.exists())
        self.assertFalse(preview_dir.exists())

    def test_section_rows_and_metric_results_can_be_replaced(self) -> None:
        project = database.create_project("结构化数据测试")

        updated = database.replace_section_rows(
            project_id=project["id"],
            code="A-1",
            rows=[
                {
                    "unit": "身份鉴别",
                    "object_name": "金融城机房",
                    "record_text": "测评验证记录：见 [[FIG:1]]。",
                    "sort_order": 1,
                    "metric_result": {
                        "d": "√",
                        "a": "√",
                        "k": "/",
                        "object_score": "1.0000",
                        "unit_score": "1.0000",
                    },
                    "cross_references": [
                        {
                            "target_image_id": None,
                            "token": "[[FIG:1]]",
                            "display_text": "图A-1-1",
                        }
                    ],
                }
            ],
        )

        self.assertIsNotNone(updated)
        section = database.get_section(project["id"], "A-1")
        rows = database.list_assessment_rows(section["id"])
        references = database.list_cross_references(section["id"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["unit"], "身份鉴别")
        self.assertEqual(rows[0]["d"], "√")
        self.assertEqual(rows[0]["k"], "/")
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]["display_text"], "图A-1-1")

    def test_assessment_rows_preserve_subsystem(self) -> None:
        project = database.create_project("subsystem project")
        database.replace_section_rows(
            project_id=project["id"],
            code="A-2",
            rows=[
                {
                    "unit": "Network identity",
                    "object_name": "Payment service",
                    "subsystem": "Core banking",
                    "record_text": "record",
                    "metric_result": {"d": "/", "a": "/", "k": "/", "object_score": "1"},
                },
                {
                    "unit": "Network identity",
                    "object_name": "Legacy service",
                    "record_text": "record",
                    "metric_result": {"d": "/", "a": "/", "k": "/", "object_score": "0"},
                },
            ],
        )

        section = database.get_section(project["id"], "A-2")
        rows = database.list_assessment_rows(section["id"])
        detail = build_section_detail(project["id"], "A-2")

        self.assertEqual(rows[0]["subsystem"], "Core banking")
        self.assertEqual(rows[1]["subsystem"], "")
        self.assertEqual(detail.rows[0].subsystem, "Core banking")
        self.assertEqual(detail.rows[1].subsystem, "")

    def test_section_update_preserves_subsystem_catalog_and_assignments(self) -> None:
        project = database.create_project("subsystem catalog project")

        update_section_detail(
            project["id"],
            "A-2",
            SectionUpdate(
                subsystems=["Core banking"],
                rows=[
                    {
                        "unit": "Network identity",
                        "object_name": "Payment service",
                        "subsystem": "Core banking",
                        "record_text": "record",
                        "metric_result": {"d": "/", "a": "/", "k": "/", "object_score": "1"},
                    },
                    {
                        "unit": "Network identity",
                        "object_name": "Legacy service",
                        "record_text": "record",
                        "metric_result": {"d": "/", "a": "/", "k": "/", "object_score": "0"},
                    },
                ],
            ),
        )

        detail = build_section_detail(project["id"], "A-2")

        self.assertEqual(detail.subsystems, ["Core banking"])
        self.assertEqual(detail.rows[0].subsystem, "Core banking")
        self.assertEqual(detail.rows[1].subsystem, "")

    def test_technical_unit_score_is_calculated_on_save(self) -> None:
        project = database.create_project("unit score project")
        database.replace_section_rows(
            project_id=project["id"],
            code="A-1",
            rows=[
                {
                    "unit": "Unit A",
                    "object_name": "Object 1",
                    "record_text": "record 1",
                    "metric_result": {"d": "/", "a": "/", "k": "/", "object_score": "1", "unit_score": "9.9999"},
                },
                {
                    "unit": "Unit A",
                    "object_name": "Object 2",
                    "record_text": "record 2",
                    "metric_result": {"d": "/", "a": "/", "k": "/", "object_score": "0", "unit_score": "9.9999"},
                },
                {
                    "unit": "Unit A",
                    "object_name": "Object 3",
                    "record_text": "record 3",
                    "metric_result": {"d": "/", "a": "/", "k": "/", "object_score": "/", "unit_score": "9.9999"},
                },
                {
                    "unit": "Unit B",
                    "object_name": "Object 4",
                    "record_text": "record 4",
                    "metric_result": {"d": "/", "a": "/", "k": "/", "object_score": "/", "unit_score": "9.9999"},
                },
                {
                    "unit": "Unit B",
                    "object_name": "Object 5",
                    "record_text": "record 5",
                    "metric_result": {"d": "/", "a": "/", "k": "/", "object_score": "/", "unit_score": "9.9999"},
                },
            ],
        )

        section = database.get_section(project["id"], "A-1")
        rows = database.list_assessment_rows(section["id"])

        self.assertEqual([row["object_score"] for row in rows[:3]], ["1.0000", "0.0000", "/"])
        self.assertEqual([row["unit_score"] for row in rows[:3]], ["0.5000", "0.5000", "0.5000"])
        self.assertEqual([row["unit_score"] for row in rows[3:]], ["/", "/"])

    def test_section_detail_shape_matches_api_contract(self) -> None:
        project = database.create_project("章节详情测试")
        database.replace_section_rows(
            project_id=project["id"],
            code="A-5",
            rows=[
                {
                    "unit": "具备密码应用安全管理制度",
                    "object_name": "管理体系",
                    "record_text": "经访谈及文档审查，制度已建立。",
                    "metric_result": {
                        "compliance": "符合",
                        "unit_score": "1.0000",
                    },
                }
            ],
        )

        detail = build_section_detail(project["id"], "A-5")

        self.assertEqual(detail.section.code, "A-5")
        self.assertEqual(len(detail.rows), 1)
        self.assertEqual(detail.rows[0].metric_result.compliance, "符合")
        self.assertEqual(detail.evidence_images, [])
        self.assertEqual(detail.cross_references, [])

    def test_section_update_schema_accepts_rows(self) -> None:
        payload = SectionUpdate(
            rows=[
                {
                    "unit": "身份鉴别",
                    "object_name": "测试对象",
                    "record_text": "测试记录",
                    "metric_result": {"d": "√", "a": "×", "k": "/"},
                }
            ]
        )

        self.assertEqual(payload.rows[0].metric_result.a, "×")


if __name__ == "__main__":
    unittest.main()
