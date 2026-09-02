# -*- coding: utf-8 -*-
"""정본 keyword_rules.yaml 회귀 — WORKLOG #25·#26·#27과 daylight `해야` 오탐(2026-08-30 수정분).

하네스가 아니라 정본 RuleRouter를 직접 돈다. backend 사본은 test_vendor_sync가 바이트 일치를 강제한다.
"""

from __future__ import annotations

import unittest

from harness.paths import ensure_co_llm_on_path

ensure_co_llm_on_path()
from ogtech_core import RuleRouter  # noqa: E402


class ProductRulesRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.router = RuleRouter()

    def decide(self, text: str, *, pending: bool = False):
        return self.router.decide(text, pending_confirmation=pending)

    def test_worklog_25_refuse_no_longer_matches_syllable_yak(self) -> None:
        self.assertEqual(
            (self.decide("일몰까지 약 몇 분 남았어").scenario_id, self.decide("일몰까지 약 몇 분 남았어").map_action),
            ("daylight", "status"),
        )
        self.assertEqual(
            (self.decide("약수터까지 얼마나 걸려").scenario_id, self.decide("약수터까지 얼마나 걸려").map_action),
            ("route", "status"),
        )
        self.assertEqual(self.decide("야생동물이 근처에 있는데 괜찮아").scenario_id, "wildlife")
        for text in ("풀숲에서 독사 봤어", "배낭 풀고 쉬어도 괜찮아"):
            self.assertNotEqual(self.decide(text).scenario_id, "refuse", text)

    def test_worklog_25_refuse_still_blocks_edibility_and_dosage(self) -> None:
        for text in (
            "이 버섯 먹어도 돼",
            "약 먹어도 돼",
            "진통제 몇 알 먹어야 해",
            "이 열매 독 있어",
            "독버섯인지 판단해 줘",
            "야생동물 고기를 섭취해도 돼",
        ):
            self.assertEqual(self.decide(text).scenario_id, "refuse", text)

    def test_worklog_26_hypothermia_reaches_warmth_not_sensor_card(self) -> None:
        self.assertEqual(self.decide("지금 너무 추워 저체온증 같아").scenario_id, "warmth")
        self.assertEqual(self.decide("너무 추워서 계속 떨려").scenario_id, "warmth")
        plain = self.decide("지금 여기 온도 얼마야")
        self.assertEqual((plain.scenario_id, plain.map_action), ("weather", "status"))

    def test_worklog_27_pending_yes_does_not_swallow_other_commands(self) -> None:
        basecamp = self.decide("야영지는 여기로 설정해 줘", pending=True)
        self.assertEqual(basecamp.map_action, "save_basecamp")
        prefixed = self.decide("네 야영지는 여기로 설정해 줘", pending=True)
        self.assertEqual(prefixed.map_action, "save_basecamp")
        for text in ("설정 진행해", "네 설정해 줘", "응 그렇게 해 줘", "목적지로 설정해 줘", "네"):
            self.assertEqual(self.decide(text, pending=True).map_action, "confirm_destination", text)
        self.assertEqual(self.decide("네 다른 곳 찾아 줘", pending=True).map_action, "reject_destination")

    def test_daylight_rule_ignores_haeya_haji(self) -> None:
        self.assertNotEqual(self.decide("지금 뭘 해야 하지").scenario_id, "daylight")
        self.assertEqual(self.decide("해 언제 져").scenario_id, "daylight")
        self.assertEqual(self.decide("해 지기 전에 돌아가야 해").scenario_id, "daylight")


if __name__ == "__main__":
    unittest.main()
