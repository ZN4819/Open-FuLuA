import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.api.record_templates import copy_record_template_endpoint as api_copy_record_template  # noqa: E402
from app.api.record_templates import create_record_template as api_create_record_template  # noqa: E402
from app.api.record_templates import delete_record_template as api_delete_record_template  # noqa: E402
from app.api.record_templates import export_record_templates as api_export_record_templates  # noqa: E402
from app.api.record_templates import import_record_templates as api_import_record_templates  # noqa: E402
from app.api.record_templates import preview_record_template_import as api_preview_record_template_import  # noqa: E402
from app.api.record_templates import update_record_template as api_update_record_template  # noqa: E402
from app.api.record_template_slots import get_record_template_slots as api_get_record_template_slots  # noqa: E402
from app.api.record_template_slots import reset_record_template_slot_endpoint as api_reset_record_template_slot  # noqa: E402
from app.api.record_template_slots import update_record_template_slot_endpoint as api_update_record_template_slot  # noqa: E402
from app.schemas import RecordTemplateCreate, RecordTemplateImportItem, RecordTemplateImportPayload, RecordTemplateSlotUpdate, RecordTemplateUpdate  # noqa: E402
from app.services.record_templates import TEMPLATE_SLOT_TYPES, ensure_record_template_slots_seeded, list_record_template_slots, list_record_templates, load_record_template_library  # noqa: E402


class RecordTemplatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_database_path = os.environ.get("FULUA_DATABASE_PATH")
        os.environ["FULUA_DATABASE_PATH"] = str(Path(self.temp_dir.name) / "test.db")
        database.init_db()

    def tearDown(self) -> None:
        if self.previous_database_path is None:
            os.environ.pop("FULUA_DATABASE_PATH", None)
        else:
            os.environ["FULUA_DATABASE_PATH"] = self.previous_database_path
        self.temp_dir.cleanup()

    def test_record_template_library_covers_all_sections(self) -> None:
        templates = list_record_templates()
        section_codes = {template["section_code"] for template in templates}

        self.assertGreaterEqual(len(templates), 100)
        self.assertEqual(section_codes, {f"A-{index}" for index in range(1, 9)})

    def test_record_templates_can_be_filtered_by_section(self) -> None:
        a5_templates = list_record_templates("A-5")

        self.assertTrue(a5_templates)
        self.assertTrue(all(template["section_code"] == "A-5" for template in a5_templates))
        self.assertTrue(any("密码应用安全管理制度" in template["unit"] for template in a5_templates))

    def test_record_templates_do_not_keep_sample_figure_numbers(self) -> None:
        templates = list_record_templates()
        figure_number = re.compile(r"图\s*A\s*-\s*\d+\s*-\s*\d+")

        self.assertFalse(any(figure_number.search(template["record_text"]) for template in templates))
        self.assertTrue(any("[插入图片引用]" in template["record_text"] for template in templates))

    def test_record_template_library_metadata_is_present(self) -> None:
        library = load_record_template_library()

        self.assertEqual(library["profile_id"], "appendix_a_record_templates_v1")
        self.assertEqual(library["source_document"], "附录A编写.docx")

    def test_record_templates_are_seeded_into_database(self) -> None:
        templates = list_record_templates()
        library_count = len(load_record_template_library()["templates"])

        with database.connect() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM record_templates
                WHERE source_type = 'system' AND deleted_at IS NULL
                """
            ).fetchone()

        self.assertEqual(len(templates), library_count)
        self.assertEqual(row["total"], library_count)
        self.assertTrue(all(template["source_type"] == "system" for template in templates))

    def test_record_template_seed_is_idempotent(self) -> None:
        first = list_record_templates()
        second = list_record_templates()

        with database.connect() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM record_templates
                WHERE source_type = 'system' AND deleted_at IS NULL
                """
            ).fetchone()

        self.assertEqual(len(first), len(second))
        self.assertEqual(row["total"], len(first))

    def test_user_record_template_can_be_created_updated_and_deleted(self) -> None:
        created = api_create_record_template(
            RecordTemplateCreate(
                section_code="A-1",
                table_type="technical",
                unit="身份鉴别",
                object_name="测试机房",
                record_text="测评验证记录：用户新增模板。",
                tags=["机房", " 身份鉴别 ", "机房"],
            )
        )

        self.assertTrue(created.id.startswith("user-"))
        self.assertEqual(created.source_type, "user")
        self.assertEqual(created.title, "身份鉴别 / 测试机房")
        self.assertEqual(created.tags, ["机房", "身份鉴别"])
        self.assertTrue(any(template["id"] == created.id for template in list_record_templates("A-1")))

        updated = api_update_record_template(
            created.id,
            RecordTemplateUpdate(
                title="用户模板标题",
                record_text="测评验证记录：用户模板已修改。",
                tags=["已修改"],
            ),
        )

        self.assertEqual(updated.title, "用户模板标题")
        self.assertEqual(updated.record_text, "测评验证记录：用户模板已修改。")
        self.assertEqual(updated.tags, ["已修改"])

        deleted = api_delete_record_template(created.id)

        self.assertEqual(deleted.id, created.id)
        self.assertFalse(deleted.is_enabled)
        self.assertFalse(any(template["id"] == created.id for template in list_record_templates("A-1")))

    def test_user_record_template_validation_rejects_invalid_payload(self) -> None:
        with self.assertRaises(HTTPException) as context:
            api_create_record_template(
                RecordTemplateCreate(
                    section_code="B-1",
                    table_type="technical",
                    unit="身份鉴别",
                    object_name="测试机房",
                    record_text="无效章节。",
                )
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("A-1 至 A-8", context.exception.detail)

        with self.assertRaises(HTTPException) as context:
            api_create_record_template(
                RecordTemplateCreate(
                    section_code="A-1",
                    table_type="technical",
                    unit="身份鉴别",
                    object_name="测试机房",
                    record_text="",
                )
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("正文不能为空", context.exception.detail)


    def test_record_templates_can_be_searched_by_keyword(self) -> None:
        created = api_create_record_template(
            RecordTemplateCreate(
                section_code="A-2",
                table_type="technical",
                unit="KB5 搜索单元",
                object_name="KB5 搜索对象",
                title="KB5 keyword marker",
                record_text="这是一条用于 LIKE 搜索的用户模板。",
                tags=["KB5Search"],
            )
        )

        results = list_record_templates(keyword="KB5Search")

        self.assertEqual([template["id"] for template in results], [created.id])

    def test_user_record_templates_can_be_exported_previewed_and_imported(self) -> None:
        created = api_create_record_template(
            RecordTemplateCreate(
                section_code="A-3",
                table_type="technical",
                unit="KB5 备份单元",
                object_name="KB5 备份对象",
                title="KB5 备份模板",
                record_text="导出前正文。",
                tags=["backup"],
            )
        )
        exported = api_export_record_templates()

        self.assertEqual(exported.profile_id, "appendix_a_user_record_templates_v1")
        self.assertEqual(len(exported.templates), 1)
        self.assertEqual(exported.templates[0].id, created.id)

        same_preview = api_preview_record_template_import(
            RecordTemplateImportPayload(templates=exported.templates)
        )
        self.assertEqual(same_preview.summary.skipped, 1)
        self.assertEqual(same_preview.summary.errors, 0)

        changed_template = exported.templates[0].model_copy(update={"record_text": "导入后正文。"})
        changed_payload = RecordTemplateImportPayload(templates=[changed_template])
        changed_preview = api_preview_record_template_import(changed_payload)
        self.assertEqual(changed_preview.summary.updated, 1)

        imported = api_import_record_templates(changed_payload)
        self.assertEqual(imported.summary.updated, 1)
        updated = [template for template in list_record_templates("A-3") if template["id"] == created.id][0]
        self.assertEqual(updated["record_text"], "导入后正文。")

    def test_import_preview_reports_invalid_templates(self) -> None:
        preview = api_preview_record_template_import(
            RecordTemplateImportPayload(
                templates=[
                    RecordTemplateImportItem(
                        section_code="B-1",
                        table_type="technical",
                        unit="无效单元",
                        object_name="无效对象",
                        title="无效模板",
                        record_text="无效章节。",
                    )
                ]
            )
        )

        self.assertEqual(preview.summary.errors, 1)
        self.assertEqual(preview.items[0].action, "error")

    def test_import_does_not_overwrite_system_templates(self) -> None:
        system_template = list_record_templates("A-1")[0]
        payload = RecordTemplateImportPayload(
            templates=[
                RecordTemplateImportItem(
                    id=system_template["id"],
                    section_code=system_template["section_code"],
                    table_type=system_template["table_type"],
                    unit=system_template["unit"],
                    object_name=system_template["object_name"],
                    title=system_template["title"],
                    record_text="不应覆盖系统模板。",
                    tags=["restore"],
                )
            ]
        )

        preview = api_preview_record_template_import(payload)
        self.assertEqual(preview.summary.created, 1)
        imported = api_import_record_templates(payload)
        self.assertEqual(imported.summary.created, 1)

        refreshed_system = [template for template in list_record_templates("A-1") if template["id"] == system_template["id"]][0]
        self.assertEqual(refreshed_system["record_text"], system_template["record_text"])
        self.assertTrue(
            any(
                template["source_type"] == "user" and template["record_text"] == "不应覆盖系统模板。"
                for template in list_record_templates("A-1")
            )
        )
    def test_system_record_template_cannot_be_updated_or_deleted(self) -> None:
        system_template = list_record_templates("A-1")[0]

        with self.assertRaises(HTTPException) as update_context:
            api_update_record_template(system_template["id"], RecordTemplateUpdate(title="不应允许修改"))

        self.assertEqual(update_context.exception.status_code, 403)
        self.assertIn("系统模板不能直接修改或删除", update_context.exception.detail)

        with self.assertRaises(HTTPException) as delete_context:
            api_delete_record_template(system_template["id"])

        self.assertEqual(delete_context.exception.status_code, 403)
        self.assertIn("系统模板不能直接修改或删除", delete_context.exception.detail)

    def test_system_record_template_can_be_copied_to_user_template(self) -> None:
        system_template = list_record_templates("A-5")[0]

        copied = api_copy_record_template(system_template["id"])

        self.assertTrue(copied.id.startswith("user-"))
        self.assertEqual(copied.source_type, "user")
        self.assertEqual(copied.section_code, system_template["section_code"])
        self.assertEqual(copied.table_type, system_template["table_type"])
        self.assertEqual(copied.record_text, system_template["record_text"])
        self.assertTrue(any(template["id"] == copied.id for template in list_record_templates("A-5")))

    def test_record_template_slots_are_seeded_for_every_fixed_unit(self) -> None:
        source_templates = list_record_templates()
        expected_units = {
            (template["section_code"], template["unit"])
            for template in source_templates
        }

        slots = list_record_template_slots()
        slots_by_unit: dict[tuple[str, str], list[dict]] = {}
        for slot in slots:
            slots_by_unit.setdefault((slot["section_code"], slot["unit"]), []).append(slot)

        self.assertEqual(set(slots_by_unit), expected_units)
        self.assertEqual(len(slots), len(expected_units) * len(TEMPLATE_SLOT_TYPES))
        for unit_slots in slots_by_unit.values():
            self.assertEqual(
                {slot["template_type"] for slot in unit_slots},
                set(TEMPLATE_SLOT_TYPES),
            )
            self.assertTrue(all(slot["record_text"] for slot in unit_slots))
            self.assertTrue(all(slot["default_record_text"] for slot in unit_slots))
            self.assertTrue(all(slot["title"] for slot in unit_slots))

    def test_record_template_slot_seed_is_idempotent_and_keeps_old_templates(self) -> None:
        first = list_record_template_slots()
        ensure_record_template_slots_seeded()
        second = list_record_template_slots()

        with database.connect() as db:
            slot_count = db.execute("SELECT COUNT(*) AS total FROM record_template_slots").fetchone()["total"]
            old_template_count = db.execute("SELECT COUNT(*) AS total FROM record_templates").fetchone()["total"]

        self.assertEqual(len(first), len(second))
        self.assertEqual(slot_count, len(first))
        self.assertEqual(old_template_count, len(load_record_template_library()["templates"]))

    def test_record_template_slots_can_be_filtered_by_section_and_unit(self) -> None:
        all_slots = list_record_template_slots("A-1")
        unit = all_slots[0]["unit"]

        unit_slots = list_record_template_slots("A-1", unit)

        self.assertEqual(len(unit_slots), len(TEMPLATE_SLOT_TYPES))
        self.assertTrue(all(slot["section_code"] == "A-1" for slot in unit_slots))
        self.assertTrue(all(slot["unit"] == unit for slot in unit_slots))
        self.assertEqual([slot["template_type"] for slot in unit_slots], list(TEMPLATE_SLOT_TYPES))

    def test_record_template_slot_api_can_read_update_and_reset(self) -> None:
        section_slots = api_get_record_template_slots(section_code="A-1")
        unit = section_slots[0].unit
        filtered = api_get_record_template_slots(
            section_code="A-1",
            unit=unit,
            template_type="non_compliant",
        )
        slot = filtered[0]
        default_tags = slot.tags

        updated = api_update_record_template_slot(
            slot.id,
            RecordTemplateSlotUpdate(
                title="T3-2 不符合模板",
                record_text="T3-2 已修改的不符合模板正文。",
                tags=["T3-2", " 不符合 ", "T3-2"],
            ),
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(updated.title, "T3-2 不符合模板")
        self.assertEqual(updated.record_text, "T3-2 已修改的不符合模板正文。")
        self.assertEqual(updated.tags, ["T3-2", "不符合"])
        self.assertTrue(updated.is_customized)

        reset = api_reset_record_template_slot(slot.id)

        self.assertEqual(reset.title, reset.template_type_label)
        self.assertEqual(reset.record_text, slot.default_record_text)
        self.assertEqual(reset.tags, default_tags)
        self.assertFalse(reset.is_customized)

    def test_record_template_slot_api_rejects_invalid_type_and_missing_slot(self) -> None:
        with self.assertRaises(HTTPException) as invalid_type_context:
            api_get_record_template_slots(template_type="extra_type")

        self.assertEqual(invalid_type_context.exception.status_code, 400)
        self.assertIn("compliant", invalid_type_context.exception.detail)

        with self.assertRaises(HTTPException) as update_context:
            api_update_record_template_slot(
                999999,
                RecordTemplateSlotUpdate(record_text="不存在的槽位。"),
            )

        self.assertEqual(update_context.exception.status_code, 404)

        with self.assertRaises(HTTPException) as reset_context:
            api_reset_record_template_slot(999999)

        self.assertEqual(reset_context.exception.status_code, 404)

    def test_record_template_slot_api_has_no_delete_route(self) -> None:
        from app.main import app  # noqa: E402

        matching_delete_routes = [
            route
            for route in app.routes
            if getattr(route, "path", "") == "/api/record-template-slots/{slot_id}"
            and "DELETE" in getattr(route, "methods", set())
        ]

        self.assertEqual(matching_delete_routes, [])

if __name__ == "__main__":
    unittest.main()
