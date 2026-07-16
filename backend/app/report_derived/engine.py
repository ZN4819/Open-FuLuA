from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable

from ..services.scoring import (
    MANAGEMENT_COMPLIANCE_SCORES,
    calculate_object_score,
    calculate_unit_score,
)
from .rules import DerivedRuleSet, IndicatorRule, load_default_rule_set, stable_hash


RESULTS = ("符合", "部分符合", "不符合", "不适用")
TECHNICAL_SECTIONS = frozenset({"A-1", "A-2", "A-3", "A-4"})
MANAGEMENT_SECTIONS = frozenset({"A-5", "A-6", "A-7", "A-8"})
CORRECTION_METRICS = {
    "confidentiality": ("通信过程中重要数据的机密性", "重要数据传输机密性"),
    "integrity": ("通信数据完整性", "重要数据传输完整性"),
}
SCORE_FOUR = Decimal("0.0001")
SCORE_TWO = Decimal("0.01")


class ProjectionInputError(RuntimeError):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("R3 projection inputs are incomplete or invalid")
        self.issues = issues


def _issue(
    code: str,
    message: str,
    *,
    section_code: str | None = None,
    indicator: str | None = None,
    object_uuid: str | None = None,
    source_row_id: int | None = None,
    field: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "section_code": section_code,
        "indicator": indicator,
        "object_uuid": object_uuid,
        "source_row_id": source_row_id,
        "field": field,
        "details": details or {},
    }


def _decimal_score(value: Any, *, allow_slash: bool = True) -> Decimal | None:
    text = "" if value is None else str(value).strip()
    if allow_slash and text == "/":
        return None
    if not text:
        raise ValueError("score is empty")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("score is invalid") from exc
    if not result.is_finite() or result < 0 or result > Decimal("1.2"):
        raise ValueError("score is outside the supported range")
    return result


def _format_four(value: Decimal) -> str:
    return format(value.quantize(SCORE_FOUR, rounding=ROUND_HALF_UP), "f")


def aggregate_indicator_result(results: Iterable[str]) -> str:
    normalized = [str(result or "").strip() for result in results]
    if not normalized or any(result not in RESULTS for result in normalized):
        return "incomplete"
    unique = set(normalized)
    if len(unique) == 1:
        return normalized[0]
    applicable = unique - {"不适用"}
    if not applicable:
        return "不适用"
    if applicable == {"符合"}:
        return "符合"
    if applicable == {"不符合"}:
        return "不符合"
    if "部分符合" in applicable:
        return "部分符合"
    if applicable == {"符合", "不符合"}:
        return "部分符合"
    return "incomplete"


def object_result_from_score(score: str) -> str:
    if score == "/":
        return "不适用"
    numeric = _decimal_score(score, allow_slash=False)
    if numeric == Decimal("1"):
        return "符合"
    if numeric == Decimal("0"):
        return "不符合"
    return "部分符合"


def calculate_a2_correction(a2_score: str, a4_scores: Iterable[str]) -> str:
    if a2_score == "/":
        return "/"
    original = _decimal_score(a2_score, allow_slash=False)
    valid = [_decimal_score(value, allow_slash=False) for value in a4_scores if str(value).strip() != "/"]
    if not valid:
        return _format_four(original)
    return _format_four(max(original, Decimal("0.5") * min(valid)))


def calculate_a4_correction(a4_score: str, a2_score: str) -> str:
    if a4_score == "/" or a2_score == "/":
        return a4_score
    original = _decimal_score(a4_score, allow_slash=False)
    source = _decimal_score(a2_score, allow_slash=False)
    return _format_four(max(original, Decimal("0.5") * source))


def _read_rows(db: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT
            s.code AS section_code,
            r.id AS source_row_id,
            r.assessment_object_uuid AS object_uuid,
            r.unit,
            r.object_name,
            r.subsystem,
            r.record_text,
            r.sort_order,
            m.d,
            m.a,
            m.k,
            m.ra,
            m.rk,
            m.object_score AS stored_object_score,
            m.unit_score AS stored_unit_score,
            m.compliance
        FROM appendix_sections s
        JOIN assessment_rows r ON r.section_id = s.id
        LEFT JOIN metric_results m ON m.row_id = r.id
        WHERE s.project_id = ?
        ORDER BY s.sort_order, r.sort_order, r.id
        """,
        (project_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def source_rows_snapshot(db: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    """Return the hash input, including Ra/Rk, for internal stale detection only."""

    rows = _read_rows(db, project_id)
    return [
        {
            key: row.get(key)
            for key in (
                "section_code", "source_row_id", "object_uuid", "unit", "object_name",
                "subsystem", "record_text", "sort_order", "d", "a", "k", "ra", "rk", "compliance",
            )
        }
        for row in rows
    ]


def _calculate_original_rows(
    db: sqlite3.Connection,
    project_id: int,
    rule_set: DerivedRuleSet,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_rows = _read_rows(db, project_id)
    issues: list[dict[str, Any]] = []
    indicator_lookup = rule_set.indicator_by_identity
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_row_ids: set[int] = set()
    for raw in raw_rows:
        section_code = str(raw.get("section_code") or "")
        unit = str(raw.get("unit") or "").strip()
        indicator = indicator_lookup.get((section_code, unit))
        row_id = int(raw["source_row_id"])
        if indicator is None:
            issues.append(
                _issue(
                    "INDICATOR_CATALOG_MISMATCH",
                    "附录 A 包含不属于固定 41 项目录的测评指标。",
                    section_code=section_code,
                    indicator=unit,
                    source_row_id=row_id,
                    field="unit",
                )
            )
            continue
        object_uuid = str(raw.get("object_uuid") or "").strip()
        if not object_uuid:
            issues.append(
                _issue(
                    "ASSESSMENT_OBJECT_BINDING_MISSING",
                    "测评记录尚未绑定中央测评对象。",
                    section_code=section_code,
                    indicator=unit,
                    source_row_id=row_id,
                    field="assessment_object_uuid",
                )
            )
            continue
        if row_id in seen_row_ids:
            issues.append(_issue("ASSESSMENT_ROW_DUPLICATE", "测评记录标识重复。", source_row_id=row_id))
            continue
        seen_row_ids.add(row_id)
        try:
            if section_code in TECHNICAL_SECTIONS:
                object_score = calculate_object_score(
                    raw.get("d"), raw.get("a"), raw.get("k"), raw.get("ra"), raw.get("rk"), strict=True
                )
                if object_score is None:
                    raise ValueError("D、A、K 未填写完整")
                object_result = object_result_from_score(object_score)
            elif section_code in MANAGEMENT_SECTIONS:
                compliance = str(raw.get("compliance") or "").strip()
                object_score = MANAGEMENT_COMPLIANCE_SCORES.get(compliance)
                if object_score is None:
                    raise ValueError("符合情况未填写或不合法")
                object_result = compliance
            else:
                raise ValueError("章节不属于 A-1 至 A-8")
        except ValueError as exc:
            issues.append(
                _issue(
                    "SCORING_INPUT_INVALID",
                    str(exc),
                    section_code=section_code,
                    indicator=unit,
                    object_uuid=object_uuid,
                    source_row_id=row_id,
                    field="metric_result",
                )
            )
            continue
        grouped[indicator.code].append(
            {
                "source_row_id": row_id,
                "object_uuid": object_uuid,
                "object_name": str(raw.get("object_name") or "").strip(),
                "subsystem": str(raw.get("subsystem") or "").strip(),
                "record_text": str(raw.get("record_text") or "").strip(),
                "section_code": section_code,
                "indicator_code": indicator.code,
                "indicator_name": indicator.name,
                "layer_code": indicator.layer_code,
                "d": raw.get("d"),
                "a": raw.get("a"),
                "k": raw.get("k"),
                "object_score": object_score,
                "object_result": object_result,
                "was_corrected": False,
            }
        )

    for indicator in rule_set.indicators:
        if not grouped[indicator.code]:
            issues.append(
                _issue(
                    "INDICATOR_DATA_MISSING",
                    "固定 41 项指标中存在未录入的测评指标。",
                    section_code=indicator.section_code,
                    indicator=indicator.name,
                    field="assessment_rows",
                    details={"indicator_code": indicator.code},
                )
            )
    if issues:
        raise ProjectionInputError(issues)

    output: list[dict[str, Any]] = []
    indicators: list[dict[str, Any]] = []
    for indicator in rule_set.indicators:
        rows = sorted(grouped[indicator.code], key=lambda row: (row["object_uuid"], row["source_row_id"]))
        unit_score = calculate_unit_score(row["object_score"] for row in rows)
        indicator_result = aggregate_indicator_result(row["object_result"] for row in rows)
        if not unit_score or indicator_result == "incomplete":
            raise ProjectionInputError(
                [_issue("INDICATOR_AGGREGATION_FAILED", "指标分值或结论无法确定。", section_code=indicator.section_code, indicator=indicator.name)]
            )
        for row in rows:
            row["unit_score"] = unit_score
            row["indicator_result"] = indicator_result
            output.append(row)
        indicators.append(
            {
                "indicator_code": indicator.code,
                "indicator_name": indicator.name,
                "section_code": indicator.section_code,
                "layer_code": indicator.layer_code,
                "unit_score": unit_score,
                "indicator_result": indicator_result,
                "object_count": len(rows),
                "source_row_ids": [row["source_row_id"] for row in rows],
            }
        )
    return output, indicators


def _read_correction_relations(db: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT correction_uuid, a2_object_uuid, a2_metric_code, a4_object_uuid,
               a4_metric_code, correction_kind, original_references_json
        FROM result_correction_relations
        WHERE project_id = ?
        ORDER BY correction_kind, a2_object_uuid, a4_object_uuid, correction_uuid
        """,
        (project_id,),
    ).fetchall()
    import json

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["original_references"] = json.loads(item.pop("original_references_json") or "{}")
        except json.JSONDecodeError:
            item["original_references"] = {}
        result.append(item)
    return result


def correction_relations_snapshot(db: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    return _read_correction_relations(db, project_id)


def _apply_corrections(
    original_rows: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_object_indicator: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in original_rows:
        by_object_indicator[(row["object_uuid"], row["indicator_name"])].append(row)
    issues: list[dict[str, Any]] = []
    validated: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    seen_a4: set[tuple[str, str]] = set()
    for relation in relations:
        kind = str(relation.get("correction_kind") or "")
        expected = CORRECTION_METRICS.get(kind)
        if expected is None or (
            relation.get("a2_metric_code"), relation.get("a4_metric_code")
        ) != expected:
            issues.append(
                _issue(
                    "CORRECTION_METRIC_PAIR_INVALID",
                    "结果修正关系只能连接两组已确认的同类指标。",
                    field="metric_code",
                    details={"correction_uuid": relation.get("correction_uuid")},
                )
            )
            continue
        a2_matches = by_object_indicator.get((str(relation["a2_object_uuid"]), expected[0]), [])
        a4_matches = by_object_indicator.get((str(relation["a4_object_uuid"]), expected[1]), [])
        if len(a2_matches) != 1 or len(a4_matches) != 1:
            issues.append(
                _issue(
                    "CORRECTION_SOURCE_ROW_NOT_UNIQUE",
                    "结果修正关系无法唯一定位双方原始测评记录。",
                    field="original_references",
                    details={
                        "correction_uuid": relation.get("correction_uuid"),
                        "a2_matches": [row["source_row_id"] for row in a2_matches],
                        "a4_matches": [row["source_row_id"] for row in a4_matches],
                    },
                )
            )
            continue
        uniqueness = (str(relation["a4_object_uuid"]), kind)
        if uniqueness in seen_a4:
            issues.append(
                _issue(
                    "CORRECTION_RELATION_CARDINALITY",
                    "同一 A-4 对象在同类指标上关联了多条 A-2 通道。",
                    object_uuid=str(relation["a4_object_uuid"]),
                )
            )
            continue
        seen_a4.add(uniqueness)
        references = relation.get("original_references") or {}
        if references.get("a2_row_id") not in (None, a2_matches[0]["source_row_id"]) or references.get("a4_row_id") not in (None, a4_matches[0]["source_row_id"]):
            issues.append(
                _issue(
                    "CORRECTION_ORIGINAL_REFERENCE_STALE",
                    "结果修正关系保存的原始记录引用已经失效。",
                    field="original_references",
                    details={"correction_uuid": relation.get("correction_uuid")},
                )
            )
            continue
        validated.append((relation, a2_matches[0], a4_matches[0]))
    if issues:
        raise ProjectionInputError(issues)

    final_scores = {row["source_row_id"]: row["object_score"] for row in original_rows}
    relations_by_a2: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for relation, a2_row, a4_row in validated:
        relations_by_a2[a2_row["source_row_id"]].append((relation, a4_row))
    correction_rows: list[dict[str, Any]] = []
    for a2_row_id, related in sorted(relations_by_a2.items()):
        a2_row = next(row for row in original_rows if row["source_row_id"] == a2_row_id)
        final_score = calculate_a2_correction(a2_row["object_score"], [row["object_score"] for _, row in related])
        final_scores[a2_row_id] = final_score
        if final_score != a2_row["object_score"]:
            correction_rows.append(
                {
                    "direction": "A-4_to_A-2",
                    "source_row_id": a2_row_id,
                    "object_uuid": a2_row["object_uuid"],
                    "object_name": a2_row["object_name"],
                    "indicator_code": a2_row["indicator_code"],
                    "indicator_name": a2_row["indicator_name"],
                    "original_score": a2_row["object_score"],
                    "final_score": final_score,
                    "related_source_row_ids": sorted(row["source_row_id"] for _, row in related),
                    "correction_uuids": sorted(str(relation["correction_uuid"]) for relation, _ in related),
                    "was_corrected": True,
                }
            )
    for relation, a2_row, a4_row in validated:
        final_score = calculate_a4_correction(a4_row["object_score"], a2_row["object_score"])
        final_scores[a4_row["source_row_id"]] = final_score
        if final_score != a4_row["object_score"]:
            correction_rows.append(
                {
                    "direction": "A-2_to_A-4",
                    "source_row_id": a4_row["source_row_id"],
                    "object_uuid": a4_row["object_uuid"],
                    "object_name": a4_row["object_name"],
                    "indicator_code": a4_row["indicator_code"],
                    "indicator_name": a4_row["indicator_name"],
                    "original_score": a4_row["object_score"],
                    "final_score": final_score,
                    "related_source_row_ids": [a2_row["source_row_id"]],
                    "correction_uuids": [str(relation["correction_uuid"])],
                    "was_corrected": True,
                }
            )

    final_rows = deepcopy(original_rows)
    correction_by_row = {row["source_row_id"]: row for row in correction_rows}
    for row in final_rows:
        original_score = row["object_score"]
        row["object_score"] = final_scores[row["source_row_id"]]
        row["object_result"] = object_result_from_score(row["object_score"])
        row["was_corrected"] = row["source_row_id"] in correction_by_row
        row["original_object_score"] = original_score
        row["final_object_score"] = row["object_score"]
        if row["was_corrected"]:
            row["correction_source"] = {
                key: correction_by_row[row["source_row_id"]][key]
                for key in ("direction", "related_source_row_ids", "correction_uuids")
            }
    correction_rows.sort(key=lambda item: (item["indicator_code"], item["object_uuid"], item["source_row_id"]))
    return final_rows, correction_rows


def _aggregate_projection_rows(
    rows: list[dict[str, Any]],
    rule_set: DerivedRuleSet,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["indicator_code"]].append(row)
    output_rows: list[dict[str, Any]] = []
    indicators: list[dict[str, Any]] = []
    for indicator in rule_set.indicators:
        indicator_rows = sorted(grouped[indicator.code], key=lambda row: (row["object_uuid"], row["source_row_id"]))
        unit_score = calculate_unit_score(row["object_score"] for row in indicator_rows)
        indicator_result = aggregate_indicator_result(row["object_result"] for row in indicator_rows)
        if not unit_score or indicator_result == "incomplete":
            raise ProjectionInputError(
                [_issue("INDICATOR_AGGREGATION_FAILED", "修正后的指标分值或结论无法确定。", section_code=indicator.section_code, indicator=indicator.name)]
            )
        for row in indicator_rows:
            row["unit_score"] = unit_score
            row["indicator_result"] = indicator_result
            output_rows.append(row)
        indicators.append(
            {
                "indicator_code": indicator.code,
                "indicator_name": indicator.name,
                "section_code": indicator.section_code,
                "layer_code": indicator.layer_code,
                "unit_score": unit_score,
                "indicator_result": indicator_result,
                "object_count": len(indicator_rows),
                "source_row_ids": [row["source_row_id"] for row in indicator_rows],
                "corrected_object_count": sum(bool(row["was_corrected"]) for row in indicator_rows),
            }
        )
    return output_rows, indicators


def calculate_statistics(indicators: list[dict[str, Any]], rule_set: DerivedRuleSet) -> dict[str, Any]:
    by_code = {item["indicator_code"]: item for item in indicators}
    layers: list[dict[str, Any]] = []
    total = Counter({result: 0 for result in RESULTS})
    for layer in rule_set.layers:
        layer_rules = [item for item in rule_set.indicators if item.layer_code == layer.code]
        counts = Counter(by_code[item.code]["indicator_result"] for item in layer_rules)
        if sum(counts.values()) != len(layer_rules):
            raise ProjectionInputError([_issue("STATISTICS_INVARIANT_FAILED", "安全层面指标统计不守恒。", section_code=layer.section_code)])
        total.update(counts)
        layers.append(
            {
                "layer_code": layer.code,
                "section_code": layer.section_code,
                "layer_name": layer.name,
                "indicator_total": len(layer_rules),
                "compliant": counts["符合"],
                "partially_compliant": counts["部分符合"],
                "noncompliant": counts["不符合"],
                "not_applicable": counts["不适用"],
            }
        )
    if sum(total.values()) != 41:
        raise ProjectionInputError([_issue("STATISTICS_INVARIANT_FAILED", "全报告固定指标统计之和不等于 41。")])
    return {
        "layers": layers,
        "total": {
            "indicator_total": 41,
            "compliant": total["符合"],
            "partially_compliant": total["部分符合"],
            "noncompliant": total["不符合"],
            "not_applicable": total["不适用"],
        },
    }


def calculate_overall_score(indicators: list[dict[str, Any]], rule_set: DerivedRuleSet) -> dict[str, Any]:
    indicator_values = {item["indicator_code"]: item["unit_score"] for item in indicators}
    layer_scores: dict[str, Decimal | None] = {}
    layer_details: list[dict[str, Any]] = []
    for layer in rule_set.layers:
        rules = [item for item in rule_set.indicators if item.layer_code == layer.code]
        applicable: list[tuple[Decimal, Decimal]] = []
        for indicator in rules:
            value = indicator_values[indicator.code]
            if value == "/":
                continue
            numeric = _decimal_score(value, allow_slash=False)
            applicable.append((numeric, indicator.weight))
        if not applicable:
            layer_score = None
        else:
            layer_score = sum(score * weight for score, weight in applicable) / sum(weight for _, weight in applicable)
        layer_scores[layer.code] = layer_score
        layer_details.append(
            {
                "layer_code": layer.code,
                "layer_name": layer.name,
                "score": None if layer_score is None else _format_four(layer_score),
                "applicable_indicator_count": len(applicable),
            }
        )
    total = Decimal("0")
    for category in ("technical", "management"):
        active_layers = [layer for layer in rule_set.layers if layer.category == category and layer_scores[layer.code] is not None]
        if not active_layers:
            continue
        denominator = sum(layer.layer_weight for layer in active_layers)
        share = active_layers[0].group_share
        total += sum(
            layer_scores[layer.code] * layer.layer_weight * share / denominator
            for layer in active_layers
            if layer_scores[layer.code] is not None
        )
    display = total.quantize(SCORE_TWO, rounding=ROUND_HALF_UP)
    return {
        "raw_score": _format_four(total),
        "display_score": format(display, ".2f"),
        "rounding": "ROUND_HALF_UP",
        "layers": layer_details,
    }


def build_projection(
    db: sqlite3.Connection,
    project_id: int,
    *,
    rule_set: DerivedRuleSet | None = None,
) -> dict[str, Any]:
    rules = rule_set or load_default_rule_set()
    original_rows, original_indicators = _calculate_original_rows(db, project_id, rules)
    relations = _read_correction_relations(db, project_id)
    final_rows, corrections = _apply_corrections(original_rows, relations)
    final_rows, final_indicators = _aggregate_projection_rows(final_rows, rules)
    statistics = calculate_statistics(final_indicators, rules)
    score = calculate_overall_score(final_indicators, rules)
    original_projection = {
        "rows": original_rows,
        "indicators": original_indicators,
    }
    correction_projection = {
        "rows": corrections,
        "render_empty_as_slash_row": not bool(corrections),
    }
    final_projection = {
        "rows": final_rows,
        "indicators": final_indicators,
        "statistics": statistics,
        "score": score,
    }
    result = {
        "original_projection": original_projection,
        "correction_projection": correction_projection,
        "final_projection": final_projection,
        "projection_hash": stable_hash(
            {
                "original": original_projection,
                "correction": correction_projection,
                "final": final_projection,
            }
        ),
    }
    def contains_private_factor(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                str(key).casefold() in {"ra", "rk"} or contains_private_factor(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(contains_private_factor(item) for item in value)
        return False

    if contains_private_factor(result):
        raise AssertionError("Ra/Rk leaked into R3 projection")
    return result


def validate_golden_vectors(rule_set: DerivedRuleSet | None = None) -> list[str]:
    rules = rule_set or load_default_rule_set()
    failures: list[str] = []
    for vector in rules.golden_vectors:
        kind = vector["kind"]
        if kind == "indicator_aggregation":
            actual = aggregate_indicator_result(vector["input"])
        elif kind == "a2_correction":
            actual = calculate_a2_correction(vector["input"]["a2"], vector["input"]["a4"])
        elif kind == "a4_correction":
            actual = calculate_a4_correction(vector["input"]["a4"], vector["input"]["a2"])
        elif kind == "overall_score":
            indicators = [
                {
                    "indicator_code": indicator.code,
                    "unit_score": "1.0000",
                }
                for indicator in rules.indicators
            ]
            actual = calculate_overall_score(indicators, rules)["display_score"]
        else:
            failures.append(str(vector["vector_id"]))
            continue
        if actual != vector["expected"]:
            failures.append(str(vector["vector_id"]))
    return failures
