"""Build the signed, column-level R7 writable contract from an R4 context."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from ..report_derived.rules import canonical_json
from .contracts import (
    block_token,
    normalize_value,
    projection_group,
    roundtrip_policy,
    row_token,
    slot_id,
    slot_tag,
    value_hash,
)
from .manifest import compute_writable_contract_hash
from .structure import extract_roundtrip_structure, is_comment_part, readonly_document_hash


TECHNICAL = {"A-1", "A-2", "A-3", "A-4"}
SECTION_TABLE = {f"A-{index}": f"report_table_{38 + index:03d}" for index in range(1, 9)}


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _slot(
    *,
    token: str,
    tag: str,
    authority_field_id: str,
    entity_path: str,
    value_type: str,
    normalizer_id: str,
    value: Any,
    projection: str,
    binding_kind: str,
    binding_key: str,
    row_uuid: str | None = None,
    section_code: str | None = None,
    column_id: str | None = None,
    options: list[str] | None = None,
) -> dict[str, Any]:
    item = {
        "slot_id": token,
        "tag": tag,
        "authority_field_id": authority_field_id,
        "entity_path": entity_path,
        "value_type": value_type,
        "normalizer_id": normalizer_id,
        "projection_group": projection,
        "value": normalize_value(value, normalizer_id, options=options),
        "binding_kind": binding_kind,
        "binding_key": binding_key,
        "row_uuid": row_uuid,
        "section_code": section_code,
        "column_id": column_id,
        "options": options or [],
    }
    item["value_hash"] = value_hash(item, item["value"])
    return item


def build_writable_contract(
    context: dict[str, Any],
    *,
    document_parts: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    if not context["project_identity"].get("roundtrip_capable"):
        raise ValueError("ROUNDTRIP_CONTEXT_REQUIRED")
    policy = roundtrip_policy()
    scalars = context["scalar_slot_values"]
    slots: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    for item in policy["scalar_slots"]:
        authority = str(item["authority_field_id"])
        entity_path = str(item["entity_path"])
        group = projection_group(authority, entity_path)
        for semantic_tag in item["slot_tags"]:
            token = slot_id("scalar", semantic_tag)
            slots.append(
                _slot(
                    token=token,
                    tag=slot_tag(token),
                    authority_field_id=authority,
                    entity_path=entity_path,
                    value_type=str(item["value_type"]),
                    normalizer_id=str(item["normalizer_id"]),
                    value=scalars.get(item["context_key"], ""),
                    projection=group,
                    binding_kind="scalar",
                    binding_key=authority,
                )
            )

    columns = {str(item["column_id"]): item for item in policy["appendix_a_columns"]}
    section_positions: dict[str, int] = defaultdict(int)
    for source in context["appendix_a_final_projection"].get("rows", []):
        section = str(source.get("section_code") or "")
        row_uuid = str(source.get("source_row_uuid") or "")
        object_uuid = str(source.get("object_uuid") or "")
        if section not in SECTION_TABLE or not row_uuid:
            raise ValueError("ROUNDTRIP_ROW_IDENTITY_MISSING")
        section_positions[section] += 1
        writable_slot_ids: list[str] = []
        corrected = bool(source.get("was_corrected"))
        column_values: dict[str, Any] = {
            "object_name": source.get("object_name") or "",
            "subsystem": source.get("subsystem") or "",
            "record_text": source.get("record_text") or "",
            "d": source.get("d"),
            "a": source.get("a"),
            "k": source.get("k"),
            "compliance": source.get("object_result") or "不适用",
        }
        allowed_columns = ["object_name", "record_text"]
        if "[[FIG:" in str(column_values["record_text"]):
            allowed_columns.remove("record_text")
        if section in TECHNICAL and not corrected:
            allowed_columns.extend(["d", "a", "k"])
        if section not in TECHNICAL:
            allowed_columns.append("compliance")
        # The current frozen Appendix A table has no subsystem column.  It is
        # deliberately not invented or inferred from another visible cell.
        for column_id in allowed_columns:
            item = columns[column_id]
            token = slot_id(row_uuid, column_id)
            authority = str(item["authority_field_id"])
            if column_id == "object_name":
                entity_path = f"assessment_objects[{object_uuid}].name_snapshot"
                group = projection_group(authority, entity_path)
                binding_kind, binding_key = "object_name", object_uuid
            else:
                entity_path = str(item["entity_path"]).replace("[*]", f"[{row_uuid}]")
                group = projection_group(authority, entity_path)
                binding_kind, binding_key = "assessment_row", row_uuid
            options = list(item.get("options") or [])
            slots.append(
                _slot(
                    token=token,
                    tag=slot_tag(token),
                    authority_field_id=authority,
                    entity_path=entity_path,
                    value_type=str(item["value_type"]),
                    normalizer_id=str(item["normalizer_id"]),
                    value=column_values[column_id],
                    projection=group,
                    binding_kind=binding_kind,
                    binding_key=binding_key,
                    row_uuid=row_uuid,
                    section_code=section,
                    column_id=column_id,
                    options=options,
                )
            )
            writable_slot_ids.append(token)
        immutable = {
            "row_uuid": row_uuid,
            "section_code": section,
            "indicator_code": source.get("indicator_code"),
            "indicator_name": source.get("indicator_name"),
            "object_uuid": object_uuid,
            "final_object_score": source.get("final_object_score") or source.get("object_score"),
            "unit_score": source.get("unit_score"),
        }
        geometry = {
            "table_id": SECTION_TABLE[section],
            "logical_columns": 8 if section in TECHNICAL else 5,
        }
        rows.append(
            {
                "row_id": row_uuid,
                "row_token": row_token(row_uuid),
                "block_token": block_token(section),
                "table_id": SECTION_TABLE[section],
                "sort_order": section_positions[section],
                "writable_slot_ids": writable_slot_ids,
                "immutable_value_hash": _canonical_hash(immutable),
                "geometry_hash": _canonical_hash(geometry),
            }
        )

    readonly_hash = "0" * 64
    if document_parts is not None:
        structure = extract_roundtrip_structure(document_parts)
        extracted_slots = {item.token: item for item in structure.slots}
        if set(extracted_slots) != {item["slot_id"] for item in slots}:
            raise ValueError("ROUNDTRIP_SLOT_SET_MISMATCH")
        extracted_rows = {item.token: item for item in structure.rows}
        if set(extracted_rows) != {item["row_token"] for item in rows}:
            raise ValueError("ROUNDTRIP_ROW_SET_MISMATCH")
        expected_blocks = {item["block_token"] for item in rows}
        if {item.token for item in structure.blocks} != expected_blocks:
            raise ValueError("ROUNDTRIP_BLOCK_SET_MISMATCH")
        for row in rows:
            extracted = extracted_rows[row["row_token"]]
            if tuple(row["writable_slot_ids"]) != extracted.slot_tokens:
                raise ValueError("ROUNDTRIP_ROW_SLOT_ORDER_MISMATCH")
            row["geometry_hash"] = extracted.geometry_hash
        readonly_hash = readonly_document_hash(document_parts)

    manifest_fields = [
        {key: item[key] for key in (
            "slot_id", "authority_field_id", "entity_path", "value_type",
            "normalizer_id", "projection_group",
        )}
        for item in slots
    ]
    baseline_hashes = {item["slot_id"]: item["value_hash"] for item in slots}
    structure_contract = {
        "schema_version": "1.0",
        "slot_tags": [item["tag"] for item in slots],
        "rows": rows,
        "table_order": [SECTION_TABLE[f"A-{index}"] for index in range(1, 9)],
        "readonly_document_hash": readonly_hash,
        "media_hashes": {
            name: hashlib.sha256(data).hexdigest()
            for name, data in sorted((document_parts or {}).items())
            if name.startswith("word/media/")
        },
        "comment_hashes": {
            name: hashlib.sha256(data).hexdigest()
            for name, data in sorted((document_parts or {}).items())
            if is_comment_part(name)
        },
    }
    structure_hash = _canonical_hash(structure_contract)
    writable_hash = compute_writable_contract_hash(manifest_fields, rows, baseline_hashes)
    baseline = {
        "schema_version": "1.0",
        "project_uuid": context["project_identity"]["project_uuid"],
        "project_revision": context["project_identity"]["project_revision"],
        "slots": slots,
        "rows": rows,
        "structure_contract": structure_contract,
        "structure_contract_hash": structure_hash,
        "writable_contract_hash": writable_hash,
    }
    baseline["baseline_hash"] = _canonical_hash(baseline)
    return {
        "baseline": baseline,
        "writable_fields": manifest_fields,
        "writable_rows": rows,
        "baseline_value_hashes": baseline_hashes,
        "writable_contract_hash": writable_hash,
        "structure_contract_hash": structure_hash,
    }
