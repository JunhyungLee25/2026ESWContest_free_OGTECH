from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "jetson"))

from power_control import (  # noqa: E402
    PowerAckConfirmationError,
    PowerButtonDetector,
    button_events,
    perform_poweroff,
)


class PowerControlTest(unittest.TestCase):
    def test_only_long_validated_power_release_is_accepted(self) -> None:
        detector = PowerButtonDetector()
        base = {
            "button": "power",
            "state": "released",
            "held_ms": 2200,
            "coordinates_exposed": False,
        }

        self.assertTrue(detector.accept(base))
        self.assertFalse(detector.accept({**base, "held_ms": 1999}))
        self.assertFalse(detector.accept({**base, "state": "pressed"}))
        self.assertFalse(detector.accept({**base, "button": "voice"}))
        self.assertFalse(detector.accept({**base, "coordinates_exposed": True}))

    def test_external_button_event_url_is_rejected_before_network(self) -> None:
        with self.assertRaises(ValueError):
            next(button_events("http://example.com:8790"))

    @patch("power_control.request_power_cancel")
    @patch("power_control.request_power_ack")
    def test_ack_is_confirmed_before_systemd_poweroff(
        self,
        request_ack,
        request_cancel,
    ) -> None:
        order: list[str] = []
        request_ack.side_effect = lambda _url: (
            order.append("ack")
            or {"state": "shutdown_ack", "gate_on": True}
        )

        def run_command(*_args, **_kwargs):
            self.assertEqual(_args[0], ["/usr/bin/systemctl", "poweroff"])
            order.append("systemctl")

        result = perform_poweroff(
            "http://127.0.0.1:8790",
            "/usr/bin/systemctl",
            run_command=run_command,
        )

        self.assertEqual(order, ["ack", "systemctl"])
        self.assertEqual(result["state"], "shutdown_ack")
        request_cancel.assert_not_called()

    @patch("power_control.request_power_cancel")
    @patch("power_control.request_power_ack")
    def test_systemd_failure_cancels_gate_cut_reservation(
        self,
        request_ack,
        request_cancel,
    ) -> None:
        order: list[str] = []
        request_ack.side_effect = lambda _url: (
            order.append("ack")
            or {"state": "shutdown_ack", "gate_on": True}
        )
        request_cancel.side_effect = lambda _url: (
            order.append("cancel")
            or {"state": "shutdown_cancelled", "gate_on": True}
        )

        def run_command(*_args, **_kwargs):
            order.append("systemctl")
            raise OSError("test systemctl failure")

        with self.assertRaisesRegex(RuntimeError, "차단 예약을 취소"):
            perform_poweroff(
                "http://127.0.0.1:8790",
                "/usr/bin/systemctl",
                run_command=run_command,
            )

        self.assertEqual(order, ["ack", "systemctl", "cancel"])

    @patch("power_control.request_power_cancel")
    @patch("power_control.request_power_ack")
    def test_cancel_protocol_failure_is_reported_as_terminal_power_error(
        self,
        request_ack,
        request_cancel,
    ) -> None:
        request_ack.return_value = {"state": "shutdown_ack", "gate_on": True}
        request_cancel.side_effect = json.JSONDecodeError("bad", "{", 0)

        def run_command(*_args, **_kwargs):
            raise OSError("test systemctl failure")

        with self.assertRaisesRegex(RuntimeError, "모두 실패"):
            perform_poweroff(
                "http://127.0.0.1:8790",
                "/usr/bin/systemctl",
                run_command=run_command,
            )

    @patch("power_control.request_power_cancel")
    @patch("power_control.request_power_ack")
    def test_lost_ack_confirmation_cancels_without_calling_systemd(
        self,
        request_ack,
        request_cancel,
    ) -> None:
        request_ack.side_effect = PowerAckConfirmationError("lost CRC response")
        request_cancel.return_value = {
            "state": "shutdown_cancelled",
            "gate_on": True,
        }
        systemd_called = False

        def run_command(*_args, **_kwargs):
            nonlocal systemd_called
            systemd_called = True

        with self.assertRaisesRegex(RuntimeError, "ACK를 확인하지 못해"):
            perform_poweroff(
                "http://127.0.0.1:8790",
                "/usr/bin/systemctl",
                run_command=run_command,
            )

        self.assertFalse(systemd_called)
        request_cancel.assert_called_once_with("http://127.0.0.1:8790")


if __name__ == "__main__":
    unittest.main()
