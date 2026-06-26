from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from ... import database
from ...config import settings
from ...schemas import DocxImportJobRead
from ..projects import remove_project_runtime_files
from .preview import docx_import_job_to_schema


IMPORT_FIG_TOKEN_RE = re.compile(r"\[\[FIG:import:(A-[1-8]-\d+)\]\]")


class DocxImportConfirmError(RuntimeError):
    """确认 DOCX 导入并创建项目失败。"""


def confirm_docx_import_project(job_id: int, project_name: str | None = None) -> DocxImportJobRead | None:
    job = database.get_docx_import_job(job_id)
    if job is None:
        return None

    _ensure_job_can_be_confirmed(job)
    parsed_payload = _load_parsed_payload(job["parsed_json_path"])
    final_project_name = _project_name(project_name, parsed_payload.get("suggested_project_name"), job["original_name"])

    database.update_docx_import_job(job_id, {"status": "importing"})
    created_project_id: int | None = None
    try:
        created_project_id = _create_project_from_payload(job_id, final_project_name, parsed_payload)
    except Exception as exc:  # noqa: BLE001
        if created_project_id is not None:
            remove_project_runtime_files(created_project_id)
        _mark_import_failed(job_id, exc)
        if isinstance(exc, DocxImportConfirmError):
            raise
        raise DocxImportConfirmError(f"确认导入失败：{exc}") from exc

    updated = database.update_docx_import_job(
        job_id,
        {
            "status": "succeeded",
            "created_project_id": created_project_id,
            "error_message": None,
            "finished_at": database.utc_now(),
        },
    )
    return docx_import_job_to_schema(updated)


def _ensure_job_can_be_confirmed(job: Any) -> None:
    if job["status"] == "succeeded" and job["created_project_id"] is not None:
        raise DocxImportConfirmError("该导入任务已经创建项目，不能重复确认。")
    if job["status"] != "preview_ready":
        raise DocxImportConfirmError("导入任务尚未生成可确认的预览结果。")

    issues = _load_json(job["issues_json"], [])
    if any(issue.get("severity") == "error" for issue in issues):
        raise DocxImportConfirmError("导入预览包含错误，请修正文档或重新上传后再创建项目。")
    if not job["parsed_json_path"]:
        raise DocxImportConfirmError("导入任务缺少解析结果，不能创建项目。")


def _create_project_from_payload(job_id: int, project_name: str, parsed_payload: dict[str, Any]) -> int:
    timestamp = database.utc_now()
    sections_payload = parsed_payload.get("sections") or []
    sections_by_code = {section.get("code"): section for section in sections_payload if section.get("code")}
    project_id: int | None = None

    try:
        with database.connect() as db:
            project_cursor = db.execute(
                "INSERT INTO projects (name, created_at, updated_at) VALUES (?, ?, ?)",
                (project_name, timestamp, timestamp),
            )
            project_id = int(project_cursor.lastrowid)

            for code, default_title, default_table_title, sort_order in database.SECTION_SEED:
                section_payload = sections_by_code.get(code, {})
                db.execute(
                    """
                    INSERT INTO appendix_sections
                        (project_id, code, title, table_title, sort_order)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        code,
                        section_payload.get("title") or default_title,
                        section_payload.get("table_title") or default_table_title,
                        sort_order,
                    ),
                )

            image_id_by_import_key: dict[str, int] = {}
            token_by_import_key: dict[str, str] = {}
            for section in sections_payload:
                for image in section.get("images") or []:
                    image_id = _copy_and_insert_image(db, project_id, image, timestamp)
                    import_key = _figure_key(image.get("figure_label"))
                    if import_key:
                        image_id_by_import_key[import_key] = image_id
                        token_by_import_key[import_key] = f"[[FIG:{image_id}]]"

            for section in sections_payload:
                section_row = database.get_section(project_id, section.get("code", ""), db)
                if section_row is None:
                    continue
                for index, row in enumerate(section.get("rows") or [], start=1):
                    _insert_assessment_row(db, section_row["id"], row, index, image_id_by_import_key, token_by_import_key, timestamp)

            db.execute(
                """
                UPDATE docx_import_jobs
                SET status = ?,
                    created_project_id = ?,
                    error_message = NULL,
                    finished_at = ?
                WHERE id = ?
                """,
                ("succeeded", project_id, timestamp, job_id),
            )
            return project_id
    except Exception:
        if project_id is not None:
            remove_project_runtime_files(project_id)
        raise


def _copy_and_insert_image(db: Any, project_id: int, image: dict[str, Any], timestamp: str) -> int:
    section_code = image.get("section_code") or ""
    if database.get_section(project_id, section_code, db) is None:
        raise DocxImportConfirmError(f"导入图片所属章节不存在：{section_code or '未知'}。")

    source_path = _storage_child_path(str(image.get("file_path") or ""))
    if not source_path.exists():
        raise DocxImportConfirmError(f"导入图片文件不存在：{image.get('figure_label') or source_path.name}。")

    relative_path = _next_project_image_path(project_id, section_code, source_path.name)
    destination = settings.storage_path / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)

    cursor = db.execute(
        """
        INSERT INTO evidence_images
            (
                project_id,
                section_code,
                file_path,
                original_name,
                caption,
                alt_text,
                sort_order,
                pixel_width,
                pixel_height,
                dpi_x,
                dpi_y,
                display_width_in,
                display_height_in,
                created_at,
                updated_at
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            section_code,
            relative_path.as_posix(),
            image.get("original_name") or source_path.name,
            image.get("caption") or "",
            image.get("caption") or image.get("original_name") or "",
            int(image.get("sort_order") or 1),
            image.get("pixel_width"),
            image.get("pixel_height"),
            image.get("dpi_x"),
            image.get("dpi_y"),
            image.get("display_width_in"),
            image.get("display_height_in"),
            timestamp,
            timestamp,
        ),
    )
    return int(cursor.lastrowid)


def _insert_assessment_row(
    db: Any,
    section_id: int,
    row: dict[str, Any],
    default_sort_order: int,
    image_id_by_import_key: dict[str, int],
    token_by_import_key: dict[str, str],
    timestamp: str,
) -> None:
    record_text = _replace_import_tokens(row.get("record_text") or "", token_by_import_key)
    cursor = db.execute(
        """
        INSERT INTO assessment_rows
            (section_id, unit, object_name, subsystem, record_text, sort_order, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            section_id,
            row.get("unit") or "",
            row.get("object_name") or "",
            row.get("subsystem") or "",
            record_text,
            int(row.get("sort_order") or default_sort_order),
            timestamp,
            timestamp,
        ),
    )
    row_id = int(cursor.lastrowid)
    metric = row.get("metric_result") or {}
    db.execute(
        """
        INSERT INTO metric_results
            (row_id, d, a, k, object_score, unit_score, compliance)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            metric.get("d"),
            metric.get("a"),
            metric.get("k"),
            metric.get("object_score"),
            metric.get("unit_score"),
            metric.get("compliance"),
        ),
    )

    for reference in row.get("cross_references") or []:
        import_key = reference.get("target_image_key") or _figure_key(reference.get("target_figure_label"))
        image_id = image_id_by_import_key.get(import_key or "")
        if image_id is None:
            continue
        token = f"[[FIG:{image_id}]]"
        db.execute(
            """
            INSERT INTO cross_references
                (source_row_id, target_image_id, token, display_text)
            VALUES (?, ?, ?, ?)
            """,
            (row_id, image_id, token, reference.get("display_text") or reference.get("target_figure_label") or ""),
        )


def _mark_import_failed(job_id: int, exc: Exception) -> None:
    issue = {
        "severity": "error",
        "code": "IMPORT_CREATE_PROJECT_FAILED",
        "message": str(exc) or "确认导入并创建项目失败。",
    }
    database.update_docx_import_job(
        job_id,
        {
            "status": "failed",
            "summary": {"errors": 1, "warnings": 0, "info": 0, "can_create_project": 0},
            "issues": [issue],
            "error_message": issue["message"],
            "finished_at": database.utc_now(),
        },
    )


def _load_parsed_payload(relative_path: str | None) -> dict[str, Any]:
    if not relative_path:
        raise DocxImportConfirmError("导入任务缺少解析结果，不能创建项目。")
    path = _storage_child_path(relative_path)
    if not path.exists():
        raise DocxImportConfirmError("导入任务解析结果文件不存在，不能创建项目。")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DocxImportConfirmError("导入任务解析结果无法读取，不能创建项目。") from exc


def _load_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _replace_import_tokens(text: str, token_by_import_key: dict[str, str]) -> str:
    def replace_match(match: re.Match[str]) -> str:
        return token_by_import_key.get(match.group(1), match.group(0))

    return IMPORT_FIG_TOKEN_RE.sub(replace_match, text or "")


def _project_name(project_name: str | None, suggested_name: str | None, original_name: str | None) -> str:
    name = (project_name or "").strip()
    if not name:
        name = (suggested_name or "").strip()
    if not name:
        name = Path(original_name or "导入项目").stem.strip()
    return name or "导入项目"


def _storage_child_path(relative_path: str) -> Path:
    storage_root = settings.storage_path.resolve()
    target = (settings.storage_path / relative_path).resolve()
    try:
        target.relative_to(storage_root)
    except ValueError as exc:
        raise DocxImportConfirmError("导入文件路径超出本地存储目录。") from exc
    return target


def _next_project_image_path(project_id: int, section_code: str, source_name: str) -> Path:
    extension = Path(source_name).suffix.lower() or ".png"
    base_name = _safe_filename(Path(source_name).stem) or "imported-image"
    relative_dir = Path("uploads") / str(project_id) / _safe_section_code(section_code)
    candidate = relative_dir / f"{base_name}{extension}"
    counter = 2
    while (settings.storage_path / candidate).exists():
        candidate = relative_dir / f"{base_name}-{counter}{extension}"
        counter += 1
    return candidate


def _safe_section_code(section_code: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", section_code or "section")


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "").strip("-_.")


def _figure_key(label: str | None) -> str:
    match = re.search(r"A\s*-\s*([1-8])\s*-\s*(\d+)", label or "")
    if not match:
        return ""
    return f"A-{match.group(1)}-{match.group(2)}"
