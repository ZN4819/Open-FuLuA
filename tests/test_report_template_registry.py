from __future__ import annotations

import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.api.report_templates import get_report_template, get_report_template_fields, get_report_template_rule_hints
from app.services.report_templates.registry import PACKAGE_ID, ReportTemplateUnavailable, report_template_registry


class ReportTemplateRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        report_template_registry._package = None
        report_template_registry._failure = None

    def test_registry_loads_trusted_assets_without_returning_paths(self) -> None:
        package = report_template_registry.load(force=True)
        self.assertEqual(package.safe_summary()["status"], "available")
        self.assertNotIn("path", repr(package.safe_summary()).lower())
        self.assertEqual((len(package.manifest["tables"]), len(package.manifest["sections"])), (55, 17))

    def test_api_exposes_only_sanitized_pending_hints(self) -> None:
        response = get_report_template_rule_hints(PACKAGE_ID)
        self.assertEqual(len(response["rules"]), 121)
        self.assertEqual({r["approval_status"] for r in response["rules"]}, {"pending"})
        self.assertNotIn('"author":', repr(response).lower())

    def test_api_exposes_manual_additional_reference_standard_entry(self) -> None:
        response = get_report_template_fields(PACKAGE_ID)
        field = next(
            item for item in response["fields"]
            if item["field_id"] == "report.assessment.additional_reference_standards"
        )
        self.assertEqual(field["label"], "其他参考标准和规范")
        self.assertEqual(field["cardinality"], "many")
        self.assertEqual(field["source_kind"], ["manual", "imported"])

    def test_unknown_package_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            get_report_template("unknown")
        self.assertEqual(raised.exception.status_code, 404)

    def test_registry_rejects_asset_tampering(self) -> None:
        source = ROOT / "templates" / "report" / "2023-2025.12.08"
        with tempfile.TemporaryDirectory() as value:
            target = Path(value) / "assets"
            shutil.copytree(source, target)
            field_path = target / "field_dictionary.json"
            field_path.write_bytes(field_path.read_bytes() + b" ")
            original_root = report_template_registry._root
            report_template_registry._root = lambda: target
            try:
                with self.assertRaises(ReportTemplateUnavailable) as raised:
                    report_template_registry.load(force=True)
                self.assertEqual(raised.exception.code, "REPORT_TEMPLATE_HASH_MISMATCH")
                self.assertEqual(raised.exception.asset, "field_dictionary.json")
            finally:
                report_template_registry._root = original_root


if __name__ == "__main__": unittest.main()
