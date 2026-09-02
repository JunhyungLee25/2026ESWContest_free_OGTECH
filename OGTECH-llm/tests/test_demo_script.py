
from __future__ import annotations

import json
import sys
import unittest

from _support import ROOT

sys.path.insert(0, str(ROOT / "eval"))

from harness import CONFIG_DIR, build_harness  # noqa: E402
from harness.mock_llm_server import start_in_thread  # noqa: E402
from run_demo_script import run_script  # noqa: E402

SCRIPT = json.loads((CONFIG_DIR / "demo_script.json").read_text(encoding="utf-8"))
UNKNOWN_SPEECH = "요청을 안전하게 분류하지 못했습니다. 지도 상태, 남은 일조 시간, 날씨 추정, 장비 상태처럼 한 가지씩 다시 말해 주세요."


class DemoScriptTest(unittest.TestCase):
    def test_every_demo_line_and_variant_resolves_by_rules_without_llm(self) -> None:
        harness = build_harness(intent_enabled=False, polish_mode="off")
        result = run_script(harness, SCRIPT, llm_available=False)
        self.assertEqual(result["failures"], [])
        rows = {row["id"]: row for row in result["rows"]}
        self.assertEqual(rows["D03"]["speech"], "네, 목적지로 설정되었습니다.")
        self.assertEqual(rows["D10"]["reason"], "classifier_unavailable")
        self.assertEqual(rows["D10"]["speech"], UNKNOWN_SPEECH)
        for row in result["rows"]:
            if row["id"] != "D10":
                self.assertIn(row["stage"], {"rule", "overlay"}, row["id"])

    def test_twenty_runs_are_identical(self) -> None:
        harness = build_harness(intent_enabled=False, polish_mode="off")
        signatures = {json.dumps(run_script(harness, SCRIPT, llm_available=False)["signature"], ensure_ascii=False) for _ in range(20)}
        self.assertEqual(len(signatures), 1)

    def test_llm_required_line_with_mock_and_with_faults(self) -> None:
        for mode, expected_reason in (("ok", "llm_unknown"), ("http500", "classifier_failed_no_retry"), ("garbage", "classifier_failed_no_retry"), ("empty", "schema_validation_failed"), ("timeout", "classifier_failed_no_retry")):
            server = start_in_thread(mode=mode, delay_s=0.6)
            try:
                harness = build_harness(llm_url=server.url, intent_enabled=True, polish_mode="off")
                harness.intent.timeout_s = 0.2
                result = run_script(harness, SCRIPT, llm_available=(mode == "ok"))
                rows = {row["id"]: row for row in result["rows"]}
                self.assertEqual(result["failures"], [], mode)
                self.assertEqual(rows["D10"]["stage"], "llm", mode)
                self.assertEqual(rows["D10"]["reason"], expected_reason, mode)
                self.assertEqual(rows["D10"]["speech"], UNKNOWN_SPEECH, mode)
                self.assertEqual(result["llm_calls_on_canonical"], 0, "시연 대사는 LLM 없이 확정돼야 한다")
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
