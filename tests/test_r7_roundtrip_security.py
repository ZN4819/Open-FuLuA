from __future__ import annotations

import base64
import asyncio
import concurrent.futures
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from lxml import etree
from starlette.requests import Request
from starlette.responses import Response


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.report_roundtrip.keys import RoundtripSigningKey  # noqa: E402
from app.report_roundtrip import key_store  # noqa: E402
from app.report_roundtrip.manifest import (  # noqa: E402
    HMAC_DOMAIN,
    ManifestSecurityError,
    build_signed_manifest,
    canonical_json_bytes,
    compute_writable_contract_hash,
    parse_manifest_json,
    verify_signed_manifest,
)
from app.report_roundtrip.package import (  # noqa: E402
    CUSTOM_XML_PARTS,
    MANIFEST_DOCUMENT_REL_ID,
    OpcSecurityError,
    embed_manifest,
    extract_manifest,
    read_safe_opc,
    validate_roundtrip_opc,
)
from app.report_roundtrip.structure import (  # noqa: E402
    StructureSecurityError,
    extract_roundtrip_structure,
    find_unresolved_revisions,
    readonly_document_hash,
    validate_field_instructions,
)
from app.report_export.word import refresh_with_word  # noqa: E402
from app.main import reject_business_writes_during_maintenance  # noqa: E402


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def _hash(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _signing_key() -> RoundtripSigningKey:
    return RoundtripSigningKey.from_bytes(bytes(range(32)))


def _signed_manifest() -> tuple[dict[str, object], RoundtripSigningKey]:
    fields = [
        {
            "slot_id": "slotToken001",
            "authority_field_id": "report.appendix_a.finding",
            "entity_path": "appendix_a.rows.00000000-0000-4000-8000-000000000005.finding",
            "value_type": "multiline",
            "normalizer_id": "multiline_v1",
            "projection_group": "appendixA001",
        }
    ]
    baselines = {"slotToken001": _hash("baseline")}
    rows = [
        {
            "row_id": "00000000-0000-4000-8000-000000000005",
            "row_token": "rowToken0001",
            "block_token": "blockToken01",
            "table_id": "report_table_001",
            "sort_order": 1,
            "writable_slot_ids": ["slotToken001"],
            "immutable_value_hash": _hash("immutable"),
            "geometry_hash": _hash("geometry"),
        }
    ]
    core: dict[str, object] = {
        "manifest_version": "1",
        "document_instance_id": "00000000-0000-4000-8000-000000000001",
        "project_uuid": "00000000-0000-4000-8000-000000000002",
        "project_type": "full_report",
        "export_job_uuid": "00000000-0000-4000-8000-000000000003",
        "snapshot_uuid": "00000000-0000-4000-8000-000000000004",
        "project_revision": 7,
        "template_package_id": "r0-report-template",
        "template_edition": "2023",
        "template_revision": "R0.6",
        "template_hash": _hash("template"),
        "field_dictionary_hash": _hash("dictionary"),
        "snapshot_hash": _hash("snapshot"),
        "writable_contract_hash": compute_writable_contract_hash(fields, rows, baselines),
        "structure_contract_hash": _hash("structure"),
        "scoring_engine_version": "R4.1",
        "issued_at": "2026-07-16T10:30:00+08:00",
        "roundtrip_capable": True,
        "export_mode": "draft",
        "writable_fields": fields,
        "writable_rows": rows,
        "baseline_value_hashes": baselines,
    }
    key = _signing_key()
    return build_signed_manifest(core, key), key


def _document(body: str = "<w:p><w:r><w:t>安全报告</w:t></w:r></w:p>") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}<w:sectPr/></w:body></w:document>'
    ).encode("utf-8")


def _base_parts(
    *,
    document: bytes | None = None,
    document_relationships: str = "",
) -> dict[str, bytes]:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{CT}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    ).encode("utf-8")
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{PR}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    ).encode("utf-8")
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{PR}">{document_relationships}</Relationships>'
    ).encode("utf-8")
    return {
        "[Content_Types].xml": content_types,
        "_rels/.rels": root_rels,
        "word/document.xml": document or _document(),
        "word/_rels/document.xml.rels": doc_rels,
    }


def _zip_entries(entries: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return output.getvalue()


def _zip_parts(parts: dict[str, bytes]) -> bytes:
    return _zip_entries(list(parts.items()))


class CanonicalManifestSecurityTests(unittest.TestCase):
    def test_canonical_json_rejects_float_nan_and_unknown_python_types(self) -> None:
        for value in ({"value": 0.5}, {"value": float("nan")}, {"value": (1, 2)}):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ManifestSecurityError) as raised:
                    canonical_json_bytes(value)
                self.assertEqual(raised.exception.code, "MANIFEST_JSON_TYPE_INVALID")

    def test_parser_rejects_float_nan_duplicate_keys_and_noncanonical_json(self) -> None:
        cases = (
            (b'{"value":0.5}', "MANIFEST_JSON_NUMBER_INVALID"),
            (b'{"value":NaN}', "MANIFEST_JSON_NUMBER_INVALID"),
            (b'{"manifest_version":"1","manifest_version":"1"}', "MANIFEST_DUPLICATE_KEY"),
        )
        for raw, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(ManifestSecurityError) as raised:
                    parse_manifest_json(raw)
                self.assertEqual(raised.exception.code, code)

        manifest, _ = _signed_manifest()
        noncanonical = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        with self.assertRaises(ManifestSecurityError) as raised:
            parse_manifest_json(noncanonical)
        self.assertEqual(raised.exception.code, "MANIFEST_JSON_NOT_CANONICAL")

        with self.assertRaises(ManifestSecurityError) as raised:
            canonical_json_bytes({"value": "\ud800"})
        self.assertEqual(raised.exception.code, "MANIFEST_STRING_INVALID")

    def test_parser_rejects_unknown_schema_and_unsupported_version(self) -> None:
        manifest, _ = _signed_manifest()
        unknown = {**manifest, "future_option": True}
        with self.assertRaises(ManifestSecurityError) as raised:
            parse_manifest_json(canonical_json_bytes(unknown))
        self.assertEqual(raised.exception.code, "MANIFEST_SCHEMA_FIELDS_INVALID")

        unsupported = {**manifest, "manifest_version": "2"}
        with self.assertRaises(ManifestSecurityError) as raised:
            parse_manifest_json(canonical_json_bytes(unsupported))
        self.assertEqual(raised.exception.code, "MANIFEST_SCHEMA_UNSUPPORTED")

    def test_hmac_is_domain_separated_keyed_and_constant_time_compared(self) -> None:
        manifest, key = _signed_manifest()
        signed_payload = {key_name: value for key_name, value in manifest.items() if key_name != "signature"}
        expected = hmac.new(
            key.material,
            HMAC_DOMAIN + canonical_json_bytes(signed_payload),
            hashlib.sha256,
        ).digest()
        actual = base64.urlsafe_b64decode(str(manifest["signature"]) + "=")
        self.assertEqual(actual, expected)
        self.assertNotEqual(
            actual,
            hmac.new(key.material, canonical_json_bytes(signed_payload), hashlib.sha256).digest(),
        )
        self.assertNotIn(key.material.hex(), repr(key))

        with patch("app.report_roundtrip.manifest.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            verified = verify_signed_manifest(manifest, {key.key_id: key})
        self.assertEqual(verified, manifest)
        self.assertGreaterEqual(compare.call_count, 3)


class SigningKeyPersistenceTests(unittest.TestCase):
    def test_first_use_concurrency_returns_only_the_persisted_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "private" / "roundtrip" / "signing-key.v1"
            barrier = __import__("threading").Barrier(2)
            real_generate = key_store.generate_signing_key

            def concurrent_generate() -> RoundtripSigningKey:
                generated = real_generate()
                barrier.wait(timeout=5)
                return generated

            with (
                patch.object(key_store, "signing_key_path", return_value=key_path),
                patch.object(key_store, "_protect", side_effect=lambda value: value),
                patch.object(key_store, "_unprotect", side_effect=lambda value: value),
                patch.object(
                    key_store,
                    "generate_signing_key",
                    side_effect=concurrent_generate,
                ),
                concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor,
            ):
                keys = list(executor.map(lambda _index: key_store.load_or_create_signing_key(), range(2)))

            persisted = RoundtripSigningKey.from_bytes(key_path.read_bytes())
            self.assertEqual({item.key_id for item in keys}, {persisted.key_id})
            self.assertFalse(list(key_path.parent.glob("*.tmp")))

    def test_hmac_rejects_signature_tamper_and_unknown_key_id(self) -> None:
        manifest, key = _signed_manifest()
        tampered = deepcopy(manifest)
        signature = str(tampered["signature"])
        tampered["signature"] = ("A" if signature[0] != "A" else "B") + signature[1:]
        with self.assertRaises(ManifestSecurityError) as raised:
            verify_signed_manifest(tampered, {key.key_id: key})
        self.assertEqual(raised.exception.code, "MANIFEST_SIGNATURE_INVALID")

        with self.assertRaises(ManifestSecurityError) as raised:
            verify_signed_manifest(manifest, {})
        self.assertEqual(raised.exception.code, "MANIFEST_SIGNING_KEY_UNAVAILABLE")


class OpcPackageSecurityTests(unittest.TestCase):
    def test_fixed_custom_xml_graph_roundtrips_and_validates(self) -> None:
        manifest, _ = _signed_manifest()
        embedded = embed_manifest(_base_parts(), manifest)
        self.assertEqual({name for name in embedded if name.startswith("customXml/")}, set(CUSTOM_XML_PARTS))
        self.assertIn(MANIFEST_DOCUMENT_REL_ID.encode("ascii"), embedded["word/_rels/document.xml.rels"])
        self.assertEqual(extract_manifest(embedded), manifest)

        package = read_safe_opc(_zip_parts(embedded))
        summary = validate_roundtrip_opc(package)
        self.assertEqual(summary["parts"], len(embedded))
        self.assertEqual(extract_manifest(package.parts), manifest)

    def test_word_normalized_custom_xml_graph_roundtrips_and_validates(self) -> None:
        manifest, _ = _signed_manifest()
        parts = embed_manifest(_base_parts(), manifest)
        parts["customXml/item1.xml"] = parts.pop("customXml/flaRoundtripManifest.xml")
        parts["customXml/itemProps1.xml"] = parts.pop(
            "customXml/flaRoundtripManifestProps.xml"
        )
        parts["customXml/_rels/item1.xml.rels"] = parts.pop(
            "customXml/_rels/flaRoundtripManifest.xml.rels"
        )

        relationships = etree.fromstring(parts["word/_rels/document.xml.rels"])
        custom = next(
            item for item in relationships if str(item.get("Type") or "").endswith("/customXml")
        )
        custom.set("Id", "rId1")
        custom.set("Target", "../customXml/item1.xml")
        parts["word/_rels/document.xml.rels"] = etree.tostring(relationships)

        item_relationships = etree.fromstring(parts["customXml/_rels/item1.xml.rels"])
        props = item_relationships[0]
        props.set("Id", "rId1")
        props.set("Target", "itemProps1.xml")
        parts["customXml/_rels/item1.xml.rels"] = etree.tostring(item_relationships)

        content_types = etree.fromstring(parts["[Content_Types].xml"])
        for override in list(content_types):
            part_name = str(override.get("PartName") or "")
            if part_name == "/customXml/flaRoundtripManifest.xml":
                content_types.remove(override)
            elif part_name == "/customXml/flaRoundtripManifestProps.xml":
                override.set("PartName", "/customXml/itemProps1.xml")
        parts["[Content_Types].xml"] = etree.tostring(content_types)

        package = read_safe_opc(_zip_parts(parts))
        self.assertEqual(extract_manifest(package.parts), manifest)
        self.assertEqual(validate_roundtrip_opc(package)["parts"], len(parts))

    def test_zip_rejects_traversal_casefold_duplicate_and_compression_bomb(self) -> None:
        base = list(_base_parts().items())
        cases = (
            (base + [("../escape.xml", b"<x/>")], "ZIP_MEMBER_PATH_INVALID"),
            (base + [("WORD/DOCUMENT.XML", b"<x/>")], "ZIP_MEMBER_DUPLICATE"),
            (base + [("word/media/bomb.dat", b"0" * (2 * 1024 * 1024))], "ZIP_COMPRESSION_RATIO_EXCEEDED"),
        )
        for entries, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(OpcSecurityError) as raised:
                    read_safe_opc(_zip_entries(entries))
                self.assertEqual(raised.exception.code, code)

    def test_dangerous_relationship_and_extra_custom_xml_are_rejected(self) -> None:
        manifest, _ = _signed_manifest()
        hyperlink = (
            '<Relationship Id="rIdLink" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            'Target="document.xml"/>'
        )
        embedded = embed_manifest(_base_parts(document_relationships=hyperlink), manifest)
        with self.assertRaises(OpcSecurityError) as raised:
            validate_roundtrip_opc(read_safe_opc(_zip_parts(embedded)))
        self.assertEqual(raised.exception.code, "OPC_DANGEROUS_RELATIONSHIP")

        extra = embed_manifest(_base_parts(), manifest)
        extra["customXml/evil.xml"] = b"<root/>"
        with self.assertRaises(OpcSecurityError) as raised:
            validate_roundtrip_opc(read_safe_opc(_zip_parts(extra)))
        self.assertEqual(raised.exception.code, "CUSTOM_XML_EXTRA_PART")

        dangerous_part = _base_parts()
        dangerous_part["[Content_Types].xml"] = dangerous_part["[Content_Types].xml"].replace(
            b"</Types>",
            b'<Default Extension="bin" ContentType="application/octet-stream"/></Types>',
        )
        dangerous_part["word/embeddings/object.bin"] = b"not-an-object"
        embedded = embed_manifest(dangerous_part, manifest)
        with self.assertRaises(OpcSecurityError) as raised:
            validate_roundtrip_opc(read_safe_opc(_zip_parts(embedded)))
        self.assertEqual(raised.exception.code, "OPC_DANGEROUS_PART")

    def test_manifest_graph_tamper_is_rejected(self) -> None:
        manifest, _ = _signed_manifest()
        embedded = embed_manifest(_base_parts(), manifest)
        embedded["word/_rels/document.xml.rels"] = embedded["word/_rels/document.xml.rels"].replace(
            b"../customXml/flaRoundtripManifest.xml",
            b"../customXml/flaRoundtripManifestProps.xml",
        )
        with self.assertRaises(OpcSecurityError) as raised:
            extract_manifest(embedded)
        self.assertEqual(raised.exception.code, "CUSTOM_XML_RELATIONSHIP_INVALID")

        extra_override = embed_manifest(_base_parts(), manifest)
        extra_override["[Content_Types].xml"] = extra_override["[Content_Types].xml"].replace(
            b"</Types>",
            b'<Override PartName="/customXml/notAllowed.xml" ContentType="application/xml"/></Types>',
        )
        with self.assertRaises(OpcSecurityError) as raised:
            extract_manifest(extra_override)
        self.assertEqual(raised.exception.code, "CUSTOM_XML_CONTENT_TYPE_SET_INVALID")

    def test_unresolved_revision_in_any_word_xml_part_blocks_validation(self) -> None:
        manifest, _ = _signed_manifest()
        parts = embed_manifest(_base_parts(), manifest)
        parts["word/comments.xml"] = (
            f'<w:comments xmlns:w="{W}"><w:comment w:id="0"><w:ins><w:r><w:t>x</w:t>'
            "</w:r></w:ins></w:comment></w:comments>"
        ).encode("utf-8")
        findings = find_unresolved_revisions(parts)
        self.assertEqual([(item.part, item.element_name) for item in findings], [("word/comments.xml", "ins")])
        with self.assertRaises(OpcSecurityError) as raised:
            validate_roundtrip_opc(read_safe_opc(_zip_parts(parts)))
        self.assertEqual(raised.exception.code, "WORD_TRACKED_CHANGES_NOT_ACCEPTED")
        self.assertEqual(raised.exception.part, "word/comments.xml")

        conflict = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml">'
            "<w:body><w14:conflictIns/></w:body></w:document>"
        ).encode("utf-8")
        findings = find_unresolved_revisions({"word/header1.xml": conflict})
        self.assertEqual(findings[0].element_name, "conflictIns")


class WordStructureSecurityTests(unittest.TestCase):
    def test_readonly_hash_binds_fixed_text_around_field_but_ignores_field_cache(self) -> None:
        original = _document(
            '<w:p><w:r><w:t>固定前文</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText> PAGE </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>1</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            '<w:r><w:t>固定后文</w:t></w:r></w:p>'
        )
        cache_changed = original.replace(b">1<", b">9<")
        fixed_changed = original.replace("固定前文".encode(), "篡改前文".encode())

        digest = readonly_document_hash({"word/document.xml": original})
        self.assertEqual(
            digest,
            readonly_document_hash({"word/document.xml": cache_changed}),
        )
        self.assertNotEqual(
            digest,
            readonly_document_hash({"word/document.xml": fixed_changed}),
        )

    def test_readonly_hash_ignores_comment_parts_but_revision_scan_does_not(self) -> None:
        base = {"word/document.xml": _document()}
        commented = {
            **base,
            "word/comments.xml": (
                f'<w:comments xmlns:w="{W}"><w:comment w:id="0">'
                "<w:p><w:r><w:t>仅供复核</w:t></w:r></w:p>"
                "</w:comment></w:comments>"
            ).encode("utf-8"),
        }
        self.assertEqual(readonly_document_hash(base), readonly_document_hash(commented))
        commented["word/comments.xml"] = commented["word/comments.xml"].replace(
            b"<w:p>", b"<w:ins><w:p>", 1
        ).replace(b"</w:p>", b"</w:p></w:ins>", 1)
        self.assertTrue(find_unresolved_revisions(commented))

    def test_readonly_hash_accepts_word_toc_and_whitespace_normalization(self) -> None:
        before = _document(
            '<w:sdt><w:sdtPr><w:tag w:val="template.toc"/></w:sdtPr><w:sdtContent>'
            '<w:p><w:fldSimple w:instr=" TOC \\o &quot;1-3&quot; "><w:r><w:t>目录</w:t></w:r></w:fldSimple></w:p>'
            '<w:p><w:fldSimple w:instr=" PAGEREF old "><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p>'
            '</w:sdtContent></w:sdt><w:p><w:r><w:t>甲</w:t><w:br/><w:t>乙</w:t></w:r></w:p>'
        )
        after = _document(
            '<w:sdt><w:sdtPr><w:tag w:val="template.toc"/></w:sdtPr><w:sdtContent>'
            '<w:p><w:fldSimple w:instr=" TOC \\o &quot;1-3&quot; "><w:r><w:t>目录</w:t></w:r></w:fldSimple></w:p>'
            '<w:p><w:fldSimple w:instr=" PAGEREF new1 "><w:r><w:t>2</w:t></w:r></w:fldSimple></w:p>'
            '<w:p><w:fldSimple w:instr=" PAGEREF new2 "><w:r><w:t>3</w:t></w:r></w:fldSimple></w:p>'
            '</w:sdtContent></w:sdt><w:p><w:r><w:t>甲 乙</w:t></w:r></w:p>'
        )
        changed = after.replace("甲 乙".encode(), "甲 丙".encode())

        before_hash = readonly_document_hash({"word/document.xml": before})
        self.assertEqual(
            before_hash,
            readonly_document_hash({"word/document.xml": after}),
        )
        self.assertNotEqual(
            before_hash,
            readonly_document_hash({"word/document.xml": changed}),
        )

    def test_split_active_field_is_rejected_but_report_fields_are_allowed(self) -> None:
        active = _document(
            '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> D</w:instrText></w:r>'
            '<w:r><w:instrText>DEAUTO calc</w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        )
        with self.assertRaises(StructureSecurityError) as raised:
            validate_field_instructions({"word/document.xml": active})
        self.assertEqual(raised.exception.code, "WORD_FIELD_INSTRUCTION_FORBIDDEN")

        safe = _document(
            '<w:p><w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p>'
            '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText> REF bookmark_1 \\h </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        )
        self.assertEqual(validate_field_instructions({"word/document.xml": safe}), ("PAGE", "REF bookmark_1 \\h"))

        manifest, _ = _signed_manifest()
        active_package = embed_manifest(_base_parts(document=active), manifest)
        with self.assertRaises(OpcSecurityError) as raised:
            validate_roundtrip_opc(read_safe_opc(_zip_parts(active_package)))
        self.assertEqual(raised.exception.code, "WORD_FIELD_INSTRUCTION_FORBIDDEN")

    def test_extracts_controlled_block_row_slot_and_geometry(self) -> None:
        body = (
            '<w:sdt><w:sdtPr><w:tag w:val="fla:r7:v1:b:blockToken01"/></w:sdtPr>'
            '<w:sdtContent><w:tbl><w:tblPr/><w:tblGrid><w:gridCol/></w:tblGrid>'
            '<w:sdt><w:sdtPr><w:tag w:val="fla:r7:v1:r:rowToken0001"/></w:sdtPr>'
            '<w:sdtContent><w:tr><w:tc><w:tcPr><w:gridSpan w:val="1"/></w:tcPr><w:p>'
            '<w:sdt><w:sdtPr><w:tag w:val="fla:r7:v1:s:slotToken001"/></w:sdtPr>'
            '<w:sdtContent><w:r><w:t>整改建议</w:t></w:r></w:sdtContent></w:sdt>'
            "</w:p></w:tc></w:tr></w:sdtContent></w:sdt>"
            "</w:tbl></w:sdtContent></w:sdt>"
        )
        structure = extract_roundtrip_structure({"word/document.xml": _document(body)})
        self.assertEqual(len(structure.blocks), 1)
        self.assertEqual(structure.blocks[0].row_tokens, ("rowToken0001",))
        self.assertEqual(structure.rows[0].block_token, "blockToken01")
        self.assertEqual(structure.rows[0].sort_order, 1)
        self.assertEqual(structure.rows[0].slot_tokens, ("slotToken001",))
        self.assertEqual(len(structure.rows[0].geometry_hash), 64)
        self.assertEqual(structure.slots[0].value, "整改建议")

    def test_rejects_duplicate_controlled_tags_and_orphan_rows(self) -> None:
        duplicate = (
            '<w:p><w:sdt><w:sdtPr><w:tag w:val="fla:r7:v1:s:slotToken001"/></w:sdtPr>'
            '<w:sdtContent><w:r><w:t>a</w:t></w:r></w:sdtContent></w:sdt>'
            '<w:sdt><w:sdtPr><w:tag w:val="fla:r7:v1:s:slotToken001"/></w:sdtPr>'
            '<w:sdtContent><w:r><w:t>b</w:t></w:r></w:sdtContent></w:sdt></w:p>'
        )
        with self.assertRaises(StructureSecurityError) as raised:
            extract_roundtrip_structure({"word/document.xml": _document(duplicate)})
        self.assertEqual(raised.exception.code, "SDT_CONTROLLED_TAG_DUPLICATE")

        orphan = (
            '<w:tbl><w:sdt><w:sdtPr><w:tag w:val="fla:r7:v1:r:rowToken0001"/></w:sdtPr>'
            '<w:sdtContent><w:tr><w:tc><w:p/></w:tc></w:tr></w:sdtContent></w:sdt></w:tbl>'
        )
        with self.assertRaises(StructureSecurityError) as raised:
            extract_roundtrip_structure({"word/document.xml": _document(orphan)})
        self.assertEqual(raised.exception.code, "SDT_ROW_BLOCK_MISSING")


class WordRefreshLockTests(unittest.TestCase):
    def test_word_refresh_uses_private_unlocked_copy_and_restores_exact_lock(self) -> None:
        document = _document(
            '<w:sdt><w:sdtPr><w:tag w:val="template.control.1"/>'
            '<w:lock w:val="sdtContentLocked"/></w:sdtPr>'
            '<w:sdtContent><w:p><w:r><w:t>只读内容</w:t></w:r></w:p></w:sdtContent></w:sdt>'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.docx"
            output = root / "output.docx"
            status = root / "status.json"
            source.write_bytes(_zip_parts({"word/document.xml": document}))
            captured_input: Path | None = None

            def fake_run(command, **_kwargs):
                nonlocal captured_input
                captured_input = Path(command[command.index("-InputPath") + 1])
                target = Path(command[command.index("-OutputPath") + 1])
                state = Path(command[command.index("-StatusPath") + 1])
                with zipfile.ZipFile(captured_input) as package:
                    unlocked = etree.fromstring(package.read("word/document.xml"))
                self.assertFalse(unlocked.xpath("//w:lock", namespaces={"w": W}))
                target.write_bytes(captured_input.read_bytes())
                state.write_text(
                    json.dumps({"status": "succeeded", "page_count": 1}),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("app.report_export.word.subprocess.run", side_effect=fake_run):
                result = refresh_with_word(source, output, status_path=status)

            self.assertEqual(result["page_count"], 1)
            assert captured_input is not None
            self.assertFalse(captured_input.exists())
            with zipfile.ZipFile(output) as package:
                restored = etree.fromstring(package.read("word/document.xml"))
            self.assertEqual(
                restored.xpath("string(//w:lock/@w:val)", namespaces={"w": W}),
                "sdtContentLocked",
            )


class LocalSessionBoundaryTests(unittest.TestCase):
    def test_session_token_is_required_for_every_mutating_api_request(self) -> None:
        token = "r7-local-session-test-token"

        def request(headers: list[tuple[bytes, bytes]]) -> Request:
            return Request(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "path": "/api/projects",
                    "raw_path": b"/api/projects",
                    "query_string": b"",
                    "headers": headers,
                    "client": ("127.0.0.1", 10000),
                    "server": ("127.0.0.1", 8000),
                }
            )

        calls = 0

        async def call_next(_request: Request) -> Response:
            nonlocal calls
            calls += 1
            return Response(status_code=204)

        with patch.dict(os.environ, {"FULUA_SESSION_TOKEN": token}, clear=False):
            denied = asyncio.run(
                reject_business_writes_during_maintenance(request([]), call_next)
            )
            accepted_by_middleware = asyncio.run(
                reject_business_writes_during_maintenance(
                    request([(b"x-fulua-session-token", token.encode("ascii"))]),
                    call_next,
                )
            )

        self.assertEqual(denied.status_code, 403)
        self.assertIn("本机操作验证失败".encode("utf-8"), denied.body)
        self.assertEqual(accepted_by_middleware.status_code, 204)
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
