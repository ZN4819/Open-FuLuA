from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from lxml import etree
from PIL import Image
from starlette.datastructures import Headers

from app import database
from app.api.report_imports import (
    confirm_report_import,
    copy_report_appendix_a,
    upload_report_import,
    update_report_import_resolutions,
)
from app.contracts import (
    FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)
from app.report_import.schemas import (
    ReportAppendixACopyWrite,
    ReportImportConfirmWrite,
    ReportImportResolutionWrite,
    ReportImportResolutionsWrite,
)
from app.services.report_domain.validation import validate_report
from app.services import projects as project_service, report_exports
from app.services.template_profile import load_template_profile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_TEMPLATE = ROOT / "templates" / "report" / "2023-2025.12.08" / "runtime_template.docx"


def _upload(filename: str, data: bytes) -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(data),
        headers=Headers(
            {"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        ),
    )


def _completed_runtime_report(*, system_name: str | None = None) -> bytes:
    source = RUNTIME_TEMPLATE.read_bytes()
    with zipfile.ZipFile(io.BytesIO(source)) as package:
        document_xml = etree.fromstring(package.read("word/document.xml"))
    namespace = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    }
    word = f"{{{namespace['w']}}}"
    tables = document_xml.xpath("/w:document/w:body/w:tbl", namespaces=namespace)

    def row_cells(row) -> list:
        return row.xpath(
            "./w:tc | ./w:sdt/w:sdtContent/w:tc",
            namespaces=namespace,
        )

    def set_cell_text(cell, value: str) -> None:
        texts = cell.xpath(".//w:t", namespaces=namespace)
        if texts:
            texts[0].text = value
            for text in texts[1:]:
                text.text = ""
            return
        paragraph = cell.find(f"{word}p")
        if paragraph is None:
            paragraph = etree.SubElement(cell, f"{word}p")
        run = etree.SubElement(paragraph, f"{word}r")
        etree.SubElement(run, f"{word}t").text = value

    profile = {
        item["code"]: item for item in load_template_profile()["sections"]
    }
    for offset, code in enumerate(
        ("A-1", "A-2", "A-3", "A-4", "A-5", "A-6", "A-7", "A-8")
    ):
        table = tables[38 + offset]
        technical = code in {"A-1", "A-2", "A-3", "A-4"}
        table_rows = table.xpath("./w:tr", namespaces=namespace)
        rows = table_rows[2:] if technical else table_rows[1:]
        fixed_names = list(profile[code].get("fixed_object_names") or [])
        for index, row in enumerate(rows, start=1):
            cells = row_cells(row)
            if technical:
                set_cell_text(cells[1], f"测评对象-{code}-{index}")
                set_cell_text(cells[2], "已完成结构化测评记录。")
                for column in (3, 4, 5):
                    set_cell_text(cells[column], "√")
            else:
                set_cell_text(cells[1], fixed_names[(index - 1) % len(fixed_names)])
                set_cell_text(cells[2], "已完成结构化管理测评记录。")
                set_cell_text(cells[3], "符合")
    if system_name is not None:
        basic_rows = tables[1].xpath("./w:tr", namespaces=namespace)
        set_cell_text(row_cells(basic_rows[8])[1], system_name)

    updated_document = etree.tostring(
        document_xml,
        encoding="UTF-8",
        xml_declaration=True,
        standalone=True,
    )
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as package, zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as rewritten:
        for item in package.infolist():
            rewritten.writestr(
                item,
                updated_document
                if item.filename == "word/document.xml"
                else package.read(item),
            )
    return output.getvalue()


class R6ReportImportWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.previous = {
            name: os.environ.get(name)
            for name in ("FULUA_DATA_DIR", "FULUA_DATABASE_PATH", "FULUA_STORAGE_PATH")
        }
        os.environ["FULUA_DATA_DIR"] = str(self.root)
        os.environ["FULUA_DATABASE_PATH"] = str(self.root / "data" / "app.db")
        os.environ["FULUA_STORAGE_PATH"] = str(self.root / "storage")
        database.init_db()

    def tearDown(self) -> None:
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temporary.cleanup()

    @staticmethod
    def _full_report_args() -> dict[str, str]:
        return {
            "project_type": "full_report",
            "template_package_id": FULL_REPORT_TEMPLATE_PACKAGE_ID,
            "template_edition": FULL_REPORT_TEMPLATE_EDITION,
            "template_revision": FULL_REPORT_TEMPLATE_REVISION,
            "template_asset_set_hash": FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
        }

    def _create_complete_appendix_project(self, name: str = "附录来源"):
        from app.services.xlsx_generator.generator import _score_workbook_validation_context

        source = database.create_project(name)
        expected_units, _ = _score_workbook_validation_context()
        for code in ("A-1", "A-2", "A-3", "A-4"):
            database.replace_section_rows(
                int(source["id"]),
                code,
                [
                    {
                        "unit": unit,
                        "object_name": f"{code} 对象-{index}",
                        "record_text": "完整记录",
                        "metric_result": {
                            "d": "√",
                            "a": "×",
                            "k": "√",
                            "ra": "0.5",
                            "rk": "1.2",
                        },
                    }
                    for index, unit in enumerate(expected_units[code], start=1)
                ],
            )
        for code in ("A-5", "A-6", "A-7", "A-8"):
            database.replace_section_rows(
                int(source["id"]),
                code,
                [
                    {
                        "unit": unit,
                        "object_name": object_name,
                        "record_text": "完整记录",
                        "metric_result": {"compliance": "符合"},
                    }
                    for unit in expected_units[code]
                    for object_name in database.fixed_object_names_for_section(code)
                ],
            )
        return source

    def test_confirm_is_atomic_idempotent_and_marks_document_factors_defaulted(self) -> None:
        source = _completed_runtime_report()
        source_hash = hashlib.sha256(source).hexdigest()
        job = upload_report_import(_upload("supported.docx", source), "migration")

        confirmed = confirm_report_import(
            job.id,
            ReportImportConfirmWrite(
                job_revision=job.job_revision,
                project_name="一次性迁移",
                appendix_a_source="document",
            ),
        )
        repeated = confirm_report_import(
            job.id,
            ReportImportConfirmWrite(
                job_revision=job.job_revision,
                project_name="重复调用不得新建",
                appendix_a_source="document",
            ),
        )

        self.assertEqual(confirmed.status, "succeeded")
        self.assertEqual(repeated.created_project_uuid, confirmed.created_project_uuid)
        with database.connect() as db:
            project = database.get_project_by_uuid(str(confirmed.created_project_uuid), db)
            self.assertIsNotNone(project)
            self.assertEqual(project["project_type"], "full_report")
            self.assertEqual(project["workflow_status"], "draft")
            self.assertEqual(project["created_by_operation"], "migration_import")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1)
            stored_job = db.execute(
                "SELECT source_docx_path, source_sha256 FROM report_import_jobs WHERE id = ?",
                (job.id,),
            ).fetchone()
            source_copy = self.root / "storage" / Path(stored_job["source_docx_path"])
            self.assertEqual(stored_job["source_sha256"], source_hash)
            self.assertEqual(hashlib.sha256(source_copy.read_bytes()).hexdigest(), source_hash)
            defaulted_paths = {
                row["field_path"]
                for row in db.execute(
                    """
                    SELECT field_path FROM report_field_sources
                    WHERE project_id = ? AND source_kind = 'defaulted'
                    """,
                    (int(project["id"]),),
                )
            }
            self.assertEqual(defaulted_paths, {"metric_results[*].ra", "metric_results[*].rk"})
            adopted = db.execute(
                """
                SELECT association_id, authority_field_id FROM report_field_sources
                WHERE project_id = ? AND mapping_status = 'adopted'
                """,
                (int(project["id"]),),
            ).fetchall()
            self.assertTrue(adopted)
            self.assertTrue(all(row["association_id"] and row["authority_field_id"] for row in adopted))
            factors = db.execute(
                """
                SELECT DISTINCT m.ra, m.rk
                FROM metric_results m
                JOIN assessment_rows r ON r.id = m.row_id
                JOIN appendix_sections s ON s.id = r.section_id
                WHERE s.project_id = ? AND s.code IN ('A-1','A-2','A-3','A-4')
                """,
                (int(project["id"]),),
            ).fetchall()
            self.assertTrue(factors)
            self.assertEqual({(row["ra"], row["rk"]) for row in factors}, {("1", "1")})

        validation = validate_report(str(confirmed.created_project_uuid))
        self.assertTrue(
            any(issue["code"] == "MIGRATION_REVIEW_PENDING" for issue in validation["issues"])
        )
        final_validation = report_exports.validate_project_export(
            str(confirmed.created_project_uuid), mode="final"
        )
        self.assertFalse(final_validation["valid"])
        self.assertTrue(
            any(issue["code"] == "MIGRATION_REVIEW_PENDING" for issue in final_validation["issues"])
        )

    def test_stable_system_name_is_imported_as_r2_fact(self) -> None:
        source_path = self.root / "system-name.docx"
        source_path.write_bytes(_completed_runtime_report(system_name="迁移系统"))

        job = upload_report_import(_upload(source_path.name, source_path.read_bytes()), "migration")
        self.assertTrue(job.fingerprint.matched)
        confirmed = confirm_report_import(
            job.id,
            ReportImportConfirmWrite(
                job_revision=job.job_revision,
                project_name="事实字段迁移",
                appendix_a_source="document",
            ),
        )

        with database.connect() as db:
            project = database.get_project_by_uuid(str(confirmed.created_project_uuid), db)
            profile = db.execute(
                "SELECT system_name FROM system_profiles WHERE project_id = ?",
                (int(project["id"]),),
            ).fetchone()
            self.assertEqual(profile["system_name"], "迁移系统")
            source = db.execute(
                """
                SELECT association_id, authority_field_id, mapping_status
                FROM report_field_sources
                WHERE project_id = ? AND authority_field_id = 'report.system.name'
                  AND mapping_status = 'adopted'
                """,
                (int(project["id"]),),
            ).fetchone()
            self.assertIsNotNone(source)
            self.assertTrue(str(source["association_id"]).startswith("FRM-"))

    def test_unmapped_content_cannot_be_adopted_and_keep_original_still_blocks_final(self) -> None:
        job = upload_report_import(
            _upload("supported.docx", _completed_runtime_report()), "migration"
        )
        issue = next(
            item
            for item in job.issues
            if item.needs_confirmation
            and item.confidence == "unmapped"
            and not item.blocks_confirmation
        )
        with self.assertRaises(HTTPException) as captured:
            update_report_import_resolutions(
                job.id,
                ReportImportResolutionsWrite(
                    job_revision=job.job_revision,
                    resolutions=[
                        ReportImportResolutionWrite(
                            issue_id=issue.id,
                            revision=issue.revision,
                            action="adopt_candidate",
                            resolved_value="不得越权写入",
                        )
                    ],
                ),
            )
        self.assertEqual(captured.exception.status_code, 409)
        self.assertEqual(captured.exception.detail["code"], "REPORT_IMPORT_MAPPING_INVALID")

        updated = update_report_import_resolutions(
            job.id,
            ReportImportResolutionsWrite(
                job_revision=job.job_revision,
                resolutions=[
                    ReportImportResolutionWrite(
                        issue_id=issue.id,
                        revision=issue.revision,
                        action="keep_original",
                    )
                ],
            ),
        )
        confirmed = confirm_report_import(
            job.id,
            ReportImportConfirmWrite(
                job_revision=updated.job_revision,
                project_name="保留原文迁移",
                appendix_a_source="document",
            ),
        )
        validation = validate_report(str(confirmed.created_project_uuid))
        matching = [
            item
            for item in validation["issues"]
            if item["code"] == "MIGRATION_REVIEW_PENDING"
            and item["details"].get("issue_id") == issue.id
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["details"]["resolution_action"], "keep_original")

        migrated_issue = next(item for item in confirmed.issues if item.id == issue.id)
        reviewed = update_report_import_resolutions(
            job.id,
            ReportImportResolutionsWrite(
                job_revision=confirmed.job_revision,
                expected_project_updated_at=confirmed.created_project_updated_at,
                resolutions=[
                    ReportImportResolutionWrite(
                        issue_id=migrated_issue.id,
                        revision=migrated_issue.revision,
                        action="skip",
                    )
                ],
            ),
        )
        self.assertEqual(reviewed.status, "succeeded")
        validation = validate_report(str(confirmed.created_project_uuid))
        self.assertFalse(
            any(
                item["code"] == "MIGRATION_REVIEW_PENDING"
                and item["details"].get("issue_id") == issue.id
                for item in validation["issues"]
            )
        )

    def test_resolution_rejects_array_candidate_for_scalar_fact(self) -> None:
        job = upload_report_import(
            _upload("supported.docx", _completed_runtime_report()), "migration"
        )
        issue = next(
            item
            for item in job.issues
            if item.code == "REPEATED_SLOT_VALUE_CONFLICT"
            and not item.blocks_confirmation
        )
        self.assertIsInstance(issue.candidate_value, list)
        with self.assertRaises(HTTPException) as captured:
            update_report_import_resolutions(
                job.id,
                ReportImportResolutionsWrite(
                    job_revision=job.job_revision,
                    resolutions=[
                        ReportImportResolutionWrite(
                            issue_id=issue.id,
                            revision=issue.revision,
                            action="adopt_candidate",
                        )
                    ],
                ),
            )
        self.assertEqual(captured.exception.status_code, 422)
        self.assertEqual(
            captured.exception.detail["code"],
            "REPORT_IMPORT_RESOLUTION_VALUE_INVALID",
        )
        preview = update_report_import_resolutions(
            job.id,
            ReportImportResolutionsWrite(
                job_revision=job.job_revision,
                resolutions=[
                    ReportImportResolutionWrite(
                        issue_id=issue.id,
                        revision=issue.revision,
                        action="keep_original",
                    )
                ],
            ),
        )
        confirmed = confirm_report_import(
            job.id,
            ReportImportConfirmWrite(
                job_revision=preview.job_revision,
                project_name="冲突值后续审阅",
                appendix_a_source="document",
            ),
        )
        migrated_issue = next(item for item in confirmed.issues if item.id == issue.id)
        reviewed = update_report_import_resolutions(
            job.id,
            ReportImportResolutionsWrite(
                job_revision=confirmed.job_revision,
                expected_project_updated_at=confirmed.created_project_updated_at,
                resolutions=[
                    ReportImportResolutionWrite(
                        issue_id=migrated_issue.id,
                        revision=migrated_issue.revision,
                        action="adopt_candidate",
                        resolved_value="审阅后的系统名称",
                    )
                ],
            ),
        )
        self.assertEqual(reviewed.status, "succeeded")
        self.assertNotEqual(
            reviewed.created_project_updated_at,
            confirmed.created_project_updated_at,
        )
        applied_resolution = next(
            item for item in reviewed.resolutions if item.issue_id == migrated_issue.id
        )
        self.assertTrue(applied_resolution.applied)
        with database.connect() as db:
            project = database.get_project_by_uuid(str(confirmed.created_project_uuid), db)
            profile = db.execute(
                "SELECT system_name FROM system_profiles WHERE project_id = ?",
                (int(project["id"]),),
            ).fetchone()
            source = db.execute(
                """
                SELECT mapping_status, needs_confirmation
                FROM report_field_sources
                WHERE project_id = ? AND report_import_job_id = ?
                  AND source_locator = ?
                """,
                (int(project["id"]), job.id, issue.source_locator),
            ).fetchone()
        self.assertEqual(profile["system_name"], "审阅后的系统名称")
        self.assertEqual(source["mapping_status"], "adopted")
        self.assertEqual(source["needs_confirmation"], 0)
        validation = validate_report(str(confirmed.created_project_uuid))
        self.assertFalse(
            any(
                item["code"] == "MIGRATION_REVIEW_PENDING"
                and item["details"].get("issue_id") == issue.id
                for item in validation["issues"]
            )
        )

    def test_post_create_review_rejects_missing_or_stale_project_revision(self) -> None:
        job = upload_report_import(
            _upload("supported.docx", _completed_runtime_report()), "migration"
        )
        issue = next(
            item
            for item in job.issues
            if item.code == "REPEATED_SLOT_VALUE_CONFLICT"
            and not item.blocks_confirmation
        )
        preview = update_report_import_resolutions(
            job.id,
            ReportImportResolutionsWrite(
                job_revision=job.job_revision,
                resolutions=[
                    ReportImportResolutionWrite(
                        issue_id=issue.id,
                        revision=issue.revision,
                        action="keep_original",
                    )
                ],
            ),
        )
        confirmed = confirm_report_import(
            job.id,
            ReportImportConfirmWrite(
                job_revision=preview.job_revision,
                project_name="并发审阅保护",
                appendix_a_source="document",
            ),
        )
        migrated_issue = next(item for item in confirmed.issues if item.id == issue.id)
        request = ReportImportResolutionWrite(
            issue_id=migrated_issue.id,
            revision=migrated_issue.revision,
            action="adopt_candidate",
            resolved_value="迁移审阅值",
        )

        with self.assertRaises(HTTPException) as captured:
            update_report_import_resolutions(
                job.id,
                ReportImportResolutionsWrite(
                    job_revision=confirmed.job_revision,
                    resolutions=[request],
                ),
            )
        self.assertEqual(captured.exception.status_code, 422)
        self.assertEqual(
            captured.exception.detail["code"],
            "REPORT_IMPORT_PROJECT_REVISION_REQUIRED",
        )

        with database.connect() as db:
            project = database.get_project_by_uuid(str(confirmed.created_project_uuid), db)
            db.execute(
                "UPDATE system_profiles SET system_name = '其他页面最新值' WHERE project_id = ?",
                (int(project["id"]),),
            )
            db.execute(
                "UPDATE projects SET updated_at = '2099-01-01T00:00:00+00:00' WHERE id = ?",
                (int(project["id"]),),
            )

        with self.assertRaises(HTTPException) as captured:
            update_report_import_resolutions(
                job.id,
                ReportImportResolutionsWrite(
                    job_revision=confirmed.job_revision,
                    expected_project_updated_at=confirmed.created_project_updated_at,
                    resolutions=[request],
                ),
            )
        self.assertEqual(captured.exception.status_code, 409)
        self.assertEqual(
            captured.exception.detail["code"],
            "REPORT_IMPORT_PROJECT_REVISION_CONFLICT",
        )
        with database.connect() as db:
            project = database.get_project_by_uuid(str(confirmed.created_project_uuid), db)
            profile = db.execute(
                "SELECT system_name FROM system_profiles WHERE project_id = ?",
                (int(project["id"]),),
            ).fetchone()
        self.assertEqual(profile["system_name"], "其他页面最新值")

    def test_confirm_requires_every_adopted_resolution_and_marks_it_applied(self) -> None:
        job = upload_report_import(
            _upload("supported.docx", _completed_runtime_report()), "migration"
        )
        issue = next(
            item
            for item in job.issues
            if item.code == "REPEATED_SLOT_VALUE_CONFLICT"
            and not item.blocks_confirmation
        )
        preview = update_report_import_resolutions(
            job.id,
            ReportImportResolutionsWrite(
                job_revision=job.job_revision,
                resolutions=[
                    ReportImportResolutionWrite(
                        issue_id=issue.id,
                        revision=issue.revision,
                        action="adopt_candidate",
                        resolved_value="明确采用的系统名称",
                    )
                ],
            ),
        )
        resolution = next(item for item in preview.resolutions if item.issue_id == issue.id)
        self.assertFalse(resolution.applied)
        with self.assertRaises(HTTPException) as captured:
            confirm_report_import(
                job.id,
                ReportImportConfirmWrite(
                    job_revision=preview.job_revision,
                    project_name="缺少采用确认",
                    appendix_a_source="document",
                ),
            )
        self.assertEqual(captured.exception.status_code, 422)
        self.assertEqual(
            captured.exception.detail["code"],
            "REPORT_IMPORT_RESOLUTION_SELECTION_INCOMPLETE",
        )

        confirmed = confirm_report_import(
            job.id,
            ReportImportConfirmWrite(
                job_revision=preview.job_revision,
                project_name="完整采用确认",
                appendix_a_source="document",
                accepted_resolutions=[resolution.id],
            ),
        )
        applied_resolution = next(
            item for item in confirmed.resolutions if item.issue_id == issue.id
        )
        self.assertTrue(applied_resolution.applied)

    def test_existing_appendix_selection_resolves_unselected_document_appendix_issues(self) -> None:
        source = self._create_complete_appendix_project("空白文档的替代附录")
        job = upload_report_import(
            _upload("blank-appendix.docx", RUNTIME_TEMPLATE.read_bytes()),
            "migration",
        )
        document_issue_ids = {
            item.id
            for item in job.issues
            if item.code.startswith("APPENDIX_A_")
            or item.code == "DOCUMENT_APPENDIX_IMAGES_REQUIRE_REVIEW"
        }
        self.assertTrue(document_issue_ids)
        self.assertFalse(job.summary["document_appendix"]["available"])

        confirmed = confirm_report_import(
            job.id,
            ReportImportConfirmWrite(
                job_revision=job.job_revision,
                project_name="使用已有附录迁移",
                appendix_a_source="existing_project",
                appendix_a_project_uuid=uuid.UUID(str(source["project_uuid"])),
            ),
        )
        resolutions = {
            item.issue_id: item.action
            for item in confirmed.resolutions
            if item.issue_id in document_issue_ids
        }
        self.assertEqual(resolutions, {issue_id: "skip" for issue_id in document_issue_ids})
        self.assertTrue(
            all(
                item.status == "ignored"
                for item in confirmed.issues
                if item.id in document_issue_ids
            )
        )
        validation = validate_report(str(confirmed.created_project_uuid))
        self.assertFalse(
            any(
                item["code"] == "MIGRATION_REVIEW_PENDING"
                and item["details"].get("issue_id") in document_issue_ids
                for item in validation["issues"]
            )
        )

    def test_existing_appendix_copy_is_idempotent_remaps_images_and_recalculates_scores(self) -> None:
        source = self._create_complete_appendix_project()

        image_dir = self.root / "storage" / "uploads" / str(source["id"]) / "A-1"
        image_dir.mkdir(parents=True)
        image_path = image_dir / "evidence.png"
        Image.new("RGB", (8, 8), color=(12, 34, 56)).save(image_path)
        timestamp = database.utc_now()
        source_image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
        with database.connect() as db:
            image = db.execute(
                """
                INSERT INTO evidence_images (
                    project_id, section_code, file_path, original_name, caption, alt_text,
                    sort_order, pixel_width, pixel_height, created_at, updated_at
                ) VALUES (?, 'A-1', ?, 'evidence.png', '证据', '证据', 1, 8, 8, ?, ?)
                """,
                (
                    int(source["id"]),
                    image_path.relative_to(self.root / "storage").as_posix(),
                    timestamp,
                    timestamp,
                ),
            )
            row = db.execute(
                """
                SELECT r.id FROM assessment_rows r
                JOIN appendix_sections s ON s.id = r.section_id
                WHERE s.project_id = ? AND s.code = 'A-1'
                """,
                (int(source["id"]),),
            ).fetchone()
            token = f"[[FIG:{int(image.lastrowid)}]]"
            db.execute("UPDATE assessment_rows SET record_text = ? WHERE id = ?", (f"完整记录 {token}", int(row["id"])))
            db.execute(
                "INSERT INTO cross_references (source_row_id, target_image_id, token, display_text) VALUES (?, ?, ?, '证据')",
                (int(row["id"]), int(image.lastrowid), token),
            )
            db.execute(
                "UPDATE metric_results SET object_score = '9.9999', unit_score = '9.9999' WHERE row_id = ?",
                (int(row["id"]),),
            )
            db.execute(
                "UPDATE assessment_rows SET record_text = record_text || ' [[FIG:999999]]' WHERE id = ?",
                (int(row["id"]),),
            )
        incomplete_target = database.create_project("孤立引用目标", **self._full_report_args())
        with self.assertRaises(HTTPException) as captured:
            copy_report_appendix_a(
                str(incomplete_target["project_uuid"]),
                ReportAppendixACopyWrite(
                    source_project_uuid=uuid.UUID(str(source["project_uuid"])),
                    idempotency_key=uuid.uuid4(),
                ),
            )
        self.assertEqual(captured.exception.detail["code"], "APPENDIX_A_SOURCE_INCOMPLETE")
        with database.connect() as db:
            db.execute(
                "UPDATE assessment_rows SET record_text = REPLACE(record_text, ' [[FIG:999999]]', '') WHERE id = ?",
                (int(row["id"]),),
            )
        with database.connect() as db:
            source_updated_at = database.get_project_by_id(int(source["id"]), db)["updated_at"]

        target = database.create_project("完整报告目标", **self._full_report_args())
        operation_key = uuid.uuid4()
        payload = ReportAppendixACopyWrite(
            source_project_uuid=uuid.UUID(str(source["project_uuid"])),
            idempotency_key=operation_key,
        )
        staged_path: Path | None = None

        def fail_after_partial_copy(images, staging_dir):
            nonlocal staged_path
            staged_path = staging_dir
            staging_dir.mkdir(parents=True, exist_ok=False)
            (staging_dir / "partial.png").write_bytes(b"partial")
            raise project_service.ProjectServiceError(
                "UPGRADE_FILE_HASH_MISMATCH",
                "复制项目图片时完整性校验失败。",
            )

        with patch(
            "app.services.report_imports.project_service._stage_evidence_files",
            side_effect=fail_after_partial_copy,
        ):
            with self.assertRaises(HTTPException) as captured:
                copy_report_appendix_a(str(target["project_uuid"]), payload)
        self.assertEqual(captured.exception.detail["code"], "UPGRADE_FILE_HASH_MISMATCH")
        self.assertIsNotNone(staged_path)
        self.assertFalse(staged_path.exists())

        first = copy_report_appendix_a(str(target["project_uuid"]), payload)
        repeated = copy_report_appendix_a(str(target["project_uuid"]), payload)

        self.assertFalse(first.repeated)
        self.assertTrue(repeated.repeated)
        self.assertEqual(first.copied_row_count, repeated.copied_row_count)
        self.assertEqual(first.copied_image_count, 1)
        with database.connect() as db:
            copied = db.execute(
                """
                SELECT r.record_text, m.object_score, m.unit_score
                FROM assessment_rows r
                JOIN appendix_sections s ON s.id = r.section_id
                JOIN metric_results m ON m.row_id = r.id
                WHERE s.project_id = ? AND s.code = 'A-1'
                """,
                (int(target["id"]),),
            ).fetchone()
            copied_image = db.execute(
                "SELECT id, file_path FROM evidence_images WHERE project_id = ?",
                (int(target["id"]),),
            ).fetchone()
            copied_reference = db.execute(
                """
                SELECT c.target_image_id, c.token FROM cross_references c
                JOIN assessment_rows r ON r.id = c.source_row_id
                JOIN appendix_sections s ON s.id = r.section_id
                WHERE s.project_id = ?
                """,
                (int(target["id"]),),
            ).fetchone()
            refreshed_source = database.get_project_by_id(int(source["id"]), db)
            self.assertEqual(refreshed_source["updated_at"], source_updated_at)
        self.assertEqual(copied["object_score"], "0.2500")
        self.assertEqual(copied["unit_score"], "0.2500")
        self.assertEqual(copied_reference["target_image_id"], copied_image["id"])
        self.assertEqual(copied_reference["token"], f"[[FIG:{copied_image['id']}]]")
        self.assertIn(copied_reference["token"], copied["record_text"])
        target_image_path = self.root / "storage" / Path(copied_image["file_path"])
        self.assertEqual(hashlib.sha256(target_image_path.read_bytes()).hexdigest(), source_image_hash)
        self.assertEqual(hashlib.sha256(image_path.read_bytes()).hexdigest(), source_image_hash)

        with self.assertRaises(HTTPException) as captured:
            copy_report_appendix_a(
                str(target["project_uuid"]),
                ReportAppendixACopyWrite(
                    source_project_uuid=uuid.UUID(str(source["project_uuid"])),
                    idempotency_key=uuid.uuid4(),
                ),
            )
        self.assertEqual(captured.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
