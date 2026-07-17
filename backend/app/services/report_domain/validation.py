from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

from ... import database
from ...report_core.field_matrix import load_default_field_matrix
from .common import load_json, parse_iso_date, require_report_project, source_hash
from .errors import ReportDomainError


_INTERNAL_SCORE_TERM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:ra|rk)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def _contains_internal_score_term(value: str) -> bool:
    return _INTERNAL_SCORE_TERM_RE.search(value) is not None


def _public_matrix_value(value: Any) -> Any:
    """生成公开矩阵视图，不修改后端使用的权威矩阵对象。"""
    if isinstance(value, str):
        return "[internal-only]" if _contains_internal_score_term(value) else value
    if isinstance(value, dict):
        return {
            key: _public_matrix_value(item)
            for key, item in value.items()
            if not _contains_internal_score_term(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [
            _public_matrix_value(item)
            for item in value
            if not (isinstance(item, str) and _contains_internal_score_term(item))
        ]
    return value


def field_relations() -> dict[str, Any]:
    matrix = load_default_field_matrix()
    return {
        "matrix_id": matrix.matrix_id,
        "matrix_version": matrix.matrix_version,
        "package_id": matrix.package_id,
        "template_edition": matrix.template_edition,
        "template_revision": matrix.template_revision,
        "matrix_hash": matrix.sha256,
        "fields": [_public_matrix_value(dataclasses.asdict(item)) for item in matrix.fields],
        "relations": [_public_matrix_value(dataclasses.asdict(item)) for item in matrix.relations],
    }


def _issue(
    rule_ref: str,
    entity_path: str,
    code: str,
    message: str,
    *,
    severity: str = "error",
    field: str | None = None,
    target: str | None = None,
    details: dict[str, Any] | None = None,
    relation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "relation_id": relation_id or f"FRM-{rule_ref}",
        "entity_path": entity_path,
        "field": field,
        "code": code,
        "severity": severity,
        "message": message,
        "target": target,
        "details": details or {},
    }


def _profile_json(row: Any, column: str) -> dict[str, Any]:
    try:
        value = json.loads(row[column] or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _warning_hash(issue: dict[str, Any]) -> str:
    details = {
        key: value
        for key, value in issue.get("details", {}).items()
        if key not in {"source_hash", "confirmed"}
    }
    return source_hash(
        {
            "relation_id": issue.get("relation_id"),
            "entity_path": issue.get("entity_path"),
            "code": issue.get("code"),
            "details": details,
        }
    )


def _confirmed_warning(db, project_id: int, issue: dict[str, Any]) -> bool:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_warning_confirmations'"
    ).fetchone()
    if not exists:
        return False
    digest = _warning_hash(issue)
    return db.execute(
        """
        SELECT 1 FROM report_warning_confirmations
        WHERE project_id=? AND relation_id=? AND entity_path=? AND warning_code=? AND source_hash=?
        """,
        (project_id, issue["relation_id"], issue["entity_path"], issue["code"], digest),
    ).fetchone() is not None


def validate_report(project_uuid: str) -> dict[str, Any]:
    # 先执行矩阵自身的启动级完整性校验。
    matrix = load_default_field_matrix()
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        metadata = db.execute("SELECT * FROM report_metadata WHERE project_id=?", (project_id,)).fetchone()
        profile = db.execute("SELECT * FROM system_profiles WHERE project_id=?", (project_id,)).fetchone()
        distribution = db.execute("SELECT * FROM report_distribution WHERE project_id=?", (project_id,)).fetchone()
        phases = db.execute("SELECT * FROM report_phase_dates WHERE project_id=?", (project_id,)).fetchone()
        organizations = db.execute("SELECT * FROM report_organizations WHERE project_id=? AND active=1", (project_id,)).fetchall()
        members = db.execute("SELECT * FROM report_members WHERE project_id=? AND active=1", (project_id,)).fetchall()
        issues: list[dict[str, Any]] = []

        import_job = db.execute(
            """
            SELECT * FROM report_import_jobs
            WHERE created_project_id = ? AND status = 'succeeded'
            ORDER BY id DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if import_job is not None:
            blocked_source_keys: set[tuple[Any, Any, str, str]] = set()
            migration_issues = db.execute(
                """
                SELECT i.*, r.action AS resolution_action
                FROM report_import_issues i
                LEFT JOIN report_import_resolutions r
                  ON r.job_id = i.job_id AND r.issue_id = i.id
                WHERE i.job_id = ? AND i.blocks_final_export = 1
                ORDER BY i.id
                """,
                (int(import_job["id"]),),
            ).fetchall()
            for migration_issue in migration_issues:
                action = str(migration_issue["resolution_action"] or "")
                if action == "skip":
                    continue
                adopted = False
                if action == "adopt_candidate":
                    adopted = db.execute(
                        """
                        SELECT 1 FROM report_field_sources
                        WHERE project_id = ? AND report_import_job_id = ?
                          AND association_id IS ? AND authority_field_id IS ?
                          AND field_path = ? AND source_locator = ?
                          AND mapping_status = 'adopted' AND needs_confirmation = 0
                        LIMIT 1
                        """,
                        (
                            project_id,
                            int(import_job["id"]),
                            migration_issue["association_id"],
                            migration_issue["authority_field_id"],
                            migration_issue["field_path"],
                            migration_issue["source_locator"],
                        ),
                    ).fetchone() is not None
                if adopted:
                    continue
                blocked_source_keys.add(
                    (
                        migration_issue["association_id"],
                        migration_issue["authority_field_id"],
                        str(migration_issue["field_path"]),
                        str(migration_issue["source_locator"]),
                    )
                )
                issues.append(
                    _issue(
                        "migration-review",
                        f"report_import_issues.{migration_issue['id']}",
                        "MIGRATION_REVIEW_PENDING",
                        "一次性迁移仍有内容需要审阅，正式导出前必须采用有效映射或明确跳过。",
                        target="migration-review",
                        relation_id=str(migration_issue["association_id"] or "R6-MIGRATION-REVIEW"),
                        details={
                            "job_id": int(import_job["id"]),
                            "issue_id": int(migration_issue["id"]),
                            "issue_code": str(migration_issue["code"]),
                            "source_locator": str(migration_issue["source_locator"]),
                            "resolution_action": action or None,
                        },
                    )
                )

            pending_sources = db.execute(
                """
                SELECT id, association_id, authority_field_id, field_path, source_locator, mapping_status
                FROM report_field_sources
                WHERE project_id = ? AND report_import_job_id = ?
                  AND (needs_confirmation = 1 OR mapping_status = 'pending')
                ORDER BY id
                """,
                (project_id, int(import_job["id"])),
            ).fetchall()
            for source in pending_sources:
                source_key = (
                    source["association_id"],
                    source["authority_field_id"],
                    str(source["field_path"]),
                    str(source["source_locator"]),
                )
                if source_key in blocked_source_keys:
                    continue
                entity_path = f"report_field_sources.{source['id']}"
                issues.append(
                    _issue(
                        "migration-review",
                        entity_path,
                        "MIGRATION_REVIEW_PENDING",
                        "迁移字段仍处于待确认状态，正式导出前必须完成审阅。",
                        target="migration-review",
                        relation_id=str(source["association_id"] or "R6-MIGRATION-REVIEW"),
                        details={
                            "job_id": int(import_job["id"]),
                            "source_id": int(source["id"]),
                            "field_path": str(source["field_path"]),
                            "source_locator": str(source["source_locator"]),
                            "mapping_status": str(source["mapping_status"]),
                        },
                    )
                )

        if not profile or not str(profile["system_name"]).strip():
            issues.append(_issue("3.6.1.01", "system_profiles.system_name", "SYSTEM_NAME_REQUIRED", "系统名称不能为空。", field="system_name", target="front.cover"))
        assessed = [row for row in organizations if row["organization_type"] == "assessed"]
        if len(assessed) != 1 or not assessed[0]["name"].strip():
            issues.append(_issue("3.6.1.03", "report_organizations.assessed", "ASSESSED_ORGANIZATION_REQUIRED", "必须填写唯一的被测单位。", field="name", target="front.basic_information"))
        if metadata:
            if str(metadata["classification_level"]).strip() != "三级":
                issues.append(_issue("3.6.3.03", "report_metadata.classification_level", "TEMPLATE_LEVEL_UNSUPPORTED", "首版完整报告只支持三级模板。", field="classification_level", target="front.basic_information", details={"supported": ["三级"]}))
            if not str(metadata["report_number"]).strip():
                issues.append(_issue("3.6.1.02", "report_metadata.report_number", "REPORT_NUMBER_REQUIRED_FOR_REVIEW", "进入复核前必须填写报告编号。", field="report_number", target="front.cover"))
            else:
                duplicate = int(
                    db.execute(
                        """
                        SELECT COUNT(*) FROM report_metadata
                        WHERE project_id<>?
                          AND LOWER(TRIM(report_number))=LOWER(TRIM(?))
                        """,
                        (project_id, metadata["report_number"]),
                    ).fetchone()[0]
                )
                if duplicate:
                    issues.append(_issue("3.6.1.02", "report_metadata.report_number", "REPORT_NUMBER_DUPLICATE", "报告编号已被其他项目使用。", field="report_number", target="front.cover", details={"duplicate_project_count": duplicate}))
            roles = [metadata[name] for name in ("compiler_member_uuid", "reviewer_member_uuid", "approver_member_uuid") if metadata[name]]
            if not metadata["compiler_member_uuid"]:
                issues.append(_issue("3.6.2.05", "report_metadata.compiler_member_uuid", "COMPILER_REQUIRED", "进入复核前必须指定编制人。", field="compiler_member_uuid", target="front.basic_information"))
            if len(roles) != len(set(roles)):
                issues.append(_issue("3.6.2.07", "report_metadata.approval_members", "APPROVAL_ROLES_MUST_BE_DISTINCT", "编制人、审核人和批准人不得由同一人员兼任。", target="front.basic_information"))
            member_by_uuid = {row["member_uuid"]: row for row in members}
            for role in ("compiler", "reviewer", "approver"):
                member_uuid = metadata[f"{role}_member_uuid"]
                if not member_uuid:
                    continue
                member = member_by_uuid.get(member_uuid)
                if member is None:
                    issues.append(_issue("3.6.2.07", f"report_metadata.{role}_member_uuid", "APPROVAL_MEMBER_INVALID", "编审人员必须来自当前项目的有效成员。", field=f"{role}_member_uuid", target="front.basic_information"))
                    continue
                if not member["qualification_passed_at"]:
                    issues.append(_issue("3.6.2.07", f"report_metadata.{role}_member_uuid", "APPROVAL_MEMBER_QUALIFICATION_REQUIRED", "编审人员必须填写密评人员考核通过时间。", field=f"{role}_member_uuid", target="front.basic_information"))
                if role == "compiler" and (bool(member["is_project_leader"]) or member["team_role"] != "组员"):
                    issues.append(_issue("3.6.2.05", "report_metadata.compiler_member_uuid", "COMPILER_ROLE_INVALID", "编制人必须为组员且不得担任项目负责人。", field="compiler_member_uuid", target="front.basic_information"))
        qualified = [row for row in members if row["qualification_passed_at"]]
        if len(qualified) < 2:
            issues.append(_issue("3.6.2.08", "report_members", "QUALIFIED_TEAM_MEMBER_COUNT_LOW", "项目组至少需要两名已通过密评人员考核的成员。", target="front.basic_information", details={"qualified_count": len(qualified)}))

        if phases:
            required_dates = ("preparation_start", "preparation_end", "scheme_start", "scheme_end", "fieldwork_start", "fieldwork_end", "analysis_start", "analysis_end")
            missing = [field for field in required_dates if not phases[field]]
            if missing:
                issues.append(_issue("3.6.2.01", "report_phase_dates", "REPORT_PHASE_DATES_INCOMPLETE", "四个测评阶段的起止日期尚未填写完整。", field=missing[0], target="chapter.1.3", details={"missing_fields": missing}))
            active_member_uuids = {row["member_uuid"] for row in members}
            phase_member_uuids: set[str] = set()
            for column in ("travel_records_json", "site_visit_records_json"):
                for record in load_json(phases[column], []):
                    if isinstance(record, dict):
                        phase_member_uuids.update(str(value) for value in record.get("member_uuids", []))
            invalid_phase_members = sorted(phase_member_uuids - active_member_uuids)
            if invalid_phase_members:
                issues.append(_issue("3.6.2.08", "report_phase_dates.member_uuids", "PHASE_MEMBER_REFERENCE_INVALID", "差旅和进离场人员必须引用当前项目的有效项目组成员。", field="member_uuids", target="appendix.b", details={"invalid_member_uuids": invalid_phase_members}))
            if phases["analysis_end"]:
                compiled = parse_iso_date(phases["analysis_end"], field="analysis_end", project_uuid=project_uuid)
                reviewed = parse_iso_date(phases["report_review_at"], field="report_review_at", project_uuid=project_uuid)
                approved = parse_iso_date(phases["approved_at"], field="approved_at", project_uuid=project_uuid)
                if (
                    (reviewed and compiled and compiled > reviewed)
                    or (approved and reviewed and reviewed > approved)
                    or (approved and not reviewed and compiled and compiled > approved)
                ):
                    issues.append(_issue("3.6.2.03", "report_phase_dates.approval_dates", "APPROVAL_DATE_ORDER_INVALID", "编制、审核和批准日期顺序不一致。", field="approved_at", target="front.basic_information"))
        if distribution:
            total = int(distribution["regulator_copies"]) + int(distribution["client_copies"]) + int(distribution["assessment_organization_copies"])
            if total <= 0:
                issues.append(_issue("3.6.2.11", "report_distribution", "REPORT_DISTRIBUTION_TOTAL_REQUIRED", "报告分发总份数必须大于 0。", target="chapter.1.4"))

        catalog: list[str] = []
        catalog: list[str] = []
        if profile:
            def error(rule: str, field: str, message: str) -> None:
                issues.append(_issue(rule, f"system_profiles.{field}", "SELECTED_BRANCH_VALUE_REQUIRED", message, field=field, target="front.basic_information"))

            def warning(rule: str, field: str, value: Any, message: str) -> None:
                issue = _issue(rule, f"system_profiles.{field}", "UNSELECTED_BRANCH_HAS_VALUE", message, severity="warning", field=field, target="front.basic_information", details={"value": value})
                if _confirmed_warning(db, project_id, issue):
                    issue["severity"] = "info"
                    issue["details"]["confirmed"] = True
                issues.append(issue)

            critical = str(profile["critical_infrastructure_status"])
            department = str(profile["critical_infrastructure_department"])
            service = _profile_json(profile, "service_scope_json")
            cloud = _profile_json(profile, "cloud_platform_json")
            plan = _profile_json(profile, "crypto_plan_json")
            operation = _profile_json(profile, "operation_json")
            required_selections = {
                "critical_infrastructure_status": critical,
                "level_filing_status": str(profile["level_filing_status"]),
                "level_assessment_status": str(profile["level_assessment_status"]),
                "cloud_dependency": str(cloud.get("dependency", "")),
                "crypto_plan_status": str(plan.get("status", "")),
                "operation_status": str(operation.get("status", "")),
                "service_scope": str(service.get("kind", "")),
            }
            for field, value in required_selections.items():
                if not value:
                    issues.append(_issue("3.6.3.01", f"system_profiles.{field}", "SYSTEM_PROFILE_SELECTION_REQUIRED", "基本信息表存在尚未选择的必填状态。", field=field, target="front.basic_information"))
            if critical == "recognized" and not department.strip(): error("3.6.3.04", "critical_infrastructure_department", "已认定为关键信息基础设施时必须填写安全保护工作部门。")
            if critical == "not_recognized" and department.strip(): warning("3.6.3.04", "critical_infrastructure_department", department, "未认定分支仍填写了安全保护工作部门，请确认。")
            filing_values = [profile["level_filing_s"], profile["level_filing_a"], profile["level_filing_g"], profile["level_filing_number"]]
            if profile["level_filing_status"] == "filed" and (not str(profile["level_filing_s"]).strip() or not str(profile["level_filing_a"]).strip()): error("3.6.3.03", "level_filing_s", "已定级备案时必须填写 S 和 A，G 可以为空。")
            if profile["level_filing_status"] == "not_filed" and any(str(value).strip() for value in filing_values): warning("3.6.3.03", "level_filing_status", filing_values, "未定级备案分支仍填写了备案信息，请确认。")
            assessment_status = str(profile["level_assessment_status"])
            assessment_values = [profile["level_assessment_organization"], profile["level_assessment_period"], profile["level_assessment_conclusion"]]
            if assessment_status == "assessed" and not all(str(value).strip() for value in assessment_values): error("3.6.3.05", "level_assessment_status", "等保已测评时必须填写机构、时间和结论。")
            if assessment_status == "assessing" and not str(profile["level_assessment_organization"]).strip(): error("3.6.3.05", "level_assessment_organization", "等保正在测评时必须填写测评机构。")
            if assessment_status == "not_assessed" and any(str(value).strip() for value in assessment_values): warning("3.6.3.05", "level_assessment_status", assessment_values, "等保未测评分支仍填写了测评信息，请确认。")
            if assessment_status == "assessing" and any(str(value).strip() for value in assessment_values[1:]): warning("3.6.3.05", "level_assessment_status", assessment_values[1:], "等保正在测评分支仍填写了已测评时间或结论，请确认。")
            if service.get("kind") in {"cross_province", "cross_city"} and not isinstance(service.get("count"), int): error("3.6.3.09", "service_scope_json", "跨省或跨市服务范围必须填写正整数数量。")
            if service.get("kind") == "other" and not str(service.get("other", "")).strip(): error("3.6.3.09", "service_scope_json", "选择其他服务范围时必须填写说明。")
            if service.get("kind") not in {"cross_province", "cross_city"} and service.get("count") is not None: warning("3.6.3.09", "service_scope_json", service.get("count"), "当前服务范围不使用数量，但仍填写了数量，请确认。")
            if service.get("kind") != "other" and str(service.get("other", "")).strip(): warning("3.6.3.09", "service_scope_json", service.get("other"), "当前服务范围不是其他，但仍填写了其他说明，请确认。")
            if cloud.get("dependency") == "yes" and (not str(cloud.get("name", "")).strip() or not cloud.get("assessment_status")): error("3.6.3.06", "cloud_platform_json", "依赖云平台时必须填写平台名称和测评状态。")
            cloud_values = [cloud.get("name"), cloud.get("assessment_status"), cloud.get("organization"), cloud.get("date"), cloud.get("conclusion")]
            if cloud.get("dependency") == "no" and any(str(value or "").strip() for value in cloud_values): warning("3.6.3.06", "cloud_platform_json", cloud_values, "不依赖云平台分支仍填写了云平台信息，请确认。")
            if cloud.get("dependency") == "yes" and cloud.get("assessment_status") == "assessed" and not all(str(cloud.get(key, "")).strip() for key in ("organization", "date", "conclusion")): error("3.6.3.06", "cloud_platform_json", "云平台已测评时必须填写机构、时间和结论。")
            if cloud.get("dependency") == "yes" and cloud.get("assessment_status") == "assessing" and not str(cloud.get("organization", "")).strip(): error("3.6.3.06", "cloud_platform_json", "云平台正在测评时必须填写测评机构。")
            if plan.get("status") == "passed" and (not plan.get("passed_at") or plan.get("mode") not in {"self", "commissioned"}): error("3.6.3.07", "crypto_plan_json", "密码应用方案已通过密评时必须填写通过时间和评估方式。")
            if plan.get("mode") == "commissioned" and not str(plan.get("organization", "")).strip(): error("3.6.3.07", "crypto_plan_json", "委托评估时必须填写测评机构。")
            plan_values = [plan.get("passed_at"), plan.get("mode"), plan.get("organization")]
            if plan.get("status") in {"not_passed", "none"} and any(str(value or "").strip() for value in plan_values): warning("3.6.3.07", "crypto_plan_json", plan_values, "当前密码应用方案分支仍填写了已通过密评信息，请确认。")
            if plan.get("mode") == "self" and str(plan.get("organization", "")).strip(): warning("3.6.3.07", "crypto_plan_json", plan.get("organization"), "自行评估分支仍填写了委托测评机构，请确认。")
            if operation.get("status") == "running" and not operation.get("started_at"): error("3.6.3.08", "operation_json", "系统已投入运行时必须填写投入运行年月。")
            if operation.get("status") == "not_running" and not str(operation.get("construction_stage", "")).strip(): error("3.6.3.08", "operation_json", "系统未投入运行时必须填写当前建设阶段。")
            if operation.get("status") == "running" and str(operation.get("construction_stage", "")).strip(): warning("3.6.3.08", "operation_json", operation.get("construction_stage"), "系统已投入运行分支仍填写了建设阶段，请确认。")
            if operation.get("status") == "not_running" and operation.get("started_at"): warning("3.6.3.08", "operation_json", operation.get("started_at"), "系统未投入运行分支仍填写了投入运行时间，请确认。")
            if operation.get("status") == "running" and operation.get("started_at") and phases and phases["analysis_end"]:
                started = parse_iso_date(str(operation["started_at"]), field="operation_started_at", project_uuid=project_uuid)
                report_date = parse_iso_date(str(phases["analysis_end"]), field="analysis_end", project_uuid=project_uuid)
                if started and report_date and started > report_date: error("3.6.3.08", "operation_json", "系统投入运行时间不得晚于报告日期。")
            filing_evidence = _profile_json(profile, "level_match_evidence_json")
            filing_same = filing_evidence.get("same")
            if profile["level_filing_status"] == "filed" and filing_same is False and (not str(filing_evidence.get("system_name", "")).strip() or not str(filing_evidence.get("difference", "")).strip()): error("3.6.2.10", "level_match_evidence_json", "备案系统与被测系统不同时必须填写备案系统名称和差异说明。")
            if profile["level_filing_status"] == "not_filed" and any(value not in (None, "", False) for value in filing_evidence.values()): warning("3.6.2.10", "level_match_evidence_json", filing_evidence, "未备案但 B.9 仍存在备案匹配数据，请确认。")
            product_total = int(db.execute("SELECT COALESCE(SUM(normalized_quantity),0) FROM system_crypto_products WHERE project_id=?", (project_id,)).fetchone()[0])
            if bool(profile["no_crypto_products"]) and product_total:
                warning("3.6.3.12", "no_crypto_products", product_total, "已选择系统未使用密码产品，但表 2-3 仍存在非零数量，请确认。")
            if not bool(profile["no_crypto_products"]) and product_total == 0:
                error("3.6.3.10", "no_crypto_products", "请填写表 2-3 密码产品，或明确选择系统未使用密码产品。")
            selected_algorithms = load_json(profile["selected_algorithms_json"], [])
            interconnection = _profile_json(profile, "interconnection_json")
            if not selected_algorithms and not interconnection.get("other_algorithms"):
                error("3.6.3.13", "selected_algorithms_json", "请至少勾选或填写一种系统使用的密码算法。")
            catalog = [str(value).strip() for value in interconnection.get("application_catalog", []) if str(value).strip()]
            if len(catalog) != len(set(catalog)):
                issues.append(_issue("3.6.4.06", "system_profiles.application_catalog", "APPLICATION_CATALOG_DUPLICATE", "表 2-7 应用名称存在重复。", field="application_catalog", target="chapter.2.4.7"))

        subsystem_missing = db.execute(
            """
            SELECT DISTINCT a.code,o.object_uuid,o.name_snapshot FROM assessment_objects o
            JOIN assessment_rows r ON r.assessment_object_uuid=o.object_uuid
            JOIN appendix_sections a ON a.id=r.section_id AND a.project_id=o.project_id
            LEFT JOIN assessment_object_subsystems s ON s.project_id=o.project_id AND s.object_uuid=o.object_uuid
            WHERE o.project_id=? AND o.active=1 AND a.code IN ('A-2','A-4')
              AND (s.id IS NULL OR TRIM(s.subsystem_name)='')
            """,
            (project_id,),
        ).fetchall()
        for row in subsystem_missing:
            code = str(row["code"])
            issues.append(
                _issue(
                    "3.6.4.05",
                    f"assessment_objects.{row['object_uuid']}",
                    f"{code.replace('-', '')}_SUBSYSTEM_REQUIRED",
                    f"{code} 测评对象必须填写所属子系统。",
                    field="subsystem_name",
                    target="appendix-transmission-relations",
                    details={"object_name": row["name_snapshot"], "section_code": code},
                )
            )
        invalid_subsystems = db.execute(
            """
            SELECT s.binding_uuid,s.subsystem_name,o.name_snapshot
            FROM assessment_object_subsystems s
            JOIN assessment_objects o ON o.object_uuid=s.object_uuid AND o.project_id=s.project_id
            WHERE s.project_id=? AND o.active=1 AND o.source_section_code='A-4'
            """,
            (project_id,),
        ).fetchall()
        for row in invalid_subsystems:
            if row["subsystem_name"] not in catalog:
                issues.append(_issue("3.6.4.06", f"assessment_object_subsystems.{row['binding_uuid']}", "A4_SUBSYSTEM_NOT_IN_APPLICATION_CATALOG", "A-4 子系统必须选自表 2-7 应用名称。", field="subsystem_name", target="objects", details={"object_name": row["name_snapshot"], "subsystem_name": row["subsystem_name"]}))

        transmission_metrics = {
            "重要数据传输机密性": "confidentiality",
            "重要数据传输完整性": "integrity",
        }
        a4_rows = db.execute(
            """
            SELECT DISTINCT o.object_uuid,o.name_snapshot,r.unit
            FROM assessment_objects o
            JOIN assessment_rows r ON r.assessment_object_uuid=o.object_uuid
            JOIN appendix_sections a ON a.id=r.section_id AND a.project_id=o.project_id
            WHERE o.project_id=? AND o.active=1 AND a.code='A-4'
            """,
            (project_id,),
        ).fetchall()
        normalized_metrics = {
            "".join(name.split()).replace("指标", ""): kind
            for name, kind in transmission_metrics.items()
        }
        for row in a4_rows:
            normalized_unit = "".join(str(row["unit"] or "").split()).replace("指标", "")
            kind = normalized_metrics.get(normalized_unit)
            if not kind:
                continue
            relation = db.execute(
                """
                SELECT 1 FROM result_correction_relations
                WHERE project_id=? AND a4_object_uuid=? AND correction_kind=?
                """,
                (project_id, row["object_uuid"], kind),
            ).fetchone()
            if relation is None:
                issues.append(
                    _issue(
                        "3.6.5.02",
                        f"assessment_objects.{row['object_uuid']}.{kind}",
                        "A4_TRANSMISSION_RELATION_REQUIRED",
                        "A-4 传输机密性/完整性对象必须关联同子系统的 A-2 通道。",
                        field="a2_object_uuid",
                        target="appendix-transmission-relations",
                        details={
                            "object_name": row["name_snapshot"],
                            "correction_kind": kind,
                            "metric": row["unit"],
                        },
                    )
                )

        invalid_relation_endpoints = db.execute(
            """
            SELECT c.correction_uuid,c.correction_kind,
                   a2.active AS a2_active,a4.active AS a4_active
            FROM result_correction_relations c
            LEFT JOIN assessment_objects a2
              ON a2.project_id=c.project_id AND a2.object_uuid=c.a2_object_uuid
            LEFT JOIN assessment_objects a4
              ON a4.project_id=c.project_id AND a4.object_uuid=c.a4_object_uuid
            WHERE c.project_id=? AND (
                COALESCE(a2.active,0)<>1 OR COALESCE(a4.active,0)<>1
                OR NOT EXISTS (
                    SELECT 1 FROM assessment_rows r2
                    JOIN appendix_sections s2 ON s2.id=r2.section_id
                    WHERE s2.project_id=c.project_id AND s2.code='A-2'
                      AND r2.assessment_object_uuid=c.a2_object_uuid
                )
                OR NOT EXISTS (
                    SELECT 1 FROM assessment_rows r4
                    JOIN appendix_sections s4 ON s4.id=r4.section_id
                    WHERE s4.project_id=c.project_id AND s4.code='A-4'
                      AND r4.assessment_object_uuid=c.a4_object_uuid
                )
            )
            """,
            (project_id,),
        ).fetchall()
        for row in invalid_relation_endpoints:
            issues.append(
                _issue(
                    "3.6.5.02",
                    f"result_correction_relations.{row['correction_uuid']}",
                    "CORRECTION_RELATION_ENDPOINT_INVALID",
                    "已有 A-2/A-4 传输关系包含停用或未绑定附录 A 的对象。",
                    field="object_uuid",
                    target="appendix-transmission-relations",
                    details={
                        "correction_kind": row["correction_kind"],
                        "a2_active": row["a2_active"],
                        "a4_active": row["a4_active"],
                    },
                )
            )

        mismatched_relations = db.execute(
            """
            SELECT c.correction_uuid,c.correction_kind,
                   a2s.subsystem_name AS a2_subsystem,a4s.subsystem_name AS a4_subsystem
            FROM result_correction_relations c
            LEFT JOIN assessment_object_subsystems a2s
              ON a2s.project_id=c.project_id AND a2s.object_uuid=c.a2_object_uuid
            LEFT JOIN assessment_object_subsystems a4s
              ON a4s.project_id=c.project_id AND a4s.object_uuid=c.a4_object_uuid
            WHERE c.project_id=?
            """,
            (project_id,),
        ).fetchall()
        for row in mismatched_relations:
            a2_subsystem = " ".join(str(row["a2_subsystem"] or "").split()).casefold()
            a4_subsystem = " ".join(str(row["a4_subsystem"] or "").split()).casefold()
            if a2_subsystem == a4_subsystem:
                continue
            issues.append(
                _issue(
                    "3.6.5.02",
                    f"result_correction_relations.{row['correction_uuid']}",
                    "CORRECTION_SUBSYSTEM_MISMATCH",
                    "已有 A-2/A-4 传输修正关系的两端子系统不一致。",
                    field="subsystem_name",
                    target="appendix-transmission-relations",
                    details={
                        "correction_kind": row["correction_kind"],
                        "a2_subsystem": row["a2_subsystem"],
                        "a4_subsystem": row["a4_subsystem"],
                    },
                )
            )

        unbound_rows = db.execute(
            """
            SELECT a.code,COUNT(*) AS row_count FROM assessment_rows r
            JOIN appendix_sections a ON a.id=r.section_id
            WHERE a.project_id=? AND TRIM(r.object_name)<>'' AND r.assessment_object_uuid IS NULL
            GROUP BY a.code ORDER BY a.sort_order
            """,
            (project_id,),
        ).fetchall()
        for row in unbound_rows:
            issues.append(_issue("3.6.4.01", f"assessment_rows.{row['code']}", "APPENDIX_A_OBJECTS_UNBOUND", "附录 A 仍有测评对象记录未绑定中央对象。", field="assessment_object_uuid", target="objects", details={"section_code": row["code"], "row_count": int(row["row_count"])}))

        table_46_conflicts = db.execute(
            """
            SELECT o.object_uuid,o.name_snapshot,
                   SUM(CASE WHEN r.unit LIKE '%访问控制信息完整性%' THEN 1 ELSE 0 END) AS access_count,
                   SUM(CASE WHEN r.unit LIKE '%重要数据存储完整性%' THEN 1 ELSE 0 END) AS storage_count
            FROM assessment_objects o
            JOIN assessment_rows r ON r.assessment_object_uuid=o.object_uuid
            JOIN appendix_sections a ON a.id=r.section_id AND a.project_id=o.project_id
            WHERE o.project_id=? AND a.code='A-4' AND o.active=1
            GROUP BY o.object_uuid,o.name_snapshot
            HAVING access_count>0 AND storage_count>0
            """,
            (project_id,),
        ).fetchall()
        for row in table_46_conflicts:
            issues.append(_issue("3.6.4.11", f"assessment_objects.{row['object_uuid']}", "TABLE_4_6_INTEGRITY_MAPPING_CONFLICT", "同一对象不得同时作为访问控制信息完整性和重要数据存储完整性对象。", target="chapter.4.1.a4.summary", details={"object_name": row["name_snapshot"]}))

        incomplete_sections = db.execute(
            """
            SELECT section_uuid, section_key, title, section_type, completion_status
            FROM report_sections
            WHERE project_id = ?
              AND section_type IN ('form', 'blocks')
              AND edit_policy <> 'readonly'
              AND completion_status <> 'complete'
            ORDER BY COALESCE(parent_section_id, 0), sort_order, id
            """,
            (project_id,),
        ).fetchall()
        for row in incomplete_sections:
            issues.append(
                _issue(
                    "section-completion",
                    f"report_sections.{row['section_uuid']}.completion_status",
                    "REPORT_SECTION_INCOMPLETE",
                    f"章节“{row['title']}”尚未标记为完成。",
                    field="completion_status",
                    target=row["section_key"],
                    details={
                        "section_uuid": row["section_uuid"],
                        "section_key": row["section_key"],
                        "title": row["title"],
                        "section_type": row["section_type"],
                        "completion_status": row["completion_status"],
                    },
                    relation_id="R2-SECTION-COMPLETION",
                )
            )

        issues.sort(key=lambda item: ({"error": 0, "warning": 1, "info": 2}[item["severity"]], item["relation_id"], item["entity_path"]))
        for item in issues:
            if item["severity"] == "warning":
                item["details"]["source_hash"] = _warning_hash(item)
        summary = {severity: sum(item["severity"] == severity for item in issues) for severity in ("error", "warning", "info")}
        return {
            "matrix_id": matrix.matrix_id,
            "matrix_version": matrix.matrix_version,
            "matrix_hash": matrix.sha256,
            "errors": summary["error"],
            "warnings": summary["warning"],
            "info": summary["info"],
            "issues": issues,
        }


def confirm_warning(project_uuid: str, relation_id: str, entity_path: str, warning_code: str, supplied_hash: str) -> dict[str, Any]:
    current = validate_report(project_uuid)
    issue = next(
        (
            item
            for item in current["issues"]
            if item["relation_id"] == relation_id
            and item["entity_path"] == entity_path
            and item["code"] == warning_code
            and item["severity"] == "warning"
        ),
        None,
    )
    if issue is None:
        raise ReportDomainError(
            "WARNING_NOT_CURRENT",
            "该警告已不存在或已经确认，请刷新校验结果。",
            status_code=409,
            project_uuid=project_uuid,
            entity_type="report_warning",
            entity_uuid=f"{relation_id}:{warning_code}",
        )
    expected_hash = _warning_hash(issue)
    if supplied_hash != expected_hash:
        raise ReportDomainError(
            "WARNING_SOURCE_CHANGED",
            "警告对应的数据已经变化，请重新检查后确认。",
            status_code=409,
            project_uuid=project_uuid,
            entity_type="report_warning",
            entity_uuid=f"{relation_id}:{warning_code}",
            details={"expected_source_hash": expected_hash},
        )
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        db.execute(
            """
            INSERT INTO report_warning_confirmations (project_id,relation_id,entity_path,warning_code,source_hash,confirmed_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(project_id,relation_id,entity_path,warning_code)
            DO UPDATE SET source_hash=excluded.source_hash,confirmed_at=excluded.confirmed_at
            """,
            (project["id"], relation_id, entity_path, warning_code, supplied_hash, database.utc_now()),
        )
        return {"relation_id": relation_id, "entity_path": entity_path, "warning_code": warning_code, "source_hash": supplied_hash, "confirmed": True}


def overview(project_uuid: str) -> dict[str, Any]:
    matrix = load_default_field_matrix()
    validation = validate_report(project_uuid)
    with database.connect() as db:
        project = require_report_project(project_uuid, db)
        project_id = int(project["id"])
        section_count = int(db.execute("SELECT COUNT(*) FROM report_sections WHERE project_id=?", (project_id,)).fetchone()[0])
        completed = int(db.execute("SELECT COUNT(*) FROM report_sections WHERE project_id=? AND completion_status='complete'", (project_id,)).fetchone()[0])
        object_count = int(db.execute("SELECT COUNT(*) FROM assessment_objects WHERE project_id=? AND active=1", (project_id,)).fetchone()[0])
        unbound = int(db.execute("SELECT COUNT(*) FROM assessment_rows r JOIN appendix_sections s ON s.id=r.section_id WHERE s.project_id=? AND TRIM(r.object_name)<>'' AND r.assessment_object_uuid IS NULL", (project_id,)).fetchone()[0])
        return {
            "project_uuid": project_uuid,
            "workflow_status": project["workflow_status"],
            "template_package_id": project["template_package_id"],
            "template_edition": project["template_edition"],
            "template_revision": project["template_revision"],
            "field_matrix_version": matrix.matrix_version,
            "field_matrix_hash": matrix.sha256,
            "section_count": section_count,
            "completed_section_count": completed,
            "object_count": object_count,
            "unbound_assessment_row_count": unbound,
            "error_count": validation["errors"],
            "warning_count": validation["warnings"],
        }
