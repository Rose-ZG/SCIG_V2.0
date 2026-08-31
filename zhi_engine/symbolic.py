from __future__ import annotations

import ast
import itertools
import math
import statistics
from typing import Any


OPS = ("+", "-", "*", "/")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _format_number(value: float, digits: int = 6) -> str:
    if not math.isfinite(value):
        return "0"
    rounded = round(value)
    if math.isclose(value, rounded, abs_tol=10 ** (-digits)):
        return str(int(rounded))
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _calc_bic(rmse: float, sample_count: int, param_count: float) -> float:
    return sample_count * math.log(max(rmse * rmse, 1e-8)) + param_count * math.log(max(sample_count, 2))


class _FormulaEvaluator(ast.NodeVisitor):
    def __init__(self, variables: dict[str, float]) -> None:
        self.variables = variables

    def visit_Expression(self, node: ast.Expression) -> float:
        return float(self.visit(node.body))

    def visit_BinOp(self, node: ast.BinOp) -> float:
        left = float(self.visit(node.left))
        right = float(self.visit(node.right))
        if not math.isfinite(left) or not math.isfinite(right):
            return float("nan")
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if abs(right) < 1e-12:
                return float("nan")
            return left / right
        raise ValueError("unsupported operator")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> float:
        operand = float(self.visit(node.operand))
        if not math.isfinite(operand):
            return float("nan")
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError("unsupported unary operator")

    def visit_Name(self, node: ast.Name) -> float:
        if node.id not in self.variables:
            raise ValueError(f"unknown variable: {node.id}")
        return float(self.variables[node.id])

    def visit_Constant(self, node: ast.Constant) -> float:
        value = node.value
        if isinstance(value, bool) or value is None:
            raise ValueError("unsupported constant")
        return float(value)

    def generic_visit(self, node: ast.AST) -> float:
        raise ValueError(f"unsupported syntax: {type(node).__name__}")


def evaluate_symbolic_formula(expression: str, temperature: float) -> float:
    tree = ast.parse(expression, mode="eval")
    value = _FormulaEvaluator({"temperature": float(temperature)}).visit(tree)
    return float(value)


def _evaluate_series(expression: str, temperatures: list[float]) -> list[float] | None:
    values: list[float] = []
    for temperature in temperatures:
        try:
            value = evaluate_symbolic_formula(expression, temperature)
        except Exception:
            return None
        if not math.isfinite(value):
            return None
        values.append(float(value))
    return values


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    size = len(matrix)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(augmented[row][col]))
        pivot_value = augmented[pivot][col]
        if abs(pivot_value) < 1e-12:
            return None
        if pivot != col:
            augmented[col], augmented[pivot] = augmented[pivot], augmented[col]

        divisor = augmented[col][col]
        for index in range(col, size + 1):
            augmented[col][index] /= divisor

        for row in range(size):
            if row == col:
                continue
            factor = augmented[row][col]
            if abs(factor) < 1e-12:
                continue
            for index in range(col, size + 1):
                augmented[row][index] -= factor * augmented[col][index]

    return [augmented[index][size] for index in range(size)]


def _fit_linear_model(design: list[list[float]], target: list[float]) -> list[float] | None:
    if not design:
        return None
    column_count = len(design[0])
    ata = [[0.0 for _ in range(column_count)] for _ in range(column_count)]
    aty = [0.0 for _ in range(column_count)]
    for row, y_value in zip(design, target):
        for i in range(column_count):
            aty[i] += row[i] * y_value
            for j in range(column_count):
                ata[i][j] += row[i] * row[j]
    for index in range(column_count):
        ata[index][index] += 1e-9
    return _solve_linear_system(ata, aty)


def _score_predictions(ys: list[float], predictions: list[float]) -> tuple[float, float]:
    residuals = [y - p for y, p in zip(ys, predictions)]
    rmse = math.sqrt(sum(r * r for r in residuals) / len(residuals))
    y_bar = statistics.mean(ys)
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, predictions))
    ss_tot = sum((y - y_bar) ** 2 for y in ys) or 1e-9
    r2 = max(0.0, 1 - ss_res / ss_tot)
    return rmse, r2


def _term_signature(values: list[float]) -> tuple[float, ...]:
    mean_value = statistics.mean(values)
    spread = statistics.pstdev(values)
    if spread < 1e-12:
        return tuple(0.0 for _ in values)
    normalized = tuple(round((value - mean_value) / spread, 8) for value in values)
    reversed_sign = tuple(round(-value, 8) for value in normalized)
    return min(normalized, reversed_sign)


def _looks_constant(values: list[float]) -> bool:
    if not values:
        return True
    return max(values) - min(values) < 1e-8


def _combine_expression(op: str, left: str, right: str) -> str:
    if op in {"+", "*"} and left > right:
        left, right = right, left
    return f"({left} {op} {right})"


def _expression_complexity(expression: str) -> int:
    operator_cost = sum(1 for char in expression if char in "+-*/")
    reciprocal_cost = expression.count("(1 /") * 2
    nested_inverse_cost = max(0, expression.count("(1 /") - 1) * 4
    return max(1, operator_cost + reciprocal_cost + nested_inverse_cost)


def _build_seed_expressions(stats: dict[str, float]) -> list[str]:
    mean_x = stats["mean_x"]
    scale_x = stats["scale_x"]
    shift_x = stats["shift_x"]
    center_shift = stats["center_shift"]
    mean_expr = _format_number(mean_x)
    scale_expr = _format_number(scale_x)
    shift_expr = _format_number(shift_x)
    center_shift_expr = _format_number(center_shift)

    centered = f"(temperature - {mean_expr})"
    scaled = f"({centered} / {scale_expr})"
    inverse = f"(1 / (temperature + {shift_expr}))"
    ratio = f"(temperature / (temperature + {shift_expr}))"
    centered_inverse = f"(1 / ({centered} + {center_shift_expr}))"
    centered_ratio = f"({centered} / ({centered} + {center_shift_expr}))"
    rational = f"((temperature * temperature) / (temperature + {shift_expr}))"
    centered_rational = f"(({centered} * {centered}) / (temperature + {shift_expr}))"

    return [
        "temperature",
        centered,
        scaled,
        "(temperature * temperature)",
        f"({centered} * {centered})",
        inverse,
        ratio,
        centered_inverse,
        centered_ratio,
        rational,
        centered_rational,
    ]


def _assess_term(expression: str, values: list[float], target: list[float]) -> dict[str, Any] | None:
    design = [[1.0, value] for value in values]
    coeffs = _fit_linear_model(design, target)
    if coeffs is None:
        return None
    predictions = [coeffs[0] + coeffs[1] * value for value in values]
    rmse, r2 = _score_predictions(target, predictions)
    return {
        "expression": expression,
        "values": values,
        "complexity": _expression_complexity(expression),
        "single_term_coefficients": coeffs,
        "single_term_rmse": rmse,
        "single_term_r2": r2,
        "term_score": rmse + 0.002 * _expression_complexity(expression),
    }


def _build_basis_library(
    temperatures: list[float],
    target: list[float],
    *,
    max_depth: int,
    basis_limit: int,
) -> list[dict[str, Any]]:
    mean_x = statistics.mean(temperatures)
    scale_x = statistics.pstdev(temperatures) or 1.0
    span_x = max(temperatures) - min(temperatures)
    if math.isclose(span_x, 0.0):
        span_x = max(abs(mean_x), 1.0)
    shift_x = max(abs(min(temperatures)), abs(max(temperatures)), 1.0) + 0.25 * span_x
    center_shift = abs(mean_x) + 0.25 * span_x + 1.0
    stats = {
        "mean_x": mean_x,
        "scale_x": scale_x,
        "shift_x": shift_x,
        "center_shift": center_shift,
    }

    seen_expressions: set[str] = set()
    seen_signatures: set[tuple[float, ...]] = set()

    def collect(expressions: list[str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for expression in expressions:
            if expression in seen_expressions:
                continue
            values = _evaluate_series(expression, temperatures)
            if values is None or _looks_constant(values):
                continue
            signature = _term_signature(values)
            if signature in seen_signatures:
                continue
            assessed = _assess_term(expression, values, target)
            if assessed is None:
                continue
            seen_expressions.add(expression)
            seen_signatures.add(signature)
            items.append(assessed)
        return items

    protected_terms = collect(_build_seed_expressions(stats))
    protected_terms.sort(key=lambda term: term["term_score"])
    generated_terms: list[dict[str, Any]] = []
    pool = protected_terms[:]

    frontier = pool[:]
    for _depth in range(2, max_depth + 1):
        candidates: list[dict[str, Any]] = []
        pair_pool = pool[:basis_limit]
        for left in frontier:
            for right in pair_pool:
                for op in OPS:
                    if op == "/" and right["expression"].strip().startswith("(1 /"):
                        continue
                    expression = _combine_expression(op, left["expression"], right["expression"])
                    if expression in seen_expressions:
                        continue
                    values = _evaluate_series(expression, temperatures)
                    if values is None or _looks_constant(values):
                        continue
                    signature = _term_signature(values)
                    if signature in seen_signatures:
                        continue
                    assessed = _assess_term(expression, values, target)
                    if assessed is None:
                        continue
                    seen_expressions.add(expression)
                    seen_signatures.add(signature)
                    candidates.append(assessed)
        candidates.sort(key=lambda term: term["term_score"])
        if not candidates:
            break
        frontier = candidates[:basis_limit]
        generated_terms.extend(frontier)
        generated_terms.sort(key=lambda term: term["term_score"])
        generated_terms = generated_terms[:basis_limit]
        pool = protected_terms + generated_terms
        pool.sort(key=lambda term: (term["term_score"], term["complexity"]))

    generated_terms.sort(key=lambda term: (term["term_score"], term["complexity"]))
    return (protected_terms + generated_terms)[:basis_limit]


def _format_formula(coeffs: list[float], terms: list[dict[str, Any]]) -> tuple[str, str, list[dict[str, Any]]]:
    rhs_parts = [_format_number(coeffs[0])]
    display_terms: list[dict[str, Any]] = []
    for index, (coef, term) in enumerate(zip(coeffs[1:], terms, strict=False), start=1):
        if abs(coef) < 1e-10:
            continue
        sign = "+" if coef >= 0 else "-"
        rhs_parts.append(f"{sign} {_format_number(abs(coef))} * ({term['expression']})")
        display_terms.append(
            {
                "coefficient": round(float(coef), 6),
                "expression": term["expression"],
            }
        )
    formula_expression = " ".join(rhs_parts) if rhs_parts else "0"
    equation = f"conversion = {formula_expression}"
    return equation, formula_expression, display_terms


def _infer_family(terms: list[dict[str, Any]]) -> str:
    expressions = [term["expression"] for term in terms]
    if any("/" in expression for expression in expressions):
        return "rational"
    if any("*" in expression for expression in expressions):
        return "polynomial"
    return "linear"


def build_symbolic_regression_layer(
    rows: list[dict],
    *,
    requested_model_id: str | None = None,
    config: dict[str, Any] | None = None,
    physics_profile: dict[str, Any] | None = None,
) -> dict:
    config = dict(config or {})
    temperatures = [float(row["temperature"]) for row in rows]
    target = [float(row["conversion"]) for row in rows]

    max_depth = max(1, int(config.get("symbolic_max_depth", 2)))
    basis_limit = max(6, int(config.get("symbolic_basis_limit", 16)))
    max_terms = max(1, int(config.get("symbolic_max_terms", 3)))
    candidate_limit = max(3, int(config.get("symbolic_candidate_limit", 6)))

    basis_library = _build_basis_library(
        temperatures,
        target,
        max_depth=max_depth,
        basis_limit=basis_limit,
    )
    selected_basis = basis_library[:basis_limit]

    candidate_models: list[dict[str, Any]] = []
    model_index = 1
    for term_count in range(1, min(max_terms, len(selected_basis)) + 1):
        for combo in itertools.combinations(selected_basis, term_count):
            design = [[1.0] + [term["values"][row_index] for term in combo] for row_index in range(len(rows))]
            coeffs = _fit_linear_model(design, target)
            if coeffs is None:
                continue

            raw_predictions = [sum(coef * value for coef, value in zip(coeffs, design_row)) for design_row in design]
            predictions = [_clamp01(value) for value in raw_predictions]
            rmse, r2 = _score_predictions(target, predictions)
            residuals = [round(y - p, 5) for y, p in zip(target, predictions)]
            equation, formula_expression, formula_terms = _format_formula(coeffs, list(combo))
            complexity = sum(term["complexity"] for term in combo) + len(combo)
            family = _infer_family(list(combo))
            effective_params = len(coeffs) + 0.55 * complexity
            bic = _calc_bic(rmse, len(rows), effective_params)
            search_score = rmse + 0.002 * complexity

            parameter_values = {"intercept": round(float(coeffs[0]), 6)}
            for index, coef in enumerate(coeffs[1:], start=1):
                parameter_values[f"coef_{index}"] = round(float(coef), 6)

            candidate_models.append(
                {
                    "key": f"symbolic_{model_index:02d}",
                    "name": f"符号回归#{model_index}",
                    "kind": "symbolic",
                    "family": family,
                    "equation": equation,
                    "formula_expression": formula_expression,
                    "formula_terms": formula_terms,
                    "parameters": parameter_values,
                    "parameter_labels": {"intercept": "截距"},
                    "physics": "基于 + - * / 的显式符号回归公式，保留代数结构并可直接审查。",
                    "basis_terms": [term["expression"] for term in combo],
                    "complexity": complexity,
                    "search_score": round(search_score, 6),
                    "effective_params": round(effective_params, 3),
                    "predictions": [round(value, 5) for value in predictions],
                    "raw_predictions": [round(value, 5) for value in raw_predictions],
                    "residuals": residuals,
                    "r2": round(r2, 5),
                    "rmse": round(rmse, 5),
                    "bic": round(bic, 4),
                    "requested": bool(requested_model_id and requested_model_id == f"symbolic_{model_index:02d}"),
                    "symbolic": {
                        "operator_set": list(OPS),
                        "max_depth": max_depth,
                        "basis_library_size": len(basis_library),
                        "selected_basis_size": len(selected_basis),
                    },
                }
            )
            model_index += 1

    candidate_models.sort(
        key=lambda model: (
            model["search_score"],
            model["bic"],
            model["complexity"],
            model["rmse"],
        )
    )

    for index, model in enumerate(candidate_models, start=1):
        model["key"] = f"symbolic_{index:02d}"
        model["name"] = f"符号回归#{index}"
        model["search_rank"] = index

    candidates = candidate_models[:candidate_limit]
    for candidate in candidates:
        candidate["requested"] = bool(requested_model_id and candidate["key"] == requested_model_id)

    search_space = {
        "operator_set": list(OPS),
        "max_depth": max_depth,
        "max_terms": max_terms,
        "basis_limit": basis_limit,
        "basis_library_size": len(basis_library),
        "candidate_limit": candidate_limit,
        "selected_basis": [term["expression"] for term in selected_basis[: min(8, len(selected_basis))]],
    }
    if physics_profile:
        search_space["physics_score"] = physics_profile.get("summary", {}).get("score")

    return {
        "status": "local_formula_search",
        "api_ready": True,
        "description": "LLM 负责理解任务、约束搜索空间和解释结果，符号回归层在 + - * / 表达式池中做稀疏拟合，输出可直接验算的显式公式。",
        "search_config": {
            "requested_model_id": requested_model_id,
            "max_depth": max_depth,
            "basis_limit": basis_limit,
            "max_terms": max_terms,
            "candidate_limit": candidate_limit,
        },
        "search_space": search_space,
        "best_formula": candidates[0] if candidates else None,
        "candidates": candidates,
    }
