from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ... import database
from ...report_schemas import (
    CryptoProductUpdate,
    CryptoProductWrite,
    DistributionWrite,
    MemberUpdate,
    MemberWrite,
    OrganizationUpdate,
    OrganizationWrite,
    PhaseDatesWrite,
    ReportMetadataWrite,
    SpecialIndicatorUpdate,
    SpecialIndicatorWrite,
    StandardUpdate,
    StandardWrite,
    SystemProfileWrite,
)
from .common import (
    dump_json,
    ensure_uuid_in_project,
    load_json,
    new_uuid,
    parse_iso_date,
    require_cas_updated,
    require_report_project,
    row_dict,
    rows_dict,
    safe_json_size,
    touch_project,
)
from .errors import ReportDomainError


def _singleton(db: sqlite3.Connection, table: str, project_id: int) -> sqlite3.Row:
    row = db.execute(f"SELECT * FROM {table} WHERE project_id = ?", (project_id,)).fetchone()
    if row is None:
        raise ReportDomainError(
            "REPORT_DOMAIN_NOT_INITIALIZED",
            "完整报告数据域尚未初始化。",
            status_code=409,
            entity_type=table,
        )
    return row


def get_metadata(project_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        return get_metadata_in_connection(db, project_uuid, int(project["id"]))


def report_number_availability(project_uuid: str, report_number: str) -> dict[str, Any]:
    normalized = report_number.strip()
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        duplicate_count = 0
        if normalized:
            duplicate_count = int(
                db.execute(
                    """
                    SELECT COUNT(*)
                    FROM report_metadata
                    WHERE project_id <> ?
                      AND TRIM(report_number) <> ''
                      AND LOWER(TRIM(report_number)) = LOWER(?)
                    """,
                    (project["id"], normalized),
                ).fetchone()[0]
            )
        return {
            "report_number": normalized,
            "available": bool(normalized) and duplicate_count == 0,
            "duplicate_project_count": duplicate_count,
            "empty": not bool(normalized),
        }


def _member_for_role(
    db: sqlite3.Connection,
    member_uuid: str | None,
    project_id: int,
    *,
    project_uuid: str,
    role: str,
) -> sqlite3.Row | None:
    if member_uuid is None:
        return None
    row = db.execute(
        "SELECT * FROM report_members WHERE project_id = ? AND member_uuid = ? AND active = 1",
        (project_id, member_uuid),
    ).fetchone()
    if row is None:
        raise ReportDomainError(
            "APPROVAL_MEMBER_INVALID",
            "编审人员必须来自当前项目的有效成员。",
            status_code=422,
            project_uuid=project_uuid,
            field=f"{role}_member_uuid",
        )
    if role == "compiler" and (bool(row["is_project_leader"]) or row["team_role"] not in {"member", "组员"}):
        raise ReportDomainError(
            "COMPILER_ROLE_INVALID",
            "编制人必须为组员且不得担任项目负责人。",
            status_code=422,
            project_uuid=project_uuid,
            field="compiler_member_uuid",
        )
    if role in {"compiler", "reviewer", "approver"} and not row["qualification_passed_at"]:
        raise ReportDomainError(
            "APPROVAL_MEMBER_QUALIFICATION_REQUIRED",
            "编审人员必须填写密评人员考核通过时间。",
            status_code=422,
            project_uuid=project_uuid,
            field=f"{role}_member_uuid",
        )
    return row


def update_metadata(project_uuid: str, payload: ReportMetadataWrite) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        row = _singleton(db, "report_metadata", project_id)
        supplied = payload.model_fields_set
        values = {
            "report_number": payload.report_number if "report_number" in supplied else row["report_number"],
            "default_export_version": payload.default_export_version if "default_export_version" in supplied else row["default_export_version"],
            "classification_level": payload.classification_level if "classification_level" in supplied else row["classification_level"],
            "confidentiality_level": payload.confidentiality_level if "confidentiality_level" in supplied else row["confidentiality_level"],
            "compiler_member_uuid": payload.compiler_member_uuid if "compiler_member_uuid" in supplied else row["compiler_member_uuid"],
            "reviewer_member_uuid": payload.reviewer_member_uuid if "reviewer_member_uuid" in supplied else row["reviewer_member_uuid"],
            "approver_member_uuid": payload.approver_member_uuid if "approver_member_uuid" in supplied else row["approver_member_uuid"],
        }
        role_values = {
            "compiler": values["compiler_member_uuid"],
            "reviewer": values["reviewer_member_uuid"],
            "approver": values["approver_member_uuid"],
        }
        for role, member_uuid in role_values.items():
            _member_for_role(db, member_uuid, project_id, project_uuid=project_uuid, role=role)
        selected = [value for value in role_values.values() if value]
        if len(selected) != len(set(selected)):
            raise ReportDomainError(
                "APPROVAL_ROLES_MUST_BE_DISTINCT",
                "编制人、审核人和批准人不得由同一人员兼任。",
                status_code=422,
                project_uuid=project_uuid,
                field="compiler_member_uuid",
            )
        extension_value = payload.controlled_extension if "controlled_extension" in supplied else __import__("json").loads(row["extension_json"])
        extension = safe_json_size(
            extension_value,
            maximum=32_768,
            project_uuid=project_uuid,
            field="controlled_extension",
        )
        timestamp = database.utc_now()
        cursor = db.execute(
            """
            UPDATE report_metadata
            SET report_number = ?, default_export_version = ?, classification_level = ?,
                confidentiality_level = ?, compiler_member_uuid = ?, reviewer_member_uuid = ?,
                approver_member_uuid = ?, extension_json = ?, revision = revision + 1,
                updated_at = ?
            WHERE project_id = ? AND metadata_uuid = ? AND revision = ?
            """,
            (
                str(values["report_number"]).strip(),
                str(values["default_export_version"]).strip(),
                str(values["classification_level"]).strip(),
                str(values["confidentiality_level"]).strip(),
                values["compiler_member_uuid"],
                values["reviewer_member_uuid"],
                values["approver_member_uuid"],
                extension,
                timestamp,
                project_id,
                row["metadata_uuid"],
                payload.expected_revision,
            ),
        )
        require_cas_updated(
            db,
            cursor,
            table="report_metadata",
            project_id=project_id,
            expected_revision=payload.expected_revision,
            project_uuid=project_uuid,
            entity_type="report_metadata",
            entity_uuid=row["metadata_uuid"],
        )
        touch_project(db, project_id)
        return get_metadata_in_connection(db, project_uuid, project_id)


def get_metadata_in_connection(db: sqlite3.Connection, project_uuid: str, project_id: int) -> dict[str, Any]:
    row = _singleton(db, "report_metadata", project_id)
    result = row_dict(row) or {}
    result["project_uuid"] = project_uuid
    result["system_name"] = _singleton(db, "system_profiles", project_id)["system_name"]
    organizations = db.execute(
        "SELECT organization_uuid, organization_type, name, active "
        "FROM report_organizations WHERE project_id=? AND organization_type IN ('assessed','client')",
        (project_id,),
    ).fetchall()
    assessed = next((item for item in organizations if item["organization_type"] == "assessed"), None)
    client = next(
        (
            item
            for item in organizations
            if item["organization_type"] == "client"
            and bool(item["active"])
            and str(item["name"]).strip()
        ),
        None,
    )
    effective = client or assessed
    result["assessed_organization_uuid"] = assessed["organization_uuid"] if assessed else None
    result["assessed_organization_name"] = assessed["name"] if assessed else ""
    result["client_organization_uuid"] = client["organization_uuid"] if client else None
    result["client_organization_name"] = client["name"] if client else ""
    result["effective_client_organization_uuid"] = effective["organization_uuid"] if effective else None
    result["effective_client_organization_name"] = effective["name"] if effective else ""
    result["operator_organization_uuid"] = assessed["organization_uuid"] if assessed else None
    result["operator_organization_name"] = assessed["name"] if assessed else ""
    return result


def list_organizations(project_uuid: str) -> list[dict[str, Any]]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        return rows_dict(
            cursor = db.execute(
                "SELECT * FROM report_organizations WHERE project_id = ? ORDER BY sort_order, id",
                (project["id"],),
            ).fetchall()
        )


def create_organization(project_uuid: str, payload: OrganizationWrite) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        timestamp = database.utc_now()
        placeholder = db.execute(
            "SELECT * FROM report_organizations WHERE project_id=? AND organization_type=?",
            (project["id"], payload.organization_type),
        ).fetchone()
        if placeholder is not None and not str(placeholder["name"]).strip():
            cursor = db.execute(
                """
                UPDATE report_organizations SET name=?,address=?,postal_code=?,contact_name=?,
                    contact_title=?,contact_department=?,office_phone=?,mobile_phone=?,email=?,
                    active=?,sort_order=?,revision=revision+1,updated_at=?
                WHERE project_id=? AND organization_uuid=? AND revision=?
                """,
                (
                    payload.name.strip(), payload.address.strip(), payload.postal_code.strip(),
                    payload.contact_name.strip(), payload.contact_title.strip(), payload.contact_department.strip(),
                    payload.office_phone.strip(), payload.mobile_phone.strip(), payload.email.strip(),
                    int(payload.active), payload.sort_order, timestamp, project["id"],
                    placeholder["organization_uuid"], placeholder["revision"],
                ),
            )
            require_cas_updated(
                db,
                cursor,
                table="report_organizations",
                project_id=int(project["id"]),
                expected_revision=int(placeholder["revision"]),
                project_uuid=project_uuid,
                entity_type="report_organization",
                entity_uuid=placeholder["organization_uuid"],
            )
            touch_project(db, int(project["id"]))
            return row_dict(db.execute("SELECT * FROM report_organizations WHERE id=?", (placeholder["id"],)).fetchone()) or {}
        organization_uuid = new_uuid()
        try:
            db.execute(
                """
                INSERT INTO report_organizations (
                    organization_uuid, project_id, organization_type, name, address, postal_code,
                    contact_name, contact_title, contact_department, office_phone, mobile_phone,
                    email, active, sort_order, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    organization_uuid,
                    project["id"],
                    payload.organization_type,
                    payload.name.strip(),
                    payload.address.strip(),
                    payload.postal_code.strip(),
                    payload.contact_name.strip(),
                    payload.contact_title.strip(),
                    payload.contact_department.strip(),
                    payload.office_phone.strip(),
                    payload.mobile_phone.strip(),
                    payload.email.strip(),
                    int(payload.active),
                    payload.sort_order,
                    timestamp,
                    timestamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ReportDomainError(
                "ORGANIZATION_ROLE_DUPLICATE",
                "被测单位和委托单位在每个项目中只能各有一个。",
                status_code=409,
                project_uuid=project_uuid,
                entity_type="report_organization",
                field="organization_type",
            ) from exc
        touch_project(db, int(project["id"]))
        return row_dict(db.execute("SELECT * FROM report_organizations WHERE organization_uuid = ?", (organization_uuid,)).fetchone()) or {}


def update_organization(project_uuid: str, organization_uuid: str, payload: OrganizationUpdate) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        row = ensure_uuid_in_project(db, "report_organizations", "organization_uuid", organization_uuid, int(project["id"]), project_uuid=project_uuid, entity_type="report_organization")
        try:
            cursor = db.execute(
                """
                UPDATE report_organizations SET organization_type=?, name=?, address=?, postal_code=?,
                    contact_name=?, contact_title=?, contact_department=?, office_phone=?, mobile_phone=?,
                    email=?, active=?, sort_order=?, revision=revision+1, updated_at=?
                WHERE organization_uuid=? AND project_id=? AND revision=?
                """,
                (
                    payload.organization_type, payload.name.strip(), payload.address.strip(), payload.postal_code.strip(),
                    payload.contact_name.strip(), payload.contact_title.strip(), payload.contact_department.strip(),
                    payload.office_phone.strip(), payload.mobile_phone.strip(), payload.email.strip(), int(payload.active),
                    payload.sort_order, database.utc_now(), organization_uuid, project["id"],
                    payload.expected_revision,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ReportDomainError("ORGANIZATION_ROLE_DUPLICATE", "被测单位和委托单位在每个项目中只能各有一个。", status_code=409, project_uuid=project_uuid, entity_type="report_organization", entity_uuid=organization_uuid, field="organization_type") from exc
        require_cas_updated(db,cursor,table="report_organizations",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="report_organization",entity_uuid=organization_uuid)
        touch_project(db, int(project["id"]))
        return row_dict(db.execute("SELECT * FROM report_organizations WHERE organization_uuid=?", (organization_uuid,)).fetchone()) or {}


def delete_organization(project_uuid: str, organization_uuid: str, expected_revision: int) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        row = ensure_uuid_in_project(db, "report_organizations", "organization_uuid", organization_uuid, int(project["id"]), project_uuid=project_uuid, entity_type="report_organization")
        references: list[dict[str, Any]] = []
        member_count = db.execute("SELECT COUNT(*) FROM report_members WHERE project_id=? AND organization_uuid=?", (project["id"], organization_uuid)).fetchone()[0]
        if member_count:
            references.append({"entity_type": "report_member", "count": int(member_count)})
        block_count = int(db.execute("SELECT COUNT(*) FROM report_blocks WHERE project_id=? AND instr(payload_json,?)>0", (project["id"], organization_uuid)).fetchone()[0])
        if block_count:
            references.append({"entity_type": "report_block", "count": block_count})
        evidence_count = int(
            db.execute(
                "SELECT COUNT(*) FROM report_evidence_items WHERE project_id = ? AND organization_uuid = ?",
                (project["id"], organization_uuid),
            ).fetchone()[0]
        )
        if evidence_count:
            references.append({"entity_type": "report_evidence_item", "count": evidence_count})
        if references:
            raise ReportDomainError("REPORT_ENTITY_REFERENCED", "该单位仍被报告数据引用，不能删除。", status_code=409, project_uuid=project_uuid, entity_type="report_organization", entity_uuid=organization_uuid, details={"references": references})
        cursor=db.execute("DELETE FROM report_organizations WHERE project_id=? AND organization_uuid=? AND revision=?", (project["id"], organization_uuid, expected_revision))
        require_cas_updated(db,cursor,table="report_organizations",project_id=int(project["id"]),expected_revision=expected_revision,project_uuid=project_uuid,entity_type="report_organization",entity_uuid=organization_uuid)
        touch_project(db, int(project["id"]))
        return row_dict(row) or {}


def list_members(project_uuid: str) -> list[dict[str, Any]]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        rows = db.execute("SELECT * FROM report_members WHERE project_id=? ORDER BY sort_order,id", (project["id"],)).fetchall()
        return [_member_result(row) for row in rows]


def _member_result(row: sqlite3.Row) -> dict[str, Any]:
    result = row_dict(row) or {}
    result["team_role"] = "leader" if bool(result.pop("is_project_leader")) else "member"
    result["is_leader"] = result["team_role"] == "leader"
    result["certificate_no"] = result.pop("certificate_number")
    return result


def _member_values(payload: MemberWrite) -> tuple[Any, ...]:
    return (
        payload.organization_uuid,
        payload.name.strip(),
        "项目负责人" if payload.is_leader else "组员",
        int(payload.is_leader),
        payload.qualification_passed_at,
        payload.title.strip(),
        payload.department.strip(),
        payload.certificate_no.strip(),
        payload.office_phone.strip(),
        payload.mobile_phone.strip(),
        payload.email.strip(),
        int(payload.active),
        payload.sort_order,
    )


def create_member(project_uuid: str, payload: MemberWrite) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        if payload.organization_uuid:
            ensure_uuid_in_project(db, "report_organizations", "organization_uuid", payload.organization_uuid, int(project["id"]), project_uuid=project_uuid, entity_type="report_organization")
        member_uuid = new_uuid()
        timestamp = database.utc_now()
        db.execute(
            """
            INSERT INTO report_members (
                member_uuid,project_id,organization_uuid,name,team_role,is_project_leader,
                qualification_passed_at,title,department,certificate_number,office_phone,
                mobile_phone,email,active,sort_order,revision,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
            """,
            (member_uuid, project["id"], *_member_values(payload), timestamp, timestamp),
        )
        touch_project(db, int(project["id"]))
        return _member_result(db.execute("SELECT * FROM report_members WHERE member_uuid=?", (member_uuid,)).fetchone())


def update_member(project_uuid: str, member_uuid: str, payload: MemberUpdate) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        row = ensure_uuid_in_project(db, "report_members", "member_uuid", member_uuid, int(project["id"]), project_uuid=project_uuid, entity_type="report_member")
        if payload.organization_uuid:
            ensure_uuid_in_project(db, "report_organizations", "organization_uuid", payload.organization_uuid, int(project["id"]), project_uuid=project_uuid, entity_type="report_organization")
        cursor = db.execute(
            """
            UPDATE report_members SET organization_uuid=?,name=?,team_role=?,is_project_leader=?,
                qualification_passed_at=?,title=?,department=?,certificate_number=?,office_phone=?,
                mobile_phone=?,email=?,active=?,sort_order=?,revision=revision+1,updated_at=?
            WHERE member_uuid=? AND project_id=? AND revision=?
            """,
            (*_member_values(payload), database.utc_now(), member_uuid, project["id"], payload.expected_revision),
        )
        require_cas_updated(db,cursor,table="report_members",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="report_member",entity_uuid=member_uuid)
        touch_project(db, int(project["id"]))
        return _member_result(db.execute("SELECT * FROM report_members WHERE member_uuid=?", (member_uuid,)).fetchone())


def delete_member(project_uuid: str, member_uuid: str, expected_revision: int) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        row = ensure_uuid_in_project(db, "report_members", "member_uuid", member_uuid, int(project["id"]), project_uuid=project_uuid, entity_type="report_member")
        metadata = _singleton(db, "report_metadata", int(project["id"]))
        references: list[dict[str, Any]] = [
            {"entity_type": "approval_role", "role": role}
            for role in ("compiler", "reviewer", "approver")
            if metadata[f"{role}_member_uuid"] == member_uuid
        ]
        phases = _singleton(db, "report_phase_dates", int(project["id"]))
        phase_references = 0
        for column in ("travel_records_json", "site_visit_records_json"):
            for record in load_json(phases[column], []):
                if isinstance(record, dict) and member_uuid in record.get("member_uuids", []):
                    phase_references += 1
        if phase_references:
            references.append({"entity_type": "report_phase_date_record", "count": phase_references})
        block_count = int(db.execute("SELECT COUNT(*) FROM report_blocks WHERE project_id=? AND instr(payload_json,?)>0", (project["id"], member_uuid)).fetchone()[0])
        if block_count:
            references.append({"entity_type": "report_block", "count": block_count})
        evidence_count = int(
            db.execute(
                "SELECT COUNT(*) FROM report_evidence_usages WHERE project_id = ? AND related_member_uuid = ?",
                (project["id"], member_uuid),
            ).fetchone()[0]
        )
        if evidence_count:
            references.append({"entity_type": "report_evidence_usage", "count": evidence_count})
        if references:
            raise ReportDomainError("REPORT_ENTITY_REFERENCED", "该成员仍被报告数据引用，不能删除。", status_code=409, project_uuid=project_uuid, entity_type="report_member", entity_uuid=member_uuid, details={"references": references})
        cursor=db.execute("DELETE FROM report_members WHERE member_uuid=? AND project_id=? AND revision=?", (member_uuid, project["id"], expected_revision))
        require_cas_updated(db,cursor,table="report_members",project_id=int(project["id"]),expected_revision=expected_revision,project_uuid=project_uuid,entity_type="report_member",entity_uuid=member_uuid)
        touch_project(db, int(project["id"]))
        return _member_result(row)


def get_phase_dates(project_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        return _phase_row(_singleton(db, "report_phase_dates", int(project["id"])))


def _phase_row(row: sqlite3.Row) -> dict[str, Any]:
    result = row_dict(row) or {}
    result["plan_start"] = result.pop("scheme_start")
    result["plan_end"] = result.pop("scheme_end")
    result["onsite_start"] = result.pop("fieldwork_start")
    result["onsite_end"] = result.pop("fieldwork_end")
    result["report_start"] = result.pop("analysis_start")
    result["report_end"] = result.pop("analysis_end")
    result["travel_records"] = __import__("json").loads(result.pop("travel_records_json"))
    result["onsite_records"] = __import__("json").loads(result.pop("site_visit_records_json"))
    result["plan_review_date"] = result.pop("scheme_review_at")
    result["report_review_date"] = result.pop("report_review_at")
    result["approval_date"] = result.pop("approved_at")
    result["local_travel_not_applicable"] = bool(result["local_travel_not_applicable"])
    result["assessment_start"] = result["preparation_start"]
    result["assessment_end"] = result["report_end"]
    result["compiled_date"] = result["report_end"]
    return result


def _validate_phase_dates(project_uuid: str, payload: PhaseDatesWrite) -> None:
    ranges = (
        ("preparation", payload.preparation_start, payload.preparation_end),
        ("plan", payload.plan_start, payload.plan_end),
        ("onsite", payload.onsite_start, payload.onsite_end),
        ("report", payload.report_start, payload.report_end),
    )
    parsed: dict[str, tuple[Any, Any]] = {}
    for prefix, start, end in ranges:
        start_date = parse_iso_date(start, field=f"{prefix}_start", project_uuid=project_uuid)
        end_date = parse_iso_date(end, field=f"{prefix}_end", project_uuid=project_uuid)
        if start_date and end_date and start_date > end_date:
            raise ReportDomainError("REPORT_DATE_ORDER_INVALID", "阶段开始日期不得晚于结束日期。", status_code=422, project_uuid=project_uuid, field=f"{prefix}_start")
        parsed[prefix] = (start_date, end_date)
    previous_end = None
    for prefix in ("preparation", "plan", "onsite", "report"):
        start_date, end_date = parsed[prefix]
        if previous_end and start_date and previous_end > start_date:
            raise ReportDomainError("REPORT_PHASE_ORDER_INVALID", "四个测评阶段的日期顺序不一致。", status_code=422, project_uuid=project_uuid, field=f"{prefix}_start")
        previous_end = end_date or previous_end
    onsite_dates = []
    for index, item in enumerate(payload.onsite_records):
        entry = parse_iso_date(item.entry_date, field=f"onsite_records.{index}.entry_date", project_uuid=project_uuid)
        exit_date = parse_iso_date(item.exit_date, field=f"onsite_records.{index}.exit_date", project_uuid=project_uuid)
        if entry and exit_date and entry > exit_date:
            raise ReportDomainError("ONSITE_RECORD_ORDER_INVALID", "进场日期不得晚于离场日期。", status_code=422, project_uuid=project_uuid, field=f"onsite_records.{index}.entry_date")
        onsite_dates.append((entry, exit_date))
    if onsite_dates:
        earliest = min(item[0] for item in onsite_dates if item[0])
        latest = max(item[1] for item in onsite_dates if item[1])
        if parsed["onsite"] != (earliest, latest):
            raise ReportDomainError("ONSITE_PERIOD_MISMATCH", "现场阶段日期必须等于进离场记录的最早进场和最晚离场日期。", status_code=422, project_uuid=project_uuid, field="onsite_records")
        if not payload.travel_records:
            raise ReportDomainError("TRAVEL_RECORD_REQUIRED", "存在现场测评记录时必须填写差旅记录；本地项目也需显式标记为本地无差旅。", status_code=422, project_uuid=project_uuid, field="travel_records")
        non_local_ranges = []
        for index, item in enumerate(payload.travel_records):
            if item.local_project:
                continue
            travel_start = parse_iso_date(item.start_date, field=f"travel_records.{index}.start_date", project_uuid=project_uuid)
            travel_end = parse_iso_date(item.end_date, field=f"travel_records.{index}.end_date", project_uuid=project_uuid)
            if not travel_start or not travel_end:
                raise ReportDomainError("TRAVEL_PERIOD_REQUIRED", "非本地项目的差旅记录必须填写起止日期。", status_code=422, project_uuid=project_uuid, field=f"travel_records.{index}.start_date")
            if travel_start > travel_end:
                raise ReportDomainError("TRAVEL_RECORD_ORDER_INVALID", "差旅开始日期不得晚于结束日期。", status_code=422, project_uuid=project_uuid, field=f"travel_records.{index}.start_date")
            non_local_ranges.append((travel_start, travel_end))
        if non_local_ranges:
            travel_start = min(item[0] for item in non_local_ranges)
            travel_end = max(item[1] for item in non_local_ranges)
            if travel_start > earliest or travel_end < latest:
                raise ReportDomainError("TRAVEL_PERIOD_NOT_COVER_ONSITE", "差旅区间必须覆盖对应的现场测评区间。", status_code=422, project_uuid=project_uuid, field="travel_records")
    review_date = parse_iso_date(payload.plan_review_date, field="plan_review_date", project_uuid=project_uuid)
    if review_date and parsed["onsite"][0] and review_date >= parsed["onsite"][0]:
        raise ReportDomainError("PLAN_REVIEW_DATE_INVALID", "方案评审时间必须早于现场测评开始日期。", status_code=422, project_uuid=project_uuid, field="plan_review_date")
    compiled = parsed["report"][1]
    reviewed = parse_iso_date(payload.report_review_date, field="report_review_date", project_uuid=project_uuid)
    approved = parse_iso_date(payload.approval_date, field="approval_date", project_uuid=project_uuid)
    if (
        (compiled and reviewed and compiled > reviewed)
        or (reviewed and approved and reviewed > approved)
        or (compiled and approved and not reviewed and compiled > approved)
    ):
        raise ReportDomainError("APPROVAL_DATE_ORDER_INVALID", "编制、审核和批准日期顺序不一致。", status_code=422, project_uuid=project_uuid, field="approval_date")


def _validate_phase_members(db: sqlite3.Connection, project_id: int, project_uuid: str, payload: PhaseDatesWrite) -> None:
    referenced = {
        member_uuid
        for record in [*payload.travel_records, *payload.onsite_records]
        for member_uuid in record.member_uuids
    }
    if not referenced:
        return
    placeholders = ",".join("?" for _ in referenced)
    rows = db.execute(
        f"SELECT member_uuid FROM report_members WHERE project_id=? AND active=1 AND member_uuid IN ({placeholders})",
        (project_id, *sorted(referenced)),
    ).fetchall()
    valid = {row["member_uuid"] for row in rows}
    missing = sorted(referenced - valid)
    if missing:
        raise ReportDomainError(
            "PHASE_MEMBER_REFERENCE_INVALID",
            "差旅和进离场人员必须引用当前项目的有效项目组成员。",
            status_code=422,
            project_uuid=project_uuid,
            field="member_uuids",
            details={"invalid_member_uuids": missing},
        )


def update_phase_dates(project_uuid: str, payload: PhaseDatesWrite) -> dict[str, Any]:
    _validate_phase_dates(project_uuid, payload)
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        _validate_phase_members(db, int(project["id"]), project_uuid, payload)
        row = _singleton(db, "report_phase_dates", int(project["id"]))
        travel = [item.model_dump(mode="json") for item in payload.travel_records]
        onsite = [item.model_dump(mode="json") for item in payload.onsite_records]
        local_not_applicable = bool(travel) and all(item.local_project for item in payload.travel_records)
        cursor = db.execute(
            """
            UPDATE report_phase_dates SET preparation_start=?,preparation_end=?,scheme_start=?,scheme_end=?,
                fieldwork_start=?,fieldwork_end=?,analysis_start=?,analysis_end=?,travel_records_json=?,
                site_visit_records_json=?,scheme_review_at=?,report_review_at=?,approved_at=?,
                local_travel_not_applicable=?,revision=revision+1,updated_at=?
            WHERE project_id=? AND phase_dates_uuid=? AND revision=?
            """,
            (payload.preparation_start,payload.preparation_end,payload.plan_start,payload.plan_end,payload.onsite_start,payload.onsite_end,
             payload.report_start,payload.report_end,dump_json(travel),dump_json(onsite),payload.plan_review_date,payload.report_review_date,
             payload.approval_date,int(local_not_applicable),database.utc_now(),project["id"],row["phase_dates_uuid"],payload.expected_revision),
        )
        require_cas_updated(db,cursor,table="report_phase_dates",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="report_phase_dates",entity_uuid=row["phase_dates_uuid"])
        touch_project(db, int(project["id"]))
        return _phase_row(_singleton(db, "report_phase_dates", int(project["id"])))


def get_distribution(project_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        result = row_dict(_singleton(db, "report_distribution", int(project["id"]))) or {}
        result["assessment_copies"] = result.pop("assessment_organization_copies")
        result["total_copies"] = result["regulator_copies"] + result["client_copies"] + result["assessment_copies"]
        return result


def update_distribution(project_uuid: str, payload: DistributionWrite) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        row = _singleton(db, "report_distribution", int(project["id"]))
        cursor = db.execute("UPDATE report_distribution SET regulator_copies=?,client_copies=?,assessment_organization_copies=?,revision=revision+1,updated_at=? WHERE project_id=? AND distribution_uuid=? AND revision=?", (payload.regulator_copies,payload.client_copies,payload.assessment_copies,database.utc_now(),project["id"],row["distribution_uuid"],payload.expected_revision))
        require_cas_updated(db,cursor,table="report_distribution",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="report_distribution",entity_uuid=row["distribution_uuid"])
        touch_project(db, int(project["id"]))
    return get_distribution(project_uuid)


def _profile_result(row: sqlite3.Row) -> dict[str, Any]:
    result = row_dict(row) or {}
    service = load_json(result.pop("service_scope_json"), {})
    operation = load_json(result.pop("operation_json"), {})
    cloud = load_json(result.pop("cloud_platform_json"), {})
    plan = load_json(result.pop("crypto_plan_json"), {})
    algorithms = load_json(result.pop("selected_algorithms_json"), [])
    platform = load_json(result.pop("platform_json"), {})
    interconnection = load_json(result.pop("interconnection_json"), {})
    filing_evidence = load_json(result.pop("level_match_evidence_json"), {})
    consistent = str(result.get("level_filing_consistent", ""))
    filing_same = filing_evidence.get("same")
    if filing_same is None and consistent:
        filing_same = consistent == "same"
    result.update({
        "service_scope": service.get("kind", ""), "service_scope_count": service.get("count"), "service_scope_other": service.get("other", ""),
        "operation_status": operation.get("status", ""), "operation_started_at": operation.get("started_at"), "construction_stage": operation.get("construction_stage", ""),
        "cloud_dependency": cloud.get("dependency", ""), "cloud_platform_name": cloud.get("name", ""), "cloud_assessment_status": cloud.get("assessment_status", ""),
        "cloud_assessment_organization": cloud.get("organization", ""), "cloud_assessment_date": cloud.get("date"), "cloud_assessment_conclusion": cloud.get("conclusion", ""),
        "crypto_plan_status": plan.get("status", ""), "crypto_plan_passed_at": plan.get("passed_at"), "crypto_plan_assessment_mode": plan.get("mode", ""),
        "crypto_plan_assessment_organization": plan.get("organization", ""), "selected_algorithms": algorithms,
        "other_algorithms": interconnection.get("other_algorithms", []),
        "application_catalog": interconnection.get("application_catalog", []),
        "platform": platform,
        "filing_certificate_no": result.pop("level_filing_number"),
        "filing_system_same": filing_same,
        "filing_system_name": result["system_name"] if filing_same is True else filing_evidence.get("system_name", ""),
        "filing_difference": filing_evidence.get("difference", result.get("level_filing_difference", "")),
    })
    return result


def get_system_profile(project_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        return _profile_result(_singleton(db, "system_profiles", int(project["id"])))


def _validate_profile_payload(project_uuid: str, payload: SystemProfileWrite) -> None:
    if payload.critical_infrastructure_status == "recognized" and not payload.critical_infrastructure_department.strip():
        raise ReportDomainError("CRITICAL_INFRASTRUCTURE_DEPARTMENT_REQUIRED", "已认定为关键信息基础设施时必须填写安全保护工作部门。", status_code=422, project_uuid=project_uuid, field="critical_infrastructure_department")
    if payload.level_filing_status == "filed" and (not payload.filing_s.strip() or not payload.filing_a.strip()):
        raise ReportDomainError("LEVEL_FILING_FIELDS_REQUIRED", "已定级备案时必须填写 S 和 A，G 可以为空。", status_code=422, project_uuid=project_uuid, field="filing_s")
    if payload.level_assessment_status == "assessed" and not (
        payload.level_assessment_organization.strip()
        and payload.level_assessment_date
        and payload.level_assessment_conclusion.strip()
    ):
        raise ReportDomainError("LEVEL_ASSESSMENT_FIELDS_REQUIRED", "等保已测评时必须填写机构、时间和结论。", status_code=422, project_uuid=project_uuid, field="level_assessment_status")
    if payload.level_assessment_status == "assessing" and not payload.level_assessment_organization.strip():
        raise ReportDomainError("LEVEL_ASSESSMENT_ORGANIZATION_REQUIRED", "等保正在测评时必须填写测评机构。", status_code=422, project_uuid=project_uuid, field="level_assessment_organization")
    if payload.cloud_dependency == "yes":
        if not payload.cloud_platform_name.strip() or not payload.cloud_assessment_status:
            raise ReportDomainError("CLOUD_PLATFORM_FIELDS_REQUIRED", "依赖云平台时必须填写平台名称和测评状态。", status_code=422, project_uuid=project_uuid, field="cloud_platform_name")
        if payload.cloud_assessment_status == "assessed" and not (
            payload.cloud_assessment_organization.strip()
            and payload.cloud_assessment_date
            and payload.cloud_assessment_conclusion.strip()
        ):
            raise ReportDomainError("CLOUD_ASSESSMENT_FIELDS_REQUIRED", "云平台已测评时必须填写机构、时间和结论。", status_code=422, project_uuid=project_uuid, field="cloud_assessment_status")
        if payload.cloud_assessment_status == "assessing" and not payload.cloud_assessment_organization.strip():
            raise ReportDomainError("CLOUD_ASSESSMENT_ORGANIZATION_REQUIRED", "云平台正在测评时必须填写测评机构。", status_code=422, project_uuid=project_uuid, field="cloud_assessment_organization")
    if payload.crypto_plan_status == "passed":
        if not payload.crypto_plan_passed_at or payload.crypto_plan_assessment_mode not in {"self", "commissioned"}:
            raise ReportDomainError("CRYPTO_PLAN_FIELDS_REQUIRED", "密码应用方案已通过密评时必须填写通过时间和评估方式。", status_code=422, project_uuid=project_uuid, field="crypto_plan_status")
        if payload.crypto_plan_assessment_mode == "commissioned" and not payload.crypto_plan_assessment_organization.strip():
            raise ReportDomainError("CRYPTO_PLAN_ORGANIZATION_REQUIRED", "委托评估时必须填写测评机构。", status_code=422, project_uuid=project_uuid, field="crypto_plan_assessment_organization")
    if payload.operation_status == "running" and not payload.operation_started_at:
        raise ReportDomainError("OPERATION_DATE_REQUIRED", "系统已投入运行时必须填写投入运行年月。", status_code=422, project_uuid=project_uuid, field="operation_started_at")
    if payload.operation_status == "not_running" and not payload.construction_stage.strip():
        raise ReportDomainError("CONSTRUCTION_STAGE_REQUIRED", "系统未投入运行时必须填写当前建设阶段。", status_code=422, project_uuid=project_uuid, field="construction_stage")
    if payload.service_scope in {"cross_province", "cross_city"} and payload.service_scope_count is None:
        raise ReportDomainError("SERVICE_SCOPE_COUNT_REQUIRED", "跨省或跨市服务范围必须填写正整数数量。", status_code=422, project_uuid=project_uuid, field="service_scope_count")
    if payload.service_scope == "other" and not payload.service_scope_other.strip():
        raise ReportDomainError("SERVICE_SCOPE_OTHER_REQUIRED", "选择其他服务范围时必须填写说明。", status_code=422, project_uuid=project_uuid, field="service_scope_other")
    if payload.filing_system_same is False and (
        not payload.filing_system_name.strip() or not payload.filing_difference.strip()
    ):
        raise ReportDomainError("FILING_SYSTEM_DIFFERENCE_REQUIRED", "备案系统与被测系统不同时必须填写备案系统名称和差异说明。", status_code=422, project_uuid=project_uuid, field="filing_system_name")
    if len(payload.application_catalog) != len(set(payload.application_catalog)):
        raise ReportDomainError("APPLICATION_CATALOG_DUPLICATE", "表 2-7 应用名称不能重复。", status_code=422, project_uuid=project_uuid, field="application_catalog")


def update_system_profile(project_uuid: str, payload: SystemProfileWrite) -> dict[str, Any]:
    _validate_profile_payload(project_uuid, payload)
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        row = _singleton(db, "system_profiles", int(project["id"]))
        service = {"kind": payload.service_scope, "count": payload.service_scope_count, "other": payload.service_scope_other}
        operation = {"status": payload.operation_status, "started_at": payload.operation_started_at, "construction_stage": payload.construction_stage}
        cloud = {"dependency": payload.cloud_dependency,"name":payload.cloud_platform_name,"assessment_status":payload.cloud_assessment_status,"organization":payload.cloud_assessment_organization,"date":payload.cloud_assessment_date,"conclusion":payload.cloud_assessment_conclusion}
        plan = {"status":payload.crypto_plan_status,"passed_at":payload.crypto_plan_passed_at,"mode":payload.crypto_plan_assessment_mode,"organization":payload.crypto_plan_assessment_organization}
        filing_name = payload.system_name.strip() if payload.filing_system_same is True else payload.filing_system_name.strip()
        filing_evidence = {
            "same": payload.filing_system_same,
            "system_name": filing_name,
            "difference": payload.filing_difference,
        }
        interconnection = {
            "application_catalog": payload.application_catalog,
            "other_algorithms": payload.other_algorithms,
        }
        cursor = db.execute(
            """
            UPDATE system_profiles SET system_name=?,system_summary=?,critical_infrastructure_status=?,critical_infrastructure_department=?,
              level_filing_status=?,level_filing_s=?,level_filing_a=?,level_filing_g=?,level_filing_number=?,
              level_filing_consistent=?,level_filing_difference=?,level_match_evidence_json=?,
              level_assessment_status=?,level_assessment_organization=?,level_assessment_period=?,level_assessment_conclusion=?,
              service_scope_json=?,operation_json=?,cloud_platform_json=?,crypto_plan_json=?,no_crypto_products=?,
              selected_algorithms_json=?,interconnection_json=?,revision=revision+1,updated_at=?
            WHERE project_id=? AND profile_uuid=? AND revision=?
            """,
            (payload.system_name.strip(),payload.system_summary,payload.critical_infrastructure_status,payload.critical_infrastructure_department,
             payload.level_filing_status,payload.filing_s,payload.filing_a,payload.filing_g,payload.filing_certificate_no,
             "same" if payload.filing_system_same is True else "different" if payload.filing_system_same is False else "",
             payload.filing_difference,dump_json(filing_evidence),
             payload.level_assessment_status,payload.level_assessment_organization,payload.level_assessment_date or "",payload.level_assessment_conclusion,
             dump_json(service),dump_json(operation),dump_json(cloud),dump_json(plan),int(payload.no_crypto_products),
             dump_json(payload.selected_algorithms),dump_json(interconnection),database.utc_now(),project["id"],row["profile_uuid"],payload.expected_revision),
        )
        require_cas_updated(db,cursor,table="system_profiles",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="system_profile",entity_uuid=row["profile_uuid"])
        touch_project(db, int(project["id"]))
        return _profile_result(_singleton(db, "system_profiles", int(project["id"])))


def normalize_quantity(quantity_text: str, *, project_uuid: str) -> int:
    value = quantity_text.strip()
    if value == "若干":
        return 1
    if not re.fullmatch(r"[0-9]+", value):
        raise ReportDomainError("CRYPTO_PRODUCT_QUANTITY_INVALID", "密码产品数量只能填写非负整数或“若干”。", status_code=422, project_uuid=project_uuid, field="quantity_text", details={"value": quantity_text, "allowed": ["非负整数", "若干"]})
    return int(value)


def list_crypto_products(project_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        rows = db.execute("SELECT * FROM system_crypto_products WHERE project_id=? ORDER BY sort_order,id", (project["id"],)).fetchall()
        items = [_product_result(row) for row in rows]
    total = sum(int(item["normalized_quantity"]) for item in items)
    return {
        "items": items,
        "summary": {
            "total": total,
            "exclusive": sum(int(item["normalized_quantity"]) for item in items if item["use_mode"] == "exclusive"),
            "shared": sum(int(item["normalized_quantity"]) for item in items if item["use_mode"] == "shared"),
            "certified": sum(int(item["normalized_quantity"]) for item in items if item["classification"] == "certified"),
            "uncertified_domestic": sum(int(item["normalized_quantity"]) for item in items if item["classification"] == "uncertified_domestic"),
            "foreign": sum(int(item["normalized_quantity"]) for item in items if item["classification"] == "foreign"),
        },
    }


def _product_result(row: sqlite3.Row) -> dict[str, Any]:
    result = row_dict(row) or {}
    result["name"] = result.pop("product_name")
    result["certificate_no"] = result.pop("certificate_number")
    return result


def _product_values(payload: CryptoProductWrite, project_uuid: str) -> tuple[Any, ...]:
    return (payload.name.strip(),payload.manufacturer.strip(),payload.model.strip(),payload.certificate_no.strip(),payload.quantity_text.strip(),normalize_quantity(payload.quantity_text, project_uuid=project_uuid),payload.use_mode,payload.classification,payload.sort_order)


def create_crypto_product(project_uuid: str, payload: CryptoProductWrite) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db); product_uuid=new_uuid(); timestamp=database.utc_now()
        db.execute("INSERT INTO system_crypto_products (product_uuid,project_id,product_name,manufacturer,model,certificate_number,quantity_text,normalized_quantity,use_mode,classification,sort_order,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)", (product_uuid,project["id"],*_product_values(payload, project_uuid),timestamp,timestamp))
        touch_project(db, int(project["id"]))
        return _product_result(db.execute("SELECT * FROM system_crypto_products WHERE product_uuid=?", (product_uuid,)).fetchone())


def update_crypto_product(project_uuid: str, product_uuid: str, payload: CryptoProductUpdate) -> dict[str, Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); row=ensure_uuid_in_project(db,"system_crypto_products","product_uuid",product_uuid,int(project["id"]),project_uuid=project_uuid,entity_type="system_crypto_product")
        cursor=db.execute("UPDATE system_crypto_products SET product_name=?,manufacturer=?,model=?,certificate_number=?,quantity_text=?,normalized_quantity=?,use_mode=?,classification=?,sort_order=?,revision=revision+1,updated_at=? WHERE product_uuid=? AND project_id=? AND revision=?", (*_product_values(payload, project_uuid),database.utc_now(),product_uuid,project["id"],payload.expected_revision))
        require_cas_updated(db,cursor,table="system_crypto_products",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="system_crypto_product",entity_uuid=product_uuid)
        touch_project(db,int(project["id"])); return _product_result(db.execute("SELECT * FROM system_crypto_products WHERE product_uuid=?",(product_uuid,)).fetchone())


def delete_crypto_product(project_uuid: str, product_uuid: str, expected_revision: int) -> dict[str, Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); row=ensure_uuid_in_project(db,"system_crypto_products","product_uuid",product_uuid,int(project["id"]),project_uuid=project_uuid,entity_type="system_crypto_product")
        cursor=db.execute("DELETE FROM system_crypto_products WHERE product_uuid=? AND project_id=? AND revision=?",(product_uuid,project["id"],expected_revision)); require_cas_updated(db,cursor,table="system_crypto_products",project_id=int(project["id"]),expected_revision=expected_revision,project_uuid=project_uuid,entity_type="system_crypto_product",entity_uuid=product_uuid); touch_project(db,int(project["id"])); return _product_result(row)


def list_standards(project_uuid: str) -> list[dict[str, Any]]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); return [_standard_result(row) for row in db.execute("SELECT * FROM report_standards WHERE project_id=? ORDER BY sort_order,id",(project["id"],)).fetchall()]


def _standard_result(row: sqlite3.Row) -> dict[str, Any]:
    result = row_dict(row) or {}
    result["kind"] = result.pop("standard_kind")
    result["code"] = result.pop("standard_code")
    result["name"] = result.pop("standard_name")
    result["source_ref"] = result.pop("source_reference")
    return result


def create_standard(project_uuid: str, payload: StandardWrite) -> dict[str, Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); standard_uuid=new_uuid(); timestamp=database.utc_now()
        db.execute("INSERT INTO report_standards (standard_uuid,project_id,standard_kind,standard_code,standard_name,source_reference,sort_order,revision,created_at,updated_at) VALUES (?,?,'manual',?,?,?,?,1,?,?)",(standard_uuid,project["id"],payload.code.strip(),payload.name.strip(),payload.source_ref.strip(),payload.sort_order,timestamp,timestamp)); touch_project(db,int(project["id"])); return _standard_result(db.execute("SELECT * FROM report_standards WHERE standard_uuid=?",(standard_uuid,)).fetchone())


def update_standard(project_uuid: str, standard_uuid: str, payload: StandardUpdate) -> dict[str, Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); row=ensure_uuid_in_project(db,"report_standards","standard_uuid",standard_uuid,int(project["id"]),project_uuid=project_uuid,entity_type="report_standard")
        if row["standard_kind"] == "template_constant": raise ReportDomainError("TEMPLATE_CONSTANT_READ_ONLY","模板固定标准不可修改。",status_code=409,project_uuid=project_uuid,entity_type="report_standard",entity_uuid=standard_uuid)
        cursor=db.execute("UPDATE report_standards SET standard_code=?,standard_name=?,source_reference=?,sort_order=?,revision=revision+1,updated_at=? WHERE standard_uuid=? AND project_id=? AND revision=?",(payload.code.strip(),payload.name.strip(),payload.source_ref.strip(),payload.sort_order,database.utc_now(),standard_uuid,project["id"],payload.expected_revision)); require_cas_updated(db,cursor,table="report_standards",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="report_standard",entity_uuid=standard_uuid); touch_project(db,int(project["id"])); return _standard_result(db.execute("SELECT * FROM report_standards WHERE standard_uuid=?",(standard_uuid,)).fetchone())


def delete_standard(project_uuid: str, standard_uuid: str, expected_revision: int) -> dict[str, Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); row=ensure_uuid_in_project(db,"report_standards","standard_uuid",standard_uuid,int(project["id"]),project_uuid=project_uuid,entity_type="report_standard")
        if row["standard_kind"] == "template_constant": raise ReportDomainError("TEMPLATE_CONSTANT_READ_ONLY","模板固定标准不可删除。",status_code=409,project_uuid=project_uuid,entity_type="report_standard",entity_uuid=standard_uuid)
        references=int(db.execute("SELECT COUNT(*) FROM special_indicators WHERE project_id=? AND manual_standard_uuid=?",(project["id"],standard_uuid)).fetchone()[0])
        if references: raise ReportDomainError("REPORT_ENTITY_REFERENCED","人工标准仍被特殊指标引用，不能删除。",status_code=409,project_uuid=project_uuid,entity_type="report_standard",entity_uuid=standard_uuid,details={"references":[{"entity_type":"special_indicator","count":references}]})
        cursor=db.execute("DELETE FROM report_standards WHERE project_id=? AND standard_uuid=? AND revision=?",(project["id"],standard_uuid,expected_revision)); require_cas_updated(db,cursor,table="report_standards",project_id=int(project["id"]),expected_revision=expected_revision,project_uuid=project_uuid,entity_type="report_standard",entity_uuid=standard_uuid); touch_project(db,int(project["id"])); return _standard_result(row)


def list_special_indicators(project_uuid: str) -> list[dict[str, Any]]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); return rows_dict(db.execute("SELECT * FROM special_indicators WHERE project_id=? ORDER BY sort_order,id",(project["id"],)).fetchall())


def _manual_standard(db: sqlite3.Connection, project_id: int, standard_uuid: str, project_uuid: str) -> None:
    row=ensure_uuid_in_project(db,"report_standards","standard_uuid",standard_uuid,project_id,project_uuid=project_uuid,entity_type="report_standard")
    if row["standard_kind"] != "manual": raise ReportDomainError("SPECIAL_INDICATOR_STANDARD_INVALID","特殊指标只能关联人工补充标准。",status_code=422,project_uuid=project_uuid,field="manual_standard_uuid")


def create_special_indicator(project_uuid: str, payload: SpecialIndicatorWrite) -> dict[str, Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); _manual_standard(db,int(project["id"]),payload.manual_standard_uuid,project_uuid); indicator_uuid=new_uuid(); timestamp=database.utc_now()
        db.execute("INSERT INTO special_indicators (indicator_uuid,project_id,manual_standard_uuid,indicator_code,indicator_name,description,sort_order,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,1,?,?)",(indicator_uuid,project["id"],payload.manual_standard_uuid,payload.indicator_code.strip(),payload.indicator_name.strip(),payload.description,payload.sort_order,timestamp,timestamp)); touch_project(db,int(project["id"])); return row_dict(db.execute("SELECT * FROM special_indicators WHERE indicator_uuid=?",(indicator_uuid,)).fetchone()) or {}


def update_special_indicator(project_uuid: str, indicator_uuid: str, payload: SpecialIndicatorUpdate) -> dict[str, Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); row=ensure_uuid_in_project(db,"special_indicators","indicator_uuid",indicator_uuid,int(project["id"]),project_uuid=project_uuid,entity_type="special_indicator"); _manual_standard(db,int(project["id"]),payload.manual_standard_uuid,project_uuid)
        cursor=db.execute("UPDATE special_indicators SET manual_standard_uuid=?,indicator_code=?,indicator_name=?,description=?,sort_order=?,revision=revision+1,updated_at=? WHERE indicator_uuid=? AND project_id=? AND revision=?",(payload.manual_standard_uuid,payload.indicator_code.strip(),payload.indicator_name.strip(),payload.description,payload.sort_order,database.utc_now(),indicator_uuid,project["id"],payload.expected_revision)); require_cas_updated(db,cursor,table="special_indicators",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="special_indicator",entity_uuid=indicator_uuid); touch_project(db,int(project["id"])); return row_dict(db.execute("SELECT * FROM special_indicators WHERE indicator_uuid=?",(indicator_uuid,)).fetchone()) or {}


def delete_special_indicator(project_uuid: str, indicator_uuid: str, expected_revision: int) -> dict[str, Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); row=ensure_uuid_in_project(db,"special_indicators","indicator_uuid",indicator_uuid,int(project["id"]),project_uuid=project_uuid,entity_type="special_indicator"); cursor=db.execute("DELETE FROM special_indicators WHERE project_id=? AND indicator_uuid=? AND revision=?",(project["id"],indicator_uuid,expected_revision)); require_cas_updated(db,cursor,table="special_indicators",project_id=int(project["id"]),expected_revision=expected_revision,project_uuid=project_uuid,entity_type="special_indicator",entity_uuid=indicator_uuid); touch_project(db,int(project["id"])); return row_dict(row) or {}
