"""R0 JSON 资产的严格契约校验。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .models import (
    EXPECTED_BUSINESS_FIELD_COUNT,
    REQUIRED_PROJECTION_CATALOG,
    REQUIRED_README_RULE_REFS,
    FieldDictionary,
    NarrativeTemplateLibrary,
    RuleHintLibrary,
)

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


def load_json_model_bytes(raw: bytes, model: type[T]) -> T:
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("JSON_SIZE_LIMIT_EXCEEDED")
    parsed = json.loads(
        raw,
        object_pairs_hook=_no_duplicate_pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("JSON_NON_FINITE_NUMBER")),
    )
    return model.model_validate(parsed)


def load_json_model(path: Path, model: type[T]) -> T:
    return load_json_model_bytes(path.read_bytes(), model)


def validate_field_dictionary(path: Path) -> FieldDictionary:
    return validate_field_dictionary_bytes(path.read_bytes())


def validate_field_dictionary_bytes(raw: bytes) -> FieldDictionary:
    result = load_json_model_bytes(raw, FieldDictionary)
    if len(result.fields) != EXPECTED_BUSINESS_FIELD_COUNT:
        raise ValueError("BUSINESS_FIELD_COUNT_INVALID")
    ids = [item.field_id for item in result.fields]
    if len(ids) != len(set(ids)):
        raise ValueError("FIELD_ID_DUPLICATE")
    slots = [slot for item in result.fields for slot in item.export_slots]
    if len(slots) != len(set(slots)):
        raise ValueError("EXPORT_SLOT_DUPLICATE")
    governed = [parameter for item in result.fields for parameter in item.governed_parameter_ids]
    if len(governed) != len(set(governed)):
        raise ValueError("GOVERNED_PARAMETER_ID_DUPLICATE")
    governed_index = {
        parameter: field
        for field in result.fields
        for parameter in field.governed_parameter_ids
    }
    for field in result.fields:
        if field.field_id not in field.governed_parameter_ids:
            raise ValueError("FIELD_ID_NOT_GOVERNED")
        if len(field.accepted_input_kinds) != len(set(field.accepted_input_kinds)):
            raise ValueError("ACCEPTED_INPUT_KIND_DUPLICATE")
        if len(field.readme_rule_refs) != len(set(field.readme_rule_refs)):
            raise ValueError("README_RULE_REF_DUPLICATE")
        if any(not re.fullmatch(r"[a-z][a-z0-9_.]{2,127}", value) for value in field.governed_parameter_ids):
            raise ValueError("GOVERNED_PARAMETER_ID_INVALID")
        if any(not re.fullmatch(r"3\.6\.[1-6]\.[0-9]{2}", value) for value in field.readme_rule_refs):
            raise ValueError("README_RULE_REF_INVALID")
        if field.source_kind in {"derived", "template_constant"}:
            if field.editable or field.accepted_input_kinds:
                raise ValueError("READ_ONLY_SOURCE_CONTRACT_INVALID")
        elif not field.editable or field.source_kind not in field.accepted_input_kinds:
            raise ValueError("EDITABLE_SOURCE_CONTRACT_INVALID")
        if field.source_kind == "derived" and (
            field.missing_behavior != "derive_or_block_final"
            or field.conflict_behavior != "recompute_from_authority"
        ):
            raise ValueError("DERIVED_SOURCE_BEHAVIOR_INVALID")
        if field.source_kind == "template_constant" and (
            field.missing_behavior != "template_package_unavailable"
            or field.conflict_behavior != "template_package_unavailable"
        ):
            raise ValueError("TEMPLATE_CONSTANT_BEHAVIOR_INVALID")
    rule_refs = {rule for field in result.fields for rule in field.readme_rule_refs}
    if rule_refs != REQUIRED_README_RULE_REFS:
        raise ValueError("README_RULE_COVERAGE_INVALID")
    contract_refs = [contract.rule_ref for contract in result.rule_contracts]
    if len(contract_refs) != len(set(contract_refs)) or set(contract_refs) != REQUIRED_README_RULE_REFS:
        raise ValueError("README_RULE_CONTRACT_COVERAGE_INVALID")
    projection_ids = [
        projection
        for contract in result.rule_contracts
        for projection in contract.projection_ids
    ]
    if (
        len(result.projection_catalog) != len(set(result.projection_catalog))
        or set(result.projection_catalog) != REQUIRED_PROJECTION_CATALOG
        or set(projection_ids) != REQUIRED_PROJECTION_CATALOG
    ):
        raise ValueError("README_RULE_PROJECTION_CATALOG_MISMATCH")
    authority_contracts: dict[str, tuple[str, tuple[str, ...], bool]] = {}
    for contract in result.rule_contracts:
        if len(contract.projection_ids) != len(set(contract.projection_ids)) or any(
            not re.fullmatch(r"[a-z][a-z0-9_.:-]{2,127}", projection)
            for projection in contract.projection_ids
        ):
            raise ValueError("README_RULE_PROJECTION_INVALID")
        for projection in contract.projection_ids:
            kind, _, target = projection.partition(":")
            if kind in {"slot", "field"} and target not in governed_index:
                raise ValueError("README_RULE_PROJECTION_TARGET_UNKNOWN")
        authority_ids = [authority.authority_id for authority in contract.authorities]
        if len(authority_ids) != len(set(authority_ids)):
            raise ValueError("README_RULE_AUTHORITY_DUPLICATE")
        for authority in contract.authorities:
            governing_field = governed_index.get(authority.authority_id)
            if governing_field is None:
                raise ValueError("README_RULE_AUTHORITY_UNGOVERNED")
            if contract.rule_ref not in governing_field.readme_rule_refs:
                raise ValueError("README_RULE_AUTHORITY_TRACE_MISSING")
            if len(authority.accepted_input_kinds) != len(set(authority.accepted_input_kinds)):
                raise ValueError("README_RULE_INPUT_KIND_DUPLICATE")
            if authority.source_kind in {"derived", "template_constant"}:
                if authority.editable or authority.accepted_input_kinds:
                    raise ValueError("README_RULE_READ_ONLY_AUTHORITY_INVALID")
            elif not authority.editable or authority.source_kind not in authority.accepted_input_kinds:
                raise ValueError("README_RULE_EDITABLE_AUTHORITY_INVALID")
            authority_signature = (
                authority.source_kind,
                tuple(authority.accepted_input_kinds),
                authority.editable,
            )
            previous = authority_contracts.setdefault(authority.authority_id, authority_signature)
            if previous != authority_signature:
                raise ValueError("README_RULE_AUTHORITY_CONFLICT")
    for field in result.fields:
        authority_signature = authority_contracts.get(field.field_id)
        if authority_signature is not None and authority_signature != (
            field.source_kind,
            tuple(field.accepted_input_kinds),
            field.editable,
        ):
            raise ValueError("FIELD_RULE_AUTHORITY_CONFLICT")
    return result


def validate_rule_hints(path: Path) -> RuleHintLibrary:
    return validate_rule_hints_bytes(path.read_bytes())


def validate_rule_hints_bytes(raw: bytes) -> RuleHintLibrary:
    result = load_json_model_bytes(raw, RuleHintLibrary)
    ids = [item.source_comment_id for item in result.rules]
    if len(ids) != 121 or len(set(ids)) != 121:
        raise ValueError("RULE_HINT_COVERAGE_INVALID")
    for rule in result.rules:
        if rule.approval_status != "approved" and rule.runtime_behavior != "none":
            raise ValueError("RULE_HINT_UNAPPROVED_BEHAVIOR")
        if rule.runtime_behavior not in {"none", "help", "warning"}:
            raise ValueError("RULE_HINT_BLOCKING_FORBIDDEN")
    return result


def validate_narrative_templates(path: Path) -> NarrativeTemplateLibrary:
    return validate_narrative_templates_bytes(path.read_bytes())


def validate_narrative_templates_bytes(raw: bytes) -> NarrativeTemplateLibrary:
    result = load_json_model_bytes(raw, NarrativeTemplateLibrary)
    ids = [item.template_id for item in result.templates]
    if len(ids) != len(set(ids)):
        raise ValueError("NARRATIVE_ID_DUPLICATE")
    payload = raw.decode("utf-8")
    if any(token in payload for token in FORBIDDEN_RUNTIME_TEXT):
        raise ValueError("NARRATIVE_FORBIDDEN_TEXT")
    if re.search(r"\b1[3-9]\d{9}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:\d{1,3}\.){3}\d{1,3}\b", payload):
        raise ValueError("NARRATIVE_SENSITIVE_PATTERN")
    return result
