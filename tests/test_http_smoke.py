from __future__ import annotations

import json
import asyncio
import os
import threading
import unittest
import urllib.request
from types import SimpleNamespace
from unittest.mock import patch

from app import AppHandler, ZhigouServer
import backend.server as fastapi_server
from backend.server import bootstrap as fastapi_bootstrap


class _FakeStore:
    def __init__(self) -> None:
        self.analysis_count = 0
        self.report_count = 0

    def list_conversations(self):
        return []

    def get_conversation(self, _conversation_id):
        return None

    def record_analysis(self, **_kwargs):
        self.analysis_count += 1

    def record_report(self, **_kwargs):
        self.report_count += 1


class _FakeDeepSeek:
    enabled = False

    def public_state(self, **extra):
        return {"enabled": False, **extra}


class _JsonRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def body(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class HttpSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.store = _FakeStore()
        runtime = SimpleNamespace(
            store=cls.store,
            deepseek=_FakeDeepSeek(),
            deployment_mode="test",
            database_mode="fake",
        )
        cls.server = ZhigouServer(("127.0.0.1", 0), AppHandler, runtime)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_bootstrap_endpoint(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/api/bootstrap", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["deployment_mode"], "test")
        self.assertIn("hypothesis_ranking", payload)

    def test_analyze_endpoint(self) -> None:
        body = json.dumps(
            {
                "dataset": {
                    "data": [
                        {"temperature": 40, "conversion": 0.16},
                        {"temperature": 60, "conversion": 0.28},
                        {"temperature": 80, "conversion": 0.41},
                        {"temperature": 100, "conversion": 0.54},
                        {"temperature": 120, "conversion": 0.64},
                        {"temperature": 140, "conversion": 0.71},
                    ],
                    "config": {"bootstrap_samples": 12, "symbolic_candidate_limit": 3},
                }
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/analyze",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertIn("evidence_gates", payload)
        self.assertIn("experiment_design", payload)
        self.assertGreaterEqual(self.store.analysis_count, 1)

    def test_fastapi_bootstrap_has_the_same_decision_capabilities(self) -> None:
        payload = asyncio.run(fastapi_bootstrap())
        self.assertIn("evidence_gates", payload)
        self.assertIn("open_set_decision", payload)
        self.assertIn("experiment_design", payload)

    def test_fastapi_analysis_works_without_external_database(self) -> None:
        dataset = {
            "data": [
                {"temperature": 40, "conversion": 0.16},
                {"temperature": 60, "conversion": 0.28},
                {"temperature": 80, "conversion": 0.41},
                {"temperature": 100, "conversion": 0.54},
            ],
            "config": {"bootstrap_samples": 12, "symbolic_candidate_limit": 2},
        }
        previous_runtime = fastapi_server._runtime
        try:
            with patch.dict(
                os.environ,
                {
                    "ZHIGOU_DATABASE_URL": "",
                    "ZHIGOU_REQUIRE_EXTERNAL_DB": "false",
                    "ZHIGOU_REQUIRE_DEEPSEEK": "false",
                    "DEEPSEEK_API_KEY": "",
                },
            ):
                fastapi_server._runtime = None
                response = asyncio.run(fastapi_server.analyze(_JsonRequest({"dataset": dataset})))
                payload = json.loads(response.body.decode("utf-8"))
                self.assertEqual(payload["summary"]["sample_count"], 4)
                self.assertEqual(fastapi_server._get_runtime().database_mode, "ephemeral")
        finally:
            fastapi_server._runtime = previous_runtime

    def test_fastapi_chat_keeps_working_in_ephemeral_mode(self) -> None:
        previous_runtime = fastapi_server._runtime
        try:
            with patch.dict(
                os.environ,
                {
                    "ZHIGOU_DATABASE_URL": "",
                    "ZHIGOU_REQUIRE_EXTERNAL_DB": "false",
                    "ZHIGOU_REQUIRE_DEEPSEEK": "false",
                    "DEEPSEEK_API_KEY": "",
                },
            ):
                fastapi_server._runtime = None
                response = asyncio.run(
                    fastapi_server.chat(
                        _JsonRequest(
                            {
                                "message": "解释当前拟合模型",
                                "history": [{"role": "user", "content": "先分析示例数据"}],
                            }
                        )
                    )
                )
                payload = json.loads(response.body.decode("utf-8"))
                self.assertEqual(payload["llm"]["mode"], "local_fallback")
                self.assertGreaterEqual(len(payload["conversation"]["messages"]), 3)
        finally:
            fastapi_server._runtime = previous_runtime


if __name__ == "__main__":
    unittest.main()
