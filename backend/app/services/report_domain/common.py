from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import date
from typing import Any, Iterable

from ... import database
from ...contracts import (
    FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)
from .errors import ReportDomainError


JSON_FIELDS = {
    "extension_json": "controlled_extension",
    "phase_json": "phase_data",
    "profile_json": "profile_data",
    "algorithms_json": "selected_algorithms",
    "application_catalog_json": "application_catalog",
    "properties_json": "properties",
    "methods_json": "methods",
    "original_refs_json": "original_references",
    "payload_json": "payload",
    "baseline_json": "baseline",
    "override_json": "override",
}


def new_uuid() -> str:
    return str(uuid.uuid4())


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def load_json(value: str | None, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for column, alias in JSON_FIELDS.items():
        if column in result:
            default: Any = [] if column in {"algorithms_json", "application_catalog_json", "methods_json"} else {}
            result[alias] = load_json(result.pop(column), default)
    for key, value in list(result.items()):
        if key in {"active", "is_leader", "no_crypto_products"}:
            result[key] = bool(value)
    return result


def rows_dict(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_dict(row) or {} for row in rows]


def parse_iso_date(value: str | None, *, field: str, project_uuid: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ReportDomainError(
            "REPORT_DATE_INVALID",
            "日期格式无效，应使用 YYYY-MM-DD。",
            status_code=422,
            project_uuid=project_uuid,
            field=field,
        ) from exc


def source_hash(value: Any) -> str:
    return hashlib.sha256(dump_json(value).encode("utf-8")).hexdigest()


def require_report_project(project_uuid: str, db: sqlite3.Connection) -> sqlite3.Row:
    try:
        normalized = str(uuid.UUID(project_uuid))
    except ValueError as exc:
        raise ReportDomainError(
            "PROJECT_UUID_INVALID",
            "项目标识格式无效。",
            status_code=422,
            project_uuid=project_uuid,
        ) from exc
    project = database.get_project_by_uuid(normalized, db)
    if project is None:
        raise ReportDomainError(
            "PROJECT_NOT_FOUND",
            "项目不存在。",
            status_code=404,
            project_uuid=normalized,
        )
    if project["project_type"] != "full_report":
        raise ReportDomainError(
            "REPORT_DOMAIN_NOT_AVAILABLE",
            "仅完整报告项目可以访问报告数据域。",
            status_code=409,
            project_uuid=normalized,
        )
    expected = (
        FULL_REPORT_TEMPLATE_PACKAGE_ID,
        FULL_REPORT_TEMPLATE_EDITION,
        FULL_REPORT_TEMPLATE_REVISION,
        FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    )
    actual = (
        project["template_package_id"],
        project["template_edition"],
        project["template_revision"],
        project["template_asset_set_hash"],
    )
    if actual != expected:
        raise ReportDomainError(
            "BOUND_TEMPLATE_UNAVAILABLE",
            "项目绑定的完整报告模板不可用。",
            status_code=409,
            project_uuid=normalized,
            details={"expected_package_id": FULL_REPORT_TEMPLATE_PACKAGE_ID},
        )
    return project


REVISION_UUID_COLUMNS = {
    "report_metadata": "metadata_uuid",
    "report_organizations": "organization_uuid",
    "report_members": "member_uuid",
    "report_phase_dates": "phase_dates_uuid",
    "report_distribution": "distribution_uuid",
    "system_profiles": "profile_uuid",
    "system_crypto_products": "product_uuid",
    "report_standards": "standard_uuid",
    "special_indicators": "indicator_uuid",
    "assessment_objects": "object_uuid",
    "assessment_object_subsystems": "binding_uuid",
    "object_relations": "relation_uuid",
    "result_correction_relations": "correction_uuid",
    "report_sections": "section_uuid",
    "report_blocks": "block_uuid",
}


def require_cas_updated(
    db: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    *,
    table: str,
    project_id: int,
    expected_revision: int,
    project_uuid: str,
    entity_type: str,
    entity_uuid: str,
) -> None:
    """校验带 revision 条件的 UPDATE 是否成功，避免读后校验竞态。"""

    if cursor.rowcount == 1:
        return
    uuid_column = REVISION_UUID_COLUMNS.get(table)
    if uuid_column is None:
        raise ValueError(f"unsupported revision table: {table}")
    current_row = db.execute(
        f"SELECT revision FROM {table} WHERE project_id=? AND {uuid_column}=?",
        (project_id, entity_uuid),
    ).fetchone()
    current_revision = int(current_row["revision"]) if current_row is not None else None
    raise ReportDomainError(
        "REVISION_CONFLICT",
        "内容已在其他页面更新，请刷新后重试。",
        status_code=409,
        project_uuid=project_uuid,
        entity_type=entity_type,
        entity_uuid=entity_uuid,
        details={"expected_revision": expected_revision, "current_revision": current_revision},
    )


def touch_project(
    db: sqlite3.Connection,
    project_id: int,
    *,
    advance_revision: bool = True,
) -> None:
    """Mark report facts changed and advance the single project revision.

    Entity-level CAS still protects the individual write.  This project-level
    monotonic revision invalidates R3/R4/R7 snapshots for every R2 fact write.
    """
    timestamp = database.utc_now()
    db.execute(
        "UPDATE projects SET workflow_status = 'draft', updated_at = ? WHERE id = ?",
        (timestamp, project_id),
    )
    if advance_revision:
        db.execute(
            """
            UPDATE report_generation_state
            SET project_revision = project_revision + 1,
                current_context_hash = NULL,
                updated_at = ?
            WHERE project_id = ?
            """,
            (timestamp, project_id),
        )


def ensure_uuid_in_project(
    db: sqlite3.Connection,
    table: str,
    uuid_column: str,
    entity_uuid: str,
    project_id: int,
    *,
    project_uuid: str,
    entity_type: str,
) -> sqlite3.Row:
    row = db.execute(
        f"SELECT * FROM {table} WHERE {uuid_column} = ? AND project_id = ?",
        (entity_uuid, project_id),
    ).fetchone()
    if row is None:
        raise ReportDomainError(
            "REPORT_ENTITY_NOT_FOUND",
            "报告数据不存在或不属于当前项目。",
            status_code=404,
            project_uuid=project_uuid,
            entity_type=entity_type,
            entity_uuid=entity_uuid,
        )
    return row


def safe_json_size(value: Any, *, maximum: int, project_uuid: str, field: str) -> str:
    encoded = dump_json(value)
    if len(encoded.encode("utf-8")) > maximum:
        raise ReportDomainError(
            "REPORT_PAYLOAD_TOO_LARGE",
            "内容超过允许的大小。",
            status_code=422,
            project_uuid=project_uuid,
            field=field,
            details={"maximum_bytes": maximum},
        )
    return encoded
