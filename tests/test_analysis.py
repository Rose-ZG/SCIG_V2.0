from __future__ import annotations

import unittest

from zhi_engine.analysis import analyze_dataset, build_report_markdown, demo_dataset


FAST_CONFIG = {
    "bootstrap_samples": 12,
    "symbolic_basis_limit": 8,
    "symbolic_candidate_limit": 4,
    "symbolic_max_terms": 2,
    "random_seed": 2026,
}


class AnalysisPipelineTests(unittest.TestCase):
    def test_units_are_normalized_and_raw_values_are_retained(self) -> None:
        rows = [
            {"temperature": 313.15, "conversion": 16, "batch": "A", "sample_id": "S1"},
            {"temperature": 323.15, "conversion": 22, "batch": "A", "sample_id": "S2"},
            {"temperature": 333.15, "conversion": 31, "batch": "B", "sample_id": "S3"},
            {"temperature": 343.15, "conversion": 43, "batch": "B", "sample_id": "S4"},
            {"temperature": 353.15, "conversion": 56, "batch": "C", "sample_id": "S5"},
            {"temperature": 363.15, "conversion": 67, "batch": "C", "sample_id": "S6"},
        ]
        result = analyze_dataset(
            {
                "data": rows,
                "config": {
                    **FAST_CONFIG,
                    "temperature_unit": "K",
                    "conversion_unit": "percent",
                },
            }
        )

        first = result["rows"][0]
        self.assertAlmostEqual(first["temperature"], 40.0, places=6)
        self.assertAlmostEqual(first["conversion"], 0.16, places=6)
        self.assertEqual(first["raw"]["temperature"], 313.15)
        self.assertEqual(first["raw"]["conversion"], 16.0)
        self.assertEqual(first["source_id"], "S1")
        self.assertTrue(result["data_contract"]["raw_values_retained"])

    def test_three_evidence_gates_and_active_design_are_returned(self) -> None:
        result = analyze_dataset(
            {
                "data": demo_dataset(),
                "config": {
                    **FAST_CONFIG,
                    "experiment_constraints": {
                        "temperature_min": 45,
                        "temperature_max": 145,
                        "safety_max_temperature": 140,
                        "budget": 2.0,
                    },
                },
            }
        )

        self.assertIn("evidence_gates", result)
        self.assertTrue(result["evidence_gates"]["cards"])
        for model in result["models"]:
            self.assertEqual(set(model["gates"]), {"science", "statistics", "identifiability"})
            self.assertIn(model["decision"], {"selected", "not_excluded", "excluded"})

        recommendation = result["experiment_design"]["recommended"]
        self.assertIsNotNone(recommendation)
        self.assertGreaterEqual(recommendation["temperature"], 45)
        self.assertLessEqual(recommendation["temperature"], 140)
        self.assertTrue(result["experiment_design"]["human_confirmation_required"])

    def test_anomaly_attribution_keeps_original_value_and_can_abstain(self) -> None:
        rows = demo_dataset()
        rows[6] = {**rows[6], "conversion": 0.98, "uncertainty": 0.02, "instrument": "calorimeter-2"}
        result = analyze_dataset({"data": rows, "config": FAST_CONFIG})

        self.assertTrue(result["anomalies"])
        item = result["anomalies"][0]
        self.assertTrue(item["raw_value_retained"])
        self.assertAlmostEqual(sum(item["posterior"].values()), 1.0, places=5)
        self.assertIn(item["conclusion"], {"entry_error", "measurement_error", "model_mismatch", "mechanism_change", "abstain"})
        source_row = next(row for row in result["rows"] if row["source_index"] == 6)
        self.assertEqual(source_row["raw"]["conversion"], 0.98)

    def test_open_set_and_audit_are_reproducible(self) -> None:
        request = {"data": demo_dataset(), "config": FAST_CONFIG}
        first = analyze_dataset(request)
        second = analyze_dataset(request)

        self.assertIn("H_other", first["open_set_decision"]["hypotheses"])
        self.assertIn(first["open_set_decision"]["decision"], {"collect_more_evidence", "provisional_conclusion"})
        self.assertEqual(first["audit"]["data_sha256"], second["audit"]["data_sha256"])
        self.assertTrue(first["audit"]["raw_values_retained"])

    def test_markdown_report_contains_decision_sections(self) -> None:
        result = analyze_dataset({"data": demo_dataset(), "config": FAST_CONFIG})
        report = build_report_markdown(result, "测试报告")

        self.assertIn("三道门控", report)
        self.assertIn("下一步实验建议", report)
        self.assertIn("开放集判断与审计", report)
        self.assertIn(result["audit"]["data_sha256"], report)


if __name__ == "__main__":
    unittest.main()
