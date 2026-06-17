import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.api.projects import list_projects as list_project_schemas  # noqa: E402
from app.api.sections import build_section_detail  # noqa: E402
from app.schemas import SectionUpdate  # noqa: E402


class StructuredDataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        os.environ["FULUA_DATABASE_PATH"] = str(Path(self.temp_dir.name) / "test.db")
        database.init_db()

    def tearDown(self) -> None:
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
