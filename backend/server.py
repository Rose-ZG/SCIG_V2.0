from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from zhi_engine.analysis import analyze_dataset, build_report_markdown, demo_dataset
from zhi_engine.deepseek_client import DeepSeekAPIError, DeepSeekSettings, generate_deepseek_answer, load_deepseek_settings
from zhi_engine.reporting import build_report_docx
from zhi_engine.store import ConversationStore


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
ASSETS = ROOT / "assets"
DATA = ROOT / "data"

if os.environ.get("VERCEL"):
    os.environ.setdefault("ZHIGOU_ENV", "production")

app = FastAPI(title="Zhigou Engine", version="0.5.0")
app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")


@dataclass
class Runtime:
    store: ConversationStore
    deepseek: DeepSeekSettings
    deployment_mode: str = "production"
    database_mode: str = "external"


_runtime: Runtime | None = None
_runtime_lock = threading.Lock()


def _clean_optional(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _deployment_mode() -> str:
    return os.environ.get("ZHIGOU_ENV", "production").strip().lower() or "production"


def _get_runtime() -> Runtime:
    global _runtime
    if _runtime is not None:
        return _runtime
    with _runtime_lock:
        if _runtime is None:
            deployment_mode = _deployment_mode()
            production = deployment_mode in {"prod", "production"}
            database_url = _clean_optional(os.environ.get("ZHIGOU_DATABASE_URL"))
            if not database_url:
                raise HTTPException(
                    status_code=500,
                    detail={"error": "服务未完成生产配置", "detail": "请配置 ZHIGOU_DATABASE_URL。"},
                )

            deepseek = load_deepseek_settings()
            if _env_bool("ZHIGOU_REQUIRE_DEEPSEEK", production) and not deepseek.enabled:
                raise HTTPException(
                    status_code=500,
                    detail={"error": "服务未完成生产配置", "detail": "请配置 DEEPSEEK_API_KEY。"},
                )

            try:
                _runtime = Runtime(
                    store=ConversationStore(database_url, legacy_json_path=DATA / "conversations.json"),
                    deepseek=deepseek,
                    deployment_mode=deployment_mode,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail={"error": "数据库连接失败", "detail": str(exc)},
                ) from exc
    return _runtime


@app.exception_handler(HTTPException)
async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(exc.detail, status_code=exc.status_code)
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def _exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"error": "server error", "detail": str(exc)}, status_code=500)


async def _read_payload(request: Request) -> dict[str, Any]:
    body = await request.body()
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail={"error": "request body must be a JSON object"})
    return payload


def _unwrap_latex_command(text: str, command: str) -> str:
    token = f"\\{command}{{"
    chunks: list[str] = []
    index = 0
    while index < len(text):
        start = text.find(token, index)
        if start == -1:
            chunks.append(text[index:])
            break
        chunks.append(text[index:start])
        body_start = start + len(token)
        cursor = body_start
        depth = 1
        while cursor < len(text):
            char = text[cursor]
            escaped = cursor > 0 and text[cursor - 1] == "\\"
            if char == "{" and not escaped:
                depth += 1
            elif char == "}" and not escaped:
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if depth == 0:
            chunks.append(text[body_start:cursor])
            index = cursor + 1
        else:
            chunks.append(text[start:])
            break
    return "".join(chunks)


def _normalize_assistant_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\\\\", "\\")
    normalized = re.sub(r"\bDeepSeek\b", "智能引擎", normalized, flags=re.IGNORECASE)
    normalized = _unwrap_latex_command(normalized, "boxed")
    normalized = re.sub(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"(\1) / (\2)", normalized)
    normalized = normalized.replace("\\begin{aligned}", "").replace("\\end{aligned}", "")
    normalized = normalized.replace("\\[", "").replace("\\]", "")
    normalized = normalized.replace("$$", "")
    normalized = normalized.replace("\\times", "×").replace("\\cdot", "·").replace("\\pm", "±")
    normalized = re.sub(r"\^\{([^{}]+)\}", r"^(\1)", normalized)
    normalized = re.sub(r"_\{([^{}]+)\}", r"_\1", normalized)
    normalized = normalized.replace("\\left", "").replace("\\right", "")
    normalized = normalized.replace("\\,", " ").replace("\\;", " ")
    normalized = re.sub(r"(?<=\d)([A-Za-z])", r" * \1", normalized)
    normalized = re.sub(r"\)([A-Za-z])", r") * \1", normalized)
    return normalized.strip()


def _infer_intent(text: str) -> str:
    lowered = text.lower()
    if any(word in text for word in ("报告", "汇报", "总结", "docx")):
        return "report"
    if any(word in text for word in ("订阅", "升级", "套餐", "方案")):
        return "subscription"
    if any(word in text for word in ("主题", "白蓝", "深蓝", "暗黑")):
        return "theme"
    if any(word in text for word in ("物理约束", "约束校验", "边界", "单调", "可行域")):
        return "physics"
    if any(word in text for word in ("符号回归", "符号", "候选方程", "机理解释")) or any(
        word in lowered for word in ("symbolic", "llm")
    ):
        return "symbolic"
    if any(word in text for word in ("假设", "排行", "排名", "排序", "推荐")):
        return "ranking"
    if any(word in text for word in ("曲线", "拟合", "模型", "动力学")) or any(
        word in lowered for word in ("fit", "curve", "model")
    ):
        return "fit"
    if any(word in text for word in ("异常", "离群", "问题", "偏差")):
        return "anomaly"
    if any(word in text for word in ("工作流", "agent", "api", "接入")):
        return "workflow"
    return "general"


def _compose_answer(intent: str, result: dict[str, Any]) -> str:
    best = result["recommended_model"]
    physics = result.get("physics_constraints", {})
    physics_summary = physics.get("data_profile", {}).get("summary", {})
    rankings = physics.get("rankings", [])
    if intent == "report":
        return "我已经把当前分析整理进报告流程了，点“生成报告”就能直接下载 DOCX。"
    if intent == "subscription":
        return "订阅区已经压缩成小入口了，点一下就能切换方案。"
    if intent == "theme":
        return "主题支持切换，包含暗黑、白蓝和深蓝。"
    if intent == "physics":
        return (
            "物理约束校验已完成："
            f"当前物理评分为 {physics_summary.get('score', result['summary'].get('physics_score', '-'))}，"
            f"边界越界 {physics_summary.get('boundary_violations', 0)} 个，"
            f"单调违例 {physics_summary.get('trend_violation_count', 0)} 个；"
            "分析面板可以查看可行域投影和具体违规项。"
        )
    if intent == "symbolic":
        symbolic = result.get("symbolic_regression_layer", {})
        best_formula = symbolic.get("best_formula") or {}
        formula = best_formula.get("equation") or best.get("equation")
        names = "、".join(model["name"] for model in result["models"][:3])
        return (
            "LLM + 符号回归层已切到真实公式搜索流程："
            f"SR 算子已经用 + - * / 生成并评估 {names}。"
            f"当前最优显式公式是：{formula}。"
            "智能引擎负责理解用户描述、解释公式和给出下一步实验建议，公式搜索与物理约束评分仍由后端可追溯执行。"
        )
    if intent == "ranking":
        top = rankings[0] if rankings else {"name": best["name"], "constraint_score": best["constraint"]["score"]}
        return (
            f"假设性排行已完成：第 1 名是 {top['name']}，"
            f"约束评分 {top.get('constraint_score', '-')}; "
            f"综合拟合与物理可行性后，推荐采用 {best['name']} 作为当前主假设。"
        )
    if intent == "fit":
        return f"基于当前数据，我优先推荐 {best['name']}，它的 R² 为 {best['r2']:.3f}，公式为：{best['equation']}。"
    if intent == "anomaly":
        return f"当前识别到 {result['summary']['anomaly_count']} 个异常点。"
    if intent == "workflow":
        return "这套系统现在就是轻量 agent：先识别意图，再做分析、诊断和报告。后续可以无缝接外部 API。"
    return "我可以帮你筛模型、拟合曲线、找异常点，并把结果整理成对话里的报告。"


def _bootstrap_payload(runtime: Runtime) -> dict[str, Any]:
    sample_result = analyze_dataset(demo_dataset())
    return {
        "deployment_mode": runtime.deployment_mode,
        "database_mode": runtime.database_mode,
        "llm_provider": runtime.deepseek.public_state(),
        "conversations": runtime.store.list_conversations(),
        "dataset": demo_dataset(),
        "themes": [
            {"key": "dark", "name": "暗黑"},
            {"key": "light", "name": "白蓝"},
            {"key": "blue", "name": "深蓝"},
        ],
        "subscription_plans": [
            {"key": "basic", "name": "基础版", "price": "0 元", "desc": "适合体验与课堂演示。"},
            {"key": "pro", "name": "专业版", "price": "49 元/月", "desc": "解锁完整分析、历史和 DOCX 报告。"},
            {"key": "enterprise", "name": "企业版", "price": "定制", "desc": "适合实验室和私有化部署。"},
        ],
        "core_technologies": sample_result["core_technologies"],
        "workflow": sample_result["workflow"],
        "application_scenarios": sample_result["application_scenarios"],
        "engine_profile": sample_result["engine_profile"],
        "physics_constraints": sample_result["physics_constraints"],
        "symbolic_regression_layer": sample_result["symbolic_regression_layer"],
        "hypothesis_ranking": sample_result["hypothesis_ranking"],
    }


@app.get("/api/bootstrap")
def bootstrap() -> JSONResponse:
    return JSONResponse(_bootstrap_payload(_get_runtime()))


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> JSONResponse:
    conversation = _get_runtime().store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail={"error": "conversation not found"})
    return JSONResponse(conversation)


@app.post("/api/analyze")
async def analyze(request: Request) -> JSONResponse:
    payload = await _read_payload(request)
    try:
        dataset = payload.get("dataset") or demo_dataset()
        result = analyze_dataset(dataset)
        _get_runtime().store.record_analysis(
            source="manual",
            dataset=dataset,
            result=result,
            conversation_id=_clean_optional(payload.get("conversationId")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    return JSONResponse(result)


@app.post("/api/chat")
async def chat(request: Request) -> JSONResponse:
    payload = await _read_payload(request)
    text = str(payload.get("message", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail={"error": "message is required"})

    runtime = _get_runtime()
    conversation_id = str(payload.get("conversationId") or uuid.uuid4())
    dataset = payload.get("dataset") or demo_dataset()
    try:
        result = analyze_dataset(dataset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    intent = _infer_intent(text)
    local_answer = _compose_answer(intent, result)
    existing_conversation = runtime.store.get_conversation(conversation_id)
    answer = local_answer
    llm_meta = runtime.deepseek.public_state(mode="local_fallback")
    if runtime.deepseek.enabled:
        try:
            answer, llm_meta = generate_deepseek_answer(
                settings=runtime.deepseek,
                user_text=text,
                intent=intent,
                result=result,
                conversation=existing_conversation,
            )
        except DeepSeekAPIError as exc:
            llm_meta = runtime.deepseek.public_state(mode="error", error=str(exc))
            answer = local_answer

    answer = _normalize_assistant_text(answer)
    user_message = {"role": "user", "content": text, "time": datetime.now().isoformat(timespec="seconds")}
    assistant_message = {
        "role": "assistant",
        "content": answer,
        "time": datetime.now().isoformat(timespec="seconds"),
        "result": result,
        "intent": intent,
        "llm": llm_meta,
    }
    conversation = runtime.store.append_messages(
        conversation_id,
        user_message,
        assistant_message,
        plan=payload.get("plan"),
        theme=payload.get("theme"),
    )
    runtime.store.record_analysis(source="chat", dataset=dataset, result=result, conversation_id=conversation_id)
    return JSONResponse({"conversation": conversation, "result": result, "intent": intent, "llm": llm_meta})


@app.post("/api/report")
async def report(request: Request) -> Response:
    payload = await _read_payload(request)
    try:
        result = payload.get("result") or analyze_dataset(payload.get("dataset") or demo_dataset())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    title = str(payload.get("title") or "知构引擎科研分析报告")
    format_name = str(payload.get("format") or "docx").lower()
    _get_runtime().store.record_report(
        title=title,
        result=result,
        report_format=format_name,
        conversation_id=_clean_optional(payload.get("conversationId")),
    )

    if format_name == "markdown":
        body = build_report_markdown(result, title).encode("utf-8")
        return Response(
            body,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="zhigou-analysis-report.md"'},
        )

    body = build_report_docx(result, title, logo_path=ASSETS / "logo.png")
    return Response(
        body,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="zhigou-analysis-report.docx"'},
    )


def _static_response(relative_path: str) -> FileResponse:
    requested = "index.html" if relative_path in {"", "/"} else relative_path
    target = (STATIC / requested).resolve()
    try:
        target.relative_to(STATIC.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": "file not found"}) from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail={"error": "file not found"})
    return FileResponse(target)


@app.get("/")
def index() -> FileResponse:
    return _static_response("index.html")


@app.get("/{path:path}")
def static_files(path: str) -> FileResponse:
    return _static_response(path)

