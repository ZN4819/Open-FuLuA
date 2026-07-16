"""Strict canonical manifest schema and domain-separated HMAC signing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Mapping

from .keys import RoundtripSigningKey


MANIFEST_VERSION = "1"
SIGNATURE_ALGORITHM = "HMAC-SHA256"
HMAC_DOMAIN = b"Open-FuLuA.Roundtrip.Manifest.v1\x00"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_INTEGER = 2**63 - 1
MIN_INTEGER = -(2**63)

_HASH = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,48}$")
_FIELD_ID = re.compile(r"^report\.[a-z0-9_]+(?:\.[a-z0-9_]+)+$")
_TABLE_ID = re.compile(r"^report_table_[0-9]{3}$")
_SIGNATURE = re.compile(r"^[A-Za-z0-9_-]{43}$")

_VALUE_TYPES = frozenset({"text", "multiline", "date", "enum"})
_NORMALIZERS = frozenset({"exact_v1", "trim_v1", "multiline_v1", "date_iso_v1", "enum_v1"})

_FIELD_KEYS = frozenset(
    {
        "slot_id",
        "authority_field_id",
        "entity_path",
        "value_type",
        "normalizer_id",
        "projection_group",
    }
)
_ROW_KEYS = frozenset(
    {
        "row_id",
        "row_token",
        "block_token",
        "table_id",
        "sort_order",
        "writable_slot_ids",
        "immutable_value_hash",
        "geometry_hash",
    }
)
_CORE_KEYS = frozenset(
    {
        "manifest_version",
        "signature_algorithm",
        "key_id",
        "document_instance_id",
        "project_uuid",
        "project_type",
        "export_job_uuid",
        "snapshot_uuid",
        "project_revision",
        "template_package_id",
        "template_edition",
        "template_revision",
        "template_hash",
        "field_dictionary_hash",
        "snapshot_hash",
        "writable_contract_hash",
        "structure_contract_hash",
        "scoring_engine_version",
        "issued_at",
        "roundtrip_capable",
        "export_mode",
        "writable_fields",
        "writable_rows",
        "baseline_value_hashes",
    }
)
_SIGNED_KEYS = _CORE_KEYS | {"manifest_hash", "signature"}


class ManifestSecurityError(ValueError):
    def __init__(self, code: str, *, location: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.location = location


def _fail(code: str, location: str | None = None) -> None:
    raise ManifestSecurityError(code, location=location)


def _normalize_str(value: str, location: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    try:
        normalized.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ManifestSecurityError("MANIFEST_STRING_INVALID", location=location) from exc
    return normalized


def _strict_normalize(value: Any, *, location: str = "$") -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not MIN_INTEGER <= value <= MAX_INTEGER:
            _fail("MANIFEST_INTEGER_OUT_OF_RANGE", location)
        return value
    if type(value) is str:
        return _normalize_str(value, location)
    if type(value) is list:
        return [
            _strict_normalize(item, location=f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
    if type(value) is dict:
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                _fail("MANIFEST_OBJECT_KEY_INVALID", location)
            normalized_key = _normalize_str(key, location)
            if normalized_key in normalized:
                _fail("MANIFEST_DUPLICATE_KEY", f"{location}.{normalized_key}")
            normalized[normalized_key] = _strict_normalize(
                item,
                location=f"{location}.{normalized_key}",
            )
        return normalized
    _fail("MANIFEST_JSON_TYPE_INVALID", location)


def canonical_json_bytes(value: Any) -> bytes:
    normalized = _strict_normalize(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("MANIFEST_DUPLICATE_KEY", key)
        value[key] = item
    return value


def _reject_number(value: str) -> Any:
    _fail("MANIFEST_JSON_NUMBER_INVALID", value)


def parse_manifest_json(raw: bytes | str) -> dict[str, Any]:
    data = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    if len(data) > MAX_MANIFEST_BYTES:
        _fail("MANIFEST_SIZE_LIMIT_EXCEEDED")
    try:
        text = data.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except ManifestSecurityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestSecurityError("MANIFEST_JSON_INVALID") from exc
    if type(value) is not dict:
        _fail("MANIFEST_ROOT_INVALID")
    normalized = _strict_normalize(value)
    _validate_signed_manifest(normalized)
    if canonical_json_bytes(normalized) != data:
        _fail("MANIFEST_JSON_NOT_CANONICAL")
    return normalized


def _require_exact_keys(value: dict[str, Any], expected: frozenset[str], location: str) -> None:
    if set(value) != set(expected):
        _fail("MANIFEST_SCHEMA_FIELDS_INVALID", location)


def _require_string(value: Any, location: str, *, max_length: int = 512) -> str:
    if type(value) is not str or not value or len(value) > max_length:
        _fail("MANIFEST_VALUE_INVALID", location)
    return value


def _require_uuid(value: Any, location: str) -> str:
    text = _require_string(value, location, max_length=36)
    try:
        parsed = uuid.UUID(text)
    except ValueError as exc:
        raise ManifestSecurityError("MANIFEST_UUID_INVALID", location=location) from exc
    if str(parsed) != text.lower():
        _fail("MANIFEST_UUID_NOT_CANONICAL", location)
    return text


def _require_hash(value: Any, location: str) -> str:
    text = _require_string(value, location, max_length=64)
    if not _HASH.fullmatch(text):
        _fail("MANIFEST_HASH_INVALID", location)
    return text


def _require_token(value: Any, location: str) -> str:
    text = _require_string(value, location, max_length=48)
    if not _TOKEN.fullmatch(text):
        _fail("MANIFEST_TOKEN_INVALID", location)
    return text


def _validate_writable_fields(value: Any) -> list[dict[str, Any]]:
    if type(value) is not list:
        _fail("MANIFEST_WRITABLE_FIELDS_INVALID", "writable_fields")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        location = f"writable_fields[{index}]"
        if type(raw) is not dict:
            _fail("MANIFEST_WRITABLE_FIELD_INVALID", location)
        _require_exact_keys(raw, _FIELD_KEYS, location)
        slot_id = _require_token(raw["slot_id"], f"{location}.slot_id")
        if slot_id in seen:
            _fail("MANIFEST_SLOT_DUPLICATE", f"{location}.slot_id")
        seen.add(slot_id)
        authority = _require_string(raw["authority_field_id"], f"{location}.authority_field_id")
        if not _FIELD_ID.fullmatch(authority):
            _fail("MANIFEST_AUTHORITY_FIELD_INVALID", f"{location}.authority_field_id")
        _require_string(raw["entity_path"], f"{location}.entity_path")
        if raw["value_type"] not in _VALUE_TYPES:
            _fail("MANIFEST_VALUE_TYPE_INVALID", f"{location}.value_type")
        if raw["normalizer_id"] not in _NORMALIZERS:
            _fail("MANIFEST_NORMALIZER_INVALID", f"{location}.normalizer_id")
        _require_token(raw["projection_group"], f"{location}.projection_group")
        result.append(raw)
    return result


def _validate_writable_rows(
    value: Any,
    *,
    known_slots: set[str],
) -> list[dict[str, Any]]:
    if type(value) is not list:
        _fail("MANIFEST_WRITABLE_ROWS_INVALID", "writable_rows")
    result: list[dict[str, Any]] = []
    row_ids: set[str] = set()
    row_tokens: set[str] = set()
    positions: set[tuple[str, int]] = set()
    owned_slots: set[str] = set()
    for index, raw in enumerate(value):
        location = f"writable_rows[{index}]"
        if type(raw) is not dict:
            _fail("MANIFEST_WRITABLE_ROW_INVALID", location)
        _require_exact_keys(raw, _ROW_KEYS, location)
        row_id = _require_uuid(raw["row_id"], f"{location}.row_id")
        row_token = _require_token(raw["row_token"], f"{location}.row_token")
        block_token = _require_token(raw["block_token"], f"{location}.block_token")
        table_id = _require_string(raw["table_id"], f"{location}.table_id")
        if not _TABLE_ID.fullmatch(table_id):
            _fail("MANIFEST_TABLE_ID_INVALID", f"{location}.table_id")
        sort_order = raw["sort_order"]
        if type(sort_order) is not int or sort_order < 1:
            _fail("MANIFEST_ROW_ORDER_INVALID", f"{location}.sort_order")
        if row_id in row_ids or row_token in row_tokens or (block_token, sort_order) in positions:
            _fail("MANIFEST_ROW_IDENTITY_DUPLICATE", location)
        row_ids.add(row_id)
        row_tokens.add(row_token)
        positions.add((block_token, sort_order))

        slots = raw["writable_slot_ids"]
        if type(slots) is not list or any(type(item) is not str for item in slots):
            _fail("MANIFEST_ROW_SLOTS_INVALID", f"{location}.writable_slot_ids")
        if len(slots) != len(set(slots)) or any(item not in known_slots for item in slots):
            _fail("MANIFEST_ROW_SLOTS_INVALID", f"{location}.writable_slot_ids")
        if owned_slots.intersection(slots):
            _fail("MANIFEST_ROW_SLOT_OWNERSHIP_DUPLICATE", f"{location}.writable_slot_ids")
        owned_slots.update(slots)
        _require_hash(raw["immutable_value_hash"], f"{location}.immutable_value_hash")
        _require_hash(raw["geometry_hash"], f"{location}.geometry_hash")
        result.append(raw)
    return result


def compute_writable_contract_hash(
    writable_fields: list[dict[str, Any]],
    writable_rows: list[dict[str, Any]],
    baseline_value_hashes: dict[str, str],
) -> str:
    value = {
        "writable_fields": writable_fields,
        "writable_rows": writable_rows,
        "baseline_value_hashes": baseline_value_hashes,
    }
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _validate_core_manifest(value: dict[str, Any]) -> None:
    _require_exact_keys(value, _CORE_KEYS, "manifest")
    if value["manifest_version"] != MANIFEST_VERSION:
        _fail("MANIFEST_SCHEMA_UNSUPPORTED", "manifest_version")
    if value["signature_algorithm"] != SIGNATURE_ALGORITHM:
        _fail("MANIFEST_SIGNATURE_ALGORITHM_INVALID", "signature_algorithm")
    _require_hash(value["key_id"], "key_id")
    for field in ("document_instance_id", "project_uuid", "export_job_uuid", "snapshot_uuid"):
        _require_uuid(value[field], field)
    if value["project_type"] != "full_report":
        _fail("MANIFEST_PROJECT_TYPE_INVALID", "project_type")
    if value["export_mode"] != "draft" or value["roundtrip_capable"] is not True:
        _fail("MANIFEST_ROUNDTRIP_MODE_INVALID", "export_mode")
    if type(value["project_revision"]) is not int or value["project_revision"] < 1:
        _fail("MANIFEST_PROJECT_REVISION_INVALID", "project_revision")
    for field in (
        "template_package_id",
        "template_edition",
        "template_revision",
        "scoring_engine_version",
    ):
        _require_string(value[field], field)
    for field in (
        "template_hash",
        "field_dictionary_hash",
        "snapshot_hash",
        "writable_contract_hash",
        "structure_contract_hash",
    ):
        _require_hash(value[field], field)
    issued_at = _require_string(value["issued_at"], "issued_at", max_length=64)
    try:
        timestamp = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestSecurityError("MANIFEST_TIMESTAMP_INVALID", location="issued_at") from exc
    if timestamp.tzinfo is None:
        _fail("MANIFEST_TIMESTAMP_INVALID", "issued_at")

    fields = _validate_writable_fields(value["writable_fields"])
    slot_ids = {item["slot_id"] for item in fields}
    rows = _validate_writable_rows(value["writable_rows"], known_slots=slot_ids)
    baselines = value["baseline_value_hashes"]
    if type(baselines) is not dict or set(baselines) != slot_ids:
        _fail("MANIFEST_BASELINE_HASH_SET_INVALID", "baseline_value_hashes")
    for slot_id, digest in baselines.items():
        _require_hash(digest, f"baseline_value_hashes.{slot_id}")
    expected_contract = compute_writable_contract_hash(fields, rows, baselines)
    if not hmac.compare_digest(expected_contract, value["writable_contract_hash"]):
        _fail("MANIFEST_WRITABLE_CONTRACT_HASH_MISMATCH", "writable_contract_hash")


def _validate_signed_manifest(value: dict[str, Any]) -> None:
    _require_exact_keys(value, _SIGNED_KEYS, "manifest")
    core = {key: value[key] for key in _CORE_KEYS}
    _validate_core_manifest(core)
    _require_hash(value["manifest_hash"], "manifest_hash")
    signature = _require_string(value["signature"], "signature", max_length=43)
    if not _SIGNATURE.fullmatch(signature):
        _fail("MANIFEST_SIGNATURE_ENCODING_INVALID", "signature")


def _signature_value(key: bytes, signed_payload: dict[str, Any]) -> str:
    digest = hmac.new(
        key,
        HMAC_DOMAIN + canonical_json_bytes(signed_payload),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_signed_manifest(
    core: Mapping[str, Any],
    signing_key: RoundtripSigningKey,
) -> dict[str, Any]:
    value = dict(core)
    if "manifest_hash" in value or "signature" in value:
        _fail("MANIFEST_SIGNED_FIELDS_PRESET")
    if value.get("key_id") not in (None, signing_key.key_id):
        _fail("MANIFEST_KEY_ID_MISMATCH", "key_id")
    value["key_id"] = signing_key.key_id
    if value.get("signature_algorithm") not in (None, SIGNATURE_ALGORITHM):
        _fail("MANIFEST_SIGNATURE_ALGORITHM_INVALID", "signature_algorithm")
    value["signature_algorithm"] = SIGNATURE_ALGORITHM
    normalized = _strict_normalize(value)
    _validate_core_manifest(normalized)
    manifest_hash = hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()
    signed_payload = {**normalized, "manifest_hash": manifest_hash}
    manifest = {
        **signed_payload,
        "signature": _signature_value(signing_key.material, signed_payload),
    }
    _validate_signed_manifest(manifest)
    return manifest


def verify_signed_manifest(
    manifest: Mapping[str, Any],
    keyring: Mapping[str, RoundtripSigningKey],
) -> dict[str, Any]:
    normalized = _strict_normalize(dict(manifest))
    _validate_signed_manifest(normalized)
    core = {key: normalized[key] for key in _CORE_KEYS}
    expected_hash = hashlib.sha256(canonical_json_bytes(core)).hexdigest()
    if not hmac.compare_digest(expected_hash, normalized["manifest_hash"]):
        _fail("MANIFEST_HASH_MISMATCH", "manifest_hash")
    key = keyring.get(normalized["key_id"])
    if key is None or key.key_id != normalized["key_id"]:
        _fail("MANIFEST_SIGNING_KEY_UNAVAILABLE", "key_id")
    signed_payload = {**core, "manifest_hash": normalized["manifest_hash"]}
    expected_signature = _signature_value(key.material, signed_payload)
    if not hmac.compare_digest(expected_signature, normalized["signature"]):
        _fail("MANIFEST_SIGNATURE_INVALID", "signature")
    return normalized
