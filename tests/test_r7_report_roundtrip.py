from __future__ import annotations

import concurrent.futures
import io
import json
import shutil
import sqlite3
import threading
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi import Response, UploadFile
from lxml import etree

from app import database
from app.api.projects import delete_project as delete_project_api
from app.config import settings
from app.main import app
from app.report_roundtrip.schemas import (
    ReportRoundtripCommitWrite,
    ReportRoundtripResolutionItem,
    ReportRoundtripResolutionWrite,
)
from app.report_roundtrip.contracts import roundtrip_policy
from app.report_schemas import ReportExportJobWrite
from app.services import report_exports
from app.services import report_roundtrips as roundtrip_service
from app.services.report_domain.common import touch_project
from app.services.report_domain.errors import ReportDomainError
from app.services.projects import (
    cleanup_deleted_project_files,
    recover_pending_project_cleanup_tasks,
)
from app.services.report_roundtrips import (
    _validate_structure,
    commit_roundtrip_job,
    create_roundtrip_job,
    get_roundtrip_diff,
    get_roundtrip_job,
    recover_abandoned_roundtrip_jobs,
    resolve_roundtrip_conflicts,
)
from tests import test_r4_report_export as r4_tests


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


class R7ReportRoundtripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.r4 = r4_tests.R4ReportExportTests(
            methodName="test_current_schema_keeps_immutable_export_storage"
        )
        self.r4.setUp()

    def tearDown(self) -> None:
        self.r4.tearDown()

    def _prepare(self):
        project, revision, _context = self.r4._prepare_final_project()
        # R4's compact fixture writes rows directly and intentionally bypasses
        # the normal R2 object-name synchronization.  Align it before signing
        # the R7 three-way baseline.
        with database.connect() as db:
            db.execute(
                """
                UPDATE assessment_objects
                SET name_snapshot = (
                    SELECT r.object_name FROM assessment_rows r
                    JOIN appendix_sections s ON s.id = r.section_id
                    WHERE s.project_id = assessment_objects.project_id
                      AND r.assessment_object_uuid = assessment_objects.object_uuid
                    ORDER BY r.sort_order, r.id LIMIT 1
                )
                WHERE project_id = ? AND EXISTS (
                    SELECT 1 FROM assessment_rows r
                    JOIN appendix_sections s ON s.id = r.section_id
                    WHERE s.project_id = assessment_objects.project_id
                      AND r.assessment_object_uuid = assessment_objects.object_uuid
                )
                """,
                (project["id"],),
            )
        return project, revision

    def _export(self, project, revision: int) -> tuple[Path, dict]:
        job = report_exports.create_export_job(
            project["project_uuid"],
            ReportExportJobWrite(
                mode="draft",
                version="V1.0",
                expected_project_revision=revision,
                roundtrip_capable=True,
            ),
        )

        def word_passthrough(source, target, **_kwargs):
            shutil.copy2(source, target)
            return {"page_count": 1}

        with patch.object(
            report_exports, "refresh_with_word", side_effect=word_passthrough
        ):
            report_exports.process_export_job(job["job_uuid"])
        completed = report_exports.get_export_job(job["job_uuid"])
        self.assertEqual(completed["status"], "succeeded", completed)
        with database.connect() as db:
            baseline = json.loads(
                db.execute(
                    "SELECT baseline_json FROM report_roundtrip_manifests WHERE export_job_uuid=?",
                    (job["job_uuid"],),
                ).fetchone()["baseline_json"]
            )
        return report_exports.export_docx_path(job["job_uuid"]), baseline

    def _edit_authority(
        self,
        source: Path,
        baseline: dict,
        authority_field_id: str,
        value: str,
        *,
        filename: str = "word-edited.docx",
    ) -> Path:
        slot_ids = {
            str(slot["slot_id"])
            for slot in baseline["slots"]
            if slot["authority_field_id"] == authority_field_id
        }
        self.assertTrue(slot_ids)
        with zipfile.ZipFile(source) as package:
            parts = {name: package.read(name) for name in package.namelist()}
        root = etree.fromstring(parts["word/document.xml"])
        changed = 0
        for control in root.xpath("//w:sdt", namespaces=NS):
            tag = control.xpath("string(w:sdtPr/w:tag/@w:val)", namespaces=NS)
            if not any(str(tag).endswith(slot_id) for slot_id in slot_ids):
                continue
            texts = control.xpath(".//w:sdtContent//w:t", namespaces=NS)
            self.assertTrue(texts)
            texts[0].text = value
            for text in texts[1:]:
                text.text = ""
            changed += 1
        self.assertEqual(changed, len(slot_ids))
        parts["word/document.xml"] = etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", standalone="yes"
        )
        target = Path(self.r4.r3.temporary.name) / filename
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as package:
            for name, data in parts.items():
                package.writestr(name, data)
        return target

    @staticmethod
    def _upload(project_uuid: str, path: Path) -> dict:
        return create_roundtrip_job(
            project_uuid,
            UploadFile(file=io.BytesIO(path.read_bytes()), filename=path.name),
        )

    def test_export_diff_and_atomic_commit_preserve_private_scores(self) -> None:
        project, revision = self._prepare()
        source, baseline = self._export(project, revision)
        self.assertFalse(
            any(
                slot.get("column_id") in {"ra", "rk", "object_score", "unit_score"}
                for slot in baseline["slots"]
            )
        )
        edited = self._edit_authority(
            source, baseline, "report.system.name", "Word 修改后的系统"
        )
        with database.connect() as db:
            factors_before = db.execute(
                "SELECT row_id, ra, rk FROM metric_results ORDER BY row_id"
            ).fetchall()
        job = self._upload(project["project_uuid"], edited)
        self.assertEqual(job["status"], "ready_to_commit", job)
        self.assertTrue(job["resolution_hash"])
        diff = get_roundtrip_diff(job["id"])
        changed = [item for item in diff["items"] if item["disposition"] == "apply_word"]
        self.assertEqual([(item["field_label"], item["word_value"]) for item in changed], [("系统名称", "Word 修改后的系统")])

        result = commit_roundtrip_job(
            job["id"],
            ReportRoundtripCommitWrite(
                resolution_hash=job["resolution_hash"],
                expected_project_revision=job["observed_project_revision"],
            ),
        )
        self.assertEqual(result["after_revision"], job["observed_project_revision"] + 1)
        self.assertEqual(result["applied_fields"], 1)
        repeated = commit_roundtrip_job(
            job["id"],
            ReportRoundtripCommitWrite(
                resolution_hash=job["resolution_hash"],
                expected_project_revision=job["observed_project_revision"],
            ),
        )
        self.assertEqual(repeated, result)
        with database.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT system_name FROM system_profiles WHERE project_id=?",
                    (project["id"],),
                ).fetchone()["system_name"],
                "Word 修改后的系统",
            )
            factors_after = db.execute(
                "SELECT row_id, ra, rk FROM metric_results ORDER BY row_id"
            ).fetchall()
            self.assertEqual([tuple(row) for row in factors_after], [tuple(row) for row in factors_before])
            self.assertEqual(
                int(db.execute("SELECT COUNT(*) FROM report_import_audits WHERE job_id=?", (job["id"],)).fetchone()[0]),
                1,
            )
            stored_path = db.execute(
                "SELECT source_docx_path FROM report_import_jobs WHERE id=?", (job["id"],)
            ).fetchone()["source_docx_path"]
        self.assertTrue(str(stored_path).startswith("private/roundtrip/imports/"))
        self.assertNotIn("storage", str(stored_path).casefold())

    def test_three_way_conflict_requires_complete_resolution(self) -> None:
        project, revision = self._prepare()
        source, baseline = self._export(project, revision)
        edited = self._edit_authority(
            source, baseline, "report.system.name", "Word 冲突值", filename="conflict.docx"
        )
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE system_profiles SET system_name='数据库冲突值',revision=revision+1 WHERE project_id=?",
                (project["id"],),
            )
            touch_project(db, int(project["id"]))
        job = self._upload(project["project_uuid"], edited)
        self.assertEqual(job["status"], "conflicts_pending", job)
        diff = get_roundtrip_diff(job["id"])
        conflicts = [item for item in diff["items"] if item["disposition"] == "conflict"]
        self.assertEqual(len(conflicts), 1)
        conflict_id = str(conflicts[0]["conflict_id"])
        saved = resolve_roundtrip_conflicts(
            job["id"],
            ReportRoundtripResolutionWrite(
                diff_hash=diff["diff_hash"],
                expected_project_revision=job["observed_project_revision"],
                resolutions=[
                    ReportRoundtripResolutionItem(
                        conflict_id=conflict_id,
                        action="apply_word",
                    )
                ],
            ),
        )
        result = commit_roundtrip_job(
            job["id"],
            ReportRoundtripCommitWrite(
                resolution_hash=saved["resolution_hash"],
                expected_project_revision=job["observed_project_revision"],
            ),
        )
        self.assertEqual(result["status"], "succeeded")
        with database.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT system_name FROM system_profiles WHERE project_id=?",
                    (project["id"],),
                ).fetchone()["system_name"],
                "Word 冲突值",
            )

    def test_failed_regeneration_rolls_back_word_fact(self) -> None:
        project, revision = self._prepare()
        source, baseline = self._export(project, revision)
        edited = self._edit_authority(
            source, baseline, "report.system.name", "不应提交", filename="rollback.docx"
        )
        job = self._upload(project["project_uuid"], edited)
        with patch(
            "app.services.report_roundtrips.regenerate_after_roundtrip_locked",
            side_effect=ReportDomainError(
                "INJECTED_REGENERATION_FAILURE", "注入重算失败。", status_code=422
            ),
        ):
            with self.assertRaises(ReportDomainError):
                commit_roundtrip_job(
                    job["id"],
                    ReportRoundtripCommitWrite(
                        resolution_hash=job["resolution_hash"],
                        expected_project_revision=job["observed_project_revision"],
                    ),
                )
        with database.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT system_name FROM system_profiles WHERE project_id=?",
                    (project["id"],),
                ).fetchone()["system_name"],
                "分行特色系统",
            )
            self.assertEqual(
                int(db.execute("SELECT COUNT(*) FROM report_import_audits WHERE job_id=?", (job["id"],)).fetchone()[0]),
                0,
            )
        self.assertEqual(get_roundtrip_job(job["id"])["status"], "failed")

    def test_stale_diff_is_persisted_and_wrong_commit_hash_does_not_destroy_job(self) -> None:
        project, revision = self._prepare()
        source, _baseline = self._export(project, revision)

        ready = self._upload(project["project_uuid"], source)
        self.assertEqual(ready["status"], "ready_to_commit")
        with self.assertRaises(ReportDomainError) as wrong_hash:
            commit_roundtrip_job(
                ready["id"],
                ReportRoundtripCommitWrite(
                    resolution_hash="0" * 64,
                    expected_project_revision=ready["observed_project_revision"],
                ),
            )
        self.assertEqual(wrong_hash.exception.code, "ROUNDTRIP_RESOLUTION_HASH_MISMATCH")
        self.assertEqual(get_roundtrip_job(ready["id"])["status"], "ready_to_commit")

        stale = self._upload(project["project_uuid"], source)
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            touch_project(db, int(project["id"]))
        with self.assertRaises(ReportDomainError) as stale_error:
            get_roundtrip_diff(stale["id"])
        self.assertEqual(stale_error.exception.code, "ROUNDTRIP_PROJECT_REVISION_STALE")
        self.assertEqual(get_roundtrip_job(stale["id"])["status"], "stale")

    def test_current_matrix_can_revoke_an_issued_writable_slot(self) -> None:
        project, revision = self._prepare()
        source, _baseline = self._export(project, revision)
        narrowed = deepcopy(roundtrip_policy())
        narrowed["scalar_slots"] = [
            item
            for item in narrowed["scalar_slots"]
            if item["authority_field_id"] != "report.system.name"
        ]

        with patch(
            "app.report_roundtrip.contracts.roundtrip_policy",
            return_value=narrowed,
        ):
            job = self._upload(project["project_uuid"], source)

        self.assertEqual(job["status"], "invalid", job)
        self.assertEqual(job["error_code"], "ROUNDTRIP_CURRENT_POLICY_REVOKED")

    def test_failed_upload_is_atomic_and_failed_sources_are_bounded(self) -> None:
        target = Path(self.r4.r3.temporary.name) / "private" / "upload.docx"
        upload = UploadFile(file=io.BytesIO(b"12345"), filename="oversized.docx")
        with patch.object(roundtrip_service, "MAX_UPLOAD_BYTES", 4):
            with self.assertRaises(ReportDomainError) as too_large:
                roundtrip_service._write_upload(  # noqa: SLF001 - storage invariant regression
                    upload,
                    target,
                    existing_private_bytes=0,
                )
        self.assertEqual(too_large.exception.code, "ROUNDTRIP_FILE_TOO_LARGE")
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix(".uploading").exists())

        project, _revision = self._prepare()
        created: list[int] = []
        private_root = roundtrip_service._private_root()  # noqa: SLF001
        with database.connect() as db:
            for index in range(3):
                timestamp = f"2026-07-16T00:00:0{index}+00:00"
                cursor = db.execute(
                    """
                    INSERT INTO report_import_jobs (
                        mode, status, original_name, source_docx_path,
                        fingerprint_json, summary_json, created_at, project_id,
                        roundtrip_status, diff_json, resolution_json
                    ) VALUES ('roundtrip', 'failed', 'invalid.docx', ?, '{}', '{}', ?, ?,
                              'invalid', '{}', '{}')
                    """,
                    (
                        f"private/roundtrip/imports/pending-{index}/source.docx",
                        timestamp,
                        project["id"],
                    ),
                )
                created.append(int(cursor.lastrowid))
                job_dir = private_root / str(int(cursor.lastrowid))
                job_dir.mkdir(parents=True, exist_ok=True)
                (job_dir / "source.docx").write_bytes(b"invalid")
                db.execute(
                    "UPDATE report_import_jobs SET source_docx_path=? WHERE id=?",
                    (
                        f"private/roundtrip/imports/{int(cursor.lastrowid)}/source.docx",
                        int(cursor.lastrowid),
                    ),
                )

        with patch.object(roundtrip_service, "MAX_RETAINED_FAILED_UPLOADS", 1):
            roundtrip_service._prune_failed_roundtrip_uploads()  # noqa: SLF001

        with database.connect() as db:
            rows = db.execute(
                "SELECT id, source_docx_path, summary_json FROM report_import_jobs WHERE id IN (?,?,?) ORDER BY id DESC",
                tuple(created),
            ).fetchall()
        self.assertNotEqual(rows[0]["source_docx_path"], "")
        for row in rows[1:]:
            self.assertEqual(row["source_docx_path"], "")
            self.assertTrue(json.loads(row["summary_json"])["source_pruned"])
            self.assertFalse((private_root / str(row["id"])).exists())

    def test_startup_recovers_interrupted_upload_and_validation_jobs(self) -> None:
        project, _revision = self._prepare()
        private_root = roundtrip_service._private_root()  # noqa: SLF001
        timestamp = "2026-07-16T00:00:00+00:00"
        with database.connect() as db:
            job_ids: list[int] = []
            for status in ("uploaded", "validating"):
                cursor = db.execute(
                    """
                    INSERT INTO report_import_jobs (
                        mode, status, original_name, source_docx_path,
                        fingerprint_json, summary_json, created_at, project_id,
                        roundtrip_status, diff_json, resolution_json
                    ) VALUES ('roundtrip', ?, 'interrupted.docx', ?, '{}', '{}', ?, ?,
                              ?, '{}', '{}')
                    """,
                    (
                        "uploaded" if status == "uploaded" else "parsing",
                        f"private/roundtrip/imports/{status}/source.docx",
                        timestamp,
                        project["id"],
                        status,
                    ),
                )
                job_ids.append(int(cursor.lastrowid))
                db.execute(
                    "UPDATE report_import_jobs SET source_docx_path=? WHERE id=?",
                    (
                        f"private/roundtrip/imports/{int(cursor.lastrowid)}/source.docx",
                        int(cursor.lastrowid),
                    ),
                )

        uploaded_dir = private_root / str(job_ids[0])
        validating_dir = private_root / str(job_ids[1])
        orphan_dir = private_root / "999999"
        for directory in (uploaded_dir, validating_dir, orphan_dir):
            directory.mkdir(parents=True, exist_ok=True)
        (uploaded_dir / "source.uploading").write_bytes(b"partial")
        (validating_dir / "source.docx").write_bytes(b"complete-source")
        (orphan_dir / "source.uploading").write_bytes(b"orphan-partial")

        self.assertEqual(recover_abandoned_roundtrip_jobs(), 2)

        self.assertFalse((uploaded_dir / "source.uploading").exists())
        self.assertFalse(uploaded_dir.exists())
        self.assertTrue((validating_dir / "source.docx").is_file())
        self.assertFalse(orphan_dir.exists())
        with database.connect() as db:
            jobs = db.execute(
                "SELECT id, roundtrip_status, error_code FROM report_import_jobs WHERE id IN (?,?) ORDER BY id",
                tuple(job_ids),
            ).fetchall()
            issues = db.execute(
                "SELECT job_id, code FROM report_import_issues WHERE job_id IN (?,?) ORDER BY job_id",
                tuple(job_ids),
            ).fetchall()
        self.assertEqual(
            [(row["roundtrip_status"], row["error_code"]) for row in jobs],
            [
                ("invalid", "ROUNDTRIP_PROCESS_INTERRUPTED"),
                ("invalid", "ROUNDTRIP_PROCESS_INTERRUPTED"),
            ],
        )
        self.assertEqual(
            [(row["job_id"], row["code"]) for row in issues],
            [
                (job_ids[0], "ROUNDTRIP_PROCESS_INTERRUPTED"),
                (job_ids[1], "ROUNDTRIP_PROCESS_INTERRUPTED"),
            ],
        )

    def test_comment_change_is_ignored_without_weakening_structure_checks(self) -> None:
        project, revision = self._prepare()
        source, baseline = self._export(project, revision)
        with zipfile.ZipFile(source) as package:
            parts = {name: package.read(name) for name in package.namelist()}
        parts["word/comments.xml"] = (
            f'<w:comments xmlns:w="{W}"><w:comment w:id="0">'
            "<w:p><w:r><w:t>仅供复核</w:t></w:r></w:p>"
            "</w:comment></w:comments>"
        ).encode("utf-8")

        _values, ignored = _validate_structure(parts, baseline)

        self.assertEqual(
            [item["field_path"] for item in ignored],
            ["document.comments"],
        )

    def test_project_delete_removes_business_data_and_keeps_hash_only_tombstone(self) -> None:
        project, revision = self._prepare()
        source, baseline = self._export(project, revision)
        edited = self._edit_authority(
            source,
            baseline,
            "report.system.name",
            "删除前敏感系统名称",
            filename="delete-audit.docx",
        )
        job = self._upload(project["project_uuid"], edited)
        committed = commit_roundtrip_job(
            job["id"],
            ReportRoundtripCommitWrite(
                resolution_hash=job["resolution_hash"],
                expected_project_revision=job["observed_project_revision"],
            ),
        )
        self.assertEqual(committed["status"], "succeeded")
        with database.connect() as db:
            manifest = db.execute(
                """
                SELECT manifest_hash, baseline_hash, structure_contract_hash
                FROM report_roundtrip_manifests WHERE project_id=?
                """,
                (project["id"],),
            ).fetchone()
            audit = db.execute(
                """
                SELECT source_sha256, manifest_hash, diff_hash, resolution_hash,
                       before_hash, after_hash
                FROM report_import_audits WHERE project_id=?
                """,
                (project["id"],),
            ).fetchone()
            private_relative = str(
                db.execute(
                    "SELECT source_docx_path FROM report_import_jobs WHERE id=?",
                    (job["id"],),
                ).fetchone()["source_docx_path"]
            )
        self.assertIsNotNone(manifest)
        self.assertIsNotNone(audit)
        private_path = (settings.database_path.parent / private_relative).resolve()
        export_root = (settings.storage_path / "exports" / str(project["project_uuid"])).resolve()
        self.assertTrue(private_path.is_file())
        self.assertTrue(export_root.is_dir())

        response = Response()
        deleted = delete_project_api(int(project["id"]), response)
        self.assertEqual(deleted.project_uuid, project["project_uuid"])
        self.assertNotIn("X-FuLua-Cleanup-Pending", response.headers)
        self.assertFalse(private_path.exists())
        self.assertFalse(export_root.exists())

        with database.connect() as db:
            self.assertIsNone(database.get_project_by_id(int(project["id"]), db))
            for table in (
                "report_roundtrip_manifests",
                "report_import_audits",
                "report_import_jobs",
                "report_export_snapshots",
                "report_export_jobs",
                "report_blocks",
            ):
                column = "project_id"
                self.assertEqual(
                    int(
                        db.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {column}=?",
                            (project["id"],),
                        ).fetchone()[0]
                    ),
                    0,
                    table,
                )
            self.assertEqual(
                int(db.execute("SELECT COUNT(*) FROM assessment_rows").fetchone()[0]),
                0,
            )
            tombstone = db.execute(
                """
                SELECT project_uuid, manifest_hashes_json, audit_hashes_json
                FROM report_roundtrip_deletion_tombstones
                WHERE source_project_id=?
                """,
                (project["id"],),
            ).fetchone()
            self.assertIsNotNone(tombstone)
            self.assertEqual(
                int(
                    db.execute(
                        "SELECT COUNT(*) FROM report_roundtrip_cleanup_queue WHERE source_project_id=?",
                        (project["id"],),
                    ).fetchone()[0]
                ),
                0,
            )
            manifest_hashes = json.loads(tombstone["manifest_hashes_json"])
            audit_hashes = json.loads(tombstone["audit_hashes_json"])
            self.assertEqual(manifest_hashes[0]["manifest_hash"], manifest["manifest_hash"])
            self.assertEqual(manifest_hashes[0]["baseline_hash"], manifest["baseline_hash"])
            self.assertEqual(audit_hashes[0]["source_sha256"], audit["source_sha256"])
            self.assertEqual(audit_hashes[0]["after_hash"], audit["after_hash"])
            serialized = tombstone["manifest_hashes_json"] + tombstone["audit_hashes_json"]
            self.assertNotIn("删除前敏感系统名称", serialized)
            self.assertNotIn("R3 集成项目", serialized)
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "REPORT_ROUNDTRIP_TOMBSTONE_IMMUTABLE",
            ):
                db.execute(
                    "UPDATE report_roundtrip_deletion_tombstones SET project_uuid='changed' WHERE source_project_id=?",
                    (project["id"],),
                )

    def test_project_delete_retries_public_cleanup_without_skipping_private_files(self) -> None:
        project, revision = self._prepare()
        source, _baseline = self._export(project, revision)
        job = self._upload(project["project_uuid"], source)
        with database.connect() as db:
            private_relative = str(
                db.execute(
                    "SELECT source_docx_path FROM report_import_jobs WHERE id=?",
                    (job["id"],),
                ).fetchone()["source_docx_path"]
            )
        private_path = (settings.database_path.parent / private_relative).resolve()
        export_root = (settings.storage_path / "exports" / str(project["project_uuid"])).resolve()
        self.assertTrue(private_path.is_file())
        self.assertTrue(export_root.is_dir())

        with patch(
            "app.services.projects.remove_project_runtime_files",
            side_effect=PermissionError("simulated open Word file"),
        ):
            response = Response()
            deleted = delete_project_api(int(project["id"]), response)

        self.assertEqual(deleted.project_uuid, project["project_uuid"])
        self.assertEqual(response.headers["X-FuLua-Cleanup-Pending"], "true")
        self.assertIsNone(database.get_project_by_id(int(project["id"])))
        self.assertFalse(private_path.exists())
        self.assertTrue(export_root.is_dir())
        with database.connect() as db:
            pending = db.execute(
                "SELECT attempts, last_error_code FROM report_roundtrip_cleanup_queue WHERE source_project_id=?",
                (project["id"],),
            ).fetchone()
        self.assertIsNotNone(pending)
        self.assertEqual(int(pending["attempts"]), 1)
        self.assertIn("PermissionError", pending["last_error_code"])

        self.assertEqual(recover_pending_project_cleanup_tasks(), 1)
        self.assertFalse(export_root.exists())
        with database.connect() as db:
            self.assertEqual(
                int(
                    db.execute(
                        "SELECT COUNT(*) FROM report_roundtrip_cleanup_queue WHERE source_project_id=?",
                        (project["id"],),
                    ).fetchone()[0]
                ),
                0,
            )

    def test_project_cleanup_waits_for_inflight_upload_before_removing_private_source(self) -> None:
        project, revision = self._prepare()
        source, _baseline = self._export(project, revision)
        upload_started = threading.Event()
        allow_upload = threading.Event()
        real_write_upload = roundtrip_service._write_upload  # noqa: SLF001

        def paused_write_upload(file, target, *, existing_private_bytes):
            upload_started.set()
            if not allow_upload.wait(timeout=10):
                raise TimeoutError("test upload release timed out")
            return real_write_upload(
                file,
                target,
                existing_private_bytes=existing_private_bytes,
            )

        upload = UploadFile(
            file=io.BytesIO(source.read_bytes()),
            filename="inflight-roundtrip.docx",
        )
        with (
            patch.object(roundtrip_service, "_write_upload", side_effect=paused_write_upload),
            concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor,
        ):
            upload_future = executor.submit(
                create_roundtrip_job,
                str(project["project_uuid"]),
                upload,
            )
            self.assertTrue(upload_started.wait(timeout=10))
            _removed, cleanup_task_id, job_ids = database.delete_project_with_policy(
                int(project["id"])
            )
            self.assertIsNotNone(cleanup_task_id)
            self.assertEqual(len(job_ids), 1)
            cleanup_future = executor.submit(
                cleanup_deleted_project_files,
                int(project["id"]),
            )
            with self.assertRaises(concurrent.futures.TimeoutError):
                cleanup_future.result(timeout=0.2)
            allow_upload.set()
            with self.assertRaises(ReportDomainError):
                upload_future.result(timeout=15)
            self.assertTrue(cleanup_future.result(timeout=15))

        private_dir = roundtrip_service._private_root() / str(job_ids[0])  # noqa: SLF001
        self.assertFalse(private_dir.exists())
        self.assertIsNone(database.get_roundtrip_cleanup_task(int(project["id"])))

    def test_openapi_exposes_roundtrip_routes(self) -> None:
        paths = app.openapi()["paths"]
        self.assertIn("/api/projects/{project_uuid}/report-import-jobs", paths)
        self.assertIn("/api/report-import-jobs/{job_id}", paths)
        self.assertIn("/api/report-import-jobs/{job_id}/diff", paths)
        self.assertIn("/api/report-import-jobs/{job_id}/issues", paths)
        self.assertIn("/api/report-import-jobs/{job_id}/resolution", paths)
        self.assertIn("/api/report-import-jobs/{job_id}/commit", paths)


if __name__ == "__main__":
    unittest.main()
