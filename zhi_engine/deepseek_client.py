from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class DeepSeekAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    timeout: float = 45.0
    max_tokens: int = 1200
    temperature: float = 0.35
    thinking: str = "disabled"
    reasoning_effort: str = "high"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def public_state(self, *, mode: str | None = None, error: str | None = None) -> dict:
        state = {
            "provider": "deepseek",
            "enabled": self.enabled,
            "mode": mode or ("remote" if self.enabled else "local_fallback"),
            "model": self.model,
            "base_url": self.base_url,
            "thinking": self.thinking,
            "reasoning_effort": self.reasoning_effort,
        }
        if error:
            state["error"] = error
        return state


def _env_int(name: str, default: int) -> int:
    try:
        return int(_read_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_read_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _read_env(name: str, default: str = "") -> str:
    if name in os.environ:
        return os.environ.get(name, default)
    if os.name != "nt":
        return default
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
        return str(value)
    except OSError:
        return default


def load_deepseek_settings() -> DeepSeekSettings:
    thinking = _read_env("DEEPSEEK_THINKING", "disabled").strip().lower()
    if thinking not in {"enabled", "disabled"}:
        thinking = "disabled"

    effort = _read_env("DEEPSEEK_REASONING_EFFORT", "high").strip().lower()
    if effort not in {"low", "high", "max"}:
        effort = "high"

    return DeepSeekSettings(
        api_key=_read_env("DEEPSEEK_API_KEY").strip(),
        base_url=_read_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/"),
        model=_read_env("DEEPSEEK_MODEL", "deepseek-v4-pro").strip() or "deepseek-v4-pro",
        timeout=_env_float("DEEPSEEK_TIMEOUT", 45.0),
        max_tokens=max(256, min(_env_int("DEEPSEEK_MAX_TOKENS", 1200), 8192)),
        temperature=max(0.0, min(_env_float("DEEPSEEK_TEMPERATURE", 0.35), 2.0)),
        thinking=thinking,
        reasoning_effort=effort,
    )


def generate_deepseek_answer(
    *,
    settings: DeepSeekSettings,
    user_text: str,
    intent: str,
    result: dict,
    conversation: dict | None,
) -> tuple[str, dict]:
    if not settings.enabled:
        raise DeepSeekAPIError("未配置 DEEPSEEK_API_KEY")

    messages = _build_messages(user_text=user_text, intent=intent, result=result, conversation=conversation)
    return _post_chat_completion(settings, messages)


def _build_messages(*, user_text: str, intent: str, result: dict, conversation: dict | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是知构引擎 Knowledge Structure Engine 的科研分析 Agent。"
                "你需要用中文回答，语气专业、清晰、像真实软件里的助手。"
                "你可以基于系统提供的本地分析结果解释物理约束、LLM+符号回归公式搜索、假设排行、异常诊断和报告生成。"
                "不要编造未提供的数据、实验结论或 API 状态；如果信息不足，先说明需要哪些数据。"
                "不要提及 DeepSeek、API key、模型供应商或内部调用状态。"
                "公式必须用普通文本表达，例如 conversion = 0.12 + 0.0063 * T - 0.000012 * T^2。"
                "不要使用 LaTeX、\\[\\]、\\boxed、$$、aligned 或 Markdown 复杂表格。"
                "如需对比多项结果，优先使用 3-5 条短列表，每条只保留关键指标。"
            ),
        },
        {
            "role": "system",
            "content": "当前本地分析上下文 JSON：\n" + json.dumps(_analysis_digest(intent, result), ensure_ascii=False),
        },
    ]

    history = (conversation or {}).get("messages") or []
    for item in history[-10:]:
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        messages.append({"role": role, "content": _truncate(content, 1400)})

    messages.append({"role": "user", "content": user_text})
    return messages


def _analysis_digest(intent: str, result: dict) -> dict:
    physics = result.get("physics_constraints") or {}
    profile = physics.get("data_profile") or {}
    symbolic = result.get("symbolic_regression_layer") or {}
    ranking = result.get("hypothesis_ranking") or {}

    return {
        "user_intent": intent,
        "summary": result.get("summary", {}),
        "recommended_model": _model_digest(result.get("recommended_model") or {}),
        "models": [_model_digest(model) for model in result.get("models", [])[:5]],
        "physics_constraints": {
            "rules": physics.get("rules", []),
            "summary": profile.get("summary", {}),
            "projection": physics.get("projection", {}),
            "rankings": physics.get("rankings", [])[:5],
        },
        "symbolic_regression_layer": {
            "status": symbolic.get("status"),
            "description": symbolic.get("description"),
            "search_space": symbolic.get("search_space", {}),
            "best_formula": symbolic.get("best_formula", {}),
            "candidates": symbolic.get("candidates", [])[:5],
        },
        "hypothesis_ranking": {
            "strategy": ranking.get("strategy"),
            "physics_weight": ranking.get("physics_weight"),
            "items": ranking.get("items", [])[:5],
        },
        "anomalies": result.get("anomalies", [])[:6],
        "suggestions": result.get("suggestions", [])[:6],
        "workflow": result.get("workflow", []),
        "report_capability": "当前系统可在对话内预览报告，并通过 /api/report 下载 DOCX。",
    }


def _model_digest(model: dict) -> dict:
    constraint = model.get("constraint") or {}
    return {
        "key": model.get("key"),
        "name": model.get("name"),
        "equation": model.get("equation"),
        "parameters": model.get("parameters", {}),
        "r2": model.get("r2"),
        "rmse": model.get("rmse"),
        "bic": model.get("bic"),
        "rank": model.get("rank"),
        "physics": model.get("physics"),
        "constraint_score": constraint.get("score", model.get("constraint_score")),
        "feasible": constraint.get("feasible", model.get("feasible")),
    }


def _post_chat_completion(settings: DeepSeekSettings, messages: list[dict[str, str]]) -> tuple[str, dict]:
    payload: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
        "stream": False,
        "max_tokens": settings.max_tokens,
        "temperature": settings.temperature,
        "thinking": {"type": settings.thinking},
    }
    if settings.thinking == "enabled":
        payload["reasoning_effort"] = settings.reasoning_effort

    request = urllib.request.Request(
        f"{settings.base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key}",
        },
        method="POST",
    )

    started_at = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DeepSeekAPIError(f"DeepSeek API 返回 {exc.code}: {_compact_error(detail)}") from exc
    except urllib.error.URLError as exc:
        raise DeepSeekAPIError(f"DeepSeek API 网络连接失败: {exc.reason}") from exc

    try:
        data = json.loads(body)
        choice = data["choices"][0]
        content = str(choice["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise DeepSeekAPIError("DeepSeek API 响应格式无法解析") from exc

    if not content:
        raise DeepSeekAPIError("DeepSeek API 返回了空回复")

    meta = settings.public_state(mode="remote")
    meta.update(
        {
            "finish_reason": choice.get("finish_reason"),
            "usage": data.get("usage", {}),
            "latency_ms": int((time.perf_counter() - started_at) * 1000),
        }
    )
    return content, meta


def _compact_error(detail: str) -> str:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return _truncate(detail.strip(), 260)
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return _truncate(str(error.get("message") or error), 260)
    return _truncate(detail.strip(), 260)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
