"""R0 JSON 资产的严格契约校验。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .models import FieldDictionary, NarrativeTemplateLibrary, RuleHintLibrary

T = TypeVar("T", bound=BaseModel)
MAX_JSON_BYTES = 2 * 1024 * 1024
FORBIDDEN_RUNTIME_TEXT = (
    "中国建设银行",
    "Ra",
    "Rk",
)


def _no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def load_json_model(path: Path, model: type[T]) -> T:
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("JSON_SIZE_LIMIT_EXCEEDED")
    parsed = json.loads(
        raw,
        object_pairs_hook=_no_duplicate_pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("JSON_NON_FINITE_NUMBER")),
    )
    return model.model_validate(parsed)


def validate_field_dictionary(path: Path) -> FieldDictionary:
    result = load_json_model(path, FieldDictionary)
    ids = [item.field_id for item in result.fields]
    if len(ids) != len(set(ids)):
        raise ValueError("FIELD_ID_DUPLICATE")
    slots = [slot for item in result.fields for slot in item.export_slots]
    if len(slots) != len(set(slots)):
        raise ValueError("EXPORT_SLOT_DUPLICATE")
    return result


def validate_rule_hints(path: Path) -> RuleHintLibrary:
    result = load_json_model(path, RuleHintLibrary)
    ids = [item.source_comment_id for item in result.rules]
    if len(ids) != 121 or len(set(ids)) != 121:
        raise ValueError("RULE_HINT_COVERAGE_INVALID")
    for rule in result.rules:
        if rule.approval_status != "approved" and rule.runtime_behavior not in {"none", "help", "warning"}:
            raise ValueError("RULE_HINT_BEHAVIOR_INVALID")
        if rule.runtime_behavior not in {"none", "help", "warning"}:
            raise ValueError("RULE_HINT_BLOCKING_FORBIDDEN")
    return result


def validate_narrative_templates(path: Path) -> NarrativeTemplateLibrary:
    result = load_json_model(path, NarrativeTemplateLibrary)
    ids = [item.template_id for item in result.templates]
    if len(ids) != len(set(ids)):
        raise ValueError("NARRATIVE_ID_DUPLICATE")
    payload = path.read_text(encoding="utf-8")
    if any(token in payload for token in FORBIDDEN_RUNTIME_TEXT):
        raise ValueError("NARRATIVE_FORBIDDEN_TEXT")
    if re.search(r"\b1[3-9]\d{9}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:\d{1,3}\.){3}\d{1,3}\b", payload):
        raise ValueError("NARRATIVE_SENSITIVE_PATTERN")
    return result
