"""STM32 통합 센서 텔레메트리 계약 테스트."""

from __future__ import annotations

from pathlib import Path
import unittest

from gps_service import (
    GpsInputError,
    GpsService,
    encode_stm32_telemetry,
    parse_stm32_telemetry,
)


ROOT = Path(__file__).resolve().parents[1]
NMEA_REPLAY = ROOT / "sample_data" / "air530_replay.nmea"


def telemetry_payload(*, alarm: bool = False) -> dict[str, object]:
    return {
        "v": 1,
        "event": "telemetry",
        "seq": 17,
        "uptime_ms": 321000,
        "gps": {
            "fix": True,
            "lat": 37.5435,
            "lon": 127.0767,
            "acc_m": 6.0,
            "hdop": 0.9,
            "sats": 11,
            "age_s": 0.2,
        },
        "env": {
            "valid": True,
            "temp_c": 23.45,
            "humidity_pct": 58.2,
            "press_hpa": 1007.4,
            "press_trend": "falling",
            "age_s": 0.1,
        },
        "co": {
            "valid": True,
            "warming_up": False,
            "ppm": 112.0 if alarm else 3.2,
            "level": "alarm" if alarm else "normal",
            "alarm": alarm,
            "age_s": 0.1,
        },
        "power": {"valid": False, "percent": None, "days_left": None},
    }


class TelemetryParserTest(unittest.TestCase):
    def test_crc_telemetry_is_normalized(self) -> None:
        line = encode_stm32_telemetry(telemetry_payload())
        result = parse_stm32_telemetry(line)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["sequence"], 17)
        self.assertTrue(result["gps"]["fix"])
        self.assertEqual(result["gps"]["satellites"], 11)
        self.assertAlmostEqual(result["environment"]["temp_c"], 23.45)
        self.assertAlmostEqual(result["environment"]["press_hpa"], 1007.4)
        self.assertEqual(result["environment"]["press_trend"], "falling")
        self.assertAlmostEqual(result["co"]["ppm"], 3.2)

    def test_single_byte_corruption_is_rejected(self) -> None:
        line = encode_stm32_telemetry(telemetry_payload()).replace("23.45", "24.45")
        with self.assertRaisesRegex(GpsInputError, "CRC16"):
            parse_stm32_telemetry(line)

    def test_alarm_flag_and_level_must_agree(self) -> None:
        payload = telemetry_payload(alarm=True)
        assert isinstance(payload["co"], dict)
        payload["co"]["level"] = "normal"
        with self.assertRaisesRegex(GpsInputError, "alarm과 level"):
            parse_stm32_telemetry(encode_stm32_telemetry(payload))

    def test_unknown_pressure_trend_is_rejected(self) -> None:
        payload = telemetry_payload()
        assert isinstance(payload["env"], dict)
        payload["env"]["press_trend"] = "storm"
        with self.assertRaisesRegex(GpsInputError, "press_trend"):
            parse_stm32_telemetry(encode_stm32_telemetry(payload))


class TelemetryServiceTest(unittest.TestCase):
    def test_sensor_snapshot_keeps_real_values_and_alarm(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            service._handle_line(
                encode_stm32_telemetry(telemetry_payload(alarm=True)),
                mode="stm32",
            )
            snapshot = service.snapshot()

            self.assertTrue(snapshot["fix"])
            self.assertFalse(snapshot["demo"])
            self.assertTrue(snapshot["environment"]["valid"])
            self.assertEqual(snapshot["environment"]["humidity_pct"], 58.2)
            self.assertEqual(snapshot["environment"]["press_hpa"], 1007.4)
            self.assertTrue(snapshot["co"]["alarm"])
            self.assertEqual(snapshot["telemetry_version"], 1)
        finally:
            service.close()

    def test_stale_sensor_is_not_reported_as_live(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            service._handle_line(
                encode_stm32_telemetry(telemetry_payload()),
                mode="stm32",
            )
            assert service._last_environment_monotonic is not None
            service._last_environment_monotonic -= 4.0
            snapshot = service.snapshot()

            self.assertFalse(snapshot["environment"]["valid"])
            self.assertTrue(snapshot["environment"]["stale"])
            self.assertEqual(snapshot["environment"]["temp_c"], 23.45)
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
