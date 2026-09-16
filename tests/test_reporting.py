from __future__ import annotations

import unittest

from zhi_engine.analysis import analyze_dataset, demo_dataset
from zhi_engine.reporting import build_report_docx


class ReportingTests(unittest.TestCase):
    def test_docx_report_is_generated(self) -> None:
        result = analyze_dataset(
            {
                "data": demo_dataset(),
                "config": {
                    "bootstrap_samples": 12,
                    "symbolic_basis_limit": 8,
                    "symbolic_candidate_limit": 4,
                    "symbolic_max_terms": 2,
                },
            }
        )
        body = build_report_docx(result, "知构引擎测试报告")
        self.assertTrue(body.startswith(b"PK"))
        self.assertGreater(len(body), 5000)


if __name__ == "__main__":
    unittest.main()
