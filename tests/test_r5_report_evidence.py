from __future__ import annotations

import io
import os
import shutil
import sqlite3
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from PIL import Image
from starlette.datastructures import Headers

from app import database
from app.contracts import (
    FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)
from app.main import app
from app.report_evidence.contracts import APPENDIX_B_CATEGORY_CODES
from app.report_evidence.schemas import EvidenceCategoryUpdate, EvidenceItemWrite
from app.report_export.renderer import render_report
from app.report_schemas import ReportExportJobWrite
from app.services import report_evidence, report_exports
from app.services.data_migration import validate_database_and_evidence
from app.services.report_evidence_files import (
    resolve_managed_path,
    stage_managed_file_removal,
)
from app.services.report_domain import basic
from app.services.report_domain.errors import ReportDomainError
from app.services.projects import remove_project_runtime_files
from lxml import etree
from tests import test_r4_report_export as r4_tests


def _png_bytes(color: tuple[int, int, int] = (32, 96, 160)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (96, 64), color).save(output, format="PNG")
    return output.getvalue()


def _upload(name: str, content_type: str, data: bytes) -> UploadFile:
    return UploadFile(
        filename=name,
        file=io.BytesIO(data),
        headers=Headers({"content-type": content_type}),
    )


class R5ReportEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "app.db"
        self.storage_path = Path(self.temporary.name) / "storage"
        self.previous_database = os.environ.get("FULUA_DATABASE_PATH")
        self.previous_storage = os.environ.get("FULUA_STORAGE_PATH")
        os.environ["FULUA_DATABASE_PATH"] = str(self.database_path)
        os.environ["FULUA_STORAGE_PATH"] = str(self.storage_path)
        database.init_db()
        self.project = database.create_project(
            "R5 附录 B 测试",
            project_type="full_report",
            template_package_id=FULL_REPORT_TEMPLATE_PACKAGE_ID,
            template_edition=FULL_REPORT_TEMPLATE_EDITION,
            template_revision=FULL_REPORT_TEMPLATE_REVISION,
            template_asset_set_hash=FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
        )
        self.project_uuid = str(self.project["project_uuid"])
        self.member_uuids: list[str] = []
        timestamp = database.utc_now()
        with database.connect() as db:
            db.execute(
                "UPDATE system_profiles SET system_name = '分行特色系统' WHERE project_id = ?",
                (self.project["id"],),
            )
            db.execute(
                "UPDATE report_organizations SET name = '被测单位' WHERE project_id = ? AND organization_type = 'assessed'",
                (self.project["id"],),
            )
            self.assessed_organization_uuid = str(
                db.execute(
                    "SELECT organization_uuid FROM report_organizations WHERE project_id = ? AND organization_type = 'assessed'",
                    (self.project["id"],),
                ).fetchone()["organization_uuid"]
            )
            self.vendor_organization_uuid = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO report_organizations (
                    organization_uuid, project_id, organization_type, name, active,
                    sort_order, revision, created_at, updated_at
                ) VALUES (?, ?, 'vendor', '密评机构', 1, 10, 1, ?, ?)
                """,
                (self.vendor_organization_uuid, self.project["id"], timestamp, timestamp),
            )
            for index, name in enumerate(("张三", "李四", "王五"), start=1):
                member_uuid = str(uuid.uuid4())
                self.member_uuids.append(member_uuid)
                db.execute(
                    """
                    INSERT INTO report_members (
                        member_uuid, project_id, name, team_role, is_project_leader,
                        qualification_passed_at, active, sort_order, revision,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, '组员', 0, '2025-01-01', 1, ?, 1, ?, ?)
                    """,
                    (member_uuid, self.project["id"], name, index, timestamp, timestamp),
                )

    def tearDown(self) -> None:
        if self.previous_database is None:
            os.environ.pop("FULUA_DATABASE_PATH", None)
        else:
            os.environ["FULUA_DATABASE_PATH"] = self.previous_database
        if self.previous_storage is None:
            os.environ.pop("FULUA_STORAGE_PATH", None)
        else:
            os.environ["FULUA_STORAGE_PATH"] = self.previous_storage
        self.temporary.cleanup()

    def _revision(self) -> int:
        return int(report_evidence.get_appendix_b(self.project_uuid)["project_revision"])

    def _create(
        self,
        category_code: str,
        subtype: str,
        *,
        title: str = "",
        starts_on: str | None = None,
        ends_on: str | None = None,
        organization_uuid: str | None = None,
        location: str = "",
        metadata: dict | None = None,
        member_uuids: list[str] | None = None,
        related_item_uuids: list[str] | None = None,
    ) -> dict:
        return report_evidence.create_item(
            self.project_uuid,
            category_code,
            EvidenceItemWrite(
                expected_project_revision=self._revision(),
                subtype=subtype,
                title=title,
                starts_on=starts_on,
                ends_on=ends_on,
                organization_uuid=organization_uuid,
                location=location,
                metadata=metadata or {},
                member_uuids=member_uuids or [],
                related_item_uuids=related_item_uuids or [],
            ),
        )

    def test_schema_eight_initializes_fixed_categories_only_for_full_report(self) -> None:
        appendix_project = database.create_project("仅附录 A")
        database.init_db()
        workspace = report_evidence.get_appendix_b(self.project_uuid)
        with database.connect() as db:
            self.assertEqual(int(db.execute("PRAGMA user_version").fetchone()[0]), 8)
            categories = db.execute(
                "SELECT category_code FROM report_evidence_categories WHERE project_id = ? ORDER BY id",
                (self.project["id"],),
            ).fetchall()
            appendix_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM report_evidence_categories WHERE project_id = ?",
                    (appendix_project["id"],),
                ).fetchone()[0]
            )
        self.assertEqual([row["category_code"] for row in categories], list(APPENDIX_B_CATEGORY_CODES))
        self.assertEqual(appendix_count, 0)
        self.assertEqual(workspace["members"][0]["team_role"], "member")
        self.assertIn("certificate_no", workspace["members"][0])
        self.assertNotIn("certificate_number", workspace["members"][0])

    def test_mutation_advances_project_revision_and_rejects_stale_revision(self) -> None:
        initial_revision = self._revision()
        self._create(
            "onsite_process",
            "visit",
            starts_on="2026-06-01",
            ends_on="2026-06-02",
            member_uuids=[self.member_uuids[0]],
        )
        self.assertEqual(self._revision(), initial_revision + 1)
        with self.assertRaises(ReportDomainError) as captured:
            report_evidence.update_category(
                self.project_uuid,
                "engagement_proof",
                EvidenceCategoryUpdate(
                    expected_project_revision=initial_revision,
                    expected_revision=1,
                    is_not_applicable=True,
                    not_applicable_reason="本项目不适用",
                ),
            )
        self.assertEqual(captured.exception.code, "PROJECT_REVISION_CONFLICT")

    def test_onsite_travel_dates_and_explicit_coverage_are_authoritative(self) -> None:
        onsite = self._create(
            "onsite_process",
            "visit",
            starts_on="2026-03-10",
            ends_on="2026-03-12",
            organization_uuid=self.vendor_organization_uuid,
            location="机房",
            member_uuids=self.member_uuids[:2],
        )
        with self.assertRaises(ReportDomainError) as uncovered:
            self._create(
                "travel_accommodation",
                "travel",
                starts_on="2026-03-11",
                ends_on="2026-03-12",
                metadata={"is_local": False},
                member_uuids=self.member_uuids[:2],
                related_item_uuids=[onsite["item_uuid"]],
            )
        self.assertEqual(uncovered.exception.code, "APPENDIX_B_TRAVEL_NOT_COVER_VISIT")
        travel = self._create(
            "travel_accommodation",
            "travel",
            starts_on="2026-03-09",
            ends_on="2026-03-13",
            metadata={"is_local": False},
            member_uuids=self.member_uuids[:2],
            related_item_uuids=[onsite["item_uuid"]],
        )
        self.assertIn(
            onsite["item_uuid"],
            {item["related_item_uuid"] for item in travel["usages"] if item["usage_kind"] == "covered_onsite"},
        )
        with database.connect() as db:
            phase = db.execute(
                "SELECT fieldwork_start, fieldwork_end, travel_records_json, site_visit_records_json FROM report_phase_dates WHERE project_id = ?",
                (self.project["id"],),
            ).fetchone()
        self.assertEqual((phase["fieldwork_start"], phase["fieldwork_end"]), ("2026-03-10", "2026-03-12"))
        self.assertIn(onsite["item_uuid"], phase["travel_records_json"])
        self.assertIn(onsite["item_uuid"], phase["site_visit_records_json"])

    def test_plan_report_review_and_personnel_roles_sync_to_r2(self) -> None:
        self._create(
            "onsite_process",
            "visit",
            starts_on="2026-04-10",
            ends_on="2026-04-11",
            member_uuids=[self.member_uuids[0]],
        )
        with self.assertRaises(ReportDomainError) as late_plan:
            self._create(
                "plan_review", "plan_review", starts_on="2026-04-10", metadata={"plan_name": "方案"}
            )
        self.assertEqual(late_plan.exception.code, "APPENDIX_B_PLAN_REVIEW_DATE_INVALID")
        self._create(
            "plan_review", "plan_review", starts_on="2026-04-09", metadata={"plan_name": "方案"}
        )
        with database.connect() as db:
            db.execute(
                "UPDATE report_phase_dates SET analysis_end = '2026-04-20', approved_at = '2026-04-22' WHERE project_id = ?",
                (self.project["id"],),
            )
        self._create("report_review", "report_review", starts_on="2026-04-21")
        self._create(
            "assessor_roster", "member", metadata={"role": "compiler"}, member_uuids=[self.member_uuids[0]]
        )
        self._create(
            "assessor_roster", "member", metadata={"role": "reviewer"}, member_uuids=[self.member_uuids[1]]
        )
        with self.assertRaises(ReportDomainError) as duplicate_person:
            self._create(
                "assessor_roster", "member", metadata={"role": "approver"}, member_uuids=[self.member_uuids[1]]
            )
        self.assertEqual(duplicate_person.exception.code, "APPENDIX_B_PERSONNEL_DUPLICATE")
        with database.connect() as db:
            phase = db.execute(
                "SELECT scheme_review_at, report_review_at FROM report_phase_dates WHERE project_id = ?",
                (self.project["id"],),
            ).fetchone()
            metadata = db.execute(
                "SELECT compiler_member_uuid, reviewer_member_uuid, approver_member_uuid FROM report_metadata WHERE project_id = ?",
                (self.project["id"],),
            ).fetchone()
        self.assertEqual((phase["scheme_review_at"], phase["report_review_at"]), ("2026-04-09", "2026-04-21"))
        self.assertEqual(metadata["compiler_member_uuid"], self.member_uuids[0])
        self.assertEqual(metadata["reviewer_member_uuid"], self.member_uuids[1])
        self.assertIsNone(metadata["approver_member_uuid"])

    def test_b9_branch_validation_and_unfiled_data_warning(self) -> None:
        with self.assertRaises(ReportDomainError) as invalid_filing:
            self._create(
                "grading_filing",
                "filing",
                metadata={"filing_system_same": False, "filing_system_name": "", "difference": ""},
            )
        self.assertEqual(invalid_filing.exception.code, "APPENDIX_B_METADATA_INVALID")
        self._create(
            "grading_filing",
            "filing",
            starts_on="2025-12-01",
            metadata={"filing_system_same": True, "filing_system_name": "保留值", "difference": ""},
        )
        result = report_evidence.get_appendix_b(self.project_uuid)
        codes = {issue["code"] for issue in result["warnings"]}
        self.assertIn("APPENDIX_B_UNFILED_DATA_PRESENT", codes)
        projection = report_evidence.build_projection(
            self.project_uuid, expected_project_revision=self._revision()
        )
        filing = projection["tables"]["B-9"]["records"][0]
        self.assertEqual(filing["effective_filing_system_name"], "分行特色系统")

    def test_category_irrelevant_relationship_fields_are_rejected(self) -> None:
        with self.assertRaises(ReportDomainError) as organization:
            self._create(
                "engagement_proof",
                "engagement",
                organization_uuid=self.vendor_organization_uuid,
            )
        self.assertEqual(
            organization.exception.code, "APPENDIX_B_ORGANIZATION_NOT_ALLOWED"
        )
        with self.assertRaises(ReportDomainError) as end_date:
            self._create(
                "plan_review",
                "plan_review",
                starts_on="2026-04-01",
                ends_on="2026-04-02",
                metadata={"plan_name": "方案"},
            )
        self.assertEqual(end_date.exception.code, "APPENDIX_B_END_DATE_NOT_ALLOWED")
        with self.assertRaises(ReportDomainError) as personnel_date:
            self._create(
                "assessor_roster",
                "member",
                starts_on="2026-04-01",
                member_uuids=[self.member_uuids[0]],
            )
        self.assertEqual(personnel_date.exception.code, "APPENDIX_B_DATE_NOT_ALLOWED")

    def test_png_upload_batch_rollback_tamper_detection_and_projection(self) -> None:
        record = self._create(
            "onsite_process",
            "visit",
            starts_on="2026-05-10",
            ends_on="2026-05-10",
            member_uuids=[self.member_uuids[0]],
        )
        with self.assertRaises(ReportDomainError) as invalid_batch:
            report_evidence.upload_images(
                record["item_uuid"],
                expected_project_revision=self._revision(),
                subtype="onsite_photo",
                caption="现场照片",
                alt_text="现场照片",
                files=[
                    _upload("valid.png", "image/png", _png_bytes()),
                    _upload("invalid.png", "image/png", b"not an image"),
                ],
            )
        self.assertEqual(invalid_batch.exception.code, "APPENDIX_B_IMAGE_SIGNATURE_INVALID")
        evidence_root = self.storage_path / "report_evidence" / self.project_uuid
        self.assertEqual(list(evidence_root.glob("*")) if evidence_root.exists() else [], [])
        images = report_evidence.upload_images(
            record["item_uuid"],
            expected_project_revision=self._revision(),
            subtype="onsite_photo",
            caption="现场照片",
            alt_text="现场人员在机房开展测评",
            files=[_upload("onsite.png", "image/png", _png_bytes())],
        )
        projection = report_evidence.build_projection(
            self.project_uuid, expected_project_revision=self._revision()
        )
        self.assertEqual(projection["status"], "current")
        self.assertEqual(len(projection["tables"]), 9)
        self.assertEqual(projection["tables"]["B-3"]["records"][0]["images"][0]["item_uuid"], images[0]["item_uuid"])
        managed_path = self.storage_path / images[0]["file_path"]
        managed_path.write_bytes(_png_bytes((160, 32, 32)))
        migration_valid, migration_reason, _projects, _images, mismatches = validate_database_and_evidence(
            self.database_path, self.storage_path
        )
        self.assertFalse(migration_valid)
        self.assertIn("哈希", migration_reason)
        self.assertEqual(mismatches, (images[0]["file_path"],))
        validated = report_evidence.validate_appendix_b(
            self.project_uuid, expected_project_revision=self._revision()
        )
        self.assertFalse(validated["valid"])
        self.assertIn("APPENDIX_B_FILE_HASH_MISMATCH", {issue["code"] for issue in validated["errors"]})

    def test_image_replace_delete_compensation_and_tombstone_recovery(self) -> None:
        record = self._create(
            "onsite_process",
            "visit",
            starts_on="2026-05-10",
            ends_on="2026-05-10",
            member_uuids=[self.member_uuids[0]],
        )
        image = report_evidence.upload_images(
            record["item_uuid"],
            expected_project_revision=self._revision(),
            subtype="onsite_photo",
            caption="现场照片",
            alt_text="现场照片",
            files=[_upload("original.png", "image/png", _png_bytes())],
        )[0]
        old_path = self.storage_path / image["file_path"]
        staged = stage_managed_file_removal(self.project_uuid, image["file_path"])
        self.assertIsNotNone(staged)
        self.assertFalse(old_path.exists())
        report_evidence.get_appendix_b(self.project_uuid)
        self.assertTrue(old_path.is_file())
        self.assertFalse(staged.tombstone.exists())

        with patch.object(
            report_evidence,
            "stage_managed_file_removal",
            side_effect=OSError("simulated stage failure"),
        ):
            with self.assertRaises(OSError):
                report_evidence.replace_image_file(
                    image["item_uuid"],
                    expected_project_revision=self._revision(),
                    expected_revision=image["revision"],
                    file=_upload("failed.png", "image/png", _png_bytes((12, 34, 56))),
                )
        self.assertTrue(old_path.is_file())
        evidence_root = old_path.parent
        self.assertEqual(
            [path.name for path in evidence_root.iterdir() if not path.name.startswith(".r5-delete-")],
            [old_path.name],
        )

        replaced = report_evidence.replace_image_file(
            image["item_uuid"],
            expected_project_revision=self._revision(),
            expected_revision=image["revision"],
            file=_upload("replacement.png", "image/png", _png_bytes((80, 100, 120))),
        )
        replacement_path = self.storage_path / replaced["file_path"]
        self.assertTrue(replacement_path.is_file())
        self.assertFalse(old_path.exists())
        with patch.object(
            report_evidence,
            "_mark_project_changed",
            side_effect=RuntimeError("simulated database rollback"),
        ):
            with self.assertRaises(RuntimeError):
                report_evidence.delete_item(
                    replaced["item_uuid"],
                    expected_project_revision=self._revision(),
                    expected_revision=replaced["revision"],
                )
        self.assertTrue(replacement_path.is_file())
        workspace = report_evidence.get_appendix_b(self.project_uuid)
        stored = next(
            item
            for category in workspace["categories"]
            for item in category["items"]
            if item["item_uuid"] == replaced["item_uuid"]
        )
        self.assertEqual(stored["file_path"], replaced["file_path"])
        with self.assertRaises(ReportDomainError) as traversal:
            resolve_managed_path(
                self.project_uuid,
                f"report_evidence/{self.project_uuid}/../outside.png",
                must_exist=False,
            )
        self.assertEqual(traversal.exception.code, "APPENDIX_B_FILE_PATH_INVALID")

    def test_evidence_references_block_member_and_organization_deletion(self) -> None:
        self._create(
            "onsite_process",
            "visit",
            starts_on="2026-06-01",
            ends_on="2026-06-02",
            organization_uuid=self.vendor_organization_uuid,
            member_uuids=[self.member_uuids[2]],
        )
        with self.assertRaises(ReportDomainError) as member_error:
            basic.delete_member(self.project_uuid, self.member_uuids[2], 1)
        with self.assertRaises(ReportDomainError) as organization_error:
            basic.delete_organization(self.project_uuid, self.vendor_organization_uuid, 1)
        self.assertIn(
            "report_evidence_usage",
            {item["entity_type"] for item in member_error.exception.details["references"]},
        )
        self.assertIn(
            "report_evidence_item",
            {item["entity_type"] for item in organization_error.exception.details["references"]},
        )

    def test_project_deletion_removes_managed_appendix_b_files(self) -> None:
        record = self._create(
            "onsite_process",
            "visit",
            starts_on="2026-06-01",
            ends_on="2026-06-01",
            member_uuids=[self.member_uuids[0]],
        )
        image = report_evidence.upload_images(
            record["item_uuid"],
            expected_project_revision=self._revision(),
            subtype="onsite_photo",
            caption="现场照片",
            alt_text="现场照片",
            files=[_upload("onsite.png", "image/png", _png_bytes())],
        )[0]
        evidence_root = (self.storage_path / image["file_path"]).parent
        self.assertTrue(evidence_root.is_dir())
        database.delete_project(self.project["id"])
        remove_project_runtime_files(self.project["id"], self.project_uuid)
        self.assertFalse(evidence_root.exists())

    def test_api_contract_exposes_all_r5_routes(self) -> None:
        routes = {(route.path, method) for route in app.routes for method in getattr(route, "methods", set())}
        expected = {
            ("/api/projects/{project_uuid}/report/appendix-b", "GET"),
            ("/api/projects/{project_uuid}/report/appendix-b/{category_code}", "PUT"),
            ("/api/projects/{project_uuid}/report/appendix-b/{category_code}/items", "POST"),
            ("/api/report-evidence-items/{item_uuid}", "PUT"),
            ("/api/report-evidence-items/{item_uuid}", "DELETE"),
            ("/api/report-evidence-items/{item_uuid}/images", "POST"),
            ("/api/report-evidence-items/{item_uuid}/file", "POST"),
            ("/api/projects/{project_uuid}/report/appendix-b/validations", "POST"),
        }
        self.assertEqual(expected - routes, set())


class R5MigrationTests(unittest.TestCase):
    def test_schema_seven_upgrades_idempotently_to_schema_eight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "app.db"
            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                project = database.create_project(
                    "旧版完整报告",
                    project_type="full_report",
                    template_package_id=FULL_REPORT_TEMPLATE_PACKAGE_ID,
                    template_edition=FULL_REPORT_TEMPLATE_EDITION,
                    template_revision=FULL_REPORT_TEMPLATE_REVISION,
                    template_asset_set_hash=FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
                )
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE report_evidence_usages")
                connection.execute("DROP TABLE report_evidence_items")
                connection.execute("DROP TABLE report_evidence_categories")
                connection.execute("PRAGMA user_version = 7")
                connection.commit()
            finally:
                connection.close()
            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                database.init_db()
                with database.connect() as db:
                    version = int(db.execute("PRAGMA user_version").fetchone()[0])
                    count = int(
                        db.execute(
                            "SELECT COUNT(*) FROM report_evidence_categories WHERE project_id = ?",
                            (project["id"],),
                        ).fetchone()[0]
                    )
            self.assertEqual(version, 8)
            self.assertEqual(count, 9)


class R5DocxProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.r4 = r4_tests.R4ReportExportTests(
            methodName="test_final_renderer_preserves_master_structure_and_corrected_display_contract"
        )
        self.r4.setUp()

    def tearDown(self) -> None:
        self.r4.tearDown()

    def test_populated_nine_table_projection_renders_images_and_personnel_table(self) -> None:
        project, _revision, context = self.r4._prepare_final_project()
        project_uuid = str(project["project_uuid"])
        with database.connect() as db:
            members = db.execute(
                "SELECT member_uuid FROM report_members WHERE project_id = ? ORDER BY sort_order",
                (project["id"],),
            ).fetchall()
        compiler_uuid = str(members[0]["member_uuid"])
        second_member_uuid = str(members[1]["member_uuid"])

        def current_revision() -> int:
            return int(report_evidence.get_appendix_b(project_uuid)["project_revision"])

        def create(category_code: str, subtype: str, **values) -> dict:
            return report_evidence.create_item(
                project_uuid,
                category_code,
                EvidenceItemWrite(
                    expected_project_revision=current_revision(),
                    subtype=subtype,
                    **values,
                ),
            )

        create(
            "engagement_proof",
            "engagement",
            title="密评委托合同",
            starts_on="2025-12-20",
            metadata={"file_type": "合同", "amount": "120000", "unit_price": "120000"},
        )
        onsite = create(
            "onsite_process",
            "visit",
            starts_on="2026-01-11",
            ends_on="2026-01-20",
            location="生产机房",
            member_uuids=[compiler_uuid, second_member_uuid],
        )
        create(
            "travel_accommodation",
            "travel",
            metadata={"is_local": True},
            member_uuids=[compiler_uuid, second_member_uuid],
            related_item_uuids=[onsite["item_uuid"]],
        )
        create("authorization_notice", "authorization", starts_on="2026-01-11")
        create("authorization_notice", "risk_notice", starts_on="2026-01-11")
        create(
            "plan_review",
            "plan_review",
            starts_on="2026-01-10",
            metadata={"plan_name": "分行特色系统测评方案"},
        )
        create("report_review", "report_review", starts_on="2026-02-01")
        create(
            "assessor_roster",
            "member",
            metadata={"role": "compiler"},
            member_uuids=[compiler_uuid],
        )
        create(
            "assessor_roster",
            "member",
            metadata={"role": "member"},
            member_uuids=[second_member_uuid],
        )
        create(
            "assessor_exam_proof",
            "exam_proof",
            member_uuids=[compiler_uuid],
        )
        create(
            "grading_filing",
            "filing",
            starts_on="2025-11-01",
            metadata={
                "filing_system_same": False,
                "filing_system_name": "备案名称",
                "difference": "与当前系统名称不同",
            },
        )
        report_evidence.upload_images(
            onsite["item_uuid"],
            expected_project_revision=current_revision(),
            subtype="onsite_photo",
            caption="现场测评照片",
            alt_text="测评人员在机房开展现场测评",
            files=[_upload("onsite.png", "image/png", _png_bytes())],
        )
        projection = report_evidence.build_projection(
            project_uuid, expected_project_revision=current_revision()
        )
        self.assertEqual(projection["status"], "current")
        context["appendix_b_projection"] = projection
        context["r5_projection_hash"] = projection["projection_hash"]
        destination = self.r4.storage / "exports" / "r5-nine-tables.docx"
        rendered = render_report(context, destination)
        self.assertEqual((rendered["section_count"], rendered["table_count"]), (17, 55))
        self.assertEqual(rendered["new_media_parts"], 1)
        with zipfile.ZipFile(destination) as package:
            root = etree.fromstring(package.read("word/document.xml"))
            tables = root.xpath("//w:tbl", namespaces=r4_tests.NS)
            appendix_b_text = [
                "".join(table.xpath(".//w:t/text()", namespaces=r4_tests.NS))
                for table in tables[46:55]
            ]
            document_xml = package.read("word/document.xml").decode("utf-8")
            media = [name for name in package.namelist() if name.startswith("word/media/r4_")]
        self.assertIn("120000", appendix_b_text[0])
        self.assertIn("生产机房", appendix_b_text[2])
        self.assertIn("分行特色系统测评方案", appendix_b_text[4])
        self.assertIn("组员、密评报告编制人", appendix_b_text[6])
        self.assertIn("与当前系统名称不同", appendix_b_text[8])
        self.assertIn("SEQ 图B-3-", document_xml)
        self.assertEqual(len(media), 1)

    def test_export_is_not_published_when_appendix_b_changes_during_word_refresh(self) -> None:
        project, revision, _context = self.r4._prepare_final_project()
        project_uuid = str(project["project_uuid"])
        workspace = report_evidence.get_appendix_b(project_uuid)
        category = next(
            item
            for item in workspace["categories"]
            if item["category_code"] == "engagement_proof"
        )
        job = report_exports.create_export_job(
            project_uuid,
            ReportExportJobWrite(
                mode="final",
                version="V1.0",
                expected_project_revision=revision,
            ),
        )

        def mutate_during_word(input_path: Path, output_path: Path, *, status_path: Path):
            shutil.copy2(input_path, output_path)
            report_evidence.update_category(
                project_uuid,
                "engagement_proof",
                EvidenceCategoryUpdate(
                    expected_project_revision=revision,
                    expected_revision=int(category["revision"]),
                    is_not_applicable=True,
                    not_applicable_reason="验收并发变更",
                ),
            )
            return {"status": "succeeded", "page_count": 69}

        with patch.object(
            report_exports,
            "refresh_with_word",
            side_effect=mutate_during_word,
        ):
            report_exports.process_export_job(job["job_uuid"])
        completed = report_exports.get_export_job(job["job_uuid"])
        self.assertEqual(completed["status"], "failed")
        self.assertEqual(completed["error_code"], "REVISION_CONFLICT")
        self.assertFalse(completed["download_available"])
