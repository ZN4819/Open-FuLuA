"""R6 完整报告的安全静态解析与模板指纹。"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from lxml import etree

from ..report_core.field_matrix import FieldMatrix, load_default_field_matrix
from ..resource_paths import resolve_resource_path
from ..services.docx_importer.tables import parse_full_report_appendix_tables
from ..services.report_templates import analyzer as template_analyzer
from ..services.report_templates.analyzer import UnsafePackageError, analyze_report_template
from ..services.scoring import (
    calculate_flat_management_rows,
    calculate_flat_technical_rows,
)
from ..services.xlsx_generator.generator import validate_score_workbook_rows


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"w": W, "pr": PR, "ct": CT, "a": A, "r": R}
DOCX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
KNOWN_IGNORED_OLE = {
    "word/embeddings/oleObject1.bin":
        "e25064867469d7a954991a6e8e2501cf1c3ef445e7d6ad9647aebddd1fd38aba",
}
HEADING_SEQUENCE = (
    "测评项目概述",
    "被测系统情况",
    "测评范围与方法",
    "单元测评",
    "整体测评",
    "风险分析",
    "评估结论",
    "附录A测评结果记录",
    "附录B密评活动有效性证明记录",
)
DANGEROUS_FLAG_CODES = {
    "has_macros": "MACRO_PRESENT",
    "has_activex": "ACTIVEX_PRESENT",
    "has_attached_template": "ATTACHED_TEMPLATE_PRESENT",
    "has_alt_chunk": "ALT_CHUNK_PRESENT",
}
ALIASES = {
    "report.header.report_number": "report.identity.number",
    "report.cover.system_name": "report.system.name",
    "report.declaration.system_name": "report.system.name",
    "report.header.system_name.1": "report.system.name",
    "report.header.system_name.2": "report.system.name",
    "report.header.system_name.3": "report.system.name",
}
class ReportImportParseError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_report_import(path: Path) -> dict[str, Any]:
    """安全扫描、指纹识别并产生候选。

    函数只读源副本；所有解析前后哈希必须一致。
    """
    source = Path(path)
    before_hash = _sha256_file(source)
    try:
        forensic = analyze_report_template(source, source_role="customer_sample")
    except UnsafePackageError as exc:
        code = str(exc).split(":", 1)[0] or "DOCX_PACKAGE_UNSAFE"
        raise ReportImportParseError(code, "DOCX 包未通过安全扫描。") from exc

    package_parts = _read_required_parts(source)
    document = _xml(package_parts["word/document.xml"], "word/document.xml")
    security_warnings = _validate_security(forensic, package_parts, document)
    fingerprint = _fingerprint(document, before_hash)
    if not fingerprint["matched"]:
        raise ReportImportParseError(
            "UNSUPPORTED_REPORT_TEMPLATE",
            "仅支持 2023 版、2025-12-08 修订的当前三级完整报告模板。",
        )

    matrix = load_default_field_matrix()
    candidates, candidate_issues = _semantic_candidates(document, matrix)
    tables = _table_snapshots(document)
    body_table_issues = _body_table_issues(tables, matrix)
    appendix_b, appendix_b_issues = _appendix_b_snapshots(tables, matrix)
    appendix_a, appendix_a_issues = _appendix_a_payload(source)
    chapter_snapshots, chapter_issues = _chapter_snapshots(document, matrix)
    image_snapshots, image_issues = _image_snapshots(document, package_parts)

    after_hash = _sha256_file(source)
    if after_hash != before_hash:
        raise ReportImportParseError("SOURCE_DOCX_CHANGED", "解析期间源副本发生变化。")

    issues = [
        *security_warnings, *candidate_issues, *chapter_issues,
        *body_table_issues, *appendix_a_issues, *appendix_b_issues, *image_issues,
    ]
    automatic = sum(
        issue["code"] == "AUTO_MAPPED_FIELD" and not issue["needs_confirmation"]
        for issue in issues
    )
    pending = sum(issue["needs_confirmation"] for issue in issues)
    unmapped = sum(issue["confidence"] == "unmapped" for issue in issues)
    summary = {
        "template_match": {
            "matched": True,
            "edition": "2023",
            "revision": "2025-12-08",
            "warnings": len(security_warnings),
        },
        "chapter_stats": {
            "headings": len(fingerprint["heading_matches"]),
            "tables": fingerprint["table_count"],
            "sections": fingerprint["section_count"],
        },
        "automatic_mappings": automatic,
        "pending_confirmation": pending,
        "unmapped_content": unmapped,
        "document_appendix": {
            "available": bool(appendix_a.get("complete")),
            "sections_present": [item.get("code") for item in appendix_a.get("sections", [])],
            "row_count": sum(len(item.get("rows", [])) for item in appendix_a.get("sections", [])),
        },
        "appendix_sources": [],
    }
    return {
        "source_sha256": before_hash,
        "detected_edition": "2023",
        "detected_revision": "2025-12-08",
        "fingerprint": fingerprint,
        "summary": summary,
        "issues": issues,
        "candidates": candidates,
        "appendix_a": appendix_a,
        "appendix_b": appendix_b,
        "chapter_snapshots": chapter_snapshots,
        "image_snapshots": image_snapshots,
        "table_snapshots": tables,
    }


def _read_required_parts(path: Path) -> dict[str, bytes]:
    wanted: set[str] = {"[Content_Types].xml", "word/document.xml"}
    parts: dict[str, bytes] = {}
    with Path(path).open("rb") as stream:
        raw = stream.read(template_analyzer.MAX_ARCHIVE_BYTES + 1)
    if len(raw) > template_analyzer.MAX_ARCHIVE_BYTES:
        raise ReportImportParseError("ZIP_ARCHIVE_LIMIT_EXCEEDED", "DOCX 包超过安全上限。")
    seen: set[str] = set()
    folded: set[str] = set()
    total_uncompressed = 0
    try:
        package_context = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ReportImportParseError("ZIP_INVALID", "DOCX 包无法读取。") from exc
    with package_context as package:
        infos = package.infolist()
        if len(infos) > template_analyzer.MAX_ENTRIES:
            raise ReportImportParseError("ZIP_ENTRY_LIMIT_EXCEEDED", "DOCX 包条目过多。")
        for info in infos:
            try:
                name = template_analyzer._validate_member(info, seen, folded)
            except UnsafePackageError as exc:
                raise ReportImportParseError(str(exc), "DOCX 包成员未通过安全校验。") from exc
            if info.is_dir():
                continue
            total_uncompressed += info.file_size
            if total_uncompressed > template_analyzer.MAX_TOTAL_BYTES:
                raise ReportImportParseError("ZIP_TOTAL_LIMIT_EXCEEDED", "DOCX 解压预算超限。")
            if (
                name in wanted
                or name.endswith(".rels")
                or (name.startswith("word/") and name.endswith(".xml"))
                or name.startswith("word/media/")
                or name.startswith("word/embeddings/")
                or name.startswith("word/activeX/")
                or name.endswith("vbaProject.bin")
            ):
                try:
                    parts[name] = template_analyzer._read_member(package, info)
                except UnsafePackageError as exc:
                    raise ReportImportParseError(str(exc), "DOCX 部件超过安全上限。") from exc
    if not wanted.issubset(parts):
        raise ReportImportParseError("OPC_REQUIRED_PART_MISSING", "DOCX 缺少必要部件。")
    return parts


def _validate_security(forensic: Any, parts: dict[str, bytes], document: etree._Element) -> list[dict[str, Any]]:
    for attribute, code in DANGEROUS_FLAG_CODES.items():
        if bool(getattr(forensic.flags, attribute)):
            raise ReportImportParseError(code, "DOCX 包含迁移不允许的活动内容。")

    _validate_content_type(parts["[Content_Types].xml"])
    _validate_ole(parts)
    external_count = _validate_external_relationships(parts)
    _validate_dde(parts)

    warnings: list[dict[str, Any]] = []
    if any(name.startswith("word/embeddings/") for name in parts):
        warnings.append(
            _issue(
                "KNOWN_OLE_IGNORED", "warning", "unmapped",
                source_locator="package:word/embeddings", original_text="",
            )
        )
    if external_count:
        warnings.append(
            _issue(
                "COMMENT_HYPERLINK_IGNORED", "warning", "unmapped",
                source_locator="part:word/comments.xml", original_text="",
            )
        )
    if forensic.document.comment_count:
        warnings.append(
            _issue(
                "TEMPLATE_COMMENTS_IGNORED", "warning", "unmapped",
                source_locator="part:word/comments.xml", original_text="",
            )
        )
    if forensic.document.revision_count:
        warnings.append(
            _issue(
                "UNRESOLVED_REVISIONS_IGNORED", "warning", "unmapped",
                source_locator="part:word/document.xml", original_text="",
            )
        )
    if forensic.flags.has_custom_xml:
        warnings.append(
            _issue(
                "CUSTOM_XML_IGNORED", "warning", "unmapped",
                source_locator="package:customXml", original_text="",
            )
        )
    if forensic.flags.has_digital_signatures:
        warnings.append(
            _issue(
                "DIGITAL_SIGNATURE_IGNORED", "warning", "unmapped",
                source_locator="package:_xmlsignatures", original_text="",
            )
        )
    return warnings


def _validate_content_type(raw: bytes) -> None:
    root = _xml(raw, "[Content_Types].xml")
    values = {
        item.get("ContentType", "")
        for item in root.findall(f"{{{CT}}}Override")
        if item.get("PartName") == "/word/document.xml"
    }
    if values != {DOCX_MAIN_CONTENT_TYPE}:
        raise ReportImportParseError("DOCX_CONTENT_TYPE_INVALID", "文件不是受支持的 .docx 文档。")


def _validate_ole(parts: dict[str, bytes]) -> None:
    embeddings = {
        name: hashlib.sha256(raw).hexdigest()
        for name, raw in parts.items()
        if name.startswith("word/embeddings/")
    }
    for name, digest in embeddings.items():
        if KNOWN_IGNORED_OLE.get(name) != digest:
            raise ReportImportParseError("OLE_OR_EMBEDDING_PRESENT", "DOCX 包含未知嵌入对象。")


def _validate_external_relationships(parts: dict[str, bytes]) -> int:
    allowed_count = 0
    for name, raw in parts.items():
        if not name.endswith(".rels"):
            continue
        root = _xml(raw, name)
        for relation in root.findall(f"{{{PR}}}Relationship"):
            if relation.get("TargetMode") != "External":
                continue
            rel_type = relation.get("Type", "")
            target = relation.get("Target", "")
            parsed = urlparse(target)
            allowed = (
                name == "word/_rels/comments.xml.rels"
                and rel_type.endswith("/hyperlink")
                and parsed.scheme.lower() in {"http", "https"}
                and bool(parsed.netloc)
            )
            if not allowed:
                raise ReportImportParseError("EXTERNAL_RELATIONSHIP_PRESENT", "DOCX 包含不允许的外部关系。")
            allowed_count += 1
    return allowed_count


def _validate_dde(parts: dict[str, bytes]) -> None:
    for name, raw in parts.items():
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        root = _xml(raw, name)
        for instruction in _field_instruction_candidates(root):
            normalized = " ".join(str(instruction).split())
            if re.search(r"(?i)(?:^|\s)DDE(?:AUTO)?(?:\s|$)", normalized):
                raise ReportImportParseError("DDE_FIELD_PRESENT", "DOCX 包含不允许的 DDE 字段。")


def _field_instruction_candidates(root: etree._Element) -> list[str]:
    """Collect complete Word field instructions, including split instrText runs."""
    candidates = [str(value) for value in root.xpath("//w:fldSimple/@w:instr", namespaces=NS)]
    field_stack: list[dict[str, Any]] = []
    loose_parts: list[str] = []

    def flush_loose() -> None:
        if loose_parts:
            candidates.append("".join(loose_parts))
            loose_parts.clear()

    for element in root.xpath("//w:fldChar | //w:instrText", namespaces=NS):
        if element.tag == f"{{{W}}}instrText":
            text = "".join(element.itertext())
            collecting = next(
                (field for field in reversed(field_stack) if field["collecting"]),
                None,
            )
            if collecting is None:
                loose_parts.append(text)
            else:
                collecting["parts"].append(text)
            continue

        flush_loose()
        field_type = str(element.get(f"{{{W}}}fldCharType") or "").lower()
        if field_type == "begin":
            field_stack.append({"parts": [], "collecting": True})
        elif field_type == "separate" and field_stack:
            field_stack[-1]["collecting"] = False
        elif field_type == "end" and field_stack:
            field = field_stack.pop()
            candidates.append("".join(field["parts"]))

    flush_loose()
    while field_stack:
        candidates.append("".join(field_stack.pop()["parts"]))
    return candidates


def _fingerprint(document: etree._Element, digest: str) -> dict[str, Any]:
    manifest = json.loads(
        resolve_resource_path("templates", "report", "2023-2025.12.08", "manifest.json")
        .read_text(encoding="utf-8")
    )
    tables = document.xpath("/w:document/w:body/w:tbl", namespaces=NS)
    columns = [len(table.xpath("./w:tblGrid/w:gridCol", namespaces=NS)) for table in tables]
    expected_columns = [int(item["column_count"]) for item in manifest["tables"]]
    paragraph_texts = [
        _normalize_text("".join(paragraph.xpath(".//w:t/text()", namespaces=NS)))
        for paragraph in document.xpath("/w:document/w:body/w:p", namespaces=NS)
    ]
    heading_matches: list[str] = []
    heading_indices: list[int] = []
    for heading in HEADING_SEQUENCE:
        normalized = _normalize_text(heading)
        try:
            index = paragraph_texts.index(normalized)
        except ValueError:
            continue
        heading_matches.append(heading)
        heading_indices.append(index)
    section_count = len(document.xpath("//w:sectPr", namespaces=NS))
    matched = (
        len(tables) == 55
        and columns == expected_columns
        and heading_matches == list(HEADING_SEQUENCE)
        and heading_indices == sorted(heading_indices)
        and len(set(heading_indices)) == len(HEADING_SEQUENCE)
        and section_count in {16, 17}
    )
    return {
        "sha256": digest,
        "table_count": len(tables),
        "section_count": section_count,
        "top_level_table_columns": columns,
        "heading_matches": heading_matches,
        "heading_indices": heading_indices,
        "matched": matched,
    }


def _semantic_candidates(document: etree._Element, matrix: FieldMatrix) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fields = {item.field_id: item for item in matrix.fields}
    sources_by_field: dict[str, list[tuple[str, str]]] = {}
    issues: list[dict[str, Any]] = []
    tag_counts: dict[str, int] = {}
    for control in document.xpath("//w:sdt", namespaces=NS):
        tags = control.xpath("./w:sdtPr/w:tag/@w:val", namespaces=NS)
        if not tags:
            continue
        tag = str(tags[0]).strip()
        if not tag.startswith("report."):
            continue
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
        locator = f"sdt:{tag}[{tag_counts[tag]}]"
        value = _normalize_value("".join(control.xpath("./w:sdtContent//w:t/text()", namespaces=NS)))
        field_id = ALIASES.get(tag, tag)
        if field_id not in fields:
            issues.append(
                _issue(
                    "UNMAPPED_SEMANTIC_SLOT", "warning", "unmapped",
                    field_path=tag, source_locator=locator, original_text=value,
                    needs_confirmation=True, blocks_final_export=True,
                )
            )
            continue
        sources_by_field.setdefault(field_id, []).append((locator, value))

    # 旧报告的内容控件 tag 可能已漂移，因此只对 R0 已冻结的稳定槽位
    # 做另一路取值。同一权威字段的多槽位会在下方统一归并和冲突检查。
    for field_id, locator, value in _stable_slot_sources(document):
        if field_id in fields:
            sources_by_field.setdefault(field_id, []).append((locator, value))

    candidates: list[dict[str, Any]] = []
    for field_id, source_values in sources_by_field.items():
        binding = fields[field_id]
        relation = _relation_for_field(matrix, field_id)
        locators = [item[0] for item in source_values]
        nonempty_values = [item[1] for item in source_values if item[1]]
        meaningful_values = [item for item in nonempty_values if not _looks_like_placeholder(item)]
        merge_values = meaningful_values or nonempty_values
        normalized_unique = list(dict.fromkeys(_normalize_value(item) for item in merge_values))
        original = "\n".join(nonempty_values)
        base = {
            "association_id": relation.relation_id if relation else None,
            "authority_field_id": field_id,
            "field_path": binding.entity_paths[0],
            "source_locator": ",".join(locators),
            "original_text": original,
            "source_value_hash": _value_hash(original),
        }
        if not relation:
            issues.append(
                _issue(
                    "FIELD_RELATION_UNAVAILABLE", "warning", "unmapped",
                    **base, needs_confirmation=True, blocks_final_export=True,
                )
            )
            continue
        if len(normalized_unique) > 1:
            candidate_value: Any = normalized_unique
            issues.append(
                _issue(
                    "REPEATED_SLOT_VALUE_CONFLICT", "warning", "ambiguous",
                    **base, candidate_value=candidate_value, needs_confirmation=True,
                    blocks_final_export=True,
                )
            )
            candidates.append({**base, "candidate_value": candidate_value, "confidence": "ambiguous"})
            continue
        candidate_value = normalized_unique[0] if normalized_unique else ""
        if binding.source_kind == "manual" and binding.editable:
            if _looks_like_placeholder(candidate_value):
                issues.append(
                    _issue(
                        "TEMPLATE_PLACEHOLDER_REQUIRES_REVIEW", "warning", "ambiguous",
                        **base, candidate_value=candidate_value, needs_confirmation=True,
                        blocks_final_export=True,
                    )
                )
                candidates.append({**base, "candidate_value": candidate_value, "confidence": "ambiguous"})
                continue
            confidence = "exact" if len(source_values) == 1 else "high"
            issues.append(
                _issue(
                    "AUTO_MAPPED_FIELD", "info", confidence,
                    **base, candidate_value=candidate_value, status="resolved",
                )
            )
            candidates.append({**base, "candidate_value": candidate_value, "confidence": confidence})
        elif binding.source_kind == "template_constant":
            issues.append(
                _issue(
                    "TEMPLATE_CONSTANT_COMPARISON_ONLY", "warning", "high",
                    **base, candidate_value=candidate_value,
                )
            )
        else:
            issues.append(
                _issue(
                    "DERIVED_VALUE_COMPARISON_ONLY", "info", "high",
                    **base, candidate_value=candidate_value,
                )
            )
    return candidates, issues


def _stable_slot_sources(document: etree._Element) -> list[tuple[str, str, str]]:
    sources: list[tuple[str, str, str]] = []
    paragraphs = document.xpath("/w:document/w:body/w:p", namespaces=NS)
    paragraph_values = [
        _normalize_value("".join(item.xpath(".//w:t/text()", namespaces=NS)))
        for item in paragraphs
    ]
    for index, text in enumerate(paragraph_values[:20]):
        if not re.match(r"^\s*报告编号\s*[:：]", text):
            continue
        cover_number = re.sub(r"^\s*报告编号\s*[:：]\s*", "", text)
        cover_number = cover_number.strip().strip("{}【】")
        if cover_number and "报告编号" not in cover_number:
            sources.append(("report.identity.number", f"paragraph:{index}", cover_number))
        break

    # 封面系统名只在其下一个非空段落是固定报告标题时才采集，
    # 不依赖 paragraph[3] 这类可漂移的绝对序号。
    nonempty = [(index, value) for index, value in enumerate(paragraph_values[:40]) if value]
    fixed_title = "商用密码应用安全性评估报告"
    for position, (_title_index, value) in enumerate(nonempty):
        if value != fixed_title:
            continue
        neighbors = []
        if position > 0:
            neighbors.append(nonempty[position - 1])
        if position + 1 < len(nonempty):
            neighbors.append(nonempty[position + 1])
        for index, candidate in neighbors:
            system_name = candidate.strip().strip("{}【】")
            if (
                system_name
                and system_name not in HEADING_SEQUENCE
                and system_name not in {"声 明", "声明"}
                and not _looks_like_placeholder(system_name)
            ):
                sources.append(("report.system.name", f"paragraph:{index}", system_name))
                break
        break

    tables = document.xpath("/w:document/w:body/w:tbl", namespaces=NS)
    if tables:
        assessed = _raw_table_cell(tables[0], 0, 1)
        if assessed:
            sources.append(("report.organization.assessed_name", "table:1/cell:1,2", assessed))
    if len(tables) > 1:
        assessed = _raw_table_cell(tables[1], 1, 1)
        if assessed:
            sources.append(("report.organization.assessed_name", "table:2/cell:2,2", assessed))
        system_name = _raw_table_cell(tables[1], 8, 1)
        if system_name:
            sources.append(("report.system.name", "table:2/cell:9,2", system_name))
    return sources


def _raw_table_cell(table: etree._Element, row_index: int, cell_index: int) -> str:
    rows = table.xpath("./w:tr", namespaces=NS)
    if row_index >= len(rows):
        return ""
    cells = rows[row_index].xpath("./w:tc", namespaces=NS)
    if cell_index >= len(cells):
        return ""
    return _normalize_value("".join(cells[cell_index].xpath(".//w:t/text()", namespaces=NS)))


def _appendix_a_payload(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        parsed = parse_full_report_appendix_tables(path)
    except Exception as exc:  # legacy parser errors are converted into an auditable R6 issue
        return {"sections": []}, [
            _issue(
                "APPENDIX_A_PARSE_FAILED", "error", "unmapped",
                source_locator="appendix:A", original_text=type(exc).__name__,
                needs_confirmation=True, blocks_confirmation=True, blocks_final_export=True,
            )
        ]

    sections: list[dict[str, Any]] = []
    for section in parsed.sections:
        rows: list[dict[str, Any]] = []
        for row in section.rows:
            raw = asdict(row)
            metric = dict(raw.get("metric_result") or {})
            if section.code in {"A-1", "A-2", "A-3", "A-4"}:
                metric = {
                    "d": metric.get("d"), "a": metric.get("a"), "k": metric.get("k"),
                    "ra": "1", "rk": "1", "object_score": None,
                    "unit_score": None, "compliance": None,
                }
            else:
                metric = {
                    "d": None, "a": None, "k": None, "ra": None, "rk": None,
                    "object_score": None, "unit_score": None,
                    "compliance": metric.get("compliance"),
                }
            raw["metric_result"] = metric
            raw["cross_references"] = []
            rows.append(raw)
        sections.append(
            {
                "code": section.code,
                "title": section.title,
                "table_title": section.table_title,
                "table_type": section.table_type,
                "rows": rows,
            }
        )
    issues: list[dict[str, Any]] = []
    for issue in parsed.issues:
        if issue.code == "IMPORT_UNKNOWN_TABLE_SHAPE":
            continue
        if issue.severity == "error":
            issues.append(
                _issue(
                    issue.code, "error", "unmapped",
                    field_path=issue.section_code or "", source_locator=issue.target or "appendix:A",
                    original_text=issue.message, needs_confirmation=True,
                    blocks_confirmation=True, blocks_final_export=True,
                )
            )
    complete = _appendix_sections_complete(sections)
    if not complete:
        issues.append(
            _issue(
                "APPENDIX_A_INCOMPLETE", "error", "unmapped",
                source_locator="appendix:A", original_text="",
                needs_confirmation=True, blocks_confirmation=True, blocks_final_export=True,
            )
        )
    # 旧的完整报告图片定位存在正文干扰，R6 宁可显式保留待审也不错配。
    issues.append(
        _issue(
            "DOCUMENT_APPENDIX_IMAGES_REQUIRE_REVIEW", "warning", "ambiguous",
            association_id=_association_for_id(load_default_field_matrix(), "report.appendix_a.records"),
            authority_field_id="report.appendix_a.records",
            field_path="evidence_images[*].file_path", source_locator="appendix:A/images",
            original_text="", needs_confirmation=True, blocks_final_export=True,
        )
    )
    return {"sections": sections, "complete": complete}, issues


def _appendix_sections_complete(sections: list[dict[str, Any]]) -> bool:
    if len(sections) != 8 or any(not item.get("rows") for item in sections):
        return False
    rows_by_section: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        code = str(section.get("code") or "")
        flat_rows: list[dict[str, Any]] = []
        for row in section.get("rows") or []:
            metric = dict(row.get("metric_result") or {})
            flat_rows.append(
                {
                    "unit": row.get("unit"),
                    "object_name": row.get("object_name"),
                    "record_text": row.get("record_text"),
                    **metric,
                }
            )
        if code in {"A-1", "A-2", "A-3", "A-4"}:
            flat_rows = calculate_flat_technical_rows(flat_rows, strict=False)
        else:
            flat_rows = calculate_flat_management_rows(flat_rows, strict=False)
        if any(not str(row.get("record_text") or "").strip() for row in flat_rows):
            return False
        rows_by_section[code] = flat_rows
    return not validate_score_workbook_rows(rows_by_section)


def _chapter_snapshots(
    document: etree._Element,
    matrix: FieldMatrix,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    chapter_titles = HEADING_SEQUENCE[:7]
    title_by_normalized = {_normalize_text(value): value for value in chapter_titles}
    paragraphs = document.xpath("/w:document/w:body/w:p", namespaces=NS)
    current_title: str | None = None
    values: dict[str, list[dict[str, str]]] = {title: [] for title in chapter_titles}
    for index, paragraph in enumerate(paragraphs):
        text = _normalize_value("".join(paragraph.xpath(".//w:t/text()", namespaces=NS)))
        normalized = _normalize_text(text)
        if normalized in title_by_normalized:
            current_title = title_by_normalized[normalized]
            continue
        style = paragraph.xpath("./w:pPr/w:pStyle/@w:val", namespaces=NS)
        if style and style[0] == "1" and current_title is not None:
            current_title = None
        if current_title is None or not text:
            continue
        values[current_title].append({"source_locator": f"paragraph:{index}", "text": text})

    association_id = _association_for_id(matrix, "report.narrative.manual_override")
    snapshots: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for chapter_index, title in enumerate(chapter_titles, start=1):
        items = values[title]
        text = "\n".join(item["text"] for item in items)
        snapshot = {
            "chapter": chapter_index,
            "title": title,
            "paragraphs": items,
            "sha256": _value_hash(text),
        }
        snapshots.append(snapshot)
        if text:
            issues.append(
                _issue(
                    "CHAPTER_TEXT_PRESERVED_AS_MANUAL_DRAFT", "warning", "ambiguous",
                    association_id=association_id,
                    authority_field_id="report.narrative.manual_override",
                    field_path=f"report_blocks[chapter_{chapter_index}].override_json",
                    source_locator=f"chapter:{chapter_index}", original_text=text,
                    candidate_value={"chapter": chapter_index, "text": text},
                    needs_confirmation=True, blocks_final_export=True,
                )
            )
    return snapshots, issues


def _image_snapshots(
    document: etree._Element,
    parts: dict[str, bytes],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    relationship_part = parts.get("word/_rels/document.xml.rels")
    relationships: dict[str, str] = {}
    if relationship_part:
        root = _xml(relationship_part, "word/_rels/document.xml.rels")
        for relation in root.findall(f"{{{PR}}}Relationship"):
            rel_id = relation.get("Id")
            target = relation.get("Target", "")
            if rel_id and relation.get("TargetMode") != "External":
                normalized = re.sub(r"^(?:\.\./)+", "", target.replace("\\", "/"))
                if normalized.startswith("media/"):
                    normalized = f"word/{normalized}"
                relationships[rel_id] = normalized

    heading_lookup = {_normalize_text(value): index + 1 for index, value in enumerate(HEADING_SEQUENCE)}
    current_location = "front_matter"
    located: dict[str, list[str]] = {}
    body = document.find("w:body", NS)
    if body is not None:
        for body_index, child in enumerate(list(body)):
            if child.tag == f"{{{W}}}p":
                text = _normalize_text("".join(child.xpath(".//w:t/text()", namespaces=NS)))
                if text in heading_lookup:
                    heading_index = heading_lookup[text]
                    current_location = (
                        f"chapter:{heading_index}" if heading_index <= 7
                        else ("appendix:A" if heading_index == 8 else "appendix:B")
                    )
            for rel_id in child.xpath(".//a:blip/@r:embed", namespaces=NS):
                part = relationships.get(str(rel_id))
                if part:
                    located.setdefault(part, []).append(f"body:{body_index}/{current_location}")

    snapshots: list[dict[str, Any]] = []
    for part, raw in sorted(parts.items()):
        if not part.startswith("word/media/"):
            continue
        snapshots.append(
            {
                "part": part,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "source_locators": located.get(part, ["package:unreferenced_media"]),
            }
        )
    if not snapshots:
        return snapshots, []
    text = json.dumps(snapshots, ensure_ascii=False, separators=(",", ":"))
    return snapshots, [
        _issue(
            "DOCUMENT_IMAGES_PRESERVED_FOR_REVIEW", "warning", "ambiguous",
            field_path="document_images[*]", source_locator="package:word/media",
            original_text=text, candidate_value={"count": len(snapshots)},
            needs_confirmation=True, blocks_final_export=True,
        )
    ]


def _body_table_issues(
    tables: list[dict[str, Any]],
    matrix: FieldMatrix,
) -> list[dict[str, Any]]:
    """把尚不能安全结构化写入 R2 的主体表显式放进审阅队列。"""

    mapped_fields = {
        2: "report.system.basic_information",
        4: "report.members.assessment_team",
        7: "report.system.crypto_products",
        11: "report.scope.application_catalog",
        14: "report.members.assessment_team",
        18: "report.assessment.special_indicators",
        38: "report.risks.problem_description",
    }
    comparison_only = set(range(25, 38)) | {3, 16, 17}
    fields = {item.field_id: item for item in matrix.fields}
    output: list[dict[str, Any]] = []
    for table_index, snapshot in enumerate(tables[:38], start=1):
        text = "\n".join(
            " | ".join(cell for cell in row if cell)
            for row in snapshot["rows"]
            if any(row)
        )
        if not text:
            continue
        field_id = mapped_fields.get(table_index)
        relation_id = _association_for_id(matrix, field_id)
        binding = fields.get(field_id) if field_id else None
        field_path = binding.entity_paths[0] if binding else f"report_tables[{table_index}]"
        if table_index in comparison_only:
            output.append(
                _issue(
                    "DERIVED_OR_TEMPLATE_TABLE_COMPARISON_ONLY",
                    "info" if table_index != 16 else "warning",
                    "high",
                    association_id=relation_id,
                    authority_field_id=field_id,
                    field_path=field_path,
                    source_locator=f"table:{table_index}",
                    original_text=text,
                    candidate_value={"table_index": table_index, "rows": snapshot["rows"]},
                )
            )
            continue
        output.append(
            _issue(
                "BODY_TABLE_REQUIRES_REVIEW",
                "warning",
                "ambiguous" if relation_id else "unmapped",
                association_id=relation_id,
                authority_field_id=field_id,
                field_path=field_path,
                source_locator=f"table:{table_index}",
                original_text=text,
                candidate_value={"table_index": table_index, "rows": snapshot["rows"]},
                needs_confirmation=True,
                blocks_final_export=True,
            )
        )
    return output


def _appendix_b_snapshots(tables: list[dict[str, Any]], matrix: FieldMatrix) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fields = (
        None,
        "report.appendix_b.travel_records",
        "report.appendix_b.onsite_records",
        None,
        "report.appendix_b.plan_review",
        "report.appendix_b.report_review",
        None,
        None,
        "report.appendix_b.filing_materials",
    )
    snapshots = tables[46:55]
    issues: list[dict[str, Any]] = []
    for offset, snapshot in enumerate(snapshots):
        field_id = fields[offset]
        text = "\n".join(
            " | ".join(cell for cell in row if cell)
            for row in snapshot["rows"]
            if any(row)
        )
        if not text:
            continue
        association_id = _association_for_id(matrix, field_id) if field_id else None
        issues.append(
            _issue(
                "APPENDIX_B_TABLE_REQUIRES_REVIEW", "warning",
                "ambiguous" if field_id else "unmapped",
                association_id=association_id,
                authority_field_id=field_id,
                field_path=field_id or f"appendix_b.table_{offset + 1}",
                source_locator=f"table:{47 + offset}", original_text=text,
                candidate_value=snapshot, needs_confirmation=True, blocks_final_export=True,
            )
        )
    return snapshots, issues


def _table_snapshots(document: etree._Element) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, table in enumerate(document.xpath("/w:document/w:body/w:tbl", namespaces=NS), start=1):
        rows: list[list[str]] = []
        for row in table.xpath("./w:tr", namespaces=NS):
            cells = [
                _normalize_value("".join(cell.xpath(".//w:t/text()", namespaces=NS)))
                for cell in row.xpath("./w:tc", namespaces=NS)
            ]
            rows.append(cells)
        output.append({"table_index": index, "rows": rows})
    return output


def _relation_for_field(matrix: FieldMatrix, field_id: str) -> Any | None:
    for relation in matrix.relations:
        if relation.authority_field_id == field_id:
            return relation
    for relation in matrix.relations:
        if field_id in relation.reference_field_ids:
            return relation
    return None


def _association_for_id(matrix: FieldMatrix, field_id: str | None) -> str | None:
    if not field_id:
        return None
    relation = _relation_for_field(matrix, field_id)
    return relation.relation_id if relation else None


def _issue(
    code: str,
    severity: str,
    confidence: str,
    *,
    association_id: str | None = None,
    authority_field_id: str | None = None,
    field_path: str = "",
    source_locator: str = "",
    original_text: str = "",
    source_value_hash: str | None = None,
    candidate_value: Any = None,
    status: str = "open",
    needs_confirmation: bool = False,
    blocks_confirmation: bool = False,
    blocks_final_export: bool = False,
) -> dict[str, Any]:
    text = original_text or ""
    return {
        "code": code,
        "severity": severity,
        "association_id": association_id,
        "authority_field_id": authority_field_id,
        "field_path": field_path,
        "source_locator": source_locator,
        "original_text": text,
        "source_value_hash": source_value_hash or (_value_hash(text) if text else None),
        "candidate_value": candidate_value,
        "confidence": confidence,
        "status": status,
        "needs_confirmation": needs_confirmation,
        "blocks_confirmation": blocks_confirmation,
        "blocks_final_export": blocks_final_export,
    }


def _xml(raw: bytes, part: str) -> etree._Element:
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ReportImportParseError("XML_DTD_OR_ENTITY_FORBIDDEN", f"{part} 包含不允许的 XML 声明。")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=False)
    try:
        return etree.fromstring(raw, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ReportImportParseError("XML_INVALID", f"{part} 无法解析。") from exc


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip()


def _normalize_value(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _looks_like_placeholder(value: str) -> bool:
    normalized = (value or "").strip()
    if not normalized:
        return True
    return bool(
        re.search(r"[【】{}]|X{2,}|x{2,}|_ *_{1,}", normalized)
        or normalized in {"被测单位", "被测系统名称", "被测信息系统名称", "报告编号"}
    )


def _value_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
