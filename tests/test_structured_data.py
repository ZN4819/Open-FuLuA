import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException, Response


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.api.projects import list_projects as list_project_schemas  # noqa: E402
from app.api.projects import delete_project as delete_project_schema  # noqa: E402
from app.api.sections import build_section_detail  # noqa: E402
from app.api.sections import import_section_to_project  # noqa: E402
from app.api.sections import update_section_detail  # noqa: E402
from app.schemas import MetricResultWrite, SectionProjectImport  # noqa: E402
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

        deleted = delete_project_schema(project["id"], Response())

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

    def test_replace_section_rows_drops_stale_cross_references_not_in_record_text(self) -> None:
        project = database.create_project("stale reference save project")
        image = database.create_evidence_image(
            project["id"],
            "A-7",
            {
                "file_path": "uploads/stale/current.png",
                "original_name": "current.png",
                "caption": "current",
                "alt_text": "current",
                "pixel_width": 100,
                "pixel_height": 100,
                "dpi_x": 150,
                "dpi_y": 150,
                "display_width_in": 1,
                "display_height_in": 1,
            },
        )

        database.replace_section_rows(
            project_id=project["id"],
            code="A-7",
            rows=[
                {
                    "unit": "Unit",
                    "object_name": "Object",
                    "record_text": f"record [[FIG:{image['id']}]]",
                    "metric_result": {
                        "d": "/",
                        "a": "/",
                        "k": "/",
                        "object_score": "/",
                        "unit_score": "/",
                    },
                    "cross_references": [
                        {
                            "target_image_id": None,
                            "token": "[[FIG:9999]]",
                            "display_text": "old figure",
                        },
                        {
                            "target_image_id": image["id"],
                            "token": f"[[FIG:{image['id']}]]",
                            "display_text": "current figure",
                        },
                    ],
                }
            ],
        )

        section = database.get_section(project["id"], "A-7")
        references = database.list_cross_references(section["id"])

        self.assertEqual([row["token"] for row in references], [f"[[FIG:{image['id']}]]"])

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

    def test_section_can_be_imported_to_another_project_as_new_data(self) -> None:
        source = database.create_project("source project")
        target = database.create_project("target project")
        source_file = settings.storage_path / "uploads" / str(source["id"]) / "A-1" / "source.png"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(b"source-image")
        image = database.create_evidence_image(
            source["id"],
            "A-1",
            {
                "file_path": source_file.relative_to(settings.storage_path).as_posix(),
                "original_name": "source.png",
                "caption": "source caption",
                "alt_text": "source alt",
                "pixel_width": 100,
                "pixel_height": 80,
                "dpi_x": 144,
                "dpi_y": 144,
                "display_width_in": 1,
                "display_height_in": 0.8,
            },
        )
        database.replace_section_rows(
            project_id=source["id"],
            code="A-1",
            rows=[
                {
                    "unit": "身份鉴别",
                    "object_name": "源机房",
                    "subsystem": "源子系统",
                    "record_text": f"记录 [[FIG:{image['id']}]]",
                    "metric_result": {"d": "√", "a": "√", "k": "/", "object_score": "1"},
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
            project_id=target["id"],
            code="A-1",
            rows=[
                {
                    "unit": "身份鉴别",
                    "object_name": "目标机房",
                    "record_text": "已有记录",
                    "metric_result": {"d": "/", "a": "/", "k": "/", "object_score": "0"},
                }
            ],
        )

        detail = import_section_to_project(
            source["id"],
            "A-1",
            SectionProjectImport(target_project_id=target["id"]),
        )

        self.assertEqual([row.object_name for row in detail.rows], ["目标机房", "源机房"])
        self.assertEqual(
            [row.metric_result.unit_score for row in detail.rows],
            ["0.5000", "0.5000"],
        )
        self.assertEqual(detail.rows[1].subsystem, "源子系统")
        self.assertEqual(detail.subsystems, ["源子系统"])
        self.assertEqual(len(detail.evidence_images), 1)
        self.assertNotEqual(detail.evidence_images[0].id, image["id"])
        self.assertTrue((settings.storage_path / detail.evidence_images[0].file_path).exists())
        self.assertIn(f"[[FIG:{detail.evidence_images[0].id}]]", detail.rows[1].record_text)
        self.assertEqual(detail.cross_references[0].target_image_id, detail.evidence_images[0].id)

    def test_section_import_rejects_duplicate_target_object_names(self) -> None:
        source = database.create_project("source duplicate project")
        target = database.create_project("target duplicate project")
        for project in (source, target):
            database.replace_section_rows(
                project_id=project["id"],
                code="A-3",
                rows=[
                    {
                        "unit": "身份鉴别",
                        "object_name": "重复对象",
                        "record_text": "记录",
                        "metric_result": {"d": "/", "a": "/", "k": "/", "object_score": "1"},
                    }
                ],
            )

        with self.assertRaises(Exception) as context:
            import_section_to_project(
                source["id"],
                "A-3",
                SectionProjectImport(target_project_id=target["id"]),
            )

        self.assertIn("同名测评对象", str(context.exception))

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
                    "metric_result": {"d": "√", "a": "√", "k": "√", "object_score": "9.9999", "unit_score": "9.9999"},
                },
                {
                    "unit": "Unit A",
                    "object_name": "Object 2",
                    "record_text": "record 2",
                    "metric_result": {"d": "×", "a": "√", "k": "√", "object_score": "9.9999", "unit_score": "9.9999"},
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

    def test_technical_scores_are_authoritative_and_legacy_rows_are_recalculated_on_read(self) -> None:
        project = database.create_project("权威评分测试")
        database.replace_section_rows(
            project_id=project["id"],
            code="A-1",
            rows=[
                {
                    "unit": "身份鉴别",
                    "object_name": "服务器",
                    "record_text": "记录",
                    "metric_result": {
                        "d": "√",
                        "a": "×",
                        "k": "×",
                        "ra": "0.2",
                        "rk": "1.2",
                        "object_score": "9.9999",
                        "unit_score": "9.9999",
                    },
                }
            ],
        )
        section = database.get_section(project["id"], "A-1")
        raw = database.list_assessment_rows(section["id"])[0]
        self.assertEqual((raw["object_score"], raw["unit_score"]), ("0.0600", "0.0600"))

        with database.connect() as db:
            db.execute(
                "UPDATE metric_results SET ra = NULL, rk = NULL, object_score = '9.9999', unit_score = '9.9999' WHERE row_id = ?",
                (raw["id"],),
            )
        detail = build_section_detail(project["id"], "A-1")
        metric = detail.rows[0].metric_result
        self.assertEqual((metric.ra, metric.rk), ("1", "1"))
        self.assertEqual((metric.object_score, metric.unit_score), ("0.2500", "0.2500"))
        stored = database.list_assessment_rows(section["id"])[0]
        self.assertEqual((stored["ra"], stored["rk"], stored["object_score"]), (None, None, "9.9999"))

    def test_metric_write_rejects_invalid_indicators_and_factors(self) -> None:
        with self.assertRaises(Exception):
            MetricResultWrite(d="非法")
        with self.assertRaises(Exception):
            MetricResultWrite(ra="0.3")
        with self.assertRaises(Exception):
            MetricResultWrite(rk="2")

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

    def test_management_object_name_is_canonicalized_from_template_profile(self) -> None:
        project = database.create_project("固定管理对象测试")

        database.replace_section_rows(
            project_id=project["id"],
            code="A-5",
            rows=[
                {
                    "unit": "具备密码应用安全管理制度",
                    "object_name": "用户输入的对象",
                    "record_text": "保留原结果记录",
                    "metric_result": {"compliance": "符合", "unit_score": "1"},
                }
            ],
        )

        detail = build_section_detail(project["id"], "A-5")
        self.assertEqual(
            detail.rows[0].object_name,
            "管理体系（包括安全管理制度类文档、密码应用方案、密钥管理制度及策略类文档、操作规程类文档、记录表单类文档、系统相关人员）",
        )
        self.assertEqual(detail.rows[0].record_text, "保留原结果记录")

    def test_a7_save_materializes_both_fixed_objects_for_each_unit(self) -> None:
        project = database.create_project("A-7 双固定对象测试")

        database.replace_section_rows(
            project_id=project["id"],
            code="A-7",
            rows=[
                {
                    "unit": "制定密码应用方案",
                    "object_name": "旧对象",
                    "record_text": "已有记录",
                    "metric_result": {"compliance": "部分符合", "unit_score": "0.5"},
                }
            ],
        )

        detail = build_section_detail(project["id"], "A-7")
        self.assertEqual(len(detail.rows), 2)
        self.assertEqual(
            [row.object_name for row in detail.rows],
            [
                "密码应用方案、密钥管理制度及策略类文档、密码实施方案、商用密码应用安全性评估报告、密码应用安全管理制度、攻防对抗演习报告、整改文档",
                "管理体系（包括安全管理制度类文档、记录表单类文档、系统相关人员）",
            ],
        )
        self.assertEqual(detail.rows[0].record_text, "已有记录")
        self.assertEqual(detail.rows[1].record_text, "")

    def test_a7_normalization_preserves_later_exact_object_record(self) -> None:
        project = database.create_project("A-7 精确对象匹配测试")
        second_object = "管理体系（包括安全管理制度类文档、记录表单类文档、系统相关人员）"

        database.replace_section_rows(
            project_id=project["id"],
            code="A-7",
            rows=[
                {
                    "unit": "制定密码应用方案",
                    "object_name": second_object,
                    "record_text": "第二固定对象的已有记录",
                },
                {
                    "unit": "制定密码应用方案",
                    "object_name": "旧版自定义对象",
                    "record_text": "应归入第一固定对象的旧记录",
                },
            ],
        )

        detail = build_section_detail(project["id"], "A-7")
        self.assertEqual(detail.rows[0].record_text, "应归入第一固定对象的旧记录")
        self.assertEqual(detail.rows[1].object_name, second_object)
        self.assertEqual(detail.rows[1].record_text, "第二固定对象的已有记录")

    def test_management_section_import_rejects_append_to_existing_fixed_rows(self) -> None:
        source = database.create_project("固定对象导入源项目")
        target = database.create_project("固定对象导入目标项目")
        for project, record_text in ((source, "源记录"), (target, "目标记录")):
            database.replace_section_rows(
                project_id=project["id"],
                code="A-5",
                rows=[
                    {
                        "unit": "具备密码应用安全管理制度",
                        "object_name": "旧对象名",
                        "record_text": record_text,
                    }
                ],
            )
            section = database.get_section(project["id"], "A-5")
            with database.connect() as db:
                db.execute(
                    "UPDATE assessment_rows SET object_name = ? WHERE section_id = ?",
                    (f"{record_text}旧对象", section["id"]),
                )

        with self.assertRaises(ValueError) as context:
            database.append_section_to_project(source["id"], target["id"], "A-5")

        self.assertIn("目标章节已有数据时不能追加导入", str(context.exception))
        target_detail = build_section_detail(target["id"], "A-5")
        self.assertEqual(len(target_detail.rows), 1)
        self.assertEqual(target_detail.rows[0].record_text, "目标记录")

    def test_management_save_rejects_rows_beyond_fixed_object_count(self) -> None:
        project = database.create_project("固定对象数量校验测试")
        payload = SectionUpdate(
            rows=[
                {"unit": "应急策略", "object_name": "对象一", "record_text": "记录一"},
                {"unit": "应急策略", "object_name": "对象二", "record_text": "记录二"},
            ]
        )

        with self.assertRaises(HTTPException) as context:
            update_section_detail(project["id"], "A-8", payload)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("只允许 1 个固定测评对象", context.exception.detail)

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
