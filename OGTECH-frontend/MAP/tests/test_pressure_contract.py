"""BMP390 기압 계측과 추세의 fail-closed 직렬 계약 테스트."""

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


def payload() -> dict[str, object]:
    return {
        "v": 1,
        "event": "telemetry",
        "seq": 2,
        "uptime_ms": 720_000,
        "gps": {"fix": False, "sats": 0, "hdop": None, "acc_m": None, "age_s": None},
        "env": {
            "valid": True,
            "sht_valid": True,
            "pressure_valid": True,
            "temp_c": 18.4,
            "humidity_pct": 62.0,
            "press_hpa": 1004.25,
            "press_trend": "falling",
            "age_s": 0.2,
            "press_age_s": 1.0,
            "bmp_address": 0x77,
        },
        "co": {
            "valid": True,
            "warming_up": False,
            "ppm": 1.2,
            "level": "normal",
            "alarm": False,
            "age_s": 0.2,
        },
        "power": {"valid": False, "percent": None, "days_left": None},
    }


class PressureContractTest(unittest.TestCase):
    def parse(self, value: dict[str, object]) -> dict[str, object]:
        result = parse_stm32_telemetry(encode_stm32_telemetry(value))
        self.assertIsNotNone(result)
        assert result is not None
        return result

    def test_confirmed_bmp390_value_and_address_are_preserved(self) -> None:
        environment = self.parse(payload())["environment"]
        self.assertTrue(environment["pressure_valid"])
        self.assertAlmostEqual(environment["press_hpa"], 1004.25)
        self.assertEqual(environment["press_trend"], "falling")
        self.assertEqual(environment["bmp_address"], 0x77)
        self.assertEqual(environment["press_age_s"], 1.0)

    def test_unconfirmed_pressure_cannot_carry_value_or_trend(self) -> None:
        value = payload()
        env = value["env"]
        assert isinstance(env, dict)
        env["pressure_valid"] = False
        with self.assertRaisesRegex(GpsInputError, "유효하지 않은 STM32 BMP390"):
            self.parse(value)

    def test_confirmed_pressure_requires_a_value(self) -> None:
        value = payload()
        env = value["env"]
        assert isinstance(env, dict)
        env["press_hpa"] = None
        with self.assertRaisesRegex(GpsInputError, "기압값이 없습니다"):
            self.parse(value)

    def test_only_supported_i2c_addresses_are_accepted(self) -> None:
        for address in (0x75, 118.5):
            with self.subTest(address=address):
                value = payload()
                env = value["env"]
                assert isinstance(env, dict)
                env["bmp_address"] = address
                with self.assertRaises(GpsInputError):
                    self.parse(value)

    def test_stale_packet_clears_pressure_confirmation_and_trend(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            service._handle_line(encode_stm32_telemetry(payload()), mode="stm32")
            assert service._last_environment_monotonic is not None
            service._last_environment_monotonic -= 4.0
            environment = service.snapshot()["environment"]
            self.assertFalse(environment["valid"])
            self.assertFalse(environment["sht_valid"])
            self.assertFalse(environment["pressure_valid"])
            self.assertEqual(environment["press_trend"], "unknown")
            # 마지막 관측값은 근거 표시용으로만 남으며 live로 취급되지 않습니다.
            self.assertAlmostEqual(environment["press_hpa"], 1004.25)
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
