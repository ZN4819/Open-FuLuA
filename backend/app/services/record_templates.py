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
EXPORT_PROFILE_ID = "appendix_a_user_record_templates_v1"
SLOT_EXPORT_PROFILE_ID = "appendix_a_record_template_slots_v1"

TEMPLATE_SLOT_GROUPS = ("verification_record", "score_basis")
TECHNICAL_SCORE_BASIS_SECTION_CODE = "TECHNICAL"
TECHNICAL_SCORE_BASIS_UNIT = "A-1 至 A-4 通用评分依据"
TECHNICAL_SECTION_CODES = {f"A-{number}" for number in range(1, 5)}
TEMPLATE_SLOT_GROUP_LABELS = {
    "verification_record": "测评验证记录模板",
    "score_basis": "测评对象评分计算依据模板",
}
TEMPLATE_SLOT_TYPES_BY_GROUP = {
    "verification_record": ("compliant", "non_compliant", "not_applicable"),
    "score_basis": ("fully_compliant", "score_adjusted", "non_compliant"),
}
TEMPLATE_SLOT_TYPES = tuple(dict.fromkeys(template_type for types in TEMPLATE_SLOT_TYPES_BY_GROUP.values() for template_type in types))
TEMPLATE_SLOT_TYPE_LABELS = {
    "compliant": "符合/部分符合模板",
    "fully_compliant": "完全符合模板",
    "score_adjusted": "分数修正模板",
    "non_compliant": "不符合模板",
    "not_applicable": "不适用模板",
}
TEMPLATE_SLOT_TYPE_TAGS = {
    "compliant": ["符合", "部分符合"],
    "fully_compliant": ["完全符合"],
    "score_adjusted": ["分数修正"],
    "non_compliant": ["不符合"],
    "not_applicable": ["不适用"],
}
VERIFICATION_MARKER = "测评验证记录："
SCORE_BASIS_MARKER = "测评对象评分计算依据："
COMPLIANT_VERIFICATION_DEFAULT_TEXT = "测评验证记录：经核查，{测评对象}满足本测评单元相关要求，详见[插入图片引用]。"
NON_COMPLIANT_DEFAULT_TEXT = (
    "测评验证记录：经核查，{测评对象}未满足本测评单元相关要求，"
    "具体不符合情况为[请补充不符合事实、依据和影响]。"
)
NOT_APPLICABLE_DEFAULT_TEXT = (
    "测评验证记录：经核查，{测评对象}不适用于本测评单元，"
    "原因是[请补充不适用原因、范围和依据]。"
)
FULLY_COMPLIANT_SCORE_DEFAULT_TEXT = (
    "测评对象评分计算依据：被测系统为等保三级系统，使用的密码产品安全等级满足要求，"
    "根据2023版量化评估规则，该测评对象评分为1。"
)
SCORE_ADJUSTED_DEFAULT_TEXT = (
    "测评对象评分计算依据：被测系统为等保三级系统，存在[请补充分数修正原因]，"
    "根据2023版量化评估规则，该测评对象评分为[请填写修正后分数]。"
)
NON_COMPLIANT_SCORE_DEFAULT_TEXT = "测评对象评分计算依据：根据量化评估规则，该测评对象评分为0。"

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


def list_record_templates(section_code: str | None = None, keyword: str | None = None) -> list[dict[str, Any]]:
    ensure_system_record_templates_seeded()
    return [_row_to_template(row) for row in database.list_record_template_rows(section_code, keyword)]


def list_record_template_slots(
    section_code: str | None = None,
    unit: str | None = None,
    template_group: str | None = None,
    template_type: str | None = None,
) -> list[dict[str, Any]]:
    ensure_record_template_slots_seeded()
    if template_group is not None:
        _validate_template_slot_group(template_group)
    if template_type is not None:
        _validate_template_slot_type(template_type, template_group)
    rows = database.list_record_template_slot_rows(
        template_group=template_group,
        template_type=template_type,
    )
    rows = _filter_visible_template_slot_rows(rows, section_code, unit)
    return [
        _row_to_template_slot(row)
        for row in _sort_template_slot_rows(rows)
    ]


def get_record_template_slot(slot_id: int) -> dict[str, Any]:
    ensure_record_template_slots_seeded()
    row = database.get_record_template_slot_row(slot_id)
    if row is None:
        raise RecordTemplateNotFoundError("结果记录模板不存在。")
    _validate_template_slot_type(row["template_type"], row["template_group"])
    return _row_to_template_slot(row)


def update_record_template_slot(slot_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_record_template_slots_seeded()
    existing = get_record_template_slot(slot_id)
    updates = _normalize_template_slot_update(payload, existing)
    if not updates:
        return existing
    row = database.update_record_template_slot_row(slot_id, updates)
    if row is None:
        raise RecordTemplateNotFoundError("结果记录模板不存在。")
    return _row_to_template_slot(row)


def reset_record_template_slot(slot_id: int) -> dict[str, Any]:
    ensure_record_template_slots_seeded()
    existing = get_record_template_slot(slot_id)
    template_type = existing["template_type"]
    row = database.reset_record_template_slot_row(
        slot_id,
        TEMPLATE_SLOT_TYPE_LABELS[template_type],
        TEMPLATE_SLOT_TYPE_TAGS[template_type],
    )
    if row is None:
        raise RecordTemplateNotFoundError("结果记录模板不存在。")
    return _row_to_template_slot(row)


def export_record_template_slots() -> dict[str, Any]:
    ensure_record_template_slots_seeded()
    return {
        "profile_id": SLOT_EXPORT_PROFILE_ID,
        "exported_at": database.utc_now(),
        "templates": [_export_template_slot(slot) for slot in list_record_template_slots()],
    }


def preview_import_record_template_slots(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_record_template_slots_seeded()
    plan = _build_slot_import_plan(payload)
    return {
        "summary": plan["summary"],
        "items": [_public_slot_import_item(item) for item in plan["items"]],
    }


def import_record_template_slots(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_record_template_slots_seeded()
    plan = _build_slot_import_plan(payload)
    if plan["summary"]["errors"] > 0:
        raise RecordTemplateValidationError("导入文件存在错误，请先根据预览结果修正后再导入。")

    imported_items: list[dict[str, Any]] = []
    for item in plan["items"]:
        if item["action"] != "update":
            imported_items.append(item)
            continue
        slot_id = item.get("slot_id")
        payload_to_update = item.get("payload")
        if not slot_id or not payload_to_update:
            imported_items.append({**item, "action": "error", "message": "缺少待更新的分段模板槽位。"})
            continue
        update_record_template_slot(
            slot_id,
            {
                "title": payload_to_update["title"],
                "record_text": payload_to_update["record_text"],
                "tags": payload_to_update["tags"],
            },
        )
        imported_items.append(item)

    summary = {
        "created": 0,
        "updated": sum(1 for item in imported_items if item["action"] == "update"),
        "skipped": sum(1 for item in imported_items if item["action"] == "skip"),
        "errors": sum(1 for item in imported_items if item["action"] == "error"),
    }
    return {
        "summary": summary,
        "items": [_public_slot_import_item(item) for item in imported_items],
    }

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




def export_user_record_templates() -> dict[str, Any]:
    ensure_system_record_templates_seeded()
    templates = [
        _export_template(_row_to_template(row))
        for row in database.list_record_template_rows(source_type="user")
    ]
    return {
        "profile_id": EXPORT_PROFILE_ID,
        "exported_at": database.utc_now(),
        "templates": templates,
    }


def preview_import_record_templates(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_system_record_templates_seeded()
    plan = _build_import_plan(payload)
    return {
        "summary": plan["summary"],
        "items": [_public_import_item(item) for item in plan["items"]],
    }


def import_user_record_templates(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_system_record_templates_seeded()
    plan = _build_import_plan(payload)
    if plan["summary"]["errors"] > 0:
        raise RecordTemplateValidationError("导入文件存在错误，请先根据预览结果修正后再导入。")

    imported_items: list[dict[str, Any]] = []
    for item in plan["items"]:
        action = item["action"]
        template_payload = item.get("payload")
        if not template_payload:
            imported_items.append(item)
            continue
        if action == "create":
            created = create_user_record_template(template_payload)
            imported_items.append({**item, "template_id": created["id"]})
        elif action == "update":
            template_id = item.get("template_id")
            if not template_id:
                imported_items.append({**item, "action": "error", "message": "缺少待更新的用户模板编号。"})
                continue
            update_user_record_template(template_id, template_payload)
            imported_items.append(item)
        else:
            imported_items.append(item)

    return {
        "summary": plan["summary"],
        "items": [_public_import_item(item) for item in imported_items],
    }

def ensure_system_record_templates_seeded() -> None:
    templates = load_record_template_library()["templates"]
    database.upsert_system_record_templates(templates)


def ensure_record_template_slots_seeded() -> None:
    ensure_system_record_templates_seeded()
    database.upsert_record_template_slots(_build_template_slot_seed())


def _build_template_slot_seed() -> list[dict[str, Any]]:
    template_order = _template_unit_order()
    representative_by_unit: dict[tuple[str, str], dict[str, Any]] = {}
    for template in load_record_template_library()["templates"]:
        key = (template["section_code"], template["unit"])
        representative_by_unit.setdefault(key, template)

    slots: list[dict[str, Any]] = []
    for (section_code, unit), row in sorted(
        representative_by_unit.items(),
        key=lambda item: template_order[item[0]],
    ):
        for template_type in TEMPLATE_SLOT_TYPES_BY_GROUP["verification_record"]:
            default_record_text = _default_slot_record_text("verification_record", template_type, row)
            slots.append(
                {
                    "section_code": section_code,
                    "table_type": row["table_type"],
                    "unit": unit,
                    "template_group": "verification_record",
                    "template_type": template_type,
                    "title": TEMPLATE_SLOT_TYPE_LABELS[template_type],
                    "record_text": default_record_text,
                    "default_record_text": default_record_text,
                    "tags": TEMPLATE_SLOT_TYPE_TAGS[template_type],
                }
            )

    technical_representative = next(
        (
            row
            for (section_code, _unit), row in sorted(representative_by_unit.items(), key=lambda item: template_order[item[0]])
            if section_code in TECHNICAL_SECTION_CODES
        ),
        None,
    )
    if technical_representative is not None:
        for template_type in TEMPLATE_SLOT_TYPES_BY_GROUP["score_basis"]:
            default_record_text = _default_slot_record_text("score_basis", template_type, technical_representative)
            slots.append(
                {
                    "section_code": TECHNICAL_SCORE_BASIS_SECTION_CODE,
                    "table_type": "technical",
                    "unit": TECHNICAL_SCORE_BASIS_UNIT,
                    "template_group": "score_basis",
                    "template_type": template_type,
                    "title": TEMPLATE_SLOT_TYPE_LABELS[template_type],
                    "record_text": default_record_text,
                    "default_record_text": default_record_text,
                    "tags": TEMPLATE_SLOT_TYPE_TAGS[template_type],
                }
            )
    return slots


def _template_unit_order() -> dict[tuple[str, str], int]:
    order: dict[tuple[str, str], int] = {}
    for template in load_record_template_library()["templates"]:
        key = (template["section_code"], template["unit"])
        order.setdefault(key, len(order))
    return order


def _sort_template_slot_rows(rows: list[Any]) -> list[Any]:
    template_order = _template_unit_order()
    template_group_order = {template_group: index for index, template_group in enumerate(TEMPLATE_SLOT_GROUPS)}
    template_type_order = {
        (template_group, template_type): index
        for template_group, template_types in TEMPLATE_SLOT_TYPES_BY_GROUP.items()
        for index, template_type in enumerate(template_types)
    }
    fallback_unit_order = len(template_order)

    def sort_key(row: Any) -> tuple[int, int, str, int, int, int]:
        section_code = row["section_code"]
        unit = row["unit"]
        template_group = row["template_group"]
        template_type = row["template_type"]
        return (
            _section_number(section_code),
            template_order.get((section_code, unit), fallback_unit_order),
            unit,
            template_group_order.get(template_group, len(template_group_order)),
            template_type_order.get((template_group, template_type), len(template_type_order)),
            int(row["id"] or 0),
        )

    return sorted(rows, key=sort_key)


def _filter_visible_template_slot_rows(
    rows: list[Any],
    section_code: str | None = None,
    unit: str | None = None,
) -> list[Any]:
    visible_rows: list[Any] = []
    for row in rows:
        row_group = row["template_group"]
        row_section = row["section_code"]
        row_unit = row["unit"]

        if row_group == "score_basis":
            is_global_score_basis = (
                row_section == TECHNICAL_SCORE_BASIS_SECTION_CODE
                and row_unit == TECHNICAL_SCORE_BASIS_UNIT
            )
            if not is_global_score_basis:
                continue
            if section_code is not None and section_code not in TECHNICAL_SECTION_CODES and section_code != TECHNICAL_SCORE_BASIS_SECTION_CODE:
                continue
            if unit is not None and unit != TECHNICAL_SCORE_BASIS_UNIT:
                continue
            visible_rows.append(row)
            continue

        if section_code is not None and row_section != section_code:
            continue
        if unit is not None and row_unit != unit:
            continue
        visible_rows.append(row)
    return visible_rows


def _section_number(section_code: str) -> int:
    if section_code == TECHNICAL_SCORE_BASIS_SECTION_CODE:
        return 4
    try:
        return int(section_code.split("-", 1)[1])
    except (IndexError, ValueError):
        return 999


def _default_slot_record_text(template_group: str, template_type: str, representative: Any) -> str:
    verification_text, score_basis_text = _split_record_template_sections(_clean_text(representative["record_text"]))
    if template_group == "verification_record":
        if template_type == "compliant":
            return verification_text or COMPLIANT_VERIFICATION_DEFAULT_TEXT
        if template_type == "non_compliant":
            return NON_COMPLIANT_DEFAULT_TEXT
        if template_type == "not_applicable":
            return NOT_APPLICABLE_DEFAULT_TEXT
    if template_group == "score_basis":
        if template_type == "fully_compliant":
            return score_basis_text or FULLY_COMPLIANT_SCORE_DEFAULT_TEXT
        if template_type == "score_adjusted":
            return SCORE_ADJUSTED_DEFAULT_TEXT
        if template_type == "non_compliant":
            return NON_COMPLIANT_SCORE_DEFAULT_TEXT
    raise RecordTemplateValidationError(f"未知结果记录模板类型：{template_group}/{template_type}")


def _split_record_template_sections(record_text: str) -> tuple[str, str]:
    score_index = record_text.find(SCORE_BASIS_MARKER)
    if score_index < 0:
        return record_text, ""
    verification_text = record_text[:score_index].strip()
    score_basis_text = record_text[score_index:].strip()
    return verification_text, score_basis_text

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


def _normalize_template_slot_update(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if "title" in payload and payload["title"] is not None:
        title = _clean_text(payload["title"])
        if not title:
            raise RecordTemplateValidationError("分段结果记录模板标题不能为空。")
        updates["title"] = title
    if "record_text" in payload and payload["record_text"] is not None:
        record_text = _clean_text(payload["record_text"])
        if not record_text:
            raise RecordTemplateValidationError("分段结果记录模板正文不能为空。")
        updates["record_text"] = record_text
    if "tags" in payload and payload["tags"] is not None:
        updates["tags"] = _normalize_tags(payload["tags"])

    _validate_template_slot_type(existing["template_type"], existing.get("template_group"))
    return updates


def _validate_template_slot_group(template_group: str) -> None:
    if template_group not in TEMPLATE_SLOT_GROUPS:
        valid_groups = "、".join(TEMPLATE_SLOT_GROUPS)
        raise RecordTemplateValidationError(f"结果记录模板分组必须为 {valid_groups}。")


def _validate_template_slot_type(template_type: str, template_group: str | None = None) -> None:
    if template_group:
        _validate_template_slot_group(template_group)
        valid_types = TEMPLATE_SLOT_TYPES_BY_GROUP[template_group]
        if template_type not in valid_types:
            valid_text = "、".join(valid_types)
            raise RecordTemplateValidationError(f"结果记录模板类型必须为 {valid_text}。")
        return
    if template_type not in TEMPLATE_SLOT_TYPES:
        valid_types = "、".join(TEMPLATE_SLOT_TYPES)
        raise RecordTemplateValidationError(f"结果记录模板类型必须为 {valid_types}。")
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




def _build_slot_import_plan(payload: dict[str, Any]) -> dict[str, Any]:
    raw_templates = payload.get("templates")
    if not isinstance(raw_templates, list):
        raise RecordTemplateValidationError("导入文件必须包含 templates 列表。")

    existing_slots = list_record_template_slots()
    slots_by_key = {_slot_key(slot): slot for slot in existing_slots}
    seen_keys: dict[tuple[str, str, str, str], int] = {}
    items: list[dict[str, Any]] = []

    for index, raw_template in enumerate(raw_templates, start=1):
        if not isinstance(raw_template, dict):
            items.append(_slot_import_item(index, "error", "模板槽位条目必须是对象。"))
            continue

        try:
            normalized = _normalize_template_slot_import_item(raw_template)
        except RecordTemplateValidationError as exc:
            items.append(_slot_import_item(index, "error", str(exc)))
            continue

        natural_key = _slot_key(normalized)
        if natural_key in seen_keys:
            items.append(
                _slot_import_item(
                    index,
                    "skip",
                    f"与本次导入第 {seen_keys[natural_key]} 条分段模板重复，已跳过。",
                    normalized,
                )
            )
            continue
        seen_keys[natural_key] = index

        existing = slots_by_key.get(natural_key)
        if existing is None:
            items.append(
                _slot_import_item(
                    index,
                    "error",
                    "未找到对应的分段模板槽位，导入不会创建新的测评单元或额外模板。",
                    normalized,
                )
            )
            continue

        if normalized["table_type"] != existing["table_type"]:
            items.append(
                _slot_import_item(
                    index,
                    "error",
                    f"表格类型不匹配，当前槽位为 {existing['table_type']}。",
                    normalized,
                    existing["id"],
                )
            )
            continue

        if _slot_payload_matches(existing, normalized):
            items.append(
                _slot_import_item(
                    index,
                    "skip",
                    "分段模板内容未变化，已跳过。",
                    normalized,
                    existing["id"],
                )
            )
            continue

        items.append(
            _slot_import_item(
                index,
                "update",
                "将更新已有分段模板槽位。",
                normalized,
                existing["id"],
            )
        )

    summary = {
        "created": 0,
        "updated": sum(1 for item in items if item["action"] == "update"),
        "skipped": sum(1 for item in items if item["action"] == "skip"),
        "errors": sum(1 for item in items if item["action"] == "error"),
    }
    return {
        "summary": summary,
        "items": items,
    }


def _normalize_template_slot_import_item(raw_template: dict[str, Any]) -> dict[str, Any]:
    section_code = _clean_text(raw_template.get("section_code"))
    table_type = _clean_text(raw_template.get("table_type"))
    unit = _clean_text(raw_template.get("unit"))
    template_group = _clean_text(raw_template.get("template_group")) or "verification_record"
    template_type = _clean_text(raw_template.get("template_type"))
    title = _clean_text(raw_template.get("title"))
    record_text = _clean_text(raw_template.get("record_text"))
    tags = _normalize_tags(raw_template.get("tags"))
    is_score_basis_import = template_group == "score_basis"
    if is_score_basis_import:
        _validate_template_slot_group(template_group)
        _validate_template_slot_type(template_type, template_group)
        if table_type != "technical":
            raise RecordTemplateValidationError("评分依据模板仅适用于 A-1 至 A-4 技术测评章节。")
        if section_code not in TECHNICAL_SECTION_CODES and section_code != TECHNICAL_SCORE_BASIS_SECTION_CODE:
            raise RecordTemplateValidationError("评分依据模板仅适用于 A-1 至 A-4 技术测评章节。")
        section_code = "A-1"
        unit = TECHNICAL_SCORE_BASIS_UNIT

    if section_code not in {f"A-{number}" for number in range(1, 9)}:
        raise RecordTemplateValidationError("分段模板章节必须为 A-1 至 A-8。")
    if table_type not in {"technical", "management"}:
        raise RecordTemplateValidationError("分段模板表格类型必须为 technical 或 management。")
    if not unit:
        raise RecordTemplateValidationError("分段模板测评单元不能为空。")
    _validate_template_slot_group(template_group)
    _validate_template_slot_type(template_type, template_group)
    if is_score_basis_import:
        section_code = TECHNICAL_SCORE_BASIS_SECTION_CODE
        unit = TECHNICAL_SCORE_BASIS_UNIT
    if not title:
        title = TEMPLATE_SLOT_TYPE_LABELS[template_type]
    if not record_text:
        raise RecordTemplateValidationError("分段模板正文不能为空。")

    return {
        "section_code": section_code,
        "table_type": table_type,
        "unit": unit,
        "template_group": template_group,
        "template_type": template_type,
        "title": title,
        "record_text": record_text,
        "tags": tags,
    }


def _slot_import_item(
    index: int,
    action: str,
    message: str,
    payload: dict[str, Any] | None = None,
    slot_id: int | None = None,
) -> dict[str, Any]:
    return {
        "index": index,
        "action": action,
        "message": message,
        "slot_id": slot_id,
        "payload": payload,
    }


def _public_slot_import_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") or {}
    return {
        "index": item["index"],
        "action": item["action"],
        "message": item["message"],
        "slot_id": item.get("slot_id"),
        "section_code": payload.get("section_code", ""),
        "unit": payload.get("unit", ""),
        "template_group": payload.get("template_group", ""),
        "template_type": payload.get("template_type", ""),
        "title": payload.get("title", ""),
    }


def _export_template_slot(slot: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_code": slot["section_code"],
        "table_type": slot["table_type"],
        "unit": slot["unit"],
        "template_group": slot["template_group"],
        "template_type": slot["template_type"],
        "title": slot["title"],
        "record_text": slot["record_text"],
        "tags": slot["tags"],
    }


def _slot_key(slot: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        slot["section_code"],
        slot["unit"],
        slot.get("template_group") or "verification_record",
        slot["template_type"],
    )


def _slot_payload_matches(existing: dict[str, Any], payload: dict[str, Any]) -> bool:
    return all(
        existing[key] == payload[key]
        for key in ["section_code", "table_type", "unit", "template_group", "template_type", "title", "record_text", "tags"]
    )

def _build_import_plan(payload: dict[str, Any]) -> dict[str, Any]:
    raw_templates = payload.get("templates")
    if not isinstance(raw_templates, list):
        raise RecordTemplateValidationError("导入文件必须包含 templates 列表。")

    existing_templates = [_row_to_template(row) for row in database.list_record_template_rows()]
    user_by_id = {template["id"]: template for template in existing_templates if template["source_type"] == "user"}
    user_by_key = {_natural_key(template): template for template in user_by_id.values()}
    seen_keys: dict[tuple[str, str, str, str, str], int] = {}
    items: list[dict[str, Any]] = []

    for index, raw_template in enumerate(raw_templates, start=1):
        if not isinstance(raw_template, dict):
            items.append(_import_item(index, "error", "模板条目必须是对象。"))
            continue

        try:
            normalized = _normalize_template_payload(raw_template, require_all=True)
        except RecordTemplateValidationError as exc:
            items.append(_import_item(index, "error", str(exc)))
            continue

        natural_key = _natural_key(normalized)
        if natural_key in seen_keys:
            items.append(
                _import_item(
                    index,
                    "skip",
                    f"与本次导入第 {seen_keys[natural_key]} 条模板重复，已跳过。",
                    normalized,
                )
            )
            continue
        seen_keys[natural_key] = index

        imported_id = _clean_text(raw_template.get("id") or raw_template.get("template_key"))
        existing_user = user_by_id.get(imported_id) if imported_id else None
        if existing_user is None:
            existing_user = user_by_key.get(natural_key)

        if existing_user is None:
            items.append(_import_item(index, "create", "将新增为用户模板。", normalized))
            continue

        if _template_payload_matches(existing_user, normalized):
            items.append(
                _import_item(
                    index,
                    "skip",
                    "已存在相同用户模板，已跳过。",
                    normalized,
                    existing_user["id"],
                )
            )
            continue

        items.append(
            _import_item(
                index,
                "update",
                "将更新已有用户模板。",
                normalized,
                existing_user["id"],
            )
        )

    summary = {
        "created": sum(1 for item in items if item["action"] == "create"),
        "updated": sum(1 for item in items if item["action"] == "update"),
        "skipped": sum(1 for item in items if item["action"] == "skip"),
        "errors": sum(1 for item in items if item["action"] == "error"),
    }
    return {
        "summary": summary,
        "items": items,
    }


def _import_item(
    index: int,
    action: str,
    message: str,
    payload: dict[str, Any] | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    return {
        "index": index,
        "action": action,
        "message": message,
        "template_id": template_id,
        "payload": payload,
    }


def _public_import_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") or {}
    return {
        "index": item["index"],
        "action": item["action"],
        "message": item["message"],
        "template_id": item.get("template_id"),
        "section_code": payload.get("section_code", ""),
        "unit": payload.get("unit", ""),
        "object_name": payload.get("object_name", ""),
        "title": payload.get("title", ""),
    }


def _export_template(template: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": template["id"],
        "section_code": template["section_code"],
        "table_type": template["table_type"],
        "unit": template["unit"],
        "object_name": template["object_name"],
        "title": template["title"],
        "record_text": template["record_text"],
        "tags": template["tags"],
    }


def _natural_key(template: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        template["section_code"],
        template["table_type"],
        template["unit"],
        template["object_name"],
        template["title"],
    )


def _template_payload_matches(existing: dict[str, Any], payload: dict[str, Any]) -> bool:
    return all(
        existing[key] == payload[key]
        for key in ["section_code", "table_type", "unit", "object_name", "title", "record_text", "tags"]
    )

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


def _row_to_template_slot(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "section_code": row["section_code"],
        "table_type": row["table_type"],
        "unit": row["unit"],
        "template_group": row["template_group"],
        "template_group_label": TEMPLATE_SLOT_GROUP_LABELS.get(row["template_group"], row["template_group"]),
        "template_type": row["template_type"],
        "template_type_label": TEMPLATE_SLOT_TYPE_LABELS.get(row["template_type"], row["template_type"]),
        "title": row["title"],
        "record_text": row["record_text"],
        "default_record_text": row["default_record_text"],
        "tags": _parse_tags(row["tags"]),
        "is_customized": bool(row["is_customized"]),
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
