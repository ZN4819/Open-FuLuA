"""生成运行时母版 manifest 与资产哈希清单。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.report_templates import analyze_report_template  # noqa: E402
from app.services.report_templates.models import (  # noqa: E402
    EXPECTED_BUSINESS_FIELD_COUNT,
    EXPECTED_OOXML_CONTENT_CONTROL_COUNT,
    EXPECTED_SEMANTIC_SCALAR_SLOT_COUNT,
    EXPECTED_TEMPLATE_CONTENT_CONTROL_COUNT,
    EXPECTED_WORD_ACCEPTANCE_EVIDENCE_SHA256,
    EXPECTED_WORD_CONTENT_CONTROL_COUNT,
    REQUIRED_README_RULE_REFS,
)
from app.services.report_templates.validator import (  # noqa: E402
    validate_field_dictionary,
    validate_narrative_templates,
    validate_rule_hints,
)

ASSET_DIR = ROOT / "templates" / "report" / "2023-2025.12.08"
WORD_ACCEPTANCE_EVIDENCE = ROOT / "docs" / "report-tool" / "evidence" / "r0-word-acceptance.json"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w": W, "r": R, "pr": PR, "ct": CT}
FIELD_NAMES = ("TOC", "PAGE", "SEQ", "REF", "PAGEREF", "STYLEREF")
FORBIDDEN_FIELD_NAMES = ("NUMPAGES",)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_asset(path: Path, payload: object) -> None:
    """Write deterministic UTF-8 JSON with LF line endings on every platform."""
    path.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True))


def signature(elements: list[etree._Element]) -> str:
    wrapper = etree.Element("contract")
    for element in elements:
        clone = xml(etree.tostring(element))
        for node in clone.iter():
            node.text = None
            node.tail = None
        wrapper.append(clone)
    return hashlib.sha256(etree.tostring(wrapper, method="c14n", exclusive=True)).hexdigest()


def integer_attr(node: etree._Element | None, name: str, default: int = 0) -> int:
    if node is None:
        return default
    return int(node.get(f"{{{W}}}{name}", default))


def reject_frozen_package_overwrite() -> None:
    freeze_path = ASSET_DIR / "asset_hashes.json"
    if not freeze_path.exists():
        return
    try:
        current = json.loads(freeze_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("REPORT_TEMPLATE_PACKAGE_OUTPUT_UNTRUSTED") from exc
    if current.get("freeze_record", {}).get("status") == "frozen":
        raise ValueError(
            "REPORT_TEMPLATE_PACKAGE_ALREADY_FROZEN: create a new package_id and output directory"
        )
    raise ValueError("REPORT_TEMPLATE_PACKAGE_OUTPUT_ALREADY_EXISTS")


def main() -> int:
    reject_frozen_package_overwrite()
    runtime = ASSET_DIR / "runtime_template.docx"
    fields = validate_field_dictionary(ASSET_DIR / "field_dictionary.json")
    rules = validate_rule_hints(ASSET_DIR / "rule_hints.json")
    validate_narrative_templates(ASSET_DIR / "narrative_templates.json")
    analysis = analyze_report_template(runtime, source_role="synthetic_fixture")
    word_evidence = json.loads(WORD_ACCEPTANCE_EVIDENCE.read_text(encoding="utf-8"))
    if (
        sha256(WORD_ACCEPTANCE_EVIDENCE) != EXPECTED_WORD_ACCEPTANCE_EVIDENCE_SHA256
        or word_evidence.get("package_id") != "report-2023-2025.12.08"
        or word_evidence.get("runtime_template_sha256") != sha256(runtime)
        or word_evidence.get("open_method") != "OpenNoRepairDialog"
        or word_evidence.get("display_alerts") != "all"
        or word_evidence.get("roundtrip_saved_and_reopened") is not True
        or word_evidence.get("section_count") != 17
        or word_evidence.get("table_count") != 55
        or word_evidence.get("content_control_count") != EXPECTED_WORD_CONTENT_CONTROL_COUNT
    ):
        raise ValueError("WORD_ACCEPTANCE_EVIDENCE_MISMATCH")

    with zipfile.ZipFile(runtime) as package:
        parts = {name: package.read(name) for name in package.namelist() if not name.endswith("/")}
    document = xml(parts["word/document.xml"])
    story_roots = [document]
    for name, data in parts.items():
        if re.fullmatch(r"word/(header|footer)\d+\.xml", name) or name in {"word/footnotes.xml", "word/endnotes.xml"}:
            story_roots.append(xml(data))
    sections = document.xpath("//w:sectPr", namespaces=NS)
    tables = document.xpath("/w:document/w:body/w:tbl", namespaces=NS)

    section_records = []
    for index, section in enumerate(sections, 1):
        page_size = section.find(f"{{{W}}}pgSz")
        margins = section.find(f"{{{W}}}pgMar")
        section_type = section.find(f"{{{W}}}type")
        references = []
        for reference in section.xpath("./w:headerReference | ./w:footerReference", namespaces=NS):
            references.append({
                "kind": "header" if etree.QName(reference).localname == "headerReference" else "footer",
                "type": reference.get(f"{{{W}}}type", "default"),
                "relationship_id": reference.get(f"{{{R}}}id", ""),
            })
        section_records.append({
            "section_id": f"section_{index:02d}",
            "order": index,
            "orientation": page_size.get(f"{{{W}}}orient", "portrait") if page_size is not None else "portrait",
            "page_size_twips": {"width": integer_attr(page_size, "w"), "height": integer_attr(page_size, "h")},
            "margins_twips": {name: integer_attr(margins, name) for name in ("top", "right", "bottom", "left", "header", "footer", "gutter")},
            "start_type": section_type.get(f"{{{W}}}val", "nextPage") if section_type is not None else "nextPage",
            "header_footer_references": references,
            "signature": analysis.section_signatures[index - 1].signature,
        })

    dynamic_tables = set(range(4, 16)) | set(range(38, 47)) | {53}
    table_records = []
    block_records = []
    for index, table in enumerate(tables, 1):
        rows = table.xpath("./w:tr", namespaces=NS)
        header_rows = 2 if index == 38 or 39 <= index <= 42 else (1 if index in dynamic_tables else 0)
        block_id = f"block.report_table_{index:03d}"
        table_records.append({
            "table_id": f"report_table_{index:03d}",
            "order": index,
            "column_count": len(table.xpath("./w:tblGrid/w:gridCol", namespaces=NS)),
            "table_anchor": f"rt_table_{index:03d}",
            "owner_block": block_id,
            "header_signature": signature(rows[: max(header_rows, 1)]),
            "signature": analysis.table_signatures[index - 1].signature,
            "dynamic_rows": {
                "strategy": "repeat_template_rows" if index in dynamic_tables else "fixed",
                "header_rows": header_rows,
                "template_row_count": max(len(rows) - header_rows, 0) if index in dynamic_tables else 0,
            },
        })
        block_records.append({
            "block_id": block_id,
            "start_anchor": f"block_table_{index:03d}_start",
            "end_anchor": f"block_table_{index:03d}_end",
            "table_anchor": f"rt_table_{index:03d}",
        })

    field_counts: Counter[str] = Counter()
    for name, data in parts.items():
        if name == "word/document.xml" or re.fullmatch(r"word/(header|footer)\d+\.xml", name) or name in {"word/footnotes.xml", "word/endnotes.xml"}:
            root = xml(data)
            instructions = root.xpath("//w:instrText/text() | //w:fldSimple/@w:instr", namespaces=NS)
            for instruction in instructions:
                match = re.search(r"\b(" + "|".join(FIELD_NAMES) + r")\b", instruction.upper())
                if match:
                    field_counts[match.group(1)] += 1

    styles = xml(parts["word/styles.xml"])
    numbering = xml(parts["word/numbering.xml"])
    relationship_types: set[str] = set()
    for name, data in parts.items():
        if name.endswith(".rels"):
            relationship_types.update(xml(data).xpath("/pr:Relationships/pr:Relationship/@Type", namespaces=NS))
    content_types = xml(parts["[Content_Types].xml"])
    content_type_contract = {
        "defaults": sorted(
            ({"extension": node.get("Extension", ""), "content_type": node.get("ContentType", "")} for node in content_types.findall(f"{{{CT}}}Default")),
            key=lambda item: item["extension"],
        ),
        "overrides": sorted(
            ({"part_name": node.get("PartName", ""), "content_type": node.get("ContentType", "")} for node in content_types.findall(f"{{{CT}}}Override")),
            key=lambda item: item["part_name"],
        ),
    }
    slots = [slot for field in fields.fields for slot in field.export_slots]
    semantic_tags = [slot.partition(":")[2] for slot in slots if slot.startswith("sdt:")]
    story_tag_values = [
        tag
        for root in story_roots
        for tag in root.xpath("//w:sdtPr/w:tag/@w:val", namespaces=NS)
    ]
    all_field_instructions = [
        instruction
        for root in story_roots
        for instruction in root.xpath("//w:instrText/text() | //w:fldSimple/@w:instr", namespaces=NS)
    ]
    if any(
        re.search(rf"\b{name}\b", instruction.upper())
        for name in FORBIDDEN_FIELD_NAMES
        for instruction in all_field_instructions
    ):
        raise ValueError("FORBIDDEN_WORD_FIELD_PRESENT")
    if len(fields.fields) != EXPECTED_BUSINESS_FIELD_COUNT:
        raise ValueError("BUSINESS_FIELD_COUNT_MISMATCH")
    if len(semantic_tags) != EXPECTED_SEMANTIC_SCALAR_SLOT_COUNT or len(set(semantic_tags)) != len(semantic_tags):
        raise ValueError("SEMANTIC_SCALAR_SLOT_COUNT_MISMATCH")
    if len(story_tag_values) != EXPECTED_OOXML_CONTENT_CONTROL_COUNT:
        raise ValueError("OOXML_CONTENT_CONTROL_COUNT_MISMATCH")
    template_tag_pattern = r"^template\.control\.\d{4}$"
    template_tag_count = sum(bool(re.fullmatch(template_tag_pattern, tag)) for tag in story_tag_values)
    if template_tag_count != EXPECTED_TEMPLATE_CONTENT_CONTROL_COUNT:
        raise ValueError("TEMPLATE_CONTENT_CONTROL_COUNT_MISMATCH")
    if len(rules.rules) != 121 or len(REQUIRED_README_RULE_REFS) != 70:
        raise ValueError("FREEZE_TRACEABILITY_COUNT_MISMATCH")
    if analysis.document.section_count != 17 or analysis.document.table_count != 55:
        raise ValueError("REPORT_TEMPLATE_STRUCTURE_COUNT_MISMATCH")
    manifest = {
        "schema_version": "1.0",
        "package_id": "report-2023-2025.12.08",
        "template_edition": "2023",
        "template_revision": "2025-12-08",
        "data_schema_compatibility": {"minimum": 3, "maximum": 3},
        "runtime_template_sha256": sha256(runtime),
        "allowed_parts": sorted(parts),
        "sections": section_records,
        "tables": table_records,
        "blocks": block_records,
        "controls": {
            "template_tag_pattern": template_tag_pattern,
            "template_expected_count": EXPECTED_TEMPLATE_CONTENT_CONTROL_COUNT,
            "semantic_scalar_tags": [{"tag": tag, "expected_count": 1} for tag in semantic_tags],
            "expected_total_count": EXPECTED_OOXML_CONTENT_CONTROL_COUNT,
            "field_export_slots": slots,
        },
        "expected_fields": {name: field_counts[name] for name in FIELD_NAMES},
        "required_style_ids": sorted(styles.xpath("/w:styles/w:style/@w:styleId", namespaces=NS)),
        "numbering": {
            "abstract_num_ids": sorted(numbering.xpath("/w:numbering/w:abstractNum/@w:abstractNumId", namespaces=NS), key=int),
            "num_ids": sorted(numbering.xpath("/w:numbering/w:num/@w:numId", namespaces=NS), key=int),
        },
        "bookmark_rules": {
            "table_anchor_pattern": r"^rt_table_\d{3}$",
            "block_start_pattern": r"^block_table_\d{3}_start$",
            "block_end_pattern": r"^block_table_\d{3}_end$",
        },
        "relationship_types": sorted(relationship_types),
        "content_types": content_type_contract,
        "forbidden": [
            "comments", "revisions", "external_relationships", "macros", "activex", "ole",
            "custom_xml", "customer_examples", "unreplaced_placeholders",
        ],
    }
    write_json_asset(ASSET_DIR / "manifest.json", manifest)
    hashes = {
        "schema_version": "2.0",
        "package_id": manifest["package_id"],
        "freeze_record": {
            "status": "frozen",
            "business_field_count": EXPECTED_BUSINESS_FIELD_COUNT,
            "readme_rule_count": len(REQUIRED_README_RULE_REFS),
            "semantic_scalar_slot_count": EXPECTED_SEMANTIC_SCALAR_SLOT_COUNT,
            "ooxml_content_control_count": EXPECTED_OOXML_CONTENT_CONTROL_COUNT,
            "word_content_control_count": EXPECTED_WORD_CONTENT_CONTROL_COUNT,
            "section_count": 17,
            "table_count": 55,
            "pending_rule_hint_count": 121,
            "pending_rule_hints_blocking": False,
            "word_acceptance_evidence_sha256": EXPECTED_WORD_ACCEPTANCE_EVIDENCE_SHA256,
        },
        "assets": {
            name: sha256(ASSET_DIR / name)
            for name in ("runtime_template.docx", "field_dictionary.json", "manifest.json", "rule_hints.json", "narrative_templates.json")
        },
    }
    write_json_asset(ASSET_DIR / "asset_hashes.json", hashes)
    print(sha256(ASSET_DIR / "asset_hashes.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
