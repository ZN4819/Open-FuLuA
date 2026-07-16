from __future__ import annotations

import json
import re
import sqlite3
import uuid
from copy import deepcopy
from typing import Any

from .. import database
from ..report_derived.engine import (
    ProjectionInputError,
    build_projection,
    correction_relations_snapshot,
    source_rows_snapshot,
    validate_golden_vectors,
)
from ..report_derived.context_contract import (
    CONTEXT_SCHEMA_VERSION,
    ContextContractViolation,
    validate_context_envelope,
    validate_context_payload,
)
from ..report_derived.narratives import (
    PLACEHOLDER_PATTERN,
    assessment_conclusion,
    build_finding_baselines,
    generate_narrative_blocks,
    read_report_facts,
    risk_snapshot_from_rows,
)
from ..report_derived.rules import (
    DerivedRuleSet,
    RuleSetUnavailable,
    canonical_json,
    load_default_rule_set,
    stable_hash,
)
from ..report_derived.schema import DERIVED_UUID_NAMESPACE, initialize_report_derived_state
from ..report_schemas import (
    ConsistencyCheckWrite,
    DerivedBlockConfirmationWrite,
    DerivedBlockOverrideWrite,
    GenerationRunWrite,
    RiskUpdateWrite,
)
from .report_domain.common import require_report_project
from .report_domain.errors import ReportDomainError


DERIVED_BLOCK_KEYS = (
    "conclusion.system_summary",
    "conclusion.assessment_summary",
    "overall_evaluation.intro",
    *(f"overall_evaluation.layer.{index}" for index in range(1, 9)),
    "overall_evaluation.outro",
    "security_issues.intro",
    *(f"security_issues.layer.{index}" for index in range(1, 9)),
    "recommendations.intro",
    *(f"recommendations.layer.{index}" for index in range(1, 9)),
    "risk_analysis.summary",
    "risk_analysis.rows",
    "assessment_conclusion",
)
BLOCK_OVERRIDE_FIELDS = {
    "conclusion.system_summary": frozenset({"text"}),
    **{f"overall_evaluation.layer.{index}": frozenset({"situation_description"}) for index in range(1, 9)},
    **{f"recommendations.layer.{index}": frozenset({"items"}) for index in range(1, 9)},
}
FORBIDDEN_OVERRIDE_PATTERN = re.compile(r"<\s*(?:script|!doctype|\?xml)|javascript:|data:[^,]+;base64,", re.IGNORECASE)
RISK_LABELS = {"high": "高", "medium": "中", "low": "低"}
R3_CONTEXT_SCHEMA_VERSION = CONTEXT_SCHEMA_VERSION


def _json(value: Any) -> str:
    return canonical_json(value)


def _loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _rules(project_uuid: str) -> DerivedRuleSet:
    try:
        result = load_default_rule_set()
        failed_vectors = validate_golden_vectors(result)
    except RuleSetUnavailable as exc:
        safe_details = {
            key: value
            for key, value in exc.details.items()
            if key in {"field", "rule_id"}
        }
        raise ReportDomainError(
            "RULE_SET_UNAVAILABLE",
            "派生规则集不可用，已停止生成。",
            status_code=503,
            project_uuid=project_uuid,
            details={"reason": exc.reason, **safe_details},
        ) from exc
    if failed_vectors:
        raise ReportDomainError(
            "RULE_SET_UNAVAILABLE",
            "派生规则集黄金向量校验失败，已停止生成。",
            status_code=503,
            project_uuid=project_uuid,
            details={"reason": "GOLDEN_VECTOR_FAILED", "vectors": failed_vectors},
        )
    return result


def _state(db: sqlite3.Connection, project_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM report_generation_state WHERE project_id = ?", (project_id,)).fetchone()
    if row is None:
        initialize_report_derived_state(db, project_id)
        row = db.execute("SELECT * FROM report_generation_state WHERE project_id = ?", (project_id,)).fetchone()
    if row is None:
        raise RuntimeError("REPORT_GENERATION_STATE_INITIALIZATION_FAILED")
    return row


def _expect_revision(state: sqlite3.Row, expected: int, project_uuid: str) -> None:
    current = int(state["project_revision"])
    if current != expected:
        raise ReportDomainError(
            "PROJECT_REVISION_CONFLICT",
            "派生内容已在其他页面更新，请刷新后重试。",
            status_code=409,
            project_uuid=project_uuid,
            field="expected_project_revision",
            details={"expected_revision": expected, "current_revision": current},
        )


def _advance_state(
    db: sqlite3.Connection,
    state: sqlite3.Row,
    project_uuid: str,
    updates: dict[str, Any] | None = None,
) -> sqlite3.Row:
    allowed = {
        "current_run_uuid", "current_input_hash", "current_context_json",
        "current_context_hash", "source_groups_json", "feature_enabled",
    }
    updates = updates or {}
    if set(updates) - allowed:
        raise ValueError("unsupported report generation state update")
    assignments = [f"{field} = ?" for field in updates]
    assignments.extend(["project_revision = project_revision + 1", "updated_at = ?"])
    values = list(updates.values()) + [database.utc_now(), state["id"], state["project_revision"]]
    cursor = db.execute(
        f"UPDATE report_generation_state SET {', '.join(assignments)} WHERE id = ? AND project_revision = ?",
        values,
    )
    if cursor.rowcount != 1:
        current = db.execute(
            "SELECT project_revision FROM report_generation_state WHERE id = ?", (state["id"],)
        ).fetchone()
        raise ReportDomainError(
            "PROJECT_REVISION_CONFLICT",
            "派生内容已在其他页面更新，请刷新后重试。",
            status_code=409,
            project_uuid=project_uuid,
            field="expected_project_revision",
            details={
                "expected_revision": int(state["project_revision"]),
                "current_revision": int(current["project_revision"]) if current else None,
            },
        )
    return db.execute("SELECT * FROM report_generation_state WHERE id = ?", (state["id"],)).fetchone()


def advance_project_revision_for_external_change(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
) -> int:
    """Advance the report revision after a non-R3 report-domain mutation.

    The caller holds the write transaction and has already checked the request's
    expected project revision. Clearing the current context hash makes the prior
    R3 context unavailable until the normal generation workflow runs again while
    retaining the historical run for audit.
    """

    state = _state(db, project_id)
    updated = _advance_state(
        db,
        state,
        project_uuid,
        {"current_context_hash": None},
    )
    return int(updated["project_revision"])


def _special_snapshot(db: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    standards = [
        dict(row)
        for row in db.execute(
            """
            SELECT standard_uuid, standard_code, standard_name, source_reference, sort_order, revision
            FROM report_standards WHERE project_id = ? AND standard_kind = 'manual'
            ORDER BY sort_order, standard_uuid
            """,
            (project_id,),
        ).fetchall()
    ]
    indicators = [
        dict(row)
        for row in db.execute(
            """
            SELECT indicator_uuid, manual_standard_uuid, indicator_code, indicator_name,
                   description, sort_order, revision
            FROM special_indicators WHERE project_id = ?
            ORDER BY sort_order, indicator_uuid
            """,
            (project_id,),
        ).fetchall()
    ]
    return {"standards": standards, "indicators": indicators}


def _load_risk_rows(db: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT
            r.risk_uuid, r.finding_uuid, r.risk_level, r.analysis_baseline_json,
            r.analysis_override_json, r.override_reason, r.confirmation_status,
            r.source_hash, r.revision,
            f.indicator_code, f.layer_code, f.final_indicator_result
        FROM report_risks r
        JOIN report_findings f
          ON f.finding_uuid = r.finding_uuid AND f.project_id = r.project_id
        WHERE r.project_id = ? AND f.active = 1
        ORDER BY f.indicator_code, r.risk_uuid
        """,
        (project_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        baseline = _loads(item.pop("analysis_baseline_json"), {})
        override = _loads(item.pop("analysis_override_json"), None)
        threat_ids = [
            str(threat["threat_catalog_id"])
            for threat in db.execute(
                """
                SELECT threat_catalog_id FROM report_risk_threat_relations
                WHERE project_id = ? AND risk_uuid = ? ORDER BY sort_order, threat_catalog_id
                """,
                (project_id, item["risk_uuid"]),
            ).fetchall()
        ]
        item.update(
            {
                "indicator_name": baseline.get("indicator_name", ""),
                "problem_description": baseline.get("problem_description", ""),
                "problem_items": baseline.get("problem_items", []),
                "analysis_baseline": baseline,
                "analysis_override": override,
                "threat_ids": threat_ids,
            }
        )
        result.append(item)
    return result


def _source_group_hashes(
    db: sqlite3.Connection,
    project_id: int,
    facts: dict[str, Any] | None = None,
) -> dict[str, str]:
    if facts is None:
        facts, _ = read_report_facts(db, project_id)
    relations = correction_relations_snapshot(db, project_id)
    raw_rows = source_rows_snapshot(db, project_id)
    risk_rows = _load_risk_rows(db, project_id)
    public_facts = {
        key: value
        for key, value in facts.items()
        if key not in {"system_summary", "special_indicator_count"}
    }
    risk_source = [
        {
            key: row.get(key)
            for key in (
                "risk_uuid", "finding_uuid", "risk_level", "analysis_override", "override_reason",
                "confirmation_status", "source_hash", "revision", "threat_ids",
            )
        }
        for row in risk_rows
    ]
    return {
        "system_summary": stable_hash({"system_summary": facts.get("system_summary", "")}),
        "report_facts": stable_hash(public_facts),
        "appendix_a": stable_hash(raw_rows),
        "correction_relations": stable_hash(relations),
        "risks": stable_hash(risk_source),
        "special_indicators": stable_hash(_special_snapshot(db, project_id)),
    }


def _upsert_findings(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    baselines: list[dict[str, Any]],
) -> None:
    timestamp = database.utc_now()
    active_codes: set[str] = set()
    for baseline in baselines:
        indicator_code = baseline["indicator_code"]
        active_codes.add(indicator_code)
        current = db.execute(
            "SELECT * FROM report_findings WHERE project_id = ? AND indicator_code = ? AND active = 1",
            (project_id, indicator_code),
        ).fetchone()
        if current is not None and (
            current["source_hash"] != baseline["source_hash"]
            or current["final_indicator_result"] != baseline["final_indicator_result"]
        ):
            db.execute(
                "UPDATE report_findings SET active = 0, updated_at = ? WHERE id = ?",
                (timestamp, current["id"]),
            )
            current = None
        if current is None:
            finding_uuid = str(
                uuid.uuid5(
                    DERIVED_UUID_NAMESPACE,
                    f"{project_uuid}:finding:{indicator_code}:{baseline['source_hash']}",
                )
            )
            historical = db.execute(
                "SELECT id FROM report_findings WHERE project_id = ? AND finding_uuid = ?",
                (project_id, finding_uuid),
            ).fetchone()
            if historical is None:
                db.execute(
                    """
                    INSERT INTO report_findings (
                        finding_uuid, project_id, indicator_code, layer_code,
                        final_indicator_result, source_hash, active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        finding_uuid, project_id, indicator_code, baseline["layer_code"],
                        baseline["final_indicator_result"], baseline["source_hash"], timestamp, timestamp,
                    ),
                )
            else:
                db.execute(
                    """
                    UPDATE report_findings
                    SET indicator_code = ?, layer_code = ?, final_indicator_result = ?,
                        source_hash = ?, active = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        indicator_code, baseline["layer_code"], baseline["final_indicator_result"],
                        baseline["source_hash"], timestamp, historical["id"],
                    ),
                )
            current = db.execute(
                "SELECT * FROM report_findings WHERE finding_uuid = ?", (finding_uuid,)
            ).fetchone()
        analysis_baseline = {
            "indicator_name": baseline["indicator_name"],
            "problem_description": baseline["problem_description"],
            "problem_items": baseline["problem_items"],
        }
        risk_uuid = str(uuid.uuid5(DERIVED_UUID_NAMESPACE, f"{project_uuid}:risk:{current['finding_uuid']}"))
        db.execute(
            """
            INSERT INTO report_risks (
                risk_uuid, project_id, finding_uuid, analysis_baseline_json,
                source_hash, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(finding_uuid) DO UPDATE SET
                analysis_baseline_json = excluded.analysis_baseline_json,
                source_hash = excluded.source_hash,
                updated_at = CASE
                    WHEN report_risks.analysis_baseline_json <> excluded.analysis_baseline_json
                      OR report_risks.source_hash <> excluded.source_hash
                    THEN excluded.updated_at ELSE report_risks.updated_at END
            """,
            (
                risk_uuid, project_id, current["finding_uuid"], _json(analysis_baseline),
                baseline["source_hash"], timestamp, timestamp,
            ),
        )
    if active_codes:
        placeholders = ",".join("?" for _ in active_codes)
        db.execute(
            f"UPDATE report_findings SET active = 0, updated_at = ? WHERE project_id = ? AND active = 1 AND indicator_code NOT IN ({placeholders})",
            (timestamp, project_id, *sorted(active_codes)),
        )
    else:
        db.execute(
            "UPDATE report_findings SET active = 0, updated_at = ? WHERE project_id = ? AND active = 1",
            (timestamp, project_id),
        )


def _stale_current_blocks(
    db: sqlite3.Connection,
    project_id: int,
    *,
    dependency: str | None = None,
) -> list[str]:
    rows = db.execute(
        """
        SELECT br.id, br.block_uuid, br.source_snapshot_json, b.block_key
        FROM report_block_revisions br
        JOIN report_blocks b ON b.block_uuid = br.block_uuid AND b.project_id = br.project_id
        WHERE br.project_id = ? AND br.is_current = 1
        """,
        (project_id,),
    ).fetchall()
    affected: list[str] = []
    for row in rows:
        dependencies = (_loads(row["source_snapshot_json"], {}).get("dependencies") or {})
        if dependency is not None and dependency not in dependencies:
            continue
        db.execute(
            "UPDATE report_block_revisions SET generation_status = 'stale' WHERE id = ?",
            (row["id"],),
        )
        db.execute(
            "UPDATE report_blocks SET generation_status = 'stale', revision = revision + 1, updated_at = ? WHERE project_id = ? AND block_uuid = ?",
            (database.utc_now(), project_id, row["block_uuid"]),
        )
        affected.append(str(row["block_key"]))
    return affected


def _effective_payload(block_key: str, baseline: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(baseline)
    if not override:
        return result
    if block_key == "conclusion.system_summary":
        result["text"] = override["text"]
    elif block_key.startswith("overall_evaluation.layer."):
        result["situation_description"] = override["situation_description"]
        number = int(block_key.rsplit(".", 1)[1])
        stats = result["statistics"]
        result["text"] = (
            f"{number}. 在{result['layer_name']}方面，{override['situation_description']}。"
            f"测评结果：符合项{stats['compliant']}项，部分符合项{stats['partially_compliant']}项，"
            f"不符合项{stats['noncompliant']}项，不适用项{stats['not_applicable']}项。"
        )
    elif block_key.startswith("recommendations.layer."):
        replacements = override["items"]
        for item in result["items"]:
            if item["indicator_code"] in replacements:
                item["text"] = replacements[item["indicator_code"]]
    return result


def _persist_blocks(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    block_specs: list[dict[str, Any]],
    source_groups: dict[str, str],
    rule_set: DerivedRuleSet,
    project_revision: int,
) -> list[dict[str, Any]]:
    timestamp = database.utc_now()
    output: list[dict[str, Any]] = []
    for sort_order, spec in enumerate(block_specs, start=1000):
        section = db.execute(
            "SELECT id FROM report_sections WHERE project_id = ? AND section_key = ?",
            (project_id, spec["section_key"]),
        ).fetchone()
        if section is None:
            raise ReportDomainError(
                "DERIVED_BLOCK_SECTION_MISSING",
                "派生正文目标章节不存在。",
                project_uuid=project_uuid,
                details={"section_key": spec["section_key"]},
            )
        block_uuid = str(uuid.uuid5(DERIVED_UUID_NAMESPACE, f"{project_uuid}:block:{spec['block_key']}"))
        stored = db.execute(
            "SELECT * FROM report_blocks WHERE project_id = ? AND block_key = ?",
            (project_id, spec["block_key"]),
        ).fetchone()
        if stored is not None and stored["source_kind"] != "derived":
            raise ReportDomainError(
                "DERIVED_BLOCK_KEY_CONFLICT",
                "派生正文块与现有人工或母版块冲突。",
                project_uuid=project_uuid,
                entity_uuid=stored["block_uuid"],
                details={"block_key": spec["block_key"]},
            )
        previous = db.execute(
            "SELECT * FROM report_block_revisions WHERE project_id = ? AND block_uuid = ? AND is_current = 1",
            (project_id, block_uuid),
        ).fetchone()
        baseline = spec["baseline"]
        baseline_hash = stable_hash(baseline)
        dependency_hashes = {name: source_groups[name] for name in spec["dependencies"]}
        source_snapshot = {"dependencies": dependency_hashes}
        source_hash = stable_hash(source_snapshot)
        override = _loads(previous["override_json"], None) if previous is not None else None
        override_reason = str(previous["override_reason"] or "") if previous is not None else ""
        hidden = baseline.get("visible") is False
        if spec["edit_policy"] == "readonly" or hidden:
            confirmation_status = "confirmed"
        elif previous is not None and previous["baseline_hash"] == baseline_hash and previous["source_hash"] == source_hash:
            confirmation_status = str(previous["confirmation_status"])
        elif override:
            confirmation_status = "review_required"
        else:
            confirmation_status = "unconfirmed"
        confirmed_at = (
            previous["confirmed_at"]
            if previous is not None and confirmation_status == "confirmed"
            else timestamp if confirmation_status == "confirmed" else None
        )
        confirmed_by = (
            previous["confirmed_by"]
            if previous is not None and confirmation_status == "confirmed"
            else "system" if confirmation_status == "confirmed" else None
        )
        effective = _effective_payload(spec["block_key"], baseline, override)
        if stored is None:
            db.execute(
                """
                INSERT INTO report_blocks (
                    block_uuid, project_id, section_id, block_key, block_type,
                    payload_json, source_kind, edit_policy, baseline_kind,
                    baseline_json, baseline_hash, override_json, source_hash,
                    generation_status, confirmation_status, sort_order,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'generated', ?, 'derived', ?, NULL, ?, ?, ?, ?,
                          'current', ?, ?, ?, ?)
                """,
                (
                    block_uuid, project_id, section["id"], spec["block_key"], _json(effective),
                    spec["edit_policy"], _json(baseline), baseline_hash,
                    _json(override) if override else None, source_hash,
                    "confirmed" if confirmation_status == "confirmed" else "unconfirmed",
                    sort_order, timestamp, timestamp,
                ),
            )
        else:
            db.execute(
                """
                UPDATE report_blocks
                SET section_id = ?, payload_json = ?, edit_policy = ?, baseline_kind = NULL,
                    baseline_json = ?, baseline_hash = ?, override_json = ?, source_hash = ?,
                    generation_status = 'current', confirmation_status = ?, sort_order = ?,
                    revision = revision + 1, updated_at = ?
                WHERE project_id = ? AND block_uuid = ?
                """,
                (
                    section["id"], _json(effective), spec["edit_policy"], _json(baseline), baseline_hash,
                    _json(override) if override else None, source_hash,
                    "confirmed" if confirmation_status == "confirmed" else "unconfirmed",
                    sort_order, timestamp, project_id, block_uuid,
                ),
            )
        current_block = db.execute(
            "SELECT revision FROM report_blocks WHERE project_id = ? AND block_uuid = ?",
            (project_id, block_uuid),
        ).fetchone()
        revision = int(current_block["revision"])
        db.execute(
            "UPDATE report_block_revisions SET is_current = 0 WHERE project_id = ? AND block_uuid = ? AND is_current = 1",
            (project_id, block_uuid),
        )
        revision_uuid = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO report_block_revisions (
                revision_uuid, block_uuid, project_id, revision,
                baseline_json, baseline_hash, source_snapshot_json, source_hash,
                override_json, override_reason, generation_status, confirmation_status,
                rule_set_id, rule_id, generated_at, confirmed_at, confirmed_by,
                project_revision, is_current
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                revision_uuid, block_uuid, project_id, revision, _json(baseline), baseline_hash,
                _json(source_snapshot), source_hash, _json(override) if override else None,
                override_reason, confirmation_status, rule_set.rule_set_id, spec["rule_id"], timestamp,
                confirmed_at, confirmed_by, project_revision,
            ),
        )
        output.append(
            {
                "block_uuid": block_uuid,
                "block_key": spec["block_key"],
                "baseline": baseline,
                "override": override,
                "effective": effective,
                "generation_status": "current",
                "confirmation_status": confirmation_status,
                "edit_policy": spec["edit_policy"],
                "rule_id": spec["rule_id"],
                "source_hash": source_hash,
                "revision": revision,
            }
        )
    return output


def _insert_run(
    db: sqlite3.Connection,
    *,
    run_uuid: str,
    project_id: int,
    status: str,
    rule_set: DerivedRuleSet,
    input_hash: str,
    projection: dict[str, Any] | None,
    issues: list[dict[str, Any]],
    state_revision: int,
) -> sqlite3.Row:
    timestamp = database.utc_now()
    db.execute(
        """
        INSERT INTO report_generation_runs (
            run_uuid, project_id, status, rule_set_id, rule_set_hash, input_hash,
            projection_json, issues_json, state_revision, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_uuid, project_id, status, rule_set.rule_set_id, rule_set.content_sha256,
            input_hash, _json(projection) if projection is not None else None,
            _json(issues), state_revision, timestamp, timestamp,
        ),
    )
    return db.execute("SELECT * FROM report_generation_runs WHERE run_uuid = ?", (run_uuid,)).fetchone()


def _run_result(row: sqlite3.Row, project_revision: int | None = None) -> dict[str, Any]:
    return {
        "run_uuid": row["run_uuid"],
        "status": row["status"],
        "rule_set_id": row["rule_set_id"],
        "rule_set_hash": row["rule_set_hash"],
        "input_hash": row["input_hash"],
        "projection": _loads(row["projection_json"], None),
        "issues": _loads(row["issues_json"], []),
        "state_revision": int(row["state_revision"]),
        "project_revision": project_revision if project_revision is not None else int(row["state_revision"]),
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def impact_preview(project_uuid: str) -> dict[str, Any]:
    rules = _rules(project_uuid)
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        state = _state(db, project_id)
        facts, fact_issues = read_report_facts(db, project_id)
        source_groups = _source_group_hashes(db, project_id, facts)
        issues = list(fact_issues)
        try:
            build_projection(db, project_id, rule_set=rules)
        except ProjectionInputError as exc:
            issues.extend(exc.issues)
        current_blocks = _load_current_blocks(db, project_id)
        affected: list[str] = []
        review_overrides: list[str] = []
        if not current_blocks:
            affected = list(DERIVED_BLOCK_KEYS)
        else:
            existing = {block["block_key"] for block in current_blocks}
            affected.extend(key for key in DERIVED_BLOCK_KEYS if key not in existing)
            for block in current_blocks:
                dependencies = block["source_snapshot"].get("dependencies", {})
                changed = block["generation_status"] != "current" or any(
                    source_groups.get(name) != source_hash for name, source_hash in dependencies.items()
                )
                if changed:
                    affected.append(block["block_key"])
                    if block["override"]:
                        review_overrides.append(block["block_key"])
        affected = list(dict.fromkeys(affected))
        return {
            "project_revision": int(state["project_revision"]),
            "current_run_uuid": state["current_run_uuid"],
            "rule_set_id": rules.rule_set_id,
            "rule_set_hash": rules.content_sha256,
            "current_input_hash": stable_hash(source_groups),
            "last_input_hash": state["current_input_hash"],
            "has_changes": bool(affected) or state["current_input_hash"] != stable_hash(source_groups),
            "affected_blocks": affected,
            "overrides_requiring_review": review_overrides,
            "can_generate": not any(issue.get("code") == "RULE_SET_UNAVAILABLE" for issue in issues),
            "issues": issues,
        }


def create_generation_run(project_uuid: str, payload: GenerationRunWrite) -> dict[str, Any]:
    rules = _rules(project_uuid)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        state = _state(db, project_id)
        _expect_revision(state, payload.expected_project_revision, project_uuid)
        facts, fact_issues = read_report_facts(db, project_id)
        projection: dict[str, Any] | None = None
        issues: list[dict[str, Any]] = []
        try:
            projection = build_projection(db, project_id, rule_set=rules)
        except ProjectionInputError as exc:
            issues.extend(exc.issues)
        issues.extend(fact_issues)
        run_uuid = str(uuid.uuid4())
        next_revision = int(state["project_revision"]) + 1

        if projection is not None:
            finding_baselines = build_finding_baselines(projection["final_projection"], rules)
            _upsert_findings(db, project_id, project_uuid, finding_baselines)
            risk_rows = _load_risk_rows(db, project_id)
            invalid_threats = sorted(
                {threat for risk in risk_rows for threat in risk["threat_ids"] if threat not in rules.threat_ids}
            )
            if invalid_threats:
                issues.append(
                    {
                        "code": "RISK_THREAT_REFERENCE_INVALID",
                        "message": "风险关联了母版威胁目录以外的编号。",
                        "field": "threat_ids",
                        "details": {"threat_ids": invalid_threats},
                    }
                )
            risk_snapshot, risk_issues = risk_snapshot_from_rows(
                risk_rows, projection["final_projection"]["statistics"]
            )
            issues.extend(risk_issues)
        else:
            finding_baselines = []
            risk_snapshot = None
            risk_rows = _load_risk_rows(db, project_id)

        source_groups = _source_group_hashes(db, project_id, facts)
        input_hash = stable_hash(source_groups)
        if issues or projection is None or risk_snapshot is None:
            _stale_current_blocks(db, project_id)
            partial = None if projection is None else {
                **projection,
                "findings": finding_baselines,
                "risk_inputs": risk_rows,
            }
            run = _insert_run(
                db,
                run_uuid=run_uuid,
                project_id=project_id,
                status="needs_input",
                rule_set=rules,
                input_hash=input_hash,
                projection=partial,
                issues=issues,
                state_revision=next_revision,
            )
            updated_state = _advance_state(
                db,
                state,
                project_uuid,
                {
                    "current_run_uuid": run_uuid,
                    "current_input_hash": input_hash,
                    "source_groups_json": _json(source_groups),
                },
            )
            return _run_result(run, int(updated_state["project_revision"]))

        conclusion = assessment_conclusion(projection["final_projection"]["score"], risk_snapshot)
        block_specs = generate_narrative_blocks(
            facts=facts,
            projection=projection,
            findings=finding_baselines,
            risk_snapshot=risk_snapshot,
            conclusion=conclusion,
            rule_set=rules,
        )
        blocks = _persist_blocks(
            db, project_id, project_uuid, block_specs, source_groups, rules, next_revision
        )
        context_base = {
            "schema_version": R3_CONTEXT_SCHEMA_VERSION,
            "generation_run_uuid": run_uuid,
            "generation_state_revision": next_revision,
            "rule_set_id": rules.rule_set_id,
            "rule_set_hash": rules.content_sha256,
            "input_hash": input_hash,
            "source_hashes": source_groups,
            **projection,
            "findings": finding_baselines,
            "risk_snapshot": risk_snapshot,
            "assessment_conclusion": conclusion,
        }
        _assert_no_private_factors(context_base, project_uuid)
        run_projection = {**context_base, "blocks": blocks}
        run = _insert_run(
            db,
            run_uuid=run_uuid,
            project_id=project_id,
            status="current",
            rule_set=rules,
            input_hash=input_hash,
            projection=run_projection,
            issues=[],
            state_revision=next_revision,
        )
        updated_state = _advance_state(
            db,
            state,
            project_uuid,
            {
                "current_run_uuid": run_uuid,
                "current_input_hash": input_hash,
                "current_context_json": _json(context_base),
                "current_context_hash": None,
                "source_groups_json": _json(source_groups),
            },
        )
        return _run_result(run, int(updated_state["project_revision"]))


def get_generation_run(project_uuid: str, run_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        state = _state(db, int(project["id"]))
        row = db.execute(
            "SELECT * FROM report_generation_runs WHERE project_id = ? AND run_uuid = ?",
            (project["id"], run_uuid),
        ).fetchone()
        if row is None:
            raise ReportDomainError(
                "GENERATION_RUN_NOT_FOUND",
                "正文生成运行不存在。",
                status_code=404,
                project_uuid=project_uuid,
                entity_uuid=run_uuid,
            )
        return _run_result(row, int(state["project_revision"]))


def list_findings(project_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        state = _state(db, project_id)
        items = [
            dict(row)
            for row in db.execute(
                """
                SELECT finding_uuid, indicator_code, layer_code, final_indicator_result,
                       source_hash, active, created_at, updated_at
                FROM report_findings WHERE project_id = ? AND active = 1
                ORDER BY indicator_code
                """,
                (project_id,),
            ).fetchall()
        ]
        return {"project_revision": int(state["project_revision"]), "items": items}


def list_risks(project_uuid: str) -> dict[str, Any]:
    rules = _rules(project_uuid)
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        state = _state(db, int(project["id"]))
        return {
            "project_revision": int(state["project_revision"]),
            "threat_catalog": list(rules.threat_catalog),
            "items": _load_risk_rows(db, int(project["id"])),
        }


def update_risk(project_uuid: str, risk_uuid: str, payload: RiskUpdateWrite) -> dict[str, Any]:
    rules = _rules(project_uuid)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        state = _state(db, project_id)
        _expect_revision(state, payload.expected_project_revision, project_uuid)
        row = db.execute(
            """
            SELECT r.* FROM report_risks r
            JOIN report_findings f ON f.finding_uuid = r.finding_uuid AND f.project_id = r.project_id
            WHERE r.project_id = ? AND r.risk_uuid = ? AND f.active = 1
            """,
            (project_id, risk_uuid),
        ).fetchone()
        if row is None:
            raise ReportDomainError("RISK_NOT_FOUND", "风险记录不存在或已失效。", status_code=404, project_uuid=project_uuid, entity_uuid=risk_uuid)
        if int(row["revision"]) != payload.expected_revision:
            raise ReportDomainError(
                "REVISION_CONFLICT", "风险记录已更新，请刷新后重试。", status_code=409,
                project_uuid=project_uuid, entity_uuid=risk_uuid,
                details={"expected_revision": payload.expected_revision, "current_revision": int(row["revision"])},
            )
        invalid = [threat for threat in payload.threat_ids if threat not in rules.threat_ids]
        if invalid:
            raise ReportDomainError(
                "RISK_THREAT_REFERENCE_INVALID", "关联威胁只能选择母版固定目录中的编号。", status_code=422,
                project_uuid=project_uuid, entity_uuid=risk_uuid, field="threat_ids", details={"threat_ids": invalid},
            )
        analysis_text = (payload.analysis_text or "").strip()
        if analysis_text and (PLACEHOLDER_PATTERN.search(analysis_text) or FORBIDDEN_OVERRIDE_PATTERN.search(analysis_text)):
            raise ReportDomainError("RISK_ANALYSIS_TEXT_INVALID", "风险分析包含占位符或不允许的内容。", status_code=422, project_uuid=project_uuid, entity_uuid=risk_uuid, field="analysis_text")
        existing_override = _loads(row["analysis_override_json"], None)
        changed_existing_level = row["risk_level"] is not None and row["risk_level"] != payload.risk_level
        changed_analysis = (existing_override or {}).get("text", "") != analysis_text
        if (changed_existing_level or (existing_override is not None and changed_analysis)) and not payload.override_reason.strip():
            raise ReportDomainError("RISK_CHANGE_REASON_REQUIRED", "调整风险等级或人工风险分析时必须填写理由。", status_code=422, project_uuid=project_uuid, entity_uuid=risk_uuid, field="override_reason")
        if payload.confirm and (payload.risk_level is None or not payload.threat_ids):
            raise ReportDomainError("RISK_INPUT_INCOMPLETE", "确认风险前必须选择风险等级和至少一个关联威胁。", status_code=422, project_uuid=project_uuid, entity_uuid=risk_uuid)
        status = "confirmed" if payload.confirm else (
            "unconfirmed" if payload.risk_level is not None and payload.threat_ids else "needs_input"
        )
        cursor = db.execute(
            """
            UPDATE report_risks
            SET risk_level = ?, analysis_override_json = ?, override_reason = ?,
                confirmation_status = ?, revision = revision + 1, updated_at = ?
            WHERE project_id = ? AND risk_uuid = ? AND revision = ?
            """,
            (
                payload.risk_level, _json({"text": analysis_text}) if analysis_text else None,
                payload.override_reason.strip(), status, database.utc_now(), project_id, risk_uuid,
                payload.expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise ReportDomainError("REVISION_CONFLICT", "风险记录已更新，请刷新后重试。", status_code=409, project_uuid=project_uuid, entity_uuid=risk_uuid)
        db.execute("DELETE FROM report_risk_threat_relations WHERE project_id = ? AND risk_uuid = ?", (project_id, risk_uuid))
        for index, threat_id in enumerate(payload.threat_ids):
            db.execute(
                "INSERT INTO report_risk_threat_relations (risk_uuid, project_id, threat_catalog_id, sort_order) VALUES (?, ?, ?, ?)",
                (risk_uuid, project_id, threat_id, index),
            )
        _stale_current_blocks(db, project_id, dependency="risks")
        updated_state = _advance_state(db, state, project_uuid, {"current_context_hash": None})
        updated = next(item for item in _load_risk_rows(db, project_id) if item["risk_uuid"] == risk_uuid)
        return {"project_revision": int(updated_state["project_revision"]), "risk": updated}


def _load_current_blocks(db: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT
            b.project_id, b.block_uuid, b.block_key, b.edit_policy, b.revision AS block_revision,
            br.revision_uuid, br.revision, br.baseline_json, br.baseline_hash,
            br.source_snapshot_json, br.source_hash, br.override_json, br.override_reason,
            br.generation_status, br.confirmation_status, br.rule_set_id, br.rule_id,
            br.generated_at, br.confirmed_at, br.confirmed_by, br.project_revision
        FROM report_blocks b
        JOIN report_block_revisions br
          ON br.block_uuid = b.block_uuid AND br.project_id = b.project_id AND br.is_current = 1
        WHERE b.project_id = ? AND b.source_kind = 'derived'
        ORDER BY b.sort_order, b.block_key
        """,
        (project_id,),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        baseline = _loads(item.pop("baseline_json"), {})
        override = _loads(item.pop("override_json"), None)
        item["source_snapshot"] = _loads(item.pop("source_snapshot_json"), {})
        item["baseline"] = baseline
        item["override"] = override
        item["effective"] = _effective_payload(item["block_key"], baseline, override)
        output.append(item)
    return output


def review_state(project_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        state = _state(db, int(project["id"]))
        latest = db.execute(
            "SELECT * FROM report_consistency_checks WHERE project_id = ? ORDER BY id DESC LIMIT 1",
            (project["id"],),
        ).fetchone()
        return {
            "project_revision": int(state["project_revision"]),
            "current_run_uuid": state["current_run_uuid"],
            "current_input_hash": state["current_input_hash"],
            "blocks": _load_current_blocks(db, int(project["id"])),
            "latest_consistency": _check_result(latest) if latest else None,
        }


def _validate_override(block: dict[str, Any], override: dict[str, Any], project_uuid: str) -> dict[str, Any]:
    block_key = block["block_key"]
    allowed = BLOCK_OVERRIDE_FIELDS.get(block_key)
    if allowed is None or block["edit_policy"] != "overrideable" or set(override) != allowed:
        raise ReportDomainError("BLOCK_OVERRIDE_NOT_ALLOWED", "该派生块或字段不允许人工覆盖。", status_code=422, project_uuid=project_uuid, entity_uuid=block["block_uuid"], field="override")
    if block_key == "conclusion.system_summary":
        text = str(override["text"] or "").strip()
        normalized: dict[str, Any] = {"text": text}
    elif block_key.startswith("overall_evaluation.layer."):
        text = str(override["situation_description"] or "").strip()
        normalized = {"situation_description": text}
    else:
        raw_items = override["items"]
        if not isinstance(raw_items, dict):
            raise ReportDomainError("BLOCK_OVERRIDE_SCHEMA_INVALID", "改进建议覆盖内容格式无效。", status_code=422, project_uuid=project_uuid, entity_uuid=block["block_uuid"], field="override.items")
        allowed_indicators = {item["indicator_code"] for item in block["baseline"].get("items", [])}
        if set(raw_items) - allowed_indicators:
            raise ReportDomainError("BLOCK_OVERRIDE_SCHEMA_INVALID", "改进建议覆盖包含未知指标。", status_code=422, project_uuid=project_uuid, entity_uuid=block["block_uuid"], field="override.items")
        normalized_items = {str(key): str(value or "").strip() for key, value in raw_items.items()}
        if not normalized_items or any(not value for value in normalized_items.values()):
            raise ReportDomainError("BLOCK_OVERRIDE_SCHEMA_INVALID", "改进建议覆盖内容不能为空。", status_code=422, project_uuid=project_uuid, entity_uuid=block["block_uuid"], field="override.items")
        normalized = {"items": normalized_items}
        text = " ".join(normalized_items.values())
    if not text or len(text) > 20000 or PLACEHOLDER_PATTERN.search(text) or FORBIDDEN_OVERRIDE_PATTERN.search(text):
        raise ReportDomainError("BLOCK_OVERRIDE_TEXT_INVALID", "人工文案不能为空，且不能包含占位符、脚本或模板提示语。", status_code=422, project_uuid=project_uuid, entity_uuid=block["block_uuid"], field="override")
    return normalized


def _replace_block_revision(
    db: sqlite3.Connection,
    block: dict[str, Any],
    *,
    override: dict[str, Any] | None,
    override_reason: str,
    confirmation_status: str,
    project_revision: int,
    confirmed_by: str | None,
) -> dict[str, Any]:
    timestamp = database.utc_now()
    db.execute(
        "UPDATE report_block_revisions SET is_current = 0 WHERE revision_uuid = ? AND is_current = 1",
        (block["revision_uuid"],),
    )
    next_revision = int(block["revision"]) + 1
    confirmed_at = timestamp if confirmation_status == "confirmed" else None
    revision_uuid = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO report_block_revisions (
            revision_uuid, block_uuid, project_id, revision, baseline_json, baseline_hash,
            source_snapshot_json, source_hash, override_json, override_reason,
            generation_status, confirmation_status, rule_set_id, rule_id,
            generated_at, confirmed_at, confirmed_by, project_revision, is_current
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            revision_uuid, block["block_uuid"], block["project_id"], next_revision,
            _json(block["baseline"]), block["baseline_hash"], _json(block["source_snapshot"]),
            block["source_hash"], _json(override) if override else None, override_reason,
            block["generation_status"], confirmation_status, block["rule_set_id"], block["rule_id"],
            timestamp, confirmed_at, confirmed_by, project_revision,
        ),
    )
    effective = _effective_payload(block["block_key"], block["baseline"], override)
    db.execute(
        """
        UPDATE report_blocks
        SET payload_json = ?, override_json = ?, confirmation_status = ?,
            revision = revision + 1, updated_at = ?
        WHERE project_id = ? AND block_uuid = ?
        """,
        (
            _json(effective), _json(override) if override else None,
            "confirmed" if confirmation_status == "confirmed" else "unconfirmed",
            timestamp, block["project_id"], block["block_uuid"],
        ),
    )
    return next(item for item in _load_current_blocks(db, int(block["project_id"])) if item["block_uuid"] == block["block_uuid"])


def override_block(project_uuid: str, block_uuid: str, payload: DerivedBlockOverrideWrite) -> dict[str, Any]:
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        state = _state(db, project_id)
        _expect_revision(state, payload.expected_project_revision, project_uuid)
        block = next((item for item in _load_current_blocks(db, project_id) if item["block_uuid"] == block_uuid), None)
        if block is None:
            raise ReportDomainError("DERIVED_BLOCK_NOT_FOUND", "派生正文块不存在。", status_code=404, project_uuid=project_uuid, entity_uuid=block_uuid)
        if block["generation_status"] != "current":
            raise ReportDomainError("DERIVED_BLOCK_STALE", "过期或失败的正文块不能修改，请先重新生成。", project_uuid=project_uuid, entity_uuid=block_uuid)
        override_reason = payload.override_reason.strip()
        if not override_reason:
            raise ReportDomainError(
                "BLOCK_OVERRIDE_REASON_REQUIRED",
                "保存人工正文版本时必须填写覆盖理由。",
                status_code=422,
                project_uuid=project_uuid,
                entity_uuid=block_uuid,
                field="override_reason",
            )
        normalized = _validate_override(block, payload.override, project_uuid)
        updated = _replace_block_revision(
            db, block, override=normalized, override_reason=override_reason,
            confirmation_status="unconfirmed", project_revision=int(state["project_revision"]) + 1,
            confirmed_by=None,
        )
        updated_state = _advance_state(db, state, project_uuid, {"current_context_hash": None})
        return {"project_revision": int(updated_state["project_revision"]), "block": updated}


def confirm_block(project_uuid: str, block_uuid: str, payload: DerivedBlockConfirmationWrite) -> dict[str, Any]:
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        state = _state(db, project_id)
        _expect_revision(state, payload.expected_project_revision, project_uuid)
        block = next((item for item in _load_current_blocks(db, project_id) if item["block_uuid"] == block_uuid), None)
        if block is None:
            raise ReportDomainError("DERIVED_BLOCK_NOT_FOUND", "派生正文块不存在。", status_code=404, project_uuid=project_uuid, entity_uuid=block_uuid)
        if block["generation_status"] != "current":
            raise ReportDomainError("DERIVED_BLOCK_STALE", "过期或失败的正文块不能确认，请先重新生成。", project_uuid=project_uuid, entity_uuid=block_uuid)
        if payload.action == "keep_override" and not block["override"]:
            raise ReportDomainError("BLOCK_OVERRIDE_MISSING", "当前正文块没有可保留的人工版本。", status_code=422, project_uuid=project_uuid, entity_uuid=block_uuid)
        override = None if payload.action in {"discard_override", "reset"} else block["override"]
        status = "unconfirmed" if payload.action == "reset" else "confirmed"
        updated = _replace_block_revision(
            db, block, override=override,
            override_reason="" if override is None else str(block["override_reason"] or ""),
            confirmation_status=status, project_revision=int(state["project_revision"]) + 1,
            confirmed_by="local-user" if status == "confirmed" else None,
        )
        updated_state = _advance_state(db, state, project_uuid, {"current_context_hash": None})
        return {"project_revision": int(updated_state["project_revision"]), "block": updated}


def _mark_changed_sources_stale(
    db: sqlite3.Connection,
    project_id: int,
    source_groups: dict[str, str],
) -> list[str]:
    affected: list[str] = []
    for block in _load_current_blocks(db, project_id):
        dependencies = block["source_snapshot"].get("dependencies", {})
        if any(source_groups.get(name) != value for name, value in dependencies.items()):
            db.execute(
                "UPDATE report_block_revisions SET generation_status = 'stale' WHERE revision_uuid = ?",
                (block["revision_uuid"],),
            )
            db.execute(
                "UPDATE report_blocks SET generation_status = 'stale', revision = revision + 1, updated_at = ? WHERE project_id = ? AND block_uuid = ?",
                (database.utc_now(), project_id, block["block_uuid"]),
            )
            affected.append(block["block_key"])
    return affected


def _compose_context(
    db: sqlite3.Connection,
    project_id: int,
    state: sqlite3.Row,
    rule_set: DerivedRuleSet,
) -> dict[str, Any]:
    base = _loads(state["current_context_json"], None)
    if not isinstance(base, dict):
        raise ReportDomainError("R3_CONTEXT_NOT_AVAILABLE", "尚未生成可供 R4 使用的派生上下文。")
    blocks = _load_current_blocks(db, project_id)
    context = {
        **base,
        "blocks": [
            {
                "block_uuid": block["block_uuid"],
                "block_key": block["block_key"],
                "effective": block["effective"],
                "rule_id": block["rule_id"],
                "source_hash": block["source_hash"],
            }
            for block in blocks
        ],
        "threat_catalog": list(rule_set.threat_catalog),
    }
    _assert_no_private_factors(context, "")
    try:
        validate_context_payload(
            context,
            expected_block_keys=DERIVED_BLOCK_KEYS,
            expected_threat_ids=(item["id"] for item in rule_set.threat_catalog),
        )
    except ContextContractViolation as exc:
        raise ReportDomainError(
            "R3_CONTEXT_SCHEMA_INVALID",
            "派生上下文不符合已冻结的 R3ProjectionContext v1 契约。",
            status_code=500,
            details={"reason": exc.reason, **exc.details},
        ) from exc
    return context


def _assert_no_private_factors(value: Any, project_uuid: str) -> None:
    def walk(item: Any) -> bool:
        if isinstance(item, dict):
            return any(str(key).lower() in {"ra", "rk"} or walk(value) for key, value in item.items())
        if isinstance(item, list):
            return any(walk(value) for value in item)
        return False

    if walk(value):
        raise ReportDomainError(
            "R3_PRIVATE_FACTOR_LEAK",
            "R3 输出中检测到仅限内部评分使用的 Ra/Rk 字段。",
            status_code=500,
            project_uuid=project_uuid or None,
        )


def run_consistency_check(project_uuid: str, payload: ConsistencyCheckWrite) -> dict[str, Any]:
    rules = _rules(project_uuid)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        state = _state(db, project_id)
        _expect_revision(state, payload.expected_project_revision, project_uuid)
        source_groups = _source_group_hashes(db, project_id)
        stale = _mark_changed_sources_stale(db, project_id, source_groups)
        blocks = _load_current_blocks(db, project_id)
        issues: list[dict[str, Any]] = []
        current_run = db.execute(
            "SELECT * FROM report_generation_runs WHERE project_id = ? AND run_uuid = ?",
            (project_id, state["current_run_uuid"]),
        ).fetchone() if state["current_run_uuid"] else None
        if current_run is None or current_run["status"] != "current":
            issues.append({"code": "R3_GENERATION_NOT_CURRENT", "message": "当前生成运行不可用于正式上下文。"})
        existing_keys = {block["block_key"] for block in blocks}
        for missing in (key for key in DERIVED_BLOCK_KEYS if key not in existing_keys):
            issues.append({"code": "R3_BLOCK_NOT_GENERATED", "message": "派生正文块尚未生成。", "block_key": missing})
        for block in blocks:
            if block["generation_status"] != "current":
                issues.append({"code": "R3_BLOCK_STALE", "message": "派生正文块已过期。", "block_key": block["block_key"]})
            elif block["confirmation_status"] == "review_required":
                issues.append({"code": "R3_BLOCK_REVIEW_REQUIRED", "message": "人工文案基线已变化，需要复核。", "block_key": block["block_key"]})
            elif block["confirmation_status"] != "confirmed":
                issues.append({"code": "R3_BLOCK_CONFIRMATION_REQUIRED", "message": "派生正文块尚未确认。", "block_key": block["block_key"]})
        if stale:
            issues.append({"code": "R3_SOURCE_CHANGED", "message": "上游事实变化导致派生内容过期。", "details": {"blocks": stale}})
        if issues:
            status = "needs_input" if all(issue["code"] in {"R3_BLOCK_CONFIRMATION_REQUIRED", "R3_BLOCK_REVIEW_REQUIRED"} for issue in issues) else "invalid"
            context_hash = None
        else:
            context = _compose_context(db, project_id, state, rules)
            context_hash = stable_hash(context)
            status = "valid"
        next_revision = int(state["project_revision"]) + 1
        check_uuid = str(uuid.uuid4())
        timestamp = database.utc_now()
        db.execute(
            """
            INSERT INTO report_consistency_checks (
                check_uuid, project_id, run_uuid, status, issues_json,
                context_hash, state_revision, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                check_uuid, project_id, state["current_run_uuid"], status, _json(issues),
                context_hash, next_revision, timestamp,
            ),
        )
        updated_state = _advance_state(
            db, state, project_uuid,
            {"current_context_hash": context_hash, "source_groups_json": _json(source_groups)},
        )
        row = db.execute("SELECT * FROM report_consistency_checks WHERE check_uuid = ?", (check_uuid,)).fetchone()
        result = _check_result(row)
        result["project_revision"] = int(updated_state["project_revision"])
        return result


def _check_result(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "check_uuid": row["check_uuid"],
        "run_uuid": row["run_uuid"],
        "status": row["status"],
        "issues": _loads(row["issues_json"], []),
        "context_hash": row["context_hash"],
        "state_revision": int(row["state_revision"]),
        "checked_at": row["checked_at"],
    }


def latest_consistency_check(project_uuid: str) -> dict[str, Any] | None:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        state = _state(db, int(project["id"]))
        row = db.execute(
            "SELECT * FROM report_consistency_checks WHERE project_id = ? ORDER BY id DESC LIMIT 1",
            (project["id"],),
        ).fetchone()
        if row is None:
            return None
        result = _check_result(row)
        result["project_revision"] = int(state["project_revision"])
        return result


def get_projection_context(project_uuid: str) -> dict[str, Any]:
    rules = _rules(project_uuid)
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        state = _state(db, project_id)
        facts, fact_issues = read_report_facts(db, project_id)
        current_source_groups = _source_group_hashes(db, project_id, facts)
        stored_source_groups = _loads(state["source_groups_json"], {})
        if fact_issues or current_source_groups != stored_source_groups:
            raise ReportDomainError(
                "R3_CONTEXT_STALE",
                "派生上下文的上游事实已经变化，请重新生成并确认。",
                project_uuid=project_uuid,
                details={
                    "fact_issues": fact_issues,
                    "changed_source_groups": sorted(
                        key
                        for key in set(current_source_groups) | set(stored_source_groups)
                        if current_source_groups.get(key) != stored_source_groups.get(key)
                    ),
                },
            )
        latest = db.execute(
            "SELECT * FROM report_consistency_checks WHERE project_id = ? ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if latest is None or latest["status"] != "valid" or int(latest["state_revision"]) != int(state["project_revision"]):
            raise ReportDomainError(
                "R3_CONTEXT_NOT_CONFIRMED",
                "派生上下文尚未通过当前 revision 的一致性校验。",
                project_uuid=project_uuid,
                details={"project_revision": int(state["project_revision"])},
            )
        current_run = db.execute(
            """
            SELECT status, rule_set_id, rule_set_hash, input_hash
            FROM report_generation_runs
            WHERE project_id = ? AND run_uuid = ?
            """,
            (project_id, state["current_run_uuid"]),
        ).fetchone()
        if (
            current_run is None
            or current_run["status"] != "current"
            or current_run["rule_set_id"] != rules.rule_set_id
            or current_run["rule_set_hash"] != rules.content_sha256
            or current_run["input_hash"] != stable_hash(current_source_groups)
        ):
            raise ReportDomainError(
                "R3_CONTEXT_STALE",
                "当前生成运行与规则或事实输入不一致，请重新生成。",
                project_uuid=project_uuid,
            )
        blocks = _load_current_blocks(db, project_id)
        block_keys = {block["block_key"] for block in blocks}
        if block_keys != set(DERIVED_BLOCK_KEYS) or any(
            block["generation_status"] != "current"
            or block["confirmation_status"] != "confirmed"
            for block in blocks
        ):
            raise ReportDomainError(
                "R3_CONTEXT_NOT_CONFIRMED",
                "派生正文块不完整、已过期或尚未确认。",
                project_uuid=project_uuid,
            )
        context = _compose_context(db, project_id, state, rules)
        actual_hash = stable_hash(context)
        if actual_hash != latest["context_hash"] or actual_hash != state["current_context_hash"]:
            raise ReportDomainError(
                "R3_CONTEXT_HASH_MISMATCH",
                "派生上下文哈希与一致性结果不匹配。",
                status_code=500,
                project_uuid=project_uuid,
            )
        result = {
            **context,
            "consistency": _check_result(latest),
            "project_revision": int(state["project_revision"]),
        }
        try:
            validate_context_envelope(
                result,
                expected_block_keys=DERIVED_BLOCK_KEYS,
                expected_threat_ids=(item["id"] for item in rules.threat_catalog),
            )
        except ContextContractViolation as exc:
            raise ReportDomainError(
                "R3_CONTEXT_SCHEMA_INVALID",
                "派生上下文不符合已冻结的 R3ProjectionContext v1 契约。",
                status_code=500,
                project_uuid=project_uuid,
                details={"reason": exc.reason, **exc.details},
            ) from exc
        return result
