"""R5 Appendix B records, relationships, validation and immutable projection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from fastapi import UploadFile
from pydantic import ValidationError

from .. import database
from ..report_derived.rules import canonical_json
from ..report_evidence.contracts import (
    ALLOWED_IMAGE_SUBTYPES,
    ALLOWED_RECORD_SUBTYPES,
    APPENDIX_B_CATEGORIES,
    APPENDIX_B_CATEGORY_CODES,
    CATEGORY_BY_CODE,
    SINGLE_RECORD_CATEGORIES,
)
from ..report_evidence.schemas import (
    EvidenceCategoryUpdate,
    EvidenceImageUpdate,
    EvidenceItemUpdate,
    EvidenceItemWrite,
    EvidenceReorderWrite,
    validate_category_metadata,
)
from .report_domain.common import dump_json, require_report_project, touch_project
from .report_domain.errors import ReportDomainError
from .report_generation import advance_project_revision_for_external_change
from .report_evidence_files import (
    ManagedFileTombstone,
    discard_managed_file,
    finalize_managed_tombstone,
    reconcile_managed_tombstones,
    resolve_managed_path,
    restore_managed_tombstone,
    save_upload,
    stage_managed_file_removal,
    verify_stored_image,
)


_IMAGE_EXPECTED = frozenset(
    {
        "engagement_proof",
        "travel_accommodation",
        "onsite_process",
        "authorization_notice",
        "plan_review",
        "report_review",
        "assessor_exam_proof",
        "grading_filing",
    }
)


def _loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value)) if value not in (None, "") else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def _issue(
    severity: str,
    code: str,
    message: str,
    *,
    category_code: str | None = None,
    item_uuid: str | None = None,
    field: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "category_code": category_code,
        "item_uuid": item_uuid,
        "field": field,
        "details": details or {},
        "navigation_target": f"appendix_b:{category_code}" if category_code else "appendix_b",
    }


def _category_contract(category_code: str) -> dict[str, object]:
    contract = CATEGORY_BY_CODE.get(category_code)
    if contract is None:
        raise ReportDomainError(
            "APPENDIX_B_CATEGORY_INVALID",
            "附录 B 类别无效。",
            status_code=422,
            field="category_code",
        )
    return contract


def _project_revision(db: sqlite3.Connection, project_id: int) -> int:
    row = db.execute(
        "SELECT project_revision FROM report_generation_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["project_revision"]) if row is not None else 1


def _expect_project_revision(
    db: sqlite3.Connection,
    project_id: int,
    expected_revision: int,
    project_uuid: str,
) -> int:
    current = _project_revision(db, project_id)
    if current != expected_revision:
        raise ReportDomainError(
            "PROJECT_REVISION_CONFLICT",
            "附录 B 所依赖的项目内容已变化，请刷新后重试。",
            status_code=409,
            project_uuid=project_uuid,
            field="expected_project_revision",
            details={"expected_revision": expected_revision, "current_revision": current},
        )
    return current


def _mark_project_changed(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
) -> int:
    revision = advance_project_revision_for_external_change(
        db, project_id, project_uuid
    )
    touch_project(db, project_id)
    return revision


def _parse_date(value: str | None, *, project_uuid: str, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ReportDomainError(
            "APPENDIX_B_DATE_INVALID",
            "日期格式无效，应使用 YYYY-MM-DD。",
            status_code=422,
            project_uuid=project_uuid,
            field=field,
        ) from exc


def _date_text(value: str | None) -> str | None:
    return value[:10] if value else None


def _record_rows(
    db: sqlite3.Connection, project_id: int, category_code: str
) -> list[sqlite3.Row]:
    return db.execute(
        """
        SELECT * FROM report_evidence_items
        WHERE project_id = ? AND category_code = ? AND item_kind = 'record'
        ORDER BY sort_order, starts_on, item_uuid
        """,
        (project_id, category_code),
    ).fetchall()


def _usage_rows(db: sqlite3.Connection, project_id: int, item_uuid: str) -> list[sqlite3.Row]:
    return db.execute(
        """
        SELECT u.*, m.name AS member_name, m.qualification_passed_at,
               m.team_role, m.is_project_leader,
               related.category_code AS related_category_code
        FROM report_evidence_usages u
        LEFT JOIN report_members m
          ON m.project_id = u.project_id AND m.member_uuid = u.related_member_uuid
        LEFT JOIN report_evidence_items related
          ON related.project_id = u.project_id AND related.item_uuid = u.related_item_uuid
        WHERE u.project_id = ? AND u.evidence_item_uuid = ?
        ORDER BY u.sort_order, u.usage_uuid
        """,
        (project_id, item_uuid),
    ).fetchall()


def _item_result(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _loads(result.pop("metadata_json"), {})
    result["usages"] = [dict(item) for item in _usage_rows(db, int(row["project_id"]), str(row["item_uuid"]))]
    if result["item_kind"] == "image":
        result["file_url"] = f"/api/projects/{result['project_id']}/report-evidence-items/{result['item_uuid']}/file"
    else:
        result["file_url"] = None
    return result


def _member_client_result(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["team_role"] = "leader" if bool(result["is_project_leader"]) else "member"
    result["is_leader"] = bool(result.pop("is_project_leader"))
    result["certificate_no"] = result.pop("certificate_number")
    result["active"] = bool(result["active"])
    return result


def _organization_client_result(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["active"] = bool(result["active"])
    return result


def _category_row(
    db: sqlite3.Connection, project_id: int, category_code: str
) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM report_evidence_categories WHERE project_id = ? AND category_code = ?",
        (project_id, category_code),
    ).fetchone()
    if row is None:
        raise ReportDomainError(
            "APPENDIX_B_CATEGORY_NOT_INITIALIZED",
            "附录 B 类别尚未初始化。",
            status_code=500,
            field="category_code",
        )
    return row


def _item_in_project(
    db: sqlite3.Connection, item_uuid: str, *, expected_kind: str | None = None
) -> tuple[sqlite3.Row, sqlite3.Row]:
    row = db.execute(
        """
        SELECT i.*, p.project_uuid, p.project_type, p.template_package_id,
               p.template_edition, p.template_revision, p.template_asset_set_hash
        FROM report_evidence_items i
        JOIN projects p ON p.id = i.project_id
        WHERE i.item_uuid = ?
        """,
        (item_uuid,),
    ).fetchone()
    if row is None or row["project_type"] != "full_report":
        raise ReportDomainError(
            "APPENDIX_B_ITEM_NOT_FOUND",
            "附录 B 记录不存在。",
            status_code=404,
            entity_uuid=item_uuid,
        )
    if expected_kind and row["item_kind"] != expected_kind:
        raise ReportDomainError(
            "APPENDIX_B_ITEM_KIND_INVALID",
            "附录 B 记录类型不匹配。",
            status_code=422,
            entity_uuid=item_uuid,
        )
    project = require_report_project(str(row["project_uuid"]), db)
    return project, row


def _validate_metadata(
    category_code: str, metadata: dict[str, Any], *, project_uuid: str
) -> dict[str, Any]:
    try:
        return validate_category_metadata(category_code, metadata)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(item) for item in first.get("loc", ()))
        raise ReportDomainError(
            "APPENDIX_B_METADATA_INVALID",
            str(first.get("msg") or "附录 B 记录内容无效。"),
            status_code=422,
            project_uuid=project_uuid,
            field=f"metadata.{location}" if location else "metadata",
        ) from exc


def _validate_member_references(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    member_uuids: Iterable[str],
) -> dict[str, sqlite3.Row]:
    requested = tuple(dict.fromkeys(member_uuids))
    if not requested:
        return {}
    placeholders = ",".join("?" for _ in requested)
    rows = db.execute(
        f"SELECT * FROM report_members WHERE project_id = ? AND active = 1 "
        f"AND member_uuid IN ({placeholders})",
        (project_id, *requested),
    ).fetchall()
    found = {str(row["member_uuid"]): row for row in rows}
    missing = sorted(set(requested) - set(found))
    if missing:
        raise ReportDomainError(
            "APPENDIX_B_MEMBER_NOT_IN_TEAM",
            "附录 B 人员必须引用当前项目的有效项目组成员。",
            status_code=422,
            project_uuid=project_uuid,
            field="member_uuids",
            details={"invalid_member_uuids": missing},
        )
    return found


def _validate_organization(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    organization_uuid: str | None,
) -> None:
    if not organization_uuid:
        return
    row = db.execute(
        "SELECT 1 FROM report_organizations WHERE project_id = ? AND organization_uuid = ? AND active = 1",
        (project_id, organization_uuid),
    ).fetchone()
    if row is None:
        raise ReportDomainError(
            "APPENDIX_B_ORGANIZATION_INVALID",
            "责任单位必须引用当前项目的有效单位。",
            status_code=422,
            project_uuid=project_uuid,
            field="organization_uuid",
        )


def _related_records(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    related_item_uuids: Iterable[str],
    *,
    expected_category: str,
) -> dict[str, sqlite3.Row]:
    requested = tuple(dict.fromkeys(related_item_uuids))
    if not requested:
        return {}
    placeholders = ",".join("?" for _ in requested)
    rows = db.execute(
        f"SELECT * FROM report_evidence_items WHERE project_id = ? "
        f"AND item_uuid IN ({placeholders}) AND item_kind = 'record' AND category_code = ?",
        (project_id, *requested, expected_category),
    ).fetchall()
    found = {str(row["item_uuid"]): row for row in rows}
    missing = sorted(set(requested) - set(found))
    if missing:
        raise ReportDomainError(
            "APPENDIX_B_RELATED_ITEM_INVALID",
            "关联记录不存在或不属于当前项目和类别。",
            status_code=404,
            project_uuid=project_uuid,
            field="related_item_uuids",
            details={"invalid_item_uuids": missing},
        )
    return found


def _validate_record_payload(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    category_code: str,
    payload: EvidenceItemWrite,
    *,
    current_item_uuid: str | None = None,
) -> dict[str, Any]:
    allowed = ALLOWED_RECORD_SUBTYPES[category_code]
    if payload.subtype not in allowed:
        raise ReportDomainError(
            "APPENDIX_B_SUBTYPE_INVALID",
            "记录子类型不属于当前附录 B 类别。",
            status_code=422,
            project_uuid=project_uuid,
            field="subtype",
            details={"allowed": sorted(allowed)},
        )
    start = _parse_date(payload.starts_on, project_uuid=project_uuid, field="starts_on")
    end = _parse_date(payload.ends_on, project_uuid=project_uuid, field="ends_on")
    if start and end and start > end:
        raise ReportDomainError(
            "APPENDIX_B_DATE_ORDER_INVALID",
            "开始日期不得晚于结束日期。",
            status_code=422,
            project_uuid=project_uuid,
            field="starts_on",
        )
    if payload.ends_on and category_code not in {"travel_accommodation", "onsite_process"}:
        raise ReportDomainError(
            "APPENDIX_B_END_DATE_NOT_ALLOWED",
            "当前类别不允许填写结束日期。",
            status_code=422,
            project_uuid=project_uuid,
            field="ends_on",
        )
    if payload.starts_on and category_code in {"assessor_roster", "assessor_exam_proof"}:
        raise ReportDomainError(
            "APPENDIX_B_DATE_NOT_ALLOWED",
            "人员资格记录的时间来自中央人员考试通过时间，不允许另填记录日期。",
            status_code=422,
            project_uuid=project_uuid,
            field="starts_on",
        )
    if payload.organization_uuid and category_code not in {
        "travel_accommodation",
        "onsite_process",
    }:
        raise ReportDomainError(
            "APPENDIX_B_ORGANIZATION_NOT_ALLOWED",
            "当前类别不允许关联责任单位。",
            status_code=422,
            project_uuid=project_uuid,
            field="organization_uuid",
        )
    _validate_organization(db, project_id, project_uuid, payload.organization_uuid)
    members = _validate_member_references(db, project_id, project_uuid, payload.member_uuids)
    metadata = _validate_metadata(category_code, payload.metadata, project_uuid=project_uuid)

    if category_code in SINGLE_RECORD_CATEGORIES:
        existing = db.execute(
            """
            SELECT item_uuid FROM report_evidence_items
            WHERE project_id = ? AND category_code = ? AND item_kind = 'record'
              AND (? IS NULL OR item_uuid <> ?)
            """,
            (project_id, category_code, current_item_uuid, current_item_uuid),
        ).fetchone()
        if existing is not None:
            raise ReportDomainError(
                "APPENDIX_B_SINGLE_RECORD_REQUIRED",
                "该附录 B 类别只允许一条结构化记录。",
                status_code=409,
                project_uuid=project_uuid,
                field="category_code",
            )

    related: dict[str, sqlite3.Row] = {}
    if category_code == "travel_accommodation":
        related = _related_records(
            db,
            project_id,
            project_uuid,
            payload.related_item_uuids,
            expected_category="onsite_process",
        )
        if not metadata.get("is_local") and (not start or not end):
            raise ReportDomainError(
                "APPENDIX_B_TRAVEL_PERIOD_REQUIRED",
                "非本地差旅记录必须填写起止日期。",
                status_code=422,
                project_uuid=project_uuid,
                field="starts_on",
            )
        for related_uuid, onsite in related.items():
            onsite_start = _parse_date(onsite["starts_on"], project_uuid=project_uuid, field="related_item_uuids")
            onsite_end = _parse_date(onsite["ends_on"], project_uuid=project_uuid, field="related_item_uuids")
            if not metadata.get("is_local") and (
                not start or not end or not onsite_start or not onsite_end
                or start > onsite_start or end < onsite_end
            ):
                raise ReportDomainError(
                    "APPENDIX_B_TRAVEL_NOT_COVER_VISIT",
                    "差旅起止日期必须覆盖所关联的进离场记录。",
                    status_code=422,
                    project_uuid=project_uuid,
                    field="related_item_uuids",
                    details={"onsite_item_uuid": related_uuid},
                )
    elif payload.related_item_uuids:
        raise ReportDomainError(
            "APPENDIX_B_RELATED_ITEM_NOT_ALLOWED",
            "当前类别不允许关联其他记录。",
            status_code=422,
            project_uuid=project_uuid,
            field="related_item_uuids",
        )

    if category_code == "onsite_process":
        if not start or not end:
            raise ReportDomainError(
                "APPENDIX_B_ONSITE_PERIOD_REQUIRED",
                "进场记录必须填写进场和离场日期。",
                status_code=422,
                project_uuid=project_uuid,
                field="starts_on",
            )
        if not members:
            raise ReportDomainError(
                "APPENDIX_B_ONSITE_MEMBER_REQUIRED",
                "进场记录必须选择至少一名现场测评人员。",
                status_code=422,
                project_uuid=project_uuid,
                field="member_uuids",
            )
    elif category_code in {"assessor_roster", "assessor_exam_proof"}:
        if len(members) != 1:
            raise ReportDomainError(
                "APPENDIX_B_PERSONNEL_CARDINALITY_INVALID",
                "人员资格记录必须且只能关联一名项目成员。",
                status_code=422,
                project_uuid=project_uuid,
                field="member_uuids",
            )
        member = next(iter(members.values()))
        if category_code == "assessor_roster":
            role = str(metadata.get("role") or "member")
            if role == "compiler" and (
                bool(member["is_project_leader"]) or str(member["team_role"]) not in {"member", "组员"}
            ):
                raise ReportDomainError(
                    "COMPILER_ROLE_INVALID",
                    "编制人必须为组员且不得担任项目负责人。",
                    status_code=422,
                    project_uuid=project_uuid,
                    field="metadata.role",
                )
            if role in {"compiler", "reviewer", "approver"} and not member["qualification_passed_at"]:
                raise ReportDomainError(
                    "APPENDIX_B_PERSONNEL_QUALIFICATION_REQUIRED",
                    "编制、审核和批准人员必须填写密评人员考试通过时间。",
                    status_code=422,
                    project_uuid=project_uuid,
                    field="member_uuids",
                )
        else:
            roster = db.execute(
                """
                SELECT 1 FROM report_evidence_usages u
                JOIN report_evidence_items i
                  ON i.project_id = u.project_id AND i.item_uuid = u.evidence_item_uuid
                WHERE u.project_id = ? AND i.category_code = 'assessor_roster'
                  AND u.usage_kind = 'personnel_role' AND u.related_member_uuid = ?
                """,
                (project_id, str(member["member_uuid"])),
            ).fetchone()
            if roster is None:
                raise ReportDomainError(
                    "APPENDIX_B_EXAM_MEMBER_NOT_IN_ROSTER",
                    "成绩证明只能关联表 B-7 已列出的人员。",
                    status_code=422,
                    project_uuid=project_uuid,
                    field="member_uuids",
                )
    elif payload.member_uuids and category_code not in {"travel_accommodation"}:
        raise ReportDomainError(
            "APPENDIX_B_MEMBER_NOT_ALLOWED",
            "当前类别不允许关联项目成员。",
            status_code=422,
            project_uuid=project_uuid,
            field="member_uuids",
        )

    if category_code == "authorization_notice":
        duplicate = db.execute(
            """
            SELECT 1 FROM report_evidence_items
            WHERE project_id = ? AND category_code = ? AND item_kind = 'record'
              AND subtype = ? AND (? IS NULL OR item_uuid <> ?)
            """,
            (project_id, category_code, payload.subtype, current_item_uuid, current_item_uuid),
        ).fetchone()
        if duplicate is not None:
            raise ReportDomainError(
                "APPENDIX_B_SUBTYPE_DUPLICATE",
                "授权书和风险告知书每种只允许一条记录。",
                status_code=409,
                project_uuid=project_uuid,
                field="subtype",
            )
    return {
        "metadata": metadata,
        "members": members,
        "related": related,
        "starts_on": _date_text(payload.starts_on),
        "ends_on": _date_text(payload.ends_on),
    }


def _replace_usages(
    db: sqlite3.Connection,
    project_id: int,
    category_code: str,
    item_uuid: str,
    payload: EvidenceItemWrite,
    metadata: dict[str, Any],
) -> None:
    db.execute(
        "DELETE FROM report_evidence_usages WHERE project_id = ? AND evidence_item_uuid = ?",
        (project_id, item_uuid),
    )
    timestamp = database.utc_now()
    usage_rows: list[tuple[str, str | None, str | None, str, int]] = []
    if category_code in {"travel_accommodation", "onsite_process"}:
        usage_rows.extend(
            ("member", member_uuid, None, "现场人员", index)
            for index, member_uuid in enumerate(payload.member_uuids)
        )
    if category_code == "travel_accommodation":
        usage_rows.extend(
            ("covered_onsite", None, related_uuid, "覆盖进场记录", index)
            for index, related_uuid in enumerate(payload.related_item_uuids)
        )
    if category_code == "assessor_roster":
        usage_rows.append(("personnel_role", payload.member_uuids[0], None, str(metadata.get("role") or "member"), 0))
    if category_code == "assessor_exam_proof":
        usage_rows.append(("exam_proof", payload.member_uuids[0], None, "成绩证明", 0))
    for usage_kind, member_uuid, related_uuid, slot_key, sort_order in usage_rows:
        db.execute(
            """
            INSERT INTO report_evidence_usages (
                usage_uuid, project_id, evidence_item_uuid, usage_kind,
                related_member_uuid, related_item_uuid, slot_key, sort_order, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), project_id, item_uuid, usage_kind,
                member_uuid, related_uuid, slot_key, sort_order, timestamp,
            ),
        )


def _member_uuids(db: sqlite3.Connection, project_id: int, item_uuid: str) -> list[str]:
    return [
        str(row["related_member_uuid"])
        for row in db.execute(
            """
            SELECT related_member_uuid FROM report_evidence_usages
            WHERE project_id = ? AND evidence_item_uuid = ?
              AND related_member_uuid IS NOT NULL
            ORDER BY sort_order, usage_uuid
            """,
            (project_id, item_uuid),
        ).fetchall()
    ]


def _covered_uuids(db: sqlite3.Connection, project_id: int, item_uuid: str) -> list[str]:
    return [
        str(row["related_item_uuid"])
        for row in db.execute(
            """
            SELECT related_item_uuid FROM report_evidence_usages
            WHERE project_id = ? AND evidence_item_uuid = ?
              AND usage_kind = 'covered_onsite'
            ORDER BY sort_order, usage_uuid
            """,
            (project_id, item_uuid),
        ).fetchall()
    ]


def _validate_stored_relationships(
    db: sqlite3.Connection, project_id: int, project_uuid: str
) -> None:
    onsite = {str(row["item_uuid"]): row for row in _record_rows(db, project_id, "onsite_process")}
    travel = _record_rows(db, project_id, "travel_accommodation")
    for row in travel:
        metadata = _loads(row["metadata_json"], {})
        covered = _covered_uuids(db, project_id, str(row["item_uuid"]))
        if onsite and not covered:
            raise ReportDomainError(
                "APPENDIX_B_TRAVEL_COVERAGE_REQUIRED",
                "存在进场记录时，每条差旅记录必须显式选择其覆盖的进场记录。",
                status_code=422,
                project_uuid=project_uuid,
                entity_uuid=str(row["item_uuid"]),
                field="related_item_uuids",
            )
        if metadata.get("is_local"):
            continue
        start = _parse_date(row["starts_on"], project_uuid=project_uuid, field="starts_on")
        end = _parse_date(row["ends_on"], project_uuid=project_uuid, field="ends_on")
        for related_uuid in covered:
            related = onsite.get(related_uuid)
            if related is None:
                raise ReportDomainError(
                    "APPENDIX_B_RELATED_ITEM_INVALID",
                    "差旅记录引用的进场记录不存在。",
                    status_code=404,
                    project_uuid=project_uuid,
                    entity_uuid=str(row["item_uuid"]),
                )
            related_start = _parse_date(related["starts_on"], project_uuid=project_uuid, field="starts_on")
            related_end = _parse_date(related["ends_on"], project_uuid=project_uuid, field="ends_on")
            if not start or not end or not related_start or not related_end or start > related_start or end < related_end:
                raise ReportDomainError(
                    "APPENDIX_B_TRAVEL_NOT_COVER_VISIT",
                    "差旅起止日期必须覆盖所关联的进离场记录。",
                    status_code=422,
                    project_uuid=project_uuid,
                    entity_uuid=str(row["item_uuid"]),
                    field="related_item_uuids",
                )

    plan_rows = _record_rows(db, project_id, "plan_review")
    onsite_starts = [
        parsed
        for row in onsite.values()
        if (parsed := _parse_date(row["starts_on"], project_uuid=project_uuid, field="starts_on"))
    ]
    if not onsite_starts:
        phase = db.execute("SELECT fieldwork_start FROM report_phase_dates WHERE project_id = ?", (project_id,)).fetchone()
        manual = _parse_date(phase["fieldwork_start"], project_uuid=project_uuid, field="fieldwork_start") if phase else None
        onsite_starts = [manual] if manual else []
    if plan_rows and plan_rows[0]["starts_on"] and onsite_starts:
        reviewed = _parse_date(plan_rows[0]["starts_on"], project_uuid=project_uuid, field="starts_on")
        if reviewed and reviewed >= min(onsite_starts):
            raise ReportDomainError(
                "APPENDIX_B_PLAN_REVIEW_DATE_INVALID",
                "方案评审时间必须早于现场测评开始日期。",
                status_code=422,
                project_uuid=project_uuid,
                entity_uuid=str(plan_rows[0]["item_uuid"]),
                field="starts_on",
            )

    report_rows = _record_rows(db, project_id, "report_review")
    if report_rows and report_rows[0]["starts_on"]:
        phase = db.execute(
            "SELECT analysis_end, approved_at FROM report_phase_dates WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        compiled = _parse_date(phase["analysis_end"], project_uuid=project_uuid, field="analysis_end") if phase else None
        reviewed = _parse_date(report_rows[0]["starts_on"], project_uuid=project_uuid, field="starts_on")
        approved = _parse_date(phase["approved_at"], project_uuid=project_uuid, field="approved_at") if phase else None
        if (compiled and reviewed and compiled > reviewed) or (reviewed and approved and reviewed > approved):
            raise ReportDomainError(
                "APPENDIX_B_REVIEW_DATE_ORDER_INVALID",
                "报告评审时间必须不早于编制日期，且不晚于批准日期。",
                status_code=422,
                project_uuid=project_uuid,
                entity_uuid=str(report_rows[0]["item_uuid"]),
                field="starts_on",
            )

    roles: dict[str, str] = {}
    persons: list[str] = []
    roster_members: set[str] = set()
    for row in _record_rows(db, project_id, "assessor_roster"):
        metadata = _loads(row["metadata_json"], {})
        role = str(metadata.get("role") or "member")
        member_ids = _member_uuids(db, project_id, str(row["item_uuid"]))
        if len(member_ids) != 1:
            raise ReportDomainError(
                "APPENDIX_B_PERSONNEL_CARDINALITY_INVALID",
                "表 B-7 每行必须关联一名项目成员。",
                status_code=422,
                project_uuid=project_uuid,
                entity_uuid=str(row["item_uuid"]),
            )
        member_uuid = member_ids[0]
        if member_uuid in roster_members:
            raise ReportDomainError(
                "APPENDIX_B_PERSONNEL_DUPLICATE",
                "表 B-7 同一成员只能出现一次。",
                status_code=409,
                project_uuid=project_uuid,
                entity_uuid=str(row["item_uuid"]),
            )
        roster_members.add(member_uuid)
        if role in {"compiler", "reviewer", "approver"}:
            if role in roles:
                raise ReportDomainError(
                    "APPENDIX_B_APPROVAL_ROLE_DUPLICATE",
                    "编制、审核和批准角色各自只能关联一人。",
                    status_code=422,
                    project_uuid=project_uuid,
                    field="metadata.role",
                )
            roles[role] = member_uuid
            persons.append(member_uuid)
    if len(persons) != len(set(persons)):
        raise ReportDomainError(
            "APPROVAL_ROLES_MUST_BE_DISTINCT",
            "编制人、审核人和批准人不得由同一人员兼任。",
            status_code=422,
            project_uuid=project_uuid,
            field="metadata.role",
        )

    for row in _record_rows(db, project_id, "assessor_exam_proof"):
        member_ids = _member_uuids(db, project_id, str(row["item_uuid"]))
        if len(member_ids) != 1 or member_ids[0] not in roster_members:
            raise ReportDomainError(
                "APPENDIX_B_EXAM_MEMBER_NOT_IN_ROSTER",
                "表 B-8 成绩证明必须关联表 B-7 中的具体人员。",
                status_code=422,
                project_uuid=project_uuid,
                entity_uuid=str(row["item_uuid"]),
            )


def _synchronize_authority(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    category_code: str,
) -> None:
    timestamp = database.utc_now()
    if category_code in {"travel_accommodation", "onsite_process", "plan_review", "report_review"}:
        phase = db.execute("SELECT * FROM report_phase_dates WHERE project_id = ?", (project_id,)).fetchone()
        if phase is None:
            raise RuntimeError("REPORT_PHASE_DATES_NOT_INITIALIZED")
        assignments: dict[str, Any] = {}
        if category_code == "travel_accommodation":
            records = _record_rows(db, project_id, category_code)
            travel = [
                {
                    "local_project": bool(_loads(row["metadata_json"], {}).get("is_local")),
                    "start_date": _date_text(row["starts_on"]),
                    "end_date": _date_text(row["ends_on"]),
                    "member_uuids": _member_uuids(db, project_id, str(row["item_uuid"])),
                    "evidence_item_uuid": str(row["item_uuid"]),
                    "covered_onsite_item_uuids": _covered_uuids(db, project_id, str(row["item_uuid"])),
                }
                for row in records
            ]
            assignments["travel_records_json"] = dump_json(travel)
            assignments["local_travel_not_applicable"] = int(bool(travel) and all(item["local_project"] for item in travel))
        elif category_code == "onsite_process":
            records = _record_rows(db, project_id, category_code)
            onsite = [
                {
                    "entry_date": _date_text(row["starts_on"]),
                    "exit_date": _date_text(row["ends_on"]),
                    "member_uuids": _member_uuids(db, project_id, str(row["item_uuid"])),
                    "evidence_item_uuid": str(row["item_uuid"]),
                }
                for row in records
            ]
            assignments["site_visit_records_json"] = dump_json(onsite)
            if onsite:
                assignments["fieldwork_start"] = min(str(item["entry_date"]) for item in onsite)
                assignments["fieldwork_end"] = max(str(item["exit_date"]) for item in onsite)
        elif category_code == "plan_review":
            records = _record_rows(db, project_id, category_code)
            assignments["scheme_review_at"] = records[0]["starts_on"] if records else None
        elif category_code == "report_review":
            records = _record_rows(db, project_id, category_code)
            assignments["report_review_at"] = records[0]["starts_on"] if records else None
        if assignments:
            sql = ", ".join(f"{column} = ?" for column in assignments)
            db.execute(
                f"UPDATE report_phase_dates SET {sql}, revision = revision + 1, updated_at = ? WHERE project_id = ?",
                (*assignments.values(), timestamp, project_id),
            )

    if category_code == "assessor_roster":
        role_values: dict[str, str | None] = {"compiler": None, "reviewer": None, "approver": None}
        for row in _record_rows(db, project_id, category_code):
            metadata = _loads(row["metadata_json"], {})
            role = str(metadata.get("role") or "member")
            if role in role_values:
                members = _member_uuids(db, project_id, str(row["item_uuid"]))
                role_values[role] = members[0] if members else None
        db.execute(
            """
            UPDATE report_metadata
            SET compiler_member_uuid = ?, reviewer_member_uuid = ?, approver_member_uuid = ?,
                revision = revision + 1, updated_at = ?
            WHERE project_id = ?
            """,
            (
                role_values["compiler"], role_values["reviewer"],
                role_values["approver"], timestamp, project_id,
            ),
        )
    _mark_project_changed(db, project_id, project_uuid)


def get_appendix_b(project_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        return _appendix_b_result(db, project, verify_files=True)


def _appendix_b_result(
    db: sqlite3.Connection, project: sqlite3.Row, *, verify_files: bool
) -> dict[str, Any]:
    project_id = int(project["id"])
    items = db.execute(
        """
        SELECT * FROM report_evidence_items WHERE project_id = ?
        ORDER BY category_code, item_kind DESC, sort_order, starts_on, item_uuid
        """,
        (project_id,),
    ).fetchall()
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in items:
        by_category[str(row["category_code"])].append(_item_result(db, row))
    issues = validate_appendix_b_in_connection(db, project, verify_files=verify_files)
    categories: list[dict[str, Any]] = []
    for contract in APPENDIX_B_CATEGORIES:
        category_code = str(contract["category_code"])
        row = _category_row(db, project_id, category_code)
        category_issues = [item for item in issues if item.get("category_code") == category_code]
        categories.append(
            {
                **dict(contract),
                "category_uuid": row["category_uuid"],
                "is_not_applicable": bool(row["is_not_applicable"]),
                "not_applicable_reason": row["not_applicable_reason"],
                "warning_acknowledged_at": row["warning_acknowledged_at"],
                "revision": int(row["revision"]),
                "items": by_category.get(category_code, []),
                "warnings": [item for item in category_issues if item["severity"] == "warning"],
                "errors": [item for item in category_issues if item["severity"] == "error"],
                "completion": "not_applicable" if row["is_not_applicable"] else ("complete" if by_category.get(category_code) else "empty"),
            }
        )
    members = [_member_client_result(row) for row in db.execute(
        "SELECT * FROM report_members WHERE project_id = ? AND active = 1 ORDER BY sort_order, member_uuid",
        (project_id,),
    ).fetchall()]
    organizations = [_organization_client_result(row) for row in db.execute(
        "SELECT * FROM report_organizations WHERE project_id = ? AND active = 1 ORDER BY sort_order, organization_uuid",
        (project_id,),
    ).fetchall()]
    return {
        "schema_version": "1.0",
        "project_uuid": str(project["project_uuid"]),
        "project_revision": _project_revision(db, project_id),
        "categories": categories,
        "members": members,
        "organizations": organizations,
        "warnings": [item for item in issues if item["severity"] == "warning"],
        "errors": [item for item in issues if item["severity"] == "error"],
        "completion": {
            "category_total": len(APPENDIX_B_CATEGORIES),
            "completed": sum(item["completion"] in {"complete", "not_applicable"} for item in categories),
            "warning_count": sum(item["severity"] == "warning" for item in issues),
            "error_count": sum(item["severity"] == "error" for item in issues),
        },
    }


def update_category(
    project_uuid: str, category_code: str, payload: EvidenceCategoryUpdate
) -> dict[str, Any]:
    _category_contract(category_code)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        _expect_project_revision(db, project_id, payload.expected_project_revision, project_uuid)
        row = _category_row(db, project_id, category_code)
        timestamp = database.utc_now()
        cursor = db.execute(
            """
            UPDATE report_evidence_categories
            SET is_not_applicable = ?, not_applicable_reason = ?,
                warning_acknowledged_at = ?, revision = revision + 1, updated_at = ?
            WHERE project_id = ? AND category_code = ? AND revision = ?
            """,
            (
                int(payload.is_not_applicable), payload.not_applicable_reason.strip(),
                timestamp if payload.acknowledge_warning else row["warning_acknowledged_at"],
                timestamp, project_id, category_code, payload.expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            current = _category_row(db, project_id, category_code)
            raise ReportDomainError(
                "REVISION_CONFLICT",
                "附录 B 类别已在其他页面更新，请刷新后重试。",
                status_code=409,
                project_uuid=project_uuid,
                field="expected_revision",
                details={"expected_revision": payload.expected_revision, "current_revision": int(current["revision"])},
            )
        _mark_project_changed(db, project_id, project_uuid)
        return _appendix_b_result(db, project, verify_files=False)


def create_item(
    project_uuid: str, category_code: str, payload: EvidenceItemWrite
) -> dict[str, Any]:
    _category_contract(category_code)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        _expect_project_revision(db, project_id, payload.expected_project_revision, project_uuid)
        _category_row(db, project_id, category_code)
        validated = _validate_record_payload(
            db, project_id, project_uuid, category_code, payload
        )
        item_uuid = str(uuid.uuid4())
        timestamp = database.utc_now()
        db.execute(
            """
            INSERT INTO report_evidence_items (
                item_uuid, project_id, category_code, item_kind, subtype, title,
                starts_on, ends_on, organization_uuid, location, sort_order,
                metadata_json, revision, created_at, updated_at
            ) VALUES (?, ?, ?, 'record', ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                item_uuid, project_id, category_code, payload.subtype,
                payload.title.strip(), validated["starts_on"], validated["ends_on"],
                payload.organization_uuid, payload.location.strip(), payload.sort_order,
                dump_json(validated["metadata"]), timestamp, timestamp,
            ),
        )
        _replace_usages(db, project_id, category_code, item_uuid, payload, validated["metadata"])
        _validate_stored_relationships(db, project_id, project_uuid)
        _synchronize_authority(db, project_id, project_uuid, category_code)
        row = db.execute("SELECT * FROM report_evidence_items WHERE item_uuid = ?", (item_uuid,)).fetchone()
        return _item_result(db, row)


def update_item(item_uuid: str, payload: EvidenceItemUpdate) -> dict[str, Any]:
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        project, row = _item_in_project(db, item_uuid, expected_kind="record")
        project_uuid = str(project["project_uuid"])
        project_id = int(project["id"])
        _expect_project_revision(db, project_id, payload.expected_project_revision, project_uuid)
        category_code = str(row["category_code"])
        validated = _validate_record_payload(
            db,
            project_id,
            project_uuid,
            category_code,
            payload,
            current_item_uuid=item_uuid,
        )
        cursor = db.execute(
            """
            UPDATE report_evidence_items
            SET subtype = ?, title = ?, starts_on = ?, ends_on = ?, organization_uuid = ?,
                location = ?, sort_order = ?, metadata_json = ?, revision = revision + 1,
                updated_at = ?
            WHERE project_id = ? AND item_uuid = ? AND revision = ? AND item_kind = 'record'
            """,
            (
                payload.subtype, payload.title.strip(), validated["starts_on"], validated["ends_on"],
                payload.organization_uuid, payload.location.strip(), payload.sort_order,
                dump_json(validated["metadata"]), database.utc_now(), project_id,
                item_uuid, payload.expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise ReportDomainError(
                "REVISION_CONFLICT",
                "附录 B 记录已在其他页面更新，请刷新后重试。",
                status_code=409,
                project_uuid=project_uuid,
                entity_uuid=item_uuid,
                field="expected_revision",
                details={"expected_revision": payload.expected_revision, "current_revision": int(row["revision"])},
            )
        _replace_usages(db, project_id, category_code, item_uuid, payload, validated["metadata"])
        _validate_stored_relationships(db, project_id, project_uuid)
        _synchronize_authority(db, project_id, project_uuid, category_code)
        updated = db.execute("SELECT * FROM report_evidence_items WHERE item_uuid = ?", (item_uuid,)).fetchone()
        return _item_result(db, updated)


def update_image(item_uuid: str, payload: EvidenceImageUpdate) -> dict[str, Any]:
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        project, row = _item_in_project(db, item_uuid, expected_kind="image")
        project_uuid = str(project["project_uuid"])
        project_id = int(project["id"])
        _expect_project_revision(db, project_id, payload.expected_project_revision, project_uuid)
        allowed = ALLOWED_IMAGE_SUBTYPES[str(row["category_code"])]
        if payload.subtype not in allowed:
            raise ReportDomainError(
                "APPENDIX_B_IMAGE_SUBTYPE_INVALID",
                "图片子类型不属于当前附录 B 类别。",
                status_code=422,
                project_uuid=project_uuid,
                field="subtype",
                details={"allowed": sorted(allowed)},
            )
        cursor = db.execute(
            """
            UPDATE report_evidence_items
            SET subtype = ?, caption = ?, alt_text = ?, sort_order = ?,
                revision = revision + 1, updated_at = ?
            WHERE project_id = ? AND item_uuid = ? AND revision = ? AND item_kind = 'image'
            """,
            (
                payload.subtype, payload.caption.strip(), payload.alt_text.strip(),
                payload.sort_order, database.utc_now(), project_id, item_uuid,
                payload.expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise ReportDomainError(
                "REVISION_CONFLICT",
                "证据图片已在其他页面更新，请刷新后重试。",
                status_code=409,
                project_uuid=project_uuid,
                entity_uuid=item_uuid,
                field="expected_revision",
            )
        _mark_project_changed(db, project_id, project_uuid)
        updated = db.execute("SELECT * FROM report_evidence_items WHERE item_uuid = ?", (item_uuid,)).fetchone()
        return _item_result(db, updated)


def delete_item(item_uuid: str, *, expected_project_revision: int, expected_revision: int) -> dict[str, Any]:
    file_paths: list[str] = []
    staged_files: list[ManagedFileTombstone] = []
    project_uuid = ""
    deleted: dict[str, Any]
    try:
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project, row = _item_in_project(db, item_uuid)
            project_uuid = str(project["project_uuid"])
            project_id = int(project["id"])
            _expect_project_revision(db, project_id, expected_project_revision, project_uuid)
            file_paths = [
                str(item["file_path"])
                for item in db.execute(
                    """
                    SELECT file_path FROM report_evidence_items
                    WHERE project_id = ? AND item_kind = 'image'
                      AND (item_uuid = ? OR parent_item_uuid = ?)
                    """,
                    (project_id, item_uuid, item_uuid),
                ).fetchall()
                if item["file_path"]
            ]
            for relative_path in file_paths:
                staged = stage_managed_file_removal(
                    project_uuid, relative_path, missing_ok=True
                )
                if staged is not None:
                    staged_files.append(staged)
            deleted = _item_result(db, row)
            cursor = db.execute(
                "DELETE FROM report_evidence_items WHERE project_id = ? AND item_uuid = ? AND revision = ?",
                (project_id, item_uuid, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ReportDomainError(
                    "REVISION_CONFLICT",
                    "附录 B 记录已在其他页面更新，请刷新后重试。",
                    status_code=409,
                    project_uuid=project_uuid,
                    entity_uuid=item_uuid,
                    field="expected_revision",
                )
            if row["item_kind"] == "record":
                _validate_stored_relationships(db, project_id, project_uuid)
                _synchronize_authority(db, project_id, project_uuid, str(row["category_code"]))
            else:
                _mark_project_changed(db, project_id, project_uuid)
    except Exception:
        for staged in reversed(staged_files):
            restore_managed_tombstone(staged)
        raise
    for staged in staged_files:
        try:
            finalize_managed_tombstone(staged)
        except OSError:
            # The active database state no longer references this hidden file;
            # the next read/export deterministically retries cleanup.
            pass
    return deleted


def upload_images(
    parent_item_uuid: str,
    *,
    expected_project_revision: int,
    subtype: str,
    caption: str,
    alt_text: str,
    files: list[UploadFile],
) -> list[dict[str, Any]]:
    if not files:
        raise ReportDomainError(
            "APPENDIX_B_IMAGE_REQUIRED",
            "请选择至少一张 PNG 或 JPEG 图片。",
            status_code=400,
            field="files",
        )
    saved: list[dict[str, Any]] = []
    project_uuid = ""
    try:
        with database.connect() as db:
            project, parent = _item_in_project(db, parent_item_uuid, expected_kind="record")
            project_uuid = str(project["project_uuid"])
            project_id = int(project["id"])
            _expect_project_revision(db, project_id, expected_project_revision, project_uuid)
            allowed = ALLOWED_IMAGE_SUBTYPES[str(parent["category_code"])]
            if subtype not in allowed:
                raise ReportDomainError(
                    "APPENDIX_B_IMAGE_SUBTYPE_INVALID",
                    "图片子类型不属于当前附录 B 类别。",
                    status_code=422,
                    project_uuid=project_uuid,
                    field="subtype",
                    details={"allowed": sorted(allowed)},
                )
        for upload in files:
            saved.append(save_upload(project_uuid, upload))
        created_uuids: list[str] = []
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project, parent = _item_in_project(db, parent_item_uuid, expected_kind="record")
            project_id = int(project["id"])
            _expect_project_revision(db, project_id, expected_project_revision, project_uuid)
            start_order = int(
                db.execute(
                    """
                    SELECT COALESCE(MAX(sort_order), -1) + 1 FROM report_evidence_items
                    WHERE project_id = ? AND parent_item_uuid = ? AND item_kind = 'image'
                    """,
                    (project_id, parent_item_uuid),
                ).fetchone()[0]
            )
            timestamp = database.utc_now()
            for offset, file_data in enumerate(saved):
                item_uuid = str(uuid.uuid4())
                db.execute(
                    """
                    INSERT INTO report_evidence_items (
                        item_uuid, project_id, category_code, parent_item_uuid, item_kind,
                        subtype, sort_order, metadata_json, file_path, original_name,
                        mime_type, caption, alt_text, pixel_width, pixel_height, dpi_x,
                        dpi_y, display_width_in, display_height_in, sha256, revision,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'image', ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        item_uuid, project_id, parent["category_code"], parent_item_uuid,
                        subtype, start_order + offset, file_data["file_path"],
                        file_data["original_name"], file_data["mime_type"], caption.strip(),
                        alt_text.strip(), file_data["pixel_width"], file_data["pixel_height"],
                        file_data["dpi_x"], file_data["dpi_y"], file_data["display_width_in"],
                        file_data["display_height_in"], file_data["sha256"], timestamp, timestamp,
                    ),
                )
                db.execute(
                    """
                    INSERT INTO report_evidence_usages (
                        usage_uuid, project_id, evidence_item_uuid, usage_kind,
                        related_item_uuid, slot_key, sort_order, created_at
                    ) VALUES (?, ?, ?, 'image_slot', ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()), project_id, item_uuid, parent_item_uuid,
                        subtype, start_order + offset, timestamp,
                    ),
                )
                created_uuids.append(item_uuid)
            _mark_project_changed(db, project_id, project_uuid)
            placeholders = ",".join("?" for _ in created_uuids)
            rows = db.execute(
                f"SELECT * FROM report_evidence_items WHERE item_uuid IN ({placeholders}) ORDER BY sort_order",
                tuple(created_uuids),
            ).fetchall()
            return [_item_result(db, row) for row in rows]
    except Exception:
        for item in saved:
            try:
                discard_managed_file(project_uuid, str(item["file_path"]))
            except Exception:
                pass
        raise


def replace_image_file(
    item_uuid: str,
    *,
    expected_project_revision: int,
    expected_revision: int,
    file: UploadFile,
) -> dict[str, Any]:
    with database.connect() as db:
        project, row = _item_in_project(db, item_uuid, expected_kind="image")
        project_uuid = str(project["project_uuid"])
        project_id = int(project["id"])
        _expect_project_revision(db, project_id, expected_project_revision, project_uuid)
    saved = save_upload(project_uuid, file)
    staged_old: ManagedFileTombstone | None = None
    try:
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project, row = _item_in_project(db, item_uuid, expected_kind="image")
            project_id = int(project["id"])
            _expect_project_revision(db, project_id, expected_project_revision, project_uuid)
            staged_old = stage_managed_file_removal(
                project_uuid, str(row["file_path"])
            )
            cursor = db.execute(
                """
                UPDATE report_evidence_items
                SET file_path = ?, original_name = ?, mime_type = ?, pixel_width = ?,
                    pixel_height = ?, dpi_x = ?, dpi_y = ?, display_width_in = ?,
                    display_height_in = ?, sha256 = ?, revision = revision + 1, updated_at = ?
                WHERE project_id = ? AND item_uuid = ? AND revision = ? AND item_kind = 'image'
                """,
                (
                    saved["file_path"], saved["original_name"], saved["mime_type"],
                    saved["pixel_width"], saved["pixel_height"], saved["dpi_x"], saved["dpi_y"],
                    saved["display_width_in"], saved["display_height_in"], saved["sha256"],
                    database.utc_now(), project_id, item_uuid, expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ReportDomainError(
                    "REVISION_CONFLICT",
                    "证据图片已在其他页面更新，请刷新后重试。",
                    status_code=409,
                    project_uuid=project_uuid,
                    entity_uuid=item_uuid,
                    field="expected_revision",
                )
            _mark_project_changed(db, project_id, project_uuid)
            updated = db.execute("SELECT * FROM report_evidence_items WHERE item_uuid = ?", (item_uuid,)).fetchone()
            result = _item_result(db, updated)
    except Exception:
        restore_managed_tombstone(staged_old)
        discard_managed_file(project_uuid, str(saved["file_path"]))
        raise
    try:
        finalize_managed_tombstone(staged_old)
    except OSError:
        # The new file is authoritative; deferred reconciliation removes the
        # hidden old copy without reporting a false replacement failure.
        pass
    return result


def reorder_items(
    project_uuid: str, category_code: str, payload: EvidenceReorderWrite
) -> list[dict[str, Any]]:
    _category_contract(category_code)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        _expect_project_revision(db, project_id, payload.expected_project_revision, project_uuid)
        current = _record_rows(db, project_id, category_code)
        current_ids = {str(row["item_uuid"]) for row in current}
        if set(payload.item_uuids) != current_ids:
            raise ReportDomainError(
                "APPENDIX_B_REORDER_SET_INVALID",
                "排序列表必须完整且只能包含当前类别的结构化记录。",
                status_code=422,
                project_uuid=project_uuid,
                field="item_uuids",
            )
        timestamp = database.utc_now()
        for index, item_uuid in enumerate(payload.item_uuids):
            db.execute(
                "UPDATE report_evidence_items SET sort_order = ?, revision = revision + 1, updated_at = ? WHERE project_id = ? AND item_uuid = ?",
                (index, timestamp, project_id, item_uuid),
            )
        _mark_project_changed(db, project_id, project_uuid)
        return [_item_result(db, row) for row in _record_rows(db, project_id, category_code)]


def validate_appendix_b(project_uuid: str, *, expected_project_revision: int) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        revision = _expect_project_revision(db, project_id, expected_project_revision, project_uuid)
        issues = validate_appendix_b_in_connection(db, project, verify_files=True)
        return {
            "project_uuid": project_uuid,
            "project_revision": revision,
            "valid": not any(item["severity"] == "error" for item in issues),
            "errors": [item for item in issues if item["severity"] == "error"],
            "warnings": [item for item in issues if item["severity"] == "warning"],
            "issues": issues,
        }


def validate_appendix_b_in_connection(
    db: sqlite3.Connection,
    project: sqlite3.Row,
    *,
    verify_files: bool,
) -> list[dict[str, Any]]:
    project_id = int(project["id"])
    project_uuid = str(project["project_uuid"])
    issues: list[dict[str, Any]] = []
    if verify_files:
        referenced_paths = {
            str(row["file_path"])
            for row in db.execute(
                """
                SELECT file_path FROM report_evidence_items
                WHERE project_id = ? AND item_kind = 'image' AND file_path IS NOT NULL
                """,
                (project_id,),
            ).fetchall()
        }
        try:
            reconcile_managed_tombstones(project_uuid, referenced_paths)
        except ReportDomainError as exc:
            issues.append(
                _issue("error", exc.code, exc.message, field=exc.field)
            )
    profile = db.execute("SELECT * FROM system_profiles WHERE project_id = ?", (project_id,)).fetchone()
    for category_code in APPENDIX_B_CATEGORY_CODES:
        category = _category_row(db, project_id, category_code)
        records = _record_rows(db, project_id, category_code)
        images = db.execute(
            """
            SELECT * FROM report_evidence_items
            WHERE project_id = ? AND category_code = ? AND item_kind = 'image'
            ORDER BY sort_order, item_uuid
            """,
            (project_id, category_code),
        ).fetchall()
        if not records and not category["is_not_applicable"]:
            issues.append(_issue(
                "warning", "APPENDIX_B_CATEGORY_EMPTY", "该附录 B 类别尚未填写证明材料。",
                category_code=category_code,
            ))
        if category["is_not_applicable"] and records:
            issues.append(_issue(
                "warning", "APPENDIX_B_NOT_APPLICABLE_DATA_PRESENT",
                "该类别已标记不适用，但仍保留已填写数据；导出会保留这些数据。",
                category_code=category_code,
            ))
        if category_code in _IMAGE_EXPECTED:
            image_parent_ids = {str(row["parent_item_uuid"]) for row in images}
            for record in records:
                if str(record["item_uuid"]) not in image_parent_ids:
                    issues.append(_issue(
                        "warning", "APPENDIX_B_IMAGE_MISSING", "该记录尚未上传证明图片。",
                        category_code=category_code, item_uuid=str(record["item_uuid"]), field="images",
                    ))
        for image in images:
            item = dict(image)
            if not str(image["caption"] or "").strip():
                issues.append(_issue(
                    "warning", "APPENDIX_B_CAPTION_MISSING", "证据图片尚未填写题注。",
                    category_code=category_code, item_uuid=str(image["item_uuid"]), field="caption",
                ))
            if verify_files:
                try:
                    verify_stored_image(project_uuid, item)
                except ReportDomainError as exc:
                    issues.append(_issue(
                        "error", exc.code, exc.message,
                        category_code=category_code, item_uuid=str(image["item_uuid"]), field=exc.field,
                    ))
        duplicate_hashes = {
            digest for digest, count in Counter(str(row["sha256"]) for row in images).items()
            if digest and count > 1
        }
        for digest in sorted(duplicate_hashes):
            issues.append(_issue(
                "warning", "APPENDIX_B_DUPLICATE_IMAGE", "当前类别存在内容相同的证据图片。",
                category_code=category_code, details={"sha256": digest},
            ))
        if category_code == "travel_accommodation":
            for record in records:
                metadata = _loads(record["metadata_json"], {})
                child_count = sum(str(image["parent_item_uuid"]) == str(record["item_uuid"]) for image in images)
                if metadata.get("is_local") and child_count:
                    issues.append(_issue(
                        "warning", "APPENDIX_B_LOCAL_TRAVEL_OPTIONAL",
                        "本地项目仍保留了差旅票证，确认后将按原数据导出。",
                        category_code=category_code, item_uuid=str(record["item_uuid"]),
                    ))
        if category_code == "grading_filing" and records and profile and profile["level_filing_status"] != "filed":
            issues.append(_issue(
                "warning", "APPENDIX_B_UNFILED_DATA_PRESENT",
                "基本信息标记为未定级备案，但表 B-9 已填写数据；确认后将保留导出。",
                category_code=category_code,
            ))
    try:
        _validate_stored_relationships(db, project_id, project_uuid)
    except ReportDomainError as exc:
        issues.append(_issue(
            "error", exc.code, exc.message,
            category_code=_category_from_item(db, project_id, exc.entity_uuid),
            item_uuid=exc.entity_uuid, field=exc.field, details=exc.details,
        ))
    return issues


def _category_from_item(
    db: sqlite3.Connection, project_id: int, item_uuid: str | None
) -> str | None:
    if not item_uuid:
        return None
    row = db.execute(
        "SELECT category_code FROM report_evidence_items WHERE project_id = ? AND item_uuid = ?",
        (project_id, item_uuid),
    ).fetchone()
    return str(row["category_code"]) if row else None


def _organization_name(
    organizations: dict[str, dict[str, Any]], organization_uuid: str | None
) -> str:
    return str(organizations.get(str(organization_uuid or ""), {}).get("name") or "")


def _projection_item(
    db: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    members: dict[str, dict[str, Any]],
    organizations: dict[str, dict[str, Any]],
    images: list[dict[str, Any]],
) -> dict[str, Any]:
    item_uuid = str(row["item_uuid"])
    member_uuids = _member_uuids(db, int(row["project_id"]), item_uuid)
    return {
        "item_uuid": item_uuid,
        "subtype": str(row["subtype"]),
        "title": str(row["title"] or ""),
        "starts_on": _date_text(row["starts_on"]),
        "ends_on": _date_text(row["ends_on"]),
        "organization_uuid": row["organization_uuid"],
        "organization_name": _organization_name(organizations, row["organization_uuid"]),
        "location": str(row["location"] or ""),
        "sort_order": int(row["sort_order"]),
        "metadata": _loads(row["metadata_json"], {}),
        "member_uuids": member_uuids,
        "members": [members[value] for value in member_uuids if value in members],
        "related_item_uuids": _covered_uuids(db, int(row["project_id"]), item_uuid),
        "images": [item for item in images if item["parent_item_uuid"] == item_uuid],
    }


def build_projection(project_uuid: str, *, expected_project_revision: int) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        revision = _expect_project_revision(db, project_id, expected_project_revision, project_uuid)
        issues = validate_appendix_b_in_connection(db, project, verify_files=True)
        member_rows = [dict(row) for row in db.execute(
            "SELECT * FROM report_members WHERE project_id = ? AND active = 1 ORDER BY sort_order, member_uuid",
            (project_id,),
        ).fetchall()]
        organization_rows = [dict(row) for row in db.execute(
            "SELECT * FROM report_organizations WHERE project_id = ? AND active = 1 ORDER BY sort_order, organization_uuid",
            (project_id,),
        ).fetchall()]
        members = {str(row["member_uuid"]): row for row in member_rows}
        organizations = {str(row["organization_uuid"]): row for row in organization_rows}
        assessed = next((row for row in organization_rows if row["organization_type"] == "assessed"), {})
        client = next((row for row in organization_rows if row["organization_type"] == "client"), {})
        effective_client = str(client.get("name") or assessed.get("name") or "")
        profile = dict(db.execute("SELECT * FROM system_profiles WHERE project_id = ?", (project_id,)).fetchone())
        image_rows = db.execute(
            """
            SELECT * FROM report_evidence_items
            WHERE project_id = ? AND item_kind = 'image'
            ORDER BY category_code, sort_order, item_uuid
            """,
            (project_id,),
        ).fetchall()
        evidence_images: list[dict[str, Any]] = []
        used_synthetic_ids: set[int] = set()
        for row in image_rows:
            item_uuid = str(row["item_uuid"])
            synthetic_id = int(uuid.UUID(item_uuid).hex[:12], 16)
            if synthetic_id in used_synthetic_ids:
                raise ReportDomainError(
                    "APPENDIX_B_IMAGE_ID_COLLISION",
                    "证据图片稳定标识发生碰撞。",
                    status_code=500,
                    entity_uuid=item_uuid,
                )
            used_synthetic_ids.add(synthetic_id)
            evidence_images.append(
                {
                    "id": synthetic_id,
                    "item_uuid": item_uuid,
                    "category_code": str(row["category_code"]),
                    "parent_item_uuid": str(row["parent_item_uuid"]),
                    "subtype": str(row["subtype"]),
                    "file_path": str(row["file_path"]),
                    "original_name": str(row["original_name"]),
                    "mime_type": str(row["mime_type"]),
                    "caption": str(row["caption"] or ""),
                    "alt_text": str(row["alt_text"] or ""),
                    "pixel_width": int(row["pixel_width"]),
                    "pixel_height": int(row["pixel_height"]),
                    "dpi_x": row["dpi_x"],
                    "dpi_y": row["dpi_y"],
                    "display_width_in": row["display_width_in"],
                    "display_height_in": row["display_height_in"],
                    "sha256": str(row["sha256"]),
                    "sort_order": int(row["sort_order"]),
                }
            )
        tables: dict[str, dict[str, Any]] = {}
        field_source_ids: dict[str, list[str]] = {}
        for contract in APPENDIX_B_CATEGORIES:
            category_code = str(contract["category_code"])
            category_row = _category_row(db, project_id, category_code)
            records = _record_rows(db, project_id, category_code)
            projected = [
                _projection_item(
                    db,
                    row,
                    members=members,
                    organizations=organizations,
                    images=evidence_images,
                )
                for row in records
            ]
            tables[str(contract["code"])] = {
                "category_code": category_code,
                "title": str(contract["title"]),
                "is_not_applicable": bool(category_row["is_not_applicable"]),
                "not_applicable_reason": str(category_row["not_applicable_reason"] or ""),
                "records": projected,
            }
            field_source_ids[category_code] = [item["item_uuid"] for item in projected]
        tables["B-1"]["effective_client_name"] = effective_client
        filing_records = tables["B-9"]["records"]
        if filing_records:
            metadata = filing_records[0]["metadata"]
            same = metadata.get("filing_system_same")
            filing_records[0]["effective_filing_system_name"] = (
                str(profile.get("system_name") or "") if same is True
                else str(metadata.get("filing_system_name") or "")
            )
        personnel_rows = tables["B-7"]["records"]
        projection = {
            "schema_version": "1.0",
            "status": "invalid" if any(item["severity"] == "error" for item in issues) else "current",
            "project_uuid": project_uuid,
            "project_revision": revision,
            "tables": tables,
            "personnel_rows": personnel_rows,
            "evidence_images": evidence_images,
            "field_source_ids": field_source_ids,
            "warnings": [item for item in issues if item["severity"] == "warning"],
            "errors": [item for item in issues if item["severity"] == "error"],
        }
        projection["projection_hash"] = hashlib.sha256(
            canonical_json(projection).encode("utf-8")
        ).hexdigest()
        return projection


def evidence_file_path(project_id: int, item_uuid: str) -> Path:
    with database.connect() as db:
        row = db.execute(
            """
            SELECT i.*, p.project_uuid FROM report_evidence_items i
            JOIN projects p ON p.id = i.project_id
            WHERE i.project_id = ? AND i.item_uuid = ? AND i.item_kind = 'image'
            """,
            (project_id, item_uuid),
        ).fetchone()
        if row is None:
            raise ReportDomainError(
                "APPENDIX_B_IMAGE_NOT_FOUND",
                "证据图片不存在或不属于当前项目。",
                status_code=404,
                entity_uuid=item_uuid,
            )
        verify_stored_image(str(row["project_uuid"]), dict(row))
        return resolve_managed_path(str(row["project_uuid"]), str(row["file_path"]))
