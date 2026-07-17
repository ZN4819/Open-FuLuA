from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

from app import database
from app.contracts import (
    FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)
from app.report_schemas import (
    AppendixTransmissionRelationWrite,
    AssessmentObjectUpdate,
    AssessmentObjectWrite,
    CorrectionRelationWrite,
    ObjectRelationWrite,
    ObjectSubsystemWrite,
)
from app.services.report_domain import objects, validation
from app.services.report_domain.errors import ReportDomainError


def _technical_row(
    unit: str,
    object_name: str,
    subsystem: str,
    *,
    object_uuid: str | None = None,
    row_id: int | None = None,
) -> dict:
    return {
        "id": row_id,
        "assessment_object_uuid": object_uuid,
        "unit": unit,
        "object_name": object_name,
        "subsystem": subsystem,
        "record_text": "测评记录",
        "metric_result": {"d": "√", "a": "√", "k": "√", "ra": "1", "rk": "1"},
    }


class AppendixObjectRelationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.previous_data_dir = os.environ.get("FULUA_DATA_DIR")
        self.previous_database = os.environ.get("FULUA_DATABASE_PATH")
        os.environ["FULUA_DATA_DIR"] = str(self.root)
        os.environ["FULUA_DATABASE_PATH"] = str(self.root / "data" / "app.db")
        database.init_db()
        self.project = database.create_project(
            "附录对象关联测试",
            project_type="full_report",
            template_package_id=FULL_REPORT_TEMPLATE_PACKAGE_ID,
            template_edition=FULL_REPORT_TEMPLATE_EDITION,
            template_revision=FULL_REPORT_TEMPLATE_REVISION,
            template_asset_set_hash=FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
        )

    def tearDown(self) -> None:
        if self.previous_data_dir is None:
            os.environ.pop("FULUA_DATA_DIR", None)
        else:
            os.environ["FULUA_DATA_DIR"] = self.previous_data_dir
        if self.previous_database is None:
            os.environ.pop("FULUA_DATABASE_PATH", None)
        else:
            os.environ["FULUA_DATABASE_PATH"] = self.previous_database
        self.temporary.cleanup()

    @property
    def project_id(self) -> int:
        return int(self.project["id"])

    @property
    def project_uuid(self) -> str:
        return str(self.project["project_uuid"])

    def _save(self, code: str, rows: list[dict]) -> list[dict]:
        database.replace_section_rows(self.project_id, code, rows)
        section = database.get_section(self.project_id, code)
        return [dict(row) for row in database.list_assessment_rows(int(section["id"]))]

    def _seed_transmission_objects(self) -> tuple[str, str, str]:
        channel_one = str(uuid.uuid4())
        channel_two = str(uuid.uuid4())
        data_object = str(uuid.uuid4())
        self._save(
            "A-2",
            [
                _technical_row("通信过程中重要数据的机密性", "通道一", " 核心  应用 ", object_uuid=channel_one),
                _technical_row("通信数据完整性", "通道一", " 核心  应用 ", object_uuid=channel_one),
                _technical_row("通信过程中重要数据的机密性", "通道二", "核心 应用", object_uuid=channel_two),
                _technical_row("通信数据完整性", "通道二", "核心 应用", object_uuid=channel_two),
            ],
        )
        self._save(
            "A-4",
            [
                _technical_row("重要数据传输机密性", "客户数据", "核心 应用", object_uuid=data_object),
                _technical_row("重要数据传输完整性", "客户数据", "核心 应用", object_uuid=data_object),
            ],
        )
        return channel_one, channel_two, data_object

    def test_section_save_auto_groups_objects_preserves_identity_and_ignores_blank_temp_uuid(self) -> None:
        rows = self._save(
            "A-2",
            [
                _technical_row("通信数据完整性", "通道甲", "业务系统"),
                _technical_row("通信过程中重要数据的机密性", "通道甲", "业务系统"),
            ],
        )
        object_uuids = {row["assessment_object_uuid"] for row in rows}
        self.assertEqual(len(object_uuids), 1)
        object_uuid = next(iter(object_uuids))
        self.assertIsNotNone(object_uuid)
        with database.connect() as db:
            obj = db.execute(
                "SELECT * FROM assessment_objects WHERE project_id=? AND object_uuid=?",
                (self.project_id, object_uuid),
            ).fetchone()
            binding = db.execute(
                "SELECT subsystem_name FROM assessment_object_subsystems WHERE project_id=? AND object_uuid=?",
                (self.project_id, object_uuid),
            ).fetchone()
        self.assertEqual(obj["object_type"], "network")
        self.assertEqual(binding["subsystem_name"], "业务系统")

        saved_again = self._save(
            "A-2",
            [
                _technical_row(row["unit"], "通道甲更新", "业务系统", row_id=int(row["id"]))
                for row in rows
            ],
        )
        self.assertEqual({row["assessment_object_uuid"] for row in saved_again}, {object_uuid})

        temporary_uuid = str(uuid.uuid4())
        blank = self._save(
            "A-3",
            [_technical_row("身份鉴别", "", "", object_uuid=temporary_uuid)],
        )
        self.assertIsNone(blank[0]["assessment_object_uuid"])
        with database.connect() as db:
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM assessment_objects WHERE object_uuid=?",
                    (temporary_uuid,),
                ).fetchone()
            )

    def test_business_api_derives_rows_is_idempotent_and_enforces_revision(self) -> None:
        channel_one, channel_two, data_object = self._seed_transmission_objects()
        initial = objects.get_appendix_transmission_relations(self.project_uuid)
        self.assertEqual(initial["shared_subsystems"], ["核心  应用"])
        self.assertEqual(
            next(item for item in initial["a4_objects"] if item["object_uuid"] == data_object)["available_kinds"],
            ["confidentiality", "integrity"],
        )

        created = objects.put_appendix_transmission_relation(
            self.project_uuid,
            AppendixTransmissionRelationWrite(
                kind="confidentiality",
                a4_object_uuid=data_object,
                a2_object_uuid=channel_one,
                expected_revision=None,
            ),
        )
        relation = next(
            relation
            for item in created["a4_objects"]
            if item["object_uuid"] == data_object
            for relation in item["relations"]
        )
        self.assertEqual(relation["revision"], 1)
        correction_uuid = relation["correction_uuid"]
        revision_after_create = created["project_revision"]

        retried = objects.put_appendix_transmission_relation(
            self.project_uuid,
            AppendixTransmissionRelationWrite(
                kind="confidentiality",
                a4_object_uuid=data_object,
                a2_object_uuid=channel_one,
                expected_revision=None,
            ),
        )
        self.assertEqual(retried["project_revision"], revision_after_create)

        updated = objects.put_appendix_transmission_relation(
            self.project_uuid,
            AppendixTransmissionRelationWrite(
                kind="confidentiality",
                a4_object_uuid=data_object,
                a2_object_uuid=channel_two,
                expected_correction_uuid=correction_uuid,
                expected_revision=1,
            ),
        )
        updated_relation = next(
            relation
            for item in updated["a4_objects"]
            if item["object_uuid"] == data_object
            for relation in item["relations"]
        )
        self.assertEqual(updated_relation["a2_object_uuid"], channel_two)
        self.assertEqual(updated_relation["revision"], 2)

        with self.assertRaises(ReportDomainError) as stale:
            objects.put_appendix_transmission_relation(
                self.project_uuid,
                AppendixTransmissionRelationWrite(
                    kind="confidentiality",
                    a4_object_uuid=data_object,
                    a2_object_uuid=channel_one,
                    expected_correction_uuid=correction_uuid,
                    expected_revision=1,
                ),
            )
        self.assertEqual(stale.exception.code, "REVISION_CONFLICT")

        deleted = objects.put_appendix_transmission_relation(
            self.project_uuid,
            AppendixTransmissionRelationWrite(
                kind="confidentiality",
                a4_object_uuid=data_object,
                a2_object_uuid=None,
                expected_correction_uuid=correction_uuid,
                expected_revision=2,
            ),
        )
        retried_delete = objects.put_appendix_transmission_relation(
            self.project_uuid,
            AppendixTransmissionRelationWrite(
                kind="confidentiality",
                a4_object_uuid=data_object,
                a2_object_uuid=None,
                expected_correction_uuid=correction_uuid,
                expected_revision=2,
            ),
        )
        self.assertEqual(retried_delete["project_revision"], deleted["project_revision"])

        recreated = objects.put_appendix_transmission_relation(
            self.project_uuid,
            AppendixTransmissionRelationWrite(
                kind="confidentiality",
                a4_object_uuid=data_object,
                a2_object_uuid=channel_one,
                expected_revision=None,
            ),
        )
        replacement = next(
            relation
            for item in recreated["a4_objects"]
            if item["object_uuid"] == data_object
            for relation in item["relations"]
        )
        self.assertNotEqual(replacement["correction_uuid"], correction_uuid)
        with self.assertRaises(ReportDomainError) as aba:
            objects.put_appendix_transmission_relation(
                self.project_uuid,
                AppendixTransmissionRelationWrite(
                    kind="confidentiality",
                    a4_object_uuid=data_object,
                    a2_object_uuid=None,
                    expected_correction_uuid=correction_uuid,
                    expected_revision=2,
                ),
            )
        self.assertEqual(aba.exception.code, "REVISION_CONFLICT")

    def test_cross_subsystem_is_blocked_and_section_save_cannot_break_existing_relation(self) -> None:
        channel_one, _, data_object = self._seed_transmission_objects()
        created = objects.put_appendix_transmission_relation(
            self.project_uuid,
            AppendixTransmissionRelationWrite(
                kind="confidentiality",
                a4_object_uuid=data_object,
                a2_object_uuid=channel_one,
                expected_revision=None,
            ),
        )
        self.assertTrue(created["a4_objects"])
        with self.assertRaises(ReportDomainError) as legacy_subsystem_write:
            objects.upsert_subsystem(
                self.project_uuid,
                ObjectSubsystemWrite(
                    object_uuid=channel_one,
                    subsystem_name="其他系统",
                    expected_revision=1,
                ),
            )
        self.assertEqual(
            legacy_subsystem_write.exception.code,
            "CORRECTION_SUBSYSTEM_MISMATCH",
        )
        channel_object = next(
            item for item in objects.list_objects(self.project_uuid)
            if item["object_uuid"] == channel_one
        )
        with self.assertRaises(ReportDomainError) as deactivate_bound_object:
            objects.update_object(
                self.project_uuid,
                channel_one,
                AssessmentObjectUpdate(
                    object_type=channel_object["object_type"],
                    name_snapshot=channel_object["name_snapshot"],
                    source_section_code=channel_object["source_section_code"],
                    source_row_id=channel_object["source_row_id"],
                    properties={},
                    active=False,
                    expected_revision=channel_object["revision"],
                ),
            )
        self.assertEqual(
            deactivate_bound_object.exception.code,
            "APPENDIX_OBJECT_BACKEND_MANAGED",
        )
        with database.connect() as db:
            db.execute(
                "UPDATE assessment_objects SET active=0 WHERE project_id=? AND object_uuid=?",
                (self.project_id, channel_one),
            )
        invalid_endpoint_codes = {
            item["code"] for item in validation.validate_report(self.project_uuid)["issues"]
        }
        self.assertIn("CORRECTION_RELATION_ENDPOINT_INVALID", invalid_endpoint_codes)
        with database.connect() as db:
            db.execute(
                "UPDATE assessment_objects SET active=1 WHERE project_id=? AND object_uuid=?",
                (self.project_id, channel_one),
            )
        section = database.get_section(self.project_id, "A-4")
        a4_rows = [
            dict(row) for row in database.list_assessment_rows(int(section["id"]))
        ]
        with self.assertRaises(ValueError) as mismatch:
            self._save(
                "A-4",
                [
                    _technical_row(
                        row["unit"],
                        row["object_name"],
                        "其他系统",
                        object_uuid=data_object,
                        row_id=int(row["id"]),
                    )
                    for row in a4_rows
                ],
            )
        self.assertIn("先解除或调整关联", str(mismatch.exception))

        other_data = str(uuid.uuid4())
        self._save(
            "A-4",
            [
                *[
                    _technical_row(
                        row["unit"], row["object_name"], "核心 应用", object_uuid=data_object, row_id=int(row["id"])
                    )
                    for row in a4_rows
                ],
                _technical_row("重要数据传输机密性", "异地数据", "其他系统", object_uuid=other_data),
            ],
        )
        with self.assertRaises(ReportDomainError) as cross:
            objects.put_appendix_transmission_relation(
                self.project_uuid,
                AppendixTransmissionRelationWrite(
                    kind="confidentiality",
                    a4_object_uuid=other_data,
                    a2_object_uuid=channel_one,
                    expected_revision=None,
                ),
            )
        self.assertEqual(cross.exception.code, "CORRECTION_SUBSYSTEM_MISMATCH")
        with database.connect() as db:
            a2_row_id = int(
                db.execute(
                    """
                    SELECT r.id FROM assessment_rows r JOIN appendix_sections s ON s.id=r.section_id
                    WHERE s.project_id=? AND s.code='A-2' AND r.assessment_object_uuid=?
                      AND r.unit='通信过程中重要数据的机密性'
                    """,
                    (self.project_id, channel_one),
                ).fetchone()[0]
            )
            a4_row_id = int(
                db.execute(
                    """
                    SELECT r.id FROM assessment_rows r JOIN appendix_sections s ON s.id=r.section_id
                    WHERE s.project_id=? AND s.code='A-4' AND r.assessment_object_uuid=?
                      AND r.unit='重要数据传输机密性'
                    """,
                    (self.project_id, other_data),
                ).fetchone()[0]
            )
        with self.assertRaises(ReportDomainError) as legacy_cross:
            objects.create_correction_relation(
                self.project_uuid,
                CorrectionRelationWrite(
                    a2_object_uuid=channel_one,
                    a4_object_uuid=other_data,
                    correction_kind="confidentiality",
                    a2_metric_code="通信过程中重要数据的机密性",
                    a4_metric_code="重要数据传输机密性",
                    original_references={"a2_row_id": a2_row_id, "a4_row_id": a4_row_id},
                ),
            )
        self.assertEqual(legacy_cross.exception.code, "CORRECTION_SUBSYSTEM_MISMATCH")

    def test_delete_cleans_corrections_but_deactivates_object_with_other_references(self) -> None:
        channel_one, _, data_object = self._seed_transmission_objects()
        objects.put_appendix_transmission_relation(
            self.project_uuid,
            AppendixTransmissionRelationWrite(
                kind="confidentiality",
                a4_object_uuid=data_object,
                a2_object_uuid=channel_one,
                expected_revision=None,
            ),
        )
        parent = objects.create_object(
            self.project_uuid,
            AssessmentObjectWrite(object_type="other", name_snapshot="引用对象"),
        )
        objects.create_object_relation(
            self.project_uuid,
            ObjectRelationWrite(
                source_object_uuid=parent["object_uuid"],
                target_object_uuid=data_object,
                relation_type="depends_on",
            ),
        )
        self._save("A-4", [])
        with database.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM result_correction_relations WHERE project_id=?",
                    (self.project_id,),
                ).fetchone()[0],
                0,
            )
            retained = db.execute(
                "SELECT active,source_row_id FROM assessment_objects WHERE object_uuid=?",
                (data_object,),
            ).fetchone()
        self.assertIsNotNone(retained)
        self.assertEqual(retained["active"], 0)
        self.assertIsNone(retained["source_row_id"])

    def test_validation_reports_missing_subsystems_missing_relation_and_legacy_mismatch(self) -> None:
        a2_uuid = str(uuid.uuid4())
        a4_uuid = str(uuid.uuid4())
        self._save(
            "A-2",
            [_technical_row("通信过程中重要数据的机密性", "无子系统通道", "", object_uuid=a2_uuid)],
        )
        self._save(
            "A-4",
            [_technical_row("重要数据传输机密性", "无子系统数据", "", object_uuid=a4_uuid)],
        )
        issues = validation.validate_report(self.project_uuid)["issues"]
        codes = {item["code"] for item in issues}
        self.assertIn("A2_SUBSYSTEM_REQUIRED", codes)
        self.assertIn("A4_SUBSYSTEM_REQUIRED", codes)
        self.assertIn("A4_TRANSMISSION_RELATION_REQUIRED", codes)

        channel, _, data_object = self._seed_transmission_objects()
        objects.put_appendix_transmission_relation(
            self.project_uuid,
            AppendixTransmissionRelationWrite(
                kind="confidentiality",
                a4_object_uuid=data_object,
                a2_object_uuid=channel,
                expected_revision=None,
            ),
        )
        with database.connect() as db:
            db.execute(
                """
                UPDATE assessment_object_subsystems
                SET subsystem_name='其他系统',revision=revision+1,updated_at=?
                WHERE project_id=? AND object_uuid=?
                """,
                (database.utc_now(), self.project_id, data_object),
            )
        mismatch_codes = {
            item["code"] for item in validation.validate_report(self.project_uuid)["issues"]
        }
        self.assertIn("CORRECTION_SUBSYSTEM_MISMATCH", mismatch_codes)


if __name__ == "__main__":
    unittest.main()
