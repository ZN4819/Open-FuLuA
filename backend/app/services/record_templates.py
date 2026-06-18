from __future__ import annotations

import json
import sqlite3
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import database


RECORD_TEMPLATES_PATH = (
    Path(__file__).resolve().parents[3]
    / "templates"
    / "appendix_a"
    / "record_templates.json"
)


class RecordTemplateError(RuntimeError):
    """结果记录模板库无法读取或结构不符合预期。"""


class RecordTemplateValidationError(RecordTemplateError):
    """结果记录模板输入不符合业务规则。"""


class RecordTemplateNotFoundError(RecordTemplateError):
    """结果记录模板不存在。"""


class RecordTemplatePermissionError(RecordTemplateError):
    """结果记录模板当前不允许执行该操作。"""


@lru_cache(maxsize=1)
def load_record_template_library() -> dict[str, Any]:
    if not RECORD_TEMPLATES_PATH.exists():
        raise RecordTemplateError(f"结果记录模板库不存在：{RECORD_TEMPLATES_PATH}")

    with RECORD_TEMPLATES_PATH.open("r", encoding="utf-8") as template_file:
        library = json.load(template_file)

    validate_record_template_library(library)
    return library


def list_record_templates(section_code: str | None = None) -> list[dict[str, Any]]:
    ensure_system_record_templates_seeded()
    return [_row_to_template(row) for row in database.list_record_template_rows(section_code)]


def create_user_record_template(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_system_record_templates_seeded()
    template = _normalize_template_payload(payload, require_all=True)
    for _ in range(5):
        template_key = f"user-{uuid.uuid4().hex[:12]}"
        try:
            row = database.create_user_record_template(template_key, template)
        except sqlite3.IntegrityError:
            continue
        return _row_to_template(row)
    raise RecordTemplateError("用户模板编号生成失败，请重试。")


def update_user_record_template(template_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_system_record_templates_seeded()
    existing = _get_existing_template(template_key)
    _ensure_user_template(existing)

    merged = {
        "section_code": existing["section_code"],
        "table_type": existing["table_type"],
        "unit": existing["unit"],
        "object_name": existing["object_name"],
        "title": existing["title"],
        "record_text": existing["record_text"],
        "tags": _parse_tags(existing["tags"]),
    }
    for key, value in payload.items():
        if value is not None:
            merged[key] = value
    updates = _normalize_template_payload(merged, require_all=True)
    row = database.update_record_template_row(template_key, updates)
    if row is None:
        raise RecordTemplateNotFoundError("结果记录模板不存在。")
    return _row_to_template(row)


def delete_user_record_template(template_key: str) -> dict[str, Any]:
    ensure_system_record_templates_seeded()
    existing = _get_existing_template(template_key)
    _ensure_user_template(existing)
    row = database.soft_delete_record_template_row(template_key)
    if row is None:
        raise RecordTemplateNotFoundError("结果记录模板不存在。")
    return _row_to_template(row)


def copy_record_template(template_key: str) -> dict[str, Any]:
    ensure_system_record_templates_seeded()
    existing = _get_existing_template(template_key)
    payload = {
        "section_code": existing["section_code"],
        "table_type": existing["table_type"],
        "unit": existing["unit"],
        "object_name": existing["object_name"],
        "title": existing["title"],
        "record_text": existing["record_text"],
        "tags": _parse_tags(existing["tags"]),
    }
    return create_user_record_template(payload)


def ensure_system_record_templates_seeded() -> None:
    templates = load_record_template_library()["templates"]
    database.upsert_system_record_templates(templates)


def validate_record_template_library(library: dict[str, Any]) -> None:
    templates = library.get("templates")
    if not isinstance(templates, list) or not templates:
        raise RecordTemplateError("结果记录模板库必须包含 templates 列表。")

    ids: set[str] = set()
    for index, template in enumerate(templates, start=1):
        _require(template, "id", str, index)
        _require(template, "section_code", str, index)
        _require(template, "table_type", str, index)
        _require(template, "unit", str, index)
        _require(template, "object_name", str, index)
        _require(template, "title", str, index)
        _require(template, "record_text", str, index)
        _require(template, "source_row", int, index)

        template_id = template["id"]
        if template_id in ids:
            raise RecordTemplateError(f"结果记录模板 id 重复：{template_id}")
        ids.add(template_id)

        section_code = template["section_code"]
        if section_code not in {f"A-{number}" for number in range(1, 9)}:
            raise RecordTemplateError(f"结果记录模板章节不合法：{section_code}")
        if template["table_type"] not in {"technical", "management"}:
            raise RecordTemplateError(f"结果记录模板表格类型不合法：{template['table_type']}")


def _require(template: dict[str, Any], key: str, expected_type: type, index: int) -> None:
    value = template.get(key)
    if not isinstance(value, expected_type):
        raise RecordTemplateError(f"第 {index} 条结果记录模板缺少 {key}。")


def _get_existing_template(template_key: str) -> Any:
    row = database.get_record_template_row(template_key)
    if row is None:
        raise RecordTemplateNotFoundError("结果记录模板不存在。")
    return row


def _ensure_user_template(row: Any) -> None:
    if row["source_type"] != "user":
        raise RecordTemplatePermissionError("系统模板不能直接修改或删除，请先复制为用户模板。")


def _normalize_template_payload(payload: dict[str, Any], require_all: bool) -> dict[str, Any]:
    section_code = _clean_text(payload.get("section_code"))
    table_type = _clean_text(payload.get("table_type"))
    unit = _clean_text(payload.get("unit"))
    object_name = _clean_text(payload.get("object_name"))
    title = _clean_text(payload.get("title"))
    record_text = _clean_text(payload.get("record_text"))
    tags = _normalize_tags(payload.get("tags"))

    if require_all:
        if section_code not in {f"A-{number}" for number in range(1, 9)}:
            raise RecordTemplateValidationError("结果记录模板章节必须为 A-1 至 A-8。")
        if table_type not in {"technical", "management"}:
            raise RecordTemplateValidationError("结果记录模板表格类型必须为 technical 或 management。")
        if not record_text:
            raise RecordTemplateValidationError("结果记录模板正文不能为空。")

    if not title:
        title = _default_title(unit, object_name)

    return {
        "section_code": section_code,
        "table_type": table_type,
        "unit": unit,
        "object_name": object_name,
        "title": title,
        "record_text": record_text,
        "tags": tags,
    }


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _default_title(unit: str, object_name: str) -> str:
    if unit and object_name:
        return f"{unit} / {object_name}"
    return unit or object_name or "自定义结果记录模板"


def _normalize_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value:
        tag = _clean_text(item)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _row_to_template(row: Any) -> dict[str, Any]:
    tags = _parse_tags(row["tags"])
    return {
        "id": row["template_key"],
        "source_type": row["source_type"],
        "section_code": row["section_code"],
        "table_type": row["table_type"],
        "unit": row["unit"],
        "object_name": row["object_name"],
        "title": row["title"],
        "record_text": row["record_text"],
        "tags": tags,
        "source_row": row["source_row"],
        "is_enabled": bool(row["is_enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]
