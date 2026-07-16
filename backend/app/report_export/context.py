"""Build the immutable, renderer-only context for a complete report export.

The builder consumes the stable R2 context service and the already-confirmed
R3 projection at one project revision, then hands a plain JSON-compatible
object to the DOCX renderer.  Neither this assembly layer nor the renderer
queries business tables or recalculates business rules while formatting.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

from ..config import settings
from ..report_core.field_matrix import load_default_field_matrix
from ..report_derived.rules import canonical_json, load_default_rule_set, stable_hash
from ..services import report_context, report_generation
from ..services.report_domain import validation as report_validation
from ..services.report_domain.errors import ReportDomainError
from ..services.report_templates.registry import report_template_registry


ExportMode = Literal["draft", "final"]


def _date(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else text


def _period(start: Any, end: Any) -> str:
    first, last = _date(start), _date(end)
    if first and last:
        return f"{first} 至 {last}"
    return first or last


def _issue(
    severity: Literal["error", "warning", "info"],
    code: str,
    message: str,
    **location: Any,
) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, **location}


def _data_table_rows(blocks: dict[str, list[dict[str, Any]]], section_key: str) -> list[dict[str, Any]]:
    for block in blocks.get(section_key, []):
        if block["block_type"] != "data_table":
            continue
        payload = block["payload"]
        columns = payload.get("columns", [])
        keys = [str(item.get("key") or "") for item in columns if isinstance(item, dict)]
        rows: list[dict[str, Any]] = []
        for source in payload.get("rows", []):
            if isinstance(source, dict):
                row = {key: str(source.get(key) or "") for key in keys}
                row["__values__"] = [row[key] for key in keys]
                rows.append(row)
        return rows
    return []


def _empty_r3(reason: ReportDomainError | None = None) -> dict[str, Any]:
    rules = load_default_rule_set()
    return {
        "schema_version": "1.0",
        "status": "unavailable",
        "reason": reason.code if reason else "R3_CONTEXT_NOT_AVAILABLE",
        "rule_set_id": rules.rule_set_id,
        "rule_set_hash": rules.content_sha256,
        "original_projection": {"rows": [], "indicators": [], "chapter4_tables": {}},
        "correction_projection": {"rows": []},
        "final_projection": {
            "rows": [], "indicators": [],
            "statistics": {"layers": [], "total": {"indicator_total": 0, "compliant": 0, "partially_compliant": 0, "noncompliant": 0, "not_applicable": 0}},
            "score": {"display_score": ""},
        },
        "blocks": [],
        "threat_catalog": list(rules.threat_catalog),
    }


def _block_map(r3: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["block_key"]): dict(item.get("effective") or {})
        for item in r3.get("blocks", [])
        if isinstance(item, dict) and item.get("block_key")
    }


def _object_table_rows(r2: dict[str, Any], r3: dict[str, Any], section_code: str, *, subsystem: bool = False) -> list[dict[str, Any]]:
    rows = [item for item in r3.get("final_projection", {}).get("rows", []) if item.get("section_code") == section_code]
    names: dict[str, str] = {}
    for row in rows:
        key = str(row.get("object_uuid") or "")
        names.setdefault(key, str(row.get("object_name") or key))
    bindings = {str(item["object_uuid"]): item for item in r2["subsystems"]}
    output: dict[str, dict[str, Any]] = {}
    for object_uuid, object_name in sorted(names.items(), key=lambda item: (item[1], item[0])):
        binding = bindings.get(object_uuid, {})
        display = str(binding.get("subsystem_name") or "") if subsystem else object_name
        if not display:
            continue
        output.setdefault(
            display,
            {
                "object_uuid": object_uuid,
                "object_name": display,
                "methods": list(binding.get("assessment_methods") or ["访谈", "文档审查"]),
                "remark": str(binding.get("remark") or ""),
            },
        )
    return list(output.values())


def _table_rows(r2: dict[str, Any], r3: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    blocks = r2["manual_blocks"]
    section_by_table = {
        5: "chapter.2.4.1", 6: "chapter.2.4.2", 7: "chapter.2.4.3",
        8: "chapter.2.4.4", 9: "chapter.2.4.5", 10: "chapter.2.4.6",
        11: "chapter.2.4.7", 12: "chapter.2.4.8", 13: "chapter.2.4.9",
        14: "chapter.2.4.10", 15: "chapter.2.5", 19: "chapter.3.2.2",
    }
    output = {
        f"report_table_{number:03d}": _data_table_rows(blocks, section)
        for number, section in section_by_table.items()
    }
    output["report_table_004"] = [
        {
            "number": index,
            "name": item.get("name", ""),
            "role": "项目负责人" if item.get("is_project_leader") else str(item.get("team_role") or "组员"),
            "qualification": _date(item.get("qualification_passed_at")),
        }
        for index, item in enumerate(r2["members"], start=1)
    ]
    algorithms = "、".join(str(value) for value in r2["profile"].get("selected_algorithms", []))
    mode_labels = {"exclusive": "独立使用", "shared": "共享使用"}
    output["report_table_007"] = [
        {
            "number": index,
            "product_name": item.get("product_name", ""),
            "manufacturer_model": " ".join(
                value for value in (str(item.get("manufacturer") or "").strip(), str(item.get("model") or "").strip())
                if value
            ),
            "certificate_number": item.get("certificate_number", ""),
            "algorithms": algorithms,
            "quantity_text": item.get("quantity_text", ""),
            "purpose": mode_labels.get(str(item.get("use_mode") or ""), item.get("use_mode", "")),
        }
        for index, item in enumerate(r2["products"], start=1)
    ]
    manual_applications = output.get("report_table_011", [])
    application_details: dict[str, dict[str, Any]] = {}
    for item in manual_applications:
        values = list(item.get("__values__") or [])
        name = str(item.get("name") or (values[1] if len(values) > 1 else values[0] if values else "")).strip()
        if name:
            application_details[name] = item
    catalog = [
        str(value).strip()
        for value in r2["profile"].get("interconnection", {}).get("application_catalog", [])
        if str(value).strip()
    ]
    output["report_table_011"] = [
        {
            "number": index,
            "name": name,
            "version": application_details.get(name, {}).get("version", ""),
            "location": application_details.get(name, {}).get("location", ""),
            "description": application_details.get(name, {}).get("description", ""),
        }
        for index, name in enumerate(catalog, start=1)
    ]
    output["report_table_016"] = [
        {"number": item.get("id", ""), "threat": item.get("description", ""), "frequency": item.get("frequency", "")}
        for item in r3.get("threat_catalog", [])
    ]
    output["report_table_020"] = _object_table_rows(r2, r3, "A-1")
    output["report_table_021"] = _object_table_rows(r2, r3, "A-2")
    output["report_table_022"] = _object_table_rows(r2, r3, "A-3")
    output["report_table_023"] = _object_table_rows(r2, r3, "A-4", subsystem=True)
    management_names = {
        "A-5": "管理制度", "A-6": "人员管理", "A-7": "建设运行", "A-8": "应急处置"
    }
    output["report_table_024"] = [
        {
            "number": index,
            "unit": management_names[section_code],
            "object_name": "、".join(
                sorted(
                    {
                        str(row.get("object_name") or "").strip()
                        for row in r3.get("final_projection", {}).get("rows", [])
                        if row.get("section_code") == section_code and str(row.get("object_name") or "").strip()
                    }
                )
            ),
            "methods": ["访谈", "文档审查"],
            "remark": "",
        }
        for index, section_code in enumerate(("A-5", "A-6", "A-7", "A-8"), start=1)
    ]
    return output


def _validate_context(
    r2: dict[str, Any],
    r3: dict[str, Any],
    *,
    mode: ExportMode,
    base_issues: list[dict[str, Any]],
    table_rows: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    issues = list(base_issues)
    scalars = r2["scalars"]
    required = {
        "report_number": "报告编号",
        "system_name": "系统名称",
        "assessed_name": "被测单位",
        "report_date": "报告日期",
        "compiler": "编制人",
    }
    for field, label in required.items():
        value = scalars.get(field)
        if not value:
            issues.append(_issue("error", f"{field.upper()}_REQUIRED", f"{label}不能为空。", field_id=field))
    if r2["project"]["workflow_status"] != "confirmed":
        issues.append(_issue("error", "REPORT_WORKFLOW_NOT_CONFIRMED", "正式导出前项目必须处于已确认状态。", field_id="workflow_status"))
    if r3.get("status") == "unavailable":
        issues.append(_issue("error", str(r3.get("reason") or "R3_CONTEXT_NOT_AVAILABLE"), "派生上下文不可用于导出。", block_id="r3_context"))

    expected_columns = {5: 4, 6: 5, 8: 9, 9: 7, 10: 6, 12: 6, 13: 3, 14: 5, 15: 3, 19: 5}
    for number, expected in expected_columns.items():
        for row_index, row in enumerate(table_rows.get(f"report_table_{number:03d}", []), start=1):
            values = row.get("__values__")
            if isinstance(values, list) and len(values) not in {expected, expected - 1}:
                issues.append(
                    _issue(
                        "error", "REPORT_TABLE_COLUMN_COUNT_MISMATCH",
                        "正文数据表列数与冻结母版不一致。",
                        table_id=f"report_table_{number:03d}", row_index=row_index,
                        details={"expected": [expected - 1, expected], "actual": len(values)},
                    )
                )

    selected = [str(value).strip() for value in r2["profile"].get("selected_algorithms", []) if str(value).strip()]
    appendix_text = "\n".join(
        str(row.get("record_text") or "")
        for row in r3.get("final_projection", {}).get("rows", [])
    )
    missing_algorithms = [algorithm for algorithm in selected if algorithm.lower() not in appendix_text.lower()]
    if missing_algorithms:
        issues.append(
            _issue(
                "error", "SELECTED_ALGORITHM_NOT_IN_APPENDIX_A",
                "已勾选密码算法未在附录 A 中出现，不能正式导出。",
                field_id="selected_algorithms", details={"algorithms": missing_algorithms},
            )
        )

    storage_root = settings.storage_path.resolve()
    for image in r2["evidence"]:
        candidate = Path(str(image.get("file_path") or ""))
        if not candidate.is_absolute():
            candidate = storage_root / candidate
        try:
            resolved = candidate.resolve()
            inside = resolved == storage_root or storage_root in resolved.parents
        except OSError:
            inside = False
            resolved = candidate
        if not inside or not resolved.is_file():
            issues.append(
                _issue(
                    "error", "EVIDENCE_FILE_UNAVAILABLE", "报告引用的证据图片不存在或路径越界。",
                    entity_uuid=image.get("evidence_uuid"), field_id="file_path",
                )
            )
    if mode == "draft":
        # Drafts retain the full issue list but only template/security failures
        # block creation.  The renderer uses slash/blank structures, never made-up
        # scores or conclusions, for unavailable upstream values.
        return issues
    return issues


def build_assembly_context(
    project_uuid: str,
    *,
    mode: ExportMode,
    version: str,
    expected_project_revision: int,
) -> dict[str, Any]:
    if mode not in {"draft", "final"}:
        raise ReportDomainError("REPORT_EXPORT_MODE_INVALID", "导出模式无效。", status_code=422)
    package = report_template_registry.load()
    matrix = load_default_field_matrix()
    template_docx_hash = hashlib.sha256(package.runtime_template_bytes).hexdigest()
    r2_envelope = report_context.get_report_context(
        project_uuid, expected_revision=expected_project_revision
    )
    project_updated_at = str(r2_envelope["project_updated_at"])
    r2 = dict(r2_envelope["context"])

    r2_hash = stable_hash(r2)
    base_validation = report_validation.validate_report(project_uuid)
    mapped_issues = [
        {
            "severity": str(item.get("severity") or "error"),
            "code": str(item.get("code") or "REPORT_VALIDATION_FAILED"),
            "message": str(item.get("message") or "完整报告字段校验未通过。"),
            "field_id": item.get("field"),
            "rule_id": item.get("relation_id"),
            "details": item.get("details") or {},
        }
        for item in base_validation.get("issues", [])
    ]
    try:
        r3 = report_generation.get_projection_context(project_uuid)
        if int(r3.get("project_revision") or 0) != expected_project_revision:
            raise ReportDomainError(
                "REVISION_CONFLICT",
                "R2 与 R3 上下文不属于同一项目 revision。",
                status_code=409,
                project_uuid=project_uuid,
                details={
                    "expected_revision": expected_project_revision,
                    "r3_revision": r3.get("project_revision"),
                },
            )
    except ReportDomainError as exc:
        if exc.code == "REVISION_CONFLICT":
            raise
        if mode == "final" or exc.code in {"R3_CONTEXT_HASH_MISMATCH", "R3_CONTEXT_SCHEMA_INVALID", "R3_PRIVATE_FACTOR_LEAK"}:
            r3 = _empty_r3(exc)
        else:
            r3 = _empty_r3(exc)
    r3_hash = stable_hash(r3)
    blocks = _block_map(r3)
    final_projection = r3.get("final_projection", {})
    statistics = final_projection.get("statistics", {}).get("total", {})
    conclusion = blocks.get("assessment_conclusion", {})
    risk_summary = blocks.get("risk_analysis.summary", {})
    scalars = dict(r2["scalars"])
    scalars.update(
        {
            "overall_score": str(final_projection.get("score", {}).get("display_score") or ""),
            "conclusion": str(conclusion.get("conclusion") or ""),
            "high_risk_judgement": str(
                risk_summary.get("summary_text", "").split("。", 1)[0].replace("根据《商用密码应用安全性评估高风险判定指引》", "")
                or conclusion.get("high_risk_judgment")
                or ""
            ),
            "not_applicable_count": int(statistics.get("not_applicable") or 0),
            "indicator_total": int(statistics.get("indicator_total") or 0),
            "high_risk_count": int(risk_summary.get("statistics", {}).get("high") or 0),
        }
    )
    table_rows = _table_rows(r2, r3)
    issues = _validate_context(
        r2, r3, mode=mode, base_issues=mapped_issues, table_rows=table_rows
    )
    errors = [item for item in issues if item["severity"] == "error"]
    warnings = [item for item in issues if item["severity"] == "warning"]
    if mode == "final" and errors:
        raise ReportDomainError(
            "REPORT_FINAL_VALIDATION_FAILED", "完整报告正式导出校验未通过。", status_code=422,
            project_uuid=project_uuid, details={"issues": issues},
        )
    context = {
        "schema_version": "1.0",
        "project_identity": {
            "project_uuid": project_uuid,
            "project_id": r2["project"]["id"],
            "project_name": r2["project"]["name"],
            "project_revision": expected_project_revision,
            "export_mode": mode,
            "export_version": version,
        },
        "template_binding": {
            "package_id": package.package_id,
            "template_edition": package.template_edition,
            "template_revision": package.template_revision,
            "asset_set_hash": package.asset_set_hash,
            "runtime_template_sha256": template_docx_hash,
        },
        "r2_context": r2,
        "r2_matrix": {"matrix_id": matrix.matrix_id, "matrix_version": matrix.matrix_version, "matrix_hash": matrix.sha256},
        "r2_context_hash": r2_hash,
        "r3_context": r3,
        "r3_context_hash": r3_hash,
        "scalar_slot_values": scalars,
        "chapter_blocks": blocks,
        "table_rows_by_table_id": table_rows,
        "appendix_a_final_projection": final_projection,
        "appendix_b_projection": {"schema_version": "1.0", "status": "not_implemented", "tables": {}},
        "validation_summary": {"errors": len(errors), "warnings": len(warnings), "issues": issues},
        "warning_summary": {
            "appendix_b": [
                _issue("warning", "APPENDIX_B_NOT_IMPLEMENTED", "附录 B 数据将在 R5 阶段接入，当前保留母版空结构。", block_id="appendix_b")
            ]
        },
    }
    if any(str(key).lower() in {"ra", "rk"} for key in _walk_keys(context)):
        raise ReportDomainError("R3_PRIVATE_FACTOR_LEAK", "装配上下文检测到 Ra/Rk 私有因子。", status_code=500)
    report_context.assert_context_current(
        project_uuid,
        expected_revision=expected_project_revision,
        expected_project_updated_at=project_updated_at,
    )
    if r3.get("status") != "unavailable":
        refreshed_r3 = report_generation.get_projection_context(project_uuid)
        if stable_hash(refreshed_r3) != r3_hash:
            raise ReportDomainError(
                "R3_CONTEXT_CHANGED_DURING_EXPORT",
                "装配上下文生成期间派生上下文已变化，请重试。",
                status_code=409,
                project_uuid=project_uuid,
            )
    context["assembly_context_hash"] = hashlib.sha256(canonical_json(context).encode("utf-8")).hexdigest()
    return context


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def validate_for_export(project_uuid: str, *, mode: ExportMode = "final") -> dict[str, Any]:
    revision = report_context.get_project_revision(project_uuid)
    try:
        context = build_assembly_context(
            project_uuid,
            mode=mode,
            version="V1.0",
            expected_project_revision=revision,
        )
        summary = context["validation_summary"]
        return {"project_uuid": project_uuid, "project_revision": revision, "mode": mode, **summary, "valid": not summary["errors"]}
    except ReportDomainError as exc:
        issues = exc.details.get("issues") if isinstance(exc.details, dict) else None
        normalized = issues if isinstance(issues, list) else [_issue("error", exc.code, exc.message)]
        return {
            "project_uuid": project_uuid,
            "project_revision": revision,
            "mode": mode,
            "errors": sum(item.get("severity") == "error" for item in normalized),
            "warnings": sum(item.get("severity") == "warning" for item in normalized),
            "issues": normalized,
            "valid": False,
        }
