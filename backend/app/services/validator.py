from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .. import database
from ..config import settings
from ..schemas import ValidationIssueRead, ValidationResponse, ValidationSummary
from .docx_analyzer import analyze_docx
from .docx_generator import DocxGenerationError, generate_project_docx
from .template_profile import load_template_profile


FIG_TOKEN_RE = re.compile(r"\[\[FIG:(\d+)\]\]")


def validate_project(project_id: int) -> ValidationResponse:
    if database.get_project_by_id(project_id) is None:
        raise ValueError("项目不存在")

    profile = load_template_profile()
    issues: list[dict[str, Any]] = []
    referenced_image_ids: set[int] = set()
    all_images: dict[int, Any] = {}

    for section in database.list_sections(project_id):
        section_profile = _section_profile(profile, section["code"])
        rows = database.list_assessment_rows(section["id"])
        images = database.list_evidence_images(project_id, section["code"])
        references = database.list_cross_references(section["id"])

        for image in images:
            all_images[int(image["id"])] = image

        issues.extend(_validate_rows(section, section_profile, rows, profile))
        section_refs = _validate_references(section, rows, references, images)
        referenced_image_ids.update(section_refs["referenced_image_ids"])
        issues.extend(section_refs["issues"])
        issues.extend(_validate_images(section, images))

    issues.extend(_validate_unused_images(all_images, referenced_image_ids))
    issues.extend(_validate_exported_docx(project_id))

    persisted = database.replace_validation_issues(project_id, issues)
    return ValidationResponse(
        summary=_summary(persisted),
        issues=[_issue_to_schema(row) for row in persisted],
    )


def _validate_rows(section: Any, section_profile: dict[str, Any], rows: list[Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    technical_options = set(profile["content_controls"]["technical_metric"]["options"])
    compliance_options = set(profile["content_controls"]["management_compliance"]["options"])

    for row in rows:
        row_label = _row_label(section, row)
        for key, label in (
            ("unit", "测评单元"),
            ("object_name", "测评对象"),
            ("record_text", "结果记录"),
        ):
            if not _text(row[key]):
                issues.append(
                    _issue(
                        "error",
                        "REQUIRED_FIELD_MISSING",
                        f"{row_label} 缺少{label}。",
                        "row",
                        row["id"],
                    )
                )

        if section_profile["table_type"] == "technical":
            for key in ("d", "a", "k"):
                value = _text(row[key])
                if not value:
                    issues.append(_issue("error", "METRIC_REQUIRED", f"{row_label} 缺少 {key.upper()} 指标。", "row", row["id"]))
                elif value not in technical_options:
                    issues.append(
                        _issue(
                            "error",
                            "INVALID_DROPDOWN_VALUE",
                            f"{row_label} 的 {key.upper()} 指标值“{value}”不在模板选项内。",
                            "row",
                            row["id"],
                        )
                    )
            _validate_score(issues, row, row_label, "object_score", "对象评分")
            _validate_score(issues, row, row_label, "unit_score", "单元得分")
        else:
            value = _text(row["compliance"])
            if not value:
                issues.append(_issue("error", "COMPLIANCE_REQUIRED", f"{row_label} 缺少符合情况。", "row", row["id"]))
            elif value not in compliance_options:
                issues.append(
                    _issue(
                        "error",
                        "INVALID_DROPDOWN_VALUE",
                        f"{row_label} 的符合情况“{value}”不在模板选项内。",
                        "row",
                        row["id"],
                    )
                )
            _validate_score(issues, row, row_label, "unit_score", "单元得分")

    return issues


def _validate_references(section: Any, rows: list[Any], references: list[Any], images: list[Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    referenced_image_ids: set[int] = set()
    section_image_ids = {int(image["id"]) for image in images}
    row_by_id = {int(row["id"]): row for row in rows}
    active_reference_tokens_by_row_id = {
        int(row["id"]): _reference_tokens(row["record_text"])
        for row in rows
    }

    for row in rows:
        for image_id in _tokens(row["record_text"]):
            referenced_image_ids.add(image_id)
            if image_id not in section_image_ids:
                issues.append(
                    _issue(
                        "error",
                        "BROKEN_IMAGE_REFERENCE",
                        f"{_row_label(section, row)} 引用了不存在或不属于本章节的图片：[[FIG:{image_id}]]。",
                        "row",
                        row["id"],
                    )
                )

    for reference in references:
        row = row_by_id.get(int(reference["source_row_id"]))
        token = _text(reference["token"])
        if row is not None and token not in active_reference_tokens_by_row_id.get(int(row["id"]), set()):
            continue
        label = _row_label(section, row) if row is not None else f"{section['code']} 引用"
        target_id = reference["target_image_id"]
        if target_id is None:
            issues.append(
                _issue(
                    "error",
                    "BROKEN_STORED_REFERENCE",
                    f"{label} 保存的交叉引用缺少目标图片。",
                    "reference",
                    reference["id"],
                )
            )
            continue
        referenced_image_ids.add(int(target_id))
        if int(target_id) not in section_image_ids:
            issues.append(
                _issue(
                    "error",
                    "BROKEN_STORED_REFERENCE",
                    f"{label} 保存的交叉引用目标图片不存在或不属于本章节。",
                    "reference",
                    reference["id"],
                )
            )

    return {"issues": issues, "referenced_image_ids": referenced_image_ids}


def _validate_images(section: Any, images: list[Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for image in images:
        image_id = int(image["id"])
        image_label = f"{section['code']} 图片 {image['sort_order']}"
        image_path = settings.storage_path / image["file_path"]
        if not Path(image_path).exists():
            issues.append(
                _issue(
                    "error",
                    "IMAGE_FILE_MISSING",
                    f"{image_label} 的本地文件不存在，导出 DOCX 时无法插入图片。",
                    "image",
                    image_id,
                )
            )
        if not _text(image["caption"]):
            issues.append(_issue("info", "IMAGE_CAPTION_MISSING", f"{image_label} 缺少题注。", "image", image_id))
    return issues


def _validate_unused_images(images: dict[int, Any], referenced_image_ids: set[int]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for image_id, image in images.items():
        if image_id not in referenced_image_ids:
            issues.append(
                _issue(
                    "info",
                    "IMAGE_UNUSED",
                    f"{image['section_code']} 图片 {image['sort_order']} 尚未在结果记录中引用。",
                    "image",
                    image_id,
                )
            )
    return issues


def _validate_exported_docx(project_id: int) -> list[dict[str, Any]]:
    try:
        path = generate_project_docx(project_id, "final")
        analysis = analyze_docx(path)
    except (DocxGenerationError, OSError) as exc:
        return [
            _issue(
                "error",
                "DOCX_EXPORT_VALIDATION_FAILED",
                f"导出 DOCX 结构校验失败：{exc}",
                "project",
                project_id,
            )
        ]

    if analysis.missing_ref_targets:
        return [
            _issue(
                "error",
                "DOCX_REF_TARGET_MISSING",
                f"导出 DOCX 存在缺失的 REF 目标：{', '.join(analysis.missing_ref_targets)}。",
                "docx",
                project_id,
            )
        ]
    return []


def _validate_score(issues: list[dict[str, Any]], row: Any, row_label: str, key: str, label: str) -> None:
    value = _text(row[key])
    if not value:
        issues.append(_issue("error", "SCORE_REQUIRED", f"{row_label} 缺少{label}。", "row", row["id"]))
        return
    if value == "/":
        return
    try:
        Decimal(value)
    except InvalidOperation:
        issues.append(_issue("error", "INVALID_SCORE", f"{row_label} 的{label}“{value}”不是有效数字。", "row", row["id"]))


def _section_profile(profile: dict[str, Any], code: str) -> dict[str, Any]:
    for section in profile["sections"]:
        if section["code"] == code:
            return section
    raise ValueError(f"模板 profile 缺少章节：{code}")


def _tokens(text: str) -> set[int]:
    return {int(match.group(1)) for match in FIG_TOKEN_RE.finditer(text or "")}


def _reference_tokens(text: str) -> set[str]:
    return {match.group(0) for match in FIG_TOKEN_RE.finditer(text or "")}


def _row_label(section: Any, row: Any | None) -> str:
    if row is None:
        return section["code"]
    return f"{section['code']} 第 {row['sort_order']} 行"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _issue(severity: str, code: str, message: str, target_type: str, target_id: Any) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "target_type": target_type,
        "target_id": str(target_id),
    }


def _issue_to_schema(row: Any) -> ValidationIssueRead:
    return ValidationIssueRead(
        id=row["id"],
        project_id=row["project_id"],
        severity=row["severity"],
        code=row["code"],
        message=row["message"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        created_at=row["created_at"],
    )


def _summary(rows: list[Any]) -> ValidationSummary:
    return ValidationSummary(
        errors=sum(1 for row in rows if row["severity"] == "error"),
        warnings=sum(1 for row in rows if row["severity"] == "warning"),
        info=sum(1 for row in rows if row["severity"] == "info"),
    )
