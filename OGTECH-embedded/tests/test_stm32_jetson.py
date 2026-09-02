"""STM32 송신 코드와 Jetson 수신 코드가 같은 `$SA1` 형식을 쓰는지 확인한다."""

from pathlib import Path
import sys
import unittest


EMBEDDED_ROOT = Path(__file__).resolve().parents[1]
MAP_ROOT = EMBEDDED_ROOT.parent / "OGTECH-frontend" / "MAP"
sys.path.insert(0, str(MAP_ROOT))

from gps_service import parse_stm32_ogt1  # noqa: E402


def xor_frame(body: str) -> str:
    checksum = 0
    for character in body:
        checksum ^= ord(character)
    return f"${body}*{checksum:02X}\r\n"


class Stm32JetsonTest(unittest.TestCase):
    def test_transmitter_uses_sa1_field_order(self):
        source = (EMBEDDED_ROOT / "Core" / "Src" / "jetson_link.c").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '"SA1,%lu,%lu,%u,%d,%u,%u,%u,%u,%ld,%ld,%u"',
            source,
        )

    def test_state_values_match_receiver(self):
        header = (EMBEDDED_ROOT / "Core" / "Inc" / "jetson_link.h").read_text(
            encoding="utf-8"
        )
        for declaration in (
            "JETSON_CO_WARMING_UP = 0",
            "JETSON_CO_VALID = 1",
            "JETSON_CO_STALE = 2",
            "JETSON_GPS_NOT_FOUND = 0",
            "JETSON_GPS_NO_FIX = 1",
            "JETSON_GPS_FIX = 2",
        ):
            self.assertIn(declaration, header)

    def test_receiver_accepts_stm32_frame(self):
        body = "SA1,7,120000,1,241,530,1,0,2,375465126,1270757141,7"
        parsed = parse_stm32_ogt1(xor_frame(body))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["protocol"], "ogt1")
        self.assertTrue(parsed["gps"]["fix"])
        self.assertEqual(parsed["environment"]["temp_c"], 24.1)
        self.assertEqual(parsed["co"]["ppm"], 0)


if __name__ == "__main__":
    unittest.main()
