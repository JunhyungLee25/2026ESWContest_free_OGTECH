
from __future__ import annotations

import unittest

from _support import ROOT  # noqa: F401

from harness.guard import MAP_ACTIONS_LLM, compile_forbidden, validate_intent, validate_polish

ALLOW = frozenset(MAP_ACTIONS_LLM)


class IntentGuardTest(unittest.TestCase):
    def verdict(self, payload, *, pending=False, allow=ALLOW, life_status=False):
        return validate_intent(payload, pending_confirmation=pending, allow_actions=allow, allow_life_status_readout=life_status)

    def test_schema_violations_fall_to_fixed_unknown(self) -> None:
        for payload in (None, "route", {}, {"scenario_id": "route"}, {"scenario_id": "route", "action": "fly"},
                        {"scenario_id": "flying", "action": "none"}, {"scenario_id": "route", "action": "none", "lat": 1}):
            verdict = self.verdict(payload)
            self.assertEqual((verdict.scenario_id, verdict.source, verdict.reason), ("unknown", "fixed", "schema_validation_failed"))
            self.assertFalse(verdict.accepted)

    def test_map_action_canonicalizes_scenario(self) -> None:
        verdict = self.verdict({"scenario_id": "lost", "action": "route_basecamp"})
        self.assertEqual((verdict.scenario_id, verdict.map_action, verdict.reason), ("route", "route_basecamp", "validated_llm_map_action"))
        verdict = self.verdict({"scenario_id": "unknown", "action": "find_nearest_water"})
        self.assertEqual((verdict.scenario_id, verdict.map_action), ("water", "find_nearest_water"))
        verdict = self.verdict({"scenario_id": "route", "action": "night_on"})
        self.assertEqual((verdict.scenario_id, verdict.map_action), ("gear", "night_on"))

    def test_confirm_reject_only_when_pending(self) -> None:
        dropped = self.verdict({"scenario_id": "route", "action": "confirm_destination"}, pending=False)
        self.assertIsNone(dropped.map_action)
        self.assertEqual((dropped.scenario_id, dropped.reason), ("route", "validated_llm_label"))
        kept = self.verdict({"scenario_id": "route", "action": "confirm_destination"}, pending=True)
        self.assertEqual(kept.map_action, "confirm_destination")
        rejected = self.verdict({"scenario_id": "route", "action": "reject_destination"}, pending=True)
        self.assertEqual(rejected.map_action, "reject_destination")

    def test_policy_allow_list_drops_actions(self) -> None:
        verdict = self.verdict({"scenario_id": "route", "action": "clear_destination"}, allow=frozenset({"status"}))
        self.assertIsNone(verdict.map_action)
        self.assertEqual(verdict.scenario_id, "route")

    def test_status_on_life_labels_blocked_by_default(self) -> None:
        for label in ("lost", "daylight", "sleep_safety"):
            verdict = self.verdict({"scenario_id": label, "action": "status"})
            self.assertEqual((verdict.scenario_id, verdict.reason), ("unknown", "llm_life_label_blocked"))
            opened = self.verdict({"scenario_id": label, "action": "status"}, life_status=True)
            self.assertEqual((opened.scenario_id, opened.map_action), (label, "status"))
        for label in ("route", "weather", "gear"):
            verdict = self.verdict({"scenario_id": label, "action": "status"})
            self.assertEqual((verdict.scenario_id, verdict.map_action), (label, "status"))

    def test_status_on_non_status_scenario_becomes_plain_label(self) -> None:
        verdict = self.verdict({"scenario_id": "water", "action": "status"})
        self.assertEqual((verdict.scenario_id, verdict.map_action, verdict.reason), ("water", None, "validated_llm_label"))

    def test_life_labels_and_refuse_and_unknown_are_never_promoted(self) -> None:
        for label in ("lost", "daylight", "warmth", "sleep_safety", "injury", "refuse"):
            verdict = self.verdict({"scenario_id": label, "action": "none"})
            self.assertEqual((verdict.scenario_id, verdict.reason), ("unknown", "llm_life_label_blocked"))
        verdict = self.verdict({"scenario_id": "unknown", "action": "none"})
        self.assertEqual(verdict.reason, "llm_unknown")

    def test_low_risk_label_is_accepted_on_path_a(self) -> None:
        verdict = self.verdict({"scenario_id": "wildlife", "action": "none"})
        self.assertEqual((verdict.scenario_id, verdict.path, verdict.source, verdict.reason), ("wildlife", "A", "llm", "validated_llm_label"))

    def test_repeat_response_is_assistant_action(self) -> None:
        verdict = self.verdict({"scenario_id": "route", "action": "repeat_response"})
        self.assertEqual((verdict.scenario_id, verdict.map_action, verdict.assistant_action), ("gear", None, "repeat_response"))


class PolishGuardTest(unittest.TestCase):
    forbidden = compile_forbidden(["먹어도\\s*(된|됩|돼)", "돌아가세요", "[{}\\[\\]<>`]"])
    source = "지도 엔진 계산 방위는 210도입니다. 경로 거리는 40미터입니다. GPS 정확도는 플러스마이너스 4.2미터입니다."

    def test_accepts_faithful_lines(self) -> None:
        verdict = validate_polish({"lines": ["방위 210도, 거리 40미터입니다.", "GPS 정확도는 4.2미터입니다."]}, source_text=self.source, forbidden=self.forbidden)
        self.assertTrue(verdict.ok)
        self.assertEqual(len(verdict.lines), 2)

    def test_rejects_new_numbers(self) -> None:
        verdict = validate_polish({"lines": ["방위 215도입니다.", "거리 40미터입니다."]}, source_text=self.source, forbidden=self.forbidden)
        self.assertFalse(verdict.ok)
        self.assertTrue(verdict.reason.startswith("new_number:215"))

    def test_rejects_forbidden_and_shape(self) -> None:
        self.assertFalse(validate_polish({"lines": ["버섯은 먹어도 됩니다.", "거리 40미터입니다."]}, source_text=self.source, forbidden=self.forbidden).ok)
        self.assertFalse(validate_polish({"lines": ["지금 베이스캠프로 돌아가세요.", "거리 40미터입니다."]}, source_text=self.source, forbidden=self.forbidden).ok)
        self.assertEqual(validate_polish({"lines": ["한 줄뿐"]}, source_text=self.source, forbidden=self.forbidden).reason, "line_count")
        self.assertEqual(validate_polish({"lines": ["a" * 41, "b"]}, source_text=self.source, forbidden=self.forbidden).reason, "line_length")
        self.assertEqual(validate_polish({"text": "x"}, source_text=self.source, forbidden=self.forbidden).reason, "schema_validation_failed")
        self.assertEqual(validate_polish({"lines": ["", "x"]}, source_text=self.source, forbidden=self.forbidden).reason, "line_length")


if __name__ == "__main__":
    unittest.main()
