"""Stable R2 report-context snapshots consumed by downstream stages."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .. import database
from .report_domain.common import require_report_project
from .report_domain.errors import ReportDomainError


ASSESSMENT_ORGANIZATION = {
    "name": "中互金认证有限公司",
    "address": "天津自贸试验区（中心商务区）新华路3678号宝风大厦28层2802",
    "postal_code": "300450",
    "contact_name": "李文宝",
    "contact_title": "商务经理",
    "contact_department": "业务部",
    "office_phone": "010-88720451",
    "mobile_phone": "15201294794",
    "email": "liwb@secallab.com",
}


def _loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _row(row: sqlite3.Row | None, *, json_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    if row is None:
        return {}
    result = dict(row)
    list_fields = {
        "travel_records_json", "site_visit_records_json", "selected_algorithms_json",
        "assessment_methods_json",
    }
    for field in json_fields:
        result[field.removesuffix("_json")] = _loads(
            result.pop(field, None), [] if field in list_fields else {}
        )
    return result


def _rows(rows: list[sqlite3.Row], *, json_fields: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    return [_row(row, json_fields=json_fields) for row in rows]


def _date(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else text


def _period(start: Any, end: Any) -> str:
    first, last = _date(start), _date(end)
    if first and last:
        return f"{first} 至 {last}"
    return first or last


def _project_revision(db: sqlite3.Connection, project_id: int) -> int:
    row = db.execute(
        "SELECT project_revision FROM report_generation_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["project_revision"]) if row is not None else 1


def _manual_blocks(db: sqlite3.Connection, project_id: int) -> dict[str, list[dict[str, Any]]]:
    records = db.execute(
        """
        SELECT s.section_key, b.block_uuid, b.block_key, b.block_type, b.payload_json,
               b.source_kind, b.sort_order, b.revision
        FROM report_blocks b
        JOIN report_sections s ON s.id = b.section_id AND s.project_id = b.project_id
        WHERE b.project_id = ? AND b.source_kind IN ('manual', 'imported')
        ORDER BY s.section_key, b.sort_order, b.block_uuid
        """,
        (project_id,),
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        item = dict(record)
        item["payload"] = _loads(item.pop("payload_json"), {})
        result.setdefault(str(record["section_key"]), []).append(item)
    return result


def _paragraph_text(blocks: dict[str, list[dict[str, Any]]], section_key: str) -> str:
    output: list[str] = []
    for block in blocks.get(section_key, []):
        payload = block["payload"]
        if block["block_type"] == "paragraph" and str(payload.get("text") or "").strip():
            output.append(str(payload["text"]).strip())
        elif block["block_type"] in {"bullet_list", "numbered_list"}:
            output.extend(str(value).strip() for value in payload.get("items", []) if str(value).strip())
    return "\n".join(output)


def _snapshot(db: sqlite3.Connection, project: sqlite3.Row) -> dict[str, Any]:
    project_id = int(project["id"])
    metadata = _row(
        db.execute("SELECT * FROM report_metadata WHERE project_id = ?", (project_id,)).fetchone(),
        json_fields=("extension_json",),
    )
    profile = _row(
        db.execute("SELECT * FROM system_profiles WHERE project_id = ?", (project_id,)).fetchone(),
        json_fields=(
            "service_scope_json", "platform_json", "operation_json", "interconnection_json",
            "cloud_platform_json", "crypto_plan_json", "selected_algorithms_json",
            "level_match_evidence_json",
        ),
    )
    organizations = _rows(db.execute(
        "SELECT * FROM report_organizations WHERE project_id = ? AND active = 1 ORDER BY sort_order, organization_uuid",
        (project_id,),
    ).fetchall())
    members = _rows(db.execute(
        "SELECT * FROM report_members WHERE project_id = ? AND active = 1 ORDER BY sort_order, member_uuid",
        (project_id,),
    ).fetchall())
    phases = _row(
        db.execute("SELECT * FROM report_phase_dates WHERE project_id = ?", (project_id,)).fetchone(),
        json_fields=("travel_records_json", "site_visit_records_json"),
    )
    distribution = _row(db.execute(
        "SELECT * FROM report_distribution WHERE project_id = ?", (project_id,)
    ).fetchone())
    products = _rows(db.execute(
        "SELECT * FROM system_crypto_products WHERE project_id = ? ORDER BY sort_order, product_uuid",
        (project_id,),
    ).fetchall())
    standards = _rows(db.execute(
        "SELECT * FROM report_standards WHERE project_id = ? ORDER BY sort_order, standard_uuid",
        (project_id,),
    ).fetchall())
    special_indicators = _rows(db.execute(
        "SELECT * FROM special_indicators WHERE project_id = ? ORDER BY sort_order, indicator_uuid",
        (project_id,),
    ).fetchall())
    objects = _rows(
        db.execute(
            "SELECT * FROM assessment_objects WHERE project_id = ? AND active = 1 ORDER BY source_section_code, name_snapshot, object_uuid",
            (project_id,),
        ).fetchall(),
        json_fields=("properties_json",),
    )
    subsystems = _rows(
        db.execute(
            "SELECT * FROM assessment_object_subsystems WHERE project_id = ? ORDER BY subsystem_name, object_uuid",
            (project_id,),
        ).fetchall(),
        json_fields=("assessment_methods_json",),
    )
    object_relations = _rows(
        db.execute(
            "SELECT * FROM object_relations WHERE project_id = ? AND active = 1 ORDER BY relation_uuid",
            (project_id,),
        ).fetchall(),
        json_fields=("properties_json",),
    )
    correction_relations = _rows(
        db.execute(
            "SELECT * FROM result_correction_relations WHERE project_id = ? ORDER BY correction_uuid",
            (project_id,),
        ).fetchall(),
        json_fields=("original_references_json",),
    )
    sections = _rows(db.execute(
        "SELECT * FROM report_sections WHERE project_id = ? ORDER BY sort_order, section_uuid",
        (project_id,),
    ).fetchall())
    manual_blocks = _manual_blocks(db, project_id)
    evidence = _rows(db.execute(
        "SELECT * FROM evidence_images WHERE project_id = ? ORDER BY section_code, sort_order, evidence_uuid, id",
        (project_id,),
    ).fetchall())

    assessed = next((item for item in organizations if item["organization_type"] == "assessed"), {})
    client = next((item for item in organizations if item["organization_type"] == "client"), {})
    members_by_uuid = {str(item["member_uuid"]): item for item in members}
    effective_client = str(client.get("name") or assessed.get("name") or "").strip()
    scalars = {
        "report_number": str(metadata.get("report_number") or "").strip(),
        "system_name": str(profile.get("system_name") or "").strip(),
        "system_overview": str(profile.get("system_summary") or "").strip(),
        "network_architecture": _paragraph_text(manual_blocks, "chapter.2.2"),
        "assessed_name": str(assessed.get("name") or "").strip(),
        "effective_client_name": effective_client,
        "has_separate_client": bool(client and str(client.get("name") or "").strip()),
        "assessment_name": ASSESSMENT_ORGANIZATION["name"],
        "report_date": _date(phases.get("analysis_end")),
        "security_level": str(metadata.get("classification_level") or "三级").strip(),
        "preparation_period": _period(phases.get("preparation_start"), phases.get("preparation_end")),
        "plan_period": _period(phases.get("scheme_start"), phases.get("scheme_end")),
        "assessment_period": _period(phases.get("fieldwork_start"), phases.get("fieldwork_end")),
        "report_period": _period(phases.get("analysis_start"), phases.get("analysis_end")),
        "assessment_start": _date(phases.get("preparation_start")),
        "assessment_end": _date(phases.get("analysis_end")),
        "regulator_copies": int(distribution.get("regulator_copies") or 0),
        "client_copies": int(distribution.get("client_copies") or 0),
        "assessment_copies": int(distribution.get("assessment_organization_copies") or 0),
    }
    scalars["total_copies"] = scalars["regulator_copies"] + scalars["client_copies"] + scalars["assessment_copies"]
    scalars["compiler"] = members_by_uuid.get(str(metadata.get("compiler_member_uuid") or ""), {})
    scalars["reviewer"] = members_by_uuid.get(str(metadata.get("reviewer_member_uuid") or ""), {})
    scalars["approver"] = members_by_uuid.get(str(metadata.get("approver_member_uuid") or ""), {})
    return {
        "schema_version": "1.0",
        "project": {
            key: project[key]
            for key in (
                "id", "project_uuid", "name", "project_type", "workflow_status",
                "template_package_id", "template_edition", "template_revision",
                "template_asset_set_hash", "updated_at",
            )
        },
        "metadata": metadata,
        "profile": profile,
        "organizations": organizations,
        "members": members,
        "phases": phases,
        "distribution": distribution,
        "products": products,
        "standards": standards,
        "special_indicators": special_indicators,
        "objects": objects,
        "subsystems": subsystems,
        "object_relations": object_relations,
        "correction_relations": correction_relations,
        "sections": sections,
        "manual_blocks": manual_blocks,
        "evidence": evidence,
        "assessment_organization": ASSESSMENT_ORGANIZATION,
        "scalars": scalars,
    }


def get_report_context(project_uuid: str, *, expected_revision: int) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        revision = _project_revision(db, int(project["id"]))
        if revision != expected_revision:
            raise ReportDomainError(
                "REVISION_CONFLICT", "项目 revision 已变化，请刷新后重试。", status_code=409,
                project_uuid=project_uuid,
                details={"expected_revision": expected_revision, "current_revision": revision},
            )
        return {
            "project_revision": revision,
            "project_updated_at": str(project["updated_at"]),
            "context": _snapshot(db, project),
        }


def get_project_revision(project_uuid: str) -> int:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        return _project_revision(db, int(project["id"]))


def assert_context_current(
    project_uuid: str,
    *,
    expected_revision: int,
    expected_project_updated_at: str,
) -> None:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        revision = _project_revision(db, int(project["id"]))
    if revision != expected_revision or str(project["updated_at"]) != expected_project_updated_at:
        raise ReportDomainError(
            "REVISION_CONFLICT",
            "装配上下文生成期间项目数据已变化，请刷新后重试。",
            status_code=409,
            project_uuid=project_uuid,
            details={"expected_revision": expected_revision, "current_revision": revision},
        )
