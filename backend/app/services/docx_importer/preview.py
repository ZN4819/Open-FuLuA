from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from ... import database
from ...config import settings
from ...schemas import DocxImportIssue, DocxImportJobRead, DocxImportSectionPreview
from .media import parse_docx_images_and_references
from .package import DocxImportPackageError
from .storage import ensure_import_job_dir, import_job_dir, parsed_json_path, source_docx_path


MAX_IMPORT_FILE_SIZE_MB = 200
MAX_IMPORT_FILE_SIZE_BYTES = MAX_IMPORT_FILE_SIZE_MB * 1024 * 1024


class DocxImportPreviewError(RuntimeError):
    """DOCX 导入预览创建失败。"""


def create_docx_import_preview(upload: UploadFile) -> DocxImportJobRead:
    original_name = _clean_filename(upload.filename)
    _validate_docx_filename(original_name)

    job = database.create_docx_import_job(
        original_name=original_name,
        source_docx_path="",
        status="uploaded",
        summary={},
        issues=[],
    )
    job_id = int(job["id"])
    source_path = source_docx_path(job_id)
    parsed_path = parsed_json_path(job_id)

    try:
        ensure_import_job_dir(job_id)
        _save_upload(upload, source_path)
        database.update_docx_import_job(
            job_id,
            {
                "status": "parsing",
                "source_docx_path": _relative_storage_path(source_path),
                "started_at": database.utc_now(),
            },
        )

        parsed = parse_docx_images_and_references(source_path, import_job_dir(job_id))
        parsed_payload = _parsed_project_to_payload(parsed)
        parsed_payload["suggested_project_name"] = Path(original_name).stem
        parsed_path.write_text(json.dumps(parsed_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        issues = parsed_payload["issues"]
        summary = dict(parsed_payload["summary"])
        summary["can_create_project"] = int(_can_create_project(issues))

        updated = database.update_docx_import_job(
            job_id,
            {
                "status": "preview_ready",
                "parsed_json_path": _relative_storage_path(parsed_path),
                "summary": summary,
                "issues": issues,
                "error_message": None,
                "finished_at": database.utc_now(),
            },
        )
        return docx_import_job_to_schema(updated)
    except (DocxImportPackageError, DocxImportPreviewError, OSError, ValueError) as exc:
        return _mark_failed(job_id, exc)


def get_docx_import_preview(job_id: int) -> DocxImportJobRead | None:
    row = database.get_docx_import_job(job_id)
    return docx_import_job_to_schema(row) if row is not None else None


def docx_import_job_to_schema(row: Any) -> DocxImportJobRead:
    raw = dict(row)
    summary = _load_json(raw.get("summary_json"), {})
    issues_payload = _load_json(raw.get("issues_json"), [])
    parsed_payload = _load_parsed_payload(raw.get("parsed_json_path"))
    sections_payload = parsed_payload.get("sections_preview", []) if parsed_payload else []
    suggested_name = parsed_payload.get("suggested_project_name") if parsed_payload else ""
    if not suggested_name:
        suggested_name = Path(raw.get("original_name") or "导入项目").stem
    issues = [DocxImportIssue(**issue) for issue in issues_payload]
    can_create = raw["status"] == "preview_ready" and _can_create_project(issues_payload)

    return DocxImportJobRead(
        id=raw["id"],
        status=raw["status"],
        original_name=raw["original_name"],
        source_docx_path=raw["source_docx_path"] or "",
        parsed_json_path=raw["parsed_json_path"],
        suggested_project_name=suggested_name,
        created_project_id=raw["created_project_id"],
        sections=[DocxImportSectionPreview(**section) for section in sections_payload],
        summary=summary,
        issues=issues,
        can_create_project=can_create,
        error_message=raw["error_message"],
        created_at=raw["created_at"],
        started_at=raw["started_at"],
        finished_at=raw["finished_at"],
    )


def _mark_failed(job_id: int, exc: Exception) -> DocxImportJobRead:
    issue = {
        "severity": "error",
        "code": "IMPORT_PARSE_FAILED",
        "message": str(exc) or "DOCX 导入解析失败。",
    }
    updated = database.update_docx_import_job(
        job_id,
        {
            "status": "failed",
            "issues": [issue],
            "summary": {"errors": 1, "warnings": 0, "info": 0, "can_create_project": 0},
            "error_message": issue["message"],
            "finished_at": database.utc_now(),
        },
    )
    return docx_import_job_to_schema(updated)


def _parsed_project_to_payload(parsed: Any) -> dict[str, Any]:
    sections = []
    sections_preview = []
    for section in parsed.sections:
        section_payload = asdict(section)
        section_payload["row_count"] = section.row_count
        section_payload["image_count"] = len(section.images)
        section_payload["reference_count"] = sum(len(row.cross_references) for row in section.rows)
        sections.append(section_payload)
        sections_preview.append(
            {
                "code": section.code,
                "title": section.title,
                "table_title": section.table_title,
                "table_type": section.table_type,
                "row_count": section.row_count,
                "image_count": len(section.images),
                "reference_count": section_payload["reference_count"],
            }
        )

    return {
        "suggested_project_name": parsed.suggested_project_name,
        "sections": sections,
        "sections_preview": sections_preview,
        "issues": [asdict(issue) for issue in parsed.issues],
        "summary": dict(parsed.summary),
    }


def _save_upload(upload: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        upload.file.seek(0)
    except (AttributeError, OSError):
        pass
    with destination.open("wb") as output_file:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_IMPORT_FILE_SIZE_BYTES:
                output_file.close()
                destination.unlink(missing_ok=True)
                raise DocxImportPreviewError(f"DOCX 文件超过 {MAX_IMPORT_FILE_SIZE_MB}MB，无法导入。")
            output_file.write(chunk)
    if size <= 0:
        destination.unlink(missing_ok=True)
        raise DocxImportPreviewError("DOCX 文件为空，无法导入。")


def _validate_docx_filename(filename: str) -> None:
    if Path(filename).suffix.lower() != ".docx":
        raise DocxImportPreviewError("仅支持 .docx 文件导入。")


def _clean_filename(filename: str | None) -> str:
    name = Path(filename or "source.docx").name.strip()
    return name or "source.docx"


def _relative_storage_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(settings.storage_path.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _load_parsed_payload(relative_path: str | None) -> dict[str, Any]:
    if not relative_path:
        return {}
    path = settings.storage_path / relative_path
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _can_create_project(issues: list[dict[str, Any]]) -> bool:
    return not any(issue.get("severity") == "error" for issue in issues)