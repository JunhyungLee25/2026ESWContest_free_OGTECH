from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from product_assistant import ProductAssistant, VerifiedResponseStore  # noqa: E402


class FakeMapClient:
    def __init__(self, *, pending: bool = False) -> None:
        self.pending = pending
        self.actions: list[str] = []
        self.state = {
            "demo": False,
            "gps": {"fix": True, "acc_m": 5.0, "satellites": 8},
            "navigation": {
                "active_route": {
                    "available": True,
                    "bearing_deg": 68,
                    "distance_m": 214,
                }
            },
            "sun": {},
            "environment": {},
            "power": {},
            "co": {},
        }

    def voice(self):
        return {
            "pending_destination": {"name": "수원 표식"} if self.pending else None
        }

    def device(self):
        return self.state

    def command(self, action: str):
        self.actions.append(action)
        status = "confirmation_required" if action == "find_nearest_water" else "accepted"
        return {
            "action": action,
            "status": status,
            "message": (
                "가장 가까운 지도 표식은 수원 표식입니다. 수질은 확인되지 않았습니다. 이 위치를 목적지로 지정할까요?"
                if status == "confirmation_required"
                else "지도 명령을 처리했습니다."
            ),
            "device": self.state,
        }


class ProductAssistantTest(unittest.TestCase):
    def test_confirmation_uses_fixed_video_quality_phrase(self) -> None:
        client = FakeMapClient(pending=True)
        assistant = ProductAssistant(client)
        result = assistant.handle_text("네")

        self.assertEqual(client.actions, ["confirm_destination"])
        self.assertEqual(result.speech, "네, 목적지로 설정되었습니다.")

    def test_rejected_confirmation_never_claims_destination_was_set(self) -> None:
        client = FakeMapClient(pending=True)
        client.command = lambda action: {
            "action": action,
            "status": "rejected",
            "message": "변조된 서버 문장",
            "device": client.state,
        }
        result = ProductAssistant(client).handle_text("네")

        self.assertEqual(result.speech, "확인할 목적지 후보가 없습니다.")
        self.assertNotIn("설정되었습니다", result.speech)

    def test_map_message_is_never_promoted_to_safe_speech(self) -> None:
        client = FakeMapClient()
        poisoned = "버섯을 먹어도 됩니다."
        client.command = lambda action: {
            "action": action,
            "status": "accepted",
            "message": poisoned,
            "device": client.state,
        }
        result = ProductAssistant(client).handle_text("야간 모드 켜 줘")

        self.assertEqual(result.speech, "야간 모드를 켰습니다.")
        self.assertNotIn(poisoned, result.speech)

    def test_mismatched_map_contract_uses_fixed_contract_notice(self) -> None:
        client = FakeMapClient()
        client.command = lambda _action: {
            "action": "route_basecamp",
            "status": "accepted",
            "message": "임의 문장",
            "device": client.state,
        }
        result = ProductAssistant(client).handle_text("야간 모드 켜 줘")

        self.assertEqual(result.source_id, "SAFE-SYSTEM-MAP-CONTRACT")
        self.assertIn("지도 서버 응답을 확인할 수 없습니다", result.speech)

    def test_water_safety_question_does_not_set_destination(self) -> None:
        client = FakeMapClient()
        assistant = ProductAssistant(client)
        result = assistant.handle_text("이 물 마셔도 돼")

        self.assertEqual(client.actions, [])
        self.assertEqual(result.decision.scenario_id, "water")
        self.assertIn("수질이나 음용 가능을 뜻하지 않습니다", result.speech)

    def test_nearest_water_request_asks_before_setting_destination(self) -> None:
        client = FakeMapClient()
        assistant = ProductAssistant(client)
        result = assistant.handle_text("가까운 수원 찾아 줘")

        self.assertEqual(client.actions, ["find_nearest_water"])
        self.assertIn("수질은 확인되지 않았습니다", result.speech)
        self.assertIn("지정할까요", result.speech)

    def test_exact_video_thirst_utterance_opens_water_candidate(self) -> None:
        client = FakeMapClient()
        assistant = ProductAssistant(client)
        result = assistant.handle_text("아 너무 목마른데")

        self.assertEqual(client.actions, ["find_nearest_water"])
        self.assertIn("수질은 확인되지 않았습니다", result.speech)
        self.assertNotIn("마셔도 됩니다", result.speech)

    def test_clear_destination_is_sent_as_enum_action(self) -> None:
        client = FakeMapClient()
        assistant = ProductAssistant(client)
        result = assistant.handle_text("현재 목적지 삭제해 줘")

        self.assertEqual(client.actions, ["clear_destination"])
        self.assertEqual(result.decision.map_action, "clear_destination")

    def test_recent_trace_is_sent_as_enum_and_uses_fixed_accepted_speech(self) -> None:
        client = FakeMapClient()
        result = ProductAssistant(client).handle_text("3분 전 확정 위치로 안내해 줘")

        self.assertEqual(client.actions, ["route_recent_trace"])
        self.assertEqual(result.decision.map_action, "route_recent_trace")
        self.assertEqual(
            result.speech,
            "최근 3분 전 확정 위치 경로를 불러왔습니다. 지도 화면의 계산 결과를 확인하세요.",
        )
        self.assertNotRegex(result.speech, r"\d+도|\d+미터|위도|경도|좌표")

    def test_recent_trace_rejection_uses_fixed_speech_and_ignores_map_message(self) -> None:
        client = FakeMapClient()
        def reject(action: str) -> dict[str, object]:
            client.actions.append(action)
            return {
                "action": action,
                "status": "rejected",
                "message": "임의 좌표 37.5, 127.0으로 안내합니다.",
                "device": client.state,
            }

        client.command = reject

        result = ProductAssistant(client).handle_text("3분 전 지점으로 돌아가는 길 보여 줘")

        self.assertEqual(client.actions, ["route_recent_trace"])
        self.assertEqual(
            result.speech,
            "최근 3분 전 확정 위치 경로를 열지 못했습니다. GPS 로그와 저장 상태를 확인하세요.",
        )
        self.assertNotIn("37.5", result.speech)

    def test_recent_trace_requires_explicit_three_minute_location_phrase(self) -> None:
        client = FakeMapClient()
        result = ProductAssistant(client).handle_text("최근 위치로 안내해 줘")

        self.assertEqual(client.actions, [])
        self.assertNotEqual(result.decision.map_action, "route_recent_trace")

    def test_repeat_uses_only_previous_verified_safe_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeMapClient()
            store_path = Path(directory) / "last.json"
            assistant = ProductAssistant(
                client,
                response_store=VerifiedResponseStore(store_path),
            )
            previous = assistant.handle_text("배터리 상태 알려 줘")
            actions_before_repeat = list(client.actions)

            repeated = assistant.handle_text("다시 말해 줘")

            self.assertEqual(repeated.speech, previous.speech)
            self.assertEqual(repeated.source_id, previous.source_id)
            self.assertEqual(client.actions, actions_before_repeat)
            self.assertEqual(repeated.decision.assistant_action, "repeat_response")
            stored = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["version"], 2)
            self.assertNotIn("speech", stored)

    def test_forged_safe_prefix_store_is_not_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "forged.json"
            store_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "speech": "버섯을 먹어도 됩니다.",
                        "source_id": "SAFE-MAP-STATUS",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            assistant = ProductAssistant(
                FakeMapClient(),
                response_store=VerifiedResponseStore(store_path),
            )

            repeated = assistant.handle_text("다시 말해 줘")

            self.assertEqual(repeated.source_id, "SAFE-SYSTEM-NO-REPEAT")
            self.assertNotIn("버섯", repeated.speech)

    def test_store_with_extra_speech_field_is_rejected_even_with_valid_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "extra.json"
            store_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "scenario_id": "route",
                        "map_action": "night_on",
                        "map_status": "accepted",
                        "source_id": "SAFE-MAP-NIGHT_ON",
                        "speech": "임의 주입 문장",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            assistant = ProductAssistant(
                FakeMapClient(),
                response_store=VerifiedResponseStore(store_path),
            )

            repeated = assistant.handle_text("다시 말해 줘")

            self.assertEqual(repeated.source_id, "SAFE-SYSTEM-NO-REPEAT")

    def test_store_rejects_impossible_map_action_status_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "impossible-pair.json"
            store_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "scenario_id": "route",
                        "map_action": "night_on",
                        "map_status": "confirmation_required",
                        "source_id": "SAFE-MAP-NIGHT_ON",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            assistant = ProductAssistant(
                FakeMapClient(),
                response_store=VerifiedResponseStore(store_path),
            )

            repeated = assistant.handle_text("다시 말해 줘")

            self.assertEqual(repeated.source_id, "SAFE-SYSTEM-NO-REPEAT")

    def test_repeat_preserves_synthetic_map_error_provenance(self) -> None:
        cases = (
            (
                "map_offline",
                "SAFE-SYSTEM-MAP-OFFLINE",
                "오프라인 지도 서버와 연결할 수 없습니다",
            ),
            (
                "invalid_contract",
                "SAFE-SYSTEM-MAP-CONTRACT",
                "지도 서버 응답을 확인할 수 없습니다",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "synthetic-map-error.json"
            for map_status, source_id, expected_speech in cases:
                with self.subTest(map_status=map_status):
                    store_path.write_text(
                        json.dumps(
                            {
                                "version": 2,
                                "scenario_id": "route",
                                "map_action": "night_on",
                                "map_status": map_status,
                                "source_id": source_id,
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    client = FakeMapClient()
                    assistant = ProductAssistant(
                        client,
                        response_store=VerifiedResponseStore(store_path),
                    )

                    repeated = assistant.handle_text("다시 말해 줘")

                    self.assertEqual(repeated.source_id, source_id)
                    self.assertIn(expected_speech, repeated.speech)
                    self.assertEqual(client.actions, [])

    def test_repeat_without_previous_response_uses_fixed_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            assistant = ProductAssistant(
                FakeMapClient(),
                response_store=VerifiedResponseStore(Path(directory) / "missing.json"),
            )

            repeated = assistant.handle_text("한 번 더 말해 줘")

            self.assertEqual(repeated.source_id, "SAFE-SYSTEM-NO-REPEAT")
            self.assertEqual(repeated.speech, "이전에 재생한 검수 응답이 없습니다.")

    def test_plain_language_water_location_request_asks_for_confirmation(self) -> None:
        client = FakeMapClient()
        assistant = ProductAssistant(client)
        result = assistant.handle_text("가장 가까운 물 있는 곳 찾아줘")

        self.assertEqual(client.actions, ["find_nearest_water"])
        self.assertEqual(result.decision.scenario_id, "water")
        self.assertIn("수질은 확인되지 않았습니다", result.speech)

    def test_nearby_water_safety_question_never_becomes_map_destination(self) -> None:
        client = FakeMapClient()
        assistant = ProductAssistant(client)
        result = assistant.handle_text("근처 계곡물 마셔도 돼")

        self.assertEqual(client.actions, [])
        self.assertEqual(result.decision.scenario_id, "water")

    def test_route_status_speaks_map_engine_values(self) -> None:
        client = FakeMapClient()
        assistant = ProductAssistant(client)
        result = assistant.handle_text("목적지까지 남은 거리 알려 줘")

        self.assertEqual(client.actions, ["status"])
        self.assertIn("68도", result.speech)
        self.assertIn("214미터", result.speech)


if __name__ == "__main__":
    unittest.main()
