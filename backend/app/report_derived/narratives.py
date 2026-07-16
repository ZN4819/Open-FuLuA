from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from decimal import Decimal
from typing import Any

from .rules import DerivedRuleSet, stable_hash


PLACEHOLDER_PATTERN = re.compile(r"【[^】]+】|\bX{2,}\b|填写说明|建议不超过\s*200\s*字|\{[^{}]+\}", re.IGNORECASE)
RISK_METHOD_TEXT = (
    "具体地，根据威胁类型和威胁发生频率，判断测评结果汇总中部分符合项或不符合项所产生的安全问题被威胁利用的可能性，"
    "可能性的取值范围为高、中和低。根据资产价值的高低，判断测评结果汇总中部分符合项或不符合项所产生的安全问题被威胁利用后，"
    "对被测系统的业务信息安全造成的影响程度，影响程度取值范围为高、中和低。综合以上的结果，密评机构根据自身经验和相关国家标准要求，"
    "对被测系统面临的安全风险进行赋值，风险值的取值范围为高、中和低。结合被测系统的安全保护等级对风险分析结果进行评价，"
    "即对国家安全、社会秩序、公共利益以及公民、法人和其他组织的合法权益造成的风险。如果存在高风险项，则认为信息系统面临高风险；"
    "同时也需要考虑多个中低风险叠加可能导致的高风险问题。"
)


def _date(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else text


def _level_phrase(value: Any) -> str:
    text = str(value or "三级").strip() or "三级"
    if text.startswith("第") and text.endswith("级别"):
        return text
    if text.startswith("第") and text.endswith("级"):
        return f"{text}别"
    if text.endswith("级"):
        return f"第{text}别"
    return f"第{text}级别"


def read_report_facts(db: sqlite3.Connection, project_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profile = db.execute("SELECT * FROM system_profiles WHERE project_id = ?", (project_id,)).fetchone()
    metadata = db.execute("SELECT * FROM report_metadata WHERE project_id = ?", (project_id,)).fetchone()
    phases = db.execute("SELECT * FROM report_phase_dates WHERE project_id = ?", (project_id,)).fetchone()
    organizations = db.execute(
        """
        SELECT organization_type, name
        FROM report_organizations
        WHERE project_id = ? AND active = 1 AND organization_type IN ('assessed', 'client')
        ORDER BY CASE organization_type WHEN 'assessed' THEN 0 ELSE 1 END, sort_order, id
        """,
        (project_id,),
    ).fetchall()
    names = {str(row["organization_type"]): str(row["name"] or "").strip() for row in organizations}
    assessed = names.get("assessed", "")
    client = names.get("client", "") or assessed
    system_name = str(profile["system_name"] or "").strip() if profile else ""
    system_summary = str(profile["system_summary"] or "").strip() if profile else ""
    preparation_start = _date(phases["preparation_start"] if phases else None)
    report_end = _date(phases["analysis_end"] if phases else None)
    classification = _level_phrase(metadata["classification_level"] if metadata else "三级")
    issues: list[dict[str, Any]] = []
    for field, value, message in (
        ("system_profiles.system_name", system_name, "系统名称尚未填写。"),
        ("system_profiles.system_summary", system_summary, "系统简介事实源尚未填写。"),
        ("report_organizations.assessed", assessed, "被测单位尚未填写。"),
        ("report_phase_dates.preparation_start", preparation_start, "测评准备阶段开始日期尚未填写。"),
        ("report_phase_dates.analysis_end", report_end, "分析与报告编制阶段结束日期尚未填写。"),
    ):
        if not value:
            issues.append({"code": "NARRATIVE_FACT_MISSING", "message": message, "field": field})
    special_count = int(
        db.execute("SELECT COUNT(*) FROM special_indicators WHERE project_id = ?", (project_id,)).fetchone()[0]
    )
    facts = {
        "system_name": system_name,
        "system_summary": system_summary,
        "assessed_organization": assessed,
        "effective_client": client,
        "assessment_start": preparation_start,
        "assessment_end": report_end,
        "classification_phrase": classification,
        "special_indicator_count": special_count,
    }
    return facts, issues


def build_finding_baselines(
    final_projection: dict[str, Any],
    rule_set: DerivedRuleSet,
) -> list[dict[str, Any]]:
    rows_by_indicator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in final_projection["rows"]:
        rows_by_indicator[row["indicator_code"]].append(row)
    indicator_by_code = {row["indicator_code"]: row for row in final_projection["indicators"]}
    findings: list[dict[str, Any]] = []
    for indicator_rule in rule_set.indicators:
        indicator = indicator_by_code[indicator_rule.code]
        if indicator["indicator_result"] not in {"部分符合", "不符合"}:
            continue
        rows = rows_by_indicator[indicator_rule.code]
        noncompliant = [row for row in rows if row["object_result"] == "不符合"]
        partial = [row for row in rows if row["object_result"] == "部分符合"]
        problem_items: list[dict[str, Any]] = []
        if noncompliant:
            names = "、".join(sorted({row["object_name"] or row["object_uuid"] for row in noncompliant}))
            evidence = "；".join(
                dict.fromkeys(row["record_text"] for row in noncompliant if row["record_text"])
            )
            text = f"测评对象“{names}”在“{indicator_rule.name}”指标下判定为不符合"
            if evidence:
                text += f"：{evidence}"
            problem_items.append(
                {
                    "result": "不符合",
                    "object_uuids": sorted(row["object_uuid"] for row in noncompliant),
                    "source_row_ids": sorted(row["source_row_id"] for row in noncompliant),
                    "text": f"{text}。" if not text.endswith("。") else text,
                }
            )
        for row in sorted(partial, key=lambda item: (item["object_uuid"], item["source_row_id"])):
            text = f"测评对象“{row['object_name'] or row['object_uuid']}”在“{indicator_rule.name}”指标下判定为部分符合"
            if row["record_text"]:
                text += f"：{row['record_text']}"
            problem_items.append(
                {
                    "result": "部分符合",
                    "object_uuids": [row["object_uuid"]],
                    "source_row_ids": [row["source_row_id"]],
                    "text": f"{text}。" if not text.endswith("。") else text,
                }
            )
        description = "".join(item["text"] for item in problem_items)
        source = {
            "indicator_code": indicator_rule.code,
            "indicator_result": indicator["indicator_result"],
            "problem_items": problem_items,
        }
        findings.append(
            {
                "indicator_code": indicator_rule.code,
                "indicator_name": indicator_rule.name,
                "layer_code": indicator_rule.layer_code,
                "final_indicator_result": indicator["indicator_result"],
                "problem_items": problem_items,
                "problem_description": description,
                "source_hash": stable_hash(source),
            }
        )
    return findings


def risk_snapshot_from_rows(
    risk_rows: list[dict[str, Any]],
    statistics: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    expected = statistics["total"]["partially_compliant"] + statistics["total"]["noncompliant"]
    if len(risk_rows) != expected:
        issues.append(
            {
                "code": "RISK_COUNT_INVARIANT_FAILED",
                "message": "风险总数必须等于部分符合指标数与不符合指标数之和。",
                "field": "report_risks",
                "details": {"expected": expected, "actual": len(risk_rows)},
            }
        )
    for risk in risk_rows:
        if risk.get("risk_level") not in {"high", "medium", "low"}:
            issues.append(
                {
                    "code": "RISK_LEVEL_REQUIRED",
                    "message": "风险等级尚未选择。",
                    "entity_uuid": risk.get("risk_uuid"),
                    "field": "risk_level",
                }
            )
        if not risk.get("threat_ids"):
            issues.append(
                {
                    "code": "RISK_THREAT_REQUIRED",
                    "message": "风险至少需要关联一个母版威胁编号。",
                    "entity_uuid": risk.get("risk_uuid"),
                    "field": "threat_ids",
                }
            )
        if risk.get("confirmation_status") != "confirmed":
            issues.append(
                {
                    "code": "RISK_CONFIRMATION_REQUIRED",
                    "message": "风险分析尚未确认。",
                    "entity_uuid": risk.get("risk_uuid"),
                    "field": "confirmation_status",
                }
            )
    if issues:
        return None, issues
    counts = {
        "high": sum(risk["risk_level"] == "high" for risk in risk_rows),
        "medium": sum(risk["risk_level"] == "medium" for risk in risk_rows),
        "low": sum(risk["risk_level"] == "low" for risk in risk_rows),
    }
    if sum(counts.values()) != expected:
        return None, [
            {
                "code": "RISK_LEVEL_COUNT_INVARIANT_FAILED",
                "message": "高、中、低风险数量之和与风险总数不一致。",
                "field": "risk_level",
            }
        ]
    if counts["high"]:
        overall = "高"
        judgment = "判定系统存在高风险"
    elif counts["medium"]:
        overall = "中"
        judgment = "判定系统不存在高风险"
    elif counts["low"]:
        overall = "低"
        judgment = "判定系统不存在高风险"
    else:
        overall = "未发现安全风险"
        judgment = "判定系统不存在高风险"
    return (
        {
            "risk_total": expected,
            "counts": counts,
            "overall_risk": overall,
            "high_risk_judgment": judgment,
            "rows": risk_rows,
        },
        [],
    )


def assessment_conclusion(score: dict[str, Any], risk_snapshot: dict[str, Any]) -> dict[str, Any]:
    display = Decimal(score["display_score"])
    high_count = int(risk_snapshot["counts"]["high"])
    if display == Decimal("100.00"):
        conclusion = "符合"
    elif Decimal("60.00") <= display < Decimal("100.00") and high_count == 0:
        conclusion = "基本符合"
    else:
        conclusion = "不符合"
    return {
        "display_score": score["display_score"],
        "conclusion": conclusion,
        "overall_risk": risk_snapshot["overall_risk"],
        "high_risk_judgment": risk_snapshot["high_risk_judgment"],
    }


def _layer_statistics(statistics: dict[str, Any], layer_code: str) -> dict[str, Any]:
    return next(item for item in statistics["layers"] if item["layer_code"] == layer_code)


def _recommendation(indicator_name: str) -> str:
    return (
        f"建议针对“{indicator_name}”指标及上述测评对象完善密码应用措施和管理要求，"
        "采用合规的密码产品、密码算法或管理流程进行整改，并在整改完成后开展复核验证。"
    )


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return bool(PLACEHOLDER_PATTERN.search(value))
    if isinstance(value, dict):
        return any(_contains_placeholder(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_placeholder(item) for item in value)
    return False


def generate_narrative_blocks(
    *,
    facts: dict[str, Any],
    projection: dict[str, Any],
    findings: list[dict[str, Any]],
    risk_snapshot: dict[str, Any],
    conclusion: dict[str, Any],
    rule_set: DerivedRuleSet,
) -> list[dict[str, Any]]:
    statistics = projection["final_projection"]["statistics"]
    total = statistics["total"]
    risk_counts = risk_snapshot["counts"]
    layers = rule_set.layer_by_code
    rows_by_layer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in projection["final_projection"]["rows"]:
        rows_by_layer[row["layer_code"]].append(row)
    findings_by_layer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        findings_by_layer[finding["layer_code"]].append(finding)

    blocks: list[dict[str, Any]] = [
        {
            "block_key": "conclusion.system_summary",
            "section_key": "front.assessment_conclusion",
            "rule_id": "R3.NARRATIVES",
            "edit_policy": "overrideable",
            "dependencies": ["system_summary"],
            "baseline": {"text": facts["system_summary"], "italic": False, "first_line_indent_chars": 0},
        },
        {
            "block_key": "conclusion.assessment_summary",
            "section_key": "front.assessment_conclusion",
            "rule_id": "R3.NARRATIVES",
            "edit_policy": "readonly",
            "dependencies": ["report_facts", "appendix_a", "correction_relations", "risks"],
            "baseline": {
                "text": (
                    f"　　受{facts['effective_client']}委托，中互金认证有限公司于{facts['assessment_start']}至{facts['assessment_end']}"
                    f"对{facts['assessed_organization']}的{facts['system_name']}进行了商用密码应用安全性评估，本次评估包含物理和环境、网络和通信、"
                    "设备和计算、应用和数据等密码技术应用要求部分和管理制度、人员管理、建设运行、应急处置等密码应用管理要求部分的测评，"
                    f"评估已完成41项测评项的测评工作，其中符合项{total['compliant']}项，部分符合项{total['partially_compliant']}项，"
                    f"不符合项{total['noncompliant']}项，不适用项{total['not_applicable']}项。风险分析{risk_snapshot['high_risk_judgment']}。"
                ),
                "italic": False,
                "first_line_indent_chars": 2,
            },
        },
        {
            "block_key": "overall_evaluation.intro",
            "section_key": "front.overall_evaluation",
            "rule_id": "R3.NARRATIVES",
            "edit_policy": "readonly",
            "dependencies": ["appendix_a", "correction_relations", "risks", "special_indicators"],
            "baseline": {
                "text": (
                    f"本次信息系统商用密码应用安全性评估依据GB/T 39786—2021《信息安全技术 信息系统密码应用基本要求》的"
                    f"{facts['classification_phrase']}要求，选取的测评指标总数为41项，其中不适用项为{total['not_applicable']}项，"
                    f"特殊指标{facts['special_indicator_count']}项。测评结果为：符合项{total['compliant']}项，"
                    f"部分符合项{total['partially_compliant']}项，不符合项{total['noncompliant']}项。其中，在部分符合和不符合项中："
                    f"高风险项{risk_counts['high']}项，中风险项{risk_counts['medium']}项，低风险项{risk_counts['low']}项。"
                )
            },
        },
    ]
    for index, layer_rule in enumerate(rule_set.layers, start=1):
        layer_stats = _layer_statistics(statistics, layer_rule.code)
        unique_objects = {row["object_uuid"] for row in rows_by_layer[layer_rule.code]}
        applicable = layer_stats["indicator_total"] - layer_stats["not_applicable"]
        blocks.append(
            {
                "block_key": f"overall_evaluation.layer.{index}",
                "section_key": "front.overall_evaluation",
                "rule_id": "R3.NARRATIVES",
                "edit_policy": "overrideable",
                "dependencies": ["appendix_a", "correction_relations"],
                "baseline": {
                    "layer_code": layer_rule.code,
                    "layer_name": layer_rule.name,
                    "situation_description": f"共测评{len(unique_objects)}个对象，实际适用{applicable}项指标",
                    "text": (
                        f"{index}. 在{layer_rule.name}方面，共测评{len(unique_objects)}个对象，实际适用{applicable}项指标。"
                        f"测评结果：符合项{layer_stats['compliant']}项，部分符合项{layer_stats['partially_compliant']}项，"
                        f"不符合项{layer_stats['noncompliant']}项，不适用项{layer_stats['not_applicable']}项。"
                    ),
                    "statistics": layer_stats,
                },
            }
        )
    blocks.append(
        {
            "block_key": "overall_evaluation.outro",
            "section_key": "front.overall_evaluation",
            "rule_id": "R3.NARRATIVES",
            "edit_policy": "readonly",
            "dependencies": ["appendix_a", "correction_relations", "risks"],
            "baseline": {
                "text": (
                    f"通过对{facts['system_name']}的物理和环境安全、网络和通信安全、设备和计算安全、应用和数据安全、管理制度、人员管理、"
                    f"建设运行和应急处置等方面的测评，该系统{conclusion['conclusion']}GB/T 39786—2021"
                    f"《信息安全技术 信息系统密码应用基本要求》的{facts['classification_phrase']}要求。"
                )
            },
        }
    )
    blocks.extend(
        [
            {
                "block_key": "security_issues.intro",
                "section_key": "front.security_issues",
                "rule_id": "R3.NARRATIVES",
                "edit_policy": "readonly",
                "dependencies": ["report_facts", "appendix_a", "correction_relations"],
                "baseline": {
                    "text": (
                        "本次信息系统商用密码应用安全性评估依据GB/T 39786—2021"
                        "《信息安全技术 信息系统密码应用基本要求》的"
                        f"{facts['classification_phrase']}要求，发现被测信息系统存在以下安全问题。"
                    ),
                    "italic": False,
                },
            },
            {
                "block_key": "recommendations.intro",
                "section_key": "front.security_issues",
                "rule_id": "R3.NARRATIVES",
                "edit_policy": "readonly",
                "dependencies": ["appendix_a", "correction_relations"],
                "baseline": {
                    "text": "建议被测信息系统根据实际情况和以下给出的建议进行整改。",
                    "italic": False,
                },
            },
        ]
    )
    for index, layer_rule in enumerate(rule_set.layers, start=1):
        layer_findings = findings_by_layer[layer_rule.code]
        visible = bool(layer_findings)
        problems = [
            {
                "number": item_index,
                "indicator_code": finding["indicator_code"],
                "indicator_name": finding["indicator_name"],
                "description": finding["problem_description"],
                "problem_items": finding["problem_items"],
            }
            for item_index, finding in enumerate(layer_findings, start=1)
        ]
        recommendations = [
            {
                "number": item["number"],
                "indicator_code": item["indicator_code"],
                "indicator_name": item["indicator_name"],
                "text": _recommendation(item["indicator_name"]),
            }
            for item in problems
        ]
        blocks.extend(
            [
                {
                    "block_key": f"security_issues.layer.{index}",
                    "section_key": "front.security_issues",
                    "rule_id": "R3.NARRATIVES",
                    "edit_policy": "readonly",
                    "dependencies": ["appendix_a", "correction_relations"],
                    "baseline": {
                        "visible": visible,
                        "layer_code": layer_rule.code,
                        "layer_name": layer_rule.name,
                        "problems": problems,
                        "italic": False,
                    },
                },
                {
                    "block_key": f"recommendations.layer.{index}",
                    "section_key": "front.security_issues",
                    "rule_id": "R3.NARRATIVES",
                    "edit_policy": "overrideable",
                    "dependencies": ["appendix_a", "correction_relations"],
                    "baseline": {
                        "visible": visible,
                        "layer_code": layer_rule.code,
                        "layer_name": layer_rule.name,
                        "items": recommendations,
                        "italic": False,
                    },
                },
            ]
        )
    risk_rows = []
    for index, risk in enumerate(risk_snapshot["rows"], start=1):
        level_label = {"high": "高", "medium": "中", "low": "低"}[risk["risk_level"]]
        analysis = (risk.get("analysis_override") or {}).get("text") or (
            f"“{risk['indicator_name']}”指标相关安全问题可能被所关联威胁利用，综合判定整体风险等级为{level_label}。"
        )
        risk_rows.append(
            {
                "number": index,
                "risk_uuid": risk["risk_uuid"],
                "layer_name": layers[risk["layer_code"]].name,
                "indicator_code": risk["indicator_code"],
                "indicator_name": risk["indicator_name"],
                "problem_description": risk["problem_description"],
                "threat_ids": risk["threat_ids"],
                "analysis": analysis,
                "risk_level": risk["risk_level"],
            }
        )
    blocks.extend(
        [
            {
                "block_key": "risk_analysis.summary",
                "section_key": "chapter.6",
                "rule_id": "R3.NARRATIVES",
                "edit_policy": "readonly",
                "dependencies": ["risks"],
                "baseline": {
                    "method_text": RISK_METHOD_TEXT,
                    "summary_text": (
                        f"根据《商用密码应用安全性评估高风险判定指引》{risk_snapshot['high_risk_judgment']}。"
                        f"经风险分析，系统存在高风险{risk_counts['high']}项，中风险{risk_counts['medium']}项，"
                        f"低风险{risk_counts['low']}项，具体见表6-1。"
                    ),
                    "statistics": risk_snapshot["counts"],
                },
            },
            {
                "block_key": "risk_analysis.rows",
                "section_key": "chapter.6",
                "rule_id": "R3.NARRATIVES",
                "edit_policy": "readonly",
                "dependencies": ["appendix_a", "correction_relations", "risks"],
                "baseline": {
                    "rows": risk_rows,
                    "statistics": risk_snapshot["counts"],
                    "always_render_full_threat_catalog": True,
                },
            },
            {
                "block_key": "assessment_conclusion",
                "section_key": "chapter.7",
                "rule_id": "R3.CONCLUSION",
                "edit_policy": "readonly",
                "dependencies": ["report_facts", "appendix_a", "correction_relations", "risks"],
                "baseline": {
                    "text": (
                        f"通过对{facts['assessed_organization']}的{facts['system_name']}的物理和环境安全、网络和通信安全、设备和计算安全、"
                        f"应用和数据安全、管理制度、人员管理、建设运行和应急处置等方面的测评，该系统综合得分为"
                        f"{conclusion['display_score']}分，"
                        + (
                            "系统密码应用未发现安全风险，"
                            if conclusion["overall_risk"] == "未发现安全风险"
                            else f"系统密码应用面临{conclusion['overall_risk']}风险，"
                        )
                        + f"{conclusion['conclusion']}GB/T 39786—2021《信息安全技术 信息系统密码应用基本要求》的"
                        f"{facts['classification_phrase']}要求。"
                    ),
                    **conclusion,
                },
            },
        ]
    )
    for block in blocks:
        if _contains_placeholder(block["baseline"]):
            raise ValueError(f"NARRATIVE_PLACEHOLDER_REMAINS:{block['block_key']}")
    return blocks
