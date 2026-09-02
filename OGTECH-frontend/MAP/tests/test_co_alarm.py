"""CO 경보 판정 계약 테스트.

임계·지속 시간은 OGTECH-embedded `Core/Src/co_alarm.c`와 같은 값이어야 한다.
부저를 걷어낸 뒤로 이 판정이 스피커 경보의 유일한 방아쇠다.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from co_alarm import (
    CO_ALARM_PPM,
    CO_CLEAR_HOLD_S,
    CO_CLEAR_PPM,
    CO_WARN_HOLD_S,
    CO_WARN_PPM,
    CoAlarmJudge,
)
from gps_service import GpsService


ROOT = Path(__file__).resolve().parents[1]
NMEA_REPLAY = ROOT / "sample_data" / "air530_replay.nmea"


def _xor_frame(body: str) -> str:
    checksum = 0
    for character in body:
        checksum ^= ord(character)
    return f"${body}*{checksum:02X}"


def _sa1(sequence: int, ppm: int, *, co_state: int = 1) -> str:
    return _xor_frame(
        f"SA1,{sequence},{sequence * 1000},1,287,530,{co_state},{ppm},2,375465126,1270757141,7"
    )


class ThresholdTest(unittest.TestCase):
    def test_thresholds_match_firmware(self) -> None:
        self.assertEqual(
            (CO_WARN_PPM, CO_WARN_HOLD_S, CO_ALARM_PPM, CO_CLEAR_PPM, CO_CLEAR_HOLD_S),
            (35.0, 180.0, 100.0, 30.0, 30.0),
        )


class CoAlarmJudgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.judge = CoAlarmJudge()

    def test_100ppm_alarms_immediately(self) -> None:
        self.assertEqual(self.judge.update(0.0, valid=True, ppm=100), "alarm")

    def test_35ppm_needs_three_minutes(self) -> None:
        self.assertEqual(self.judge.update(0.0, valid=True, ppm=40), "none")
        self.assertEqual(self.judge.update(179.0, valid=True, ppm=40), "none")
        self.assertEqual(self.judge.update(180.0, valid=True, ppm=40), "warning")

    def test_warning_hold_restarts_when_concentration_drops(self) -> None:
        self.judge.update(0.0, valid=True, ppm=40)
        self.judge.update(100.0, valid=True, ppm=10)      # 지속이 끊긴다
        self.assertEqual(self.judge.update(200.0, valid=True, ppm=40), "none")
        self.assertEqual(self.judge.update(380.0, valid=True, ppm=40), "warning")

    def test_alarm_is_latched_while_sensor_input_is_missing(self) -> None:
        self.judge.update(0.0, valid=True, ppm=150)
        for offset in (1.0, 60.0, 600.0):
            self.assertEqual(self.judge.update(offset, valid=False, ppm=None), "alarm")
        # 값이 없는 동안은 해제 지속 시간도 쌓이지 않는다.
        self.assertEqual(self.judge.update(601.0, valid=True, ppm=10), "alarm")
        self.assertEqual(self.judge.update(631.0, valid=True, ppm=10), "none")

    def test_clear_needs_thirty_seconds_below_30ppm(self) -> None:
        self.judge.update(0.0, valid=True, ppm=150)
        self.assertEqual(self.judge.update(10.0, valid=True, ppm=29), "alarm")
        self.assertEqual(self.judge.update(39.0, valid=True, ppm=29), "alarm")
        self.assertEqual(self.judge.update(40.0, valid=True, ppm=29), "none")

    def test_30ppm_does_not_clear(self) -> None:
        self.judge.update(0.0, valid=True, ppm=150)
        self.assertEqual(self.judge.update(600.0, valid=True, ppm=30), "alarm")

    def test_non_numeric_ppm_is_treated_as_missing(self) -> None:
        self.judge.update(0.0, valid=True, ppm=150)
        self.assertEqual(self.judge.update(1.0, valid=True, ppm="n/a"), "alarm")

    def test_reset_drops_the_latched_alarm(self) -> None:
        self.judge.update(0.0, valid=True, ppm=150)
        self.judge.reset()
        self.assertEqual(self.judge.level, "none")


class GpsServiceCoAlarmTest(unittest.TestCase):
    """CSV에 경보 필드가 없어도 스냅샷에는 경보가 실려야 한다."""

    def test_sa1_high_ppm_raises_alarm_in_snapshot(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            service._handle_line(_sa1(1, 0), mode="stm32")
            self.assertFalse(service.snapshot()["co"]["alarm"])
            self.assertEqual(service.snapshot()["co"]["level"], "normal")

            service._handle_line(_sa1(2, 150), mode="stm32")
            co = service.snapshot()["co"]
            self.assertTrue(co["alarm"])
            self.assertEqual(co["level"], "alarm")
            self.assertEqual(co["ppm"], 150)

            # 센서가 예열/단절 상태로 떨어져도 경보는 내려가지 않는다.
            service._handle_line(_sa1(3, 0, co_state=2), mode="stm32")
            self.assertTrue(service.snapshot()["co"]["alarm"])
        finally:
            service.close()

    def test_json_v1_telemetry_keeps_firmware_judgement(self) -> None:
        """JSON v1 펌웨어는 스스로 경보를 보낸다 — Jetson 판정으로 덮어쓰지 않는다."""
        from gps_service import encode_stm32_telemetry

        service = GpsService(NMEA_REPLAY)
        try:
            service._handle_line(
                encode_stm32_telemetry(
                    {
                        "v": 1,
                        "event": "telemetry",
                        "seq": 1,
                        "uptime_ms": 1000,
                        "gps": {"fix": False, "sats": 0},
                        "env": {"valid": False},
                        "co": {
                            "valid": True,
                            "warming_up": False,
                            "ppm": 12.0,
                            "level": "alarm",
                            "alarm": True,
                            "age_s": 0,
                        },
                        "power": {"valid": False},
                    }
                ),
                mode="stm32",
            )
            co = service.snapshot()["co"]
            self.assertTrue(co["alarm"])
            self.assertEqual(co["level"], "alarm")
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
