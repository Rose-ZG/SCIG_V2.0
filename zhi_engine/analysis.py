from __future__ import annotations

import csv
import io
import math
import statistics
from datetime import datetime
from typing import Any

from zhi_engine.symbolic import build_symbolic_regression_layer, evaluate_symbolic_formula


MODEL_ORDER = ("power", "arrhenius", "langmuir_hinshelwood")
MODEL_NAMES = {
    "power": "幂律动力学",
    "arrhenius": "Arrhenius 温度依赖",
    "langmuir_hinshelwood": "Langmuir-Hinshelwood",
}

PHYSICS_RULESET = [
    {
        "key": "range",
        "title": "边界约束",
        "detail": "温度必须高于绝对零度，转化率应落在 [0, 1] 区间内。",
    },
    {
        "key": "trend",
        "title": "单调约束",
        "detail": "按温度排序后，观测曲线应总体非递减，允许少量噪声回落。",
    },
    {
        "key": "projection",
        "title": "可行域投影",
        "detail": "用单调投影恢复最可能的物理可行状态，并量化修正幅度。",
    },
    {
        "key": "parameter",
        "title": "参数可行性",
        "detail": "候选模型参数必须满足对应机理的正性、饱和性或上界约束。",
    },
]

CORE_TECHNOLOGIES = [
    {
        "key": "physics_constraint",
        "title": "物理约束校验层",
        "detail": "检查边界、单调性、平滑性和参数可行性，把噪声观测投影到物理可行域。",
    },
    {
        "key": "symbolic_regression",
        "title": "LLM + 符号回归层",
        "detail": "由对话意图和机理先验生成候选方程，并保留后续接入大模型 API 的适配入口。",
    },
    {
        "key": "hypothesis_ranking",
        "title": "假设性排行层",
        "detail": "综合拟合误差、BIC、约束惩罚和可解释性，对机理假设进行排序与推荐。",
    },
]

APPLICATION_SCENARIOS = [
    {
        "title": "化工工艺自查",
        "detail": "用少量在线监测点反推真实趋势，识别漂移、噪声和设备校准误差。",
    },
    {
        "title": "材料与界面改性",
        "detail": "对吸附、润湿、涂层耐久等过程做边界检查，快速判断数据是否越过物理可行域。",
    },
    {
        "title": "生物与医药工艺",
        "detail": "对发酵、纯化、释放动力学做逆向求解，并生成可下载的分析报告。",
    },
]

WORKFLOW_TEMPLATE = [
    {
        "key": "understand",
        "title": "理解任务",
        "detail": "识别用户意图、目标模型和数据结构，确定是否进入约束优化模式。",
    },
    {
        "key": "normalize",
        "title": "数据归一",
        "detail": "把 JSON、CSV 或表格数据统一成可分析行，并识别温度、转化率和批次信息。",
    },
    {
        "key": "constraint_optimize",
        "title": "物理约束校验",
        "detail": "检查边界、单调性和平滑性，用单调投影恢复最可能的物理可行曲线。",
    },
    {
        "key": "rank",
        "title": "假设性排行",
        "detail": "对候选模型做拟合、约束惩罚和综合评分，选出最可信的机理假设。",
    },
    {
        "key": "report",
        "title": "生成报告",
        "detail": "把结论整理进对话视图和 DOCX 报告，支持后续下载与留档。",
    },
]


def _graph_positions() -> dict[str, tuple[int, int]]:
    return {
        "core": (50, 48),
        "knowledge": (20, 24),
        "physics": (78, 24),
        "projection": (50, 76),
        "diagnosis": (20, 74),
        "advice": (80, 74),
        "model": (50, 74),
        "tag_batch": (50, 18),
        "tag_anomaly": (10, 48),
        "tag_cross": (90, 48),
    }


def demo_dataset() -> list[dict]:
    temps = [40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150]
    conversion = [0.16, 0.205, 0.251, 0.305, 0.356, 0.411, 0.465, 0.517, 0.566, 0.611, 0.655, 0.694]
    batches = ["A01", "A01", "A01", "A02", "A02", "A02", "A03", "A03", "A03", "A04", "A04", "A04"]
    return [{"temperature": t, "conversion": y, "batch": b} for t, y, b in zip(temps, conversion, batches)]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _extract_first(mapping: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


def _row_to_mapping(row: object, feature_names: list[str] | None = None) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    if isinstance(row, (list, tuple)):
        if feature_names:
            mapping: dict[str, Any] = {}
            for index, name in enumerate(feature_names):
                if index < len(row):
                    mapping[str(name)] = row[index]
            if len(row) > len(feature_names):
                mapping["features"] = list(row[len(feature_names) :])
            return mapping
        mapping = {}
        if len(row) > 0:
            mapping["temperature"] = row[0]
        if len(row) > 1:
            mapping["conversion"] = row[1]
        if len(row) > 2:
            mapping["batch"] = row[2]
        if len(row) > 3:
            mapping["features"] = list(row[3:])
        return mapping
    raise ValueError("dataset rows must be mapping objects or sequences")


def _as_float(row: dict[str, Any], keys: list[str]) -> float:
    value = _extract_first(row, keys)
    if value is None:
        raise ValueError(f"missing keys: {', '.join(keys)}")
    return float(value)


def _clean_dataset(dataset: object, feature_names: list[str] | None = None) -> list[dict]:
    if isinstance(dataset, str):
        rows = list(csv.DictReader(io.StringIO(dataset)))
    elif isinstance(dataset, list):
        rows = dataset
    else:
        raise ValueError("dataset must be a list of rows or CSV text")

    cleaned: list[dict] = []
    for index, row in enumerate(rows):
        try:
            mapping = _row_to_mapping(row, feature_names=feature_names)
            temp = _as_float(mapping, ["temperature", "temp", "t", "x", "温度"])
            value = _as_float(mapping, ["conversion", "response", "yield", "y", "转化率"])
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            raise ValueError(f"第 {index + 1} 行缺少有效的 temperature / conversion") from exc

        batch = _extract_first(mapping, ["batch", "批次"], "未标注")
        cleaned_row = {
            "temperature": temp,
            "conversion": value,
            "batch": str(batch),
            "source_index": index,
        }
        extra = {
            key: value
            for key, value in mapping.items()
            if key
            not in {
                "temperature",
                "temp",
                "t",
                "x",
                "温度",
                "conversion",
                "response",
                "yield",
                "y",
                "转化率",
                "batch",
                "批次",
            }
        }
        if extra:
            cleaned_row["features"] = extra
        cleaned.append(cleaned_row)

    if len(cleaned) < 4:
        raise ValueError("至少需要 4 行数据")
    cleaned.sort(key=lambda item: (item["temperature"], item["source_index"]))
    return cleaned


def _normalize_request(dataset: object) -> tuple[list[dict], str | None, dict[str, Any]]:
    requested_model_id: str | None = None
    config: dict[str, Any] = {}
    feature_names: list[str] | None = None
    rows_source: object = dataset

    if isinstance(dataset, dict):
        requested_model_id = str(
            _extract_first(dataset, ["model_id", "preferred_model", "target_model"], "")
        ).strip() or None
        raw_config = dataset.get("config")
        if isinstance(raw_config, dict):
            config = dict(raw_config)
        raw_feature_names = config.get("feature_names") or dataset.get("feature_names")
        if isinstance(raw_feature_names, list) and raw_feature_names:
            feature_names = [str(item) for item in raw_feature_names]

        if "data" in dataset:
            rows_source = dataset["data"]
        elif "rows" in dataset:
            rows_source = dataset["rows"]
        elif "dataset" in dataset:
            rows_source = dataset["dataset"]
        elif any(key in dataset for key in ("temperature", "conversion", "temp", "x", "y")):
            rows_source = [dataset]

    rows = _clean_dataset(rows_source, feature_names=feature_names)
    return rows, requested_model_id, config


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    x_bar, y_bar = statistics.mean(xs), statistics.mean(ys)
    denom = sum((x - x_bar) ** 2 for x in xs) or 1e-9
    slope = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys)) / denom
    intercept = y_bar - slope * x_bar
    predictions = [slope * x + intercept for x in xs]
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, predictions))
    ss_tot = sum((y - y_bar) ** 2 for y in ys) or 1e-9
    r2 = max(0.0, 1 - ss_res / ss_tot)
    return slope, intercept, r2


def _score_predictions(ys: list[float], predictions: list[float]) -> tuple[float, float]:
    residuals = [y - p for y, p in zip(ys, predictions)]
    rmse = math.sqrt(sum(r * r for r in residuals) / len(residuals))
    y_bar = statistics.mean(ys)
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, predictions))
    ss_tot = sum((y - y_bar) ** 2 for y in ys) or 1e-9
    r2 = max(0.0, 1 - ss_res / ss_tot)
    return rmse, r2


def _fit_power(rows: list[dict]) -> dict:
    xs = [row["temperature"] for row in rows]
    ys = [max(row["conversion"], 1e-4) for row in rows]
    lx = [math.log(max(x, 1e-4)) for x in xs]
    ly = [math.log(y) for y in ys]
    slope, intercept, _ = _linear_fit(lx, ly)
    coefficient = math.exp(intercept)
    predictions = [_clamp01(coefficient * max(x, 1e-4) ** slope) for x in xs]
    rmse, r2 = _score_predictions([row["conversion"] for row in rows], predictions)
    equation = f"conversion = {coefficient:.4f} * temperature^{slope:.3f}"
    return {
        "key": "power",
        "name": MODEL_NAMES["power"],
        "equation": equation,
        "parameters": {"coefficient": round(coefficient, 6), "exponent": round(slope, 6)},
        "parameter_labels": {"coefficient": "比例因子", "exponent": "温度指数"},
        "physics": "适合单调增长趋势，参数少，便于做基线比较和物理可行性检查。",
        "predictions": [round(value, 5) for value in predictions],
        "residuals": [round(row["conversion"] - pred, 5) for row, pred in zip(rows, predictions)],
        "r2": round(r2, 5),
        "rmse": round(rmse, 5),
        "bic": round(len(rows) * math.log(max(rmse * rmse, 1e-8)) + 2 * math.log(len(rows)), 4),
    }


def _fit_arrhenius(rows: list[dict]) -> dict:
    xs = [row["temperature"] for row in rows]
    ys = [max(1 - row["conversion"], 1e-4) for row in rows]
    lx = [1 / (x + 273.15) for x in xs]
    ly = [math.log(y) for y in ys]
    slope, intercept, _ = _linear_fit(lx, ly)
    predictions = [_clamp01(1 - math.exp(intercept + slope / (x + 273.15))) for x in xs]
    rmse, r2 = _score_predictions([row["conversion"] for row in rows], predictions)
    activation = max(0.0, -slope * 8.314 / 1000)
    equation = f"conversion = 1 - exp({intercept:.3f} + {slope:.1f} / T)"
    return {
        "key": "arrhenius",
        "name": MODEL_NAMES["arrhenius"],
        "equation": equation,
        "parameters": {"activation_energy_kj_mol": round(activation, 6), "prefactor_log": round(intercept, 6)},
        "parameter_labels": {"activation_energy_kj_mol": "表观活化能 (kJ/mol)", "prefactor_log": "前因子对数"},
        "physics": "用温度倒数刻画热激活过程，适合评估温度敏感性和参数可行性。",
        "predictions": [round(value, 5) for value in predictions],
        "residuals": [round(row["conversion"] - pred, 5) for row, pred in zip(rows, predictions)],
        "r2": round(r2, 5),
        "rmse": round(rmse, 5),
        "bic": round(len(rows) * math.log(max(rmse * rmse, 1e-8)) + 2 * math.log(len(rows)), 4),
    }


def _fit_langmuir_hinshelwood(rows: list[dict]) -> dict:
    xs = [max(row["temperature"], 1e-4) for row in rows]
    ys = [max(row["conversion"], 1e-4) for row in rows]
    lx = [1 / x for x in xs]
    ly = [1 / y for y in ys]
    slope, intercept, _ = _linear_fit(lx, ly)
    intercept = max(intercept, 1e-6)
    capacity = 1 / intercept
    affinity = max(slope * capacity, 1e-6)
    predictions = [_clamp01((capacity * x) / (affinity + x)) for x in xs]
    rmse, r2 = _score_predictions([row["conversion"] for row in rows], predictions)
    equation = f"conversion = ({capacity:.3f} * temperature) / ({affinity:.3f} + temperature)"
    return {
        "key": "langmuir_hinshelwood",
        "name": MODEL_NAMES["langmuir_hinshelwood"],
        "equation": equation,
        "parameters": {"capacity": round(capacity, 6), "affinity": round(affinity, 6)},
        "parameter_labels": {"capacity": "饱和容量", "affinity": "亲和常数"},
        "physics": "通过饱和项限制上界，更像界面吸附或限域转化过程的表达。",
        "predictions": [round(value, 5) for value in predictions],
        "residuals": [round(row["conversion"] - pred, 5) for row, pred in zip(rows, predictions)],
        "r2": round(r2, 5),
        "rmse": round(rmse, 5),
        "bic": round(len(rows) * math.log(max(rmse * rmse, 1e-8)) + 2 * math.log(len(rows)), 4),
    }


def _fit_model(key: str, rows: list[dict]) -> dict:
    if key == "power":
        return _fit_power(rows)
    if key == "arrhenius":
        return _fit_arrhenius(rows)
    if key == "langmuir_hinshelwood":
        return _fit_langmuir_hinshelwood(rows)
    raise KeyError(key)


def _predict_value(model: dict, temperature: float) -> float:
    key = model["key"]
    params = model["parameters"]
    if key == "power":
        coefficient = float(params["coefficient"])
        exponent = float(params["exponent"])
        return _clamp01(coefficient * max(temperature, 1e-4) ** exponent)
    if key == "arrhenius":
        activation = float(params["activation_energy_kj_mol"])
        prefactor = float(params["prefactor_log"])
        slope = -activation * 1000 / 8.314
        return _clamp01(1 - math.exp(prefactor + slope / (temperature + 273.15)))
    if key == "langmuir_hinshelwood":
        capacity = float(params["capacity"])
        affinity = float(params["affinity"])
        temperature = max(temperature, 1e-4)
        return _clamp01((capacity * temperature) / (affinity + temperature))
    if key.startswith("symbolic_"):
        expression = str(model.get("formula_expression") or "").strip()
        if not expression:
            return 0.0
        try:
            return _clamp01(evaluate_symbolic_formula(expression, temperature))
        except Exception:
            return 0.0
    return 0.0


def _isotonic_projection(values: list[float]) -> list[float]:
    if not values:
        return []
    blocks = [[value, 1] for value in values]
    index = 0
    while index < len(blocks) - 1:
        left_mean = blocks[index][0] / blocks[index][1]
        right_mean = blocks[index + 1][0] / blocks[index + 1][1]
        if left_mean <= right_mean + 1e-12:
            index += 1
            continue
        merged_total = blocks[index][0] + blocks[index + 1][0]
        merged_count = blocks[index][1] + blocks[index + 1][1]
        blocks[index : index + 2] = [[merged_total, merged_count]]
        if index:
            index -= 1
    projected: list[float] = []
    for total, count in blocks:
        projected.extend([_clamp01(total / count)] * count)
    return projected


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _build_physics_profile(rows: list[dict]) -> dict:
    temperatures = [row["temperature"] for row in rows]
    conversions = [row["conversion"] for row in rows]
    clipped = [_clamp01(value) for value in conversions]
    projected = _isotonic_projection(clipped)
    deltas = [projected_value - observed for projected_value, observed in zip(projected, conversions)]

    tolerance = 0.015
    raw_drops = [
        index
        for index in range(1, len(conversions))
        if conversions[index] + tolerance < conversions[index - 1]
    ]
    allowed_noise_drops = 1 if len(conversions) >= 6 else 0
    trend_violation_count = max(0, len(raw_drops) - allowed_noise_drops)

    boundary_violations = [
        {
            "index": index,
            "temperature": row["temperature"],
            "conversion": row["conversion"],
            "message": "转化率越界，应落在 [0, 1] 之内。",
        }
        for index, row in enumerate(rows)
        if row["conversion"] < -0.02 or row["conversion"] > 1.02
    ]

    if len(conversions) >= 3:
        first_diff = [conversions[index] - conversions[index - 1] for index in range(1, len(conversions))]
        second_diff = [first_diff[index] - first_diff[index - 1] for index in range(1, len(first_diff))]
        roughness = _mean([abs(value) for value in second_diff]) / max(max(conversions) - min(conversions), 1e-6)
    else:
        roughness = 0.0

    projection_gap_mean = _mean([abs(delta) for delta in deltas])
    projection_gap_max = max((abs(delta) for delta in deltas), default=0.0)
    out_of_bounds_penalty = min(40.0, len(boundary_violations) * 18.0)
    trend_penalty = min(28.0, trend_violation_count * 9.0)
    roughness_penalty = min(18.0, roughness * 50.0)
    projection_penalty = min(24.0, projection_gap_mean * 80.0)
    penalty = round(out_of_bounds_penalty + trend_penalty + roughness_penalty + projection_penalty, 4)
    score = max(0.0, 100.0 - penalty)

    reverse_solution = [
        {
            "temperature": row["temperature"],
            "observed": round(row["conversion"], 5),
            "projected": round(projected_value, 5),
            "delta": round(delta, 5),
        }
        for row, projected_value, delta in zip(rows, projected, deltas)
    ]

    return {
        "rules": PHYSICS_RULESET,
        "summary": {
            "sample_count": len(rows),
            "temperature_span": [min(temperatures), max(temperatures)],
            "conversion_span": [min(conversions), max(conversions)],
            "boundary_violations": len(boundary_violations),
            "trend_drop_count": len(raw_drops),
            "trend_violation_count": trend_violation_count,
            "roughness": round(roughness, 5),
            "projection_gap_mean": round(projection_gap_mean, 5),
            "projection_gap_max": round(projection_gap_max, 5),
            "feasible": penalty < 35.0,
            "score": round(score, 2),
        },
        "boundary_violations": boundary_violations,
        "trend_drops": raw_drops,
        "reverse_solution": reverse_solution,
        "projection": {
            "observed": [round(value, 5) for value in conversions],
            "projected": [round(value, 5) for value in projected],
            "delta": [round(value, 5) for value in deltas],
        },
        "penalty": penalty,
        "score": round(score, 2),
    }


def _model_constraint_checks(model: dict, rows: list[dict], physics_profile: dict) -> dict:
    predictions = [float(value) for value in model["predictions"]]
    residuals = [float(value) for value in model["residuals"]]
    temperatures = [row["temperature"] for row in rows]
    projected_curve = physics_profile["projection"]["projected"]
    trend_tolerance = 0.01

    nonmonotonic_steps = [
        index
        for index in range(1, len(predictions))
        if predictions[index] + trend_tolerance < predictions[index - 1]
    ]
    prediction_gap_mean = _mean([abs(pred - proj) for pred, proj in zip(predictions, projected_curve)])
    prediction_gap_max = max((abs(pred - proj) for pred, proj in zip(predictions, projected_curve)), default=0.0)
    residual_bias = _mean(residuals)

    violations: list[dict] = []
    penalty = 0.0
    critical = False

    def add_violation(
        key: str,
        title: str,
        severity: str,
        detail: str,
        weight: float,
        suggestion: str,
    ) -> None:
        nonlocal penalty, critical
        violations.append(
            {
                "key": key,
                "title": title,
                "severity": severity,
                "detail": detail,
                "penalty": round(weight, 2),
                "suggestion": suggestion,
            }
        )
        penalty += weight
        if severity == "critical":
            critical = True

    if model["key"] == "power":
        coefficient = float(model["parameters"]["coefficient"])
        exponent = float(model["parameters"]["exponent"])
        if coefficient <= 0:
            add_violation(
                "coefficient_positive",
                "比例因子必须为正",
                "critical",
                f"coefficient = {coefficient:.6f}",
                28.0,
                "检查归一化方式或初始观测是否把基线拉到了负区间。",
            )
        if exponent < 0:
            add_violation(
                "exponent_nonnegative",
                "温度指数应为非负",
                "high",
                f"exponent = {exponent:.6f}",
                14.0,
                "当前数据更像随温度上升而增强的过程，建议优先保留单调上升假设。",
            )
    elif model["key"] == "arrhenius":
        activation = float(model["parameters"]["activation_energy_kj_mol"])
        prefactor = float(model["parameters"]["prefactor_log"])
        if activation <= 0:
            add_violation(
                "activation_positive",
                "活化能应为正",
                "critical",
                f"activation_energy_kj_mol = {activation:.6f}",
                28.0,
                "说明当前拟合不支持热激活型机理，建议回看数据单位和温标。",
            )
        if not math.isfinite(prefactor):
            add_violation(
                "prefactor_finite",
                "前因子必须有限",
                "critical",
                "prefactor_log is not finite",
                24.0,
                "检查输入数据是否存在空值、极端值或对数变换错误。",
            )
    elif model["key"] == "langmuir_hinshelwood":
        capacity = float(model["parameters"]["capacity"])
        affinity = float(model["parameters"]["affinity"])
        if capacity <= 0:
            add_violation(
                "capacity_positive",
                "饱和容量必须为正",
                "critical",
                f"capacity = {capacity:.6f}",
                26.0,
                "检查饱和上限是否被噪声压成了负值或零值。",
            )
        if capacity > 1.05:
            add_violation(
                "capacity_upper_bound",
                "饱和上限不应明显超过 1",
                "high",
                f"capacity = {capacity:.6f}",
                18.0,
                "对于转化率建模，过高上限通常意味着归一化或单位有问题。",
            )
        if affinity <= 0:
            add_violation(
                "affinity_positive",
                "亲和常数必须为正",
                "critical",
                f"affinity = {affinity:.6f}",
                26.0,
                "检查分母项是否因噪声拟合成了不合理的负值。",
            )

    if nonmonotonic_steps:
        add_violation(
            "monotonic_prediction",
            "模型预测出现局部回落",
            "medium",
            f"nonmonotonic steps = {nonmonotonic_steps}",
            min(12.0, len(nonmonotonic_steps) * 4.0),
            "如果过程应当随温度单调增强，建议增加约束权重或改用更强的先验。",
        )

    if prediction_gap_mean > 0.06:
        add_violation(
            "projection_gap",
            "模型与可行域投影偏差偏大",
            "medium",
            f"mean gap = {prediction_gap_mean:.5f}",
            min(14.0, prediction_gap_mean * 120.0),
            "说明当前候选模型偏离了物理可行曲线，建议提高约束惩罚权重。",
        )

    if residual_bias < -0.04 or residual_bias > 0.04:
        add_violation(
            "residual_bias",
            "残差存在系统偏差",
            "low",
            f"residual bias = {residual_bias:.5f}",
            4.0,
            "可以再检查一次基线、标定和温度单位。",
        )

    feasible = not critical and penalty < 45.0
    constraint_score = max(0.0, 100.0 - penalty)

    return {
        "feasible": feasible,
        "score": round(constraint_score, 2),
        "penalty": round(penalty, 4),
        "projection_gap_mean": round(prediction_gap_mean, 5),
        "projection_gap_max": round(prediction_gap_max, 5),
        "nonmonotonic_steps": nonmonotonic_steps,
        "violations": violations,
        "summary": {
            "temperature_span": [min(temperatures), max(temperatures)],
            "mean_residual_bias": round(residual_bias, 5),
        },
    }


def _min_max_normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if math.isclose(lo, hi):
        return [0.0 for _ in values]
    span = hi - lo
    return [(value - lo) / span for value in values]


def _rank_models(models: list[dict], requested_model_id: str | None, physics_weight: float) -> list[dict]:
    bic_scores = _min_max_normalize([model["bic"] for model in models])
    penalty_scores = _min_max_normalize([model["constraint"]["penalty"] for model in models])
    projection_scores = _min_max_normalize([model["constraint"]["projection_gap_mean"] for model in models])

    for index, model in enumerate(models):
        fit_score = bic_scores[index]
        constraint_score = penalty_scores[index]
        projection_score = projection_scores[index]
        combined_score = fit_score + physics_weight * (0.7 * constraint_score + 0.3 * projection_score)
        if requested_model_id and model["key"] == requested_model_id:
            combined_score *= 0.98
            model["requested"] = True
        else:
            model["requested"] = False
        model["fit_score"] = round(fit_score, 6)
        model["constraint_score"] = round(model["constraint"]["score"], 2)
        model["combined_score"] = round(combined_score, 6)

    models.sort(
        key=lambda model: (
            model["combined_score"],
            model["bic"],
            model["constraint"]["penalty"],
            model["rmse"],
        )
    )

    for rank, model in enumerate(models, start=1):
        model["rank"] = rank
        model["constraint"]["rank"] = rank
    return models


def _build_curve(best_model: dict, min_temp: float, max_temp: float) -> list[dict]:
    if math.isclose(min_temp, max_temp):
        temps = [min_temp for _ in range(25)]
    else:
        temps = [round(min_temp + (max_temp - min_temp) * i / 24, 2) for i in range(25)]
    return [{"temperature": temp, "conversion": round(_predict_value(best_model, temp), 5)} for temp in temps]


def _build_anomalies(best_model: dict, rows: list[dict]) -> list[dict]:
    residuals = best_model["residuals"]
    sigma = statistics.pstdev(residuals) or 0.001
    anomalies = []
    for index, (row, residual) in enumerate(zip(rows, residuals)):
        z = abs(residual) / sigma
        if z >= 1.35:
            anomalies.append(
                {
                    "index": index,
                    "temperature": row["temperature"],
                    "conversion": row["conversion"],
                    "batch": row["batch"],
                    "residual": residual,
                    "severity": "high" if z >= 2 else "medium",
                    "reason": "偏离推荐模型趋势，建议优先核查实验记录、批次信息和设备校准。",
                }
            )
    return anomalies


def _build_knowledge_graph(best: dict, anomalies: list[dict], rows: list[dict], physics_profile: dict) -> dict:
    min_temp = min(row["temperature"] for row in rows)
    max_temp = max(row["temperature"] for row in rows)
    positions = _graph_positions()
    graph_nodes = [
        {"id": "core", "label": "知构引擎", "kind": "core", "x": positions["core"][0], "y": positions["core"][1]},
        {"id": "knowledge", "label": "知识图谱", "kind": "component", "x": positions["knowledge"][0], "y": positions["knowledge"][1]},
        {"id": "physics", "label": "物理约束", "kind": "component", "x": positions["physics"][0], "y": positions["physics"][1]},
        {"id": "projection", "label": "可行域投影", "kind": "component", "x": positions["projection"][0], "y": positions["projection"][1]},
        {"id": "diagnosis", "label": "异常诊断", "kind": "component", "x": positions["diagnosis"][0], "y": positions["diagnosis"][1]},
        {"id": "advice", "label": "实验建议", "kind": "component", "x": positions["advice"][0], "y": positions["advice"][1]},
        {"id": "model", "label": best["name"], "kind": "result", "x": positions["model"][0], "y": positions["model"][1]},
        {"id": "tag_batch", "label": "批次追踪", "kind": "signal", "x": positions["tag_batch"][0], "y": positions["tag_batch"][1]},
        {"id": "tag_anomaly", "label": "异常点", "kind": "signal", "x": positions["tag_anomaly"][0], "y": positions["tag_anomaly"][1]},
        {"id": "tag_cross", "label": "跨域建议", "kind": "signal", "x": positions["tag_cross"][0], "y": positions["tag_cross"][1]},
    ]
    graph_edges = [
        {"from": "core", "to": "knowledge"},
        {"from": "core", "to": "physics"},
        {"from": "physics", "to": "projection"},
        {"from": "projection", "to": "model"},
        {"from": "core", "to": "diagnosis"},
        {"from": "core", "to": "advice"},
        {"from": "knowledge", "to": "model"},
        {"from": "diagnosis", "to": "tag_anomaly"},
        {"from": "knowledge", "to": "tag_batch"},
        {"from": "advice", "to": "tag_cross"},
    ]
    matched_tags = ["物理约束", "可行域投影", "逆向求解"]
    if best["key"] == "arrhenius":
        matched_tags.insert(0, "温度敏感")
        cross_domain = "可借鉴热激活过程中的参数回推和约束优化思想。"
    elif best["key"] == "langmuir_hinshelwood":
        matched_tags.insert(0, "饱和上限")
        cross_domain = "可借鉴界面吸附与饱和曲线分析的表达方式。"
    else:
        matched_tags.insert(0, "经验速率")
        cross_domain = "可借鉴经验动力学中的参数筛选和趋势校验方法。"
    if anomalies:
        matched_tags.append("异常诊断")
    if len(rows) >= 8:
        matched_tags.append("批次追踪")
    if physics_profile["summary"]["boundary_violations"] or physics_profile["summary"]["trend_violation_count"]:
        matched_tags.append("约束修正")
    return {
        "nodes": graph_nodes,
        "edges": graph_edges,
        "matched_tags": matched_tags,
        "cross_domain": cross_domain,
        "range": [min_temp, max_temp],
        "physics_summary": physics_profile["summary"],
    }


def _build_suggestions(best: dict, anomalies: list[dict], rows: list[dict], physics_profile: dict) -> list[str]:
    min_temp = min(row["temperature"] for row in rows)
    max_temp = max(row["temperature"] for row in rows)
    mid_temp = round((min_temp + max_temp) / 2, 1)
    suggestions = [
        f"在 {mid_temp}°C 附近增加 2-3 个重复点，确认是否仍满足单调上升和可行域边界。",
        "优先复核异常点对应的批次、操作员、设备校准和原始记录。",
        "如果要验证新机理，建议围绕高敏感区间做最小判别实验，而不是盲目扩充样本量。",
    ]
    if anomalies:
        suggestions.insert(0, f"当前共有 {len(anomalies)} 个异常点，建议先按严重度处理，再决定是否复测。")
    if physics_profile["summary"]["boundary_violations"]:
        suggestions.append("转化率越界，说明数据归一化或抄录环节可能存在问题。")
    if physics_profile["summary"]["trend_violation_count"]:
        suggestions.append("观察到噪声回落，建议提高约束权重或做单调投影后再做机理判断。")
    if best["key"] == "langmuir_hinshelwood":
        suggestions.append("可在低温和高覆盖区分别取样，验证饱和项是否确实主导反应。")
    return suggestions


def analyze_dataset(dataset: object) -> dict:
    rows, requested_model_id, config = _normalize_request(dataset)
    physics_lambda = float(config.get("lambda", 10.0))
    physics_weight = max(0.2, min(5.0, physics_lambda / 10.0))
    solver = str(config.get("solver", "L-BFGS-B"))
    max_iter = int(config.get("max_iter", 5000))

    physics_profile = _build_physics_profile(rows)
    symbolic_regression_layer = build_symbolic_regression_layer(
        rows,
        requested_model_id=requested_model_id,
        config=config,
        physics_profile=physics_profile,
    )
    symbolic_models = list(symbolic_regression_layer.get("candidates") or [])
    models = [_fit_model(key, rows) for key in MODEL_ORDER] + symbolic_models

    for model in models:
        model["constraint"] = _model_constraint_checks(model, rows, physics_profile)

    ranked_models = _rank_models(models, requested_model_id, physics_weight)
    best = ranked_models[0]
    anomalies = _build_anomalies(best, rows)
    min_temp = min(row["temperature"] for row in rows)
    max_temp = max(row["temperature"] for row in rows)
    curve = _build_curve(best, min_temp, max_temp)
    knowledge_graph = _build_knowledge_graph(best, anomalies, rows, physics_profile)
    suggestions = _build_suggestions(best, anomalies, rows, physics_profile)

    fit_confidence = max(0.0, min(0.99, 0.54 + best["r2"] * 0.34 - best["rmse"] * 0.12))
    physics_confidence = best["constraint"]["score"] / 100.0
    confidence = round(0.6 * fit_confidence + 0.4 * physics_confidence, 2)

    physics_constraints = {
        "rules": PHYSICS_RULESET,
        "request": {
            "requested_model_id": requested_model_id,
            "lambda": physics_lambda,
            "solver": solver,
            "max_iter": max_iter,
        },
        "data_profile": physics_profile,
        "rankings": [
            {
                "rank": model["rank"],
                "key": model["key"],
                "name": model["name"],
                "combined_score": model["combined_score"],
                "fit_score": model["fit_score"],
                "constraint_score": model["constraint"]["score"],
                "constraint_penalty": model["constraint"]["penalty"],
                "feasible": model["constraint"]["feasible"],
                "violations": model["constraint"]["violations"],
            }
            for model in ranked_models
        ],
        "projection": physics_profile["projection"],
        "reverse_solution": physics_profile["reverse_solution"],
    }
    symbolic_regression_layer["candidates"] = [
        {
            "key": model["key"],
            "name": model["name"],
            "kind": model.get("kind", "mechanism_template"),
            "family": model.get("family"),
            "equation": model["equation"],
            "parameters": model["parameters"],
            "physics": model["physics"],
            "r2": model["r2"],
            "rmse": model["rmse"],
            "bic": model["bic"],
            "complexity": model.get("complexity"),
            "constraint_score": model["constraint"]["score"],
            "feasible": model["constraint"]["feasible"],
            "rank": model["rank"],
        }
        for model in ranked_models
    ]
    symbolic_regression_layer["best_formula"] = next(
        (model for model in ranked_models if model.get("kind") == "symbolic"),
        symbolic_regression_layer.get("best_formula"),
    )
    hypothesis_ranking = {
        "strategy": "fit_error + BIC + physics_constraint_penalty",
        "physics_weight": physics_weight,
        "items": [
            {
                "rank": model["rank"],
                "key": model["key"],
                "name": model["name"],
                "r2": model["r2"],
                "rmse": model["rmse"],
                "bic": model["bic"],
                "fit_score": model["fit_score"],
                "constraint_score": model["constraint"]["score"],
                "combined_score": model["combined_score"],
                "feasible": model["constraint"]["feasible"],
            }
            for model in ranked_models
        ],
    }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "requested_model_id": requested_model_id,
        "config": {
            "lambda": physics_lambda,
            "solver": solver,
            "max_iter": max_iter,
            **{key: value for key, value in config.items() if key not in {"lambda", "solver", "max_iter"}},
        },
        "rows": rows,
        "models": ranked_models,
        "recommended_model": best,
        "anomalies": anomalies,
        "curve": curve,
        "knowledge_graph": knowledge_graph,
        "physics_constraints": physics_constraints,
        "symbolic_regression_layer": symbolic_regression_layer,
        "hypothesis_ranking": hypothesis_ranking,
        "summary": {
            "sample_count": len(rows),
            "temperature_range": [min_temp, max_temp],
            "conversion_range": [min(row["conversion"] for row in rows), max(row["conversion"] for row in rows)],
            "anomaly_count": len(anomalies),
            "confidence": confidence,
            "physics_score": best["constraint"]["score"],
            "projection_gap_mean": physics_profile["summary"]["projection_gap_mean"],
            "feasible_models": sum(1 for item in ranked_models if item["constraint"]["feasible"]),
        },
        "suggestions": suggestions,
        "core_technologies": CORE_TECHNOLOGIES,
        "workflow": WORKFLOW_TEMPLATE,
        "application_scenarios": APPLICATION_SCENARIOS,
        "engine_profile": {
            "mode": "本地规则代理 + 物理约束优化",
            "api_ready": True,
            "constraint_registry": list(MODEL_ORDER) + [model["key"] for model in symbolic_models],
            "hooks": [
                "intent routing",
                "physics constraint evaluator",
                "feasible-region projection",
                "symbolic regression formula search",
                "hypothesis ranking",
                "report generator",
                "future LLM API adapter",
            ],
        },
    }


def _physics_report_section(result: dict) -> list[str]:
    physics = result.get("physics_constraints", {})
    summary = physics.get("data_profile", {}).get("summary", {})
    rankings = physics.get("rankings", [])
    lines = [
        "",
        "## 三、物理约束校验",
        "",
        f"- 数据边界：{summary.get('temperature_span', ['-', '-'])[0]} - {summary.get('temperature_span', ['-', '-'])[1]} °C",
        f"- 转化率范围：{summary.get('conversion_span', ['-', '-'])[0]} - {summary.get('conversion_span', ['-', '-'])[1]}",
        f"- 越界点：{summary.get('boundary_violations', 0)}",
        f"- 单调性违例：{summary.get('trend_violation_count', 0)}",
        f"- 可行域投影平均修正：{summary.get('projection_gap_mean', 0)}",
        f"- 物理可行性评分：{summary.get('score', 0)}",
        "",
        "| 模型 | 组合评分 | 约束评分 | 约束惩罚 | 可行性 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for item in rankings:
        lines.append(
            f"| {item['name']} | {item['combined_score']} | {item['constraint_score']} | {item['constraint_penalty']} | {'是' if item['feasible'] else '否'} |"
        )
    return lines


def build_report_markdown(result: dict, title: str) -> str:
    best = result["recommended_model"]
    lines = [
        f"# {title}",
        "",
        f"> 生成时间：{result['generated_at']}。本报告用于科研分析辅助，不替代研究者判断。",
        "",
        "## 一、分析摘要",
        f"- 样本数：{result['summary']['sample_count']}",
        f"- 温度范围：{result['summary']['temperature_range'][0]} - {result['summary']['temperature_range'][1]} °C",
        f"- 推荐模型：{best['name']}",
        f"- 拟合 R²：{best['r2']}，RMSE：{best['rmse']}",
        f"- 异常点：{result['summary']['anomaly_count']} 个",
        f"- 物理评分：{result['summary'].get('physics_score', 0)}",
        "",
        "## 二、核心技术",
    ]
    for item in result.get("core_technologies", CORE_TECHNOLOGIES):
        lines.append(f"- **{item['title']}**：{item['detail']}")

    lines.extend(_physics_report_section(result))
    lines += [
        "",
        "## 四、模型排行",
        "| 模型 | 公式 | R² | RMSE | BIC |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {model['name']} | `{model['equation']}` | {model['r2']} | {model['rmse']} | {model['bic']} |"
        for model in result["models"]
    )
    lines += [
        "",
        "## 五、推荐模型",
        f"- 方程：{best['equation']}",
        "- 参数：" + ", ".join(f"{key}={value}" for key, value in best["parameters"].items()),
        f"- 物理约束评分：{best['constraint']['score']}",
    ]
    if best["constraint"]["violations"]:
        lines.append("- 主要约束问题：" + "；".join(item["title"] for item in best["constraint"]["violations"]))

    lines += [
        "",
        "## 六、异常点",
    ]
    if result["anomalies"]:
        lines += ["| 温度 | 转化率 | 批次 | 严重度 |", "| ---: | ---: | --- | --- |"]
        lines.extend(f"| {item['temperature']} | {item['conversion']} | {item['batch']} | {item['severity']} |" for item in result["anomalies"])
    else:
        lines.append("当前未发现需要重点复核的异常点。")

    lines += ["", "## 七、下一步实验建议", ""] + [f"{index}. {item}" for index, item in enumerate(result["suggestions"], 1)]
    return "\n".join(lines) + "\n"
