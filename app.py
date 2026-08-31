from __future__ import annotations

import json
import mimetypes
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from pg0 import Pg0

from zhi_engine.analysis import analyze_dataset, build_report_markdown, demo_dataset
from zhi_engine.deepseek_client import DeepSeekAPIError, DeepSeekSettings, generate_deepseek_answer, load_deepseek_settings
from zhi_engine.reporting import build_report_docx
from zhi_engine.store import ConversationStore


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
ASSETS = ROOT / "assets"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)


def json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _clean_optional(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _deployment_mode() -> str:
    return os.environ.get("ZHIGOU_ENV", "development").strip().lower() or "development"


@dataclass
class AppRuntime:
    store: ConversationStore
    deepseek: DeepSeekSettings
    pg0: Pg0 | None = None
    database_mode: str = "external"
    deployment_mode: str = "development"

    def close(self) -> None:
        if self.pg0 is not None:
            try:
                self.pg0.stop()
            except Exception:
                pass


def build_runtime() -> AppRuntime:
    legacy_json = DATA / "conversations.json"
    deployment_mode = _deployment_mode()
    production = deployment_mode in {"prod", "production"}
    require_external_db = _env_bool("ZHIGOU_REQUIRE_EXTERNAL_DB", production)
    allow_embedded_db = _env_bool("ZHIGOU_ALLOW_EMBEDDED_DB", not require_external_db)
    require_deepseek = _env_bool("ZHIGOU_REQUIRE_DEEPSEEK", production)
    deepseek_settings = load_deepseek_settings()
    if require_deepseek and not deepseek_settings.enabled:
        raise RuntimeError("生产环境需要配置 DEEPSEEK_API_KEY，不能以本地兜底模式启动。")

    database_url = _clean_optional(os.environ.get("ZHIGOU_DATABASE_URL"))
    if database_url:
        return AppRuntime(
            store=ConversationStore(database_url, legacy_json_path=legacy_json),
            deepseek=deepseek_settings,
            database_mode="external",
            deployment_mode=deployment_mode,
        )

    if not allow_embedded_db:
        raise RuntimeError(
            "生产环境需要配置 ZHIGOU_DATABASE_URL，不能使用内置本地 PostgreSQL。"
        )

    instance_name = os.environ.get("ZHIGOU_PG0_NAME", "zhigou-engine")
    instance_database = os.environ.get("ZHIGOU_PG0_DATABASE", "zhigou_engine")
    instance_user = os.environ.get("ZHIGOU_PG0_USER", "postgres")
    instance_password = os.environ.get("ZHIGOU_PG0_PASSWORD", "postgres")
    pg0 = Pg0(
        name=instance_name,
        username=instance_user,
        password=instance_password,
        database=instance_database,
    )
    info = pg0.start()
    database_url = info.uri or pg0.uri
    if not database_url:
        pg0.stop()
        raise RuntimeError("embedded PostgreSQL did not expose a connection URI")
    return AppRuntime(
        store=ConversationStore(database_url, legacy_json_path=legacy_json),
        deepseek=deepseek_settings,
        pg0=pg0,
        database_mode="embedded",
        deployment_mode=deployment_mode,
    )


class ZhigouServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class, runtime: AppRuntime) -> None:
        super().__init__(server_address, handler_class)
        self.runtime = runtime


class AppHandler(BaseHTTPRequestHandler):
    server_version = "ZhigouEngine/0.4"

    @property
    def runtime(self) -> AppRuntime:
        return self.server.runtime  # type: ignore[attr-defined]

    @property
    def store(self) -> ConversationStore:
        return self.runtime.store

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")

    def send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_binary(self, body: bytes, content_type: str, filename: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/bootstrap":
            sample_result = analyze_dataset(demo_dataset())
            self.send_json(
                {
                    "deployment_mode": self.runtime.deployment_mode,
                    "database_mode": self.runtime.database_mode,
                    "llm_provider": self.runtime.deepseek.public_state(),
                    "conversations": self.store.list_conversations(),
                    "dataset": demo_dataset(),
                    "capabilities": [
                        {"icon": "scan", "title": "物理约束校验层", "detail": "检查边界、单调性、平滑性和参数可行性。"},
                        {"icon": "sparkles", "title": "LLM + 符号回归层", "detail": "智能引擎解析任务与先验，SR 算子用 + - * / 搜索显式拟合公式。"},
                        {"icon": "route", "title": "假设性排行层", "detail": "综合拟合误差、BIC、约束惩罚和可解释性排序。"},
                    ],
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
            )
            return

        if path.startswith("/api/conversations/"):
            conversation_id = path.rsplit("/", 1)[-1]
            conversation = self.store.get_conversation(conversation_id)
            if conversation is None:
                self.send_json({"error": "conversation not found"}, HTTPStatus.NOT_FOUND)
            else:
                self.send_json(conversation)
            return

        if path.startswith("/assets/"):
            self.serve_file(ASSETS / Path(path.removeprefix("/assets/")))
            return

        self.serve_file(STATIC / ("index.html" if path in ("/", "") else path.removeprefix("/")))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/analyze":
                dataset = payload.get("dataset") or demo_dataset()
                result = analyze_dataset(dataset)
                self.store.record_analysis(
                    source="manual",
                    dataset=dataset,
                    result=result,
                    conversation_id=_clean_optional(payload.get("conversationId")),
                )
                self.send_json(result)
                return

            if path == "/api/chat":
                self.handle_chat(payload)
                return

            if path == "/api/report":
                self.handle_report(payload)
                return

            self.send_json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": f"server error: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_report(self, payload: dict) -> None:
        result = payload.get("result") or analyze_dataset(payload.get("dataset") or demo_dataset())
        title = str(payload.get("title") or "知构引擎科研分析报告")
        format_name = str(payload.get("format") or "docx").lower()
        self.store.record_report(
            title=title,
            result=result,
            report_format=format_name,
            conversation_id=_clean_optional(payload.get("conversationId")),
        )
        if format_name == "markdown":
            report = build_report_markdown(result, title)
            self.send_binary(report.encode("utf-8"), "text/markdown; charset=utf-8", "zhigou-analysis-report.md")
            return

        body = build_report_docx(result, title, logo_path=ASSETS / "logo.png")
        self.send_binary(
            body,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "zhigou-analysis-report.docx",
        )

    def handle_chat(self, payload: dict) -> None:
        text = str(payload.get("message", "")).strip()
        if not text:
            self.send_json({"error": "message is required"}, HTTPStatus.BAD_REQUEST)
            return

        conversation_id = str(payload.get("conversationId") or uuid.uuid4())
        dataset = payload.get("dataset") or demo_dataset()
        result = analyze_dataset(dataset)
        intent = self._infer_intent(text)
        local_answer = self._compose_answer(intent, result)
        existing_conversation = self.store.get_conversation(conversation_id)
        answer = local_answer
        llm_meta = self.runtime.deepseek.public_state(mode="local_fallback")
        if self.runtime.deepseek.enabled:
            try:
                answer, llm_meta = generate_deepseek_answer(
                    settings=self.runtime.deepseek,
                    user_text=text,
                    intent=intent,
                    result=result,
                    conversation=existing_conversation,
                )
            except DeepSeekAPIError as exc:
                llm_meta = self.runtime.deepseek.public_state(mode="error", error=str(exc))
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
        conversation = self.store.append_messages(
            conversation_id,
            user_message,
            assistant_message,
            plan=payload.get("plan"),
            theme=payload.get("theme"),
        )
        self.store.record_analysis(
            source="chat",
            dataset=dataset,
            result=result,
            conversation_id=conversation_id,
        )
        self.send_json({"conversation": conversation, "result": result, "intent": intent, "llm": llm_meta})

    def _infer_intent(self, text: str) -> str:
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

    def _compose_answer(self, intent: str, result: dict) -> str:
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
                "右侧“分析”面板可以查看可行域投影和具体违规项。"
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

    def serve_file(self, file_path: Path) -> None:
        try:
            file_path = file_path.resolve()
            allowed = [STATIC.resolve(), ASSETS.resolve()]
            if not any(str(file_path).startswith(str(base)) for base in allowed):
                raise FileNotFoundError
            if not file_path.exists() or not file_path.is_file():
                raise FileNotFoundError
            body = file_path.read_bytes()
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            if file_path.suffix == ".html":
                content_type = "text/html; charset=utf-8"
            elif file_path.suffix == ".js":
                content_type = "text/javascript; charset=utf-8"
            elif file_path.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_json({"error": "file not found"}, HTTPStatus.NOT_FOUND)


def main() -> None:
    host = os.environ.get("ZHIGOU_HOST", "127.0.0.1")
    port = int(os.environ.get("ZHIGOU_PORT", "8000"))
    runtime = build_runtime()
    server = ZhigouServer((host, port), AppHandler, runtime)
    print(f"知构引擎已启动 http://{host}:{port} ({runtime.deployment_mode}, {runtime.database_mode})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭知构引擎")
    finally:
        server.server_close()
        runtime.close()


if __name__ == "__main__":
    main()
