from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "templates" / "appendix_a" / "template_profile.json"
OUTPUT_PATH = ROOT / "templates" / "appendix_a" / "record_templates.json"
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
FIGURE_REFERENCE_RE = re.compile(r"图\s*A\s*-\s*\d+\s*-\s*\d+")
PLACEHOLDER = "[插入图片引用]"


def main() -> None:
    parser = argparse.ArgumentParser(description="从样本文档抽取结果记录模板。")
    parser.add_argument("--source", type=Path, default=None, help="样本文档路径，默认读取模板 profile 的 source_document。")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="输出 JSON 路径。")
    args = parser.parse_args()

    profile = _load_json(PROFILE_PATH)
    source = args.source or ROOT / profile["source_document"]
    templates = extract_record_templates(source, profile)
    payload = {
        "profile_id": "appendix_a_record_templates_v1",
        "source_document": profile["source_document"],
        "normalization": {
            "figure_reference_placeholder": PLACEHOLDER,
            "notes": "从样本文档抽取结果记录，固定图号统一替换为可编辑占位文字。"
        },
        "templates": templates,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(templates)} templates to {args.output}")


def extract_record_templates(source: Path, profile: dict[str, Any]) -> list[dict[str, Any]]:
    tables = _document_tables(source)
    sections = profile["sections"]
    if len(tables) < len(sections):
        raise ValueError(f"样本文档表格数量不足：期望至少 {len(sections)}，实际 {len(tables)}。")

    templates: list[dict[str, Any]] = []
    for table_index, section in enumerate(sections):
        section_code = section["code"]
        table_type = section["table_type"]
        header_rows = 2 if table_type == "technical" else 1
        current_unit = ""
        current_object = ""

        rows = tables[table_index].findall("./w:tr", NS)
        section_count = 0
        for row_number, row in enumerate(rows, start=1):
            cells = [_normalize_whitespace(_cell_text(cell)) for cell in row.findall("./w:tc", NS)]
            if row_number <= header_rows or len(cells) < 3:
                continue

            unit = cells[0]
            object_name = cells[1]
            record_text = cells[2]
            if unit:
                current_unit = unit
            if object_name:
                current_object = object_name
            if not record_text:
                continue

            section_count += 1
            normalized_record = normalize_record_text(record_text)
            templates.append(
                {
                    "id": f"{section_code.lower().replace('-', '')}-{section_count:03d}",
                    "section_code": section_code,
                    "table_type": table_type,
                    "unit": current_unit,
                    "object_name": current_object,
                    "title": _template_title(current_unit, current_object),
                    "record_text": normalized_record,
                    "source_row": row_number,
                }
            )

    return templates


def normalize_record_text(text: str) -> str:
    return _normalize_whitespace(FIGURE_REFERENCE_RE.sub(PLACEHOLDER, text))


def _document_tables(source: Path) -> list[etree._Element]:
    if not source.exists():
        raise FileNotFoundError(f"样本文档不存在：{source}")
    with zipfile.ZipFile(source) as package:
        document_xml = package.read("word/document.xml")
    document = etree.fromstring(document_xml)
    return document.findall(".//w:tbl", NS)


def _cell_text(cell: etree._Element) -> str:
    return "".join(cell.xpath(".//w:t/text()", namespaces=NS))


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _template_title(unit: str, object_name: str) -> str:
    if not object_name or object_name == unit:
        return unit
    return f"{unit} / {object_name}"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as profile_file:
        return json.load(profile_file)


if __name__ == "__main__":
    main()
