from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ... import database
from ...report_core.initializer import _load_r2_template_manifest
from ...report_schemas import (
    BlockReorderWrite,
    ReportBlockCreate,
    ReportBlockPatch,
    ReportSectionUpdate,
)
from .common import (
    dump_json,
    ensure_uuid_in_project,
    new_uuid,
    require_cas_updated,
    require_report_project,
    row_dict,
    safe_json_size,
    touch_project,
)
from .errors import ReportDomainError


MANUAL_BLOCK_TYPES = (
    "paragraph",
    "bullet_list",
    "numbered_list",
    "key_value_table",
    "data_table",
    "figure",
    "reference",
)
FORBIDDEN_CONTENT_RE = re.compile(
    r"<\s*(?:!--|!doctype\b|/?[a-z][a-z0-9:_-]*(?:\s+[^<>]*)?/?)\s*>"
    r"|javascript:|data:[^,]+;base64,|<\?xml",
    re.IGNORECASE,
)


def _manifest_sections() -> dict[str, dict[str, Any]]:
    manifest = _load_r2_template_manifest()
    return {str(item["section_key"]): item for item in manifest["sections"]}


def _allowed_types(section_type: str, edit_policy: str) -> list[str]:
    if edit_policy == "readonly" or section_type in {"generated", "appendix_a", "appendix_b", "form"}:
        return []
    return list(MANUAL_BLOCK_TYPES)


def _section_result(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    result = row_dict(row) or {}
    parent = None
    if row["parent_section_id"] is not None:
        parent_row = db.execute("SELECT section_uuid FROM report_sections WHERE id=? AND project_id=?", (row["parent_section_id"], row["project_id"])).fetchone()
        parent = parent_row["section_uuid"] if parent_row else None
    result["parent_section_uuid"] = parent
    result["allowed_block_types"] = _allowed_types(row["section_type"], row["edit_policy"])
    result["form_key"] = row["section_key"] if row["section_type"] == "form" else None
    return result


def _block_result(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    result = row_dict(row) or {}
    section = db.execute("SELECT section_uuid FROM report_sections WHERE id=? AND project_id=?", (row["section_id"], row["project_id"])).fetchone()
    result["section_uuid"] = section["section_uuid"] if section else None
    return result


def list_sections(project_uuid: str) -> list[dict[str, Any]]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        rows = db.execute(
            "SELECT * FROM report_sections WHERE project_id=? ORDER BY COALESCE(parent_section_id,0),sort_order,id",
            (project["id"],),
        ).fetchall()
        return [_section_result(db, row) for row in rows]


def get_section(project_uuid: str, section_uuid: str) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        section = ensure_uuid_in_project(db, "report_sections", "section_uuid", section_uuid, int(project["id"]), project_uuid=project_uuid, entity_type="report_section")
        blocks = db.execute("SELECT * FROM report_blocks WHERE project_id=? AND section_id=? ORDER BY sort_order,id", (project["id"], section["id"])).fetchall()
        result = {
            "section": _section_result(db, section),
            "blocks": [_block_result(db, row) for row in blocks],
        }
        section_key = str(section["section_key"])
    # 校验服务自行管理连接；先关闭章节读取事务，避免嵌套写锁和重复快照。
    from .validation import validate_report

    validation_result = validate_report(project_uuid)
    result["issues"] = [
        issue
        for issue in validation_result["issues"]
        if issue.get("target") == section_key
        or str(issue.get("target") or "").startswith(f"{section_key}.")
    ]
    return result


def update_section(project_uuid: str, section_uuid: str, payload: ReportSectionUpdate) -> dict[str, Any]:
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        section = ensure_uuid_in_project(db,"report_sections","section_uuid",section_uuid,int(project["id"]),project_uuid=project_uuid,entity_type="report_section")
        if section["edit_policy"] == "readonly" or section["section_type"] in {"generated", "appendix_a"}:
            raise ReportDomainError(
                "SECTION_READ_ONLY",
                "该章节的完成状态由权威数据或后续阶段生成，当前不可人工修改。",
                status_code=409,
                project_uuid=project_uuid,
                entity_type="report_section",
                entity_uuid=section_uuid,
            )
        cursor=db.execute("UPDATE report_sections SET completion_status=?,revision=revision+1,updated_at=? WHERE project_id=? AND section_uuid=? AND revision=?",(payload.completion_status,database.utc_now(),project["id"],section_uuid,payload.expected_revision))
        require_cas_updated(db,cursor,table="report_sections",project_id=int(project["id"]),expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="report_section",entity_uuid=section_uuid)
        touch_project(db,int(project["id"])); updated=db.execute("SELECT * FROM report_sections WHERE section_uuid=?",(section_uuid,)).fetchone(); return _section_result(db,updated)


def _require_string(value: Any, field: str, maximum: int, project_uuid: str) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ReportDomainError("BLOCK_PAYLOAD_INVALID", "结构化块内容格式无效。", status_code=422, project_uuid=project_uuid, field=field)
    if FORBIDDEN_CONTENT_RE.search(value):
        raise ReportDomainError("BLOCK_FORBIDDEN_CONTENT", "结构化块不允许 HTML、脚本、OOXML 或内嵌 base64 内容。", status_code=422, project_uuid=project_uuid, field=field)
    return value


def _validate_payload(project_uuid: str, block_type: str, payload: dict[str, Any], db: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    if block_type == "paragraph":
        if set(payload) != {"text"}: raise ReportDomainError("BLOCK_PAYLOAD_INVALID","段落块只能包含 text。",status_code=422,project_uuid=project_uuid,field="payload")
        _require_string(payload["text"],"payload.text",20_000,project_uuid)
    elif block_type in {"bullet_list","numbered_list"}:
        if set(payload)!={"items"} or not isinstance(payload["items"],list) or len(payload["items"])>1000: raise ReportDomainError("BLOCK_PAYLOAD_INVALID","列表块内容格式无效。",status_code=422,project_uuid=project_uuid,field="payload.items")
        for index,item in enumerate(payload["items"]): _require_string(item,f"payload.items.{index}",2000,project_uuid)
    elif block_type=="key_value_table":
        if set(payload)!={"rows"} or not isinstance(payload["rows"],list) or len(payload["rows"])>500: raise ReportDomainError("BLOCK_PAYLOAD_INVALID","键值表内容格式无效。",status_code=422,project_uuid=project_uuid,field="payload.rows")
        for index,row in enumerate(payload["rows"]):
            if not isinstance(row,dict) or set(row)!={"key","value"}: raise ReportDomainError("BLOCK_PAYLOAD_INVALID","键值表行格式无效。",status_code=422,project_uuid=project_uuid,field=f"payload.rows.{index}")
            _require_string(row["key"],f"payload.rows.{index}.key",500,project_uuid); _require_string(row["value"],f"payload.rows.{index}.value",5000,project_uuid)
    elif block_type=="data_table":
        if set(payload)!={"schema_version","columns","rows"}: raise ReportDomainError("BLOCK_PAYLOAD_INVALID","数据表内容格式无效。",status_code=422,project_uuid=project_uuid,field="payload")
        _require_string(payload["schema_version"],"payload.schema_version",40,project_uuid)
        columns=payload["columns"]; rows=payload["rows"]
        if not isinstance(columns,list) or not 1<=len(columns)<=50 or not isinstance(rows,list) or len(rows)>5000: raise ReportDomainError("BLOCK_PAYLOAD_INVALID","数据表规模或列定义无效。",status_code=422,project_uuid=project_uuid,field="payload.columns")
        keys=[]
        for index,column in enumerate(columns):
            if not isinstance(column,dict) or set(column)!={"key","label"}: raise ReportDomainError("BLOCK_PAYLOAD_INVALID","数据表列定义无效。",status_code=422,project_uuid=project_uuid,field=f"payload.columns.{index}")
            key=_require_string(column["key"],f"payload.columns.{index}.key",100,project_uuid)
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*",key) or key in keys: raise ReportDomainError("BLOCK_PAYLOAD_INVALID","数据表列键必须唯一且格式有效。",status_code=422,project_uuid=project_uuid,field=f"payload.columns.{index}.key")
            keys.append(key); _require_string(column["label"],f"payload.columns.{index}.label",200,project_uuid)
        for index,row in enumerate(rows):
            if not isinstance(row,dict) or set(row)-set(keys): raise ReportDomainError("BLOCK_PAYLOAD_INVALID","数据表行包含未知列。",status_code=422,project_uuid=project_uuid,field=f"payload.rows.{index}")
            for key,value in row.items(): _require_string(value,f"payload.rows.{index}.{key}",5000,project_uuid)
    elif block_type in {"figure","reference"}:
        target_key="figure_uuid" if block_type=="figure" else "target_uuid"
        allowed={target_key,"caption" if block_type=="figure" else "label"}
        if target_key not in payload or set(payload)-allowed: raise ReportDomainError("BLOCK_PAYLOAD_INVALID","引用块内容格式无效。",status_code=422,project_uuid=project_uuid,field=f"payload.{target_key}")
        target=_require_string(payload[target_key],f"payload.{target_key}",100,project_uuid)
        if not _same_project_target_exists(db,project_id,target,block_type=block_type): raise ReportDomainError("REPORT_REFERENCE_TARGET_INVALID","引用目标不存在或不属于当前项目。",status_code=422,project_uuid=project_uuid,field=f"payload.{target_key}")
        optional="caption" if block_type=="figure" else "label"
        if optional in payload and payload[optional] is not None: _require_string(payload[optional],f"payload.{optional}",1000,project_uuid)
    else:
        raise ReportDomainError("BLOCK_TYPE_INVALID","块类型不受支持。",status_code=422,project_uuid=project_uuid,field="block_type")
    safe_json_size(payload,maximum=262_144,project_uuid=project_uuid,field="payload")
    return payload


def _same_project_target_exists(db:sqlite3.Connection,project_id:int,target:str,*,block_type:str)->bool:
    if block_type == "figure":
        return db.execute(
            "SELECT 1 FROM evidence_images WHERE project_id=? AND evidence_uuid=?",
            (project_id,target),
        ).fetchone() is not None
    targets=(
        ("report_sections","section_uuid"),("report_blocks","block_uuid"),("assessment_objects","object_uuid"),
        ("report_organizations","organization_uuid"),("report_members","member_uuid"),
    )
    return any(db.execute(f"SELECT 1 FROM {table} WHERE project_id=? AND {column}=?",(project_id,target)).fetchone() is not None for table,column in targets)


def create_block(project_uuid:str,section_uuid:str,payload:ReportBlockCreate)->dict[str,Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); project_id=int(project["id"]); section=ensure_uuid_in_project(db,"report_sections","section_uuid",section_uuid,project_id,project_uuid=project_uuid,entity_type="report_section")
        if payload.block_type not in _allowed_types(section["section_type"],section["edit_policy"]): raise ReportDomainError("BLOCK_TYPE_NOT_ALLOWED","当前章节不允许新增该类型的块。",status_code=409,project_uuid=project_uuid,entity_type="report_section",entity_uuid=section_uuid,field="block_type")
        content=_validate_payload(project_uuid,payload.block_type,payload.payload,db,project_id); block_uuid=new_uuid(); block_key=f"manual.{block_uuid}"; timestamp=database.utc_now()
        next_order=int(db.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM report_blocks WHERE section_id=?",(section["id"],)).fetchone()[0]); sort_order=payload.sort_order if payload.sort_order is not None else next_order
        if sort_order!=next_order: raise ReportDomainError("BLOCK_ORDER_INVALID","新增块必须追加到当前章节末尾。",status_code=422,project_uuid=project_uuid,field="sort_order",details={"expected":next_order})
        try: db.execute("INSERT INTO report_blocks (block_uuid,project_id,section_id,block_key,block_type,payload_json,source_kind,edit_policy,generation_status,confirmation_status,sort_order,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,'manual','editable','not_generated','unconfirmed',?,1,?,?)",(block_uuid,project_id,section["id"],block_key,payload.block_type,dump_json(content),sort_order,timestamp,timestamp))
        except sqlite3.IntegrityError as exc: raise ReportDomainError("BLOCK_KEY_OR_ORDER_DUPLICATE","块标识或排序位置已存在。",status_code=409,project_uuid=project_uuid,field="block_key") from exc
        touch_project(db,project_id); return _block_result(db,db.execute("SELECT * FROM report_blocks WHERE block_uuid=?",(block_uuid,)).fetchone())


def update_block(project_uuid:str,block_uuid:str,payload:ReportBlockPatch)->dict[str,Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); project_id=int(project["id"]); block=ensure_uuid_in_project(db,"report_blocks","block_uuid",block_uuid,project_id,project_uuid=project_uuid,entity_type="report_block")
        if block["edit_policy"]=="readonly" or block["source_kind"] in {"derived","template_constant"}: raise ReportDomainError("BLOCK_READ_ONLY","该块由模板或后续阶段生成，当前不可修改。",status_code=409,project_uuid=project_uuid,entity_type="report_block",entity_uuid=block_uuid)
        content=_validate_payload(project_uuid,block["block_type"],payload.payload,db,project_id)
        cursor=db.execute("UPDATE report_blocks SET payload_json=?,revision=revision+1,updated_at=? WHERE project_id=? AND block_uuid=? AND revision=?",(dump_json(content),database.utc_now(),project_id,block_uuid,payload.expected_revision)); require_cas_updated(db,cursor,table="report_blocks",project_id=project_id,expected_revision=payload.expected_revision,project_uuid=project_uuid,entity_type="report_block",entity_uuid=block_uuid); touch_project(db,project_id); return _block_result(db,db.execute("SELECT * FROM report_blocks WHERE block_uuid=?",(block_uuid,)).fetchone())


def delete_block(project_uuid:str,block_uuid:str,expected_revision:int)->dict[str,Any]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); project_id=int(project["id"]); block=ensure_uuid_in_project(db,"report_blocks","block_uuid",block_uuid,project_id,project_uuid=project_uuid,entity_type="report_block")
        if block["edit_policy"]=="readonly" or block["baseline_kind"]=="template_default": raise ReportDomainError("BLOCK_READ_ONLY","模板固定块不能删除。",status_code=409,project_uuid=project_uuid,entity_type="report_block",entity_uuid=block_uuid)
        references = []
        for candidate in db.execute(
            "SELECT block_uuid,payload_json FROM report_blocks WHERE project_id=? AND block_uuid<>? AND block_type='reference'",
            (project_id, block_uuid),
        ).fetchall():
            payload = json.loads(candidate["payload_json"] or "{}")
            if payload.get("target_uuid") == block_uuid:
                references.append(candidate["block_uuid"])
        if references:
            raise ReportDomainError(
                "REPORT_ENTITY_REFERENCED",
                "该结构化块仍被其他块引用，不能删除。",
                status_code=409,
                project_uuid=project_uuid,
                entity_type="report_block",
                entity_uuid=block_uuid,
                details={"references": [{"entity_type": "report_block", "entity_uuid": value} for value in references]},
            )
        deleted=db.execute("DELETE FROM report_blocks WHERE project_id=? AND block_uuid=? AND revision=?",(project_id,block_uuid,expected_revision)); require_cas_updated(db,deleted,table="report_blocks",project_id=project_id,expected_revision=expected_revision,project_uuid=project_uuid,entity_type="report_block",entity_uuid=block_uuid); remaining=db.execute("SELECT * FROM report_blocks WHERE section_id=? ORDER BY sort_order,id",(block["section_id"],)).fetchall()
        for order,row in enumerate(remaining):
            cursor=db.execute("UPDATE report_blocks SET sort_order=?,revision=revision+1,updated_at=? WHERE project_id=? AND block_uuid=? AND revision=?",(order,database.utc_now(),project_id,row["block_uuid"],row["revision"]))
            require_cas_updated(db,cursor,table="report_blocks",project_id=project_id,expected_revision=int(row["revision"]),project_uuid=project_uuid,entity_type="report_block",entity_uuid=row["block_uuid"])
        touch_project(db,project_id); return _block_result(db,block)


def reorder_blocks(project_uuid:str,payload:BlockReorderWrite)->list[dict[str,Any]]:
    with database.connect() as db:
        project=require_report_project(project_uuid,db); project_id=int(project["id"]); section=ensure_uuid_in_project(db,"report_sections","section_uuid",payload.section_uuid,project_id,project_uuid=project_uuid,entity_type="report_section")
        rows=db.execute("SELECT * FROM report_blocks WHERE project_id=? AND section_id=? ORDER BY sort_order,id",(project_id,section["id"])).fetchall(); submitted={item.block_uuid:item for item in payload.items}
        if section["edit_policy"] == "readonly" or any(
            row["edit_policy"] == "readonly" or row["source_kind"] in {"derived", "template_constant"}
            for row in rows
        ):
            raise ReportDomainError(
                "BLOCK_REORDER_READ_ONLY",
                "当前章节包含只读或模板固定块，不能调整顺序。",
                status_code=409,
                project_uuid=project_uuid,
                entity_type="report_section",
                entity_uuid=payload.section_uuid,
            )
        if set(submitted)!={row["block_uuid"] for row in rows} or sorted(item.sort_order for item in payload.items)!=list(range(len(rows))): raise ReportDomainError("BLOCK_REORDER_SET_INVALID","批量重排必须包含当前章节全部块并使用连续顺序。",status_code=422,project_uuid=project_uuid,field="items")
        # 先使用当前最大值之后的正数腾空唯一索引，再写入连续顺序。
        temporary_base = max(int(row["sort_order"]) for row in rows) + len(rows) + 1
        for index,row in enumerate(rows,1):
            item=submitted[row["block_uuid"]]
            cursor=db.execute("UPDATE report_blocks SET sort_order=? WHERE project_id=? AND block_uuid=? AND revision=?",(temporary_base+index,project_id,row["block_uuid"],item.expected_revision))
            require_cas_updated(db,cursor,table="report_blocks",project_id=project_id,expected_revision=item.expected_revision,project_uuid=project_uuid,entity_type="report_block",entity_uuid=row["block_uuid"])
        timestamp=database.utc_now()
        for row in rows:
            item=submitted[row["block_uuid"]]
            cursor=db.execute("UPDATE report_blocks SET sort_order=?,revision=revision+1,updated_at=? WHERE project_id=? AND block_uuid=? AND revision=?",(item.sort_order,timestamp,project_id,row["block_uuid"],item.expected_revision))
            require_cas_updated(db,cursor,table="report_blocks",project_id=project_id,expected_revision=item.expected_revision,project_uuid=project_uuid,entity_type="report_block",entity_uuid=row["block_uuid"])
        touch_project(db,project_id); updated=db.execute("SELECT * FROM report_blocks WHERE section_id=? ORDER BY sort_order,id",(section["id"],)).fetchall(); return [_block_result(db,row) for row in updated]
