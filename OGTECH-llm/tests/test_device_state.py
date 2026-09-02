
from __future__ import annotations

import unittest

from _support import ROOT  # noqa: F401

from harness.device_state import estimate_tokens, serialize_device_state
from harness.fake_map import FakeMapClient


class DeviceStateTest(unittest.TestCase):
    def test_fake_device_fits_sixty_tokens(self) -> None:
        text = serialize_device_state(FakeMapClient().device(), 60)
        self.assertLessEqual(estimate_tokens(text), 60)
        self.assertIn("gps=fix", text)
        self.assertIn("sun=", text)
        self.assertIn("co=0ppm", text)

    def test_no_fix_is_stated_not_estimated(self) -> None:
        text = serialize_device_state(FakeMapClient(fix=False).device(), 60)
        self.assertIn("gps=nofix last73s", text)
        self.assertNotIn("lat", text)

    def test_empty_and_truncation(self) -> None:
        self.assertEqual(serialize_device_state(None), "device=unavailable")
        self.assertEqual(serialize_device_state({}), "device=unavailable")
        device = FakeMapClient().device()
        device["navigation"]["active_route"] = {"available": True, "bearing_deg": 292, "distance_m": 231, "eta_min": 4, "target": {"id": "demo-water-ilgam"}}
        device["power"] = {"valid": True, "percent": 83, "days_left": 9.5}
        text = serialize_device_state(device, 20)
        self.assertLessEqual(estimate_tokens(text), 20)
        self.assertTrue(text.startswith("gps=fix"))


if __name__ == "__main__":
    unittest.main()
