from __future__ import annotations

import unittest

from zhi_engine.symbolic import evaluate_symbolic_formula, refit_symbolic_basis


class SymbolicRegressionTests(unittest.TestCase):
    def test_formula_evaluator_allows_only_arithmetic(self) -> None:
        self.assertAlmostEqual(evaluate_symbolic_formula("temperature / (temperature + 10)", 40), 0.8)
        with self.assertRaises(ValueError):
            evaluate_symbolic_formula("__import__('os').getcwd()", 40)

    def test_selected_basis_can_be_refitted(self) -> None:
        rows = [
            {"temperature": 10.0, "conversion": 0.2},
            {"temperature": 20.0, "conversion": 0.3},
            {"temperature": 30.0, "conversion": 0.4},
            {"temperature": 40.0, "conversion": 0.5},
        ]
        fitted = refit_symbolic_basis(rows, ["temperature"])
        self.assertGreater(fitted["r2"], 0.99)
        self.assertIn("temperature", fitted["formula_expression"])


if __name__ == "__main__":
    unittest.main()
