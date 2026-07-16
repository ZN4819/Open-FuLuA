from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..resource_paths import resolve_resource_path


RULE_SET_ID = "report-derived-2023-2025.12.08-v1"
RULE_MATRIX_PATH = (
    "templates",
    "report",
    "contracts",
    "2023-2025.12.08",
    "derived_rule_matrix.v1.json",
)
EXPECTED_SCHEMA_VERSION = "1.0"
EXPECTED_PACKAGE_ID = "report-2023-2025.12.08"
ALLOWED_ALGORITHMS = frozenset(
    {
        "original_projection_v1",
        "bidirectional_correction_v1",
        "final_projection_v1",
        "fixed_indicator_statistics_v1",
        "finding_projection_v1",
        "risk_snapshot_v1",
        "weighted_score_v1",
        "assessment_conclusion_v1",
        "deterministic_narratives_v1",
        "consistency_gate_v1",
    }
)
ALLOWED_CONFIRMATION_POLICIES = frozenset(
    {
        "readonly",
        "manual_input_and_confirmation",
        "allowlisted_override",
        "all_current_blocks_confirmed",
    }
)
ALLOWED_INPUT_PREFIXES = (
    "appendix_a.",
    "assessment_conclusion",
    "assessment_",
    "final_projection",
    "indicator_weights",
    "layer_weights",
    "original_projection",
    "correction_projection",
    "report_",
    "result_correction_relations",
    "risk_snapshot",
    "source_hashes",
    "statistics",
    "overall_score",
    "threat_catalog",
)
ALLOWED_OUTPUT_PREFIXES = (
    "assessment_conclusion",
    "consistency_result",
    "correction_projection",
    "final_projection",
    "original_projection",
    "overall_score",
    "report_",
    "risk_snapshot",
    "r3_projection_context",
    "statistics",
)
FORBIDDEN_DYNAMIC_PATTERN = re.compile(
    r"(?:\beval\b|\bexec\b|\bimport\b|https?://|javascript:|__\w+__)",
    re.IGNORECASE,
)


class RuleSetUnavailable(RuntimeError):
    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


@dataclass(frozen=True)
class LayerRule:
    code: str
    section_code: str
    name: str
    category: str
    layer_weight: Decimal
    group_share: Decimal


@dataclass(frozen=True)
class IndicatorRule:
    code: str
    layer_code: str
    section_code: str
    name: str
    weight: Decimal


@dataclass(frozen=True)
class DerivedRuleSet:
    rule_set_id: str
    content_sha256: str
    layers: tuple[LayerRule, ...]
    indicators: tuple[IndicatorRule, ...]
    threat_catalog: tuple[dict[str, str], ...]
    rules: tuple[dict[str, Any], ...]
    golden_vectors: tuple[dict[str, Any], ...]

    @property
    def indicator_by_code(self) -> dict[str, IndicatorRule]:
        return {item.code: item for item in self.indicators}

    @property
    def indicator_by_identity(self) -> dict[tuple[str, str], IndicatorRule]:
        return {(item.section_code, item.name): item for item in self.indicators}

    @property
    def layer_by_code(self) -> dict[str, LayerRule]:
        return {item.code: item for item in self.layers}

    @property
    def threat_ids(self) -> frozenset[str]:
        return frozenset(item["id"] for item in self.threat_catalog)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RuleSetUnavailable("RULE_SET_NUMERIC_VALUE_INVALID", details={"field": field}) from exc
    if not result.is_finite() or result <= 0:
        raise RuleSetUnavailable("RULE_SET_NUMERIC_VALUE_INVALID", details={"field": field})
    return result


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise RuleSetUnavailable("RULE_SET_SCHEMA_INVALID", details={"field": field})
    return tuple(value)


def _validate_paths(values: tuple[str, ...], prefixes: tuple[str, ...], field: str) -> None:
    for value in values:
        if FORBIDDEN_DYNAMIC_PATTERN.search(value) or not value.startswith(prefixes):
            raise RuleSetUnavailable("RULE_SET_PATH_NOT_ALLOWED", details={"field": field, "path": value})


def _validate_dag(rules: list[dict[str, Any]]) -> None:
    ids = {str(rule["rule_id"]) for rule in rules}
    dependencies = {
        str(rule["rule_id"]): tuple(_strings(rule.get("depends_on"), f"{rule['rule_id']}.depends_on"))
        for rule in rules
    }
    if any(dependency not in ids for values in dependencies.values() for dependency in values):
        raise RuleSetUnavailable("RULE_SET_DEPENDENCY_UNKNOWN")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(rule_id: str) -> None:
        if rule_id in visiting:
            raise RuleSetUnavailable("RULE_SET_DEPENDENCY_CYCLE", details={"rule_id": rule_id})
        if rule_id in visited:
            return
        visiting.add(rule_id)
        for dependency in dependencies[rule_id]:
            visit(dependency)
        visiting.remove(rule_id)
        visited.add(rule_id)

    for rule_id in ids:
        visit(rule_id)


def load_rule_set(path: Path) -> DerivedRuleSet:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuleSetUnavailable("RULE_SET_FILE_UNAVAILABLE", details={"path": str(path)}) from exc
    if not isinstance(document, dict) or set(document) != {
        "schema_version", "rule_set_id", "content_sha256", "payload"
    }:
        raise RuleSetUnavailable("RULE_SET_SCHEMA_INVALID")
    if document["schema_version"] != EXPECTED_SCHEMA_VERSION or document["rule_set_id"] != RULE_SET_ID:
        raise RuleSetUnavailable("RULE_SET_IDENTITY_MISMATCH")
    payload = document["payload"]
    if not isinstance(payload, dict) or stable_hash(payload) != document["content_sha256"]:
        raise RuleSetUnavailable("RULE_SET_HASH_MISMATCH")
    identity = payload.get("template_identity")
    if not isinstance(identity, dict) or identity.get("package_id") != EXPECTED_PACKAGE_ID:
        raise RuleSetUnavailable("RULE_SET_TEMPLATE_MISMATCH")

    raw_layers = payload.get("layers")
    raw_indicators = payload.get("indicators")
    raw_threats = payload.get("threat_catalog")
    raw_rules = payload.get("rules")
    raw_vectors = payload.get("golden_vectors")
    if not all(isinstance(value, list) for value in (raw_layers, raw_indicators, raw_threats, raw_rules, raw_vectors)):
        raise RuleSetUnavailable("RULE_SET_SCHEMA_INVALID")
    if len(raw_layers) != 8 or len(raw_indicators) != 41 or len(raw_threats) != 24 or not raw_vectors:
        raise RuleSetUnavailable("RULE_SET_CARDINALITY_INVALID")

    layers: list[LayerRule] = []
    for index, raw in enumerate(raw_layers):
        if not isinstance(raw, dict) or set(raw) != {
            "code", "section_code", "name", "category", "layer_weight", "group_share"
        }:
            raise RuleSetUnavailable("RULE_SET_SCHEMA_INVALID", details={"field": f"layers.{index}"})
        if raw["category"] not in {"technical", "management"}:
            raise RuleSetUnavailable("RULE_SET_SCHEMA_INVALID", details={"field": f"layers.{index}.category"})
        layers.append(
            LayerRule(
                code=str(raw["code"]), section_code=str(raw["section_code"]), name=str(raw["name"]),
                category=str(raw["category"]), layer_weight=_decimal(raw["layer_weight"], "layer_weight"),
                group_share=_decimal(raw["group_share"], "group_share"),
            )
        )
    layer_codes = [item.code for item in layers]
    section_codes = [item.section_code for item in layers]
    if len(set(layer_codes)) != 8 or section_codes != [f"A-{index}" for index in range(1, 9)]:
        raise RuleSetUnavailable("RULE_SET_LAYER_CATALOG_INVALID")

    indicators: list[IndicatorRule] = []
    identities: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_indicators):
        if not isinstance(raw, dict) or set(raw) != {"code", "layer_code", "section_code", "name", "weight"}:
            raise RuleSetUnavailable("RULE_SET_SCHEMA_INVALID", details={"field": f"indicators.{index}"})
        identity_key = (str(raw["section_code"]), str(raw["name"]))
        if identity_key in identities or raw["layer_code"] not in layer_codes:
            raise RuleSetUnavailable("RULE_SET_INDICATOR_CATALOG_INVALID")
        identities.add(identity_key)
        indicators.append(
            IndicatorRule(
                code=str(raw["code"]), layer_code=str(raw["layer_code"]),
                section_code=str(raw["section_code"]), name=str(raw["name"]),
                weight=_decimal(raw["weight"], "indicator.weight"),
            )
        )
    if len({item.code for item in indicators}) != 41:
        raise RuleSetUnavailable("RULE_SET_INDICATOR_CATALOG_INVALID")

    threats: list[dict[str, str]] = []
    for index, raw in enumerate(raw_threats):
        if not isinstance(raw, dict) or set(raw) != {"id", "layer", "description"}:
            raise RuleSetUnavailable("RULE_SET_SCHEMA_INVALID", details={"field": f"threat_catalog.{index}"})
        threats.append({key: unicodedata.normalize("NFC", str(raw[key])) for key in ("id", "layer", "description")})
    if len({item["id"] for item in threats}) != 24:
        raise RuleSetUnavailable("RULE_SET_THREAT_CATALOG_INVALID")

    rules: list[dict[str, Any]] = []
    rule_ids: set[str] = set()
    required_rule_fields = {
        "rule_id", "source_contract_ids", "input_paths", "output_paths", "algorithm",
        "confirmation_policy", "stale_dependencies", "failure_code", "depends_on",
    }
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, dict) or set(raw) != required_rule_fields:
            raise RuleSetUnavailable("RULE_SET_SCHEMA_INVALID", details={"field": f"rules.{index}"})
        rule_id = str(raw["rule_id"])
        if rule_id in rule_ids or not re.fullmatch(r"R3\.[A-Z0-9_.]+", rule_id):
            raise RuleSetUnavailable("RULE_SET_RULE_ID_INVALID", details={"rule_id": rule_id})
        rule_ids.add(rule_id)
        algorithm = str(raw["algorithm"])
        if algorithm not in ALLOWED_ALGORITHMS or FORBIDDEN_DYNAMIC_PATTERN.search(algorithm):
            raise RuleSetUnavailable("RULE_SET_ALGORITHM_NOT_ALLOWED", details={"rule_id": rule_id})
        if raw["confirmation_policy"] not in ALLOWED_CONFIRMATION_POLICIES:
            raise RuleSetUnavailable("RULE_SET_CONFIRMATION_POLICY_INVALID", details={"rule_id": rule_id})
        inputs = _strings(raw["input_paths"], f"{rule_id}.input_paths")
        outputs = _strings(raw["output_paths"], f"{rule_id}.output_paths")
        _validate_paths(inputs, ALLOWED_INPUT_PREFIXES, "input_paths")
        _validate_paths(outputs, ALLOWED_OUTPUT_PREFIXES, "output_paths")
        _strings(raw["source_contract_ids"], f"{rule_id}.source_contract_ids")
        _strings(raw["stale_dependencies"], f"{rule_id}.stale_dependencies")
        if FORBIDDEN_DYNAMIC_PATTERN.search(canonical_json(raw)):
            raise RuleSetUnavailable("RULE_SET_DYNAMIC_CONTENT_FORBIDDEN", details={"rule_id": rule_id})
        rules.append(dict(raw))
    _validate_dag(rules)

    vector_ids = [str(vector.get("vector_id", "")) for vector in raw_vectors if isinstance(vector, dict)]
    if len(vector_ids) != len(raw_vectors) or len(set(vector_ids)) != len(vector_ids) or not all(vector_ids):
        raise RuleSetUnavailable("RULE_SET_GOLDEN_VECTOR_INVALID")
    return DerivedRuleSet(
        rule_set_id=RULE_SET_ID,
        content_sha256=str(document["content_sha256"]),
        layers=tuple(layers),
        indicators=tuple(indicators),
        threat_catalog=tuple(threats),
        rules=tuple(rules),
        golden_vectors=tuple(dict(vector) for vector in raw_vectors),
    )


@lru_cache(maxsize=1)
def load_default_rule_set() -> DerivedRuleSet:
    return load_rule_set(resolve_resource_path(*RULE_MATRIX_PATH))


def clear_rule_set_cache() -> None:
    load_default_rule_set.cache_clear()
