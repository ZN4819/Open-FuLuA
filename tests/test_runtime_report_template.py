from __future__ import annotations

import re
import sys
import unittest
import zipfile
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.report_templates import analyze_report_template

RUNTIME = ROOT / "templates" / "report" / "2023-2025.12.08" / "runtime_template.docx"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


class RuntimeReportTemplateTests(unittest.TestCase):
    def test_runtime_template_is_clean_and_structurally_complete(self) -> None:
        result = analyze_report_template(RUNTIME, source_role="synthetic_fixture")
        self.assertEqual(result.document.table_count, 55)
        self.assertEqual(result.document.section_count, 17)
        self.assertEqual(result.document.comment_count, 0)
        self.assertEqual(result.document.revision_count, 0)
        self.assertFalse(any(result.flags.model_dump().values()))
        from docx import Document
        reopened = Document(RUNTIME)
        self.assertEqual((len(reopened.tables), len(reopened.sections)), (55, 17))

    def test_runtime_package_has_no_private_or_executable_parts(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            names = set(package.namelist())
            self.assertFalse(any(name.startswith(("customXml/", "word/glossary/", "word/embeddings/", "word/activeX/")) for name in names))
            self.assertFalse(any("comments" in name.lower() or "people" in name.lower() for name in names))
            payload = b"\n".join(package.read(name) for name in names if name.endswith((".xml", ".rels"))).decode("utf-8", errors="ignore")
        for forbidden in ("中互金认证有限公司", "liwb@", "15201294794", "TargetMode=\"External\""):
            self.assertNotIn(forbidden, payload)

    def test_every_control_and_table_has_a_stable_identifier(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            document = etree.fromstring(package.read("word/document.xml"))
        tags = document.xpath("//w:sdtPr/w:tag/@w:val", namespaces=NS)
        bookmarks = document.xpath("//w:bookmarkStart[starts-with(@w:name, 'rt_table_')]/@w:name", namespaces=NS)
        self.assertEqual(len(tags), 583)
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(bookmarks, [f"rt_table_{index:03d}" for index in range(1, 56)])

    def test_a7_conformance_column_is_not_merged_across_objects(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            document = etree.fromstring(package.read("word/document.xml"))
        table = document.xpath("/w:document/w:body/w:tbl", namespaces=NS)[45]
        for row in table.xpath("./w:tr[position()>1]", namespaces=NS):
            cells = row.xpath("./w:tc | ./w:sdt/w:sdtContent/w:tc", namespaces=NS)
            self.assertGreaterEqual(len(cells), 4)
            self.assertFalse(cells[3].xpath("./w:tcPr/w:vMerge", namespaces=NS))


if __name__ == "__main__":
    unittest.main()
