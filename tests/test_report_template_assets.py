from __future__ import annotations

import sys
import copy
import hashlib
import os
import tempfile
import unittest
import zipfile
import re
import json
import shutil
import subprocess
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
from scripts.verify_report_template_freeze import verify_asset_dir, verify_packaged_assets

ASSETS = ROOT / "templates" / "report" / "2023-2025.12.08"


class ReportTemplateAssetTests(unittest.TestCase):
    def test_field_dictionary_has_unique_stable_ids_and_slots(self) -> None:
        result = validate_field_dictionary(ASSETS / "field_dictionary.json")
        self.assertEqual(len(result.fields), 26)
        self.assertTrue(all(not field.field_id.startswith(("paragraph", "table")) for field in result.fields))
        self.assertEqual(result.schema_version, "2.0")
        self.assertEqual(result.package_id, "report-2023-2025.12.08")
        self.assertEqual(result.contract_status, "frozen")
        self.assertEqual(len(result.rule_contracts), 70)
        self.assertEqual(len(result.projection_catalog), 92)
        self.assertTrue(all(isinstance(field.source_kind, str) for field in result.fields))
        self.assertTrue(all(field.accepted_input_kinds is not None for field in result.fields))
        self.assertTrue(all(field.missing_behavior for field in result.fields))
        self.assertTrue(all(field.conflict_behavior for field in result.fields))
        self.assertTrue(all(field.governed_parameter_ids for field in result.fields))
        governed = [parameter for field in result.fields for parameter in field.governed_parameter_ids]
        self.assertEqual(len(governed), len(set(governed)))
        expected_rule_refs = {
            f"3.6.{section}.{index:02d}"
            for section, count in ((1, 10), (2, 11), (3, 16), (4, 11), (5, 8), (6, 14))
            for index in range(1, count + 1)
        }
        actual_rule_refs = {rule for field in result.fields for rule in field.readme_rule_refs}
        self.assertEqual(actual_rule_refs, expected_rule_refs)
        self.assertEqual({contract.rule_ref for contract in result.rule_contracts}, expected_rule_refs)
        effective_client = next(contract for contract in result.rule_contracts if contract.rule_ref == "3.6.1.03")
        self.assertEqual(
            {authority.authority_id: authority.source_kind for authority in effective_client.authorities},
            {
                "report.organization.assessed_name": "manual",
                "report.organization.client_name": "manual",
                "report.organization.effective_client_name": "derived",
            },
        )
        assessment_org = next(contract for contract in result.rule_contracts if contract.rule_ref == "3.6.1.07")
        self.assertEqual({authority.source_kind for authority in assessment_org.authorities}, {"template_constant"})
        approval_dates = next(contract for contract in result.rule_contracts if contract.rule_ref == "3.6.2.03")
        self.assertEqual(
            {authority.authority_id: authority.source_kind for authority in approval_dates.authorities},
            {
                "report.approval.compiled_date": "derived",
                "report.approval.review_date": "derived",
                "report.approval.approved_date": "manual",
            },
        )
        with zipfile.ZipFile(ASSETS / "runtime_template.docx") as package:
            document = etree.fromstring(package.read("word/document.xml"))
            story_roots = [document]
            for name in package.namelist():
                if re.fullmatch(r"word/(header|footer)\d+\.xml", name) or name in {
                    "word/footnotes.xml",
                    "word/endnotes.xml",
                }:
                    story_roots.append(etree.fromstring(package.read(name)))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        bookmarks = set(document.xpath("//w:bookmarkStart/@w:name", namespaces=ns))
        tags = {
            tag
            for root in story_roots
            for tag in root.xpath("//w:sdtPr/w:tag/@w:val", namespaces=ns)
        }
        for field in result.fields:
            for slot in field.export_slots:
                kind, target = slot.split(":", 1)
                self.assertIn(target, bookmarks if kind == "bookmark" else tags, slot)
        additional_references = next(
            field for field in result.fields if field.field_id == "report.assessment.additional_reference_standards"
        )
        self.assertEqual(additional_references.cardinality, "many")
        self.assertEqual(additional_references.source_kind, "manual")
        self.assertEqual(additional_references.accepted_input_kinds, ["manual", "imported"])
        self.assertTrue(additional_references.editable)
        self.assertEqual(additional_references.export_slots, ["bookmark:report_additional_reference_standards"])
        system_name = next(field for field in result.fields if field.field_id == "report.system.name")
        self.assertEqual(
            system_name.export_slots,
            [
                "sdt:report.cover.system_name",
                "sdt:report.system.name",
                "sdt:report.declaration.system_name",
                "sdt:report.header.system_name.1",
                "sdt:report.header.system_name.2",
                "sdt:report.header.system_name.3",
            ],
        )
        high_risk_judgement = next(
            field for field in result.fields if field.field_id == "report.risk.high_risk_judgement"
        )
        self.assertEqual(high_risk_judgement.data_type, "derived")
        self.assertEqual(high_risk_judgement.source_kind, "derived")
        self.assertEqual(high_risk_judgement.accepted_input_kinds, [])
        self.assertFalse(high_risk_judgement.editable)
        self.assertEqual(high_risk_judgement.source_evidence, ["confirmed_risk_snapshot"])
        self.assertEqual(high_risk_judgement.format["rule"], "high_risk_count_gt_zero")
        self.assertEqual(high_risk_judgement.format["present_text"], "判定系统存在高风险")
        self.assertEqual(high_risk_judgement.format["absent_text"], "判定系统不存在高风险")
        self.assertEqual(high_risk_judgement.format["incomplete_behavior"], "block_export")

        assessment_name = next(
            field for field in result.fields if field.field_id == "report.organization.assessment_name"
        )
        self.assertEqual(assessment_name.source_kind, "template_constant")
        self.assertEqual(assessment_name.accepted_input_kinds, [])
        self.assertFalse(assessment_name.editable)

        report_date = next(field for field in result.fields if field.field_id == "report.identity.date")
        total_copies = next(
            field for field in result.fields if field.field_id == "report.distribution.total_copies"
        )
        conclusion = next(field for field in result.fields if field.field_id == "report.result.conclusion")
        for field in (report_date, total_copies, conclusion):
            self.assertEqual(field.source_kind, "derived")
            self.assertEqual(field.accepted_input_kinds, [])
            self.assertFalse(field.editable)

    def test_field_dictionary_rejects_legacy_multi_source_authority(self) -> None:
        payload = json.loads((ASSETS / "field_dictionary.json").read_text(encoding="utf-8"))
        payload["fields"][0]["source_kind"] = ["manual", "imported"]
        with tempfile.TemporaryDirectory() as value:
            path = Path(value) / "field_dictionary.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_field_dictionary(path)

    def test_field_dictionary_rejects_cross_rule_authority_conflicts(self) -> None:
        payload = json.loads((ASSETS / "field_dictionary.json").read_text(encoding="utf-8"))
        contract = next(item for item in payload["rule_contracts"] if item["rule_ref"] == "3.6.2.04")
        authority = next(
            item for item in contract["authorities"]
            if item["authority_id"] == "report.approval.review_date"
        )
        authority.update(
            source_kind="manual",
            accepted_input_kinds=["manual", "imported"],
            editable=True,
        )
        with tempfile.TemporaryDirectory() as value:
            path = Path(value) / "field_dictionary.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "README_RULE_AUTHORITY_CONFLICT"):
                validate_field_dictionary(path)

    def test_field_dictionary_rejects_ungoverned_authority_and_projection_typo(self) -> None:
        original = json.loads((ASSETS / "field_dictionary.json").read_text(encoding="utf-8"))
        authority_payload = copy.deepcopy(original)
        contract = next(item for item in authority_payload["rule_contracts"] if item["rule_ref"] == "3.6.3.03")
        contract["authorities"][0]["authority_id"] = "report.nonexistent.typo"
        with tempfile.TemporaryDirectory() as value:
            authority_path = Path(value) / "authority.json"
            authority_path.write_text(json.dumps(authority_payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "README_RULE_AUTHORITY_UNGOVERNED"):
                validate_field_dictionary(authority_path)

            projection_payload = copy.deepcopy(original)
            projection_contract = next(
                item for item in projection_payload["rule_contracts"]
                if item["rule_ref"] == "3.6.3.03"
            )
            projection_contract["projection_ids"][0] = "slot:report.nonexistent.typo"
            projection_path = Path(value) / "projection.json"
            projection_path.write_text(json.dumps(projection_payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "README_RULE_PROJECTION_CATALOG_MISMATCH"):
                validate_field_dictionary(projection_path)

            synchronized_typo = copy.deepcopy(original)
            synchronized_contract = next(
                item for item in synchronized_typo["rule_contracts"]
                if item["rule_ref"] == "3.6.2.07"
            )
            approved = synchronized_contract["projection_ids"][0]
            typo = "service:report.nonexistent.typo"
            synchronized_contract["projection_ids"][0] = typo
            synchronized_typo["projection_catalog"][
                synchronized_typo["projection_catalog"].index(approved)
            ] = typo
            synchronized_path = Path(value) / "synchronized-projection.json"
            synchronized_path.write_text(
                json.dumps(synchronized_typo, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "README_RULE_PROJECTION_CATALOG_MISMATCH"):
                validate_field_dictionary(synchronized_path)

    def test_field_dictionary_requires_rule_trace_on_the_governing_field(self) -> None:
        payload = json.loads((ASSETS / "field_dictionary.json").read_text(encoding="utf-8"))
        appendix_a = next(item for item in payload["fields"] if item["field_id"] == "report.appendix_a.records")
        appendix_a["readme_rule_refs"].remove("3.6.3.13")
        with tempfile.TemporaryDirectory() as value:
            path = Path(value) / "field_dictionary.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "README_RULE_AUTHORITY_TRACE_MISSING"):
                validate_field_dictionary(path)

    def test_freeze_record_uses_exact_r0_counts(self) -> None:
        payload = json.loads((ASSETS / "asset_hashes.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "2.0")
        self.assertEqual(
            payload["freeze_record"],
            {
                "status": "frozen",
                "business_field_count": 26,
                "readme_rule_count": 70,
                "semantic_scalar_slot_count": 29,
                "ooxml_content_control_count": 612,
                "word_content_control_count": 605,
                "section_count": 17,
                "table_count": 55,
                "pending_rule_hint_count": 121,
                "pending_rule_hints_blocking": False,
                "word_acceptance_evidence_sha256": "e53071bb482295fdc8d7f56ab8cf04bacfeb8448ad98e58a82f84866f17dc4f9",
            },
        )

    def test_frozen_package_generator_cannot_rewrite_the_same_package_id(self) -> None:
        manifest_path = ASSETS / "manifest.json"
        hashes_path = ASSETS / "asset_hashes.json"
        before = (manifest_path.read_bytes(), hashes_path.read_bytes())
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_report_asset_manifest.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("REPORT_TEMPLATE_PACKAGE_ALREADY_FROZEN", result.stderr)
        self.assertEqual((manifest_path.read_bytes(), hashes_path.read_bytes()), before)

    def test_word_acceptance_evidence_is_bound_to_the_runtime_template(self) -> None:
        evidence_path = ROOT / "docs" / "report-tool" / "evidence" / "r0-word-acceptance.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(
            evidence["runtime_template_sha256"],
            hashlib.sha256((ASSETS / "runtime_template.docx").read_bytes()).hexdigest(),
        )
        self.assertEqual(evidence["open_method"], "OpenNoRepairDialog")
        self.assertEqual(evidence["display_alerts"], "all")
        self.assertTrue(evidence["roundtrip_saved_and_reopened"])
        self.assertEqual(
            (evidence["section_count"], evidence["table_count"], evidence["content_control_count"]),
            (17, 55, 605),
        )

    def test_frozen_text_assets_force_lf_checkout_bytes(self) -> None:
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        for asset in (
            "asset_hashes.json",
            "field_dictionary.json",
            "manifest.json",
            "narrative_templates.json",
            "rule_hints.json",
        ):
            self.assertIn(
                f"templates/report/2023-2025.12.08/{asset} text eol=lf",
                attributes,
            )
        self.assertIn(
            "docs/report-tool/evidence/r0-word-acceptance.json text eol=lf",
            attributes,
        )
        self.assertIn(
            "templates/report/2023-2025.12.08/runtime_template.docx -text",
            attributes,
        )
        word_acceptance_script = (ROOT / "scripts" / "test_word_report_template.ps1").read_text(encoding="utf-8")
        self.assertIn('-replace "`r`n", "`n"', word_acceptance_script)
        self.assertIn('$json + "`n"', word_acceptance_script)

    def test_freeze_verifier_compares_all_six_packaged_assets(self) -> None:
        source = verify_asset_dir(ASSETS, require_trust_root=True)
        self.assertEqual(len(source), 6)
        with tempfile.TemporaryDirectory() as value:
            packaged = Path(value) / "2023-2025.12.08"
            shutil.copytree(ASSETS, packaged)
            verify_packaged_assets(packaged)
            (packaged / "manifest.json").write_bytes((packaged / "manifest.json").read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "REPORT_TEMPLATE_ASSET_HASH_MISMATCH"):
                verify_packaged_assets(packaged)

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
