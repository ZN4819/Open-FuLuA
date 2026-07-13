from __future__ import annotations

import unittest

from app.services.scoring import (
    calculate_flat_technical_rows,
    calculate_flat_management_rows,
    calculate_management_rows,
    calculate_management_unit_score,
    calculate_object_score,
    calculate_technical_rows,
    calculate_unit_score,
)


class ScoringTests(unittest.TestCase):
    def test_calculates_all_score_branches(self) -> None:
        cases = [
            (("/", "/", "/", "1", "1"), "/"),
            (("×", "/", "/", "1", "1"), "0.0000"),
            (("√", "√", "√", "0.2", "1.2"), "1.0000"),
            (("√", "×", "√", "0.5", "1"), "0.2500"),
            (("√", "√", "×", "1", "1.2"), "0.6000"),
            (("√", "×", "×", "0.2", "1.2"), "0.0600"),
            (("√", "/", "√", "0.5", "1"), "0.2500"),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(calculate_object_score(*arguments), expected)

    def test_incomplete_or_invalid_metrics_do_not_calculate(self) -> None:
        self.assertIsNone(calculate_object_score("√", "", "√", "1", "1"))
        self.assertIsNone(calculate_object_score("错误", "√", "√", strict=False))
        with self.assertRaises(ValueError):
            calculate_object_score("错误", "√", "√")

    def test_empty_factors_default_and_invalid_factors_are_rejected(self) -> None:
        self.assertEqual(calculate_object_score("√", "×", "×", "", None), "0.2500")
        with self.assertRaises(ValueError):
            calculate_object_score("√", "×", "√", "0.3", "1")
        self.assertIsNone(calculate_object_score("√", "×", "√", "0.3", "1", strict=False))

    def test_unit_score_requires_every_object_to_be_complete(self) -> None:
        self.assertEqual(calculate_unit_score(["1.0000", "/", "0.5000"]), "0.7500")
        self.assertEqual(calculate_unit_score(["/", "/"]), "/")
        self.assertEqual(calculate_unit_score(["1.0000", None]), "")
        self.assertEqual(calculate_unit_score([]), "")

    def test_calculate_rows_copies_input_and_sets_defaults(self) -> None:
        source = [
            {"unit": "单元一", "metric_result": {"d": "√", "a": "×", "k": "×", "ra": "0.2", "rk": "1.2"}},
            {"unit": "单元一", "metric_result": {"d": "/", "a": "/", "k": "/"}},
        ]
        output = calculate_technical_rows(source)
        self.assertNotIn("object_score", source[0]["metric_result"])
        self.assertEqual(output[0]["metric_result"]["object_score"], "0.0600")
        self.assertEqual(output[0]["metric_result"]["unit_score"], "0.0600")
        self.assertEqual(output[1]["metric_result"]["ra"], "1")
        self.assertEqual(output[1]["metric_result"]["rk"], "1")

    def test_flat_rows_use_the_same_rules(self) -> None:
        output = calculate_flat_technical_rows(
            [{"unit": "单元一", "d": "√", "a": "√", "k": "×", "ra": "1", "rk": "1.2"}]
        )
        self.assertEqual(output[0]["object_score"], "0.6000")
        self.assertEqual(output[0]["unit_score"], "0.6000")

    def test_management_scores_map_compliance_and_average_effective_objects(self) -> None:
        self.assertEqual(calculate_management_unit_score(["符合", "部分符合"]), "0.7500")
        self.assertEqual(calculate_management_unit_score(["符合", "不适用"]), "1.0000")
        self.assertEqual(calculate_management_unit_score(["不适用", "不适用"]), "/")
        self.assertEqual(calculate_management_unit_score(["符合", ""]), "")

    def test_management_invalid_compliance_is_strict_or_blank(self) -> None:
        with self.assertRaises(ValueError):
            calculate_management_unit_score(["未知"])
        self.assertEqual(calculate_management_unit_score(["未知"], strict=False), "")

    def test_management_rows_replace_manual_unit_score(self) -> None:
        source = [
            {"unit": "制度", "metric_result": {"compliance": "符合", "unit_score": "1.5000"}},
            {"unit": "制度", "metric_result": {"compliance": "部分符合", "unit_score": "1.5000"}},
        ]
        nested = calculate_management_rows(source)
        self.assertEqual([row["metric_result"]["unit_score"] for row in nested], ["0.7500", "0.7500"])
        flat = calculate_flat_management_rows(
            [{"unit": "制度", "compliance": "符合", "unit_score": "9.9999"}]
        )
        self.assertEqual(flat[0]["unit_score"], "1.0000")


if __name__ == "__main__":
    unittest.main()
