from __future__ import annotations

import json
import asyncio
import threading
import unittest
import urllib.request
from types import SimpleNamespace

from app import AppHandler, ZhigouServer
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


if __name__ == "__main__":
    unittest.main()
