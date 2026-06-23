from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from ..template_profile import load_template_profile
from .models import (
    DocxImportIssueModel,
    DocxImportSectionPreviewModel,
    DocxStructureScan,
    DocxTableCandidate,
)
from .package import read_docx_package


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}
W = f"{{{NS['w']}}}"
M = f"{{{NS['m']}}}"
TABLE_CAPTION_RE = re.compile(r"表\s*A\s*-\s*([1-8])")
SECTION_CODE_RE = re.compile(r"\bA\s*-\s*([1-8])\b")


@dataclass(frozen=True)
class BodyBlock:
    kind: str
    body_index: int
    text: str
    row_count: int = 0
    column_count: int = 0


def scan_docx_structure(path: str | Path) -> DocxStructureScan:
    package = read_docx_package(path)
    profile = load_template_profile()
    blocks = _body_blocks(package.document)
    issues: list[DocxImportIssueModel] = []
    has_appendix_title = any("附录A测评结果记录" in block.text for block in blocks if block.kind == "paragraph")
    if not has_appendix_title:
        issues.append(
            DocxImportIssueModel(
                severity="error",
                code="IMPORT_NOT_APPENDIX_A",
                message="未识别到“附录A测评结果记录”总标题。",
            )
        )

    sections = _initial_section_state(profile)
    table_candidates: list[DocxTableCandidate] = []
    current_section_code: str | None = None
    table_index = 0

    for block in blocks:
        if block.kind == "paragraph":
            section_code = _match_section_code(block.text, profile)
            if section_code:
                current_section_code = section_code
                sections[section_code]["section_seen"] = True
                if not sections[section_code]["title"]:
                    sections[section_code]["title"] = _section_profile(profile, section_code)["title"]
            caption_code = _match_caption_code(block.text)
            if caption_code:
                current_section_code = caption_code
                sections[caption_code]["caption_seen"] = True
                sections[caption_code]["section_seen"] = True
                sections[caption_code]["table_title"] = _section_profile(profile, caption_code)["table_title"]
            continue

        table_index += 1
        section_code = current_section_code or _infer_section_code_from_table_order(table_index, profile)
        section = _section_profile(profile, section_code)
        table_type = section["table_type"]
        expected_columns = len(profile["tables"][table_type]["columns"])
        header_rows = 2 if table_type == "technical" else 1
        data_row_count = max(0, block.row_count - header_rows)
        confidence = _table_confidence(section_code, current_section_code, block.column_count, expected_columns)
        candidate = DocxTableCandidate(
            body_index=block.body_index,
            table_index=table_index,
            section_code=section_code,
            table_type=table_type,
            row_count=block.row_count,
            column_count=block.column_count,
            data_row_count=data_row_count,
            confidence=confidence,
        )
        table_candidates.append(candidate)

        state = sections[section_code]
        if not state["table_seen"]:
            state["table_seen"] = True
            state["row_count"] = data_row_count
            if not state["title"]:
                state["title"] = section["title"]
            if not state["table_title"]:
                state["table_title"] = section["table_title"]
        if block.column_count != expected_columns:
            issues.append(
                DocxImportIssueModel(
                    severity="error",
                    code="IMPORT_UNKNOWN_TABLE_SHAPE",
                    message=f"{section_code} 表格列数为 {block.column_count}，预期为 {expected_columns}。",
                    section_code=section_code,
                    target=f"table:{table_index}",
                )
            )

    for section in profile["sections"]:
        code = section["code"]
        state = sections[code]
        if not state["section_seen"]:
            issues.append(
                DocxImportIssueModel(
                    severity="warning",
                    code="IMPORT_MISSING_SECTION",
                    message=f"未识别到 {code} {section['title']} 章节标题或表题。",
                    section_code=code,
                )
            )
        if not state["table_seen"]:
            issues.append(
                DocxImportIssueModel(
                    severity="error",
                    code="IMPORT_MISSING_TABLE",
                    message=f"未识别到 {code} 核心测评结果表。",
                    section_code=code,
                )
            )

    summary = _summary(blocks, table_candidates, sections, package.media_paths, issues)
    previews = [
        DocxImportSectionPreviewModel(
            code=section["code"],
            title=sections[section["code"]]["title"] or section["title"],
            table_title=sections[section["code"]]["table_title"] or section["table_title"],
            table_type=section["table_type"],
            row_count=int(sections[section["code"]]["row_count"]),
            image_count=0,
            reference_count=0,
        )
        for section in profile["sections"]
    ]

    return DocxStructureScan(
        suggested_project_name=Path(path).stem,
        has_appendix_title=has_appendix_title,
        sections=previews,
        table_candidates=table_candidates,
        issues=issues,
        summary=summary,
    )


def _body_blocks(document: ET.Element) -> list[BodyBlock]:
    body = document.find("w:body", NS)
    if body is None:
        return []
    blocks: list[BodyBlock] = []
    for body_index, child in enumerate(list(body), start=1):
        if child.tag == W + "p":
            text = _normalize_text(_element_text(child))
            blocks.append(BodyBlock(kind="paragraph", body_index=body_index, text=text))
        elif child.tag == W + "tbl":
            row_count, column_count = _table_shape(child)
            text = _normalize_text(_element_text(child))
            blocks.append(
                BodyBlock(
                    kind="table",
                    body_index=body_index,
                    text=text,
                    row_count=row_count,
                    column_count=column_count,
                )
            )
    return blocks


def _table_shape(table: ET.Element) -> tuple[int, int]:
    rows = table.findall("w:tr", NS)
    max_columns = 0
    for row in rows:
        column_count = 0
        for cell in _row_cells(row):
            column_count += _grid_span(cell)
        max_columns = max(max_columns, column_count)
    return len(rows), max_columns


def _row_cells(row: ET.Element) -> list[ET.Element]:
    cells: list[ET.Element] = []
    for child in list(row):
        if child.tag == W + "tc":
            cells.append(child)
            continue
        if child.tag == W + "sdt":
            content = child.find("w:sdtContent", NS)
            if content is not None:
                cells.extend(item for item in list(content) if item.tag == W + "tc")
    return cells


def _grid_span(cell: ET.Element) -> int:
    grid_span = cell.find("w:tcPr/w:gridSpan", NS)
    if grid_span is None:
        return 1
    value = grid_span.get(W + "val")
    try:
        return max(1, int(value or "1"))
    except ValueError:
        return 1


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


def _normalize_text(text: str) -> str:
    return " ".join((text or "").replace("\u3000", " ").split())


def _initial_section_state(profile: dict) -> dict[str, dict[str, object]]:
    return {
        section["code"]: {
            "title": "",
            "table_title": "",
            "section_seen": False,
            "caption_seen": False,
            "table_seen": False,
            "row_count": 0,
        }
        for section in profile["sections"]
    }


def _match_section_code(text: str, profile: dict) -> str | None:
    if not text:
        return None
    code_match = SECTION_CODE_RE.search(text)
    if code_match:
        code = f"A-{code_match.group(1)}"
        section = _section_profile(profile, code)
        if section["title"] in text or text.strip().startswith(code):
            return code
    for section in profile["sections"]:
        if text == section["title"] or text.endswith(section["title"]):
            return section["code"]
    return None


def _match_caption_code(text: str) -> str | None:
    match = TABLE_CAPTION_RE.search(text or "")
    if not match:
        return None
    return f"A-{match.group(1)}"


def _infer_section_code_from_table_order(table_index: int, profile: dict) -> str:
    index = min(max(table_index, 1), len(profile["sections"])) - 1
    return profile["sections"][index]["code"]


def _section_profile(profile: dict, code: str) -> dict:
    for section in profile["sections"]:
        if section["code"] == code:
            return section
    raise ValueError(f"模板 profile 缺少章节：{code}")


def _table_confidence(section_code: str, current_section_code: str | None, column_count: int, expected_columns: int) -> float:
    confidence = 0.35
    if section_code == current_section_code:
        confidence += 0.4
    if column_count == expected_columns:
        confidence += 0.2
    return min(confidence, 0.95)


def _summary(
    blocks: list[BodyBlock],
    table_candidates: list[DocxTableCandidate],
    sections: dict[str, dict[str, object]],
    media_paths: list[str],
    issues: list[DocxImportIssueModel],
) -> dict[str, int]:
    return {
        "body_blocks": len(blocks),
        "paragraphs": sum(1 for block in blocks if block.kind == "paragraph"),
        "tables": len(table_candidates),
        "expected_tables": 8,
        "sections": sum(1 for state in sections.values() if state["section_seen"]),
        "expected_sections": 8,
        "media_files": len(media_paths),
        "errors": sum(1 for issue in issues if issue.severity == "error"),
        "warnings": sum(1 for issue in issues if issue.severity == "warning"),
        "info": sum(1 for issue in issues if issue.severity == "info"),
    }
