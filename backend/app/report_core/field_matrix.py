"""R2 报告字段及参数关联矩阵的只读加载与启动校验。

矩阵是 R0 第 3.6 节业务基线到 R2/R3/R4/R5 实现的机器可读桥梁。
本模块只校验契约，不执行评分、正文派生或 Word 装配。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from ..resource_paths import resolve_resource_path


MATRIX_RELATIVE_PATH = (
    "templates",
    "report",
    "contracts",
    "2023-2025.12.08",
    "field_relation_matrix.v1.json",
)
FIELD_DICTIONARY_RELATIVE_PATH = (
    "templates",
    "report",
    "2023-2025.12.08",
    "field_dictionary.json",
)
MANIFEST_RELATIVE_PATH = (
    "templates",
    "report",
    "2023-2025.12.08",
    "manifest.json",
)

EXPECTED_SCHEMA_VERSION = "1.0"
EXPECTED_PACKAGE_ID = "report-2023-2025.12.08"
EXPECTED_SECTIONS = frozenset(f"3.6.{index}" for index in range(1, 7))
EXPECTED_SOURCE_KINDS = frozenset({"manual", "derived", "template_constant"})
EXPECTED_RELATION_TYPES = frozenset(
    {
        "aggregation_input",
        "conditional_requirement",
        "derived_output_slot",
        "referential_integrity",
        "repeated_projection",
        "staleness",
        "validation",
    }
)
EXPECTED_MISSING_BEHAVIORS = frozenset(
    {
        "allow_draft_block_final",
        "allow_empty",
        "derive_or_block_final",
        "render_empty_structure",
        "template_package_unavailable",
    }
)
EXPECTED_CONFLICT_BEHAVIORS = frozenset(
    {
        "preserve_and_warn",
        "recompute_from_authority",
        "reject",
        "require_confirmation",
        "template_package_unavailable",
    }
)
EXPECTED_IMPLEMENTATION_STAGES = frozenset({"R2", "R3", "R4", "R5"})
EXPECTED_CONFIRMATION_STATUSES = frozenset({"confirmed"})

_RELATION_ID = re.compile(r"^FRM-3\.6\.[1-6]\.\d{2}$")
_RULE_REF = re.compile(r"^3\.6\.[1-6]\.\d{2}$")
_FIELD_ID = re.compile(r"^report\.[a-z0-9_]+(?:\.[a-z0-9_]+)+$")
_ENTITY_PATH = re.compile(
    r"^[a-z][a-z0-9_]*(?:\[[a-z0-9_*=-]+\])?"
    r"(?:\.[a-z][a-z0-9_]*(?:\[[a-z0-9_*=-]+\])?)*$"
)
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]+$")
_TEST_VECTOR_ID = re.compile(
    r"^TV-3\.6\.[1-6]\.\d{2}-(?:authority|missing|conflict)$"
)
_EXPORT_MAPPING_ID = re.compile(r"^EM-3\.6\.[1-6]\.\d{2}-\d{2}$")

_TOP_LEVEL_REQUIRED = frozenset(
    {
        "schema_version",
        "matrix_id",
        "matrix_version",
        "package_id",
        "template_edition",
        "template_revision",
        "source_contracts",
        "word_roundtrip_policy",
        "closed_sets",
        "field_catalog",
        "relations",
    }
)
_FIELD_REQUIRED = frozenset(
    {"field_id", "entity_paths", "source_kind", "editable"}
)
_RELATION_REQUIRED = frozenset(
    {
        "relation_id",
        "readme_rule_ref",
        "authority_field_id",
        "authority_paths",
        "reference_field_ids",
        "reference_paths",
        "target_ids",
        "relation_type",
        "source_kind",
        "editable",
        "constraint_expression",
        "missing_behavior",
        "conflict_behavior",
        "implemented_in",
        "confirmation_status",
        "test_vector_ids",
        "export_mapping_ids",
        "export_stage",
    }
)


class FieldMatrixValidationError(RuntimeError):
    """矩阵损坏、漂移或无法追溯时的稳定启动错误。"""

    def __init__(
        self,
        code: str,
        *,
        location: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.location = location
        self.details = details or {}


@dataclass(frozen=True)
class FieldBinding:
    field_id: str
    entity_paths: tuple[str, ...]
    source_kind: str
    editable: bool


@dataclass(frozen=True)
class FailureBehavior:
    action: str
    code: str
    severity: str


@dataclass(frozen=True)
class FieldRelation:
    relation_id: str
    readme_rule_ref: str
    authority_field_id: str
    authority_paths: tuple[str, ...]
    reference_field_ids: tuple[str, ...]
    reference_paths: tuple[str, ...]
    target_ids: tuple[str, ...]
    relation_type: str
    source_kind: str
    editable: bool
    constraint_expression: str
    missing_behavior: FailureBehavior
    conflict_behavior: FailureBehavior
    implemented_in: str
    confirmation_status: str
    test_vector_ids: tuple[str, ...]
    export_mapping_ids: tuple[str, ...]
    export_stage: str


@dataclass(frozen=True)
class FieldMatrix:
    matrix_id: str
    matrix_version: str
    package_id: str
    template_edition: str
    template_revision: str
    fields: tuple[FieldBinding, ...]
    relations: tuple[FieldRelation, ...]
    roundtrip_policy: dict[str, Any]
    sha256: str

    def relation(self, relation_id: str) -> FieldRelation:
        for item in self.relations:
            if item.relation_id == relation_id:
                return item
        raise KeyError(relation_id)

    @property
    def covered_rule_refs(self) -> frozenset[str]:
        return frozenset(item.readme_rule_ref for item in self.relations)


def _fail(
    code: str,
    *,
    location: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    raise FieldMatrixValidationError(code, location=location, details=details)


def _validate_roundtrip_policy(
    value: Any,
    fields_by_id: dict[str, FieldBinding],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "policy_version", "scalar_slots", "appendix_a_columns", "forbidden_entity_paths"
    }:
        _fail("FIELD_MATRIX_ROUNDTRIP_POLICY_INVALID", location="word_roundtrip_policy")
    if value.get("policy_version") != "R7.1":
        _fail("FIELD_MATRIX_ROUNDTRIP_POLICY_INVALID", location="word_roundtrip_policy.policy_version")
    forbidden = _require_string_list(
        value.get("forbidden_entity_paths"),
        location="word_roundtrip_policy.forbidden_entity_paths",
    )
    if set(forbidden) != {
        "metric_results[*].ra", "metric_results[*].rk",
        "metric_results[*].object_score", "metric_results[*].unit_score",
    }:
        _fail("FIELD_MATRIX_ROUNDTRIP_FORBIDDEN_SET_INVALID", location="word_roundtrip_policy")
    entries = []
    for group in ("scalar_slots", "appendix_a_columns"):
        items = value.get(group)
        if not isinstance(items, list) or not items:
            _fail("FIELD_MATRIX_ROUNDTRIP_POLICY_INVALID", location=f"word_roundtrip_policy.{group}")
        entries.extend(items)
    seen_paths: set[str] = set()
    for index, item in enumerate(entries):
        location = f"word_roundtrip_policy.entry[{index}]"
        if not isinstance(item, dict):
            _fail("FIELD_MATRIX_ROUNDTRIP_POLICY_INVALID", location=location)
        field_id = _require_string(item.get("authority_field_id"), location=f"{location}.authority_field_id")
        binding = fields_by_id.get(field_id)
        if binding is None or not binding.editable or binding.source_kind != "manual":
            _fail("FIELD_MATRIX_ROUNDTRIP_AUTHORITY_INVALID", location=field_id)
        path = _require_string(item.get("entity_path"), location=f"{location}.entity_path")
        if path in forbidden or path in seen_paths:
            _fail("FIELD_MATRIX_ROUNDTRIP_PATH_INVALID", location=path)
        if any(token in path.lower() for token in (".ra", ".rk", "object_score", "unit_score")):
            _fail("FIELD_MATRIX_ROUNDTRIP_PATH_INVALID", location=path)
        if item.get("value_type") not in {"text", "multiline", "date", "enum"}:
            _fail("FIELD_MATRIX_ROUNDTRIP_POLICY_INVALID", location=f"{location}.value_type")
        if item.get("normalizer_id") not in {"exact_v1", "trim_v1", "multiline_v1", "date_iso_v1", "enum_v1"}:
            _fail("FIELD_MATRIX_ROUNDTRIP_POLICY_INVALID", location=f"{location}.normalizer_id")
        seen_paths.add(path)
    return value


def _load_json_object(path: Path, *, asset: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FieldMatrixValidationError(
            "FIELD_MATRIX_ASSET_UNAVAILABLE",
            location=asset,
        ) from exc
    if not isinstance(value, dict):
        _fail("FIELD_MATRIX_ASSET_INVALID", location=asset)
    return value, raw


def _require_keys(value: dict[str, Any], required: frozenset[str], location: str) -> None:
    missing = sorted(required - value.keys())
    if missing:
        _fail(
            "FIELD_MATRIX_REQUIRED_FIELD_MISSING",
            location=location,
            details={"fields": missing},
        )


def _require_string(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("FIELD_MATRIX_VALUE_INVALID", location=location)
    return value


def _require_string_list(
    value: Any,
    *,
    location: str,
    allow_empty: bool = False,
    allow_duplicates: bool = False,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item for item in value)
        or (not allow_duplicates and len(set(value)) != len(value))
    ):
        _fail("FIELD_MATRIX_VALUE_INVALID", location=location)
    return tuple(value)


def _validate_closed_sets(value: Any) -> None:
    if not isinstance(value, dict):
        _fail("FIELD_MATRIX_CLOSED_SET_INVALID", location="closed_sets")
    expected = {
        "source_kind": EXPECTED_SOURCE_KINDS,
        "relation_type": EXPECTED_RELATION_TYPES,
        "missing_behavior": EXPECTED_MISSING_BEHAVIORS,
        "conflict_behavior": EXPECTED_CONFLICT_BEHAVIORS,
        "implemented_in": EXPECTED_IMPLEMENTATION_STAGES,
        "confirmation_status": EXPECTED_CONFIRMATION_STATUSES,
    }
    if set(value) != set(expected):
        _fail("FIELD_MATRIX_CLOSED_SET_INVALID", location="closed_sets")
    for name, accepted in expected.items():
        candidate = value.get(name)
        if not isinstance(candidate, list) or frozenset(candidate) != accepted or len(candidate) != len(accepted):
            _fail(
                "FIELD_MATRIX_CLOSED_SET_INVALID",
                location=f"closed_sets.{name}",
            )


def _validate_behavior(
    value: Any,
    *,
    accepted_actions: frozenset[str],
    expected_action: str,
    location: str,
) -> FailureBehavior:
    if not isinstance(value, dict) or set(value) != {"action", "code", "severity"}:
        _fail("FIELD_MATRIX_BEHAVIOR_INVALID", location=location)
    action = value.get("action")
    code = value.get("code")
    severity = value.get("severity")
    if (
        action not in accepted_actions
        or action != expected_action
        or not isinstance(code, str)
        or not _ERROR_CODE.fullmatch(code)
        or severity not in {"error", "warning"}
    ):
        _fail("FIELD_MATRIX_BEHAVIOR_INVALID", location=location)
    return FailureBehavior(action, code, severity)


def _field_contracts(rule_contracts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    for rule in rule_contracts:
        authorities = rule.get("authorities")
        if not isinstance(authorities, list):
            _fail("FIELD_MATRIX_SOURCE_CONTRACT_INVALID", location="field_dictionary.json")
        for authority in authorities:
            if not isinstance(authority, dict):
                _fail("FIELD_MATRIX_SOURCE_CONTRACT_INVALID", location="field_dictionary.json")
            field_id = authority.get("authority_id")
            contract = {
                "source_kind": authority.get("source_kind"),
                "editable": authority.get("editable"),
            }
            if not isinstance(field_id, str):
                _fail("FIELD_MATRIX_SOURCE_CONTRACT_INVALID", location="field_dictionary.json")
            previous = expected.setdefault(field_id, contract)
            if previous != contract:
                _fail(
                    "FIELD_MATRIX_AUTHORITY_CONFLICT",
                    location=f"field_dictionary.json:{field_id}",
                )
    return expected


def load_field_matrix(
    matrix_path: Path,
    *,
    field_dictionary_path: Path,
    manifest_path: Path,
    known_entity_paths: Iterable[str] | None = None,
) -> FieldMatrix:
    """读取并严格验证一份矩阵；任何漂移均以稳定错误码失败。"""

    matrix, matrix_raw = _load_json_object(matrix_path, asset=matrix_path.name)
    dictionary, dictionary_raw = _load_json_object(
        field_dictionary_path,
        asset=field_dictionary_path.name,
    )
    manifest, manifest_raw = _load_json_object(manifest_path, asset=manifest_path.name)
    _require_keys(matrix, _TOP_LEVEL_REQUIRED, "matrix")

    if matrix.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        _fail("FIELD_MATRIX_SCHEMA_UNSUPPORTED", location="schema_version")
    if (
        matrix.get("package_id") != EXPECTED_PACKAGE_ID
        or matrix.get("package_id") != dictionary.get("package_id")
        or matrix.get("package_id") != manifest.get("package_id")
        or matrix.get("template_edition") != manifest.get("template_edition")
        or matrix.get("template_revision") != manifest.get("template_revision")
    ):
        _fail("FIELD_MATRIX_TEMPLATE_IDENTITY_MISMATCH", location="matrix")

    source_contracts = matrix.get("source_contracts")
    expected_source_contracts = {
        "field_dictionary_sha256": hashlib.sha256(dictionary_raw).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
    }
    if source_contracts != expected_source_contracts:
        _fail("FIELD_MATRIX_SOURCE_HASH_MISMATCH", location="source_contracts")
    _validate_closed_sets(matrix.get("closed_sets"))

    rule_contracts = dictionary.get("rule_contracts")
    projection_catalog = dictionary.get("projection_catalog")
    if not isinstance(rule_contracts, list) or not isinstance(projection_catalog, list):
        _fail("FIELD_MATRIX_SOURCE_CONTRACT_INVALID", location="field_dictionary.json")
    rules_by_ref: dict[str, dict[str, Any]] = {}
    for rule in rule_contracts:
        if not isinstance(rule, dict) or not isinstance(rule.get("rule_ref"), str):
            _fail("FIELD_MATRIX_SOURCE_CONTRACT_INVALID", location="field_dictionary.json")
        rule_ref = rule["rule_ref"]
        if rule_ref in rules_by_ref:
            _fail("FIELD_MATRIX_SOURCE_RULE_DUPLICATE", location=rule_ref)
        rules_by_ref[rule_ref] = rule
    expected_rules = frozenset(rules_by_ref)
    if (
        not expected_rules
        or {".".join(value.split(".")[:3]) for value in expected_rules} != EXPECTED_SECTIONS
        or any(not _RULE_REF.fullmatch(value) for value in expected_rules)
    ):
        _fail("FIELD_MATRIX_SOURCE_COVERAGE_INVALID", location="field_dictionary.json")

    expected_fields = _field_contracts(rule_contracts)
    field_items = matrix.get("field_catalog")
    if not isinstance(field_items, list):
        _fail("FIELD_MATRIX_VALUE_INVALID", location="field_catalog")
    fields: list[FieldBinding] = []
    fields_by_id: dict[str, FieldBinding] = {}
    known_paths = frozenset(known_entity_paths) if known_entity_paths is not None else None
    for index, item in enumerate(field_items):
        location = f"field_catalog[{index}]"
        if not isinstance(item, dict):
            _fail("FIELD_MATRIX_VALUE_INVALID", location=location)
        _require_keys(item, _FIELD_REQUIRED, location)
        field_id = _require_string(item.get("field_id"), location=f"{location}.field_id")
        paths = _require_string_list(item.get("entity_paths"), location=f"{location}.entity_paths")
        source_kind = item.get("source_kind")
        editable = item.get("editable")
        if not _FIELD_ID.fullmatch(field_id):
            _fail("FIELD_MATRIX_FIELD_ID_INVALID", location=f"{location}.field_id")
        if field_id in fields_by_id:
            _fail("FIELD_MATRIX_DUPLICATE_FIELD_ID", location=field_id)
        if source_kind not in EXPECTED_SOURCE_KINDS or not isinstance(editable, bool):
            _fail("FIELD_MATRIX_SOURCE_KIND_INVALID", location=field_id)
        if source_kind != "manual" and editable:
            _fail("FIELD_MATRIX_EDIT_POLICY_INVALID", location=field_id)
        source_contract = expected_fields.get(field_id)
        if source_contract != {"source_kind": source_kind, "editable": editable}:
            _fail("FIELD_MATRIX_AUTHORITY_CONTRACT_MISMATCH", location=field_id)
        for path in paths:
            if not _ENTITY_PATH.fullmatch(path):
                _fail("FIELD_MATRIX_ENTITY_PATH_INVALID", location=path)
            if known_paths is not None and path not in known_paths:
                _fail("FIELD_MATRIX_ENTITY_PATH_UNKNOWN", location=path)
        binding = FieldBinding(field_id, paths, source_kind, editable)
        fields.append(binding)
        fields_by_id[field_id] = binding
    if set(fields_by_id) != set(expected_fields):
        _fail(
            "FIELD_MATRIX_FIELD_COVERAGE_INVALID",
            location="field_catalog",
            details={
                "missing": sorted(set(expected_fields) - set(fields_by_id)),
                "unexpected": sorted(set(fields_by_id) - set(expected_fields)),
            },
        )

    relation_items = matrix.get("relations")
    if not isinstance(relation_items, list):
        _fail("FIELD_MATRIX_VALUE_INVALID", location="relations")
    relations: list[FieldRelation] = []
    relation_ids: set[str] = set()
    covered_rules: set[str] = set()
    vector_ids: set[str] = set()
    mapping_ids: set[str] = set()
    projection_ids = frozenset(projection_catalog)
    for index, item in enumerate(relation_items):
        location = f"relations[{index}]"
        if not isinstance(item, dict):
            _fail("FIELD_MATRIX_VALUE_INVALID", location=location)
        _require_keys(item, _RELATION_REQUIRED, location)
        relation_id = _require_string(item.get("relation_id"), location=f"{location}.relation_id")
        rule_ref = _require_string(item.get("readme_rule_ref"), location=f"{location}.readme_rule_ref")
        if not _RELATION_ID.fullmatch(relation_id):
            _fail("FIELD_MATRIX_RELATION_ID_INVALID", location=relation_id)
        if relation_id in relation_ids:
            _fail("FIELD_MATRIX_DUPLICATE_RELATION_ID", location=relation_id)
        if relation_id != f"FRM-{rule_ref}":
            _fail("FIELD_MATRIX_RELATION_ID_INVALID", location=relation_id)
        if rule_ref in covered_rules:
            _fail("FIELD_MATRIX_DUPLICATE_RULE_REF", location=rule_ref)
        source_rule = rules_by_ref.get(rule_ref)
        if source_rule is None:
            _fail("FIELD_MATRIX_RULE_REF_UNKNOWN", location=rule_ref)

        authority_field_id = _require_string(
            item.get("authority_field_id"),
            location=f"{location}.authority_field_id",
        )
        authority = fields_by_id.get(authority_field_id)
        if authority is None:
            _fail("FIELD_MATRIX_AUTHORITY_UNKNOWN", location=authority_field_id)
        authority_paths = _require_string_list(
            item.get("authority_paths"),
            location=f"{location}.authority_paths",
        )
        if authority_paths != authority.entity_paths:
            _fail("FIELD_MATRIX_AUTHORITY_PATH_MISMATCH", location=relation_id)
        reference_field_ids = _require_string_list(
            item.get("reference_field_ids"),
            location=f"{location}.reference_field_ids",
            allow_empty=True,
        )
        reference_paths = _require_string_list(
            item.get("reference_paths"),
            location=f"{location}.reference_paths",
            allow_empty=True,
            allow_duplicates=True,
        )
        expected_authority_ids = tuple(
            authority_item.get("authority_id")
            for authority_item in source_rule.get("authorities", [])
        )
        if (
            authority_field_id not in expected_authority_ids
            or set(reference_field_ids) != set(expected_authority_ids) - {authority_field_id}
            or len(reference_field_ids) != len(expected_authority_ids) - 1
        ):
            _fail("FIELD_MATRIX_AUTHORITY_SET_MISMATCH", location=relation_id)
        expected_reference_paths = tuple(
            path
            for field_id in reference_field_ids
            for path in fields_by_id[field_id].entity_paths
        )
        if reference_paths != expected_reference_paths:
            _fail("FIELD_MATRIX_REFERENCE_PATH_MISMATCH", location=relation_id)

        target_ids = _require_string_list(item.get("target_ids"), location=f"{location}.target_ids")
        if tuple(source_rule.get("projection_ids", [])) != target_ids or any(
            target not in projection_ids for target in target_ids
        ):
            _fail("FIELD_MATRIX_TARGET_MISMATCH", location=relation_id)
        relation_type = item.get("relation_type")
        source_kind = item.get("source_kind")
        editable = item.get("editable")
        if relation_type not in EXPECTED_RELATION_TYPES:
            _fail("FIELD_MATRIX_RELATION_TYPE_INVALID", location=relation_id)
        if source_kind not in EXPECTED_SOURCE_KINDS:
            _fail("FIELD_MATRIX_SOURCE_KIND_INVALID", location=relation_id)
        if source_kind != authority.source_kind or editable != authority.editable:
            _fail("FIELD_MATRIX_AUTHORITY_CONTRACT_MISMATCH", location=relation_id)
        constraint = _require_string(
            item.get("constraint_expression"),
            location=f"{location}.constraint_expression",
        )
        missing = _validate_behavior(
            item.get("missing_behavior"),
            accepted_actions=EXPECTED_MISSING_BEHAVIORS,
            expected_action=str(source_rule.get("missing_behavior")),
            location=f"{location}.missing_behavior",
        )
        conflict = _validate_behavior(
            item.get("conflict_behavior"),
            accepted_actions=EXPECTED_CONFLICT_BEHAVIORS,
            expected_action=str(source_rule.get("conflict_behavior")),
            location=f"{location}.conflict_behavior",
        )
        implemented_in = item.get("implemented_in")
        confirmation_status = item.get("confirmation_status")
        export_stage = item.get("export_stage")
        if implemented_in not in EXPECTED_IMPLEMENTATION_STAGES or implemented_in != source_rule.get("implementation_owner"):
            _fail("FIELD_MATRIX_IMPLEMENTATION_STAGE_INVALID", location=relation_id)
        if confirmation_status not in EXPECTED_CONFIRMATION_STATUSES:
            _fail("FIELD_MATRIX_CONFIRMATION_STATUS_INVALID", location=relation_id)
        if export_stage != "R4":
            _fail("FIELD_MATRIX_EXPORT_STAGE_INVALID", location=relation_id)
        tests = _require_string_list(
            item.get("test_vector_ids"),
            location=f"{location}.test_vector_ids",
        )
        expected_tests = tuple(
            f"TV-{rule_ref}-{suffix}" for suffix in ("authority", "missing", "conflict")
        )
        if tests != expected_tests or any(not _TEST_VECTOR_ID.fullmatch(value) for value in tests):
            _fail("FIELD_MATRIX_TEST_VECTOR_INVALID", location=relation_id)
        duplicate_test = next((value for value in tests if value in vector_ids), None)
        if duplicate_test:
            _fail("FIELD_MATRIX_DUPLICATE_TEST_VECTOR_ID", location=duplicate_test)
        vector_ids.update(tests)
        mappings = _require_string_list(
            item.get("export_mapping_ids"),
            location=f"{location}.export_mapping_ids",
        )
        expected_mappings = tuple(
            f"EM-{rule_ref}-{number:02d}" for number in range(1, len(target_ids) + 1)
        )
        if mappings != expected_mappings or any(not _EXPORT_MAPPING_ID.fullmatch(value) for value in mappings):
            _fail("FIELD_MATRIX_EXPORT_MAPPING_INVALID", location=relation_id)
        duplicate_mapping = next((value for value in mappings if value in mapping_ids), None)
        if duplicate_mapping:
            _fail("FIELD_MATRIX_DUPLICATE_EXPORT_MAPPING_ID", location=duplicate_mapping)
        mapping_ids.update(mappings)

        relations.append(
            FieldRelation(
                relation_id,
                rule_ref,
                authority_field_id,
                authority_paths,
                reference_field_ids,
                reference_paths,
                target_ids,
                relation_type,
                source_kind,
                editable,
                constraint,
                missing,
                conflict,
                implemented_in,
                confirmation_status,
                tests,
                mappings,
                export_stage,
            )
        )
        relation_ids.add(relation_id)
        covered_rules.add(rule_ref)

    if covered_rules != expected_rules:
        _fail(
            "FIELD_MATRIX_RULE_COVERAGE_INVALID",
            location="relations",
            details={
                "missing": sorted(expected_rules - covered_rules),
                "unexpected": sorted(covered_rules - expected_rules),
            },
        )

    roundtrip_policy = _validate_roundtrip_policy(
        matrix.get("word_roundtrip_policy"), fields_by_id
    )
    return FieldMatrix(
        matrix_id=_require_string(matrix.get("matrix_id"), location="matrix_id"),
        matrix_version=_require_string(matrix.get("matrix_version"), location="matrix_version"),
        package_id=str(matrix["package_id"]),
        template_edition=str(matrix["template_edition"]),
        template_revision=str(matrix["template_revision"]),
        fields=tuple(fields),
        relations=tuple(relations),
        roundtrip_policy=roundtrip_policy,
        sha256=hashlib.sha256(matrix_raw).hexdigest(),
    )


@lru_cache(maxsize=1)
def load_default_field_matrix() -> FieldMatrix:
    """加载随应用分发的矩阵；可直接由应用启动闸门调用。"""

    return load_field_matrix(
        resolve_resource_path(*MATRIX_RELATIVE_PATH),
        field_dictionary_path=resolve_resource_path(*FIELD_DICTIONARY_RELATIVE_PATH),
        manifest_path=resolve_resource_path(*MANIFEST_RELATIVE_PATH),
    )


def validate_default_field_matrix() -> None:
    """启动闸门入口：成功无返回，失败抛出稳定错误码。"""

    load_default_field_matrix()
