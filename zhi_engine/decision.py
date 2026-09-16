from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from typing import Any, Callable

from zhi_engine.symbolic import refit_symbolic_basis


FitCallback = Callable[[str, list[dict]], dict]
PredictCallback = Callable[[dict, float], float]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _parameter_count(model: dict) -> float:
    return max(1.0, float(model.get("effective_params") or len(model.get("parameters") or {}) or 1))


def _aicc(model: dict, sample_count: int) -> float:
    rss = max(float(model["rmse"]) ** 2 * sample_count, 1e-12)
    parameter_count = _parameter_count(model)
    aic = sample_count * math.log(rss / sample_count) + 2.0 * parameter_count
    denominator = sample_count - parameter_count - 1.0
    if denominator <= 0:
        return float("inf")
    return aic + (2.0 * parameter_count * (parameter_count + 1.0)) / denominator


def _refit_like(model: dict, rows: list[dict], fit_model: FitCallback) -> dict:
    if model.get("kind") == "symbolic" or str(model.get("key", "")).startswith("symbolic_"):
        return refit_symbolic_basis(rows, list(model.get("basis_terms") or []))
    return fit_model(str(model["key"]), rows)


def _cross_validated_rmse(
    model: dict,
    rows: list[dict],
    fit_model: FitCallback,
    predict_value: PredictCallback,
    fold_count: int,
) -> tuple[float | None, int]:
    fold_count = max(2, min(fold_count, len(rows) // 2))
    squared_errors: list[float] = []
    failures = 0
    for fold in range(fold_count):
        train = [row for index, row in enumerate(rows) if index % fold_count != fold]
        test = [row for index, row in enumerate(rows) if index % fold_count == fold]
        if len(train) < 4 or not test:
            continue
        try:
            fitted = _refit_like(model, train, fit_model)
            for row in test:
                prediction = predict_value(fitted, float(row["temperature"]))
                squared_errors.append((float(row["conversion"]) - prediction) ** 2)
        except (ArithmeticError, KeyError, TypeError, ValueError, OverflowError):
            failures += 1
    if not squared_errors:
        return None, failures
    return math.sqrt(_mean(squared_errors)), failures


def _bootstrap_stability(
    model: dict,
    rows: list[dict],
    fit_model: FitCallback,
    predict_value: PredictCallback,
    sample_count: int,
    seed: int,
) -> dict[str, Any]:
    rng = random.Random(seed)
    temperatures = [float(row["temperature"]) for row in rows]
    lo, hi = min(temperatures), max(temperatures)
    checkpoints = [lo, (lo + hi) / 2.0, hi]
    prediction_samples: list[list[float]] = []
    parameter_samples: dict[str, list[float]] = {}
    failures = 0

    for _ in range(sample_count):
        sampled = [rows[rng.randrange(len(rows))] for _ in rows]
        sampled.sort(key=lambda item: (float(item["temperature"]), int(item.get("source_index", 0))))
        try:
            fitted = _refit_like(model, sampled, fit_model)
            predictions = [float(predict_value(fitted, temperature)) for temperature in checkpoints]
            if not all(math.isfinite(value) for value in predictions):
                raise ValueError("non-finite bootstrap prediction")
            prediction_samples.append(predictions)
            for key, value in (fitted.get("parameters") or {}).items():
                numeric = float(value)
                if math.isfinite(numeric):
                    parameter_samples.setdefault(str(key), []).append(numeric)
        except (ArithmeticError, KeyError, TypeError, ValueError, OverflowError):
            failures += 1

    success_count = len(prediction_samples)
    failure_rate = failures / max(sample_count, 1)
    prediction_std = []
    intervals = []
    for index, temperature in enumerate(checkpoints):
        values = [sample[index] for sample in prediction_samples]
        if values:
            ordered = sorted(values)
            lower = ordered[max(0, int(0.05 * (len(ordered) - 1)))]
            upper = ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))]
            std = statistics.pstdev(values) if len(values) > 1 else 0.0
        else:
            lower = upper = std = 1.0
        prediction_std.append(std)
        intervals.append(
            {
                "temperature": round(temperature, 6),
                "lower_90": round(lower, 6),
                "upper_90": round(upper, 6),
                "std": round(std, 6),
            }
        )

    parameter_cv: dict[str, float | None] = {}
    finite_cvs: list[float] = []
    for key, values in parameter_samples.items():
        if len(values) < max(4, sample_count // 5):
            parameter_cv[key] = None
            continue
        mean_value = _mean(values)
        if abs(mean_value) < 1e-10:
            parameter_cv[key] = None
            continue
        cv = abs(statistics.pstdev(values) / mean_value) if len(values) > 1 else 0.0
        parameter_cv[key] = round(cv, 6)
        finite_cvs.append(cv)

    worst_prediction_std = max(prediction_std, default=1.0)
    median_parameter_cv = statistics.median(finite_cvs) if finite_cvs else 1.0
    score = 100.0 - 260.0 * worst_prediction_std - 32.0 * failure_rate - 12.0 * min(median_parameter_cv, 2.0)
    score = _clamp(score, 0.0, 100.0)
    return {
        "samples": sample_count,
        "successful_refits": success_count,
        "failure_rate": round(failure_rate, 6),
        "prediction_intervals": intervals,
        "max_prediction_std": round(worst_prediction_std, 6),
        "parameter_cv": parameter_cv,
        "median_parameter_cv": round(median_parameter_cv, 6),
        "score": round(score, 2),
    }


def build_evidence_gates(
    rows: list[dict],
    models: list[dict],
    config: dict[str, Any],
    fit_model: FitCallback,
    predict_value: PredictCallback,
) -> dict[str, Any]:
    fold_count = int(config.get("cv_folds", 4))
    bootstrap_samples = max(12, min(200, int(config.get("bootstrap_samples", 36))))
    seed = int(config.get("random_seed", 2026))
    response_std = statistics.pstdev(float(row["conversion"]) for row in rows) or 0.05

    for index, model in enumerate(models):
        aicc = _aicc(model, len(rows))
        cv_rmse, cv_failures = _cross_validated_rmse(model, rows, fit_model, predict_value, fold_count)
        stability = _bootstrap_stability(
            model,
            rows,
            fit_model,
            predict_value,
            bootstrap_samples,
            seed + index * 997,
        )
        model["evidence"] = {
            "aicc": round(aicc, 6) if math.isfinite(aicc) else None,
            "bic": model.get("bic"),
            "cv_rmse": round(cv_rmse, 6) if cv_rmse is not None else None,
            "cv_failures": cv_failures,
            "cv_error_ratio": round(cv_rmse / response_std, 6) if cv_rmse is not None else None,
            "bootstrap": stability,
        }

    finite_aicc = [model["evidence"]["aicc"] for model in models if model["evidence"]["aicc"] is not None]
    best_aicc = min(finite_aicc) if finite_aicc else 0.0
    cards = []
    for model in models:
        evidence = model["evidence"]
        delta_aicc = (evidence["aicc"] - best_aicc) if evidence["aicc"] is not None else float("inf")
        evidence["delta_aicc"] = round(delta_aicc, 6) if math.isfinite(delta_aicc) else None

        science_status = "pass" if model["constraint"]["feasible"] else "fail"
        cv_ratio = evidence["cv_error_ratio"]
        if delta_aicc <= 6 and cv_ratio is not None and cv_ratio <= 1.25:
            statistics_status = "pass"
        elif delta_aicc <= 12 and cv_ratio is not None and cv_ratio <= 2.0:
            statistics_status = "caution"
        else:
            statistics_status = "fail"

        stability_score = float(evidence["bootstrap"]["score"])
        if stability_score >= 65:
            identifiability_status = "pass"
        elif stability_score >= 40:
            identifiability_status = "caution"
        else:
            identifiability_status = "fail"

        statuses = [science_status, statistics_status, identifiability_status]
        if science_status == "fail" or statuses.count("fail") >= 2:
            overall = "fail"
        elif "fail" in statuses or "caution" in statuses:
            overall = "caution"
        else:
            overall = "pass"

        cv_component = 0.0 if cv_ratio is None else max(0.0, 1.0 - min(cv_ratio, 3.0) / 3.0)
        aicc_component = math.exp(-0.5 * min(delta_aicc, 50.0)) if math.isfinite(delta_aicc) else 0.0
        evidence_score = (
            0.35 * model["constraint"]["score"]
            + 30.0 * aicc_component
            + 20.0 * cv_component
            + 0.15 * stability_score
        )
        model["evidence_score"] = round(evidence_score, 6)
        model["gate_status"] = overall
        model["gates"] = {
            "science": {"status": science_status, "score": model["constraint"]["score"]},
            "statistics": {
                "status": statistics_status,
                "aicc": evidence["aicc"],
                "delta_aicc": evidence["delta_aicc"],
                "cv_rmse": evidence["cv_rmse"],
            },
            "identifiability": {
                "status": identifiability_status,
                "score": stability_score,
                "max_prediction_std": evidence["bootstrap"]["max_prediction_std"],
            },
        }
        cards.append(
            {
                "key": model["key"],
                "name": model["name"],
                "status": overall,
                "evidence_score": model["evidence_score"],
                "gates": model["gates"],
                "evidence_gaps": [
                    gate for gate, payload in model["gates"].items() if payload["status"] != "pass"
                ],
            }
        )

    priority = {"pass": 0, "caution": 1, "fail": 2}
    models.sort(key=lambda item: (priority[item["gate_status"]], -item["evidence_score"], item["combined_score"]))
    selected_assigned = False
    for rank, model in enumerate(models, start=1):
        model["rank"] = rank
        model["constraint"]["rank"] = rank
        if model["gate_status"] == "pass" and not selected_assigned:
            model["decision"] = "selected"
            selected_assigned = True
        elif model["gate_status"] in {"pass", "caution"}:
            model["decision"] = "not_excluded"
        else:
            model["decision"] = "excluded"

    return {
        "strategy": "science_constraints -> statistical_evidence -> identifiability_stability",
        "cv_folds": max(2, min(fold_count, len(rows) // 2)),
        "bootstrap_samples": bootstrap_samples,
        "cards": sorted(cards, key=lambda item: next(m["rank"] for m in models if m["key"] == item["key"])),
    }


def _normalized_entropy(probabilities: list[float]) -> float:
    positive = [value for value in probabilities if value > 0]
    if len(positive) <= 1:
        return 0.0
    entropy = -sum(value * math.log(value) for value in positive)
    return entropy / math.log(len(positive))


def _normalize_scores(scores: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, value) for value in scores.values()) or 1.0
    return {key: max(0.0, value) / total for key, value in scores.items()}


def build_anomaly_attribution(rows: list[dict], models: list[dict], anomalies: list[dict]) -> dict[str, Any]:
    if not anomalies:
        return {"status": "clear", "items": [], "policy": "retain_raw_values"}

    all_residuals = [float(value) for model in models for value in model.get("residuals", [])]
    median_residual = statistics.median(all_residuals) if all_residuals else 0.0
    mad = statistics.median(abs(value - median_residual) for value in all_residuals) if all_residuals else 0.0
    scale = max(1.4826 * mad, 1e-4)
    enriched = []
    actions_by_cause = {
        "entry_error": "核对原始记录、单位和录入过程",
        "measurement_error": "检查仪器校准与误差带，必要时重测",
        "model_mismatch": "扩展候选模型或补充缺失变量",
        "mechanism_change": "保留该点并设计局部验证实验",
    }

    for anomaly in anomalies:
        index = int(anomaly["index"])
        row = rows[index]
        residuals = [abs(float(model["residuals"][index])) for model in models]
        model_scales = [statistics.pstdev(model["residuals"]) or 1e-4 for model in models]
        consensus = _mean([1.0 if residual >= 1.5 * sigma else 0.0 for residual, sigma in zip(residuals, model_scales)])
        robust_z = abs(float(anomaly["residual"]) - median_residual) / scale
        uncertainty = float(row.get("measurement_uncertainty") or 0.0)
        boundary = float(row["conversion"]) < 0.0 or float(row["conversion"]) > 1.0

        scores = {
            "entry_error": 0.25 + (1.4 if boundary else 0.0) + (0.45 if robust_z > 4 else 0.0),
            "measurement_error": 0.35 + (0.9 if uncertainty and abs(float(anomaly["residual"])) <= 2.5 * uncertainty else 0.0),
            "model_mismatch": 0.35 + 0.9 * (1.0 - consensus),
            "mechanism_change": 0.25 + 1.15 * consensus,
        }
        posterior = _normalize_scores(scores)
        entropy = _normalized_entropy(list(posterior.values()))
        cause, probability = max(posterior.items(), key=lambda item: item[1])
        abstain = probability < 0.45 or entropy > 0.92
        if abstain:
            action = "暂缓自动归因；保留原值并执行核对、复测或扩展模型"
            conclusion = "abstain"
        else:
            action = actions_by_cause[cause]
            conclusion = cause
        enriched.append(
            {
                **anomaly,
                "robust_z": round(robust_z, 4),
                "multi_model_consensus": round(consensus, 4),
                "posterior": {key: round(value, 6) for key, value in posterior.items()},
                "posterior_entropy": round(entropy, 6),
                "conclusion": conclusion,
                "abstain": abstain,
                "action": action,
                "raw_value_retained": True,
            }
        )
    return {
        "status": "review_required",
        "policy": "retain_raw_values",
        "cause_labels": {
            "entry_error": "录入问题",
            "measurement_error": "测量误差",
            "model_mismatch": "模型失配",
            "mechanism_change": "候选机制变化",
        },
        "items": enriched,
    }


def build_open_set_decision(
    rows: list[dict],
    models: list[dict],
    anomaly_count: int,
    predict_value: PredictCallback,
) -> dict[str, Any]:
    viable = [model for model in models if model.get("gate_status") != "fail"] or models[:2]
    lo = min(float(row["temperature"]) for row in rows)
    hi = max(float(row["temperature"]) for row in rows)
    span = max(hi - lo, 1.0)
    probe = hi + 0.15 * span
    predictions = [float(predict_value(model, probe)) for model in viable]
    evidence_weights = {}
    for model in viable:
        delta_value = model["evidence"].get("delta_aicc")
        delta_aicc = 50.0 if delta_value is None else float(delta_value)
        evidence_weights[model["key"]] = (
            math.exp(-0.5 * min(delta_aicc, 50.0))
            * max(0.1, float(model["evidence"]["bootstrap"]["score"]) / 100.0)
        )
    probabilities = _normalize_scores(evidence_weights)
    disagreement = 0.0
    if predictions:
        disagreement = min(1.0, (max(predictions) - min(predictions)) / 0.35)
    residual_structure = min(1.0, _mean([abs(_mean(model.get("residuals", []))) for model in viable]) / 0.08)
    anomaly_pressure = min(1.0, anomaly_count / max(len(rows) * 0.25, 1.0))
    no_pass = not any(model.get("gate_status") == "pass" for model in models)
    h_other = _clamp(0.06 + 0.32 * disagreement + 0.22 * residual_structure + 0.22 * anomaly_pressure + (0.18 if no_pass else 0.0))
    scaled = {key: value * (1.0 - h_other) for key, value in probabilities.items()}
    scaled["H_other"] = h_other
    entropy = _normalized_entropy(list(scaled.values()))
    abstain = h_other >= 0.35 or entropy >= 0.82 or no_pass
    triggers = []
    if disagreement >= 0.45:
        triggers.append("extrapolation_disagreement")
    if anomaly_pressure >= 0.5:
        triggers.append("structured_residual_or_anomaly_pressure")
    if no_pass:
        triggers.append("no_hypothesis_passed_all_gates")
    return {
        "hypotheses": {key: round(value, 6) for key, value in scaled.items()},
        "h_other_probability": round(h_other, 6),
        "posterior_entropy": round(entropy, 6),
        "abstain": abstain,
        "decision": "collect_more_evidence" if abstain else "provisional_conclusion",
        "triggers": triggers,
        "probe_temperature": round(probe, 6),
        "note": "H_other 保留候选库之外的新机制；高不确定性时不强制形成唯一结论。",
    }


def build_experiment_design(
    rows: list[dict],
    models: list[dict],
    config: dict[str, Any],
    predict_value: PredictCallback,
) -> dict[str, Any]:
    temperatures = [float(row["temperature"]) for row in rows]
    lo, hi = min(temperatures), max(temperatures)
    span = max(hi - lo, 1.0)
    constraints = dict(config.get("experiment_constraints") or {})
    allowed_lo = float(constraints.get("temperature_min", lo))
    allowed_hi = float(constraints.get("temperature_max", hi + 0.15 * span))
    safety_hi = float(constraints.get("safety_max_temperature", allowed_hi))
    budget = float(constraints.get("budget", 3.0))
    cost_weight = max(0.0, float(constraints.get("cost_weight", 0.08)))
    viable = [model for model in models if model.get("gate_status") != "fail"] or models[:2]
    noise = statistics.pstdev(float(value) for model in viable for value in model.get("residuals", [])) or 0.02

    candidates = []
    for index in range(25):
        temperature = allowed_lo + (allowed_hi - allowed_lo) * index / 24.0
        if min(abs(temperature - observed) for observed in temperatures) < 0.02 * span:
            continue
        predictions = [float(predict_value(model, temperature)) for model in viable]
        prediction_mean = _mean(predictions)
        disagreement = _mean([(value - prediction_mean) ** 2 for value in predictions])
        information_gain = 0.5 * math.log1p(disagreement / max(noise * noise, 1e-8))
        extrapolation = max(0.0, temperature - hi) / span + max(0.0, lo - temperature) / span
        cost = 1.0 + 1.6 * extrapolation + 0.15 * abs(temperature - (lo + hi) / 2.0) / span
        feasible = allowed_lo <= temperature <= allowed_hi and temperature <= safety_hi and cost <= budget
        utility = information_gain - cost_weight * cost if feasible else float("-inf")
        candidates.append(
            {
                "temperature": round(temperature, 6),
                "predicted_conversion": round(prediction_mean, 6),
                "model_disagreement": round(disagreement, 8),
                "expected_information_gain": round(information_gain, 6),
                "estimated_cost": round(cost, 6),
                "feasible": feasible,
                "utility": round(utility, 6) if math.isfinite(utility) else None,
                "region": "extrapolation" if temperature > hi or temperature < lo else "interpolation",
            }
        )

    feasible_candidates = [item for item in candidates if item["feasible"]]
    feasible_candidates.sort(key=lambda item: (-float(item["utility"]), -item["expected_information_gain"], item["estimated_cost"]))
    recommended = feasible_candidates[0] if feasible_candidates else None
    return {
        "objective": "maximize_expected_information_gain_minus_cost",
        "constraints": {
            "temperature_min": allowed_lo,
            "temperature_max": allowed_hi,
            "safety_max_temperature": safety_hi,
            "budget": budget,
            "cost_weight": cost_weight,
        },
        "recommended": recommended,
        "candidates": feasible_candidates[:10],
        "rejected_count": len(candidates) - len(feasible_candidates),
        "human_confirmation_required": True,
    }


def build_audit_record(rows: list[dict], config: dict[str, Any], best: dict, open_set: dict) -> dict[str, Any]:
    canonical = json.dumps(
        {"rows": rows, "config": config},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    data_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "schema_version": "1.0",
        "engine_version": "0.5.0",
        "data_sha256": data_hash,
        "raw_values_retained": True,
        "random_seed": int(config.get("random_seed", 2026)),
        "decision_trace": [
            "normalize_and_preserve_provenance",
            "physics_constraint_gate",
            "statistical_evidence_gate",
            "identifiability_and_bootstrap_gate",
            "anomaly_posterior_attribution",
            "open_set_abstention_check",
            "constrained_active_experiment_design",
        ],
        "selected_model": best["key"],
        "final_decision": open_set["decision"],
    }
