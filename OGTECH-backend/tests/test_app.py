# -*- coding: utf-8 -*-
"""백엔드 HTTP 계층 단위 테스트 — 규칙 엔진 계약과 에러 처리를 검증한다."""

import json
import threading
import unittest
from http.client import HTTPConnection

import app as backend_app


class BackendApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = backend_app.build_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, payload=None, raw_body=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = raw_body if raw_body is not None else (
            json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        )
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        conn.close()
        return response.status, data

    def test_health(self):
        status, data = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["engine"], "rule_router")
        self.assertFalse(data["llm"])

    def test_edible_question_is_refused_not_medical(self):
        status, data = self.request("POST", "/api/classify", {"text": "이 버섯 먹어도 되나요"})
        self.assertEqual(status, 200)
        self.assertEqual(data["scenario_id"], "refuse")
        self.assertEqual(data["path"], "B")
        self.assertEqual(data["reason"], "refuse_priority")

    def test_unmatched_text_falls_back_to_unknown_not_a_card(self):
        status, data = self.request("POST", "/api/classify", {"text": "오늘 주식 시장 어때"})
        self.assertEqual(status, 200)
        self.assertEqual(data["scenario_id"], "unknown")
        self.assertEqual(data["path"], "B")
        self.assertEqual(data["reason"], "classifier_unavailable")

    def test_respond_renders_reviewed_card(self):
        status, data = self.request("POST", "/api/respond", {"text": "길을 잃었어"})
        self.assertEqual(status, 200)
        self.assertEqual(data["decision"]["path"], "B")
        self.assertIn("구조 요청 수단이 아닙니다", data["card"]["text"])

    def test_card_endpoint_rejects_unknown_scenario(self):
        status, data = self.request("GET", "/api/card/bleeding")
        self.assertEqual(status, 404)
        self.assertIn("scenario_id", data["error"])

    def test_invalid_json_returns_400_not_connection_drop(self):
        status, data = self.request("POST", "/api/classify", raw_body=b"{broken")
        self.assertEqual(status, 400)
        self.assertIn("JSON", data["error"])

    def test_missing_text_returns_422(self):
        status, data = self.request("POST", "/api/classify", {"voltage": "abc"})
        self.assertEqual(status, 422)
        self.assertIn("text", data["error"])

    def test_unknown_route_returns_404(self):
        status, data = self.request("POST", "/api/emergency", {})
        self.assertEqual(status, 404)
        self.assertIn("경로", data["error"])


if __name__ == "__main__":
    unittest.main()
