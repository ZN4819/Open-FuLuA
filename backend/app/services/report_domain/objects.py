from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from ... import database
from ...report_schemas import (
    AppendixTransmissionRelationWrite,
    AssessmentObjectUpdate,
    AssessmentObjectWrite,
    BindingConfirmWrite,
    CorrectionRelationUpdate,
    CorrectionRelationWrite,
    ObjectRelationUpdate,
    ObjectRelationWrite,
    ObjectMergeWrite,
    ObjectSubsystemWrite,
)
from .common import (
    dump_json,
    ensure_uuid_in_project,
    new_uuid,
    require_cas_updated,
    require_report_project,
    row_dict,
    rows_dict,
    safe_json_size,
    touch_project,
)
from .errors import ReportDomainError


SECTION_OBJECT_TYPES = {
    "A-1": "physical",
    "A-2": "network",
    "A-3": "device",
    "A-4": "application",
    "A-5": "management",
    "A-6": "management",
    "A-7": "management",
    "A-8": "management",
}


def _object_result(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    result = row_dict(row) or {}
    subsystem = db.execute(
        "SELECT subsystem_name, assessment_methods_json, remark, revision AS subsystem_revision "
        "FROM assessment_object_subsystems WHERE project_id=? AND object_uuid=?",
        (row["project_id"], row["object_uuid"]),
    ).fetchone()
    if subsystem:
        result["subsystem_name"] = subsystem["subsystem_name"]
        result["methods"] = json.loads(subsystem["assessment_methods_json"])
        result["remark"] = subsystem["remark"]
        result["subsystem_revision"] = subsystem["subsystem_revision"]
    else:
        result.update({"subsystem_name": None, "methods": [], "remark": "", "subsystem_revision": None})
    result["reference_count"] = int(
        db.execute(
            "SELECT COUNT(*) FROM object_relations WHERE project_id=? AND (source_object_uuid=? OR target_object_uuid=?)",
            (row["project_id"], row["object_uuid"], row["object_uuid"]),
        ).fetchone()[0]
    )
    return result


def list_objects(project_uuid: str) -> list[dict[str, Any]]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        rows = db.execute(
            "SELECT * FROM assessment_objects WHERE project_id=? ORDER BY active DESC, name_snapshot, id",
            (project["id"],),
        ).fetchall()
        return [_object_result(db, row) for row in rows]


def duplicate_candidates(project_uuid: str) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in list_objects(project_uuid):
        groups[(item["object_type"], item["name_snapshot"].strip().casefold())].append(item)
    return [
        {"object_type": key[0], "normalized_name": key[1], "objects": values, "requires_confirmation": True}
        for key, values in sorted(groups.items())
        if key[1] and len(values) > 1
    ]


def merge_object(project_uuid: str, source_uuid: str, payload: ObjectMergeWrite) -> dict[str, Any]:
    if source_uuid == payload.target_object_uuid:
        raise ReportDomainError("OBJECT_MERGE_SELF", "测评对象不能与自身合并。", status_code=422, project_uuid=project_uuid)
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        source = ensure_uuid_in_project(db,"assessment_objects","object_uuid",source_uuid,project_id,project_uuid=project_uuid,entity_type="assessment_object")
        target = ensure_uuid_in_project(db,"assessment_objects","object_uuid",payload.target_object_uuid,project_id,project_uuid=project_uuid,entity_type="assessment_object")
        source_lock = db.execute(
            "UPDATE assessment_objects SET revision=revision WHERE project_id=? AND object_uuid=? AND revision=?",
            (project_id,source_uuid,payload.source_expected_revision),
        )
        require_cas_updated(db,source_lock,table="assessment_objects",project_id=project_id,expected_revision=payload.source_expected_revision,project_uuid=project_uuid,entity_type="assessment_object",entity_uuid=source_uuid)
        target_lock = db.execute(
            "UPDATE assessment_objects SET revision=revision WHERE project_id=? AND object_uuid=? AND revision=?",
            (project_id,payload.target_object_uuid,payload.target_expected_revision),
        )
        require_cas_updated(db,target_lock,table="assessment_objects",project_id=project_id,expected_revision=payload.target_expected_revision,project_uuid=project_uuid,entity_type="assessment_object",entity_uuid=payload.target_object_uuid)
        if source["object_type"] != target["object_type"]:
            raise ReportDomainError(
                "OBJECT_MERGE_TYPE_CONFLICT",
                "不同类型的测评对象不能合并。",
                status_code=409,
                project_uuid=project_uuid,
                details={"source_type": source["object_type"], "target_type": target["object_type"]},
            )
        if source["source_section_code"] and target["source_section_code"] and source["source_section_code"] != target["source_section_code"]:
            raise ReportDomainError("OBJECT_MERGE_SOURCE_CONFLICT", "来自不同附录 A 章节的对象不能合并。", status_code=409, project_uuid=project_uuid, details={"source_sections":[source["source_section_code"],target["source_section_code"]]})
        source_subsystem=db.execute("SELECT * FROM assessment_object_subsystems WHERE project_id=? AND object_uuid=?",(project_id,source_uuid)).fetchone(); target_subsystem=db.execute("SELECT * FROM assessment_object_subsystems WHERE project_id=? AND object_uuid=?",(project_id,payload.target_object_uuid)).fetchone()
        if source_subsystem and target_subsystem and source_subsystem["subsystem_name"]!=target_subsystem["subsystem_name"]:
            raise ReportDomainError("OBJECT_MERGE_SUBSYSTEM_CONFLICT","两个 A-4 对象的子系统不同，不能自动合并。",status_code=409,project_uuid=project_uuid)
        if _contains_cycle_after_merge(db,project_id,source_uuid,payload.target_object_uuid):
            raise ReportDomainError(
                "OBJECT_RELATION_CYCLE",
                "对象合并后会使包含关系形成循环。",
                status_code=422,
                project_uuid=project_uuid,
                field="target_object_uuid",
                details={"source_object_uuid":source_uuid,"target_object_uuid":payload.target_object_uuid},
            )
        # 唯一关系可能在替换端点后冲突，先拒绝而不是猜测去重。
        relation_rows = db.execute("SELECT * FROM object_relations WHERE project_id=? AND (source_object_uuid=? OR target_object_uuid=?)",(project_id,source_uuid,source_uuid)).fetchall()
        for row in relation_rows:
            new_source=payload.target_object_uuid if row["source_object_uuid"]==source_uuid else row["source_object_uuid"]; new_target=payload.target_object_uuid if row["target_object_uuid"]==source_uuid else row["target_object_uuid"]
            if new_source==new_target or db.execute("SELECT 1 FROM object_relations WHERE project_id=? AND source_object_uuid=? AND target_object_uuid=? AND relation_type=? AND relation_uuid<>?",(project_id,new_source,new_target,row["relation_type"],row["relation_uuid"])).fetchone():
                raise ReportDomainError("OBJECT_MERGE_RELATION_CONFLICT","对象关系在合并后会产生自环或重复，请先处理关系。",status_code=409,project_uuid=project_uuid)
        correction_rows = db.execute("SELECT * FROM result_correction_relations WHERE project_id=? AND (a2_object_uuid=? OR a4_object_uuid=?)",(project_id,source_uuid,source_uuid)).fetchall()
        for row in correction_rows:
            if row["a4_object_uuid"] != source_uuid:
                continue
            if db.execute("SELECT 1 FROM result_correction_relations WHERE project_id=? AND a4_object_uuid=? AND correction_kind=? AND correction_uuid<>?",(project_id,payload.target_object_uuid,row["correction_kind"],row["correction_uuid"])).fetchone():
                raise ReportDomainError("OBJECT_MERGE_CORRECTION_CONFLICT","对象修正关系在合并后会违反每类指标的单通道约束，请先处理修正关系。",status_code=409,project_uuid=project_uuid)
        if source_subsystem and not target_subsystem:
            cursor=db.execute("UPDATE assessment_object_subsystems SET object_uuid=?,revision=revision+1,updated_at=? WHERE project_id=? AND binding_uuid=? AND revision=?",(payload.target_object_uuid,database.utc_now(),project_id,source_subsystem["binding_uuid"],source_subsystem["revision"]))
            require_cas_updated(db,cursor,table="assessment_object_subsystems",project_id=project_id,expected_revision=int(source_subsystem["revision"]),project_uuid=project_uuid,entity_type="assessment_object_subsystem",entity_uuid=source_subsystem["binding_uuid"])
        for relation in relation_rows:
            cursor=db.execute("UPDATE object_relations SET source_object_uuid=CASE WHEN source_object_uuid=? THEN ? ELSE source_object_uuid END,target_object_uuid=CASE WHEN target_object_uuid=? THEN ? ELSE target_object_uuid END,revision=revision+1,updated_at=? WHERE project_id=? AND relation_uuid=? AND revision=?",(source_uuid,payload.target_object_uuid,source_uuid,payload.target_object_uuid,database.utc_now(),project_id,relation["relation_uuid"],relation["revision"]))
            require_cas_updated(db,cursor,table="object_relations",project_id=project_id,expected_revision=int(relation["revision"]),project_uuid=project_uuid,entity_type="object_relation",entity_uuid=relation["relation_uuid"])
        for correction in correction_rows:
            cursor=db.execute("UPDATE result_correction_relations SET a2_object_uuid=CASE WHEN a2_object_uuid=? THEN ? ELSE a2_object_uuid END,a4_object_uuid=CASE WHEN a4_object_uuid=? THEN ? ELSE a4_object_uuid END,revision=revision+1,updated_at=? WHERE project_id=? AND correction_uuid=? AND revision=?",(source_uuid,payload.target_object_uuid,source_uuid,payload.target_object_uuid,database.utc_now(),project_id,correction["correction_uuid"],correction["revision"]))
            require_cas_updated(db,cursor,table="result_correction_relations",project_id=project_id,expected_revision=int(correction["revision"]),project_uuid=project_uuid,entity_type="result_correction_relation",entity_uuid=correction["correction_uuid"])
        db.execute("UPDATE assessment_rows SET assessment_object_uuid=? WHERE assessment_object_uuid=?",(payload.target_object_uuid,source_uuid))
        aliases=json.loads(target["properties_json"] or "{}"); alias_values=aliases.get("aliases",[]); alias_values.extend([source["name_snapshot"],source_uuid]); aliases["aliases"]=list(dict.fromkeys(value for value in alias_values if value))
        source_row_id=target["source_row_id"] or source["source_row_id"]
        source_section=target["source_section_code"] or source["source_section_code"]
        deleted=db.execute("DELETE FROM assessment_objects WHERE project_id=? AND object_uuid=? AND revision=?",(project_id,source_uuid,payload.source_expected_revision))
        require_cas_updated(db,deleted,table="assessment_objects",project_id=project_id,expected_revision=payload.source_expected_revision,project_uuid=project_uuid,entity_type="assessment_object",entity_uuid=source_uuid)
        cursor=db.execute("UPDATE assessment_objects SET source_row_id=?,source_section_code=?,properties_json=?,revision=revision+1,updated_at=? WHERE project_id=? AND object_uuid=? AND revision=?",(source_row_id,source_section,dump_json(aliases),database.utc_now(),project_id,payload.target_object_uuid,payload.target_expected_revision))
        require_cas_updated(db,cursor,table="assessment_objects",project_id=project_id,expected_revision=payload.target_expected_revision,project_uuid=project_uuid,entity_type="assessment_object",entity_uuid=payload.target_object_uuid)
        touch_project(db,project_id)
        return _object_result(db,db.execute("SELECT * FROM assessment_objects WHERE object_uuid=?",(payload.target_object_uuid,)).fetchone())


def create_object(project_uuid: str, payload: AssessmentObjectWrite) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        _validate_section_type(payload.object_type, payload.source_section_code, project_uuid)
        source_section_code = payload.source_section_code
        name_snapshot = payload.name_snapshot.strip()
        if payload.source_row_id is not None:
            source = _assessment_row(db, int(project["id"]), payload.source_row_id, project_uuid)
            source_section_code = _validate_object_source(payload.object_type, payload.source_section_code, source, project_uuid)
            _ensure_row_binding_available(source, None, project_uuid)
            name_snapshot = str(source["object_name"]).strip()
        object_uuid = new_uuid()
        timestamp = database.utc_now()
        properties = safe_json_size(payload.properties, maximum=32_768, project_uuid=project_uuid, field="properties")
        try:
            db.execute(
                """
                INSERT INTO assessment_objects (
                    object_uuid,project_id,object_type,name_snapshot,source_section_code,source_row_id,
                    properties_json,active,revision,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,1,?,?)
                """,
                (object_uuid, project["id"], payload.object_type, name_snapshot, source_section_code, payload.source_row_id, properties, int(payload.active), timestamp, timestamp),
            )
        except sqlite3.IntegrityError as exc:
            raise ReportDomainError("ASSESSMENT_OBJECT_SOURCE_DUPLICATE", "同一附录 A 行只能绑定一个测评对象。", status_code=409, project_uuid=project_uuid, field="source_row_id") from exc
        if payload.source_row_id is not None:
            db.execute("UPDATE assessment_rows SET assessment_object_uuid=? WHERE id=?", (object_uuid, payload.source_row_id))
        touch_project(db, int(project["id"]))
        row = db.execute("SELECT * FROM assessment_objects WHERE object_uuid=?", (object_uuid,)).fetchone()
        return _object_result(db, row)


def update_object(project_uuid: str, object_uuid: str, payload: AssessmentObjectUpdate) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        row = ensure_uuid_in_project(db, "assessment_objects", "object_uuid", object_uuid, int(project["id"]), project_uuid=project_uuid, entity_type="assessment_object")
        _validate_section_type(payload.object_type, payload.source_section_code, project_uuid)
        bound_sections = {
            str(item["section_code"])
            for item in db.execute(
                """
                SELECT DISTINCT s.code AS section_code
                FROM assessment_rows r
                JOIN appendix_sections s ON s.id=r.section_id
                WHERE s.project_id=? AND r.assessment_object_uuid=?
                """,
                (project["id"], object_uuid),
            ).fetchall()
        }
        for section_code in bound_sections:
            _validate_section_type(payload.object_type, section_code, project_uuid)
        if bound_sections and (
            not payload.active
            or payload.object_type != row["object_type"]
            or payload.name_snapshot.strip() != str(row["name_snapshot"] or "").strip()
            or payload.source_section_code != row["source_section_code"]
            or payload.source_row_id != row["source_row_id"]
        ):
            raise ReportDomainError(
                "APPENDIX_OBJECT_BACKEND_MANAGED",
                "附录 A 已绑定对象的身份、来源和启用状态由后台维护，请在附录 A 中修改对象。",
                status_code=422,
                project_uuid=project_uuid,
                entity_type="assessment_object",
                entity_uuid=object_uuid,
            )
        if payload.source_section_code and bound_sections and payload.source_section_code not in bound_sections:
            raise ReportDomainError(
                "ASSESSMENT_OBJECT_SOURCE_SECTION_MISMATCH",
                "对象来源章节与既有附录 A 绑定不一致。",
                status_code=422,
                project_uuid=project_uuid,
                field="source_section_code",
                details={"bound_sections": sorted(bound_sections)},
            )
        source_section_code = payload.source_section_code
        name_snapshot = payload.name_snapshot.strip()
        if payload.source_row_id is not None:
            source = _assessment_row(db, int(project["id"]), payload.source_row_id, project_uuid)
            source_section_code = _validate_object_source(payload.object_type, payload.source_section_code, source, project_uuid)
            _ensure_row_binding_available(source, object_uuid, project_uuid)
            name_snapshot = str(source["object_name"]).strip()
        properties = safe_json_size(payload.properties, maximum=32_768, project_uuid=project_uuid, field="properties")
        try:
            cursor = db.execute(
                """
                UPDATE assessment_objects SET object_type=?,name_snapshot=?,source_section_code=?,source_row_id=?,
                    properties_json=?,active=?,revision=revision+1,updated_at=?
                WHERE project_id=? AND object_uuid=? AND revision=?
                """,
                (payload.object_type,name_snapshot,source_section_code,payload.source_row_id,properties,int(payload.active),database.utc_now(),project["id"],object_uuid,payload.expected_revision),
            )
        except sqlite3.IntegrityError as exc:
            raise ReportDomainError("ASSESSMENT_OBJECT_SOURCE_DUPLICATE", "同一附录 A 行只能绑定一个测评对象。", status_code=409, project_uuid=project_uuid, field="source_row_id") from exc
        require_cas_updated(db,cursor,table="assessment_objects",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="assessment_object",entity_uuid=object_uuid)
        if row["source_row_id"] and row["source_row_id"] != payload.source_row_id:
            db.execute("UPDATE assessment_rows SET assessment_object_uuid=NULL WHERE id=? AND assessment_object_uuid=?", (row["source_row_id"], object_uuid))
        if payload.source_row_id:
            db.execute("UPDATE assessment_rows SET assessment_object_uuid=? WHERE id=?", (object_uuid, payload.source_row_id))
        touch_project(db, int(project["id"]))
        return _object_result(db, db.execute("SELECT * FROM assessment_objects WHERE object_uuid=?", (object_uuid,)).fetchone())


def delete_object(project_uuid: str, object_uuid: str, expected_revision: int) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        row = ensure_uuid_in_project(db, "assessment_objects", "object_uuid", object_uuid, int(project["id"]), project_uuid=project_uuid, entity_type="assessment_object")
        references: list[dict[str, Any]] = []
        appendix_count = int(db.execute("SELECT COUNT(*) FROM assessment_rows WHERE assessment_object_uuid=?", (object_uuid,)).fetchone()[0])
        if appendix_count:
            references.append({"entity_type": "assessment_row", "count": appendix_count})
        for table, predicate, entity_type in (
            ("object_relations", "source_object_uuid=? OR target_object_uuid=?", "object_relation"),
            ("result_correction_relations", "a2_object_uuid=? OR a4_object_uuid=?", "result_correction_relation"),
        ):
            count = int(db.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id=? AND ({predicate})", (project["id"], object_uuid, object_uuid)).fetchone()[0])
            if count:
                references.append({"entity_type": entity_type, "count": count})
        block_count = int(db.execute("SELECT COUNT(*) FROM report_blocks WHERE project_id=? AND instr(payload_json,?)>0", (project["id"], object_uuid)).fetchone()[0])
        if block_count:
            references.append({"entity_type": "report_block", "count": block_count})
        if references:
            raise ReportDomainError("REPORT_ENTITY_REFERENCED", "测评对象仍被关系引用，请先迁移引用或停用对象。", status_code=409, project_uuid=project_uuid, entity_type="assessment_object", entity_uuid=object_uuid, details={"references": references})
        deleted = _object_result(db, row)
        cursor=db.execute("DELETE FROM assessment_objects WHERE project_id=? AND object_uuid=? AND revision=?", (project["id"], object_uuid, expected_revision))
        require_cas_updated(db,cursor,table="assessment_objects",project_id=int(project["id"]),expected_revision=expected_revision,project_uuid=project_uuid,entity_type="assessment_object",entity_uuid=object_uuid)
        touch_project(db, int(project["id"]))
        return deleted


def _assessment_row(db: sqlite3.Connection, project_id: int, row_id: int, project_uuid: str) -> sqlite3.Row:
    row = db.execute(
        """
        SELECT r.*, s.code AS section_code FROM assessment_rows r
        JOIN appendix_sections s ON s.id=r.section_id
        WHERE r.id=? AND s.project_id=?
        """,
        (row_id, project_id),
    ).fetchone()
    if row is None:
        raise ReportDomainError("APPENDIX_A_ROW_NOT_FOUND", "附录 A 测评对象记录不存在。", status_code=404, project_uuid=project_uuid, field="source_row_id")
    return row


def _validate_object_source(object_type: str, source_section_code: str | None, source: sqlite3.Row, project_uuid: str) -> str:
    actual_section = str(source["section_code"])
    if source_section_code not in (None, actual_section):
        raise ReportDomainError(
            "ASSESSMENT_OBJECT_SOURCE_SECTION_MISMATCH",
            "对象来源章节与附录 A 行不一致。",
            status_code=422,
            project_uuid=project_uuid,
            field="source_section_code",
            details={"expected": actual_section, "actual": source_section_code},
        )
    expected_type = SECTION_OBJECT_TYPES[actual_section]
    if object_type != expected_type:
        raise ReportDomainError(
            "ASSESSMENT_OBJECT_TYPE_MISMATCH",
            "对象类型与附录 A 来源章节不一致。",
            status_code=422,
            project_uuid=project_uuid,
            field="object_type",
            details={"expected": expected_type, "actual": object_type},
        )
    return actual_section


def _validate_section_type(object_type: str, section_code: str | None, project_uuid: str) -> None:
    if section_code is None:
        return
    expected_type = SECTION_OBJECT_TYPES[section_code]
    if object_type != expected_type:
        raise ReportDomainError(
            "ASSESSMENT_OBJECT_TYPE_MISMATCH",
            "对象类型与附录 A 来源章节不一致。",
            status_code=422,
            project_uuid=project_uuid,
            field="object_type",
            details={"expected": expected_type, "actual": object_type, "section_code": section_code},
        )


def _ensure_row_binding_available(
    source: sqlite3.Row,
    object_uuid: str | None,
    project_uuid: str,
) -> None:
    current = source["assessment_object_uuid"]
    if current and current != object_uuid:
        raise ReportDomainError(
            "APPENDIX_A_ROW_ALREADY_BOUND",
            "该附录 A 行已绑定其他测评对象，请先处理现有绑定或合并对象。",
            status_code=409,
            project_uuid=project_uuid,
            field="source_row_id",
            details={"current_object_uuid": current},
        )


def preview_bindings(project_uuid: str) -> dict[str, list[dict[str, Any]]]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        appendix_rows = db.execute(
            """
            SELECT r.id AS source_row_id,r.object_name,r.subsystem,s.code AS section_code,r.assessment_object_uuid
            FROM assessment_rows r JOIN appendix_sections s ON s.id=r.section_id
            WHERE s.project_id=? AND TRIM(r.object_name)<>'' ORDER BY s.sort_order,r.sort_order,r.id
            """,
            (project["id"],),
        ).fetchall()
        objects = db.execute("SELECT * FROM assessment_objects WHERE project_id=? AND active=1", (project["id"],)).fetchall()
        result: dict[str, list[dict[str, Any]]] = {key: [] for key in ("exact", "candidate", "ambiguous", "unmatched")}
        for source in appendix_rows:
            normalized = source["object_name"].strip().casefold()
            exact = [row for row in objects if row["source_row_id"] == source["source_row_id"] or row["object_uuid"] == source["assessment_object_uuid"]]
            expected_type = SECTION_OBJECT_TYPES[source["section_code"]]
            candidates = [
                row
                for row in objects
                if row["object_type"] == expected_type
                and row["name_snapshot"].strip().casefold() == normalized
                and row not in exact
            ]
            item = {
                "source_row_id": source["source_row_id"],
                "section_code": source["section_code"],
                "object_name": source["object_name"],
                "subsystem": source["subsystem"],
                "matches": [{"object_uuid": row["object_uuid"], "name_snapshot": row["name_snapshot"], "object_type": row["object_type"]} for row in exact + candidates],
            }
            if len(exact) == 1:
                result["exact"].append(item)
            elif len(candidates) == 1:
                result["candidate"].append(item)
            elif len(exact) + len(candidates) > 1:
                result["ambiguous"].append(item)
            else:
                result["unmatched"].append(item)
        return result


def confirm_bindings(project_uuid: str, payload: BindingConfirmWrite) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        seen_rows: set[int] = set()
        for choice in payload.choices:
            if choice.source_row_id in seen_rows:
                raise ReportDomainError("APPENDIX_A_BINDING_DUPLICATE", "同一附录 A 行不能重复提交绑定。", status_code=422, project_uuid=project_uuid, field="choices")
            seen_rows.add(choice.source_row_id)
            row = _assessment_row(db, project_id, choice.source_row_id, project_uuid)
            obj = ensure_uuid_in_project(db, "assessment_objects", "object_uuid", choice.object_uuid, project_id, project_uuid=project_uuid, entity_type="assessment_object")
            _ensure_row_binding_available(row, choice.object_uuid, project_uuid)
            expected_type = SECTION_OBJECT_TYPES[row["section_code"]]
            if obj["object_type"] != expected_type or obj["source_section_code"] not in (None, row["section_code"]):
                raise ReportDomainError("ASSESSMENT_OBJECT_BINDING_TYPE_MISMATCH", "测评对象类型或来源章节与附录 A 行不一致。", status_code=422, project_uuid=project_uuid, entity_type="assessment_object", entity_uuid=choice.object_uuid, details={"expected_type": expected_type, "section_code": row["section_code"]})
            db.execute("UPDATE assessment_rows SET assessment_object_uuid=? WHERE id=?", (choice.object_uuid, choice.source_row_id))
            cursor = db.execute(
                """
                UPDATE assessment_objects
                SET source_row_id=COALESCE(source_row_id,?),
                    source_section_code=COALESCE(source_section_code,?),
                    name_snapshot=CASE WHEN source_row_id IS NULL THEN ? ELSE name_snapshot END,
                    revision=revision+1,updated_at=?
                WHERE project_id=? AND object_uuid=? AND revision=?
                """,
                (choice.source_row_id,row["section_code"],row["object_name"],database.utc_now(),project_id,choice.object_uuid,obj["revision"]),
            )
            require_cas_updated(db,cursor,table="assessment_objects",project_id=project_id,expected_revision=int(obj["revision"]),project_uuid=project_uuid,entity_type="assessment_object",entity_uuid=choice.object_uuid)
        touch_project(db, project_id)
        return {"bound_count": len(payload.choices), "bindings": [choice.model_dump() for choice in payload.choices]}


def list_subsystems(project_uuid: str) -> list[dict[str, Any]]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        rows = db.execute("SELECT * FROM assessment_object_subsystems WHERE project_id=? ORDER BY id", (project["id"],)).fetchall()
        result = []
        for row in rows:
            item = row_dict(row) or {}
            item["methods"] = json.loads(item.pop("assessment_methods_json"))
            result.append(item)
        return result


def _application_catalog(db: sqlite3.Connection, project_id: int) -> list[str]:
    row = db.execute("SELECT interconnection_json FROM system_profiles WHERE project_id=?", (project_id,)).fetchone()
    if not row:
        return []
    data = json.loads(row["interconnection_json"])
    values = data.get("application_catalog", [])
    return [str(value).strip() for value in values if str(value).strip()]


def _ensure_correction_subsystem_compatible(
    db: sqlite3.Connection,
    *,
    project_id: int,
    project_uuid: str,
    object_uuid: str,
    subsystem_name: str,
) -> None:
    desired = _normalized_subsystem(subsystem_name)
    relations = db.execute(
        """
        SELECT a2_object_uuid,a4_object_uuid
        FROM result_correction_relations
        WHERE project_id=? AND (a2_object_uuid=? OR a4_object_uuid=?)
        """,
        (project_id, object_uuid, object_uuid),
    ).fetchall()
    for relation in relations:
        other_uuid = (
            relation["a4_object_uuid"]
            if relation["a2_object_uuid"] == object_uuid
            else relation["a2_object_uuid"]
        )
        other = db.execute(
            """
            SELECT subsystem_name FROM assessment_object_subsystems
            WHERE project_id=? AND object_uuid=?
            """,
            (project_id, other_uuid),
        ).fetchone()
        other_subsystem = _normalized_subsystem(
            str(other["subsystem_name"] or "") if other is not None else ""
        )
        if not desired or desired != other_subsystem:
            raise ReportDomainError(
                "CORRECTION_SUBSYSTEM_MISMATCH",
                "该对象已有 A-2/A-4 传输关系，修改子系统前请先解除或调整关联。",
                status_code=422,
                project_uuid=project_uuid,
                entity_uuid=object_uuid,
                field="subsystem_name",
            )


def upsert_subsystem(project_uuid: str, payload: ObjectSubsystemWrite) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        obj = ensure_uuid_in_project(db, "assessment_objects", "object_uuid", payload.object_uuid, project_id, project_uuid=project_uuid, entity_type="assessment_object")
        if obj["source_section_code"] not in {"A-2", "A-4"}:
            raise ReportDomainError("APPENDIX_SUBSYSTEM_OBJECT_INVALID", "只有 A-2/A-4 测评对象可以绑定所属子系统。", status_code=422, project_uuid=project_uuid, entity_uuid=payload.object_uuid, field="object_uuid")
        if obj["source_section_code"] == "A-4":
            catalog = _application_catalog(db, project_id)
            if len(catalog) != len(set(catalog)):
                raise ReportDomainError("APPLICATION_CATALOG_DUPLICATE", "表 2-7 应用名称存在重复，不能建立 A-4 子系统关联。", status_code=422, project_uuid=project_uuid, field="application_catalog")
            if payload.subsystem_name not in catalog:
                raise ReportDomainError("A4_SUBSYSTEM_NOT_IN_APPLICATION_CATALOG", "A-4 子系统必须选自表 2-7 应用名称。", status_code=422, project_uuid=project_uuid, field="subsystem_name", details={"allowed": catalog})
        _ensure_correction_subsystem_compatible(
            db,
            project_id=project_id,
            project_uuid=project_uuid,
            object_uuid=payload.object_uuid,
            subsystem_name=payload.subsystem_name,
        )
        existing = db.execute("SELECT * FROM assessment_object_subsystems WHERE project_id=? AND object_uuid=?", (project_id,payload.object_uuid)).fetchone()
        timestamp=database.utc_now()
        if existing:
            if payload.expected_revision is None:
                raise ReportDomainError("REVISION_REQUIRED", "更新子系统关联时必须提供 revision。", status_code=422, project_uuid=project_uuid, entity_type="assessment_object_subsystem", entity_uuid=existing["binding_uuid"])
            cursor=db.execute("UPDATE assessment_object_subsystems SET subsystem_name=?,assessment_methods_json=?,remark=?,revision=revision+1,updated_at=? WHERE project_id=? AND binding_uuid=? AND revision=?",(payload.subsystem_name,dump_json(payload.methods),payload.remark,timestamp,project_id,existing["binding_uuid"],payload.expected_revision))
            require_cas_updated(db,cursor,table="assessment_object_subsystems",project_id=project_id,expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="assessment_object_subsystem",entity_uuid=existing["binding_uuid"])
            binding_uuid=existing["binding_uuid"]
        else:
            binding_uuid=new_uuid(); db.execute("INSERT INTO assessment_object_subsystems (binding_uuid,project_id,object_uuid,subsystem_name,assessment_methods_json,remark,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,1,?,?)",(binding_uuid,project_id,payload.object_uuid,payload.subsystem_name,dump_json(payload.methods),payload.remark,timestamp,timestamp))
        touch_project(db,project_id)
        row=db.execute("SELECT * FROM assessment_object_subsystems WHERE binding_uuid=?",(binding_uuid,)).fetchone(); item=row_dict(row) or {}; item["methods"]=json.loads(item.pop("assessment_methods_json")); return item


def list_object_relations(project_uuid: str) -> list[dict[str, Any]]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); return rows_dict(db.execute("SELECT * FROM object_relations WHERE project_id=? ORDER BY id",(project["id"],)).fetchall())


def _contains_cycle(db: sqlite3.Connection, project_id: int, source: str, target: str, exclude: str | None=None) -> bool:
    graph: dict[str,list[str]]=defaultdict(list)
    for row in db.execute("SELECT relation_uuid,source_object_uuid,target_object_uuid FROM object_relations WHERE project_id=? AND relation_type='contains' AND active=1",(project_id,)).fetchall():
        if row["relation_uuid"] != exclude: graph[row["source_object_uuid"]].append(row["target_object_uuid"])
    graph[source].append(target); stack=[target]; seen=set()
    while stack:
        node=stack.pop()
        if node==source: return True
        if node not in seen: seen.add(node); stack.extend(graph[node])
    return False


def _contains_cycle_after_merge(db: sqlite3.Connection, project_id: int, source: str, target: str) -> bool:
    graph: dict[str,list[str]]=defaultdict(list)
    nodes: set[str] = set()
    indegree: dict[str,int] = defaultdict(int)
    for row in db.execute(
        "SELECT source_object_uuid,target_object_uuid FROM object_relations "
        "WHERE project_id=? AND relation_type='contains' AND active=1",
        (project_id,),
    ).fetchall():
        new_source = target if row["source_object_uuid"] == source else row["source_object_uuid"]
        new_target = target if row["target_object_uuid"] == source else row["target_object_uuid"]
        if new_source == new_target:
            return True
        graph[new_source].append(new_target)
        nodes.update((new_source,new_target))
        indegree[new_target] += 1
        indegree.setdefault(new_source,0)

    queue = [node for node in nodes if indegree[node] == 0]
    visited_count = 0
    while queue:
        node = queue.pop()
        visited_count += 1
        for child in graph[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    return visited_count != len(nodes)


def _relation_values(db: sqlite3.Connection, project_id: int, project_uuid: str, payload: ObjectRelationWrite, exclude: str|None=None) -> tuple[Any,...]:
    ensure_uuid_in_project(db,"assessment_objects","object_uuid",payload.source_object_uuid,project_id,project_uuid=project_uuid,entity_type="assessment_object"); ensure_uuid_in_project(db,"assessment_objects","object_uuid",payload.target_object_uuid,project_id,project_uuid=project_uuid,entity_type="assessment_object")
    if payload.relation_type=="contains" and _contains_cycle(db,project_id,payload.source_object_uuid,payload.target_object_uuid,exclude): raise ReportDomainError("OBJECT_RELATION_CYCLE","对象包含关系不能形成循环。",status_code=422,project_uuid=project_uuid,field="target_object_uuid")
    return (payload.source_object_uuid,payload.target_object_uuid,payload.relation_type,safe_json_size(payload.properties,maximum=32768,project_uuid=project_uuid,field="properties"),int(payload.active))


def create_object_relation(project_uuid: str,payload:ObjectRelationWrite)->dict[str,Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); relation_uuid=new_uuid(); timestamp=database.utc_now()
        try: db.execute("INSERT INTO object_relations (relation_uuid,project_id,source_object_uuid,target_object_uuid,relation_type,properties_json,active,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,1,?,?)",(relation_uuid,project["id"],*_relation_values(db,int(project["id"]),project_uuid,payload),timestamp,timestamp))
        except sqlite3.IntegrityError as exc: raise ReportDomainError("OBJECT_RELATION_DUPLICATE","相同对象关系已存在。",status_code=409,project_uuid=project_uuid) from exc
        touch_project(db,int(project["id"])); return row_dict(db.execute("SELECT * FROM object_relations WHERE relation_uuid=?",(relation_uuid,)).fetchone()) or {}


def update_object_relation(project_uuid:str,relation_uuid:str,payload:ObjectRelationUpdate)->dict[str,Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); row=ensure_uuid_in_project(db,"object_relations","relation_uuid",relation_uuid,int(project["id"]),project_uuid=project_uuid,entity_type="object_relation")
        try:
            cursor=db.execute("UPDATE object_relations SET source_object_uuid=?,target_object_uuid=?,relation_type=?,properties_json=?,active=?,revision=revision+1,updated_at=? WHERE relation_uuid=? AND project_id=? AND revision=?",(*_relation_values(db,int(project["id"]),project_uuid,payload,relation_uuid),database.utc_now(),relation_uuid,project["id"],payload.expected_revision))
        except sqlite3.IntegrityError as exc:
            raise ReportDomainError("OBJECT_RELATION_DUPLICATE","相同对象关系已存在。",status_code=409,project_uuid=project_uuid) from exc
        require_cas_updated(db,cursor,table="object_relations",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="object_relation",entity_uuid=relation_uuid)
        touch_project(db,int(project["id"])); return row_dict(db.execute("SELECT * FROM object_relations WHERE relation_uuid=?",(relation_uuid,)).fetchone()) or {}


def delete_object_relation(project_uuid:str,relation_uuid:str,expected_revision:int)->dict[str,Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); row=ensure_uuid_in_project(db,"object_relations","relation_uuid",relation_uuid,int(project["id"]),project_uuid=project_uuid,entity_type="object_relation"); cursor=db.execute("DELETE FROM object_relations WHERE project_id=? AND relation_uuid=? AND revision=?",(project["id"],relation_uuid,expected_revision)); require_cas_updated(db,cursor,table="object_relations",project_id=int(project["id"]),expected_revision=expected_revision,project_uuid=project_uuid,entity_type="object_relation",entity_uuid=relation_uuid); touch_project(db,int(project["id"])); return row_dict(row) or {}


CORRECTION_METRICS = {
    "confidentiality": ("通信过程中重要数据的机密性", "重要数据传输机密性"),
    "integrity": ("通信数据完整性", "重要数据传输完整性"),
}


def _normalize_metric(value:str)->str: return "".join(value.split()).replace("指标","")


def _project_revision(db: sqlite3.Connection, project_id: int) -> int:
    row = db.execute(
        "SELECT project_revision FROM report_generation_state WHERE project_id=?",
        (project_id,),
    ).fetchone()
    return int(row["project_revision"]) if row is not None else 0


def _appendix_transmission_relations_result(
    db: sqlite3.Connection,
    project: sqlite3.Row,
) -> dict[str, Any]:
    project_id = int(project["id"])
    source_rows = db.execute(
        """
        SELECT o.object_uuid,o.name_snapshot,s.code AS section_code,
               r.object_name,r.unit,r.sort_order,
               b.subsystem_name
        FROM assessment_objects o
        JOIN assessment_rows r ON r.assessment_object_uuid=o.object_uuid
        JOIN appendix_sections s ON s.id=r.section_id AND s.project_id=o.project_id
        LEFT JOIN assessment_object_subsystems b
          ON b.project_id=o.project_id AND b.object_uuid=o.object_uuid
        WHERE o.project_id=? AND o.active=1 AND s.code IN ('A-2','A-4')
        ORDER BY s.sort_order,r.sort_order,r.id
        """,
        (project_id,),
    ).fetchall()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in source_rows:
        key = (str(row["section_code"]), str(row["object_uuid"]))
        item = grouped.setdefault(
            key,
            {
                "object_uuid": str(row["object_uuid"]),
                "object_name": str(row["object_name"] or row["name_snapshot"] or ""),
                "subsystem": str(row["subsystem_name"] or "").strip(),
                "available_kinds": [],
                "relations": [],
            },
        )
        for kind, metric_pair in CORRECTION_METRICS.items():
            expected = metric_pair[0] if key[0] == "A-2" else metric_pair[1]
            if (
                _normalize_metric(str(row["unit"] or "")) == _normalize_metric(expected)
                and kind not in item["available_kinds"]
            ):
                item["available_kinds"].append(kind)

    relations = []
    for row in db.execute(
        """
        SELECT correction_uuid,a2_object_uuid,a4_object_uuid,correction_kind,revision
        FROM result_correction_relations
        WHERE project_id=?
        ORDER BY correction_kind,a2_object_uuid,a4_object_uuid
        """,
        (project_id,),
    ).fetchall():
        relation = {
            "correction_uuid": str(row["correction_uuid"]),
            "kind": str(row["correction_kind"]),
            "a2_object_uuid": str(row["a2_object_uuid"]),
            "a4_object_uuid": str(row["a4_object_uuid"]),
            "revision": int(row["revision"]),
        }
        relations.append(relation)
        for key in (("A-2", relation["a2_object_uuid"]), ("A-4", relation["a4_object_uuid"])):
            if key in grouped:
                grouped[key]["relations"].append(dict(relation))

    a2_objects = [item for (section, _), item in grouped.items() if section == "A-2"]
    a4_objects = [item for (section, _), item in grouped.items() if section == "A-4"]
    for items in (a2_objects, a4_objects):
        items.sort(key=lambda item: (item["subsystem"], item["object_name"], item["object_uuid"]))
        for item in items:
            item["available_kinds"].sort()
            item["relations"].sort(
                key=lambda relation: (relation["kind"], relation["a4_object_uuid"], relation["a2_object_uuid"])
            )
    subsystem_labels: dict[str, str] = {}
    for item in [*a2_objects, *a4_objects]:
        label = str(item["subsystem"] or "").strip()
        if label:
            subsystem_labels.setdefault(_normalized_subsystem(label), label)
    return {
        "project_revision": _project_revision(db, project_id),
        "shared_subsystems": sorted(subsystem_labels.values()),
        "a2_objects": a2_objects,
        "a4_objects": a4_objects,
    }


def get_appendix_transmission_relations(project_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        return _appendix_transmission_relations_result(db, project)


def _bound_correction_object(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    object_uuid: str,
    section_code: str,
) -> sqlite3.Row:
    obj = ensure_uuid_in_project(
        db,
        "assessment_objects",
        "object_uuid",
        object_uuid,
        project_id,
        project_uuid=project_uuid,
        entity_type="assessment_object",
    )
    bound = db.execute(
        """
        SELECT 1 FROM assessment_rows r
        JOIN appendix_sections s ON s.id=r.section_id
        WHERE s.project_id=? AND s.code=? AND r.assessment_object_uuid=?
        LIMIT 1
        """,
        (project_id, section_code, object_uuid),
    ).fetchone()
    if not bool(obj["active"]) or bound is None:
        raise ReportDomainError(
            "CORRECTION_RELATION_ENDPOINT_INVALID",
            "修正关系端点必须是已绑定的 A-2/A-4 测评对象。",
            status_code=422,
            project_uuid=project_uuid,
            entity_uuid=object_uuid,
        )
    return obj


def _bound_metric_row(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    object_uuid: str,
    section_code: str,
    metric_code: str,
) -> sqlite3.Row:
    matched = [
        row
        for row in db.execute(
            """
            SELECT r.id,r.unit FROM assessment_rows r
            JOIN appendix_sections s ON s.id=r.section_id
            WHERE s.project_id=? AND s.code=? AND r.assessment_object_uuid=?
            ORDER BY r.sort_order,r.id
            """,
            (project_id, section_code, object_uuid),
        ).fetchall()
        if _normalize_metric(str(row["unit"] or "")) == _normalize_metric(metric_code)
    ]
    if len(matched) != 1:
        raise ReportDomainError(
            "CORRECTION_METRIC_ROW_INVALID",
            "修正关系要求每个对象在对应传输指标下恰好存在一条原始测评记录。",
            status_code=422,
            project_uuid=project_uuid,
            entity_uuid=object_uuid,
            details={
                "section_code": section_code,
                "metric_code": metric_code,
                "matched_row_ids": [int(row["id"]) for row in matched],
            },
        )
    return matched[0]


def _bound_subsystem(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    object_uuid: str,
) -> str:
    row = db.execute(
        "SELECT subsystem_name FROM assessment_object_subsystems WHERE project_id=? AND object_uuid=?",
        (project_id, object_uuid),
    ).fetchone()
    value = str(row["subsystem_name"] or "").strip() if row is not None else ""
    if not value:
        raise ReportDomainError(
            "CORRECTION_SUBSYSTEM_REQUIRED",
            "建立传输修正关系前，A-2 和 A-4 对象都必须填写所属子系统。",
            status_code=422,
            project_uuid=project_uuid,
            entity_uuid=object_uuid,
            field="subsystem",
        )
    return value


def _normalized_subsystem(value: str) -> str:
    return " ".join(value.split()).casefold()


def _revision_conflict(
    project_uuid: str,
    expected_revision: int | None,
    current_revision: int | None,
    *,
    expected_correction_uuid: str | None = None,
    current_correction_uuid: str | None = None,
) -> ReportDomainError:
    return ReportDomainError(
        "REVISION_CONFLICT",
        "修正关系已在其他页面更新，请刷新后重试。",
        status_code=409,
        project_uuid=project_uuid,
        entity_type="result_correction_relation",
        details={
            "expected_revision": expected_revision,
            "current_revision": current_revision,
            "expected_correction_uuid": expected_correction_uuid,
            "current_correction_uuid": current_correction_uuid,
        },
    )


def put_appendix_transmission_relation(
    project_uuid: str,
    payload: AppendixTransmissionRelationWrite,
) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        _bound_correction_object(db, project_id, project_uuid, payload.a4_object_uuid, "A-4")
        existing = db.execute(
            """
            SELECT * FROM result_correction_relations
            WHERE project_id=? AND a4_object_uuid=? AND correction_kind=?
            """,
            (project_id, payload.a4_object_uuid, payload.kind),
        ).fetchone()

        if (
            existing is not None
            and payload.expected_correction_uuid is not None
            and payload.expected_correction_uuid != existing["correction_uuid"]
        ):
            raise _revision_conflict(
                project_uuid,
                payload.expected_revision,
                int(existing["revision"]),
                expected_correction_uuid=payload.expected_correction_uuid,
                current_correction_uuid=str(existing["correction_uuid"]),
            )

        if payload.a2_object_uuid is None:
            if existing is None:
                return _appendix_transmission_relations_result(db, project)
            if payload.expected_revision is None or payload.expected_correction_uuid is None:
                raise ReportDomainError(
                    "REVISION_REQUIRED",
                    "删除现有修正关系时必须提供 relation UUID 和 revision。",
                    status_code=422,
                    project_uuid=project_uuid,
                    field="expected_revision",
                )
            cursor = db.execute(
                """
                DELETE FROM result_correction_relations
                WHERE project_id=? AND correction_uuid=? AND revision=?
                """,
                (project_id, existing["correction_uuid"], payload.expected_revision),
            )
            require_cas_updated(
                db,
                cursor,
                table="result_correction_relations",
                project_id=project_id,
                expected_revision=payload.expected_revision,
                project_uuid=project_uuid,
                entity_type="result_correction_relation",
                entity_uuid=str(existing["correction_uuid"]),
            )
            touch_project(db, project_id)
            return _appendix_transmission_relations_result(db, project)

        _bound_correction_object(db, project_id, project_uuid, payload.a2_object_uuid, "A-2")
        a2_metric_code, a4_metric_code = CORRECTION_METRICS[payload.kind]
        a2_row = _bound_metric_row(
            db, project_id, project_uuid, payload.a2_object_uuid, "A-2", a2_metric_code
        )
        a4_row = _bound_metric_row(
            db, project_id, project_uuid, payload.a4_object_uuid, "A-4", a4_metric_code
        )
        a2_subsystem = _bound_subsystem(db, project_id, project_uuid, payload.a2_object_uuid)
        a4_subsystem = _bound_subsystem(db, project_id, project_uuid, payload.a4_object_uuid)
        if _normalized_subsystem(a2_subsystem) != _normalized_subsystem(a4_subsystem):
            raise ReportDomainError(
                "CORRECTION_SUBSYSTEM_MISMATCH",
                "A-2 通道与 A-4 重要数据对象必须属于同一子系统。",
                status_code=422,
                project_uuid=project_uuid,
                field="a2_object_uuid",
                details={"a2_subsystem": a2_subsystem, "a4_subsystem": a4_subsystem},
            )
        references = {"a2_row_id": int(a2_row["id"]), "a4_row_id": int(a4_row["id"])}
        references_json = dump_json(references)
        desired_matches = existing is not None and all(
            (
                existing["a2_object_uuid"] == payload.a2_object_uuid,
                existing["a2_metric_code"] == a2_metric_code,
                existing["a4_metric_code"] == a4_metric_code,
                existing["original_references_json"] == references_json,
            )
        )
        if desired_matches:
            return _appendix_transmission_relations_result(db, project)

        timestamp = database.utc_now()
        if existing is None:
            if payload.expected_revision is not None or payload.expected_correction_uuid is not None:
                raise _revision_conflict(
                    project_uuid,
                    payload.expected_revision,
                    None,
                    expected_correction_uuid=payload.expected_correction_uuid,
                )
            try:
                db.execute(
                    """
                    INSERT INTO result_correction_relations (
                        correction_uuid,project_id,a2_object_uuid,a2_metric_code,
                        a4_object_uuid,a4_metric_code,correction_kind,
                        original_references_json,revision,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,1,?,?)
                    """,
                    (
                        new_uuid(),
                        project_id,
                        payload.a2_object_uuid,
                        a2_metric_code,
                        payload.a4_object_uuid,
                        a4_metric_code,
                        payload.kind,
                        references_json,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise _revision_conflict(project_uuid, payload.expected_revision, None) from exc
        else:
            if payload.expected_revision is None or payload.expected_correction_uuid is None:
                raise ReportDomainError(
                    "REVISION_REQUIRED",
                    "更新现有修正关系时必须提供 relation UUID 和 revision。",
                    status_code=422,
                    project_uuid=project_uuid,
                    field="expected_revision",
                )
            cursor = db.execute(
                """
                UPDATE result_correction_relations
                SET a2_object_uuid=?,a2_metric_code=?,a4_metric_code=?,
                    original_references_json=?,revision=revision+1,updated_at=?
                WHERE project_id=? AND correction_uuid=? AND revision=?
                """,
                (
                    payload.a2_object_uuid,
                    a2_metric_code,
                    a4_metric_code,
                    references_json,
                    timestamp,
                    project_id,
                    existing["correction_uuid"],
                    payload.expected_revision,
                ),
            )
            require_cas_updated(
                db,
                cursor,
                table="result_correction_relations",
                project_id=project_id,
                expected_revision=payload.expected_revision,
                project_uuid=project_uuid,
                entity_type="result_correction_relation",
                entity_uuid=str(existing["correction_uuid"]),
            )
        touch_project(db, project_id)
        return _appendix_transmission_relations_result(db, project)


def _validate_correction_original_references(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    payload: CorrectionRelationWrite,
) -> dict[str,int]:
    submitted_references = payload.original_references.model_dump()
    expected = {
        "a2_row_id": ("A-2", payload.a2_metric_code, payload.a2_object_uuid),
        "a4_row_id": ("A-4", payload.a4_metric_code, payload.a4_object_uuid),
    }
    normalized: dict[str,int] = {}
    for key,(section_code,metric_code,object_uuid) in expected.items():
        row_id = submitted_references[key]
        if row_id is None:
            raise ReportDomainError(
                "CORRECTION_ORIGINAL_REFERENCE_INVALID",
                "修正关系必须同时引用对应的 A-2 和 A-4 原始测评行。",
                status_code=422,
                project_uuid=project_uuid,
                field=f"original_references.{key}",
                details={"reason":"missing_row_id"},
            )
        row = db.execute(
            """
            SELECT r.id,r.unit,r.assessment_object_uuid,s.code AS section_code,s.project_id
            FROM assessment_rows r
            JOIN appendix_sections s ON s.id=r.section_id
            WHERE r.id=?
            """,
            (row_id,),
        ).fetchone()
        reason = None
        if row is None:
            reason = "row_not_found"
        elif int(row["project_id"]) != project_id:
            reason = "project_mismatch"
        elif row["section_code"] != section_code:
            reason = "section_mismatch"
        elif _normalize_metric(str(row["unit"])) != _normalize_metric(metric_code):
            reason = "metric_mismatch"
        elif not row["assessment_object_uuid"]:
            reason = "object_not_bound"
        elif row["assessment_object_uuid"] != object_uuid:
            reason = "object_mismatch"
        if reason:
            raise ReportDomainError(
                "CORRECTION_ORIGINAL_REFERENCE_INVALID",
                "修正关系引用的原始测评行与项目、指标或测评对象不一致。",
                status_code=422,
                project_uuid=project_uuid,
                field=f"original_references.{key}",
                details={"reason":reason,"row_id":row_id,"expected_section":section_code,"expected_object_uuid":object_uuid},
            )
        normalized[key] = row_id
    return normalized


def _correction_values(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    payload: CorrectionRelationWrite,
) -> tuple[Any, ...]:
    a2 = ensure_uuid_in_project(
        db,
        "assessment_objects",
        "object_uuid",
        payload.a2_object_uuid,
        project_id,
        project_uuid=project_uuid,
        entity_type="assessment_object",
    )
    a4 = ensure_uuid_in_project(
        db,
        "assessment_objects",
        "object_uuid",
        payload.a4_object_uuid,
        project_id,
        project_uuid=project_uuid,
        entity_type="assessment_object",
    )
    if a2["source_section_code"] != "A-2" or a4["source_section_code"] != "A-4":
        raise ReportDomainError(
            "CORRECTION_RELATION_ENDPOINT_INVALID",
            "修正关系必须连接 A-2 通道和 A-4 重要数据对象。",
            status_code=422,
            project_uuid=project_uuid,
        )
    expected = CORRECTION_METRICS[payload.correction_kind]
    if (
        _normalize_metric(payload.a2_metric_code) != _normalize_metric(expected[0])
        or _normalize_metric(payload.a4_metric_code) != _normalize_metric(expected[1])
    ):
        raise ReportDomainError(
            "CORRECTION_METRIC_PAIR_INVALID",
            "修正关系的指标类型不匹配。",
            status_code=422,
            project_uuid=project_uuid,
            field="a2_metric_code",
            details={"expected": expected},
        )
    original_references = _validate_correction_original_references(
        db, project_id, project_uuid, payload
    )
    a2_subsystem = _bound_subsystem(db, project_id, project_uuid, payload.a2_object_uuid)
    a4_subsystem = _bound_subsystem(db, project_id, project_uuid, payload.a4_object_uuid)
    if _normalized_subsystem(a2_subsystem) != _normalized_subsystem(a4_subsystem):
        raise ReportDomainError(
            "CORRECTION_SUBSYSTEM_MISMATCH",
            "A-2 通道与 A-4 重要数据对象必须属于同一子系统。",
            status_code=422,
            project_uuid=project_uuid,
            details={"a2_subsystem": a2_subsystem, "a4_subsystem": a4_subsystem},
        )
    return (
        payload.a2_object_uuid,
        payload.a2_metric_code,
        payload.a4_object_uuid,
        payload.a4_metric_code,
        payload.correction_kind,
        safe_json_size(
            original_references,
            maximum=32768,
            project_uuid=project_uuid,
            field="original_references",
        ),
    )


def list_correction_relations(project_uuid:str)->list[dict[str,Any]]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); return [_correction_result(row) for row in db.execute("SELECT * FROM result_correction_relations WHERE project_id=? ORDER BY correction_kind,id",(project["id"],)).fetchall()]


def _correction_result(row: sqlite3.Row) -> dict[str, Any]:
    result = row_dict(row) or {}
    result["original_references"] = json.loads(result.pop("original_references_json"))
    return result


def create_correction_relation(project_uuid:str,payload:CorrectionRelationWrite)->dict[str,Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); correction_uuid=new_uuid(); timestamp=database.utc_now()
        try: db.execute("INSERT INTO result_correction_relations (correction_uuid,project_id,a2_object_uuid,a2_metric_code,a4_object_uuid,a4_metric_code,correction_kind,original_references_json,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,1,?,?)",(correction_uuid,project["id"],*_correction_values(db,int(project["id"]),project_uuid,payload),timestamp,timestamp))
        except sqlite3.IntegrityError as exc: raise ReportDomainError("CORRECTION_RELATION_CARDINALITY","每个 A-4 对象在同类指标上只能关联一条 A-2 通道。",status_code=409,project_uuid=project_uuid) from exc
        touch_project(db,int(project["id"])); return _correction_result(db.execute("SELECT * FROM result_correction_relations WHERE correction_uuid=?",(correction_uuid,)).fetchone())


def update_correction_relation(project_uuid:str,correction_uuid:str,payload:CorrectionRelationUpdate)->dict[str,Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); row=ensure_uuid_in_project(db,"result_correction_relations","correction_uuid",correction_uuid,int(project["id"]),project_uuid=project_uuid,entity_type="result_correction_relation")
        try:
            cursor=db.execute("UPDATE result_correction_relations SET a2_object_uuid=?,a2_metric_code=?,a4_object_uuid=?,a4_metric_code=?,correction_kind=?,original_references_json=?,revision=revision+1,updated_at=? WHERE correction_uuid=? AND project_id=? AND revision=?",(*_correction_values(db,int(project["id"]),project_uuid,payload),database.utc_now(),correction_uuid,project["id"],payload.expected_revision))
        except sqlite3.IntegrityError as exc:
            raise ReportDomainError("CORRECTION_RELATION_CARDINALITY","每个 A-4 对象在同类指标上只能关联一条 A-2 通道。",status_code=409,project_uuid=project_uuid) from exc
        require_cas_updated(db,cursor,table="result_correction_relations",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="result_correction_relation",entity_uuid=correction_uuid)
        touch_project(db,int(project["id"])); return _correction_result(db.execute("SELECT * FROM result_correction_relations WHERE correction_uuid=?",(correction_uuid,)).fetchone())


def delete_correction_relation(project_uuid:str,correction_uuid:str,expected_revision:int)->dict[str,Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); row=ensure_uuid_in_project(db,"result_correction_relations","correction_uuid",correction_uuid,int(project["id"]),project_uuid=project_uuid,entity_type="result_correction_relation"); cursor=db.execute("DELETE FROM result_correction_relations WHERE project_id=? AND correction_uuid=? AND revision=?",(project["id"],correction_uuid,expected_revision)); require_cas_updated(db,cursor,table="result_correction_relations",project_id=int(project["id"]),expected_revision=expected_revision,project_uuid=project_uuid,entity_type="result_correction_relation",entity_uuid=correction_uuid); touch_project(db,int(project["id"])); return _correction_result(row)


TABLE_4_6_COLUMNS = (
    ("transmission_confidentiality", "重要数据传输机密性", ("重要数据传输机密性",)),
    ("storage_confidentiality", "重要数据存储机密性", ("重要数据存储机密性",)),
    ("transmission_integrity", "重要数据传输完整性", ("重要数据传输完整性",)),
    (
        "storage_integrity",
        "存储完整性",
        ("访问控制信息完整性", "重要数据存储完整性"),
    ),
)


def _projection_source(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_row_id": int(row["id"]),
        "object_uuid": row.get("assessment_object_uuid"),
        "object_name": row.get("object_name") or "",
        "indicator": row.get("unit") or "",
        "compliance": row.get("compliance") or "",
        "object_score": row.get("object_score"),
        "unit_score": row.get("unit_score"),
    }


def _single_indicator_projection(
    project_uuid: str,
    projection_id: str,
    rows: list[dict[str, Any]],
    indicator: str,
) -> dict[str, Any]:
    matched = [row for row in rows if indicator in str(row.get("unit", ""))]
    by_object: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in matched:
        by_object[str(row["assessment_object_uuid"])].append(row)
    output: list[dict[str, Any]] = []
    for object_uuid, object_rows in sorted(
        by_object.items(), key=lambda item: str(item[1][0].get("object_name") or "")
    ):
        if len(object_rows) != 1:
            raise ReportDomainError(
                "REPORT_PROJECTION_DUPLICATE_CELL",
                "同一测评对象的同一指标存在多条记录，无法确定性投影。",
                status_code=409,
                project_uuid=project_uuid,
                details={
                    "projection_id": projection_id,
                    "object_uuid": object_uuid,
                    "source_row_ids": [int(row["id"]) for row in object_rows],
                },
            )
        source = _projection_source(object_rows[0])
        output.append(
            {
                "object_uuid": object_uuid,
                "object_name": source["object_name"],
                "indicator": source["indicator"],
                "compliance": source["compliance"],
                "source_row_id": source["source_row_id"],
            }
        )
    return {
        "projection_id": projection_id,
        "readonly": True,
        "indicator": indicator,
        "rows": output,
        "render_empty_structure": not bool(output),
    }


def _table_4_4_projection(project_uuid: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        subsystem = str(row.get("subsystem_name") or "").strip()
        if not subsystem:
            continue
        grouped[(subsystem, str(row.get("unit") or ""))].append(_projection_source(row))
    output = [
        {
            "object_name": subsystem,
            "subsystem_name": subsystem,
            "indicator": indicator,
            "unit": indicator,
            "source_row_ids": [item["source_row_id"] for item in sources],
            "source_records": sorted(sources, key=lambda item: (item["object_name"], item["source_row_id"])),
        }
        for (subsystem, indicator), sources in sorted(grouped.items())
    ]
    return {
        "projection_id": "table_4_4",
        "readonly": True,
        "grouping": ["subsystem_name", "indicator"],
        "aggregation_owner": "R3",
        "rows": output,
        "render_empty_structure": not bool(output),
    }


def _table_4_6_projection(project_uuid: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    relevant = [
        row
        for row in rows
        if any(term in str(row.get("unit", "")) for _, _, terms in TABLE_4_6_COLUMNS for term in terms)
    ]
    by_object: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in relevant:
        by_object[str(row["assessment_object_uuid"])].append(row)
    output: list[dict[str, Any]] = []
    for object_uuid, object_rows in sorted(
        by_object.items(), key=lambda item: str(item[1][0].get("object_name") or "")
    ):
        cells: dict[str, dict[str, Any]] = {}
        source_row_ids: list[int] = []
        for key, label, terms in TABLE_4_6_COLUMNS:
            matches = [
                row
                for row in object_rows
                if any(term in str(row.get("unit", "")) for term in terms)
            ]
            if len(matches) > 1:
                code = (
                    "TABLE_4_6_INTEGRITY_MAPPING_CONFLICT"
                    if key == "storage_integrity"
                    else "REPORT_PROJECTION_DUPLICATE_CELL"
                )
                raise ReportDomainError(
                    code,
                    "同一对象的表 4-6 投影列存在互斥或重复指标记录。",
                    status_code=409,
                    project_uuid=project_uuid,
                    details={
                        "projection_id": "table_4_6",
                        "object_uuid": object_uuid,
                        "column": key,
                        "source_row_ids": [int(row["id"]) for row in matches],
                    },
                )
            if matches:
                source = _projection_source(matches[0])
                source_row_ids.append(source["source_row_id"])
                cells[key] = {
                    "label": label,
                    "source_indicator": source["indicator"],
                    "compliance": source["compliance"],
                    "source_row_id": source["source_row_id"],
                    "missing": False,
                }
            else:
                cells[key] = {
                    "label": label,
                    "source_indicator": None,
                    "compliance": "不适用",
                    "source_row_id": None,
                    "missing": True,
                }
        output.append(
            {
                "object_uuid": object_uuid,
                "object_name": object_rows[0].get("object_name") or "",
                "cells": cells,
                "source_row_ids": sorted(source_row_ids),
            }
        )
    return {
        "projection_id": "table_4_6",
        "readonly": True,
        "columns": [{"key": key, "label": label} for key, label, _ in TABLE_4_6_COLUMNS],
        "rows": output,
        "render_empty_structure": not bool(output),
    }


def get_projection(project_uuid:str,projection_id:str)->dict[str,Any]:
    allowed={f"table_3_{i}" for i in range(4,8)}|{f"table_4_{i}" for i in range(1,12)}
    if projection_id not in allowed: raise ReportDomainError("REPORT_PROJECTION_NOT_AVAILABLE","投影标识不受支持。",status_code=404,project_uuid=project_uuid,field="projection_id")
    with database.connect() as db:
        project=require_report_project(project_uuid,db); project_id=int(project["id"])
        if projection_id.startswith("table_3_"):
            section={"table_3_4":"A-1","table_3_5":"A-2","table_3_6":"A-3","table_3_7":"A-4"}[projection_id]
            object_rows=db.execute(
                """
                SELECT DISTINCT r.assessment_object_uuid AS object_uuid,r.object_name
                FROM assessment_rows r
                JOIN appendix_sections a ON a.id=r.section_id
                JOIN assessment_objects o ON o.object_uuid=r.assessment_object_uuid AND o.project_id=a.project_id
                WHERE a.project_id=? AND a.code=? AND o.active=1
                ORDER BY r.object_name,r.id
                """,
                (project_id,section),
            ).fetchall()
            if section=="A-4":
                object_uuids={row["object_uuid"] for row in object_rows}
                names=sorted({row["subsystem_name"] for row in db.execute("SELECT subsystem_name,object_uuid FROM assessment_object_subsystems WHERE project_id=?",(project_id,)).fetchall() if row["object_uuid"] in object_uuids})
            else: names=sorted({row["object_name"] for row in object_rows})
            return {"projection_id":projection_id,"readonly":True,"rows":[{"object_name":name} for name in names]}
        section_index=int(projection_id.rsplit("_",1)[1]); section={1:"A-1",2:"A-2",3:"A-3",4:"A-4",8:"A-5",9:"A-6",10:"A-7",11:"A-8"}.get(section_index,"A-4")
        query="""SELECT r.id,r.unit,r.object_name,r.assessment_object_uuid,m.object_score,m.unit_score,m.compliance,s.subsystem_name FROM assessment_rows r JOIN appendix_sections a ON a.id=r.section_id LEFT JOIN metric_results m ON m.row_id=r.id JOIN assessment_objects o ON o.object_uuid=r.assessment_object_uuid AND o.project_id=a.project_id AND o.active=1 LEFT JOIN assessment_object_subsystems s ON s.object_uuid=o.object_uuid AND s.project_id=o.project_id WHERE a.project_id=? AND a.code=? ORDER BY r.sort_order,r.id"""
        rows=rows_dict(db.execute(query,(project_id,section)).fetchall())
        if section_index == 4:
            return _table_4_4_projection(project_uuid, rows)
        if section_index == 5:
            return _single_indicator_projection(project_uuid, projection_id, rows, "身份鉴别")
        if section_index == 6:
            return _table_4_6_projection(project_uuid, rows)
        if section_index == 7:
            return _single_indicator_projection(project_uuid, projection_id, rows, "不可否认性")
        return {"projection_id":projection_id,"readonly":True,"rows":rows}
