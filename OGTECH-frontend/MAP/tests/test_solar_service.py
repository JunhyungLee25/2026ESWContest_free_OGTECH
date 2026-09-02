"""오프라인 일출·일몰 계산 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from solar_service import calculate_solar_times


class SolarServiceTest(unittest.TestCase):
    def test_seoul_summer_times_are_on_requested_local_date(self) -> None:
        kst = timezone(timedelta(hours=9))
        now = datetime(2026, 8, 9, 12, 0, tzinfo=kst)
        result = calculate_solar_times(37.5665, 126.9780, now=now, local_tz=kst)

        self.assertTrue(result["computed"])
        self.assertEqual(result["sunrise"].date(), now.date())
        self.assertEqual(result["sunset"].date(), now.date())
        self.assertGreaterEqual(result["sunrise"].hour, 4)
        self.assertLessEqual(result["sunrise"].hour, 7)
        self.assertGreaterEqual(result["sunset"].hour, 18)
        self.assertLessEqual(result["sunset"].hour, 21)
        self.assertGreater(result["civil_end"], result["sunset"])

    def test_polar_night_returns_unavailable_events(self) -> None:
        utc = timezone.utc
        result = calculate_solar_times(
            89.0,
            0.0,
            now=datetime(2026, 12, 21, 12, 0, tzinfo=utc),
            local_tz=utc,
        )

        self.assertFalse(result["computed"])
        self.assertIsNone(result["sunrise"])
        self.assertIsNone(result["sunset"])


if __name__ == "__main__":
    unittest.main()
