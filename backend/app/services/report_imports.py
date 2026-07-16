"""R6 完整报告迁移的任务、解决项和确认事务。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from .. import database
from ..config import settings
from ..contracts import (
    FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)
from ..report_core.field_matrix import load_default_field_matrix
from ..report_import.parser import ReportImportParseError, parse_report_import
from ..report_import.schemas import (
    ReportAppendixACopyRead,
    ReportAppendixACopyWrite,
    ReportImportConfirmWrite,
    ReportImportFingerprint,
    ReportImportIssueRead,
    ReportImportJobRead,
    ReportImportResolutionRead,
    ReportImportResolutionsWrite,
)
from . import projects as project_service
from .report_domain.common import touch_project


MAX_REPORT_IMPORT_BYTES = 64 * 1024 * 1024
API_ORIGINAL_TEXT_LIMIT = 4000
API_CANDIDATE_JSON_LIMIT = 20_000
REPORT_IMPORT_DIR = "report_imports"

TEXT_FACT_FIELDS = {
    "report.identity.number",
    "report.system.name",
    "report.system.overview",
    "report.organization.assessed_name",
    "report.organization.client_name",
}
FACT_DISTRIBUTION_COLUMNS = {
    "report.distribution.regulator_copies": "regulator_copies",
    "report.distribution.client_copies": "client_copies",
    "report.distribution.assessment_copies": "assessment_organization_copies",
}
FACT_PHASE_COLUMNS = {
    "report.assessment.preparation_period": ("preparation_start", "preparation_end"),
    "report.assessment.plan_period": ("scheme_start", "scheme_end"),
    "report.assessment.report_period": ("analysis_start", "analysis_end"),
}


class ReportImportServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": {}}


def create_report_import_job(upload: UploadFile, *, mode: str = "migration") -> ReportImportJobRead:
    if mode == "roundtrip":
        raise ReportImportServiceError(
            "ROUNDTRIP_NOT_IMPLEMENTED",
            "当前只支持创建新项目的一次性迁移。",
            status_code=501,
        )
    if mode != "migration":
        raise ReportImportServiceError("REPORT_IMPORT_MODE_INVALID", "迁移模式不受支持。", status_code=422)
    original_name = _clean_filename(upload.filename)
    if Path(original_name).suffix.lower() != ".docx":
        raise ReportImportServiceError("REPORT_IMPORT_FILE_TYPE_INVALID", "仅支持 .docx 文件。", status_code=400)

    incoming = _save_incoming(upload)
    timestamp = database.utc_now()
    job_id: int | None = None
    try:
        with database.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO report_import_jobs (
                    mode, status, job_revision, original_name, source_docx_path,
                    fingerprint_json, summary_json, created_at
                ) VALUES ('migration', 'uploaded', 1, ?, '', '{}', '{}', ?)
                """,
                (original_name, timestamp),
            )
            job_id = int(cursor.lastrowid)
        job_dir = _job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        source = job_dir / "source.docx"
        os.replace(incoming, source)
        _update_job(
            job_id,
            status="parsing",
            source_docx_path=_relative_storage_path(source),
            started_at=database.utc_now(),
        )
        try:
            parsed = parse_report_import(source)
            parsed_path = job_dir / "parsed.json"
            parsed_path.write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with database.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                _replace_issues(db, job_id, parsed["issues"])
                _update_job_locked(
                    db,
                    job_id,
                    status="preview_ready",
                    source_sha256=parsed["source_sha256"],
                    detected_edition=parsed["detected_edition"],
                    detected_revision=parsed["detected_revision"],
                    fingerprint_json=_json(parsed["fingerprint"]),
                    parsed_json_path=_relative_storage_path(parsed_path),
                    summary_json=_json(parsed["summary"]),
                    error_message=None,
                    finished_at=database.utc_now(),
                )
        except ReportImportParseError as exc:
            _mark_parse_failed(job_id, source, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - 不向返回体泄漏客户内容或本机路径
            _mark_parse_failed(
                job_id,
                source,
                "REPORT_IMPORT_PARSE_FAILED",
                f"迁移解析失败（{type(exc).__name__}）。",
            )
        return get_report_import_job(job_id)  # type: ignore[return-value]
    finally:
        incoming.unlink(missing_ok=True)


def get_report_import_job(job_id: int) -> ReportImportJobRead | None:
    with database.connect() as db:
        row = db.execute("SELECT * FROM report_import_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return _job_read(db, row)


def get_project_migration_review(project_uuid: str) -> ReportImportJobRead | None:
    with database.connect() as db:
        project = database.get_project_by_uuid(project_uuid, db)
        if project is None:
            return None
        job = db.execute(
            """
            SELECT * FROM report_import_jobs
            WHERE created_project_id = ? AND status = 'succeeded'
            ORDER BY id DESC LIMIT 1
            """,
            (int(project["id"]),),
        ).fetchone()
        return _job_read(db, job) if job is not None else None


def copy_appendix_a_into_report(
    target_project_uuid: str,
    payload: ReportAppendixACopyWrite,
) -> ReportAppendixACopyRead:
    """把完整的附录 A 项目复制到一个空的完整报告草稿。

    `idempotency_key` 被编码进来源记录。重复请求只返回首次结果，不会再次
    追加行或图片。
    """

    target_uuid = str(target_project_uuid).strip()
    source_uuid = str(payload.source_project_uuid)
    operation_key = str(payload.idempotency_key)
    marker = f"project:{source_uuid}/appendix:A/copy:{operation_key}"
    repeated = _completed_appendix_copy(target_uuid, source_uuid, operation_key, marker)
    if repeated is not None:
        return repeated

    source_project, source_images, staging_dir, staged_files = _prepare_existing_appendix(
        source_uuid,
        f"copy-{operation_key}",
    )
    target_upload_root = (settings.storage_path / "uploads" / target_uuid).resolve()
    upload_root_preexisted = target_upload_root.exists()
    target_files_may_exist = False
    try:
        if source_images and upload_root_preexisted:
            raise ReportImportServiceError(
                "APPENDIX_A_COPY_TARGET_PATH_CONFLICT",
                "目标项目存在未登记的附录图片目录，不能安全覆盖。",
            )
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            target = database.get_project_by_uuid(target_uuid, db)
            if target is None:
                raise ReportImportServiceError(
                    "REPORT_PROJECT_NOT_FOUND", "目标完整报告不存在。", status_code=404
                )
            if target["project_type"] != "full_report" or target["workflow_status"] != "draft":
                raise ReportImportServiceError(
                    "APPENDIX_A_COPY_TARGET_INVALID", "附录 A 只能复制到完整报告草稿。"
                )
            existing_marker = db.execute(
                """
                SELECT 1 FROM report_field_sources
                WHERE project_id = ? AND source_locator = ?
                LIMIT 1
                """,
                (int(target["id"]), marker),
            ).fetchone()
            if existing_marker is not None:
                return _appendix_copy_result(
                    db, target, source_uuid, operation_key, repeated=True
                )
            _assert_empty_appendix_target(db, int(target["id"]))
            _, current_images = _revalidate_appendix_source(
                db,
                expected_source=source_project,
                expected_uuid=source_uuid,
                expected_images=source_images,
            )
            target_files_may_exist = bool(current_images)
            project_service._clone_appendix_a_domain(
                db,
                source_project_id=int(source_project["id"]),
                target_project_id=int(target["id"]),
                source_images=current_images,
                staged_files=staged_files,
                timestamp=database.utc_now(),
                target_project_uuid=target_uuid,
            )
            _recalculate_appendix_scores(db, int(target["id"]))
            _insert_appendix_source_records(
                db,
                job_id=None,
                project_id=int(target["id"]),
                appendix_source="existing_project",
                source_project_uuid=source_uuid,
                appendix_payload={},
                timestamp=database.utc_now(),
                source_locator_override=marker,
            )
            return _appendix_copy_result(
                db, target, source_uuid, operation_key, repeated=False
            )
    except project_service.ProjectServiceError as exc:
        if target_files_may_exist and not upload_root_preexisted:
            project_service._remove_storage_child(Path("uploads") / target_uuid)
        raise ReportImportServiceError(
            exc.code, exc.message, status_code=exc.status_code
        ) from exc
    except Exception:
        if target_files_may_exist and not upload_root_preexisted:
            project_service._remove_storage_child(Path("uploads") / target_uuid)
        raise
    finally:
        _remove_staging(staging_dir)


def resolve_report_import_issues(
    job_id: int,
    payload: ReportImportResolutionsWrite,
) -> ReportImportJobRead:
    matrix = load_default_field_matrix()
    relations = {item.relation_id: item for item in matrix.relations}
    timestamp = database.utc_now()
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM report_import_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ReportImportServiceError("REPORT_IMPORT_NOT_FOUND", "迁移任务不存在。", status_code=404)
        if job["status"] not in {"preview_ready", "succeeded"}:
            raise ReportImportServiceError("REPORT_IMPORT_STATE_CONFLICT", "当前迁移任务不能修改解决项。")
        if int(job["job_revision"]) != payload.job_revision:
            raise ReportImportServiceError("REPORT_IMPORT_REVISION_CONFLICT", "迁移预览已更新，请刷新后重试。")

        created_project: sqlite3.Row | None = None
        if job["status"] == "succeeded":
            if job["created_project_id"] is None:
                raise ReportImportServiceError("REPORT_IMPORT_STATE_CONFLICT", "迁移任务缺少已创建项目。")
            created_project = database.get_project_by_id(int(job["created_project_id"]), db)
            if (
                created_project is None
                or created_project["project_type"] != "full_report"
                or created_project["workflow_status"] != "draft"
            ):
                raise ReportImportServiceError(
                    "REPORT_IMPORT_REVIEW_PROJECT_LOCKED",
                    "只有迁移创建的完整报告草稿可以继续处理待确认项。",
                )
            if payload.expected_project_updated_at is None:
                raise ReportImportServiceError(
                    "REPORT_IMPORT_PROJECT_REVISION_REQUIRED",
                    "项目创建后的迁移审阅必须携带当前项目版本。",
                    status_code=422,
                )
            if str(created_project["updated_at"]) != payload.expected_project_updated_at:
                raise ReportImportServiceError(
                    "REPORT_IMPORT_PROJECT_REVISION_CONFLICT",
                    "项目内容已在其他页面更新，请刷新迁移审阅后重试。",
                    status_code=409,
                )

        for request in payload.resolutions:
            issue = db.execute(
                "SELECT * FROM report_import_issues WHERE id = ? AND job_id = ?",
                (request.issue_id, job_id),
            ).fetchone()
            if issue is None:
                raise ReportImportServiceError("REPORT_IMPORT_ISSUE_NOT_FOUND", "待确认项不存在。", status_code=404)
            if int(issue["revision"]) != request.revision:
                raise ReportImportServiceError("REPORT_IMPORT_ISSUE_REVISION_CONFLICT", "待确认项已更新。")
            if not bool(issue["needs_confirmation"]) or bool(issue["blocks_confirmation"]):
                raise ReportImportServiceError("REPORT_IMPORT_ISSUE_NOT_RESOLVABLE", "该诊断项不能通过手工值绕过。")
            existing_resolution = db.execute(
                "SELECT * FROM report_import_resolutions WHERE job_id = ? AND issue_id = ?",
                (job_id, int(issue["id"])),
            ).fetchone()
            if created_project is not None:
                if request.action == "keep_original":
                    raise ReportImportServiceError(
                        "REPORT_IMPORT_REVIEW_ACTION_REQUIRED",
                        "项目创建后必须采用有效映射或明确跳过，不能继续保留为待确认。",
                        status_code=422,
                    )
                finalized = False
                if existing_resolution is not None and existing_resolution["action"] == "skip":
                    finalized = True
                elif existing_resolution is not None and existing_resolution["action"] == "adopt_candidate":
                    finalized = db.execute(
                        """
                        SELECT 1 FROM report_field_sources
                        WHERE project_id = ? AND report_import_job_id = ?
                          AND association_id IS ? AND authority_field_id IS ?
                          AND field_path = ? AND source_locator = ?
                          AND mapping_status = 'adopted' AND needs_confirmation = 0
                        LIMIT 1
                        """,
                        (
                            int(created_project["id"]),
                            job_id,
                            issue["association_id"],
                            issue["authority_field_id"],
                            issue["field_path"],
                            issue["source_locator"],
                        ),
                    ).fetchone() is not None
                if finalized:
                    raise ReportImportServiceError(
                        "REPORT_IMPORT_RESOLUTION_FINALIZED",
                        "该迁移项已经完成最终处理，不能反向覆盖。",
                    )
            _validate_issue_mapping(issue, relations)
            if request.action == "adopt_candidate" and (
                issue["association_id"] is None or issue["authority_field_id"] is None
            ):
                raise ReportImportServiceError(
                    "REPORT_IMPORT_MAPPING_INVALID",
                    "未映射内容不能直接写入项目字段，请保留原文或跳过。",
                )
            if request.action == "adopt_candidate" and (
                issue["candidate_value_json"] is None and request.resolved_value is None
            ):
                raise ReportImportServiceError("REPORT_IMPORT_RESOLUTION_VALUE_REQUIRED", "采用候选时必须提供值。", status_code=422)
            resolved_value = request.resolved_value
            if resolved_value is None and issue["candidate_value_json"] is not None:
                resolved_value = _load_json(issue["candidate_value_json"], None)
            if request.action == "adopt_candidate":
                _validate_resolution_scalar(resolved_value)
                _validate_adoptable_fact(str(issue["authority_field_id"]), resolved_value)
                if created_project is not None:
                    _apply_post_confirm_resolution(
                        db,
                        job_id=job_id,
                        project=created_project,
                        issue=issue,
                        action=request.action,
                        resolved_value=resolved_value,
                        timestamp=timestamp,
                    )
            elif created_project is not None:
                _apply_post_confirm_resolution(
                    db,
                    job_id=job_id,
                    project=created_project,
                    issue=issue,
                    action=request.action,
                    resolved_value=None,
                    timestamp=timestamp,
                )
            db.execute(
                """
                INSERT INTO report_import_resolutions (
                    job_id, issue_id, issue_revision, association_id, authority_field_id,
                    field_path, action, resolved_value_json, resolved_by_user,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(job_id, issue_id) DO UPDATE SET
                    issue_revision = excluded.issue_revision,
                    association_id = excluded.association_id,
                    authority_field_id = excluded.authority_field_id,
                    field_path = excluded.field_path,
                    action = excluded.action,
                    resolved_value_json = excluded.resolved_value_json,
                    resolved_by_user = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id, int(issue["id"]), int(issue["revision"]),
                    issue["association_id"], issue["authority_field_id"], issue["field_path"],
                    request.action, _json(resolved_value) if resolved_value is not None else None,
                    timestamp, timestamp,
                ),
            )
            status = "ignored" if request.action == "skip" else "resolved"
            db.execute(
                """
                UPDATE report_import_issues
                SET status = ?, revision = revision + 1, updated_at = ?
                WHERE id = ?
                """,
                (status, timestamp, int(issue["id"])),
            )
        db.execute(
            "UPDATE report_import_jobs SET job_revision = job_revision + 1 WHERE id = ?",
            (job_id,),
        )
        if created_project is not None:
            touch_project(db, int(created_project["id"]))
        updated = db.execute("SELECT * FROM report_import_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job_read(db, updated)


def _validate_resolution_scalar(value: Any) -> None:
    if value is None or isinstance(value, (bool, list, dict)) or not isinstance(
        value, (str, int, float)
    ):
        raise ReportImportServiceError(
            "REPORT_IMPORT_RESOLUTION_VALUE_INVALID",
            "采用候选时必须提交单个文本或数值，不能提交数组或对象。",
            status_code=422,
        )


def _validate_adoptable_fact(field_id: str, value: Any) -> None:
    if field_id not in TEXT_FACT_FIELDS | set(FACT_DISTRIBUTION_COLUMNS) | set(FACT_PHASE_COLUMNS):
        raise ReportImportServiceError(
            "REPORT_IMPORT_MAPPING_NOT_WRITABLE",
            "该候选字段当前不能通过迁移审阅直接写入，请保留原文或明确跳过。",
            status_code=422,
        )
    text = str(value).strip()
    if not text:
        raise ReportImportServiceError(
            "REPORT_IMPORT_RESOLUTION_VALUE_INVALID",
            "采用候选时不能提交空值。",
            status_code=422,
        )
    if field_id in FACT_DISTRIBUTION_COLUMNS and re.search(r"\d+", text) is None:
        raise ReportImportServiceError(
            "REPORT_IMPORT_RESOLUTION_VALUE_INVALID",
            "该解决值不符合目标字段要求。",
            status_code=422,
        )
    if field_id in FACT_PHASE_COLUMNS and len(_date_values(text)) != 2:
        raise ReportImportServiceError(
            "REPORT_IMPORT_RESOLUTION_VALUE_INVALID",
            "该解决值不符合目标字段要求。",
            status_code=422,
        )
    if len(str(value).encode("utf-8")) > API_CANDIDATE_JSON_LIMIT:
        raise ReportImportServiceError(
            "REPORT_IMPORT_RESOLUTION_VALUE_TOO_LARGE",
            "迁移解决值超过允许长度。",
            status_code=422,
        )


def _apply_post_confirm_resolution(
    db: sqlite3.Connection,
    *,
    job_id: int,
    project: sqlite3.Row,
    issue: sqlite3.Row,
    action: str,
    resolved_value: Any,
    timestamp: str,
) -> None:
    project_id = int(project["id"])
    if action == "adopt_candidate":
        if not _write_fact(
            db,
            project_id,
            str(project["project_uuid"]),
            str(issue["authority_field_id"]),
            resolved_value,
            timestamp,
        ):
            raise ReportImportServiceError(
                "REPORT_IMPORT_RESOLUTION_VALUE_INVALID",
                "该解决值不符合目标字段要求。",
                status_code=422,
            )
        mapping_status = "adopted"
        source_kind = "imported"
    else:
        mapping_status = "skipped"
        source_kind = "imported_manual_draft" if issue["association_id"] else "unmapped"

    existing = db.execute(
        """
        SELECT id FROM report_field_sources
        WHERE project_id = ? AND report_import_job_id = ?
          AND association_id IS ? AND authority_field_id IS ?
          AND field_path = ? AND source_locator = ?
        ORDER BY id LIMIT 1
        """,
        (
            project_id,
            job_id,
            issue["association_id"],
            issue["authority_field_id"],
            issue["field_path"],
            issue["source_locator"],
        ),
    ).fetchone()
    if existing is not None:
        db.execute(
            """
            UPDATE report_field_sources
            SET source_kind = ?, mapping_status = ?, needs_confirmation = 0, updated_at = ?
            WHERE id = ?
            """,
            (source_kind, mapping_status, timestamp, int(existing["id"])),
        )
        return
    db.execute(
        """
        INSERT INTO report_field_sources (
            project_id, report_import_job_id, association_id, authority_field_id,
            field_path, source_kind, source_locator, source_value_hash, original_text,
            confidence, mapping_status, needs_confirmation, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            project_id,
            job_id,
            issue["association_id"],
            issue["authority_field_id"],
            issue["field_path"],
            source_kind,
            issue["source_locator"],
            issue["source_value_hash"],
            issue["original_text"],
            issue["confidence"],
            mapping_status,
            timestamp,
            timestamp,
        ),
    )


def confirm_report_import_job(
    job_id: int,
    payload: ReportImportConfirmWrite,
) -> ReportImportJobRead:
    job = _get_job_row(job_id)
    if job is None:
        raise ReportImportServiceError("REPORT_IMPORT_NOT_FOUND", "迁移任务不存在。", status_code=404)
    if job["status"] == "succeeded" and job["created_project_id"] is not None:
        return get_report_import_job(job_id)  # type: ignore[return-value]
    if job["status"] != "preview_ready":
        raise ReportImportServiceError("REPORT_IMPORT_STATE_CONFLICT", "迁移任务尚未形成可确认的预览。")
    if int(job["job_revision"]) != payload.job_revision:
        raise ReportImportServiceError("REPORT_IMPORT_REVISION_CONFLICT", "迁移预览已更新，请刷新后重试。")

    source = _storage_child(str(job["source_docx_path"] or ""))
    if _sha256_file(source) != str(job["source_sha256"] or ""):
        raise ReportImportServiceError("SOURCE_DOCX_CHANGED", "迁移源副本已变化，请重新上传。")
    parsed = _load_parsed(job)
    _validate_confirm_blockers(job_id, payload.appendix_a_source, parsed)
    _validate_accepted_resolutions(job_id, set(payload.accepted_resolutions))

    staged_files: dict[int, Path] = {}
    staging_dir: Path | None = None
    source_project: sqlite3.Row | None = None
    source_images: list[sqlite3.Row] = []
    if payload.appendix_a_source == "existing_project":
        source_project, source_images, staging_dir, staged_files = _prepare_existing_appendix(
            str(payload.appendix_a_project_uuid), job_id
        )

    target_project_id: int | None = None
    target_project_uuid = str(uuid.uuid4())
    target_files_may_exist = False
    timestamp = database.utc_now()
    try:
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM report_import_jobs WHERE id = ?", (job_id,)).fetchone()
            if current is not None and current["status"] == "succeeded" and current["created_project_id"] is not None:
                return _job_read(db, current)
            if current is None or current["status"] != "preview_ready":
                raise ReportImportServiceError("REPORT_IMPORT_STATE_CONFLICT", "迁移任务状态已变化。")
            if int(current["job_revision"]) != payload.job_revision:
                raise ReportImportServiceError("REPORT_IMPORT_REVISION_CONFLICT", "迁移预览已更新。")
            project = database._insert_project(
                db,
                name=payload.project_name.strip(),
                project_type="full_report",
                workflow_status="draft",
                template_package_id=FULL_REPORT_TEMPLATE_PACKAGE_ID,
                template_edition=FULL_REPORT_TEMPLATE_EDITION,
                template_revision=FULL_REPORT_TEMPLATE_REVISION,
                template_asset_set_hash=FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
                source_project_uuid=None,
                created_by_operation="migration_import",
                project_uuid=target_project_uuid,
            )
            target_project_id = int(project["id"])
            if payload.appendix_a_source == "document":
                _insert_document_appendix(db, target_project_id, parsed.get("appendix_a") or {}, timestamp)
            else:
                assert source_project is not None
                _, current_images = _revalidate_appendix_source(
                    db,
                    expected_source=source_project,
                    expected_uuid=str(source_project["project_uuid"]),
                    expected_images=source_images,
                )
                target_files_may_exist = True
                project_service._clone_appendix_a_domain(
                    db,
                    source_project_id=int(source_project["id"]),
                    target_project_id=target_project_id,
                    source_images=current_images,
                    staged_files=staged_files,
                    timestamp=timestamp,
                    target_project_uuid=target_project_uuid,
                )
                _recalculate_appendix_scores(db, target_project_id)
                _resolve_unselected_document_appendix(db, job_id, timestamp)
            _apply_field_candidates(
                db,
                job_id=job_id,
                project_id=target_project_id,
                project_uuid=target_project_uuid,
                accepted_resolution_ids=set(payload.accepted_resolutions),
                keep_unresolved_original=payload.keep_unresolved_original,
                timestamp=timestamp,
            )
            _insert_appendix_source_records(
                db,
                job_id=job_id,
                project_id=target_project_id,
                appendix_source=payload.appendix_a_source,
                source_project_uuid=(str(source_project["project_uuid"]) if source_project is not None else None),
                appendix_payload=parsed.get("appendix_a") or {},
                timestamp=timestamp,
            )
            if _sha256_file(source) != str(current["source_sha256"] or ""):
                raise ReportImportServiceError(
                    "SOURCE_DOCX_CHANGED", "确认期间迁移源副本发生变化。"
                )
            _update_job_locked(
                db,
                job_id,
                status="succeeded",
                appendix_a_source=payload.appendix_a_source,
                created_project_id=target_project_id,
                job_revision=int(current["job_revision"]) + 1,
                error_message=None,
                finished_at=timestamp,
            )
    except project_service.ProjectServiceError as exc:
        if target_project_id is not None or target_files_may_exist:
            project_service.remove_project_runtime_files(target_project_id or -1, target_project_uuid)
        raise ReportImportServiceError(
            exc.code, exc.message, status_code=exc.status_code
        ) from exc
    except Exception:
        if target_project_id is not None or target_files_may_exist:
            project_service.remove_project_runtime_files(target_project_id or -1, target_project_uuid)
        raise
    finally:
        if staging_dir is not None:
            _remove_staging(staging_dir)

    return get_report_import_job(job_id)  # type: ignore[return-value]


def _resolve_unselected_document_appendix(
    db: sqlite3.Connection,
    job_id: int,
    timestamp: str,
) -> None:
    """用户改用既有项目时，显式跳过 DOCX 内未采用的附录 A 候选。"""

    issues = db.execute(
        """
        SELECT * FROM report_import_issues
        WHERE job_id = ?
          AND (code LIKE 'APPENDIX_A_%' OR code = 'DOCUMENT_APPENDIX_IMAGES_REQUIRE_REVIEW')
        ORDER BY id
        """,
        (job_id,),
    ).fetchall()
    for issue in issues:
        db.execute(
            """
            INSERT INTO report_import_resolutions (
                job_id, issue_id, issue_revision, association_id, authority_field_id,
                field_path, action, resolved_value_json, resolved_by_user,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'skip', NULL, 1, ?, ?)
            ON CONFLICT(job_id, issue_id) DO UPDATE SET
                issue_revision = excluded.issue_revision,
                action = 'skip',
                resolved_value_json = NULL,
                resolved_by_user = 1,
                updated_at = excluded.updated_at
            """,
            (
                job_id,
                int(issue["id"]),
                int(issue["revision"]),
                issue["association_id"],
                issue["authority_field_id"],
                issue["field_path"],
                timestamp,
                timestamp,
            ),
        )
        db.execute(
            """
            UPDATE report_import_issues
            SET status = 'ignored', revision = revision + 1, updated_at = ?
            WHERE id = ?
            """,
            (timestamp, int(issue["id"])),
        )


def _save_incoming(upload: UploadFile) -> Path:
    incoming_dir = settings.storage_path / REPORT_IMPORT_DIR / ".incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    target = incoming_dir / f"{uuid.uuid4().hex}.docx"
    size = 0
    try:
        upload.file.seek(0)
    except (AttributeError, OSError):
        pass
    try:
        with target.open("xb") as stream:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_REPORT_IMPORT_BYTES:
                    raise ReportImportServiceError("REPORT_IMPORT_FILE_TOO_LARGE", "DOCX 超过 64MB 安全上限。", status_code=413)
                stream.write(chunk)
        if size == 0:
            raise ReportImportServiceError("REPORT_IMPORT_FILE_EMPTY", "DOCX 文件为空。", status_code=400)
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _mark_parse_failed(job_id: int, source: Path, code: str, message: str) -> None:
    digest = _sha256_file(source) if source.is_file() else None
    timestamp = database.utc_now()
    issue = {
        "code": code,
        "severity": "error",
        "association_id": None,
        "authority_field_id": None,
        "field_path": "",
        "source_locator": "package",
        "original_text": "",
        "source_value_hash": None,
        "candidate_value": None,
        "confidence": "unmapped",
        "status": "open",
        "needs_confirmation": False,
        "blocks_confirmation": True,
        "blocks_final_export": True,
    }
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _replace_issues(db, job_id, [issue])
        _update_job_locked(
            db,
            job_id,
            status="failed",
            source_sha256=digest,
            fingerprint_json=_json({"sha256": digest or "", "matched": False}),
            summary_json=_json(
                {
                    "template_match": {"matched": False},
                    "chapter_stats": {},
                    "automatic_mappings": 0,
                    "pending_confirmation": 0,
                    "unmapped_content": 1,
                    "document_appendix": {"available": False, "sections_present": [], "row_count": 0},
                    "appendix_sources": [],
                }
            ),
            error_message=message,
            finished_at=timestamp,
        )


def _replace_issues(db: sqlite3.Connection, job_id: int, issues: list[dict[str, Any]]) -> None:
    db.execute("DELETE FROM report_import_issues WHERE job_id = ?", (job_id,))
    timestamp = database.utc_now()
    for issue in issues:
        db.execute(
            """
            INSERT INTO report_import_issues (
                job_id, revision, code, severity, association_id, authority_field_id,
                field_path, source_locator, original_text, source_value_hash,
                candidate_value_json, confidence, status, needs_confirmation,
                blocks_confirmation, blocks_final_export, created_at, updated_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, issue["code"], issue["severity"], issue.get("association_id"),
                issue.get("authority_field_id"), issue.get("field_path", ""),
                issue.get("source_locator", ""), issue.get("original_text", ""),
                issue.get("source_value_hash"),
                _json(issue.get("candidate_value")) if issue.get("candidate_value") is not None else None,
                issue["confidence"], issue.get("status", "open"),
                int(bool(issue.get("needs_confirmation"))),
                int(bool(issue.get("blocks_confirmation"))),
                int(bool(issue.get("blocks_final_export"))), timestamp, timestamp,
            ),
        )


def _job_read(db: sqlite3.Connection, row: sqlite3.Row) -> ReportImportJobRead:
    issues = db.execute(
        "SELECT * FROM report_import_issues WHERE job_id = ? ORDER BY id", (int(row["id"]),)
    ).fetchall()
    resolutions = db.execute(
        "SELECT * FROM report_import_resolutions WHERE job_id = ? ORDER BY id", (int(row["id"]),)
    ).fetchall()
    summary = _load_json(row["summary_json"], {})
    candidates = _appendix_project_candidates(db)
    summary["appendix_sources"] = candidates
    document_available = bool((summary.get("document_appendix") or {}).get("available"))
    global_blockers = [
        issue for issue in issues
        if bool(issue["blocks_confirmation"])
        and issue["status"] == "open"
        and not str(issue["code"]).startswith("APPENDIX_A_")
    ]
    has_appendix_source = document_available or any(item["complete"] for item in candidates)
    created_project_uuid = None
    created_project_updated_at = None
    project: sqlite3.Row | None = None
    if row["created_project_id"] is not None:
        project = database.get_project_by_id(int(row["created_project_id"]), db)
        if project is not None:
            created_project_uuid = str(project["project_uuid"])
            created_project_updated_at = str(project["updated_at"])
    issue_by_id = {int(issue["id"]): issue for issue in issues}
    return ReportImportJobRead(
        id=int(row["id"]),
        status=row["status"],
        mode=row["mode"],
        job_revision=int(row["job_revision"]),
        original_name=row["original_name"],
        detected_edition=row["detected_edition"],
        detected_revision=row["detected_revision"],
        fingerprint=ReportImportFingerprint(**_load_json(row["fingerprint_json"], {})),
        summary=summary,
        issues=[_issue_read(issue) for issue in issues],
        resolutions=[
            _resolution_read(
                item,
                applied=_resolution_is_applied(
                    db,
                    project_id=int(project["id"]) if project is not None else None,
                    job_id=int(row["id"]),
                    resolution=item,
                    issue=issue_by_id.get(int(item["issue_id"])),
                ),
            )
            for item in resolutions
        ],
        appendix_a_source=row["appendix_a_source"],
        confirmable=(row["status"] == "preview_ready" and not global_blockers and has_appendix_source),
        created_project_uuid=created_project_uuid,
        created_project_updated_at=created_project_updated_at,
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _issue_read(row: sqlite3.Row) -> ReportImportIssueRead:
    original_text = str(row["original_text"] or "")
    return ReportImportIssueRead(
        id=int(row["id"]), revision=int(row["revision"]), code=row["code"], severity=row["severity"],
        association_id=row["association_id"], authority_field_id=row["authority_field_id"],
        field_path=row["field_path"], source_locator=row["source_locator"],
        original_text=original_text[:API_ORIGINAL_TEXT_LIMIT],
        original_text_truncated=len(original_text) > API_ORIGINAL_TEXT_LIMIT,
        source_value_hash=row["source_value_hash"],
        candidate_value=_api_candidate(row["candidate_value_json"]), confidence=row["confidence"],
        status=row["status"], needs_confirmation=bool(row["needs_confirmation"]),
        blocks_confirmation=bool(row["blocks_confirmation"]),
        blocks_final_export=bool(row["blocks_final_export"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _resolution_read(row: sqlite3.Row, *, applied: bool = False) -> ReportImportResolutionRead:
    return ReportImportResolutionRead(
        id=int(row["id"]), issue_id=int(row["issue_id"]), issue_revision=int(row["issue_revision"]),
        association_id=row["association_id"], authority_field_id=row["authority_field_id"],
        field_path=row["field_path"], action=row["action"],
        resolved_value=_api_candidate(row["resolved_value_json"]),
        resolved_by_user=bool(row["resolved_by_user"]),
        applied=applied,
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _resolution_is_applied(
    db: sqlite3.Connection,
    *,
    project_id: int | None,
    job_id: int,
    resolution: sqlite3.Row,
    issue: sqlite3.Row | None,
) -> bool:
    if project_id is None or issue is None:
        return False
    action = str(resolution["action"])
    if action == "skip":
        return True
    if action != "adopt_candidate":
        return False
    return db.execute(
        """
        SELECT 1 FROM report_field_sources
        WHERE project_id = ? AND report_import_job_id = ?
          AND association_id IS ? AND authority_field_id IS ?
          AND field_path = ? AND source_locator = ?
          AND mapping_status = 'adopted' AND needs_confirmation = 0
        LIMIT 1
        """,
        (
            project_id,
            job_id,
            issue["association_id"],
            issue["authority_field_id"],
            issue["field_path"],
            issue["source_locator"],
        ),
    ).fetchone() is not None


def _appendix_project_candidates(db: sqlite3.Connection) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    projects = db.execute(
        "SELECT * FROM projects WHERE project_type = 'appendix_a' ORDER BY updated_at DESC, id DESC LIMIT 100"
    ).fetchall()
    for project in projects:
        sections_present = [
            str(row["code"])
            for row in db.execute(
                """
                SELECT s.code
                FROM appendix_sections s
                WHERE s.project_id = ?
                  AND EXISTS (SELECT 1 FROM assessment_rows r WHERE r.section_id = s.id)
                ORDER BY s.sort_order
                """,
                (int(project["id"]),),
            ).fetchall()
        ]
        error_count = _appendix_candidate_error_count(db, int(project["id"]))
        output.append(
            {
                "project_uuid": str(project["project_uuid"]),
                "name": str(project["name"]),
                "updated_at": str(project["updated_at"]),
                "sections_present": sections_present,
                "validation_error_count": error_count,
                "complete": len(sections_present) == 8 and error_count == 0,
            }
        )
    return output


def _completed_appendix_copy(
    target_uuid: str,
    source_uuid: str,
    operation_key: str,
    marker: str,
) -> ReportAppendixACopyRead | None:
    with database.connect() as db:
        target = database.get_project_by_uuid(target_uuid, db)
        if target is None:
            return None
        exists = db.execute(
            "SELECT 1 FROM report_field_sources WHERE project_id = ? AND source_locator = ? LIMIT 1",
            (int(target["id"]), marker),
        ).fetchone()
        if exists is None:
            return None
        return _appendix_copy_result(
            db, target, source_uuid, operation_key, repeated=True
        )


def _appendix_copy_result(
    db: sqlite3.Connection,
    target: sqlite3.Row,
    source_uuid: str,
    operation_key: str,
    *,
    repeated: bool,
) -> ReportAppendixACopyRead:
    project_id = int(target["id"])
    row_count = int(
        db.execute(
            """
            SELECT COUNT(*) FROM assessment_rows r
            JOIN appendix_sections s ON s.id = r.section_id
            WHERE s.project_id = ?
            """,
            (project_id,),
        ).fetchone()[0]
    )
    image_count = int(
        db.execute(
            "SELECT COUNT(*) FROM evidence_images WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
    )
    return ReportAppendixACopyRead(
        target_project_uuid=str(target["project_uuid"]),
        source_project_uuid=source_uuid,
        idempotency_key=operation_key,
        copied_row_count=row_count,
        copied_image_count=image_count,
        repeated=repeated,
    )


def _assert_empty_appendix_target(db: sqlite3.Connection, project_id: int) -> None:
    counts = {
        "assessment_rows": int(
            db.execute(
                """
                SELECT COUNT(*) FROM assessment_rows r
                JOIN appendix_sections s ON s.id = r.section_id
                WHERE s.project_id = ?
                """,
                (project_id,),
            ).fetchone()[0]
        ),
        "evidence_images": int(
            db.execute(
                "SELECT COUNT(*) FROM evidence_images WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        ),
        "section_subsystems": int(
            db.execute(
                "SELECT COUNT(*) FROM section_subsystems WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        ),
        "assessment_objects": int(
            db.execute(
                "SELECT COUNT(*) FROM assessment_objects WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        ),
        "result_correction_relations": int(
            db.execute(
                "SELECT COUNT(*) FROM result_correction_relations WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        ),
    }
    if any(counts.values()):
        raise ReportImportServiceError(
            "APPENDIX_A_COPY_TARGET_NOT_EMPTY",
            "目标完整报告已经包含附录 A 数据，不能重复覆盖。",
        )


def _appendix_candidate_error_count(db: sqlite3.Connection, project_id: int) -> int:
    errors = 0
    score_rows_by_section: dict[str, list[dict[str, Any]]] = {}
    sections = db.execute(
        "SELECT id, code FROM appendix_sections WHERE project_id = ? ORDER BY sort_order",
        (project_id,),
    ).fetchall()
    if len(sections) != 8:
        errors += abs(8 - len(sections)) or 1
    technical_codes = {"A-1", "A-2", "A-3", "A-4"}
    for section in sections:
        rows = db.execute(
            """
            SELECT r.*, m.d, m.a, m.k, m.ra, m.rk, m.object_score, m.unit_score, m.compliance
            FROM assessment_rows r
            LEFT JOIN metric_results m ON m.row_id = r.id
            WHERE r.section_id = ?
            ORDER BY r.sort_order, r.id
            """,
            (int(section["id"]),),
        ).fetchall()
        score_rows_by_section[str(section["code"])] = [dict(row) for row in rows]
        if not rows:
            errors += 1
            continue
        for row in rows:
            if any(not str(row[field] or "").strip() for field in ("unit", "object_name", "record_text")):
                errors += 1
            if section["code"] in technical_codes:
                if any(str(row[field] or "").strip() not in {"√", "×", "/"} for field in ("d", "a", "k")):
                    errors += 1
                if str(row["ra"] or "").strip() not in {"1", "0.5", "0.2"}:
                    errors += 1
                if str(row["rk"] or "").strip() not in {"1", "1.2"}:
                    errors += 1
                if not _valid_score(row["object_score"]):
                    errors += 1
            elif str(row["compliance"] or "").strip() not in {"符合", "部分符合", "不符合", "不适用"}:
                errors += 1
            if not _valid_score(row["unit_score"]):
                errors += 1
        if section["code"] not in technical_codes:
            expected_names = set(database.fixed_object_names_for_section(str(section["code"])))
            names_by_unit: dict[str, set[str]] = {}
            for row in rows:
                names_by_unit.setdefault(str(row["unit"] or "").strip(), set()).add(
                    str(row["object_name"] or "").strip()
                )
            errors += sum(names != expected_names for names in names_by_unit.values())
    for image in db.execute(
        "SELECT file_path FROM evidence_images WHERE project_id = ?", (project_id,)
    ).fetchall():
        relative = Path(str(image["file_path"] or ""))
        root = settings.storage_path.resolve()
        try:
            candidate = (root / relative).resolve(strict=True)
        except (OSError, RuntimeError):
            errors += 1
            continue
        if relative.is_absolute() or not candidate.is_file() or not candidate.is_relative_to(root):
            errors += 1
    errors += int(
        db.execute(
            """
            SELECT COUNT(*)
            FROM cross_references c
            JOIN assessment_rows r ON r.id = c.source_row_id
            JOIN appendix_sections s ON s.id = r.section_id
            LEFT JOIN evidence_images e
              ON e.id = c.target_image_id
             AND e.project_id = s.project_id
             AND e.section_code = s.code
            WHERE s.project_id = ?
              AND (c.target_image_id IS NULL OR e.id IS NULL OR INSTR(r.record_text, c.token) = 0)
            """,
            (project_id,),
        ).fetchone()[0]
    )
    errors += _orphan_figure_token_count(db, project_id)
    from .xlsx_generator.generator import validate_score_workbook_rows

    errors += len(validate_score_workbook_rows(score_rows_by_section))
    return errors


def _orphan_figure_token_count(db: sqlite3.Connection, project_id: int) -> int:
    """统计没有同项目、同章节引用记录支撑的正文图片 token。"""

    errors = 0
    rows = db.execute(
        """
        SELECT r.id, r.record_text
        FROM assessment_rows r
        JOIN appendix_sections s ON s.id = r.section_id
        WHERE s.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    for row in rows:
        tokens = {match.group(0): int(match.group(1)) for match in database.FIG_TOKEN_RE.finditer(str(row["record_text"] or ""))}
        if not tokens:
            continue
        references = db.execute(
            """
            SELECT c.token, c.target_image_id
            FROM cross_references c
            JOIN assessment_rows r ON r.id = c.source_row_id
            JOIN appendix_sections s ON s.id = r.section_id
            JOIN evidence_images e
              ON e.id = c.target_image_id
             AND e.project_id = s.project_id
             AND e.section_code = s.code
            WHERE c.source_row_id = ? AND s.project_id = ?
            """,
            (int(row["id"]), project_id),
        ).fetchall()
        valid = {
            str(reference["token"]): int(reference["target_image_id"])
            for reference in references
            if reference["target_image_id"] is not None
        }
        errors += sum(valid.get(token) != image_id for token, image_id in tokens.items())
    return errors


def _revalidate_appendix_source(
    db: sqlite3.Connection,
    *,
    expected_source: sqlite3.Row,
    expected_uuid: str,
    expected_images: list[sqlite3.Row],
) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    """在目标写事务内重新确认附录来源没有发生并发漂移。"""

    current = database.get_project_by_uuid(expected_uuid, db)
    if (
        current is None
        or current["project_type"] != "appendix_a"
        or int(current["id"]) != int(expected_source["id"])
        or str(current["project_uuid"]) != str(expected_source["project_uuid"])
        or _appendix_candidate_error_count(db, int(current["id"]))
    ):
        raise ReportImportServiceError(
            "SOURCE_PROJECT_CHANGED",
            "附录 A 源项目已发生变化，请重新选择后再试。",
        )
    images = db.execute(
        "SELECT * FROM evidence_images WHERE project_id = ? ORDER BY section_code, sort_order, id",
        (int(current["id"]),),
    ).fetchall()
    if project_service._image_record_signature(images) != project_service._image_record_signature(
        expected_images
    ):
        raise ReportImportServiceError(
            "SOURCE_PROJECT_CHANGED",
            "附录 A 源项目已发生变化，请重新选择后再试。",
        )
    return current, images


def _valid_score(value: Any) -> bool:
    text = str(value or "").strip()
    if text == "/":
        return True
    if not text:
        return False
    try:
        Decimal(text)
    except InvalidOperation:
        return False
    return True


def _validate_confirm_blockers(job_id: int, appendix_source: str, parsed: dict[str, Any]) -> None:
    with database.connect() as db:
        blockers = db.execute(
            "SELECT code FROM report_import_issues WHERE job_id = ? AND status = 'open' AND blocks_confirmation = 1",
            (job_id,),
        ).fetchall()
    codes = [str(item["code"]) for item in blockers]
    non_appendix = [code for code in codes if not code.startswith("APPENDIX_A_")]
    if non_appendix:
        raise ReportImportServiceError("REPORT_IMPORT_BLOCKED", "迁移预览存在不能绕过的错误。")
    if appendix_source == "document":
        appendix = parsed.get("appendix_a") or {}
        if not appendix.get("complete"):
            raise ReportImportServiceError("APPENDIX_A_INCOMPLETE", "DOCX 中的附录 A 不完整。")


def _validate_accepted_resolutions(job_id: int, accepted_ids: set[int]) -> None:
    with database.connect() as db:
        rows = db.execute(
            "SELECT id, action FROM report_import_resolutions WHERE job_id = ?",
            (job_id,),
        ).fetchall()
    found = {
        int(row["id"]): str(row["action"])
        for row in rows
        if int(row["id"]) in accepted_ids
    }
    if set(found) != accepted_ids or any(action != "adopt_candidate" for action in found.values()):
        raise ReportImportServiceError(
            "REPORT_IMPORT_RESOLUTION_SELECTION_INVALID",
            "确认请求包含不存在、越权或非采用类型的解决项。",
            status_code=422,
        )
    required_ids = {
        int(row["id"])
        for row in rows
        if str(row["action"]) == "adopt_candidate"
    }
    if accepted_ids != required_ids:
        raise ReportImportServiceError(
            "REPORT_IMPORT_RESOLUTION_SELECTION_INCOMPLETE",
            "所有采用候选的解决项都必须在创建项目时明确确认。",
            status_code=422,
        )


def _prepare_existing_appendix(
    source_project_uuid: str,
    job_id: int | str,
) -> tuple[sqlite3.Row, list[sqlite3.Row], Path, dict[int, Path]]:
    with database.connect() as db:
        source = database.get_project_by_uuid(source_project_uuid, db)
        if source is None or source["project_type"] != "appendix_a":
            raise ReportImportServiceError("APPENDIX_A_SOURCE_NOT_FOUND", "附录 A 源项目不存在。", status_code=404)
        section_count = int(
            db.execute(
                """
                SELECT COUNT(*) FROM appendix_sections s
                WHERE s.project_id = ?
                  AND EXISTS (SELECT 1 FROM assessment_rows r WHERE r.section_id = s.id)
                """,
                (int(source["id"]),),
            ).fetchone()[0]
        )
        if section_count != 8 or _appendix_candidate_error_count(db, int(source["id"])):
            raise ReportImportServiceError("APPENDIX_A_SOURCE_INCOMPLETE", "附录 A 源项目尚不完整。")
        images = db.execute(
            "SELECT * FROM evidence_images WHERE project_id = ? ORDER BY section_code, sort_order, id",
            (int(source["id"]),),
        ).fetchall()
    staging_dir = settings.runtime_paths.migration_path / "report-imports" / str(job_id) / uuid.uuid4().hex
    try:
        staged = project_service._stage_evidence_files(images, staging_dir)
    except project_service.ProjectServiceError as exc:
        _remove_staging(staging_dir)
        raise ReportImportServiceError(
            exc.code, exc.message, status_code=exc.status_code
        ) from exc
    except Exception:
        _remove_staging(staging_dir)
        raise
    return source, images, staging_dir, staged


def _insert_document_appendix(
    db: sqlite3.Connection,
    project_id: int,
    appendix: dict[str, Any],
    timestamp: str,
) -> None:
    sections = appendix.get("sections") or []
    if len(sections) != 8:
        raise ReportImportServiceError("APPENDIX_A_INCOMPLETE", "DOCX 中的附录 A 不完整。")
    for section in sections:
        code = str(section.get("code") or "")
        target = database.get_section(project_id, code, db)
        if target is None:
            raise ReportImportServiceError("APPENDIX_A_SECTION_INVALID", "附录 A 章节无法映射。")
        prepared = database.prepare_section_rows(code, list(section.get("rows") or []), strict=False)
        for index, row in enumerate(prepared, start=1):
            cursor = db.execute(
                """
                INSERT INTO assessment_rows (
                    section_id, unit, object_name, subsystem, record_text,
                    sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(target["id"]), row.get("unit") or "", row.get("object_name") or "",
                    row.get("subsystem") or "", row.get("record_text") or "",
                    int(row.get("sort_order") or index), timestamp, timestamp,
                ),
            )
            metric = dict(row.get("metric_result") or {})
            db.execute(
                """
                INSERT INTO metric_results (row_id, d, a, k, ra, rk, object_score, unit_score, compliance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(cursor.lastrowid), metric.get("d"), metric.get("a"), metric.get("k"),
                    metric.get("ra"), metric.get("rk"), metric.get("object_score"),
                    metric.get("unit_score"), metric.get("compliance"),
                ),
            )


def _recalculate_appendix_scores(db: sqlite3.Connection, project_id: int) -> None:
    for section in db.execute(
        "SELECT id, code FROM appendix_sections WHERE project_id = ? ORDER BY sort_order",
        (project_id,),
    ).fetchall():
        rows = db.execute(
            """
            SELECT r.*, m.d, m.a, m.k, m.ra, m.rk, m.object_score, m.unit_score, m.compliance
            FROM assessment_rows r
            LEFT JOIN metric_results m ON m.row_id = r.id
            WHERE r.section_id = ?
            ORDER BY r.sort_order, r.id
            """,
            (int(section["id"]),),
        ).fetchall()
        payload = [
            {
                "id": int(row["id"]),
                "unit": row["unit"],
                "object_name": row["object_name"],
                "subsystem": row["subsystem"],
                "record_text": row["record_text"],
                "sort_order": row["sort_order"],
                "metric_result": {
                    "d": row["d"], "a": row["a"], "k": row["k"],
                    "ra": row["ra"], "rk": row["rk"],
                    "object_score": None, "unit_score": None,
                    "compliance": row["compliance"],
                },
            }
            for row in rows
        ]
        prepared = database.prepare_section_rows(str(section["code"]), payload, strict=False)
        if len(prepared) != len(rows):
            raise ReportImportServiceError(
                "APPENDIX_A_SOURCE_INCOMPLETE",
                "附录 A 固定对象结构不完整，无法安全重算。",
            )
        prepared_by_id = {int(item["id"]): item for item in prepared}
        if set(prepared_by_id) != {int(row["id"]) for row in rows}:
            raise ReportImportServiceError(
                "APPENDIX_A_SCORE_RECALCULATION_FAILED",
                "附录 A 评分重算无法稳定对应原始记录。",
            )
        for row in rows:
            metric = prepared_by_id[int(row["id"])].get("metric_result") or {}
            db.execute(
                "UPDATE metric_results SET object_score = ?, unit_score = ? WHERE row_id = ?",
                (metric.get("object_score"), metric.get("unit_score"), int(row["id"])),
            )


def _apply_field_candidates(
    db: sqlite3.Connection,
    *,
    job_id: int,
    project_id: int,
    project_uuid: str,
    accepted_resolution_ids: set[int],
    keep_unresolved_original: bool,
    timestamp: str,
) -> None:
    resolutions = {
        int(row["issue_id"]): row
        for row in db.execute("SELECT * FROM report_import_resolutions WHERE job_id = ?", (job_id,)).fetchall()
        if row["action"] != "adopt_candidate" or int(row["id"]) in accepted_resolution_ids
    }
    issues = db.execute("SELECT * FROM report_import_issues WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
    for issue in issues:
        resolution = resolutions.get(int(issue["id"]))
        candidate = _load_json(issue["candidate_value_json"], None)
        action = None
        value = candidate
        if issue["code"] == "AUTO_MAPPED_FIELD" and issue["status"] == "resolved":
            action = "adopt_candidate"
        elif resolution is not None:
            action = str(resolution["action"])
            if resolution["resolved_value_json"] is not None:
                value = _load_json(resolution["resolved_value_json"], None)

        mapping_status = "comparison_only"
        source_kind = "unmapped"
        needs_confirmation = bool(issue["needs_confirmation"] and issue["status"] == "open")
        if action == "skip":
            mapping_status = "skipped"
        elif action == "adopt_candidate" and issue["association_id"] and issue["authority_field_id"]:
            if _write_fact(db, project_id, project_uuid, str(issue["authority_field_id"]), value, timestamp):
                mapping_status = "adopted"
                source_kind = "imported"
                needs_confirmation = False
            else:
                if resolution is not None:
                    raise ReportImportServiceError(
                        "REPORT_IMPORT_RESOLUTION_VALUE_INVALID",
                        "已确认采用的解决值未能写入目标字段。",
                        status_code=422,
                    )
                mapping_status = "pending"
                source_kind = "imported_manual_draft"
                needs_confirmation = True
        elif keep_unresolved_original and (issue["original_text"] or issue["candidate_value_json"]):
            mapping_status = "pending"
            source_kind = "imported_manual_draft" if issue["association_id"] else "unmapped"

        if not (issue["original_text"] or issue["candidate_value_json"] or issue["association_id"]):
            continue
        db.execute(
            """
            INSERT INTO report_field_sources (
                project_id, report_import_job_id, association_id, authority_field_id,
                field_path, source_kind, source_locator, source_value_hash, original_text,
                confidence, mapping_status, needs_confirmation, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id, job_id, issue["association_id"], issue["authority_field_id"],
                issue["field_path"], source_kind, issue["source_locator"], issue["source_value_hash"],
                issue["original_text"], issue["confidence"], mapping_status,
                int(needs_confirmation), timestamp, timestamp,
            ),
        )


def _write_fact(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    field_id: str,
    value: Any,
    timestamp: str,
) -> bool:
    text = "" if value is None else str(value).strip()
    if field_id == "report.identity.number":
        db.execute(
            "UPDATE report_metadata SET report_number = ?, revision = revision + 1, updated_at = ? WHERE project_id = ?",
            (text, timestamp, project_id),
        )
        return True
    if field_id == "report.system.name":
        db.execute(
            "UPDATE system_profiles SET system_name = ?, revision = revision + 1, updated_at = ? WHERE project_id = ?",
            (text, timestamp, project_id),
        )
        return True
    if field_id == "report.system.overview":
        db.execute(
            "UPDATE system_profiles SET system_summary = ?, revision = revision + 1, updated_at = ? WHERE project_id = ?",
            (text, timestamp, project_id),
        )
        return True
    if field_id in {"report.organization.assessed_name", "report.organization.client_name"}:
        organization_type = "assessed" if field_id.endswith("assessed_name") else "client"
        existing = db.execute(
            "SELECT id FROM report_organizations WHERE project_id = ? AND organization_type = ? AND active = 1 ORDER BY sort_order LIMIT 1",
            (project_id, organization_type),
        ).fetchone()
        if existing is not None:
            db.execute(
                "UPDATE report_organizations SET name = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                (text, timestamp, int(existing["id"])),
            )
        else:
            db.execute(
                """
                INSERT INTO report_organizations (
                    organization_uuid, project_id, organization_type, name, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 2, ?, ?)
                """,
                (str(uuid.uuid5(uuid.UUID(project_uuid), f"organization:{organization_type}")), project_id, organization_type, text, timestamp, timestamp),
            )
        return True
    if field_id in FACT_DISTRIBUTION_COLUMNS:
        match = re.search(r"\d+", text)
        if not match:
            return False
        column = FACT_DISTRIBUTION_COLUMNS[field_id]
        db.execute(
            f"UPDATE report_distribution SET {column} = ?, revision = revision + 1, updated_at = ? WHERE project_id = ?",
            (int(match.group()), timestamp, project_id),
        )
        return True
    if field_id in FACT_PHASE_COLUMNS:
        values = _date_values(text)
        if len(values) != 2:
            return False
        start, end = FACT_PHASE_COLUMNS[field_id]
        db.execute(
            f"UPDATE report_phase_dates SET {start} = ?, {end} = ?, revision = revision + 1, updated_at = ? WHERE project_id = ?",
            (values[0], values[1], timestamp, project_id),
        )
        return True
    return False


def _insert_appendix_source_records(
    db: sqlite3.Connection,
    *,
    job_id: int | None,
    project_id: int,
    appendix_source: str,
    source_project_uuid: str | None,
    appendix_payload: dict[str, Any],
    timestamp: str,
    source_locator_override: str | None = None,
) -> None:
    matrix = load_default_field_matrix()
    bindings = {item.field_id: item for item in matrix.fields}
    field_ids = (
        "report.appendix_a.records",
        "report.appendix_a.technical_inputs",
        "report.appendix_a.management_inputs",
    )
    for field_id in field_ids:
        relation = next(
            (
                item for item in matrix.relations
                if item.authority_field_id == field_id or field_id in item.reference_field_ids
            ),
            None,
        )
        if relation is None:
            raise ReportImportServiceError(
                "REPORT_IMPORT_MAPPING_INVALID",
                "附录 A 来源无法关联当前 R2 字段矩阵。",
            )
        binding = bindings[field_id]
        base_locator = source_locator_override or (
            f"project:{source_project_uuid}/appendix:A"
            if source_project_uuid
            else "appendix:A"
        )
        for field_path in binding.entity_paths:
            if appendix_source == "existing_project":
                source_kind = "copied"
            elif field_path.endswith((".ra", ".rk")):
                source_kind = "defaulted"
            else:
                source_kind = "imported"
            original = _json(
                {
                    "sections": [item.get("code") for item in appendix_payload.get("sections", [])],
                    "source": appendix_source,
                    "field_path": field_path,
                }
            )
            db.execute(
                """
                INSERT INTO report_field_sources (
                    project_id, report_import_job_id, association_id, authority_field_id,
                    field_path, source_kind, source_locator, source_value_hash, original_text,
                    confidence, mapping_status, needs_confirmation, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'exact', 'adopted', 0, ?, ?)
                """,
                (
                    project_id, job_id, relation.relation_id, field_id, field_path,
                    source_kind, base_locator,
                    hashlib.sha256(original.encode("utf-8")).hexdigest(), original,
                    timestamp, timestamp,
                ),
            )


def _validate_issue_mapping(issue: sqlite3.Row, relations: dict[str, Any]) -> None:
    association_id = issue["association_id"]
    field_id = issue["authority_field_id"]
    if association_id is None or field_id is None:
        if issue["confidence"] == "unmapped":
            return
        raise ReportImportServiceError("REPORT_IMPORT_MAPPING_INVALID", "待确认项缺少 R2 映射。")
    relation = relations.get(str(association_id))
    if relation is None or field_id not in (relation.authority_field_id, *relation.reference_field_ids):
        raise ReportImportServiceError("REPORT_IMPORT_MAPPING_INVALID", "待确认项的 R2 映射已失效。")


def _date_values(value: str) -> list[str]:
    output: list[str] = []
    pattern = re.compile(r"(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})")
    for year, month, day in pattern.findall(value):
        output.append(f"{int(year):04d}-{int(month):02d}-{int(day):02d}")
    return output


def _update_job(job_id: int, **values: Any) -> None:
    with database.connect() as db:
        _update_job_locked(db, job_id, **values)


def _update_job_locked(db: sqlite3.Connection, job_id: int, **values: Any) -> None:
    allowed = {
        "status", "job_revision", "source_docx_path", "source_sha256", "detected_edition",
        "detected_revision", "fingerprint_json", "parsed_json_path", "summary_json",
        "appendix_a_source", "created_project_id", "error_message", "started_at", "finished_at",
    }
    updates = {key: value for key, value in values.items() if key in allowed}
    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    db.execute(f"UPDATE report_import_jobs SET {assignments} WHERE id = ?", (*updates.values(), job_id))


def _get_job_row(job_id: int) -> sqlite3.Row | None:
    with database.connect() as db:
        return db.execute("SELECT * FROM report_import_jobs WHERE id = ?", (job_id,)).fetchone()


def _load_parsed(job: sqlite3.Row) -> dict[str, Any]:
    relative = str(job["parsed_json_path"] or "")
    if not relative:
        raise ReportImportServiceError("REPORT_IMPORT_PARSED_DATA_MISSING", "迁移解析结果不存在。")
    path = _storage_child(relative)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportImportServiceError("REPORT_IMPORT_PARSED_DATA_INVALID", "迁移解析结果无法读取。") from exc
    if not isinstance(value, dict):
        raise ReportImportServiceError("REPORT_IMPORT_PARSED_DATA_INVALID", "迁移解析结果格式无效。")
    return value


def _job_dir(job_id: int) -> Path:
    return settings.storage_path / REPORT_IMPORT_DIR / str(job_id)


def _relative_storage_path(path: Path) -> str:
    return path.resolve().relative_to(settings.storage_path.resolve()).as_posix()


def _storage_child(relative: str) -> Path:
    if not relative.strip() or Path(relative).is_absolute():
        raise ReportImportServiceError("REPORT_IMPORT_STORAGE_PATH_INVALID", "迁移存储路径无效。")
    root = settings.storage_path.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ReportImportServiceError("REPORT_IMPORT_STORAGE_PATH_INVALID", "迁移存储路径越界。")
    return target


def _remove_staging(path: Path) -> None:
    root = settings.runtime_paths.migration_path.resolve()
    target = path.resolve()
    if target.exists() and target.is_relative_to(root):
        shutil.rmtree(target)


def _clean_filename(value: str | None) -> str:
    name = Path(value or "source.docx").name.strip()
    return name[:255] or "source.docx"


def _api_candidate(raw: str | None) -> Any:
    if raw is None:
        return None
    if len(raw) > API_CANDIDATE_JSON_LIMIT:
        return {"truncated": True, "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
    return _load_json(raw, None)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
