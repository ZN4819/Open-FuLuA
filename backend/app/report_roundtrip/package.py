"""Bounded OPC reader and fixed custom XML manifest package contract."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from lxml import etree

from .manifest import canonical_json_bytes, parse_manifest_json


MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_PART_BYTES = 32 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 500
MAX_XML_NODES = 500_000
MAX_XML_DEPTH = 256

CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DS = "http://schemas.openxmlformats.org/officeDocument/2006/customXml"
MANIFEST_NAMESPACE = "urn:open-fulua:roundtrip:manifest:v1"

DOCX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
MANIFEST_CONTENT_TYPE = "application/xml"
CUSTOM_XML_PROPS_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.customXmlProperties+xml"
)
CUSTOM_XML_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml"
)
CUSTOM_XML_PROPS_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXmlProps"
)

MANIFEST_PART = "customXml/flaRoundtripManifest.xml"
MANIFEST_RELS_PART = "customXml/_rels/flaRoundtripManifest.xml.rels"
MANIFEST_PROPS_PART = "customXml/flaRoundtripManifestProps.xml"
CUSTOM_XML_PARTS = frozenset({MANIFEST_PART, MANIFEST_RELS_PART, MANIFEST_PROPS_PART})
MANIFEST_DOCUMENT_REL_ID = "rIdFlaRoundtripManifest"
MANIFEST_PROPS_REL_ID = "rIdFlaRoundtripProps"
MANIFEST_DOCUMENT_TARGET = "../customXml/flaRoundtripManifest.xml"
MANIFEST_PROPS_TARGET = "flaRoundtripManifestProps.xml"

_REQUIRED_PARTS = frozenset(
    {
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
        "word/_rels/document.xml.rels",
    }
)
_DANGEROUS_REL_SUFFIXES = frozenset(
    {
        "attachedtemplate",
        "oleobject",
        "package",
        "afchunk",
        "externallink",
        "vbaproject",
        "control",
        "hyperlink",
    }
)
_DANGEROUS_CONTENT_TYPE_MARKERS = (
    "macroenabled",
    "vbaproject",
    "activex",
    "oleobject",
    "msword.template.macroenabled",
)
_DANGEROUS_ELEMENT_NAMES = frozenset({"altchunk", "object", "oleobject", "control"})


class OpcSecurityError(ValueError):
    def __init__(self, code: str, *, part: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.part = part


def _fail(code: str, part: str | None = None) -> None:
    raise OpcSecurityError(code, part=part)


@dataclass(frozen=True)
class OpcPackage:
    source_sha256: str
    source_size: int
    total_uncompressed: int
    parts: dict[str, bytes]


def _read_source(source: Path | bytes | bytearray) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
        if len(raw) > MAX_ARCHIVE_BYTES:
            _fail("ZIP_ARCHIVE_LIMIT_EXCEEDED")
        return raw
    with Path(source).open("rb") as stream:
        raw = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(raw) > MAX_ARCHIVE_BYTES:
        _fail("ZIP_ARCHIVE_LIMIT_EXCEEDED")
    return raw


def _validate_member(
    info: zipfile.ZipInfo,
    seen: set[str],
    folded: set[str],
) -> str:
    name = info.filename
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or ":" in name
        or "//" in name
        or unquote(name) != name
    ):
        _fail("ZIP_MEMBER_PATH_INVALID")
    if name.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", name):
        _fail("ZIP_MEMBER_PATH_INVALID")
    normalized = name.rstrip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _fail("ZIP_MEMBER_PATH_INVALID")
    if normalized in seen or normalized.casefold() in folded:
        _fail("ZIP_MEMBER_DUPLICATE")
    seen.add(normalized)
    folded.add(normalized.casefold())
    if info.flag_bits & 0x1:
        _fail("ZIP_MEMBER_ENCRYPTED")
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if unix_mode and stat.S_ISLNK(unix_mode):
        _fail("ZIP_MEMBER_LINK")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        _fail("ZIP_COMPRESSION_METHOD_UNSUPPORTED")
    if info.file_size > MAX_PART_BYTES:
        _fail("ZIP_MEMBER_TOO_LARGE")
    if info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
        _fail("ZIP_COMPRESSION_RATIO_EXCEEDED")
    return normalized


def _read_member(package: zipfile.ZipFile, info: zipfile.ZipInfo, name: str) -> bytes:
    limit = MAX_XML_BYTES if name.lower().endswith((".xml", ".rels")) else MAX_PART_BYTES
    chunks: list[bytes] = []
    size = 0
    try:
        with package.open(info, "r") as stream:
            while chunk := stream.read(64 * 1024):
                size += len(chunk)
                if size > limit:
                    _fail("ZIP_MEMBER_STREAM_LIMIT_EXCEEDED", name)
                chunks.append(chunk)
    except (RuntimeError, zipfile.BadZipFile) as exc:
        raise OpcSecurityError("ZIP_MEMBER_READ_FAILED", part=name) from exc
    return b"".join(chunks)


def parse_xml_part(data: bytes, part: str) -> etree._Element:
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        _fail("XML_DTD_OR_ENTITY_FORBIDDEN", part)
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=False)
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise OpcSecurityError("XML_INVALID", part=part) from exc
    count = 0
    stack: list[tuple[etree._Element, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_XML_NODES:
            _fail("XML_NODE_LIMIT_EXCEEDED", part)
        if depth > MAX_XML_DEPTH:
            _fail("XML_DEPTH_LIMIT_EXCEEDED", part)
        stack.extend((child, depth + 1) for child in node)
    return root


def read_safe_opc(source: Path | bytes | bytearray) -> OpcPackage:
    raw = _read_source(source)
    parts: dict[str, bytes] = {}
    seen: set[str] = set()
    folded: set[str] = set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            infos = package.infolist()
            if len(infos) > MAX_ENTRIES:
                _fail("ZIP_ENTRY_LIMIT_EXCEEDED")
            for info in infos:
                name = _validate_member(info, seen, folded)
                if info.is_dir():
                    continue
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    _fail("ZIP_TOTAL_LIMIT_EXCEEDED")
                parts[name] = _read_member(package, info, name)
    except zipfile.BadZipFile as exc:
        raise OpcSecurityError("ZIP_INVALID") from exc
    if not _REQUIRED_PARTS.issubset(parts):
        _fail("OPC_REQUIRED_PART_MISSING")
    for name, data in parts.items():
        if name.lower().endswith((".xml", ".rels")):
            parse_xml_part(data, name)
    return OpcPackage(
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_size=len(raw),
        total_uncompressed=total,
        parts=parts,
    )


def _content_type_maps(parts: Mapping[str, bytes]) -> tuple[dict[str, str], dict[str, str]]:
    root = parse_xml_part(parts["[Content_Types].xml"], "[Content_Types].xml")
    if root.tag != f"{{{CT}}}Types":
        _fail("OPC_CONTENT_TYPES_ROOT_INVALID", "[Content_Types].xml")
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}
    for child in root:
        if child.tag == f"{{{CT}}}Default":
            extension = str(child.get("Extension") or "").lower()
            content_type = str(child.get("ContentType") or "")
            if not extension or not content_type or extension in defaults:
                _fail("OPC_CONTENT_TYPE_DUPLICATE", "[Content_Types].xml")
            defaults[extension] = content_type
        elif child.tag == f"{{{CT}}}Override":
            part_name = str(child.get("PartName") or "")
            content_type = str(child.get("ContentType") or "")
            normalized = part_name.lstrip("/")
            if (
                not part_name.startswith("/")
                or part_name.startswith("//")
                or not normalized
                or "\\" in normalized
                or "//" in normalized
                or unquote(part_name) != part_name
                or any(item in {"", ".", ".."} for item in PurePosixPath(normalized).parts)
                or not content_type
                or normalized.casefold() in {key.casefold() for key in overrides}
            ):
                _fail("OPC_CONTENT_TYPE_OVERRIDE_INVALID", "[Content_Types].xml")
            overrides[normalized] = content_type
        else:
            _fail("OPC_CONTENT_TYPES_CHILD_INVALID", "[Content_Types].xml")
    return defaults, overrides


def _validate_content_types(parts: Mapping[str, bytes]) -> None:
    defaults, overrides = _content_type_maps(parts)
    if overrides.get("word/document.xml") != DOCX_MAIN_CONTENT_TYPE:
        _fail("DOCX_CONTENT_TYPE_INVALID", "[Content_Types].xml")
    for content_type in [*defaults.values(), *overrides.values()]:
        folded = content_type.casefold()
        if any(marker in folded for marker in _DANGEROUS_CONTENT_TYPE_MARKERS):
            _fail("OPC_DANGEROUS_CONTENT_TYPE", "[Content_Types].xml")
    for name in parts:
        if name == "[Content_Types].xml":
            continue
        # OPC relationship parts can be named exactly ``.rels``; pathlib treats
        # that as a dot-file with no suffix, while [Content_Types].xml correctly
        # addresses it through the ``rels`` Default extension.
        extension = "rels" if name.lower().endswith(".rels") else PurePosixPath(name).suffix.lstrip(".").lower()
        if name not in overrides and extension not in defaults:
            _fail("OPC_PART_CONTENT_TYPE_MISSING", name)


def _relationship_source_part(name: str) -> str:
    if name == "_rels/.rels":
        return ""
    match = re.fullmatch(r"(?:(.+)/)?_rels/([^/]+)\.rels", name)
    if not match:
        _fail("OPC_RELATIONSHIP_PART_PATH_INVALID", name)
    parent = match.group(1) or ""
    source_name = match.group(2)
    return f"{parent}/{source_name}" if parent else source_name


def _resolve_relationship_target(rel_part: str, target: str) -> str:
    if (
        not target
        or "\\" in target
        or "//" in target
        or unquote(target) != target
    ):
        _fail("OPC_RELATIONSHIP_TARGET_INVALID", rel_part)
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        _fail("OPC_RELATIONSHIP_TARGET_INVALID", rel_part)
    source_part = _relationship_source_part(rel_part)
    base = list(PurePosixPath(source_part).parent.parts) if source_part else []
    if target.startswith("/"):
        base = []
    for item in PurePosixPath(target.lstrip("/")).parts:
        if item in {"", "."}:
            continue
        if item == "..":
            if not base:
                _fail("OPC_RELATIONSHIP_TARGET_ESCAPE", rel_part)
            base.pop()
        else:
            base.append(item)
    if not base:
        _fail("OPC_RELATIONSHIP_TARGET_INVALID", rel_part)
    return PurePosixPath(*base).as_posix()


def _validate_relationships(parts: Mapping[str, bytes]) -> int:
    count = 0
    for name, data in parts.items():
        if not name.endswith(".rels"):
            continue
        root = parse_xml_part(data, name)
        if root.tag != f"{{{PR}}}Relationships":
            _fail("OPC_RELATIONSHIP_ROOT_INVALID", name)
        ids: set[str] = set()
        for relation in root:
            if relation.tag != f"{{{PR}}}Relationship":
                _fail("OPC_RELATIONSHIP_CHILD_INVALID", name)
            rel_id = str(relation.get("Id") or "")
            rel_type = str(relation.get("Type") or "")
            target = str(relation.get("Target") or "")
            if not rel_id or rel_id in ids or not rel_type:
                _fail("OPC_RELATIONSHIP_ID_INVALID", name)
            ids.add(rel_id)
            if relation.get("TargetMode") not in (None, ""):
                _fail("EXTERNAL_RELATIONSHIP_PRESENT", name)
            if rel_type.rsplit("/", 1)[-1].casefold() in _DANGEROUS_REL_SUFFIXES:
                _fail("OPC_DANGEROUS_RELATIONSHIP", name)
            if rel_type == CUSTOM_XML_REL_TYPE and name != "word/_rels/document.xml.rels":
                _fail("CUSTOM_XML_RELATIONSHIP_INVALID", name)
            if rel_type == CUSTOM_XML_PROPS_REL_TYPE and not (
                name.startswith("customXml/_rels/") and name.endswith(".xml.rels")
            ):
                _fail("CUSTOM_XML_PROPS_RELATIONSHIP_INVALID", name)
            resolved = _resolve_relationship_target(name, target)
            if resolved not in parts:
                _fail("OPC_RELATIONSHIP_TARGET_MISSING", name)
            count += 1
    return count


def _validate_dangerous_parts(parts: Mapping[str, bytes]) -> None:
    manifest_part, manifest_rels_part, props_part = _validate_manifest_graph(parts)
    allowed_custom_parts = {manifest_part, manifest_rels_part, props_part}
    for name in parts:
        folded = name.casefold()
        if folded.startswith("customxml/") and name not in allowed_custom_parts:
            _fail("CUSTOM_XML_EXTRA_PART", name)
        if (
            folded.startswith(("word/activex/", "word/embeddings/", "_xmlsignatures/", "word/glossary/"))
            or folded.endswith("vbaproject.bin")
            or (folded.startswith("word/") and folded.endswith(".bin"))
        ):
            _fail("OPC_DANGEROUS_PART", name)
    for name, data in parts.items():
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        root = parse_xml_part(data, name)
        for element in root.iter():
            if etree.QName(element).localname.casefold() in _DANGEROUS_ELEMENT_NAMES:
                _fail("OPC_DANGEROUS_XML_ELEMENT", name)


def _manifest_xml(manifest: Mapping[str, Any]) -> bytes:
    payload = base64.urlsafe_b64encode(canonical_json_bytes(dict(manifest))).decode("ascii").rstrip("=")
    root = etree.Element(f"{{{MANIFEST_NAMESPACE}}}roundtripManifest", nsmap={"fla": MANIFEST_NAMESPACE})
    root.set("version", "1")
    root.set("encoding", "base64url")
    root.text = payload
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")


def _props_xml(document_instance_id: str) -> bytes:
    item_id = "{" + document_instance_id.upper() + "}"
    root = etree.Element(f"{{{DS}}}datastoreItem", nsmap={"ds": DS})
    root.set(f"{{{DS}}}itemID", item_id)
    refs = etree.SubElement(root, f"{{{DS}}}schemaRefs")
    ref = etree.SubElement(refs, f"{{{DS}}}schemaRef")
    ref.set(f"{{{DS}}}uri", MANIFEST_NAMESPACE)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="no")


def _manifest_rels_xml() -> bytes:
    root = etree.Element(f"{{{PR}}}Relationships", nsmap={None: PR})
    relation = etree.SubElement(root, f"{{{PR}}}Relationship")
    relation.set("Id", MANIFEST_PROPS_REL_ID)
    relation.set("Type", CUSTOM_XML_PROPS_REL_TYPE)
    relation.set("Target", MANIFEST_PROPS_TARGET)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")


def embed_manifest(
    parts: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> dict[str, bytes]:
    validated = parse_manifest_json(canonical_json_bytes(dict(manifest)))
    output = dict(parts)
    if any(name.startswith("customXml/") for name in output):
        _fail("CUSTOM_XML_ALREADY_PRESENT")
    for required in _REQUIRED_PARTS:
        if required not in output:
            _fail("OPC_REQUIRED_PART_MISSING", required)
    _validate_content_types(output)

    relationships = parse_xml_part(
        output["word/_rels/document.xml.rels"],
        "word/_rels/document.xml.rels",
    )
    if relationships.tag != f"{{{PR}}}Relationships" or any(
        child.tag != f"{{{PR}}}Relationship" for child in relationships
    ):
        _fail("OPC_RELATIONSHIP_ROOT_INVALID", "word/_rels/document.xml.rels")
    existing_ids = {
        str(item.get("Id") or "")
        for item in relationships.findall(f"{{{PR}}}Relationship")
    }
    if MANIFEST_DOCUMENT_REL_ID in existing_ids or any(
        str(item.get("Type") or "") == CUSTOM_XML_REL_TYPE
        for item in relationships.findall(f"{{{PR}}}Relationship")
    ):
        _fail("CUSTOM_XML_RELATIONSHIP_ALREADY_PRESENT", "word/_rels/document.xml.rels")
    relation = etree.SubElement(relationships, f"{{{PR}}}Relationship")
    relation.set("Id", MANIFEST_DOCUMENT_REL_ID)
    relation.set("Type", CUSTOM_XML_REL_TYPE)
    relation.set("Target", MANIFEST_DOCUMENT_TARGET)
    output["word/_rels/document.xml.rels"] = etree.tostring(
        relationships,
        xml_declaration=True,
        encoding="UTF-8",
        standalone="yes",
    )

    content_types = parse_xml_part(output["[Content_Types].xml"], "[Content_Types].xml")
    if content_types.tag != f"{{{CT}}}Types":
        _fail("OPC_CONTENT_TYPES_ROOT_INVALID", "[Content_Types].xml")
    existing_overrides = {
        str(item.get("PartName") or "")
        for item in content_types.findall(f"{{{CT}}}Override")
    }
    for part_name, content_type in (
        (MANIFEST_PART, MANIFEST_CONTENT_TYPE),
        (MANIFEST_PROPS_PART, CUSTOM_XML_PROPS_CONTENT_TYPE),
    ):
        absolute = f"/{part_name}"
        if absolute in existing_overrides:
            _fail("CUSTOM_XML_CONTENT_TYPE_ALREADY_PRESENT", "[Content_Types].xml")
        override = etree.SubElement(content_types, f"{{{CT}}}Override")
        override.set("PartName", absolute)
        override.set("ContentType", content_type)
    output["[Content_Types].xml"] = etree.tostring(
        content_types,
        xml_declaration=True,
        encoding="UTF-8",
        standalone="yes",
    )
    output[MANIFEST_PART] = _manifest_xml(validated)
    output[MANIFEST_RELS_PART] = _manifest_rels_xml()
    output[MANIFEST_PROPS_PART] = _props_xml(validated["document_instance_id"])
    return output


def _validate_manifest_graph(parts: Mapping[str, bytes]) -> tuple[str, str, str]:
    """Resolve the one signed manifest graph in tool or Word-normalized form.

    Word rewrites the fixed tool-owned names and relationship IDs to
    ``itemN.xml``/``itemPropsN.xml`` on SaveAs.  Only those two deterministic
    shapes are accepted; unrelated or additional custom XML remains forbidden.
    """

    document_rels = parse_xml_part(
        parts["word/_rels/document.xml.rels"],
        "word/_rels/document.xml.rels",
    )
    if document_rels.tag != f"{{{PR}}}Relationships" or any(
        child.tag != f"{{{PR}}}Relationship" for child in document_rels
    ):
        _fail("OPC_RELATIONSHIP_ROOT_INVALID", "word/_rels/document.xml.rels")
    custom_relationships = [
        item
        for item in document_rels.findall(f"{{{PR}}}Relationship")
        if str(item.get("Type") or "") == CUSTOM_XML_REL_TYPE
    ]
    if len(custom_relationships) != 1:
        _fail("CUSTOM_XML_RELATIONSHIP_COUNT_INVALID", "word/_rels/document.xml.rels")
    relation = custom_relationships[0]
    if (
        set(relation.attrib) != {"Id", "Type", "Target"}
        or relation.get("Type") != CUSTOM_XML_REL_TYPE
        or not relation.get("Id")
        or relation.get("TargetMode") not in (None, "")
    ):
        _fail("CUSTOM_XML_RELATIONSHIP_INVALID", "word/_rels/document.xml.rels")
    manifest_part = _resolve_relationship_target(
        "word/_rels/document.xml.rels", str(relation.get("Target") or "")
    )
    word_match = re.fullmatch(r"customXml/item([1-9][0-9]*)\.xml", manifest_part)
    if manifest_part == MANIFEST_PART:
        if (
            relation.get("Id") != MANIFEST_DOCUMENT_REL_ID
            or relation.get("Target") != MANIFEST_DOCUMENT_TARGET
        ):
            _fail("CUSTOM_XML_RELATIONSHIP_INVALID", "word/_rels/document.xml.rels")
        manifest_rels_part = MANIFEST_RELS_PART
        props_part = MANIFEST_PROPS_PART
        expected_props_id = MANIFEST_PROPS_REL_ID
        expected_props_target = MANIFEST_PROPS_TARGET
        expected_custom_overrides = {MANIFEST_PART, MANIFEST_PROPS_PART}
    elif word_match is not None:
        index = word_match.group(1)
        if (
            not re.fullmatch(r"rId[1-9][0-9]*", str(relation.get("Id")))
            or relation.get("Target") != f"../customXml/item{index}.xml"
        ):
            _fail("CUSTOM_XML_RELATIONSHIP_INVALID", "word/_rels/document.xml.rels")
        manifest_rels_part = f"customXml/_rels/item{index}.xml.rels"
        props_part = f"customXml/itemProps{index}.xml"
        expected_props_id = "rId1"
        expected_props_target = f"itemProps{index}.xml"
        expected_custom_overrides = {props_part}
    else:
        _fail("CUSTOM_XML_RELATIONSHIP_INVALID", "word/_rels/document.xml.rels")

    actual_custom = {name for name in parts if name.startswith("customXml/")}
    expected_custom = {manifest_part, manifest_rels_part, props_part}
    extra_custom = sorted(actual_custom - expected_custom)
    if extra_custom:
        _fail("CUSTOM_XML_EXTRA_PART", extra_custom[0])
    if actual_custom != expected_custom:
        _fail("CUSTOM_XML_PART_SET_INVALID")

    defaults, overrides = _content_type_maps(parts)
    custom_overrides = {
        name for name in overrides if name.casefold().startswith("customxml/")
    }
    if custom_overrides != expected_custom_overrides:
        _fail("CUSTOM_XML_CONTENT_TYPE_SET_INVALID", "[Content_Types].xml")
    if (overrides.get(manifest_part) or defaults.get("xml")) != MANIFEST_CONTENT_TYPE:
        _fail("CUSTOM_XML_MANIFEST_CONTENT_TYPE_INVALID", "[Content_Types].xml")
    if overrides.get(props_part) != CUSTOM_XML_PROPS_CONTENT_TYPE:
        _fail("CUSTOM_XML_PROPS_CONTENT_TYPE_INVALID", "[Content_Types].xml")

    item_rels = parse_xml_part(parts[manifest_rels_part], manifest_rels_part)
    items = list(item_rels)
    if (
        item_rels.tag != f"{{{PR}}}Relationships"
        or dict(item_rels.attrib)
        or len(items) != 1
        or items[0].tag != f"{{{PR}}}Relationship"
    ):
        _fail("CUSTOM_XML_PROPS_RELATIONSHIP_COUNT_INVALID", manifest_rels_part)
    props_relation = items[0]
    if (
        set(props_relation.attrib) != {"Id", "Type", "Target"}
        or props_relation.get("Id") != expected_props_id
        or props_relation.get("Type") != CUSTOM_XML_PROPS_REL_TYPE
        or props_relation.get("Target") != expected_props_target
        or props_relation.get("TargetMode") not in (None, "")
    ):
        _fail("CUSTOM_XML_PROPS_RELATIONSHIP_INVALID", manifest_rels_part)
    if _resolve_relationship_target(manifest_rels_part, expected_props_target) != props_part:
        _fail("CUSTOM_XML_PROPS_RELATIONSHIP_INVALID", manifest_rels_part)
    return manifest_part, manifest_rels_part, props_part


def extract_manifest(parts: Mapping[str, bytes]) -> dict[str, Any]:
    manifest_part, _manifest_rels_part, props_part = _validate_manifest_graph(parts)
    root = parse_xml_part(parts[manifest_part], manifest_part)
    if (
        root.tag != f"{{{MANIFEST_NAMESPACE}}}roundtripManifest"
        or dict(root.attrib) != {"version": "1", "encoding": "base64url"}
        or len(root) != 0
        or not (root.text or "")
    ):
        _fail("CUSTOM_XML_MANIFEST_SCHEMA_INVALID", manifest_part)
    encoded = str(root.text)
    try:
        raw = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise OpcSecurityError("CUSTOM_XML_MANIFEST_ENCODING_INVALID", part=manifest_part) from exc
    manifest = parse_manifest_json(raw)

    props = parse_xml_part(parts[props_part], props_part)
    expected_item_id = "{" + manifest["document_instance_id"].upper() + "}"
    refs = props.xpath("/ds:datastoreItem/ds:schemaRefs/ds:schemaRef", namespaces={"ds": DS})
    schema_refs = props.xpath("/ds:datastoreItem/ds:schemaRefs", namespaces={"ds": DS})
    if (
        props.tag != f"{{{DS}}}datastoreItem"
        or dict(props.attrib) != {f"{{{DS}}}itemID": expected_item_id}
        or len(props) != 1
        or len(schema_refs) != 1
        or dict(schema_refs[0].attrib)
        or len(schema_refs[0]) != 1
        or len(refs) != 1
        or dict(refs[0].attrib) != {f"{{{DS}}}uri": MANIFEST_NAMESPACE}
        or len(refs[0]) != 0
        or any(
            text and text.strip()
            for text in (
                props.text,
                schema_refs[0].text if schema_refs else None,
                schema_refs[0].tail if schema_refs else None,
                refs[0].text if refs else None,
                refs[0].tail if refs else None,
            )
        )
    ):
        _fail("CUSTOM_XML_PROPS_SCHEMA_INVALID", props_part)
    return manifest


def validate_roundtrip_opc(package: OpcPackage) -> dict[str, int]:
    parts = package.parts
    _validate_content_types(parts)
    _validate_dangerous_parts(parts)
    relationship_count = _validate_relationships(parts)
    extract_manifest(parts)

    # Local import avoids a module cycle while keeping one public validation entrypoint.
    from .structure import (
        StructureSecurityError,
        find_unresolved_revisions,
        validate_field_instructions,
    )

    revisions = find_unresolved_revisions(parts)
    if revisions:
        _fail("WORD_TRACKED_CHANGES_NOT_ACCEPTED", revisions[0].part)
    try:
        validate_field_instructions(parts)
    except StructureSecurityError as exc:
        raise OpcSecurityError(exc.code, part=exc.part) from exc
    return {
        "parts": len(parts),
        "relationships": relationship_count,
        "word_xml_parts": sum(name.startswith("word/") and name.endswith(".xml") for name in parts),
    }
