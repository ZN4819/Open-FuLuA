from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from ..template_profile import load_template_profile
from .document import scan_docx_structure
from .models import (
    DocxImportAssessmentRowModel,
    DocxImportIssueModel,
    DocxImportMetricResultModel,
    DocxImportParsedProject,
    DocxImportParsedSectionModel,
    DocxTableCandidate,
)
from .package import read_docx_package


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}
W = f"{{{NS['w']}}}"
M = f"{{{NS['m']}}}"

TECHNICAL_HEADER_MARKERS = {"测评单元", "测评对象", "结果记录", "量化指标"}
MANAGEMENT_HEADER_MARKERS = {"测评单元", "测评对象", "结果记录", "符合情况"}


@dataclass(frozen=True)
class ExpandedCell:
    text: str
    grid_span: int = 1
    vmerge: str | None = None


def parse_docx_core_tables(path: str | Path) -> DocxImportParsedProject:
    """解析附录 A 核心测评表，生成后续导入新项目可复用的结构化行。"""
    package = read_docx_package(path)
    profile = load_template_profile()
    structure = scan_docx_structure(path)
    issues = list(structure.issues)
    tables_by_body_index = _table_elements_by_body_index(package.document)
    candidate_by_section = _candidate_by_section(structure.table_candidates)

    parsed_sections: list[DocxImportParsedSectionModel] = []
    for preview in structure.sections:
        section_profile = _section_profile(profile, preview.code)
        candidate = candidate_by_section.get(preview.code)
        rows: list[DocxImportAssessmentRowModel] = []
        if candidate is not None:
            table = tables_by_body_index.get(candidate.body_index)
            if table is not None:
                rows = _parse_section_rows(table, candidate, section_profile, profile, issues)
        parsed_sections.append(
            DocxImportParsedSectionModel(
                code=preview.code,
                title=preview.title,
                table_title=preview.table_title,
                table_type=preview.table_type,
                rows=rows,
                image_count=preview.image_count,
                reference_count=preview.reference_count,
            )
        )

    summary = dict(structure.summary)
    summary["assessment_rows"] = sum(section.row_count for section in parsed_sections)
    summary["parsed_sections"] = sum(1 for section in parsed_sections if section.row_count > 0)
    summary["errors"] = sum(1 for issue in issues if issue.severity == "error")
    summary["warnings"] = sum(1 for issue in issues if issue.severity == "warning")
    summary["info"] = sum(1 for issue in issues if issue.severity == "info")

    return DocxImportParsedProject(
        suggested_project_name=structure.suggested_project_name,
        sections=parsed_sections,
        issues=issues,
        summary=summary,
    )


def _table_elements_by_body_index(document: ET.Element) -> dict[int, ET.Element]:
    body = document.find("w:body", NS)
    if body is None:
        return {}
    tables: dict[int, ET.Element] = {}
    for body_index, child in enumerate(list(body), start=1):
        if child.tag == W + "tbl":
            tables[body_index] = child
    return tables


def _candidate_by_section(candidates: list[DocxTableCandidate]) -> dict[str, DocxTableCandidate]:
    selected: dict[str, DocxTableCandidate] = {}
    for candidate in sorted(candidates, key=lambda item: (-item.confidence, item.table_index)):
        selected.setdefault(candidate.section_code, candidate)
    return selected


def _parse_section_rows(
    table: ET.Element,
    candidate: DocxTableCandidate,
    section_profile: dict,
    profile: dict,
    issues: list[DocxImportIssueModel],
) -> list[DocxImportAssessmentRowModel]:
    table_type = section_profile["table_type"]
    columns = profile["tables"][table_type]["columns"]
    keys = [column["key"] for column in columns]
    expanded_rows = _expanded_table_rows(table)
    header_count = _header_row_count(expanded_rows, table_type)
    rows: list[DocxImportAssessmentRowModel] = []

    for source_row_index, raw_values in enumerate(expanded_rows[header_count:], start=header_count + 1):
        values = _values_by_key(raw_values, keys, table_type)
        if _is_empty_import_row(values, table_type, profile):
            continue

        sort_order = len(rows) + 1
        metric_result = _metric_result(values, table_type)
        row = DocxImportAssessmentRowModel(
            section_code=section_profile["code"],
            unit=values.get("unit", ""),
            object_name=values.get("object_name", ""),
            record_text=values.get("record_text", ""),
            sort_order=sort_order,
            metric_result=metric_result,
            source_table_index=candidate.table_index,
            source_row_index=source_row_index,
        )
        rows.append(row)
        _append_row_issues(row, table_type, profile, issues)

    return rows


def _expanded_table_rows(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    active_vmerge: dict[int, str] = {}
    for row in table.findall("w:tr", NS):
        expanded: list[str] = []
        column_index = 0
        for cell in row.findall("w:tc", NS):
            grid_span = _grid_span(cell)
            vmerge = _vmerge(cell)
            raw_text = _cell_text(cell)
            for span_offset in range(grid_span):
                text = raw_text
                if vmerge == "continue":
                    text = active_vmerge.get(column_index, raw_text)
                elif vmerge == "restart":
                    active_vmerge[column_index] = raw_text
                else:
                    active_vmerge.pop(column_index, None)
                expanded.append(text if span_offset == 0 else text)
                column_index += 1
        rows.append(expanded)
    return rows


def _grid_span(cell: ET.Element) -> int:
    grid_span = cell.find("w:tcPr/w:gridSpan", NS)
    if grid_span is None:
        return 1
    value = grid_span.get(W + "val")
    try:
        return max(1, int(value or "1"))
    except ValueError:
        return 1


def _vmerge(cell: ET.Element) -> str | None:
    merge = cell.find("w:tcPr/w:vMerge", NS)
    if merge is None:
        return None
    value = merge.get(W + "val") or "continue"
    return "restart" if value == "restart" else "continue"


def _cell_text(cell: ET.Element) -> str:
    paragraphs = list(cell.iter(W + "p"))
    if not paragraphs:
        return _clean_text(_element_text(cell))
    lines = [_clean_text(_element_text(paragraph)) for paragraph in paragraphs]
    return "\n".join(line for line in lines if line)


def _element_text(element: ET.Element) -> str:
    parts: list[str] = []
    for child in element.iter():
        if child.tag in {W + "t", M + "t"}:
            parts.append(child.text or "")
        elif child.tag == W + "tab":
            parts.append("\t")
        elif child.tag in {W + "br", W + "cr"}:
            parts.append("\n")
    return "".join(parts)


def _clean_text(text: str) -> str:
    normalized = (text or "").replace("\u00a0", " ").replace("\u3000", " ")
    lines = [" ".join(line.split()) for line in normalized.splitlines()]
    return "\n".join(line for line in lines if line)


def _header_row_count(rows: list[list[str]], table_type: str) -> int:
    if not rows:
        return 0
    if table_type == "technical":
        first_row_text = "".join(rows[0])
        if "量化指标" in first_row_text:
            return 2 if len(rows) >= 2 else 1
        if len(rows) >= 2 and _is_technical_second_header_row(rows[1]):
            return 2
        return 1
    if _row_contains_any(rows[0], MANAGEMENT_HEADER_MARKERS):
        return 1
    return 1


def _is_technical_second_header_row(row: list[str]) -> bool:
    joined = "".join(row)
    header_phrases = {"密码使用有效性", "密码算法", "密钥管理安全", "测评对象评分"}
    if any(phrase in joined for phrase in header_phrases):
        return True
    exact_metric_cells = sum(1 for cell in row if cell.strip() in {"D", "A", "K"})
    return exact_metric_cells >= 2

def _row_contains_any(row: list[str], markers: set[str]) -> bool:
    joined = "".join(row)
    return any(marker in joined for marker in markers)


def _values_by_key(raw_values: list[str], keys: list[str], table_type: str) -> dict[str, str]:
    if table_type == "technical":
        return _technical_values_by_key(raw_values, keys)

    values: dict[str, str] = {}
    for index, key in enumerate(keys):
        values[key] = raw_values[index] if index < len(raw_values) else ""
    return values


def _technical_values_by_key(raw_values: list[str], keys: list[str]) -> dict[str, str]:
    values = {key: "" for key in keys}
    for index, key in enumerate(keys[:3]):
        values[key] = raw_values[index] if index < len(raw_values) else ""

    if len(raw_values) in {4, 5}:
        values["object_score"] = raw_values[3] if len(raw_values) >= 4 else ""
        values["unit_score"] = raw_values[4] if len(raw_values) >= 5 else ""
        return values

    for index, key in enumerate(keys[3:], start=3):
        values[key] = raw_values[index] if index < len(raw_values) else ""
    return values

def _metric_result(values: dict[str, str], table_type: str) -> DocxImportMetricResultModel:
    if table_type == "technical":
        return DocxImportMetricResultModel(
            d=values.get("d") or None,
            a=values.get("a") or None,
            k=values.get("k") or None,
            object_score=values.get("object_score") or None,
            unit_score=values.get("unit_score") or None,
        )
    return DocxImportMetricResultModel(
        unit_score=values.get("unit_score") or None,
        compliance=values.get("compliance") or None,
    )


def _is_empty_import_row(values: dict[str, str], table_type: str, profile: dict) -> bool:
    if any(values.get(key) for key in ("unit", "object_name", "record_text", "object_score", "unit_score")):
        return False
    if table_type == "technical":
        default_metric = profile["content_controls"]["technical_metric"].get("default", "/")
        return all((values.get(key) or default_metric) == default_metric for key in ("d", "a", "k"))
    default_compliance = profile["content_controls"]["management_compliance"].get("default", "不适用")
    return (values.get("compliance") or default_compliance) == default_compliance


def _append_row_issues(
    row: DocxImportAssessmentRowModel,
    table_type: str,
    profile: dict,
    issues: list[DocxImportIssueModel],
) -> None:
    for field_name, label, value in [
        ("unit", "测评单元", row.unit),
        ("object_name", "测评对象", row.object_name),
        ("record_text", "结果记录", row.record_text),
    ]:
        if not value:
            issues.append(
                DocxImportIssueModel(
                    severity="warning",
                    code="IMPORT_EMPTY_REQUIRED_CELL",
                    message=f"{row.section_code} 第 {row.sort_order} 行缺少{label}。",
                    section_code=row.section_code,
                    target=f"row:{row.sort_order}:{field_name}",
                )
            )

    if table_type == "technical":
        options = set(profile["content_controls"]["technical_metric"]["options"])
        metric_values = {
            "D": row.metric_result.d,
            "A": row.metric_result.a,
            "K": row.metric_result.k,
        }
        for label, value in metric_values.items():
            if value and value not in options:
                issues.append(
                    DocxImportIssueModel(
                        severity="warning",
                        code="IMPORT_INVALID_DAK_VALUE",
                        message=f"{row.section_code} 第 {row.sort_order} 行 {label} 值“{value}”不在模板选项内。",
                        section_code=row.section_code,
                        target=f"row:{row.sort_order}:{label.lower()}",
                    )
                )
        return

    options = set(profile["content_controls"]["management_compliance"]["options"])
    compliance = row.metric_result.compliance
    if compliance and compliance not in options:
        issues.append(
            DocxImportIssueModel(
                severity="warning",
                code="IMPORT_INVALID_COMPLIANCE_VALUE",
                message=f"{row.section_code} 第 {row.sort_order} 行符合情况“{compliance}”不在模板选项内。",
                section_code=row.section_code,
                target=f"row:{row.sort_order}:compliance",
            )
        )


def _section_profile(profile: dict, code: str) -> dict:
    for section in profile["sections"]:
        if section["code"] == code:
            return section
    raise ValueError(f"模板 profile 缺少章节：{code}")
