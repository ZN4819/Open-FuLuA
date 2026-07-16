from __future__ import annotations

import re
from typing import Any, Iterable


CONTEXT_SCHEMA_VERSION = "1.0"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SOURCE_HASH_KEYS = frozenset(
    {
        "system_summary",
        "report_facts",
        "appendix_a",
        "correction_relations",
        "risks",
        "special_indicators",
    }
)
PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "generation_run_uuid",
        "generation_state_revision",
        "rule_set_id",
        "rule_set_hash",
        "input_hash",
        "source_hashes",
        "original_projection",
        "correction_projection",
        "final_projection",
        "projection_hash",
        "findings",
        "risk_snapshot",
        "assessment_conclusion",
        "blocks",
        "threat_catalog",
    }
)
ENVELOPE_KEYS = PAYLOAD_KEYS | {"consistency", "project_revision"}


class ContextContractViolation(RuntimeError):
    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


def _fail(reason: str, **details: Any) -> None:
    raise ContextContractViolation(reason, details=details)


def _require_exact_keys(value: Any, expected: frozenset[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("TYPE_MISMATCH", location=location, expected="object")
    actual = frozenset(str(key) for key in value)
    if actual != expected:
        _fail(
            "KEY_SET_MISMATCH",
            location=location,
            missing=sorted(expected - actual),
            unexpected=sorted(actual - expected),
        )
    return value


def _require_sha256(value: Any, location: str) -> None:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        _fail("INVALID_SHA256", location=location)


def _require_projection(
    value: Any,
    location: str,
    *,
    final: bool = False,
    chapter4: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("TYPE_MISMATCH", location=location, expected="object")
    if not isinstance(value.get("rows"), list) or not isinstance(value.get("indicators"), list):
        _fail("PROJECTION_COLLECTION_INVALID", location=location)
    if len(value["indicators"]) != 41:
        _fail("INDICATOR_CARDINALITY_INVALID", location=location, actual=len(value["indicators"]))
    if chapter4:
        tables = value.get("chapter4_tables")
        expected = {f"table_4_{index}" for index in range(1, 12)}
        if not isinstance(tables, dict) or set(tables) != expected:
            _fail(
                "CHAPTER4_TABLE_SET_MISMATCH",
                location=f"{location}.chapter4_tables",
                expected=sorted(expected),
                actual=sorted(tables) if isinstance(tables, dict) else None,
            )
        for table_id, table in tables.items():
            if (
                not isinstance(table, dict)
                or table.get("projection_id") != table_id
                or not isinstance(table.get("columns"), list)
                or not isinstance(table.get("rows"), list)
                or not isinstance(table.get("summary"), dict)
                or not isinstance(table.get("render_empty_structure"), bool)
            ):
                _fail(
                    "CHAPTER4_TABLE_INVALID",
                    location=f"{location}.chapter4_tables.{table_id}",
                )
    if final:
        statistics = value.get("statistics")
        score = value.get("score")
        if not isinstance(statistics, dict) or not isinstance(score, dict):
            _fail("FINAL_SUMMARY_MISSING", location=location)
        if len(statistics.get("layers", [])) != 8:
            _fail("LAYER_CARDINALITY_INVALID", location=f"{location}.statistics.layers")
        total = statistics.get("total")
        if not isinstance(total, dict) or total.get("indicator_total") != 41:
            _fail("STATISTICS_CARDINALITY_INVALID", location=f"{location}.statistics.total")
        if not isinstance(score.get("display_score"), str):
            _fail("DISPLAY_SCORE_MISSING", location=f"{location}.score")
    return value


def validate_context_payload(
    value: Any,
    *,
    expected_block_keys: Iterable[str],
    expected_threat_ids: Iterable[str],
) -> None:
    payload = _require_exact_keys(value, PAYLOAD_KEYS, "R3ProjectionContextPayload")
    if payload["schema_version"] != CONTEXT_SCHEMA_VERSION:
        _fail("SCHEMA_VERSION_MISMATCH", actual=payload["schema_version"])
    if not isinstance(payload["generation_run_uuid"], str) or not payload["generation_run_uuid"]:
        _fail("GENERATION_RUN_MISSING")
    if not isinstance(payload["generation_state_revision"], int) or payload["generation_state_revision"] < 1:
        _fail("GENERATION_REVISION_INVALID")
    for field in ("rule_set_hash", "input_hash", "projection_hash"):
        _require_sha256(payload[field], field)
    if not isinstance(payload["rule_set_id"], str) or not payload["rule_set_id"]:
        _fail("RULE_SET_ID_MISSING")

    source_hashes = _require_exact_keys(payload["source_hashes"], SOURCE_HASH_KEYS, "source_hashes")
    for key, digest in source_hashes.items():
        _require_sha256(digest, f"source_hashes.{key}")

    _require_projection(
        payload["original_projection"],
        "original_projection",
        chapter4=True,
    )
    final_projection = _require_projection(payload["final_projection"], "final_projection", final=True)
    correction = payload["correction_projection"]
    if not isinstance(correction, dict) or not isinstance(correction.get("rows"), list) or not isinstance(
        correction.get("render_empty_as_slash_row"), bool
    ):
        _fail("CORRECTION_PROJECTION_INVALID")

    findings = payload["findings"]
    if not isinstance(findings, list):
        _fail("FINDINGS_INVALID")
    statistics = final_projection["statistics"]["total"]
    expected_risks = int(statistics["partially_compliant"]) + int(statistics["noncompliant"])
    if len(findings) != expected_risks:
        _fail("FINDING_CARDINALITY_INVALID", expected=expected_risks, actual=len(findings))

    risk_snapshot = payload["risk_snapshot"]
    if not isinstance(risk_snapshot, dict) or risk_snapshot.get("risk_total") != expected_risks:
        _fail("RISK_CARDINALITY_INVALID", expected=expected_risks)
    counts = risk_snapshot.get("counts")
    if not isinstance(counts, dict) or set(counts) != {"high", "medium", "low"}:
        _fail("RISK_COUNTS_INVALID")
    if sum(int(counts[key]) for key in ("high", "medium", "low")) != expected_risks:
        _fail("RISK_COUNT_INVARIANT_FAILED")
    expected_judgment = "判定系统存在高风险" if int(counts["high"]) else "判定系统不存在高风险"
    if risk_snapshot.get("high_risk_judgment") != expected_judgment:
        _fail("HIGH_RISK_JUDGMENT_INVALID")
    if not isinstance(risk_snapshot.get("rows"), list) or len(risk_snapshot["rows"]) != expected_risks:
        _fail("RISK_ROWS_INVALID")

    conclusion = payload["assessment_conclusion"]
    if not isinstance(conclusion, dict):
        _fail("ASSESSMENT_CONCLUSION_INVALID")
    if conclusion.get("display_score") != final_projection["score"]["display_score"]:
        _fail("CONCLUSION_SCORE_MISMATCH")
    for field in ("overall_risk", "high_risk_judgment"):
        if conclusion.get(field) != risk_snapshot.get(field):
            _fail("CONCLUSION_RISK_MISMATCH", field=field)

    blocks = payload["blocks"]
    expected_blocks = tuple(expected_block_keys)
    if not isinstance(blocks, list):
        _fail("BLOCKS_INVALID")
    actual_blocks = [block.get("block_key") for block in blocks if isinstance(block, dict)]
    if len(actual_blocks) != len(blocks) or set(actual_blocks) != set(expected_blocks) or len(blocks) != len(expected_blocks):
        _fail("BLOCK_SET_MISMATCH", expected=list(expected_blocks), actual=actual_blocks)
    for block in blocks:
        if set(block) != {"block_uuid", "block_key", "effective", "rule_id", "source_hash"}:
            _fail("BLOCK_SHAPE_INVALID", block_key=block.get("block_key"))
        _require_sha256(block["source_hash"], f"blocks.{block['block_key']}.source_hash")

    threats = payload["threat_catalog"]
    expected_threats = tuple(expected_threat_ids)
    if not isinstance(threats, list):
        _fail("THREAT_CATALOG_INVALID")
    actual_threats = [item.get("id") for item in threats if isinstance(item, dict)]
    if actual_threats != list(expected_threats):
        _fail("THREAT_CATALOG_MISMATCH", expected=list(expected_threats), actual=actual_threats)


def validate_context_envelope(
    value: Any,
    *,
    expected_block_keys: Iterable[str],
    expected_threat_ids: Iterable[str],
) -> None:
    envelope = _require_exact_keys(value, ENVELOPE_KEYS, "R3ProjectionContext")
    payload = {key: envelope[key] for key in PAYLOAD_KEYS}
    validate_context_payload(
        payload,
        expected_block_keys=expected_block_keys,
        expected_threat_ids=expected_threat_ids,
    )
    consistency = envelope["consistency"]
    expected_consistency_keys = frozenset(
        {"check_uuid", "run_uuid", "status", "issues", "context_hash", "state_revision", "checked_at"}
    )
    consistency = _require_exact_keys(consistency, expected_consistency_keys, "consistency")
    if consistency["status"] != "valid" or consistency["issues"] != []:
        _fail("CONSISTENCY_NOT_VALID")
    if consistency["run_uuid"] != payload["generation_run_uuid"]:
        _fail("CONSISTENCY_RUN_MISMATCH")
    _require_sha256(consistency["context_hash"], "consistency.context_hash")
    if not isinstance(envelope["project_revision"], int) or envelope["project_revision"] < 1:
        _fail("PROJECT_REVISION_INVALID")
    if consistency["state_revision"] != envelope["project_revision"]:
        _fail("CONSISTENCY_REVISION_MISMATCH")
