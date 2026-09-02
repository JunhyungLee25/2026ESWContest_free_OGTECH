"""DS3231 RTC 텔레메트리와 항법 시각 선택 회귀 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from gps_service import (
    GpsInputError,
    GpsService,
    encode_stm32_telemetry,
    parse_stm32_telemetry,
)
from navigation_service import NavigationService


ROOT = Path(__file__).resolve().parents[1]
NMEA_REPLAY = ROOT / "sample_data" / "air530_replay.nmea"
KST = timezone(timedelta(hours=9))


def _telemetry(*, rtc: dict[str, object], sequence: int = 1) -> str:
    """필수 센서 필드만 채운 CRC 포함 STM32 텔레메트리를 만든다."""

    return encode_stm32_telemetry(
        {
            "v": 1,
            "event": "telemetry",
            "seq": sequence,
            "uptime_ms": sequence * 1000,
            "gps": {
                "fix": True,
                "lat": 37.5465,
                "lon": 127.0757,
                "acc_m": 5.0,
                "sats": 9,
                "age_s": 0,
            },
            "rtc": rtc,
            "env": {"valid": False, "age_s": 0},
            "co": {
                "valid": False,
                "warming_up": False,
                "ppm": None,
                "level": "unknown",
                "alarm": False,
                "age_s": 0,
            },
            "power": None,
        }
    )


class _Registry:
    """일조 계산에 필요한 지도 레지스트리 최소 구현."""

    def overview(self) -> dict[str, object]:
        return {"name": "RTC 회귀 테스트 지도", "source_name": "rtc-test", "demo": False}

    def trail_offset_m(self, lat: float, lon: float) -> float:
        return 0.0


class RtcTelemetryRegressionTest(unittest.TestCase):
    def test_valid_utc_rtc_is_normalized_and_accepted(self) -> None:
        parsed = parse_stm32_telemetry(
            _telemetry(
                rtc={
                    "valid": True,
                    "iso_utc": "2026-08-19T12:34:56Z",
                    "age_s": 0,
                }
            )
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(
            parsed["rtc"],
            {
                "valid": True,
                "iso_utc": "2026-08-19T12:34:56+00:00",
                "age_s": 0.0,
            },
        )

    def test_invalid_date_non_utc_and_false_valid_claim_fail_closed(self) -> None:
        cases = (
            {"valid": True, "iso_utc": "2026-02-30T12:00:00Z", "age_s": 0},
            {"valid": True, "iso_utc": "2026-08-19T21:00:00+09:00", "age_s": 0},
            {"valid": False, "iso_utc": "2026-08-19T12:00:00Z", "age_s": 0},
        )

        for rtc in cases:
            with self.subTest(rtc=rtc), self.assertRaises(GpsInputError):
                parse_stm32_telemetry(_telemetry(rtc=rtc))

    def test_invalid_rtc_line_is_rejected_without_overwriting_previous_good_value(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            service._handle_line(
                _telemetry(
                    rtc={
                        "valid": True,
                        "iso_utc": "2026-08-19T12:34:56Z",
                        "age_s": 0,
                    }
                ),
                mode="stm32",
            )
            service._handle_line(
                _telemetry(
                    rtc={
                        "valid": False,
                        "iso_utc": "2026-08-19T12:34:56Z",
                        "age_s": 0,
                    },
                    sequence=2,
                ),
                mode="stm32",
            )

            snapshot = service.snapshot()
            self.assertEqual(snapshot["rtc"]["iso_utc"], "2026-08-19T12:34:56+00:00")
            self.assertTrue(snapshot["rtc"]["valid"])
            self.assertEqual(snapshot["rejected_lines"], 1)
            self.assertIn("유효하지 않은 STM32 RTC", snapshot["error"])
        finally:
            service.close()

    def test_stale_rtc_is_not_confirmed_or_pass(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            service._handle_line(
                _telemetry(
                    rtc={
                        "valid": True,
                        "iso_utc": "2026-08-19T12:34:56Z",
                        "age_s": 3.1,
                    }
                ),
                mode="stm32",
            )

            rtc = service.snapshot()["rtc"]
            self.assertTrue(rtc["stale"])
            self.assertFalse(rtc["valid"])
        finally:
            service.close()

    def test_stale_rtc_cannot_be_navigation_confirmed(self) -> None:
        gps = GpsService(NMEA_REPLAY)
        temporary = tempfile.TemporaryDirectory()
        navigation = None
        try:
            gps._handle_line(
                _telemetry(
                    rtc={
                        "valid": True,
                        "iso_utc": "2030-01-02T12:00:00Z",
                        "age_s": 3.1,
                    }
                ),
                mode="stm32",
            )
            navigation = NavigationService(
                _Registry(),
                gps,
                Path(temporary.name) / "waypoints.json",
                local_tz=KST,
            )

            result = navigation.snapshot()

            self.assertNotEqual(result["clock"]["source"], "ds3231")
            self.assertFalse(result["clock"]["confirmed"])
        finally:
            if navigation is not None:
                navigation.close()
            gps.close()
            temporary.cleanup()

    def test_without_explicit_now_navigation_uses_ds3231_for_clock_and_solar_date(self) -> None:
        gps = GpsService(NMEA_REPLAY)
        temporary = tempfile.TemporaryDirectory()
        navigation = None
        rtc_iso = "2030-01-02T23:30:00Z"
        try:
            gps._handle_line(
                _telemetry(
                    rtc={"valid": True, "iso_utc": rtc_iso, "age_s": 0}
                ),
                mode="stm32",
            )
            navigation = NavigationService(
                _Registry(),
                gps,
                Path(temporary.name) / "waypoints.json",
                local_tz=KST,
            )

            result = navigation.snapshot()
            expected_local = datetime.fromisoformat(rtc_iso.replace("Z", "+00:00")).astimezone(KST)

            self.assertEqual(result["clock"]["source"], "ds3231")
            self.assertTrue(result["clock"]["confirmed"])
            self.assertEqual(result["clock"]["iso_utc"], rtc_iso.replace("Z", "+00:00"))
            self.assertEqual(result["clock"]["local_iso"], expected_local.isoformat(timespec="seconds"))
            self.assertEqual(result["sun"]["date"], expected_local.date().isoformat())
            self.assertEqual(result["sun"]["now"], expected_local.isoformat(timespec="seconds"))
        finally:
            if navigation is not None:
                navigation.close()
            gps.close()
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
