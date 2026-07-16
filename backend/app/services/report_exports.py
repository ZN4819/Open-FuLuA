"""Asynchronous complete-report export jobs and immutable snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .. import database
from ..config import settings
from ..report_derived.rules import canonical_json
from ..report_export.context import build_assembly_context, validate_for_export
from ..report_export.renderer import render_report, validate_rendered_report
from ..report_export.word import WordRefreshError, refresh_with_word
from ..report_schemas import ReportExportJobWrite
from . import report_context, report_evidence
from .report_domain.common import require_report_project
from .report_domain.errors import ReportDomainError
from .report_templates.registry import report_template_registry


VERSION_RE = re.compile(r"^V[0-9]+(?:\.[0-9]+){1,3}$", re.IGNORECASE)
WORD_PATH_CHARACTER_LIMIT = 245
DEFAULT_REPORT_FILENAME_LIMIT = 220
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _windows_utf16_units(value: str) -> int:
    """Return the number of UTF-16 code units used by a Windows path string."""
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _truncate_utf16(value: str, max_units: int) -> str:
    """Truncate without splitting a Unicode code point at the UTF-16 budget."""
    if max_units <= 0:
        return ""
    if _windows_utf16_units(value) <= max_units:
        return value

    lower = 0
    upper = len(value)
    while lower < upper:
        midpoint = (lower + upper + 1) // 2
        if _windows_utf16_units(value[:midpoint]) <= max_units:
            lower = midpoint
        else:
            upper = midpoint - 1
    return value[:lower]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _safe_component(value: Any) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(value or "")).strip(" ._")
    cleaned = re.sub(r"_+", "_", cleaned).replace("（客户复核版）", "").replace("(客户复核版)", "")
    if cleaned.upper() in WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return _truncate_utf16(cleaned, 80).rstrip(" .")


def report_filename(
    context: dict[str, Any],
    *,
    max_length: int = DEFAULT_REPORT_FILENAME_LIMIT,
) -> str:
    scalars = context["scalar_slot_values"]
    identity = context["project_identity"]
    components = (
        _safe_component(scalars.get("report_number")) or "未编号",
        _safe_component(scalars.get("assessed_name")) or "未填写被测单位",
        _safe_component(scalars.get("system_name")) or "未填写系统名称",
    )
    version = _safe_component(identity["export_version"])
    suffix = "-草稿" if identity["export_mode"] == "draft" else ""
    base = f"{components[0]}-{components[1]}-{components[2]}商用密码应用安全性评估报告"
    stem = f"{base}{version}{suffix}"
    filename = f"{stem}.docx"
    if _windows_utf16_units(filename) <= max_length:
        return filename
    digest = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:10]
    tail = f"-{digest}-{version}{suffix}.docx"
    prefix_budget = max_length - _windows_utf16_units(tail)
    if prefix_budget < 12:
        raise ReportDomainError(
            "REPORT_EXPORT_PATH_TOO_LONG",
            "报告导出目录过深，Microsoft Word 无法安全刷新，请缩短客户端数据目录。",
            status_code=500,
        )
    prefix = _truncate_utf16(base, prefix_budget).rstrip(" .-")
    return f"{prefix}{tail}"


def _word_safe_report_filename(context: dict[str, Any], directory: Path) -> str:
    directory_length = _windows_utf16_units(str(directory.resolve())) + 1
    max_length = min(
        DEFAULT_REPORT_FILENAME_LIMIT,
        WORD_PATH_CHARACTER_LIMIT - directory_length,
    )
    return report_filename(context, max_length=max_length)


def _job_result(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "job_uuid": row["job_uuid"],
        "project_id": int(row["project_id"]),
        "mode": row["export_mode"],
        "version": row["export_version"],
        "status": row["status"],
        "project_revision": int(row["project_revision"]),
        "template_package_id": row["template_package_id"],
        "template_asset_set_hash": row["template_asset_set_hash"],
        "template_docx_hash": row["template_docx_hash"],
        "r2_context_hash": row["r2_context_hash"],
        "r3_context_hash": row["r3_context_hash"],
        "assembly_context_hash": row["assembly_context_hash"],
        "snapshot_uuid": row["snapshot_uuid"],
        "docx_hash": row["docx_hash"],
        "page_count": row["page_count"],
        "word_refresh_status": row["word_refresh_status"],
        "issues": _loads(row["issues_json"], []),
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "download_available": row["status"] == "succeeded" and bool(row["output_relative_path"]),
    }


def create_export_job(project_uuid: str, payload: ReportExportJobWrite) -> dict[str, Any]:
    version = payload.version.strip().upper()
    if not VERSION_RE.fullmatch(version):
        raise ReportDomainError(
            "REPORT_EXPORT_VERSION_INVALID", "版本号应使用 V1.0 形式。", status_code=422,
            project_uuid=project_uuid, field="version",
        )
    package = report_template_registry.load()
    template_hash = hashlib.sha256(package.runtime_template_bytes).hexdigest()
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        project = require_report_project(project_uuid, db)
        state = db.execute(
            "SELECT project_revision FROM report_generation_state WHERE project_id = ?",
            (project["id"],),
        ).fetchone()
        current_revision = int(state["project_revision"]) if state else 1
        if payload.expected_project_revision != current_revision:
            raise ReportDomainError(
                "REVISION_CONFLICT", "项目 revision 已变化，请刷新后重试。", status_code=409,
                project_uuid=project_uuid,
                details={"expected_revision": payload.expected_project_revision, "current_revision": current_revision},
            )
        job_uuid = str(uuid.uuid4())
        timestamp = database.utc_now()
        try:
            db.execute(
                """
                INSERT INTO report_export_jobs (
                    job_uuid, project_id, export_mode, export_version, status,
                    project_revision, template_package_id, template_asset_set_hash,
                    template_docx_hash, created_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    job_uuid, project["id"], payload.mode, version,
                    current_revision, package.package_id, package.asset_set_hash,
                    template_hash, timestamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ReportDomainError(
                "REPORT_EXPORT_ALREADY_RUNNING", "同一项目 revision 和模式已有导出任务正在运行。",
                status_code=409, project_uuid=project_uuid,
            ) from exc
        row = db.execute("SELECT * FROM report_export_jobs WHERE job_uuid = ?", (job_uuid,)).fetchone()
        return _job_result(row)


def get_export_job(job_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        row = db.execute("SELECT * FROM report_export_jobs WHERE job_uuid = ?", (job_uuid,)).fetchone()
        if row is None:
            raise ReportDomainError("REPORT_EXPORT_JOB_NOT_FOUND", "完整报告导出任务不存在。", status_code=404)
        return _job_result(row)


def get_export_issues(job_uuid: str) -> dict[str, Any]:
    job = get_export_job(job_uuid)
    issues = job["issues"]
    return {
        "job_uuid": job_uuid,
        "status": job["status"],
        "errors": [item for item in issues if item.get("severity") == "error"],
        "warnings": [item for item in issues if item.get("severity") == "warning"],
        "info": [item for item in issues if item.get("severity") == "info"],
    }


def _write_snapshot(context: dict[str, Any], job_uuid: str) -> tuple[str, str, str]:
    project_uuid = str(context["project_identity"]["project_uuid"])
    snapshot_uuid = str(uuid.uuid4())
    directory = settings.storage_path / "report-snapshots" / project_uuid / snapshot_uuid
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "context.json"
    data = canonical_json(context).encode("utf-8")
    temporary = directory / "context.json.tmp"
    temporary.write_bytes(data)
    os.replace(temporary, path)
    relative = path.resolve().relative_to(settings.storage_path.resolve()).as_posix()
    return snapshot_uuid, relative, hashlib.sha256(data).hexdigest()


def _insert_snapshot(
    context: dict[str, Any],
    job_uuid: str,
    snapshot_uuid: str,
    relative_path: str,
    context_hash: str,
) -> None:
    identity = context["project_identity"]
    template = context["template_binding"]
    r3 = context["r3_context"]
    with database.connect() as db:
        db.execute(
            """
            INSERT INTO report_export_snapshots (
                snapshot_uuid, project_id, job_uuid, project_revision, export_mode,
                export_version, context_relative_path, context_hash,
                template_package_id, template_asset_set_hash, template_docx_hash,
                r2_context_hash, r3_schema_version, r3_rule_set_id,
                r3_rule_set_hash, r3_context_hash, validation_summary_json,
                warning_summary_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_uuid, identity["project_id"], job_uuid, identity["project_revision"],
                identity["export_mode"], identity["export_version"], relative_path, context_hash,
                template["package_id"], template["asset_set_hash"], template["runtime_template_sha256"],
                context["r2_context_hash"], str(r3.get("schema_version") or "1.0"),
                str(r3.get("rule_set_id") or "unavailable"), str(r3.get("rule_set_hash") or "0" * 64),
                context["r3_context_hash"], _json(context["validation_summary"]),
                _json(context["warning_summary"]), database.utc_now(),
            ),
        )
        db.execute(
            """
            UPDATE report_export_jobs
            SET r2_context_hash = ?, r3_context_hash = ?, assembly_context_hash = ?,
                snapshot_uuid = ?, issues_json = ?
            WHERE job_uuid = ? AND status = 'running'
            """,
            (
                context["r2_context_hash"], context["r3_context_hash"], context["assembly_context_hash"],
                snapshot_uuid, _json(context["validation_summary"]["issues"]),
                job_uuid,
            ),
        )


def _fail_job(job_uuid: str, exc: Exception) -> None:
    if isinstance(exc, ReportDomainError):
        code, message = exc.code, exc.message
        issues = exc.details.get("issues") if isinstance(exc.details, dict) else None
    elif isinstance(exc, WordRefreshError):
        code, message, issues = exc.code, str(exc), None
    else:
        code, message, issues = "REPORT_EXPORT_FAILED", str(exc), None
    with database.connect() as db:
        existing = db.execute("SELECT issues_json FROM report_export_jobs WHERE job_uuid = ?", (job_uuid,)).fetchone()
        current = _loads(existing["issues_json"], []) if existing else []
        if isinstance(issues, list):
            current = issues
        current.append({"severity": "error", "code": code, "message": message})
        db.execute(
            """
            UPDATE report_export_jobs
            SET status = 'failed', issues_json = ?, error_code = ?, error_message = ?,
                word_refresh_status = CASE WHEN ? LIKE 'WORD_%' THEN 'failed' ELSE word_refresh_status END,
                finished_at = ?
            WHERE job_uuid = ? AND status IN ('queued', 'running')
            """,
            (_json(current), code, message, code, database.utc_now(), job_uuid),
        )


def process_export_job(job_uuid: str) -> None:
    staging: Path | None = None
    published: Path | None = None
    try:
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute(
                """
                SELECT j.*, p.project_uuid
                FROM report_export_jobs j JOIN projects p ON p.id = j.project_id
                WHERE j.job_uuid = ?
                """,
                (job_uuid,),
            ).fetchone()
            if job is None:
                return
            if job["status"] != "queued":
                return
            db.execute(
                "UPDATE report_export_jobs SET status = 'running', started_at = ? WHERE job_uuid = ? AND status = 'queued'",
                (database.utc_now(), job_uuid),
            )
            project_uuid = str(job["project_uuid"])
            mode = str(job["export_mode"])
            version = str(job["export_version"])
            revision = int(job["project_revision"])

        context = build_assembly_context(
            project_uuid, mode=mode, version=version, expected_project_revision=revision
        )
        snapshot_uuid, snapshot_path, snapshot_hash = _write_snapshot(context, job_uuid)
        _insert_snapshot(context, job_uuid, snapshot_uuid, snapshot_path, snapshot_hash)

        project_root = settings.storage_path / "exports" / project_uuid
        final_dir = project_root / job_uuid
        staging = project_root / f".{job_uuid}.tmp"
        if final_dir.exists() or staging.exists():
            raise ReportDomainError("REPORT_EXPORT_PATH_CONFLICT", "导出任务目录已存在。", status_code=500)
        staging.mkdir(parents=True, exist_ok=False)
        initial = staging / "assembly.docx"
        full_filename = report_filename(context)
        safe_filename = _word_safe_report_filename(context, staging)
        refreshed = staging / safe_filename
        if safe_filename != full_filename:
            with database.connect() as db:
                row = db.execute(
                    "SELECT issues_json FROM report_export_jobs WHERE job_uuid = ?",
                    (job_uuid,),
                ).fetchone()
                issues = _loads(row["issues_json"], [])
                issues.append(
                    {
                        "severity": "warning",
                        "code": "REPORT_EXPORT_FILENAME_SHORTENED",
                        "message": "为兼容 Microsoft Word 路径长度限制，导出文件名已确定性缩短。",
                    }
                )
                db.execute(
                    "UPDATE report_export_jobs SET issues_json = ? WHERE job_uuid = ?",
                    (_json(issues), job_uuid),
                )
        render_report(context, initial)

        word_status = "not_started"
        page_count = None
        try:
            state = refresh_with_word(
                initial, refreshed, status_path=staging / "word-status.json"
            )
            word_status = "succeeded"
            page_count = int(state.get("page_count") or 0) or None
            validate_rendered_report(refreshed, final=mode == "final")
        except WordRefreshError as exc:
            if mode == "final":
                raise
            word_status = "skipped"
            shutil.copy2(initial, refreshed)
            with database.connect() as db:
                row = db.execute("SELECT issues_json FROM report_export_jobs WHERE job_uuid = ?", (job_uuid,)).fetchone()
                issues = _loads(row["issues_json"], [])
                issues.append({"severity": "warning", "code": exc.code, "message": "草稿未完成 Microsoft Word 字段刷新。"})
                db.execute("UPDATE report_export_jobs SET issues_json = ? WHERE job_uuid = ?", (_json(issues), job_uuid))

        # Word automation may be long-running. Do not publish a document built
        # from an obsolete R2/R3/R5 snapshot if another window changed the
        # report while Word was refreshing fields and repaginating.
        report_context.assert_context_current(
            project_uuid,
            expected_revision=revision,
            expected_project_updated_at=str(context["r2_context"]["project"]["updated_at"]),
        )
        current_appendix_b = report_evidence.build_projection(
            project_uuid,
            expected_project_revision=revision,
        )
        if current_appendix_b["projection_hash"] != context["r5_projection_hash"]:
            raise ReportDomainError(
                "APPENDIX_B_CHANGED_DURING_EXPORT",
                "附录 B 在 Word 刷新期间发生变化，请重新导出。",
                status_code=409,
                project_uuid=project_uuid,
            )
        initial.unlink(missing_ok=True)
        status_file = staging / "word-status.json"
        status_file.unlink(missing_ok=True)
        os.replace(staging, final_dir)
        staging = None
        published = final_dir
        output = final_dir / refreshed.name
        relative = output.resolve().relative_to(settings.storage_path.resolve()).as_posix()
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        with database.connect() as db:
            cursor = db.execute(
                """
                UPDATE report_export_jobs
                SET status = 'succeeded', output_relative_path = ?, docx_hash = ?,
                    page_count = ?, word_refresh_status = ?, finished_at = ?
                WHERE job_uuid = ? AND status = 'running'
                """,
                (relative, digest, page_count, word_status, database.utc_now(), job_uuid),
            )
            if cursor.rowcount != 1:
                raise ReportDomainError(
                    "REPORT_EXPORT_STATE_CONFLICT",
                    "导出任务状态已变化，不能发布文件。",
                    status_code=409,
                )
        published = None
    except Exception as exc:  # task boundary records every failure deterministically
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if published is not None and published.exists():
            shutil.rmtree(published, ignore_errors=True)
        _fail_job(job_uuid, exc)


def export_docx_path(job_uuid: str) -> Path:
    with database.connect() as db:
        row = db.execute(
            "SELECT status, output_relative_path FROM report_export_jobs WHERE job_uuid = ?",
            (job_uuid,),
        ).fetchone()
    if row is None:
        raise ReportDomainError("REPORT_EXPORT_JOB_NOT_FOUND", "完整报告导出任务不存在。", status_code=404)
    if row["status"] != "succeeded" or not row["output_relative_path"]:
        raise ReportDomainError("REPORT_EXPORT_NOT_READY", "完整报告尚不可下载。", status_code=409)
    storage = settings.storage_path.resolve()
    path = (storage / str(row["output_relative_path"])).resolve()
    if storage not in path.parents or not path.is_file():
        raise ReportDomainError("REPORT_EXPORT_FILE_UNAVAILABLE", "完整报告导出文件不存在或路径异常。", status_code=500)
    return path


def validate_project_export(project_uuid: str, *, mode: str = "final") -> dict[str, Any]:
    return validate_for_export(project_uuid, mode=mode)  # type: ignore[arg-type]
