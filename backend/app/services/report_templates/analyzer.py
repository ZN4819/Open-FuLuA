"""不解压落盘、不执行外部内容的 DOCX/OPC 静态取证器。"""

from __future__ import annotations

import hashlib
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from lxml import etree

from .models import (
    AnalysisIssue,
    ContentFlags,
    DocumentSummary,
    PackageSummary,
    ReportTemplateForensics,
    StructuralSignature,
)

MAX_ENTRIES = 4096
MAX_PART_BYTES = 32 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 500
MAX_XML_NODES = 500_000
MAX_XML_DEPTH = 256

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W, "r": R, "pr": PR}


class UnsafePackageError(ValueError):
    """输入不是可安全静态分析的 OPC 包。"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_member(info: zipfile.ZipInfo, seen: set[str], folded: set[str]) -> str:
    name = info.filename
    if not name or "\x00" in name or "\\" in name:
        raise UnsafePackageError("ZIP_MEMBER_PATH_INVALID")
    if name.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", name):
        raise UnsafePackageError("ZIP_MEMBER_PATH_INVALID")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UnsafePackageError("ZIP_MEMBER_PATH_INVALID")
    normalized = path.as_posix().rstrip("/")
    if normalized in seen or normalized.casefold() in folded:
        raise UnsafePackageError("ZIP_MEMBER_DUPLICATE")
    seen.add(normalized)
    folded.add(normalized.casefold())
    if info.flag_bits & 0x1:
        raise UnsafePackageError("ZIP_MEMBER_ENCRYPTED")
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if unix_mode and stat.S_ISLNK(unix_mode):
        raise UnsafePackageError("ZIP_MEMBER_LINK")
    if info.file_size > MAX_PART_BYTES:
        raise UnsafePackageError("ZIP_MEMBER_TOO_LARGE")
    ratio = info.file_size / max(info.compress_size, 1)
    if ratio > MAX_COMPRESSION_RATIO:
        raise UnsafePackageError("ZIP_COMPRESSION_RATIO_EXCEEDED")
    return normalized


def _read_member(package: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    limit = MAX_XML_BYTES if info.filename.lower().endswith((".xml", ".rels")) else MAX_PART_BYTES
    chunks: list[bytes] = []
    size = 0
    with package.open(info, "r") as stream:
        while chunk := stream.read(64 * 1024):
            size += len(chunk)
            if size > limit:
                raise UnsafePackageError("ZIP_MEMBER_STREAM_LIMIT_EXCEEDED")
            chunks.append(chunk)
    return b"".join(chunks)


def _parse_xml(data: bytes, part: str) -> etree._Element:
    upper = data[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise UnsafePackageError("XML_DTD_OR_ENTITY_FORBIDDEN")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=False)
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise UnsafePackageError(f"XML_INVALID:{part}") from exc
    count = 0
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_XML_NODES:
            raise UnsafePackageError("XML_NODE_LIMIT_EXCEEDED")
        if depth > MAX_XML_DEPTH:
            raise UnsafePackageError("XML_DEPTH_LIMIT_EXCEEDED")
        stack.extend((child, depth + 1) for child in node)
    return root


def _signature(element: etree._Element) -> str:
    clone = etree.fromstring(etree.tostring(element))
    for node in clone.iter():
        node.text = None
        node.tail = None
    return _sha256(etree.tostring(clone, method="c14n", exclusive=True))


def _relationship_summary(parts: dict[str, bytes]) -> tuple[int, int, set[str], bool]:
    total = external = 0
    types: set[str] = set()
    attached = False
    for name, data in parts.items():
        if not name.endswith(".rels"):
            continue
        root = _parse_xml(data, name)
        ids: set[str] = set()
        for rel in root.findall(f"{{{PR}}}Relationship"):
            rel_id = rel.get("Id", "")
            if not rel_id or rel_id in ids:
                raise UnsafePackageError("OPC_RELATIONSHIP_ID_INVALID")
            ids.add(rel_id)
            total += 1
            rel_type = rel.get("Type", "")
            types.add(rel_type)
            if rel.get("TargetMode") == "External":
                external += 1
            if rel_type.endswith("/attachedTemplate"):
                attached = True
    return total, external, types, attached


def analyze_report_template(path: Path, *, source_role: str) -> ReportTemplateForensics:
    source = Path(path)
    raw = source.read_bytes()
    parts: dict[str, bytes] = {}
    seen: set[str] = set()
    folded: set[str] = set()
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(source) as package:
            infos = package.infolist()
            if len(infos) > MAX_ENTRIES:
                raise UnsafePackageError("ZIP_ENTRY_LIMIT_EXCEEDED")
            for info in infos:
                normalized = _validate_member(info, seen, folded)
                if info.is_dir():
                    continue
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_TOTAL_BYTES:
                    raise UnsafePackageError("ZIP_TOTAL_LIMIT_EXCEEDED")
                parts[normalized] = _read_member(package, info)
    except zipfile.BadZipFile as exc:
        raise UnsafePackageError("ZIP_INVALID") from exc

    required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
    if not required.issubset(parts):
        raise UnsafePackageError("OPC_REQUIRED_PART_MISSING")

    document = _parse_xml(parts["word/document.xml"], "word/document.xml")
    relationships, external, _rel_types, attached = _relationship_summary(parts)
    tables = document.xpath("/w:document/w:body/w:tbl", namespaces=NS)
    sections = document.xpath("//w:sectPr", namespaces=NS)
    controls = document.xpath("//w:sdt", namespaces=NS)
    dropdowns = document.xpath("//w:sdtPr/w:dropDownList", namespaces=NS)
    revisions = document.xpath("//w:ins | //w:del | //w:moveFrom | //w:moveTo", namespaces=NS)
    comments = _parse_xml(parts["word/comments.xml"], "word/comments.xml") if "word/comments.xml" in parts else None

    names = set(parts)
    flags = ContentFlags(
        has_macros=any(name.endswith("vbaProject.bin") for name in names),
        has_activex=any(name.startswith("word/activeX/") for name in names),
        has_ole_or_embeddings=any(name.startswith("word/embeddings/") for name in names),
        has_custom_xml=any(name.startswith("customXml/") for name in names),
        has_digital_signatures=any(name.startswith("_xmlsignatures/") for name in names),
        has_external_relationships=external > 0,
        has_attached_template=attached,
        has_alt_chunk=bool(document.xpath("//w:altChunk", namespaces=NS)),
    )
    issues: list[AnalysisIssue] = []
    for code, present in (
        ("MACRO_PRESENT", flags.has_macros),
        ("ACTIVEX_PRESENT", flags.has_activex),
        ("OLE_OR_EMBEDDING_PRESENT", flags.has_ole_or_embeddings),
        ("CUSTOM_XML_PRESENT", flags.has_custom_xml),
        ("DIGITAL_SIGNATURE_PRESENT", flags.has_digital_signatures),
        ("EXTERNAL_RELATIONSHIP_PRESENT", flags.has_external_relationships),
        ("ATTACHED_TEMPLATE_PRESENT", flags.has_attached_template),
        ("ALT_CHUNK_PRESENT", flags.has_alt_chunk),
        ("UNRESOLVED_REVISION_PRESENT", bool(revisions)),
    ):
        if present:
            issues.append(AnalysisIssue(code=code, severity="warning"))

    return ReportTemplateForensics(
        source_role=source_role,
        source_sha256=_sha256(raw),
        source_size_bytes=len(raw),
        package=PackageSummary(
            part_count=len(parts),
            uncompressed_bytes=total_uncompressed,
            relationship_count=relationships,
            external_relationship_count=external,
            media_count=sum(name.startswith("word/media/") for name in names),
        ),
        document=DocumentSummary(
            body_paragraph_count=len(document.xpath("/w:document/w:body/w:p", namespaces=NS)),
            table_count=len(tables),
            section_count=len(sections),
            header_part_count=sum(bool(re.fullmatch(r"word/header\d+\.xml", name)) for name in names),
            footer_part_count=sum(bool(re.fullmatch(r"word/footer\d+\.xml", name)) for name in names),
            content_control_count=len(controls),
            dropdown_control_count=len(dropdowns),
            bookmark_count=len(document.xpath("//w:bookmarkStart", namespaces=NS)),
            field_instruction_count=len(document.xpath("//w:instrText", namespaces=NS)),
            comment_count=len(comments.findall(f"{{{W}}}comment")) if comments is not None else 0,
            revision_count=len(revisions),
        ),
        flags=flags,
        section_signatures=[StructuralSignature(index=i, signature=_signature(node)) for i, node in enumerate(sections)],
        table_signatures=[StructuralSignature(index=i, signature=_signature(node)) for i, node in enumerate(tables)],
        issues=issues,
    )
