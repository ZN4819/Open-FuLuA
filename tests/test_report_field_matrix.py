from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.report_core.field_matrix import (  # noqa: E402
    FIELD_DICTIONARY_RELATIVE_PATH,
    MANIFEST_RELATIVE_PATH,
    MATRIX_RELATIVE_PATH,
    FieldMatrixValidationError,
    load_default_field_matrix,
    load_field_matrix,
    validate_default_field_matrix,
)


MATRIX_PATH = ROOT.joinpath(*MATRIX_RELATIVE_PATH)
FIELD_DICTIONARY_PATH = ROOT.joinpath(*FIELD_DICTIONARY_RELATIVE_PATH)
MANIFEST_PATH = ROOT.joinpath(*MANIFEST_RELATIVE_PATH)


class ReportFieldMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        load_default_field_matrix.cache_clear()
        self.payload = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        load_default_field_matrix.cache_clear()

    def _load_mutation(self, mutate) -> None:
        payload = copy.deepcopy(self.payload)
        mutate(payload)
        with tempfile.TemporaryDirectory() as value:
            path = Path(value) / "field_relation_matrix.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            load_field_matrix(
                path,
                field_dictionary_path=FIELD_DICTIONARY_PATH,
                manifest_path=MANIFEST_PATH,
            )

    def _assert_error(self, mutate, code: str) -> FieldMatrixValidationError:
        with self.assertRaises(FieldMatrixValidationError) as raised:
            self._load_mutation(mutate)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_default_matrix_is_startup_valid_and_covers_all_r0_rules(self) -> None:
        validate_default_field_matrix()
        matrix = load_default_field_matrix()

        self.assertEqual(matrix.matrix_version, "R2.2-2026-07-15.1")
        self.assertEqual(matrix.package_id, "report-2023-2025.12.08")
        self.assertEqual((len(matrix.fields), len(matrix.relations)), (99, 70))
        self.assertEqual(len(matrix.sha256), 64)
        self.assertEqual(
            Counter(
                ".".join(item.readme_rule_ref.split(".")[:3])
                for item in matrix.relations
            ),
            {
                "3.6.1": 10,
                "3.6.2": 11,
                "3.6.3": 16,
                "3.6.4": 11,
                "3.6.5": 8,
                "3.6.6": 14,
            },
        )

    def test_authority_paths_preserve_single_source_and_r2_entity_contracts(self) -> None:
        matrix = load_default_field_matrix()

        system_name = matrix.relation("FRM-3.6.1.01")
        self.assertEqual(system_name.authority_field_id, "report.system.name")
        self.assertEqual(system_name.authority_paths, ("system_profiles.system_name",))
        self.assertEqual(system_name.source_kind, "manual")
        self.assertTrue(system_name.editable)

        report_date = matrix.relation("FRM-3.6.2.02")
        self.assertEqual(report_date.authority_paths, ("report_phase_dates.analysis_end",))
        self.assertEqual(report_date.source_kind, "derived")
        self.assertFalse(report_date.editable)

        assessment_organization = matrix.relation("FRM-3.6.1.07")
        self.assertEqual(assessment_organization.source_kind, "template_constant")
        self.assertFalse(assessment_organization.editable)

    def test_every_relation_has_stable_test_vectors_and_export_mappings(self) -> None:
        matrix = load_default_field_matrix()
        test_ids: set[str] = set()
        mapping_ids: set[str] = set()

        for relation in matrix.relations:
            rule_ref = relation.readme_rule_ref
            self.assertEqual(relation.relation_id, f"FRM-{rule_ref}")
            self.assertEqual(
                relation.test_vector_ids,
                tuple(
                    f"TV-{rule_ref}-{suffix}"
                    for suffix in ("authority", "missing", "conflict")
                ),
            )
            self.assertEqual(
                relation.export_mapping_ids,
                tuple(
                    f"EM-{rule_ref}-{index:02d}"
                    for index in range(1, len(relation.target_ids) + 1)
                ),
            )
            self.assertEqual(relation.export_stage, "R4")
            self.assertTrue(test_ids.isdisjoint(relation.test_vector_ids))
            self.assertTrue(mapping_ids.isdisjoint(relation.export_mapping_ids))
            test_ids.update(relation.test_vector_ids)
            mapping_ids.update(relation.export_mapping_ids)

    def test_matrix_rejects_duplicate_relation_id(self) -> None:
        def duplicate(payload):
            payload["relations"][1]["relation_id"] = payload["relations"][0]["relation_id"]

        self._assert_error(duplicate, "FIELD_MATRIX_DUPLICATE_RELATION_ID")

    def test_matrix_rejects_missing_relation_field(self) -> None:
        def remove_field(payload):
            del payload["relations"][0]["constraint_expression"]

        error = self._assert_error(
            remove_field,
            "FIELD_MATRIX_REQUIRED_FIELD_MISSING",
        )
        self.assertIn("constraint_expression", error.details["fields"])

    def test_matrix_rejects_source_kind_outside_closed_set(self) -> None:
        def mutate(payload):
            payload["relations"][0]["source_kind"] = "imported"

        self._assert_error(mutate, "FIELD_MATRIX_SOURCE_KIND_INVALID")

    def test_matrix_rejects_closed_set_drift(self) -> None:
        def mutate(payload):
            payload["closed_sets"]["source_kind"].append("imported")

        self._assert_error(mutate, "FIELD_MATRIX_CLOSED_SET_INVALID")

    def test_matrix_rejects_missing_r0_rule_coverage(self) -> None:
        def mutate(payload):
            payload["relations"].pop()

        error = self._assert_error(mutate, "FIELD_MATRIX_RULE_COVERAGE_INVALID")
        self.assertEqual(error.details["missing"], ["3.6.6.14"])

    def test_matrix_rejects_source_asset_hash_drift(self) -> None:
        def mutate(payload):
            payload["source_contracts"]["field_dictionary_sha256"] = "0" * 64

        self._assert_error(mutate, "FIELD_MATRIX_SOURCE_HASH_MISMATCH")

    def test_matrix_rejects_behavior_that_conflicts_with_frozen_dictionary(self) -> None:
        def mutate(payload):
            relation = next(
                item
                for item in payload["relations"]
                if item["readme_rule_ref"] == "3.6.1.03"
            )
            relation["conflict_behavior"]["action"] = "reject"

        self._assert_error(mutate, "FIELD_MATRIX_BEHAVIOR_INVALID")

    def test_matrix_can_check_paths_against_runtime_entity_registry(self) -> None:
        all_paths = {
            path
            for item in self.payload["field_catalog"]
            for path in item["entity_paths"]
        }
        missing_path = "system_profiles.system_name"
        with self.assertRaises(FieldMatrixValidationError) as raised:
            load_field_matrix(
                MATRIX_PATH,
                field_dictionary_path=FIELD_DICTIONARY_PATH,
                manifest_path=MANIFEST_PATH,
                known_entity_paths=all_paths - {missing_path},
            )
        self.assertEqual(raised.exception.code, "FIELD_MATRIX_ENTITY_PATH_UNKNOWN")
        self.assertEqual(raised.exception.location, missing_path)

    def test_matrix_rejects_duplicate_field_binding(self) -> None:
        def mutate(payload):
            payload["field_catalog"].append(copy.deepcopy(payload["field_catalog"][0]))

        self._assert_error(mutate, "FIELD_MATRIX_DUPLICATE_FIELD_ID")


if __name__ == "__main__":
    unittest.main()
