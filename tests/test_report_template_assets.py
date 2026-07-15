from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.report_templates.models import NarrativeTemplateLibrary
from app.services.report_templates.validator import (
    load_json_model,
    validate_field_dictionary,
    validate_narrative_templates,
    validate_rule_hints,
)
from lxml import etree

ASSETS = ROOT / "templates" / "report" / "2023-2025.12.08"


class ReportTemplateAssetTests(unittest.TestCase):
    def test_field_dictionary_has_unique_stable_ids_and_slots(self) -> None:
        result = validate_field_dictionary(ASSETS / "field_dictionary.json")
        self.assertGreaterEqual(len(result.fields), 17)
        self.assertTrue(all(not field.field_id.startswith(("paragraph", "table")) for field in result.fields))
        with zipfile.ZipFile(ASSETS / "runtime_template.docx") as package:
            document = etree.fromstring(package.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        bookmarks = set(document.xpath("//w:bookmarkStart/@w:name", namespaces=ns))
        tags = set(document.xpath("//w:sdtPr/w:tag/@w:val", namespaces=ns))
        for field in result.fields:
            for slot in field.export_slots:
                kind, target = slot.split(":", 1)
                self.assertIn(target, bookmarks if kind == "bookmark" else tags, slot)
        additional_references = next(
            field for field in result.fields if field.field_id == "report.assessment.additional_reference_standards"
        )
        self.assertEqual(additional_references.cardinality, "many")
        self.assertEqual(additional_references.source_kind, ["manual", "imported"])
        self.assertEqual(additional_references.export_slots, ["bookmark:report_additional_reference_standards"])
        high_risk_judgement = next(
            field for field in result.fields if field.field_id == "report.risk.high_risk_judgement"
        )
        self.assertEqual(high_risk_judgement.data_type, "derived")
        self.assertEqual(high_risk_judgement.source_kind, ["derived"])
        self.assertEqual(high_risk_judgement.source_evidence, ["confirmed_risk_snapshot"])
        self.assertEqual(high_risk_judgement.format["rule"], "high_risk_count_gt_zero")
        self.assertEqual(high_risk_judgement.format["present_text"], "判定系统存在高风险")
        self.assertEqual(high_risk_judgement.format["absent_text"], "判定系统不存在高风险")
        self.assertEqual(high_risk_judgement.format["incomplete_behavior"], "block_export")

    def test_rule_hints_cover_every_comment_without_approval_or_pii(self) -> None:
        result = validate_rule_hints(ASSETS / "rule_hints.json")
        self.assertEqual(len(result.rules), 121)
        self.assertEqual({rule.approval_status for rule in result.rules}, {"pending"})
        self.assertEqual({rule.runtime_behavior for rule in result.rules}, {"none"})
        counts = Counter(rule.category for rule in result.rules)
        self.assertEqual(
            counts,
            {"history": 18, "layout": 10, "field_source": 7, "consistency": 18, "conditional": 19, "evidence": 13, "authoring_help": 36},
        )
        payload = (ASSETS / "rule_hints.json").read_text(encoding="utf-8")
        for forbidden in ('"author":', '"initials":', '"created_at":', "中国建设银行", "F:\\"):
            self.assertNotIn(forbidden, payload)

    def test_narratives_require_confirmation_and_exclude_scoring_parameters(self) -> None:
        result = validate_narrative_templates(ASSETS / "narrative_templates.json")
        self.assertTrue(all(item.user_confirmation_required for item in result.templates))
        issue_references = [item for item in result.templates if item.template_id.startswith("narrative.security_issue.")]
        self.assertEqual(len(issue_references), 16)
        self.assertEqual(
            {item.section_id for item in issue_references},
            {
                "security-issues.physical",
                "security-issues.network",
                "security-issues.device",
                "security-issues.application",
                "security-issues.policy",
                "security-issues.personnel",
                "security-issues.construction",
                "security-issues.emergency",
            },
        )
        self.assertTrue(all("XX" not in item.text for item in issue_references))

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            path = Path(value) / "duplicate.json"
            path.write_text('{"schema_version":"1.0","schema_version":"1.0","templates":[]}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON_DUPLICATE_KEY"):
                load_json_model(path, NarrativeTemplateLibrary)


if __name__ == "__main__":
    unittest.main()
