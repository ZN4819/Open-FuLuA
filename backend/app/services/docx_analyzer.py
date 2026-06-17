from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}
W = f"{{{NS['w']}}}"


@dataclass(frozen=True)
class DocxAnalysis:
    sections: int
    tables: int
    content_controls: int
    dropdown_controls: int
    ref_fields: int
    seq_fields: int
    bookmarks: int
    images: int
    inline_images: int
    anchored_images: int
    missing_ref_targets: list[str]
    table_shapes: list[str]
    seq_names: dict[str, int]


class DocxAnalysisError(RuntimeError):
    """DOCX 分析失败。"""


def analyze_docx(path: str | Path) -> DocxAnalysis:
    docx_path = Path(path)
    if not docx_path.exists():
        raise DocxAnalysisError(f"DOCX 文件不存在：{docx_path}")

    with zipfile.ZipFile(docx_path) as package:
        document = _read_xml(package, "word/document.xml")

    field_instructions = _field_instructions(document)
    field_counts = _field_counts(field_instructions)
    ref_targets = _field_targets(field_instructions, "REF")
    bookmark_names = {
        _attr(bookmark, "w:name")
        for bookmark in document.findall(".//w:bookmarkStart", NS)
        if _attr(bookmark, "w:name")
    }
    missing_ref_targets = sorted({target for target in ref_targets if target not in bookmark_names})
    inline_images = len(document.findall(".//wp:inline", NS))
    anchored_images = len(document.findall(".//wp:anchor", NS))

    return DocxAnalysis(
        sections=len(document.findall(".//w:sectPr", NS)),
        tables=len(document.findall(".//w:tbl", NS)),
        content_controls=len(document.findall(".//w:sdt", NS)),
        dropdown_controls=len(document.findall(".//w:sdtPr/w:dropDownList", NS)),
        ref_fields=field_counts.get("REF", 0),
        seq_fields=field_counts.get("SEQ", 0),
        bookmarks=len(bookmark_names),
        images=inline_images + anchored_images,
        inline_images=inline_images,
        anchored_images=anchored_images,
        missing_ref_targets=missing_ref_targets,
        table_shapes=_table_shapes(document),
        seq_names=dict(_seq_names(field_instructions)),
    )


def _read_xml(package: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(package.read(name))
    except KeyError as exc:
        raise DocxAnalysisError(f"DOCX 缺少必要部件：{name}") from exc


def _qn(name: str) -> str:
    prefix, local_name = name.split(":", 1)
    return f"{{{NS[prefix]}}}{local_name}"


def _attr(element: ET.Element | None, name: str) -> str | None:
    if element is None:
        return None
    return element.get(_qn(name))


def _field_instructions(document: ET.Element) -> list[str]:
    instructions: list[str] = []

    for field in document.findall(".//w:fldSimple", NS):
        instruction = _attr(field, "w:instr")
        if instruction:
            instructions.append(_normalize_instruction(instruction))

    inside_field = False
    current_parts: list[str] = []
    for element in document.iter():
        if element.tag == W + "fldChar":
            field_type = _attr(element, "w:fldCharType")
            if field_type == "begin":
                inside_field = True
                current_parts = []
            elif field_type == "end":
                if inside_field and current_parts:
                    instructions.append(_normalize_instruction("".join(current_parts)))
                inside_field = False
                current_parts = []
        elif inside_field and element.tag == W + "instrText":
            current_parts.append(element.text or "")

    return instructions


def _normalize_instruction(instruction: str) -> str:
    return " ".join(instruction.split())


def _field_counts(instructions: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for instruction in instructions:
        match = re.match(r"([A-Za-z]+)\b", instruction)
        if match:
            counts[match.group(1).upper()] += 1
    return counts


def _field_targets(instructions: list[str], field_type: str) -> list[str]:
    targets: list[str] = []
    for instruction in instructions:
        match = re.match(r"([A-Za-z]+)\s+(\S+)", instruction)
        if match and match.group(1).upper() == field_type:
            targets.append(match.group(2))
    return targets


def _seq_names(instructions: list[str]) -> Counter[str]:
    names: Counter[str] = Counter()
    for instruction in instructions:
        match = re.match(r"SEQ\s+(\S+)", instruction, re.IGNORECASE)
        if match:
            names[match.group(1)] += 1
    return names


def _table_shapes(document: ET.Element) -> list[str]:
    shapes: list[str] = []
    for table in document.findall(".//w:tbl", NS):
        rows = table.findall("w:tr", NS)
        max_cells = max((len(row.findall("w:tc", NS)) for row in rows), default=0)
        shapes.append(f"{len(rows)}x{max_cells}")
    return shapes
