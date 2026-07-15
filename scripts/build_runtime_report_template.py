"""从批准源模板按 OPC 白名单重建脱敏运行时母版。"""

from __future__ import annotations

import argparse
import hashlib
import re
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

APPROVED_SOURCE_SHA256 = "b3957fd1da3bf19c31ac515fbdc6bf989fd7df033ca4d179c4b6e9567247fcf8"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
NS = {"w": W, "pr": PR, "ct": CT, "cp": CP, "dc": DC}

REMOVE_PREFIXES = ("customXml/", "word/glossary/", "word/embeddings/", "_xmlsignatures/", "word/activeX/")
REMOVE_PARTS = {
    "word/comments.xml", "word/commentsExtended.xml", "word/commentsExtensible.xml",
    "word/commentsIds.xml", "word/people.xml", "word/_rels/comments.xml.rels",
    "docProps/custom.xml",
}
FORBIDDEN_REL_SUFFIXES = (
    "/comments", "/commentsExtended", "/commentsExtensible", "/commentsIds", "/person", "/people",
    "/oleObject", "/customXml", "/glossaryDocument", "/attachedTemplate", "/package",
    "/custom-properties",
)


def _keep_part(name: str) -> bool:
    return name not in REMOVE_PARTS and not name.startswith(REMOVE_PREFIXES)


def _xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))


def _serialize(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _clean_relationships(data: bytes) -> bytes:
    root = _xml(data)
    for rel in list(root):
        rel_type = rel.get("Type", "")
        if rel.get("TargetMode") == "External" or rel_type.endswith(FORBIDDEN_REL_SUFFIXES):
            root.remove(rel)
    return _serialize(root)


def _clean_content_types(data: bytes, kept: set[str]) -> bytes:
    root = _xml(data)
    for node in list(root):
        part_name = node.get("PartName")
        if part_name and part_name.lstrip("/") not in kept:
            root.remove(node)
        if node.get("Extension") in {"bin"}:
            root.remove(node)
    return _serialize(root)


def _clear_sdt_content(sdt: etree._Element) -> None:
    if sdt.xpath("./w:sdtPr/w:docPartObj", namespaces=NS):
        return
    for text in sdt.xpath(".//w:t", namespaces=NS):
        text.text = ""
    for checked in sdt.xpath(".//w:checked", namespaces=NS):
        checked.set(f"{{{W}}}val", "0")


def _iter_row_cells(row: etree._Element) -> list[etree._Element]:
    return row.xpath("./w:tc | ./w:sdt/w:sdtContent/w:tc", namespaces=NS)


def _clean_document(data: bytes) -> bytes:
    root = _xml(data)
    for node in root.xpath("//w:commentRangeStart | //w:commentRangeEnd | //w:commentReference | //w:object | //w:altChunk", namespaces=NS):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    for sdt_index, sdt in enumerate(root.xpath("//w:sdt", namespaces=NS), start=1):
        properties = sdt.find(f"{{{W}}}sdtPr")
        if properties is not None:
            for name in ("tag", "alias", "dataBinding"):
                for old in properties.findall(f"{{{W}}}{name}"):
                    properties.remove(old)
            tag = etree.Element(f"{{{W}}}tag")
            tag.set(f"{{{W}}}val", f"template.control.{sdt_index:04d}")
            properties.insert(0, tag)
            alias = etree.Element(f"{{{W}}}alias")
            alias.set(f"{{{W}}}val", f"运行时控件 {sdt_index:04d}")
            properties.insert(1, alias)
        _clear_sdt_content(sdt)

    for text in root.xpath("//w:t", namespaces=NS):
        value = text.text or ""
        value = re.sub(r"\{[^{}]{0,500}\}", "", value)
        value = re.sub(r"(?<![A-Za-z])X{1,20}(?![A-Za-z])", "", value)
        value = re.sub(r"\b1[3-9]\d{9}\b", "", value)
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "", value)
        value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "", value)
        text.text = value.replace("选择一项。", "")

    # 基础模板 A-7 的第 4 列错误地让两个对象共享符合情况；运行时改为对象级输入。
    tables = root.xpath("/w:document/w:body/w:tbl", namespaces=NS)
    if len(tables) >= 46:
        for row in tables[45].xpath("./w:tr[position()>1]", namespaces=NS):
            cells = _iter_row_cells(row)
            if len(cells) >= 4:
                for merge in cells[3].xpath("./w:tcPr/w:vMerge", namespaces=NS):
                    merge.getparent().remove(merge)

    # 为 55 张表建立稳定、不可见的书签锚点，避免后续依赖表格序号定位。
    bookmark_id = 9000
    for table_index, table in enumerate(tables, start=1):
        paragraphs = table.xpath(".//w:p", namespaces=NS)
        if not paragraphs:
            continue
        start = etree.Element(f"{{{W}}}bookmarkStart")
        start.set(f"{{{W}}}id", str(bookmark_id))
        start.set(f"{{{W}}}name", f"rt_table_{table_index:03d}")
        end = etree.Element(f"{{{W}}}bookmarkEnd")
        end.set(f"{{{W}}}id", str(bookmark_id))
        paragraphs[0].insert(0, start)
        paragraphs[0].insert(1, end)
        bookmark_id += 1
    return _serialize(root)


def _clean_core(data: bytes) -> bytes:
    root = _xml(data)
    for xpath in ("//dc:creator", "//cp:lastModifiedBy", "//cp:keywords", "//dc:subject"):
        for node in root.xpath(xpath, namespaces=NS):
            node.text = ""
    return _serialize(root)


def build(source: Path, output: Path) -> None:
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != APPROVED_SOURCE_SHA256:
        raise ValueError("SOURCE_FINGERPRINT_NOT_APPROVED")
    with zipfile.ZipFile(source) as package:
        source_parts = {info.filename: package.read(info) for info in package.infolist() if not info.is_dir()}
    kept = {name for name in source_parts if _keep_part(name)}
    transformed: dict[str, bytes] = {}
    for name in sorted(kept):
        data = source_parts[name]
        if name == "[Content_Types].xml":
            data = _clean_content_types(data, kept)
        elif name.endswith(".rels"):
            data = _clean_relationships(data)
        elif name == "word/document.xml":
            data = _clean_document(data)
        elif name == "docProps/core.xml":
            data = _clean_core(data)
        transformed[name] = data

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".docx", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
            for name, data in transformed.items():
                info = zipfile.ZipInfo(name, date_time=(2025, 12, 8, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                target.writestr(info, data)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
