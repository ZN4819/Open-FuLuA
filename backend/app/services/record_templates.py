from __future__ import annotations

import json
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
