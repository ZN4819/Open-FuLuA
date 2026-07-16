"""R7 writable projection contract helpers.

The machine-readable policy lives in the R2 field matrix.  This module only
derives opaque SDT tokens and normalized value hashes from that policy.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from ..report_core.field_matrix import load_default_field_matrix


ROUNDTRIP_TAG_PREFIX = "fla:r7:v1"
REPORT_ROUNDTRIP_TABLES = (
    "report_roundtrip_manifests",
    "report_sync_conflicts",
    "report_import_audits",
    "report_roundtrip_deletion_tombstones",
    "report_roundtrip_cleanup_queue",
)


def roundtrip_policy() -> dict[str, Any]:
    return load_default_field_matrix().roundtrip_policy


def _matches_entity_path(pattern: str, value: str) -> bool:
    expression = re.escape(pattern).replace(r"\[\*\]", r"\[[^\[\]]+\]")
    return re.fullmatch(expression, value) is not None


def validate_slots_against_current_policy(slots: list[dict[str, Any]]) -> None:
    """Reject an issued contract when today's matrix has narrowed access.

    A signed manifest proves what was writable when the draft was issued.  It
    does not grant that permission forever: upload must also satisfy the
    current R2 matrix, and an old draft containing a revoked slot is rejected
    as a whole so the user can export a fresh contract.
    """

    policy = roundtrip_policy()
    scalar_slots: dict[str, dict[str, Any]] = {}
    for item in policy["scalar_slots"]:
        for semantic_tag in item["slot_tags"]:
            scalar_slots[slot_id("scalar", semantic_tag)] = item
    appendix_columns = {
        str(item["column_id"]): item for item in policy["appendix_a_columns"]
    }

    for slot in slots:
        binding_kind = str(slot.get("binding_kind") or "")
        column_id = str(slot.get("column_id") or "")
        if binding_kind == "scalar":
            current = scalar_slots.get(str(slot.get("slot_id") or ""))
            expected_binding = "scalar"
        else:
            current = appendix_columns.get(column_id)
            expected_binding = "object_name" if column_id == "object_name" else "assessment_row"
        if current is None or binding_kind != expected_binding:
            raise ValueError("ROUNDTRIP_CURRENT_POLICY_REVOKED")
        if (
            str(slot.get("authority_field_id") or "") != str(current["authority_field_id"])
            or str(slot.get("value_type") or "") != str(current["value_type"])
            or str(slot.get("normalizer_id") or "") != str(current["normalizer_id"])
            or list(slot.get("options") or []) != list(current.get("options") or [])
            or not _matches_entity_path(
                str(current["entity_path"]), str(slot.get("entity_path") or "")
            )
        ):
            raise ValueError("ROUNDTRIP_CURRENT_POLICY_REVOKED")
        if binding_kind != "scalar" and str(slot.get("section_code") or "") not in {
            str(section) for section in current.get("sections") or []
        }:
            raise ValueError("ROUNDTRIP_CURRENT_POLICY_REVOKED")


def opaque_token(*parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(b"Open-FuLuA.R7.Token.v1\x00" + material).hexdigest()[:24]


def slot_id(*parts: object) -> str:
    return opaque_token("slot", *parts)


def slot_tag(token: str) -> str:
    return f"{ROUNDTRIP_TAG_PREFIX}:s:{token}"


def row_token(row_uuid: str) -> str:
    return opaque_token("row", row_uuid)


def block_token(section_code: str) -> str:
    return opaque_token("block", section_code)


def projection_group(authority_field_id: str, entity_path: str) -> str:
    return opaque_token("projection", authority_field_id, entity_path)


def normalize_value(value: Any, normalizer_id: str, *, options: list[str] | None = None) -> str:
    text = unicodedata.normalize("NFC", str(value if value is not None else ""))
    if normalizer_id == "exact_v1":
        result = text
    elif normalizer_id == "trim_v1":
        result = re.sub(r"[ \t]+", " ", text).strip()
    elif normalizer_id == "multiline_v1":
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        while lines and not lines[-1]:
            lines.pop()
        result = "\n".join(lines)
    elif normalizer_id == "date_iso_v1":
        result = text.strip()[:10]
        if result and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", result):
            raise ValueError("ROUNDTRIP_DATE_INVALID")
    elif normalizer_id == "enum_v1":
        result = text.strip()
        if options is not None and result not in options:
            raise ValueError("ROUNDTRIP_ENUM_INVALID")
    else:
        raise ValueError("ROUNDTRIP_NORMALIZER_UNSUPPORTED")
    return result


def value_hash(slot: dict[str, Any], value: Any) -> str:
    normalized = normalize_value(
        value,
        str(slot["normalizer_id"]),
        options=list(slot.get("options") or []) or None,
    )
    envelope = {
        "slot_id": slot["slot_id"],
        "authority_field_id": slot["authority_field_id"],
        "entity_path": slot["entity_path"],
        "value_type": slot["value_type"],
        "normalizer_id": slot["normalizer_id"],
        "value": normalized,
    }
    raw = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
