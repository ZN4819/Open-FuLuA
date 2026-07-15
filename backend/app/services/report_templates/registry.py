"""只读、失败隔离的完整报告模板注册表。"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from lxml import etree

from ...resource_paths import resolve_resource_path
from ...runtime import SCHEMA_VERSION
from .models import (
    EXPECTED_BUSINESS_FIELD_COUNT,
    EXPECTED_OOXML_CONTENT_CONTROL_COUNT,
    EXPECTED_SEMANTIC_SCALAR_SLOT_COUNT,
    EXPECTED_TEMPLATE_CONTENT_CONTROL_COUNT,
    EXPECTED_WORD_ACCEPTANCE_EVIDENCE_SHA256,
    EXPECTED_WORD_CONTENT_CONTROL_COUNT,
    REQUIRED_README_RULE_REFS,
)
from .validator import validate_field_dictionary_bytes, validate_narrative_templates_bytes, validate_rule_hints_bytes

PACKAGE_ID = "report-2023-2025.12.08"
PACKAGE_RELATIVE_PATH = ("templates", "report", "2023-2025.12.08")
TRUSTED_ASSET_HASHES_SHA256 = "2c58c4d6b58192276a1fceb5c1b61819c1e6c6c00b89aa31e2349aadf07aa654"
EXPECTED_ASSETS = ("runtime_template.docx", "field_dictionary.json", "manifest.json", "rule_hints.json", "narrative_templates.json")
FIELD_NAMES = ("TOC", "PAGE", "SEQ", "REF", "PAGEREF", "STYLEREF")
FORBIDDEN_FIELD_NAMES = ("NUMPAGES",)
EXPECTED_FREEZE_RECORD = {
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
}
APPROVED_WORKFLOW_IMAGE_PART = "word/media/image1.emf"
APPROVED_WORKFLOW_IMAGE_SHA256 = "008976a91115718e266c4dffcf3985fe92d2ee00063eac1fc42be592100d2a86"


class ReportTemplateUnavailable(RuntimeError):
    def __init__(self, code: str, asset: str | None = None) -> None:
        super().__init__(code)
        self.code, self.asset = code, asset


@dataclass(frozen=True)
class ReportTemplatePackage:
    package_id: str
    template_edition: str
    template_revision: str
    status: str
    manifest: dict[str, Any]
    fields: tuple[dict[str, Any], ...]
    rule_contracts: tuple[dict[str, Any], ...]
    projection_catalog: tuple[str, ...]
    rule_hints: tuple[dict[str, Any], ...]
    runtime_template_bytes: bytes

    def safe_summary(self) -> dict[str, Any]:
        return {"package_id": self.package_id, "template_edition": self.template_edition, "template_revision": self.template_revision, "status": self.status}


class ReportTemplateRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._package: ReportTemplatePackage | None = None
        self._failure: ReportTemplateUnavailable | None = None

    def _root(self) -> Path:
        candidate = resolve_resource_path(*PACKAGE_RELATIVE_PATH)
        if self._is_reparse_or_symlink(candidate):
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_PATH_UNTRUSTED")
        return candidate.resolve()

    @staticmethod
    def _is_reparse_or_symlink(path: Path) -> bool:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))

    @staticmethod
    def _validate_runtime_contract(runtime: bytes, manifest: dict[str, Any], slots: list[str]) -> None:
        with zipfile.ZipFile(io.BytesIO(runtime)) as package:
            parts = {name: package.read(name) for name in package.namelist() if not name.endswith("/")}
            document = etree.fromstring(parts["word/document.xml"])
            story_roots = [document]
            for name in parts:
                if re.fullmatch(r"word/(header|footer)\d+\.xml", name) or name in {"word/footnotes.xml", "word/endnotes.xml"}:
                    story_roots.append(etree.fromstring(parts[name]))
        media_parts = sorted(name for name in parts if name.startswith("word/media/"))
        if (
            media_parts != [APPROVED_WORKFLOW_IMAGE_PART]
            or hashlib.sha256(parts[APPROVED_WORKFLOW_IMAGE_PART]).hexdigest() != APPROVED_WORKFLOW_IMAGE_SHA256
        ):
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_WORKFLOW_IMAGE_MISMATCH", "runtime_template.docx")
        word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        office_rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        content_type_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
        ns = {"w": word_ns, "r": office_rel_ns, "pr": rel_ns, "ct": content_type_ns}

        compatibility = manifest.get("data_schema_compatibility", {})
        current_schema = int(SCHEMA_VERSION)
        if not (compatibility.get("minimum") <= current_schema <= compatibility.get("maximum")):
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_SCHEMA_INCOMPATIBLE", "manifest.json")
        if manifest.get("allowed_parts") != sorted(parts):
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_PART_MANIFEST_MISMATCH", "runtime_template.docx")

        def structural_signature(elements: list[etree._Element], *, group: bool = False) -> str:
            wrapper = etree.Element("contract")
            for element in elements:
                clone = etree.fromstring(etree.tostring(element))
                for node in clone.iter():
                    node.text = None
                    node.tail = None
                wrapper.append(clone)
            target = wrapper if group else wrapper[0]
            return hashlib.sha256(etree.tostring(target, method="c14n", exclusive=True)).hexdigest()

        sections = document.xpath("//w:sectPr", namespaces=ns)
        section_contracts = manifest.get("sections", [])
        if len(sections) != 17 or len(section_contracts) != 17:
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_SECTION_COUNT_MISMATCH", "manifest.json")
        section_required = {"section_id", "order", "orientation", "page_size_twips", "margins_twips", "start_type", "header_footer_references", "signature"}
        for index, (section, contract) in enumerate(zip(sections, section_contracts, strict=True), 1):
            page_size = section.find(f"{{{word_ns}}}pgSz")
            margins = section.find(f"{{{word_ns}}}pgMar")
            section_type = section.find(f"{{{word_ns}}}type")
            references = []
            for reference in section.xpath("./w:headerReference | ./w:footerReference", namespaces=ns):
                references.append({
                    "kind": "header" if etree.QName(reference).localname == "headerReference" else "footer",
                    "type": reference.get(f"{{{word_ns}}}type", "default"),
                    "relationship_id": reference.get(f"{{{office_rel_ns}}}id", ""),
                })
            expected_section_values = {
                "section_id": f"section_{index:02d}",
                "order": index,
                "orientation": page_size.get(f"{{{word_ns}}}orient", "portrait") if page_size is not None else "portrait",
                "page_size_twips": {"width": int(page_size.get(f"{{{word_ns}}}w", 0)) if page_size is not None else 0, "height": int(page_size.get(f"{{{word_ns}}}h", 0)) if page_size is not None else 0},
                "margins_twips": {name: int(margins.get(f"{{{word_ns}}}{name}", 0)) if margins is not None else 0 for name in ("top", "right", "bottom", "left", "header", "footer", "gutter")},
                "start_type": section_type.get(f"{{{word_ns}}}val", "nextPage") if section_type is not None else "nextPage",
                "header_footer_references": references,
            }
            if (
                not section_required.issubset(contract)
                or any(contract.get(key) != value for key, value in expected_section_values.items())
                or structural_signature([section]) != contract.get("signature")
            ):
                raise ReportTemplateUnavailable("REPORT_TEMPLATE_SECTION_CONTRACT_MISMATCH", "manifest.json")

        tables = document.xpath("/w:document/w:body/w:tbl", namespaces=ns)
        table_contracts = manifest.get("tables", [])
        if len(tables) != 55 or len(table_contracts) != 55:
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_TABLE_COUNT_MISMATCH", "manifest.json")
        table_required = {"table_id", "order", "column_count", "table_anchor", "owner_block", "header_signature", "signature", "dynamic_rows"}
        dynamic_tables = set(range(4, 16)) | set(range(38, 47)) | {53}
        for index, (table, contract) in enumerate(zip(tables, table_contracts, strict=True), 1):
            rows = table.xpath("./w:tr", namespaces=ns)
            dynamic = contract.get("dynamic_rows", {})
            header_rows = dynamic.get("header_rows")
            expected_header_rows = 2 if index == 38 or 39 <= index <= 42 else (1 if index in dynamic_tables else 0)
            if (
                not table_required.issubset(contract)
                or contract.get("order") != index
                or contract.get("table_id") != f"report_table_{index:03d}"
                or contract.get("table_anchor") != f"rt_table_{index:03d}"
                or contract.get("owner_block") != f"block.report_table_{index:03d}"
                or not isinstance(header_rows, int)
                or header_rows != expected_header_rows
                or dynamic.get("strategy") != ("repeat_template_rows" if index in dynamic_tables else "fixed")
                or dynamic.get("template_row_count") != (max(len(rows) - header_rows, 0) if index in dynamic_tables else 0)
                or contract.get("column_count") != len(table.xpath("./w:tblGrid/w:gridCol", namespaces=ns))
                or structural_signature([table]) != contract.get("signature")
                or structural_signature(rows[: max(header_rows, 1)], group=True) != contract.get("header_signature")
            ):
                raise ReportTemplateUnavailable("REPORT_TEMPLATE_TABLE_CONTRACT_MISMATCH", "manifest.json")

        bookmarks = set(document.xpath("//w:bookmarkStart/@w:name", namespaces=ns))
        tag_values = [
            tag
            for root in story_roots
            for tag in root.xpath("//w:sdtPr/w:tag/@w:val", namespaces=ns)
        ]
        tags = set(tag_values)
        controls = manifest.get("controls", {})
        manifest_slots = controls.get("field_export_slots")
        if manifest_slots != slots:
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_FIELD_SLOT_MANIFEST_MISMATCH", "manifest.json")
        for slot in slots:
            kind, separator, target = slot.partition(":")
            if not separator or (kind == "bookmark" and target not in bookmarks) or (kind == "sdt" and target not in tags) or kind not in {"bookmark", "sdt"}:
                raise ReportTemplateUnavailable("REPORT_TEMPLATE_FIELD_SLOT_MISSING", "runtime_template.docx")

        semantic_contract = controls.get("semantic_scalar_tags", [])
        expected_semantic = {item.get("tag"): item.get("expected_count") for item in semantic_contract}
        slot_semantic = {slot.partition(":")[2] for slot in slots if slot.startswith("sdt:")}
        if set(expected_semantic) != slot_semantic or len(expected_semantic) != EXPECTED_SEMANTIC_SCALAR_SLOT_COUNT or any(tag_values.count(tag) != count for tag, count in expected_semantic.items()):
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_SEMANTIC_CONTROL_MISMATCH", "manifest.json")
        template_pattern = controls.get("template_tag_pattern")
        template_count = sum(bool(re.fullmatch(template_pattern, tag)) for tag in tag_values) if isinstance(template_pattern, str) else -1
        if (
            template_count != EXPECTED_TEMPLATE_CONTENT_CONTROL_COUNT
            or controls.get("template_expected_count") != EXPECTED_TEMPLATE_CONTENT_CONTROL_COUNT
            or len(tag_values) != EXPECTED_OOXML_CONTENT_CONTROL_COUNT
            or controls.get("expected_total_count") != EXPECTED_OOXML_CONTENT_CONTROL_COUNT
        ):
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_CONTROL_COUNT_MISMATCH", "manifest.json")

        blocks = manifest.get("blocks", [])
        if len(blocks) != 55:
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_BLOCK_COUNT_MISMATCH", "manifest.json")
        for index, block in enumerate(blocks, 1):
            expected = {
                "block_id": f"block.report_table_{index:03d}",
                "start_anchor": f"block_table_{index:03d}_start",
                "end_anchor": f"block_table_{index:03d}_end",
                "table_anchor": f"rt_table_{index:03d}",
            }
            if block != expected or not {expected["start_anchor"], expected["end_anchor"], expected["table_anchor"]}.issubset(bookmarks):
                raise ReportTemplateUnavailable("REPORT_TEMPLATE_BLOCK_ANCHOR_MISMATCH", "manifest.json")
        if manifest.get("bookmark_rules") != {
            "table_anchor_pattern": r"^rt_table_\d{3}$",
            "block_start_pattern": r"^block_table_\d{3}_start$",
            "block_end_pattern": r"^block_table_\d{3}_end$",
        }:
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_BOOKMARK_RULE_MISMATCH", "manifest.json")

        # A-7 是第 45 表；其每个对象必须拥有独立的“符合情况”单元格。
        for row in tables[44].xpath("./w:tr[position()>1]", namespaces=ns):
            cells = row.xpath("./w:tc | ./w:sdt/w:sdtContent/w:tc", namespaces=ns)
            if len(cells) < 4 or cells[3].xpath("./w:tcPr/w:vMerge", namespaces=ns):
                raise ReportTemplateUnavailable("REPORT_TEMPLATE_A7_OBJECT_INPUT_MERGED", "runtime_template.docx")

        field_counts: Counter[str] = Counter()
        for root in story_roots:
            for instruction in root.xpath("//w:instrText/text() | //w:fldSimple/@w:instr", namespaces=ns):
                if any(re.search(rf"\b{name}\b", instruction.upper()) for name in FORBIDDEN_FIELD_NAMES):
                    raise ReportTemplateUnavailable("REPORT_TEMPLATE_FORBIDDEN_FIELD_PRESENT", "runtime_template.docx")
                match = re.search(r"\b(" + "|".join(FIELD_NAMES) + r")\b", instruction.upper())
                if match:
                    field_counts[match.group(1)] += 1
        if manifest.get("expected_fields") != {name: field_counts[name] for name in FIELD_NAMES}:
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_FIELD_INSTRUCTION_MISMATCH", "manifest.json")

        styles = etree.fromstring(parts["word/styles.xml"])
        actual_styles = set(styles.xpath("/w:styles/w:style/@w:styleId", namespaces=ns))
        if not set(manifest.get("required_style_ids", [])).issubset(actual_styles):
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_STYLE_MISMATCH", "manifest.json")
        numbering = etree.fromstring(parts["word/numbering.xml"])
        actual_numbering = {
            "abstract_num_ids": sorted(numbering.xpath("/w:numbering/w:abstractNum/@w:abstractNumId", namespaces=ns), key=int),
            "num_ids": sorted(numbering.xpath("/w:numbering/w:num/@w:numId", namespaces=ns), key=int),
        }
        if manifest.get("numbering") != actual_numbering:
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_NUMBERING_MISMATCH", "manifest.json")

        relationship_types: set[str] = set()
        has_external = False
        for name, data in parts.items():
            if name.endswith(".rels"):
                relationships = etree.fromstring(data)
                relationship_types.update(relationships.xpath("/pr:Relationships/pr:Relationship/@Type", namespaces=ns))
                has_external = has_external or bool(relationships.xpath("/pr:Relationships/pr:Relationship[@TargetMode='External']", namespaces=ns))
        if sorted(relationship_types) != manifest.get("relationship_types") or has_external:
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_RELATIONSHIP_MISMATCH", "manifest.json")
        content_types = etree.fromstring(parts["[Content_Types].xml"])
        actual_content_types = {
            "defaults": sorted(({"extension": node.get("Extension", ""), "content_type": node.get("ContentType", "")} for node in content_types.findall(f"{{{content_type_ns}}}Default")), key=lambda item: item["extension"]),
            "overrides": sorted(({"part_name": node.get("PartName", ""), "content_type": node.get("ContentType", "")} for node in content_types.findall(f"{{{content_type_ns}}}Override")), key=lambda item: item["part_name"]),
        }
        if actual_content_types != manifest.get("content_types"):
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_CONTENT_TYPE_MISMATCH", "manifest.json")

        revisions = sum(len(root.xpath("//w:ins | //w:del | //w:moveFrom | //w:moveTo", namespaces=ns)) for root in story_roots)
        forbidden_part = any(
            name.startswith(("customXml/", "word/activeX/", "word/embeddings/", "_xmlsignatures/"))
            or "comments" in name.lower()
            or name.endswith("vbaProject.bin")
            for name in parts
        )
        if revisions or forbidden_part:
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_FORBIDDEN_CONTENT_PRESENT", "runtime_template.docx")
        required_forbidden = {"comments", "revisions", "external_relationships", "macros", "activex", "ole", "custom_xml", "customer_examples", "unreplaced_placeholders"}
        if set(manifest.get("forbidden", [])) != required_forbidden:
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_FORBIDDEN_CONTRACT_MISMATCH", "manifest.json")
        visible_text = "".join(text for root in story_roots for text in root.xpath("//w:t/text()", namespaces=ns))
        if "选择一项。" in visible_text or "示例" in visible_text or "RaRk" in visible_text or re.search(r"\{[^{}]*\}|(?<![A-Za-z])X{1,20}(?![A-Za-z])", visible_text):
            raise ReportTemplateUnavailable("REPORT_TEMPLATE_EXAMPLE_CONTENT_PRESENT", "runtime_template.docx")

    def load(self, *, force: bool = False) -> ReportTemplatePackage:
        with self._lock:
            if self._package is not None and not force:
                return self._package
            if self._failure is not None and not force:
                raise self._failure
            try:
                root = self._root()
                hash_bytes = (root / "asset_hashes.json").read_bytes()
                if hashlib.sha256(hash_bytes).hexdigest() != TRUSTED_ASSET_HASHES_SHA256:
                    raise ReportTemplateUnavailable("REPORT_TEMPLATE_TRUST_ROOT_MISMATCH", "asset_hashes.json")
                hashes = json.loads(hash_bytes)
                if (
                    hashes.get("schema_version") != "2.0"
                    or hashes.get("package_id") != PACKAGE_ID
                    or hashes.get("freeze_record") != EXPECTED_FREEZE_RECORD
                    or set(hashes.get("assets", {})) != set(EXPECTED_ASSETS)
                ):
                    raise ReportTemplateUnavailable("REPORT_TEMPLATE_FREEZE_RECORD_MISMATCH", "asset_hashes.json")
                loaded: dict[str, bytes] = {}
                for asset in EXPECTED_ASSETS:
                    candidate = root / asset
                    if self._is_reparse_or_symlink(candidate):
                        raise ReportTemplateUnavailable("REPORT_TEMPLATE_PATH_UNTRUSTED", asset)
                    path = candidate.resolve()
                    if path.parent != root:
                        raise ReportTemplateUnavailable("REPORT_TEMPLATE_PATH_UNTRUSTED", asset)
                    data = path.read_bytes()
                    if hashlib.sha256(data).hexdigest() != hashes.get("assets", {}).get(asset):
                        raise ReportTemplateUnavailable("REPORT_TEMPLATE_HASH_MISMATCH", asset)
                    loaded[asset] = data
                manifest = json.loads(loaded["manifest.json"])
                if manifest.get("package_id") != PACKAGE_ID:
                    raise ReportTemplateUnavailable("REPORT_TEMPLATE_PACKAGE_ID_MISMATCH", "manifest.json")
                if hashlib.sha256(loaded["runtime_template.docx"]).hexdigest() != manifest.get("runtime_template_sha256"):
                    raise ReportTemplateUnavailable("REPORT_TEMPLATE_MANIFEST_MISMATCH", "runtime_template.docx")
                fields = validate_field_dictionary_bytes(loaded["field_dictionary.json"])
                rules = validate_rule_hints_bytes(loaded["rule_hints.json"])
                validate_narrative_templates_bytes(loaded["narrative_templates.json"])
                slots = [slot for field in fields.fields for slot in field.export_slots]
                self._validate_runtime_contract(loaded["runtime_template.docx"], manifest, slots)
                self._package = ReportTemplatePackage(
                    PACKAGE_ID,
                    str(manifest["template_edition"]),
                    str(manifest["template_revision"]),
                    "available",
                    manifest,
                    tuple(item.model_dump(mode="json") for item in fields.fields),
                    tuple(item.model_dump(mode="json") for item in fields.rule_contracts),
                    tuple(fields.projection_catalog),
                    tuple(item.model_dump(mode="json") for item in rules.rules),
                    loaded["runtime_template.docx"],
                )
                self._failure = None
                return self._package
            except ReportTemplateUnavailable as exc:
                self._package, self._failure = None, exc
                raise
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                failure = ReportTemplateUnavailable("REPORT_TEMPLATE_UNAVAILABLE")
                self._failure = failure
                raise failure from exc

    def status(self) -> dict[str, Any]:
        try:
            return self.load().safe_summary()
        except ReportTemplateUnavailable as exc:
            return {"package_id": PACKAGE_ID, "status": "unavailable", "error_code": exc.code}


report_template_registry = ReportTemplateRegistry()
