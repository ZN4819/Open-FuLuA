from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


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
    templates = load_record_template_library()["templates"]
    if section_code is None:
        return templates
    return [template for template in templates if template["section_code"] == section_code]


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
