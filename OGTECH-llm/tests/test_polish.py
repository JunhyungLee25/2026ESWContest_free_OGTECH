
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from _support import ROOT, StubLlmClient

from harness import CONFIG_DIR, DemoAssistant
from harness.demo_router import DemoRouter
from harness.fake_map import FakeMapClient
from harness.normalize import load_lexicon
from harness.polish import Polisher


def make_polisher(stub, mode, log: Path | None = None) -> Polisher:
    return Polisher(
        stub,
        mode=mode,
        system_prompt=(CONFIG_DIR / "system_prompt_polish_ko.txt").read_text(encoding="utf-8"),
        schema=json.loads((CONFIG_DIR / "schema_polish.json").read_text(encoding="utf-8")),
        forbidden=json.loads((CONFIG_DIR / "polish_forbidden.json").read_text(encoding="utf-8"))["patterns"],
        shadow_log=log,
    )


class PolishTest(unittest.TestCase):
    card = "장치는 야생동물의 위치나 행동을 확정할 수 없습니다. 거리를 두고 현장 표지와 검수된 야생동물 카드를 확인하세요."

    def test_shadow_logs_but_never_speaks(self) -> None:
        stub = StubLlmClient({"lines": ["야생동물 위치는 확정할 수 없습니다.", "거리를 두고 현장 표지를 확인하세요."]})
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "shadow.jsonl"
            result = make_polisher(stub, "shadow", log).polish(self.card, device=FakeMapClient().device(), scenario_id="wildlife")
            self.assertEqual(result.reason, "ok")
            self.assertIsNotNone(result.lines)
            self.assertIsNone(result.spoken_lines)
            entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(entry["scenario_id"], "wildlife")
            self.assertIn("gps=fix", entry["device_state"])

    def test_speak_mode_replaces_only_when_guard_passes(self) -> None:
        good = StubLlmClient({"lines": ["야생동물 위치는 확정할 수 없습니다.", "거리를 두고 현장 표지를 확인하세요."]})
        self.assertIsNotNone(make_polisher(good, "speak").polish(self.card, device=None, scenario_id="wildlife").spoken_lines)
        bad = StubLlmClient({"lines": ["야생동물은 먹어도 됩니다.", "거리를 두세요."]})
        result = make_polisher(bad, "speak").polish(self.card, device=None, scenario_id="wildlife")
        self.assertIsNone(result.spoken_lines)
        self.assertTrue(result.reason.startswith("forbidden:"))
        numbers = StubLlmClient({"lines": ["동물까지 50미터입니다.", "거리를 두세요."]})
        self.assertTrue(make_polisher(numbers, "speak").polish(self.card, device=None, scenario_id="wildlife").reason.startswith("new_number"))
        failed = StubLlmClient(ok=False, error="timeout")
        self.assertTrue(make_polisher(failed, "speak").polish(self.card, device=None, scenario_id="wildlife").reason.startswith("llm_error"))

    def test_off_mode_does_not_call_llm(self) -> None:
        stub = StubLlmClient({"lines": ["a", "b"]})
        result = make_polisher(stub, "off").polish(self.card, device=None, scenario_id="wildlife")
        self.assertEqual(result.reason, "polish_off")
        self.assertEqual(stub.calls, [])

    def test_assistant_polishes_only_path_a_cards(self) -> None:
        stub = StubLlmClient({"lines": ["야생동물 위치는 확정할 수 없습니다.", "거리를 두고 현장 표지를 확인하세요."]})
        router = DemoRouter(overlay_path=CONFIG_DIR / "keyword_rules_demo.yaml", lexicon=load_lexicon(), intent=None)
        assistant = DemoAssistant(FakeMapClient(), router=router, polisher=make_polisher(stub, "speak"))
        wildlife = assistant.handle_text("멧돼지 소리가 들려")
        self.assertEqual(wildlife.speech, "야생동물 위치는 확정할 수 없습니다. 거리를 두고 현장 표지를 확인하세요.")
        self.assertEqual(wildlife.source_id, "SAFE-WILDLIFE-001")
        calls_after_wildlife = len(stub.calls)
        lost = assistant.handle_text("길을 잃은 것 같아")
        self.assertEqual((lost.decision.scenario_id, lost.decision.path), ("lost", "B"))
        self.assertTrue(lost.speech.startswith("GPS가 현재 위치를 수신 중입니다."), lost.speech)
        self.assertEqual(lost.source_id, "SAFE-LOST-001")
        night = assistant.handle_text("야간 모드 켜 줘")
        self.assertEqual(night.speech, "야간 모드를 켰습니다.")
        self.assertEqual(len(stub.calls), calls_after_wildlife, "생명 카드·지도 명령은 다듬지 않는다")


if __name__ == "__main__":
    unittest.main()
