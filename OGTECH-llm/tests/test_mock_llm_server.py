
from __future__ import annotations

import json
import unittest

from _support import ROOT  # noqa: F401

from harness import CONFIG_DIR
from harness.guard import validate_intent, MAP_ACTIONS_LLM
from harness.llm_client import LlmClient
from harness.mock_llm_server import heuristic_intent, start_in_thread

SCHEMA = json.loads((CONFIG_DIR / "schema_intent.json").read_text(encoding="utf-8"))


class MockServerTest(unittest.TestCase):
    def run_mode(self, mode: str, *, timeout_s: float = 2.0, delay_s: float = 0.0):
        server = start_in_thread(mode=mode, delay_s=delay_s)
        try:
            client = LlmClient(server.url, model="qwen2.5-1.5b-instruct", timeout_s=timeout_s)
            self.assertTrue(client.health())
            messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "발화: 야간 모드 켜 줘\n확인 대기: 아니오"}]
            return client, client.chat_json(messages, SCHEMA, max_tokens=32)
        finally:
            server.shutdown()
            server.server_close()

    def test_ok_mode_roundtrip_and_tokenize(self) -> None:
        client, response = self.run_mode("ok")
        self.assertTrue(response.ok, response.error)
        self.assertEqual(response.content, {"scenario_id": "gear", "action": "night_on"})
        self.assertIn("prompt_tokens", response.usage)

    def test_failure_modes_are_reported_not_raised(self) -> None:
        _, http500 = self.run_mode("http500")
        self.assertFalse(http500.ok)
        self.assertIn("HTTPError", http500.error)
        _, garbage = self.run_mode("garbage")
        self.assertEqual((garbage.ok, garbage.error), (False, "content_not_json"))
        _, empty = self.run_mode("empty")
        self.assertTrue(empty.ok)
        self.assertEqual(validate_intent(empty.content, pending_confirmation=False, allow_actions=frozenset(MAP_ACTIONS_LLM)).reason, "schema_validation_failed")
        _, timeout = self.run_mode("timeout", timeout_s=0.2, delay_s=0.8)
        self.assertFalse(timeout.ok)
        self.assertRegex(timeout.error, "timed out|TimeoutError|timeout")

    def test_client_refuses_non_local_urls(self) -> None:
        with self.assertRaises(ValueError):
            LlmClient("http://example.com/v1/chat/completions", model="x")
        with self.assertRaises(ValueError):
            LlmClient("https://127.0.0.1:8080/v1/chat/completions", model="x")

    def test_heuristic_is_deterministic_for_demo_lines(self) -> None:
        self.assertEqual(heuristic_intent("아 너무 목마른데", False), {"scenario_id": "water", "action": "find_nearest_water"})
        self.assertEqual(heuristic_intent("네", True), {"scenario_id": "route", "action": "confirm_destination"})
        self.assertEqual(heuristic_intent("이 버섯 먹어도 돼", False), {"scenario_id": "refuse", "action": "none"})
        self.assertEqual(heuristic_intent("이 상황에서 무엇을 먼저 살펴보면 좋을까", False), {"scenario_id": "unknown", "action": "none"})


if __name__ == "__main__":
    unittest.main()
