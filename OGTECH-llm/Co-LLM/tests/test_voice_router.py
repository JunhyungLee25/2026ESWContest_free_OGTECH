from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ogtech_core import CardRenderer, RuleRouter  # noqa: E402


class VoiceRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.router = RuleRouter()
        cls.cards = CardRenderer()
        cls.eval_payload = json.loads(
            (ROOT / "eval" / "voice_cases.json").read_text(encoding="utf-8")
        )

    def test_all_expression_cases_match_expected_contract(self) -> None:
        cases = self.eval_payload["cases"]
        self.assertGreaterEqual(len(cases), 160)
        failures = []
        for case in cases:
            decision = self.router.decide(
                case["text"], pending_confirmation=bool(case.get("pending"))
            )
            actual = (
                decision.scenario_id,
                decision.map_action,
                decision.assistant_action,
            )
            expected = (
                case["scenario_id"],
                case.get("map_action"),
                case.get("assistant_action"),
            )
            if actual != expected:
                failures.append((case["text"], expected, actual, decision.reason))
            if case.get("reason") and decision.reason != case["reason"]:
                failures.append(
                    (case["text"], case["reason"], decision.reason, "reason")
                )
        self.assertEqual(failures, [])

    def test_false_positive_controls_do_not_cross_route(self) -> None:
        blanket = self.router.decide("이불 챙겨왔어야 했는데")
        self.assertIsNone(blanket.map_action)
        self.assertNotEqual(blanket.scenario_id, "water")

        ordinary_food = self.router.decide("먹을 거 다 떨어졌어")
        self.assertEqual(ordinary_food.scenario_id, "food")
        self.assertNotEqual(ordinary_food.scenario_id, "refuse")

        water_safety = self.router.decide("이 물 마셔도 돼")
        self.assertEqual(water_safety.scenario_id, "water")
        self.assertIsNone(water_safety.map_action)

    def test_llm_cannot_promote_missed_life_query_to_life_path(self) -> None:
        decision = self.router.resolve(
            "무슨 일인지 잘 모르겠어", classifier=lambda _text: "injury"
        )
        self.assertEqual(decision.scenario_id, "unknown")
        self.assertEqual(decision.path, "B")
        self.assertEqual(decision.reason, "llm_life_label_blocked")

    def test_route_card_reads_only_code_values_and_marks_demo(self) -> None:
        device = {
            "demo": True,
            "gps": {"fix": True, "acc_m": 4.2, "satellites": 9},
            "navigation": {
                "active_route": {
                    "available": True,
                    "bearing_deg": 210,
                    "distance_m": 40,
                }
            },
        }
        rendered = self.cards.render("route", device)
        self.assertTrue(rendered.demo)
        self.assertIn("데모 값", rendered.text)
        self.assertIn("210도", rendered.text)
        self.assertIn("40미터", rendered.text)
        self.assertIn("4.2미터", rendered.text)

    def test_no_fix_card_never_invents_current_position(self) -> None:
        rendered = self.cards.render(
            "lost",
            {
                "demo": False,
                "gps": {
                    "fix": False,
                    "last_fix": {"lat": 37.5, "lon": 127.0},
                    "last_age_s": 73,
                },
            },
        )
        self.assertIn("현재 GPS가 미수신", rendered.text)
        self.assertIn("73초 전", rendered.text)
        self.assertIn("추정하지 않", rendered.text)

    def test_weather_card_reads_pressure_as_local_estimate(self) -> None:
        rendered = self.cards.render(
            "weather",
            {
                "environment": {
                    "valid": True,
                    "temp_c": 23.4,
                    "humidity_pct": 58.2,
                    "press_hpa": 1007.4,
                    "press_trend": "falling",
                }
            },
        )

        self.assertIn("1007.4헥토파스칼", rendered.text)
        self.assertIn("하강", rendered.text)
        self.assertIn("국지 추정", rendered.text)

    def test_daylight_after_sunset_never_says_negative_minutes_remaining(self) -> None:
        rendered = self.cards.render(
            "daylight",
            {
                "sun": {
                    "computed": True,
                    "reference": "current_fix",
                    "remaining_min": -107,
                    "return_by_clock": "18:20",
                    "level": "danger",
                }
            },
        )

        self.assertIn("일몰 후 107분 지났습니다", rendered.text)
        self.assertNotIn("-107분 남았습니다", rendered.text)

    def test_trail_status_without_accuracy_is_not_spoken_as_certain(self) -> None:
        rendered = self.cards.render(
            "route",
            {
                "gps": {"fix": True, "acc_m": None},
                "trail": {"status": "accuracy_unknown", "offset_m": 11.2},
                "navigation": {"active_route": {"available": False}},
            },
        )

        self.assertIn("11미터", rendered.text)
        self.assertIn("정확도가 없어", rendered.text)
        self.assertIn("확정할 수 없습니다", rendered.text)

    def test_route_without_accuracy_omits_numeric_unit(self) -> None:
        rendered = self.cards.render(
            "route",
            {
                "gps": {"fix": True, "acc_m": None},
                "navigation": {
                    "active_route": {
                        "available": True,
                        "bearing_deg": 307,
                        "distance_m": 934,
                    }
                },
            },
        )

        self.assertIn("GPS 정확도는 확인할 수 없습니다", rendered.text)
        self.assertNotIn("확인 불가미터", rendered.text)


if __name__ == "__main__":
    unittest.main()
