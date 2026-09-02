from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from physical_voice import VoiceButtonState, button_events  # noqa: E402
from product_assistant import MapApiError  # noqa: E402


class VoiceButtonStateTest(unittest.TestCase):
    @staticmethod
    def _event(state: str, held_ms: int = 0) -> dict[str, object]:
        return {
            "event_count": 1,
            "button": "voice",
            "state": state,
            "held_ms": held_ms,
            "coordinates_exposed": False,
        }

    def test_pressed_release_pair_runs_once(self) -> None:
        state = VoiceButtonState()

        self.assertEqual(state.accept(self._event("pressed")), "start")
        self.assertIsNone(state.accept(self._event("pressed")))
        self.assertEqual(state.accept(self._event("released", 1200)), "finish")
        self.assertIsNone(state.accept(self._event("released", 1200)))

    def test_short_and_overlong_sessions_are_discarded(self) -> None:
        state = VoiceButtonState()
        state.accept(self._event("pressed"))
        self.assertEqual(state.accept(self._event("released", 100)), "discard")
        state.accept(self._event("pressed"))
        self.assertEqual(state.accept(self._event("released", 16_000)), "discard")

    def test_non_voice_or_coordinate_bearing_event_is_ignored(self) -> None:
        state = VoiceButtonState()
        checkpoint = self._event("pressed")
        checkpoint["button"] = "checkpoint"
        self.assertIsNone(state.accept(checkpoint))
        unsafe = self._event("pressed")
        unsafe["coordinates_exposed"] = True
        self.assertIsNone(state.accept(unsafe))

    def test_button_sse_rejects_non_local_host_before_network(self) -> None:
        with self.assertRaises(MapApiError):
            next(button_events("http://example.com:8790"))


if __name__ == "__main__":
    unittest.main()
