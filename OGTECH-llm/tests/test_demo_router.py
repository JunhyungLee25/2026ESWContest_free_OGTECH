
from __future__ import annotations

import json
import unittest

from _support import ROOT, StubLlmClient

from harness import CONFIG_DIR
from harness.demo_router import DemoRouter
from harness.guard import MAP_ACTIONS_LLM
from harness.intent import IntentResolver, load_fewshot
from harness.normalize import load_lexicon

OVERLAY = CONFIG_DIR / "keyword_rules_demo.yaml"
LLM_ONLY_TEXT = "지금 뭐 하면 좋을지 감이 안 와"


def make_router(stub: StubLlmClient | None, *, life_status: bool = False) -> DemoRouter:
    intent = None
    if stub is not None:
        intent = IntentResolver(
            stub,
            system_prompt=(CONFIG_DIR / "system_prompt_ko.txt").read_text(encoding="utf-8"),
            fewshot=load_fewshot(CONFIG_DIR / "fewshot_intent.jsonl"),
            schema=json.loads((CONFIG_DIR / "schema_intent.json").read_text(encoding="utf-8")),
            allow_actions=frozenset(MAP_ACTIONS_LLM),
            allow_life_status_readout=life_status,
        )
    return DemoRouter(overlay_path=OVERLAY, lexicon=load_lexicon(), intent=intent)


class DemoRouterTest(unittest.TestCase):
    def test_rules_win_and_llm_is_not_called(self) -> None:
        stub = StubLlmClient({"scenario_id": "refuse", "action": "none"})
        router = make_router(stub)
        decision = router.resolve("야간 모드 켜 줘")
        self.assertEqual((decision.scenario_id, decision.map_action, decision.source), ("gear", "night_on", "rule"))
        self.assertEqual(stub.calls, [])
        self.assertEqual(router.last_trace["stage"], "rule")

    def test_refuse_never_reaches_llm(self) -> None:
        stub = StubLlmClient({"scenario_id": "food", "action": "none"})
        router = make_router(stub)
        decision = router.resolve("이 버섯 먹어도 돼")
        self.assertEqual((decision.scenario_id, decision.reason), ("refuse", "refuse_priority"))
        self.assertEqual(stub.calls, [])

    def test_lexicon_repairs_before_rules(self) -> None:
        router = make_router(None)
        decision = router.decide("헨트 안에서 번호 켜도 돼")
        self.assertEqual((decision.scenario_id, decision.source), ("sleep_safety", "rule"))
        self.assertEqual(router.last_trace["normalized"], "텐트 안에서 버너 켜도 돼")

    def test_overlay_catches_demo_variant_without_llm(self) -> None:
        stub = StubLlmClient({"scenario_id": "gear", "action": "none"})
        router = make_router(stub)
        decision = router.resolve("야간 모드로 바꿔 줘")
        self.assertEqual((decision.scenario_id, decision.map_action), ("gear", "night_on"))
        self.assertTrue(decision.reason.startswith("demo_overlay:"))
        self.assertEqual(router.last_trace["stage"], "overlay")
        self.assertEqual(stub.calls, [])

    def test_llm_fallback_sets_map_action(self) -> None:
        stub = StubLlmClient({"scenario_id": "gear", "action": "night_on"})
        router = make_router(stub)
        base = DemoRouter(overlay_path=OVERLAY, lexicon=load_lexicon(), intent=None).decide(LLM_ONLY_TEXT)
        self.assertEqual(base.source, "llm_required", "테스트 문장은 규칙·오버레이를 통과해야 한다")
        decision = router.resolve(LLM_ONLY_TEXT)
        self.assertEqual((decision.scenario_id, decision.map_action, decision.source, decision.path), ("gear", "night_on", "llm", "A"))
        self.assertEqual(decision.reason, "validated_llm_map_action")
        self.assertEqual(len(stub.calls), 1)
        last_user = stub.calls[0][-1]
        self.assertEqual(last_user["role"], "user")
        self.assertIn("확인 대기: 아니오", last_user["content"])
        self.assertEqual(stub.calls[0][0]["role"], "system")

    def test_llm_life_label_is_blocked(self) -> None:
        router = make_router(StubLlmClient({"scenario_id": "injury", "action": "none"}))
        decision = router.resolve(LLM_ONLY_TEXT)
        self.assertEqual((decision.scenario_id, decision.path, decision.reason), ("unknown", "B", "llm_life_label_blocked"))

    def test_llm_status_on_life_label_respects_policy(self) -> None:
        blocked = make_router(StubLlmClient({"scenario_id": "daylight", "action": "status"})).resolve(LLM_ONLY_TEXT)
        self.assertEqual((blocked.scenario_id, blocked.reason), ("unknown", "llm_life_label_blocked"))
        opened = make_router(StubLlmClient({"scenario_id": "daylight", "action": "status"}), life_status=True).resolve(LLM_ONLY_TEXT)
        self.assertEqual((opened.scenario_id, opened.map_action), ("daylight", "status"))

    def test_llm_confirm_requires_pending(self) -> None:
        stub = StubLlmClient({"scenario_id": "route", "action": "confirm_destination"})
        router = make_router(stub)
        without = router.resolve(LLM_ONLY_TEXT, pending_confirmation=False)
        self.assertIsNone(without.map_action)
        with_pending = router.resolve(LLM_ONLY_TEXT, pending_confirmation=True)
        self.assertEqual(with_pending.map_action, "confirm_destination")
        self.assertIn("확인 대기: 예", stub.calls[-1][-1]["content"])

    def test_llm_failure_and_garbage_fall_back_without_retry(self) -> None:
        failed = make_router(StubLlmClient(ok=False, error="URLError: connection refused"))
        decision = failed.resolve(LLM_ONLY_TEXT)
        self.assertEqual((decision.scenario_id, decision.source, decision.reason), ("unknown", "fixed", "classifier_failed_no_retry"))
        self.assertEqual(len(failed.intent.client.calls), 1)
        bad_schema = make_router(StubLlmClient({"scenario_id": "route", "action": "fly"}))
        self.assertEqual(bad_schema.resolve(LLM_ONLY_TEXT).reason, "schema_validation_failed")

    def test_without_intent_legacy_classifier_path_is_kept(self) -> None:
        router = make_router(None)
        self.assertEqual(router.resolve(LLM_ONLY_TEXT).reason, "classifier_unavailable")
        decision = router.resolve(LLM_ONLY_TEXT, classifier=lambda _t: ("food", "note"))
        self.assertEqual((decision.scenario_id, decision.source), ("food", "llm"))
        self.assertIsNone(decision.map_action)


if __name__ == "__main__":
    unittest.main()
