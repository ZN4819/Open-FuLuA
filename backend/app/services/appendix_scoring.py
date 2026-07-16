"""Shared authoritative Appendix A score recalculation."""

from __future__ import annotations

import sqlite3
from typing import Any

from .scoring import (
    MANAGEMENT_COMPLIANCE_SCORES,
    TECHNICAL_SECTION_CODES,
    calculate_management_rows,
    calculate_technical_rows,
)


class AppendixScoringError(ValueError):
    pass


def recalculate_appendix_scores_locked(
    db: sqlite3.Connection,
    project_id: int,
    *,
    strict: bool = True,
) -> int:
    """Recalculate stored object/unit scores inside the caller transaction.

    The row set and identities must remain unchanged.  Ra/Rk are read as
    authoritative database inputs and are never sourced from Word.
    """

    updated = 0
    sections = db.execute(
        "SELECT id, code FROM appendix_sections WHERE project_id = ? ORDER BY sort_order",
        (project_id,),
    ).fetchall()
    for section in sections:
        rows = db.execute(
            """
            SELECT r.*, m.d, m.a, m.k, m.ra, m.rk,
                   m.object_score, m.unit_score, m.compliance
            FROM assessment_rows r
            LEFT JOIN metric_results m ON m.row_id = r.id
            WHERE r.section_id = ?
            ORDER BY r.sort_order, r.id
            """,
            (int(section["id"]),),
        ).fetchall()
        payload: list[dict[str, Any]] = [
            {
                "id": int(row["id"]),
                "unit": row["unit"],
                "object_name": row["object_name"],
                "subsystem": row["subsystem"],
                "record_text": row["record_text"],
                "sort_order": row["sort_order"],
                "metric_result": {
                    "d": row["d"],
                    "a": row["a"],
                    "k": row["k"],
                    "ra": row["ra"],
                    "rk": row["rk"],
                    "object_score": None,
                    "unit_score": None,
                    "compliance": row["compliance"],
                },
            }
            for row in rows
        ]
        try:
            if str(section["code"]) in TECHNICAL_SECTION_CODES:
                prepared = calculate_technical_rows(payload, strict=strict)
            else:
                prepared = calculate_management_rows(payload, strict=strict)
                for item in prepared:
                    metric = dict(item.get("metric_result") or {})
                    compliance = str(metric.get("compliance") or "").strip()
                    object_score = MANAGEMENT_COMPLIANCE_SCORES.get(compliance)
                    if object_score is None and strict:
                        raise ValueError("符合情况只能为符合、部分符合、不符合或不适用")
                    metric["object_score"] = object_score
                    item["metric_result"] = metric
        except ValueError as exc:
            raise AppendixScoringError(str(exc)) from exc
        if len(prepared) != len(rows):
            raise AppendixScoringError("APPENDIX_A_FIXED_OBJECT_STRUCTURE_CHANGED")
        prepared_by_id = {int(item["id"]): item for item in prepared}
        if set(prepared_by_id) != {int(row["id"]) for row in rows}:
            raise AppendixScoringError("APPENDIX_A_ROW_IDENTITY_CHANGED")
        for row in rows:
            row_id = int(row["id"])
            metric = prepared_by_id[row_id].get("metric_result") or {}
            cursor = db.execute(
                """
                UPDATE metric_results
                SET object_score = ?, unit_score = ?
                WHERE row_id = ?
                """,
                (metric.get("object_score"), metric.get("unit_score"), row_id),
            )
            if cursor.rowcount != 1:
                raise AppendixScoringError("APPENDIX_A_METRIC_RESULT_MISSING")
            updated += 1
    return updated
