from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


TECHNICAL_SECTION_CODES = frozenset({"A-1", "A-2", "A-3", "A-4"})
MANAGEMENT_SECTION_CODES = frozenset({"A-5", "A-6", "A-7", "A-8"})
TECHNICAL_METRIC_VALUES = frozenset({"√", "×", "/"})
MANAGEMENT_COMPLIANCE_SCORES = {
    "符合": "1.0000",
    "部分符合": "0.5000",
    "不符合": "0.0000",
    "不适用": "/",
}
RA_VALUES = frozenset({"1", "0.5", "0.2"})
RK_VALUES = frozenset({"1", "1.2"})
DEFAULT_RA = "1"
DEFAULT_RK = "1"
SCORE_QUANTUM = Decimal("0.0001")


def calculate_object_score(
    d: Any,
    a: Any,
    k: Any,
    ra: Any = DEFAULT_RA,
    rk: Any = DEFAULT_RK,
    *,
    strict: bool = True,
) -> str | None:
    metrics = tuple(_text(value) for value in (d, a, k))
    if not all(metrics):
        return None
    if any(value not in TECHNICAL_METRIC_VALUES for value in metrics):
        if strict:
            raise ValueError("D、A、K 只能为 √、× 或 /")
        return None

    normalized_ra = _factor(ra, RA_VALUES, DEFAULT_RA, "Ra", strict)
    normalized_rk = _factor(rk, RK_VALUES, DEFAULT_RK, "Rk", strict)
    if normalized_ra is None or normalized_rk is None:
        return None

    if metrics == ("/", "/", "/"):
        return "/"
    if metrics[0] != "√":
        return "0.0000"

    score = Decimal("1")
    if metrics[1] != "√":
        score *= Decimal("0.5") * Decimal(normalized_ra)
    if metrics[2] != "√":
        score *= Decimal("0.5") * Decimal(normalized_rk)
    return _format_decimal(score)


def calculate_unit_score(scores: Iterable[Any]) -> str:
    normalized = [_score_text(score) for score in scores]
    if not normalized or any(score == "" for score in normalized):
        return ""
    numeric_scores: list[Decimal] = []
    for score in normalized:
        if score == "/":
            continue
        try:
            numeric_scores.append(Decimal(score))
        except InvalidOperation:
            return ""
    if not numeric_scores:
        return "/"
    return _format_decimal(sum(numeric_scores) / Decimal(len(numeric_scores)))


def calculate_technical_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        metric = dict(row.get("metric_result") or {})
        metric["ra"] = _default_factor(metric.get("ra"), DEFAULT_RA)
        metric["rk"] = _default_factor(metric.get("rk"), DEFAULT_RK)
        metric["object_score"] = calculate_object_score(
            metric.get("d"),
            metric.get("a"),
            metric.get("k"),
            metric["ra"],
            metric["rk"],
            strict=strict,
        )
        row["metric_result"] = metric
        output.append(row)
    return _with_unit_scores(output, nested=True)


def calculate_flat_technical_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        row["ra"] = _default_factor(row.get("ra"), DEFAULT_RA)
        row["rk"] = _default_factor(row.get("rk"), DEFAULT_RK)
        row["object_score"] = calculate_object_score(
            row.get("d"),
            row.get("a"),
            row.get("k"),
            row["ra"],
            row["rk"],
            strict=strict,
        )
        output.append(row)
    return _with_unit_scores(output, nested=False)


def calculate_management_unit_score(
    compliances: Iterable[Any],
    *,
    strict: bool = True,
) -> str:
    scores: list[str] = []
    for compliance in compliances:
        normalized = _text(compliance)
        if not normalized:
            return ""
        score = MANAGEMENT_COMPLIANCE_SCORES.get(normalized)
        if score is None:
            if strict:
                raise ValueError("符合情况只能为符合、部分符合、不符合或不适用")
            return ""
        scores.append(score)
    return calculate_unit_score(scores)


def calculate_management_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    return _with_management_unit_scores([dict(row) for row in rows], nested=True, strict=strict)


def calculate_flat_management_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    return _with_management_unit_scores([dict(row) for row in rows], nested=False, strict=strict)


def _with_unit_scores(rows: list[dict[str, Any]], *, nested: bool) -> list[dict[str, Any]]:
    scores_by_unit: dict[str, list[Any]] = {}
    for row in rows:
        metric = row.get("metric_result") or {} if nested else row
        scores_by_unit.setdefault(_text(row.get("unit")), []).append(metric.get("object_score"))
    unit_scores = {unit: calculate_unit_score(scores) for unit, scores in scores_by_unit.items()}

    output: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        if nested:
            metric = dict(row.get("metric_result") or {})
            metric["unit_score"] = unit_scores.get(_text(row.get("unit")), "")
            row["metric_result"] = metric
        else:
            row["unit_score"] = unit_scores.get(_text(row.get("unit")), "")
        output.append(row)
    return output


def _with_management_unit_scores(
    rows: list[dict[str, Any]],
    *,
    nested: bool,
    strict: bool,
) -> list[dict[str, Any]]:
    compliances_by_unit: dict[str, list[Any]] = {}
    for row in rows:
        metric = (row.get("metric_result") or {}) if nested else row
        compliances_by_unit.setdefault(_text(row.get("unit")), []).append(metric.get("compliance"))
    unit_scores = {
        unit: calculate_management_unit_score(compliances, strict=strict)
        for unit, compliances in compliances_by_unit.items()
    }

    output: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        unit_score = unit_scores.get(_text(row.get("unit")), "")
        if nested:
            metric = dict(row.get("metric_result") or {})
            metric["unit_score"] = unit_score
            row["metric_result"] = metric
        else:
            row["unit_score"] = unit_score
        output.append(row)
    return output


def _factor(value: Any, allowed: frozenset[str], default: str, name: str, strict: bool) -> str | None:
    text = _default_factor(value, default)
    if text in allowed:
        return text
    if strict:
        raise ValueError(f"{name} 的值 {text!r} 不合法，允许值为：{', '.join(sorted(allowed))}")
    return None


def _default_factor(value: Any, default: str) -> str:
    return _text(value) or default


def _score_text(value: Any) -> str:
    text = _text(value)
    if not text or text == "/":
        return text
    try:
        score = Decimal(text)
    except InvalidOperation:
        return text
    if not score.is_finite():
        return text
    return _format_decimal(score)


def _format_decimal(value: Decimal) -> str:
    return str(value.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP))


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()
