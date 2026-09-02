"""Air530 NMEA와 STM32 GET_FIX 입력 계약 테스트."""

from __future__ import annotations

from pathlib import Path
import time
import unittest

from gps_service import (
    GpsConfiguration,
    GpsInputError,
    GpsService,
    NmeaParser,
    encode_stm32_telemetry,
    parse_stm32_button,
    parse_stm32_fix,
    parse_stm32_ogt1,
    parse_stm32_output,
    parse_stm32_power_event,
    parse_stm32_telemetry,
)


ROOT = Path(__file__).resolve().parents[1]
NMEA_REPLAY = ROOT / "sample_data" / "air530_replay.nmea"
# 실장 `$SA1` 필드 모양을 공개 DEMO 좌표로 재구성한 fixture(실측 GPS 좌표는 커밋하지 않는다).
SA1_FIX_BODY = "SA1,4758,4758034,1,287,530,1,0,2,375465126,1270757141,7"


def _xor_frame(body: str) -> str:
    checksum = 0
    for character in body:
        checksum ^= ord(character)
    return f"${body}*{checksum:02X}"


class NmeaParserTest(unittest.TestCase):
    def test_gga_fix_keeps_reported_fields_without_inventing_accuracy(self) -> None:
        result = NmeaParser().parse(
            "$GNGGA,120000.00,3732.7908,N,12704.5428,E,1,10,0.8,35.0,M,0.0,M,,*76"
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["fix"])
        self.assertAlmostEqual(result["lat"], 37.54651333333333)
        self.assertAlmostEqual(result["lon"], 127.07571333333333)
        self.assertEqual(result["satellites"], 10)
        self.assertEqual(result["hdop"], 0.8)
        self.assertIsNone(result["acc_m"])
        self.assertEqual(result["accuracy_kind"], "unknown")

    def test_gga_no_fix_has_no_coordinate(self) -> None:
        result = NmeaParser().parse(
            "$GNGGA,120004.00,,,,,0,00,99.9,,,,,,*46"
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["fix"])
        self.assertNotIn("lat", result)
        self.assertNotIn("lon", result)

    def test_bad_checksum_is_rejected(self) -> None:
        with self.assertRaises(GpsInputError):
            NmeaParser().parse(
                "$GNGGA,120000.00,3732.7908,N,12704.5428,E,1,10,0.8,35.0,M,0.0,M,,*00"
            )


class Stm32ParserTest(unittest.TestCase):
    def test_button_event_requires_crc_and_fixed_enums(self) -> None:
        line = encode_stm32_telemetry(
            {
                "v": 1,
                "event": "button",
                "seq": 7,
                "button": "voice",
                "state": "released",
                "held_ms": 1840,
            }
        )

        self.assertEqual(
            parse_stm32_button(line),
            {
                "version": 1,
                "sequence": 7,
                "button": "voice",
                "state": "released",
                "held_ms": 1840,
            },
        )
        with self.assertRaises(GpsInputError):
            parse_stm32_button(
                encode_stm32_telemetry(
                    {
                        "v": 1,
                        "event": "button",
                        "seq": 7,
                        "button": "shell",
                        "state": "released",
                        "held_ms": 1840,
                    }
                )
            )
        with self.assertRaises(GpsInputError):
            parse_stm32_button(
                '{"v":1,"event":"button","seq":7,"button":"voice",'
                '"state":"released","held_ms":1840}'
            )

    def test_output_ack_requires_crc_and_consistent_level(self) -> None:
        line = encode_stm32_telemetry(
            {
                "v": 1,
                "event": "output",
                "seq": 2,
                "output": "trail",
                "level": "caution",
                "active": True,
                "watchdog_ms": 5000,
            }
        )

        result = parse_stm32_output(line)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["action"], "trail_caution")
        with self.assertRaises(GpsInputError):
            parse_stm32_output(
                encode_stm32_telemetry(
                    {
                        "v": 1,
                        "event": "output",
                        "seq": 3,
                        "output": "trail",
                        "level": "off",
                        "active": True,
                        "watchdog_ms": 5000,
                    }
                )
            )

    def test_power_event_requires_crc_and_consistent_gate_state(self) -> None:
        line = encode_stm32_telemetry(
            {
                "v": 1,
                "event": "power",
                "seq": 4,
                "state": "shutdown_ack",
                "gate_on": True,
                "shutdown_pending": True,
            }
        )

        event = parse_stm32_power_event(line)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["state"], "shutdown_ack")
        cancelled = parse_stm32_power_event(
            encode_stm32_telemetry(
                {
                    "v": 1,
                    "event": "power",
                    "seq": 5,
                    "state": "shutdown_cancelled",
                    "gate_on": True,
                    "shutdown_pending": False,
                }
            )
        )
        self.assertIsNotNone(cancelled)
        assert cancelled is not None
        self.assertEqual(cancelled["state"], "shutdown_cancelled")
        with self.assertRaises(GpsInputError):
            parse_stm32_power_event(
                encode_stm32_telemetry(
                    {
                        "v": 1,
                        "event": "power",
                        "seq": 6,
                        "state": "gate_off",
                        "gate_on": True,
                        "shutdown_pending": False,
                    }
                )
            )

    def test_live_fix_contract(self) -> None:
        result = parse_stm32_fix(
            '{"ok":true,"event":"fix","lat":37.12345,"lon":128.54321,'
            '"acc_m":6.2,"sats":11,"age_s":2}'
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["fix"])
        self.assertEqual(result["acc_m"], 6.2)
        self.assertEqual(result["satellites"], 11)
        self.assertEqual(result["accuracy_kind"], "reported")

    def test_no_fix_contract(self) -> None:
        result = parse_stm32_fix(
            '{"ok":true,"event":"fix","fix":false,"last_age_s":840}'
        )

        self.assertEqual(
            result,
            {"fix": False, "last_age_s": 840.0, "sentence": "STM32_JSON"},
        )

    def test_unrelated_event_is_ignored(self) -> None:
        self.assertIsNone(parse_stm32_fix('{"ok":true,"event":"status"}'))


class Stm32Ogt1ParserTest(unittest.TestCase):
    """현재 실장 펌웨어의 `$SA1`/`$OGT1` CSV+XOR 텔레메트리 계약."""

    def test_sa1_fix_frame_normalizes_like_json_v1(self) -> None:
        result = parse_stm32_ogt1(_xor_frame(SA1_FIX_BODY) + "\r\n")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["protocol"], "ogt1")
        self.assertEqual(result["sequence"], 4758)
        self.assertEqual(result["uptime_ms"], 4758034)
        gps = result["gps"]
        self.assertTrue(gps["fix"])
        self.assertAlmostEqual(gps["lat"], 37.5465126)
        self.assertAlmostEqual(gps["lon"], 127.0757141)
        self.assertEqual(gps["satellites"], 7)
        self.assertIsNone(gps["acc_m"])
        self.assertIsNone(gps["hdop"])
        self.assertEqual(gps["accuracy_kind"], "unknown")
        environment = result["environment"]
        self.assertTrue(environment["valid"])
        self.assertEqual(environment["temp_c"], 28.7)
        self.assertEqual(environment["humidity_pct"], 53.0)
        self.assertFalse(environment["pressure_valid"])
        self.assertIsNone(environment["press_hpa"])
        co = result["co"]
        self.assertTrue(co["valid"])
        self.assertEqual(co["ppm"], 0)
        self.assertFalse(co["warming_up"])
        self.assertEqual(co["level"], "normal")
        self.assertFalse(co["alarm"])
        self.assertFalse(result["rtc"]["valid"])
        self.assertIsNone(result["rtc"]["iso_utc"])
        self.assertFalse(result["power"]["valid"])
        self.assertIsNone(result["power"]["jetson_gate_on"])
        self.assertIsNone(result["power"]["shutdown_pending"])

        # _apply_telemetry 호환: JSON v1 정규화 결과와 키 집합이 같아야 한다.
        v1 = parse_stm32_telemetry(
            encode_stm32_telemetry(
                {
                    "v": 1,
                    "event": "telemetry",
                    "seq": 1,
                    "uptime_ms": 1000,
                    "gps": {"fix": True, "lat": 37.5, "lon": 127.0, "sats": 7},
                    "env": {"valid": True, "temp_c": 28.7, "humidity_pct": 53.0},
                    "co": {"valid": True, "ppm": 0},
                    "power": {"valid": False, "jetson_gate_on": None, "shutdown_pending": None},
                }
            )
        )
        assert v1 is not None
        self.assertEqual(set(result), set(v1))
        for section in ("gps", "rtc", "environment", "co", "power"):
            self.assertEqual(set(result[section]), set(v1[section]), section)

    def test_ogt1_prefix_and_other_prefixes(self) -> None:
        result = parse_stm32_ogt1(_xor_frame("OGT1" + SA1_FIX_BODY[3:]))
        assert result is not None
        self.assertTrue(result["gps"]["fix"])
        self.assertIsNone(parse_stm32_ogt1('{"v":1,"event":"telemetry"}'))
        self.assertIsNone(parse_stm32_ogt1("$GNGGA,120004.00,,,,,0,00,99.9,,,,,,*46"))

    def test_bad_checksum_and_field_count_are_rejected(self) -> None:
        frame = _xor_frame(SA1_FIX_BODY)
        wrong = f"{(int(frame[-2:], 16) ^ 0xFF):02X}"
        with self.assertRaisesRegex(GpsInputError, "체크섬"):
            parse_stm32_ogt1(frame[:-2] + wrong)
        with self.assertRaisesRegex(GpsInputError, "필드 수"):
            parse_stm32_ogt1(_xor_frame("SA1,4758,4758034,1,287,530,1,0,2,375465126,1270757141"))
        with self.assertRaisesRegex(GpsInputError, "gps_state"):
            parse_stm32_ogt1(_xor_frame("SA1,1,1000,1,287,530,1,0,3,0,0,0"))

    def test_no_fix_has_no_coordinates(self) -> None:
        for gps_state in (0, 1):
            result = parse_stm32_ogt1(
                _xor_frame(f"SA1,1,1000,1,287,530,1,0,{gps_state},375465126,1270757141,0")
            )
            assert result is not None
            self.assertFalse(result["gps"]["fix"])
            self.assertNotIn("lat", result["gps"])
            self.assertNotIn("lon", result["gps"])
            self.assertEqual(result["gps"]["satellites"], 0)

    def test_invalid_dht_and_warming_co_have_no_values(self) -> None:
        result = parse_stm32_ogt1(_xor_frame("SA1,1,1000,0,-50,910,0,0,1,0,0,0"))
        assert result is not None
        self.assertFalse(result["environment"]["valid"])
        self.assertIsNone(result["environment"]["temp_c"])
        self.assertIsNone(result["environment"]["humidity_pct"])
        self.assertFalse(result["co"]["valid"])
        self.assertTrue(result["co"]["warming_up"])
        self.assertIsNone(result["co"]["ppm"])
        self.assertEqual(result["co"]["level"], "unknown")
        # 음수 온도는 dht_valid=1일 때 그대로 반영한다.
        cold = parse_stm32_ogt1(_xor_frame("SA1,2,2000,1,-53,910,2,0,1,0,0,0"))
        assert cold is not None
        self.assertEqual(cold["environment"]["temp_c"], -5.3)
        self.assertFalse(cold["co"]["valid"])
        self.assertFalse(cold["co"]["warming_up"])


class GpsServiceTest(unittest.TestCase):
    def test_sa1_csv_line_updates_snapshot_with_ogt1_protocol(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            service._handle_line(_xor_frame(SA1_FIX_BODY), mode="stm32")
            snapshot = service.snapshot()

            self.assertEqual(snapshot["rejected_lines"], 0)
            self.assertIsNone(snapshot["error"])
            self.assertEqual(snapshot["telemetry_protocol"], "ogt1")
            self.assertEqual(snapshot["telemetry_sequence"], 4758)
            self.assertTrue(snapshot["fix"])
            self.assertAlmostEqual(snapshot["lat"], 37.5465126)
            self.assertEqual(snapshot["satellites"], 7)
            self.assertIsNone(snapshot["acc_m"])
            self.assertEqual(snapshot["environment"]["temp_c"], 28.7)
            self.assertTrue(snapshot["environment"]["valid"])
            self.assertEqual(snapshot["co"]["ppm"], 0)
            self.assertTrue(snapshot["co"]["valid"])
            self.assertFalse(snapshot["rtc"]["valid"])
            self.assertFalse(snapshot["power"]["valid"])

            service._handle_line(
                encode_stm32_telemetry(
                    {
                        "v": 1,
                        "event": "telemetry",
                        "seq": 4759,
                        "uptime_ms": 4759034,
                        "gps": {"fix": False},
                        "env": {"valid": False},
                        "co": {"valid": False},
                    }
                ),
                mode="stm32",
            )
            self.assertEqual(service.snapshot()["telemetry_protocol"], "v1")
        finally:
            service.close()

    def test_button_event_is_published_without_coordinate_payload(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            line = encode_stm32_telemetry(
                {
                    "v": 1,
                    "event": "button",
                    "seq": 3,
                    "button": "checkpoint",
                    "state": "released",
                    "held_ms": 320,
                }
            )
            service._handle_line(line, mode="stm32")

            buttons = service.snapshot()["hardware_buttons"]
            self.assertEqual(buttons["event_count"], 1)
            self.assertEqual(buttons["last_event"]["button"], "checkpoint")
            self.assertFalse(buttons["coordinates_accepted"])
            self.assertNotIn("lat", buttons["last_event"])
            self.assertNotIn("lon", buttons["last_event"])
        finally:
            service.close()

    def test_stm32_output_queue_accepts_only_fixed_enum_commands(self) -> None:
        class FakeSerial:
            def __init__(self, *, fail_flush_once: bool = False) -> None:
                self.writes: list[bytes] = []
                self.flushes = 0
                self.fail_flush_once = fail_flush_once

            def write(self, payload: bytes) -> None:
                self.writes.append(payload)

            def flush(self) -> None:
                self.flushes += 1
                if self.fail_flush_once:
                    self.fail_flush_once = False
                    raise OSError("test flush failure")

        service = GpsService(NMEA_REPLAY)
        try:
            self.assertFalse(service.request_stm32_output("trail_alert"))
            with self.assertRaises(GpsInputError):
                service.request_stm32_output("ALERT TRAIL ON\nPOWER OFF")

            with service._lock:  # 테스트 전용: 직렬 스레드 없이 STM32 모드만 설정한다.
                service._configuration = GpsConfiguration(
                    mode="stm32", port="test", baud=115200
                )
                service._state["mode"] = "stm32"
                service._state["connected"] = True
            self.assertTrue(service.request_stm32_output("trail_alert"))
            self.assertTrue(service.request_stm32_output("trail_clear"))

            connection = FakeSerial()
            service._drain_stm32_outputs(connection)
            snapshot = service.snapshot()

            self.assertEqual(
                connection.writes,
                [b"ALERT TRAIL OFF\n"],
            )
            self.assertEqual(connection.flushes, 1)
            self.assertEqual(snapshot["hardware_output"]["last_sent"], "trail_clear")
            self.assertEqual(snapshot["hardware_output"]["sent_count"], 1)
            self.assertFalse(snapshot["hardware_output"]["confirmed"])

            self.assertTrue(service.request_stm32_output("trail_caution"))
            failing = FakeSerial(fail_flush_once=True)
            with self.assertRaises(OSError):
                service._drain_stm32_outputs(failing)
            recovered = FakeSerial()
            service._drain_stm32_outputs(recovered)
            self.assertEqual(recovered.writes, [b"ALERT TRAIL CAUTION\n"])
            self.assertEqual(
                service.snapshot()["hardware_output"]["last_sent"],
                "trail_caution",
            )
            service._handle_line(
                encode_stm32_telemetry(
                    {
                        "v": 1,
                        "event": "output",
                        "seq": 8,
                        "output": "trail",
                        "level": "caution",
                        "active": True,
                        "watchdog_ms": 5000,
                    }
                ),
                mode="stm32",
            )
            self.assertTrue(service.snapshot()["hardware_output"]["confirmed"])

            self.assertTrue(service.request_stm32_output("trail_alert"))
            self.assertFalse(service.request_stm32_power_shutdown_ack())
            service._handle_line(
                encode_stm32_telemetry(
                    {
                        "v": 1,
                        "event": "power",
                        "seq": 9,
                        "state": "shutdown_requested",
                        "gate_on": True,
                        "shutdown_pending": True,
                    }
                ),
                mode="stm32",
            )
            self.assertTrue(service.request_stm32_power_shutdown_ack())
            prioritized = FakeSerial()
            service._drain_stm32_outputs(prioritized)
            service._drain_stm32_outputs(prioritized)
            self.assertEqual(
                prioritized.writes,
                [b"POWER OFF ACK\n", b"ALERT TRAIL ON\n"],
            )
            service._handle_line(
                encode_stm32_telemetry(
                    {
                        "v": 1,
                        "event": "power",
                        "seq": 10,
                        "state": "shutdown_ack",
                        "gate_on": True,
                        "shutdown_pending": True,
                    }
                ),
                mode="stm32",
            )
            self.assertTrue(service.request_stm32_power_shutdown_cancel())
            service._drain_stm32_outputs(prioritized)
            self.assertEqual(prioritized.writes[-1], b"POWER OFF CANCEL\n")
            service._handle_line(
                encode_stm32_telemetry(
                    {
                        "v": 1,
                        "event": "power",
                        "seq": 11,
                        "state": "shutdown_cancelled",
                        "gate_on": True,
                        "shutdown_pending": False,
                    }
                ),
                mode="stm32",
            )
            hardware_power = service.snapshot()["hardware_power"]
            self.assertEqual(hardware_power["transaction_phase"], "idle")
            self.assertFalse(hardware_power["cancel_requested"])
        finally:
            service.close()

    def test_lost_shutdown_ack_event_still_allows_fail_safe_cancel(self) -> None:
        class FakeSerial:
            def __init__(self) -> None:
                self.writes: list[bytes] = []

            def write(self, payload: bytes) -> None:
                self.writes.append(payload)

            def flush(self) -> None:
                pass

        service = GpsService(NMEA_REPLAY)
        try:
            with service._lock:  # 테스트 전용: 직렬 스레드 없이 STM32 모드만 설정한다.
                service._configuration = GpsConfiguration(
                    mode="stm32", port="test", baud=115200
                )
                service._state["mode"] = "stm32"
                service._state["connected"] = True
            service._handle_line(
                encode_stm32_telemetry(
                    {
                        "v": 1,
                        "event": "power",
                        "seq": 13,
                        "state": "shutdown_requested",
                        "gate_on": True,
                        "shutdown_pending": True,
                    }
                ),
                mode="stm32",
            )
            self.assertTrue(service.request_stm32_power_shutdown_ack())
            connection = FakeSerial()
            service._drain_stm32_outputs(connection)

            # ACK write 뒤 CRC shutdown_ack 이벤트만 유실된 상황이다.
            self.assertEqual(
                service.snapshot()["hardware_power"]["transaction_phase"],
                "ack_queued",
            )
            self.assertTrue(service.request_stm32_power_shutdown_cancel())
            service._drain_stm32_outputs(connection)

            self.assertEqual(
                connection.writes,
                [b"POWER OFF ACK\n", b"POWER OFF CANCEL\n"],
            )
        finally:
            service.close()

    def test_newer_non_pending_telemetry_overrides_older_pending_event(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            with service._lock:  # 테스트 전용: 직렬 스레드 없이 상태 순서를 만든다.
                service._configuration = GpsConfiguration(
                    mode="stm32", port="test", baud=115200
                )
                service._state["mode"] = "stm32"
                service._state["connected"] = True
            service._handle_line(
                encode_stm32_telemetry(
                    {
                        "v": 1,
                        "event": "power",
                        "seq": 12,
                        "state": "shutdown_requested",
                        "gate_on": True,
                        "shutdown_pending": True,
                    }
                ),
                mode="stm32",
            )
            with service._lock:
                service._last_power = {
                    "jetson_gate_on": True,
                    "shutdown_pending": False,
                }
                service._last_power_monotonic = time.monotonic()

            self.assertFalse(service.request_stm32_power_shutdown_ack())
        finally:
            service.close()

    def test_replay_publishes_demo_fix(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            service.configure(mode="replay")
            deadline = time.monotonic() + 2.0
            snapshot = service.snapshot()
            while not snapshot["fix"] and time.monotonic() < deadline:
                time.sleep(0.02)
                snapshot = service.snapshot()

            self.assertTrue(snapshot["connected"])
            self.assertTrue(snapshot["demo"])
            self.assertTrue(snapshot["fix"])
            self.assertIsNone(snapshot["acc_m"])
            self.assertEqual(snapshot["satellites"], 10)
        finally:
            service.close()

    def test_stm32_no_fix_preserves_last_coordinate_and_reported_age(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            service._handle_line(  # 테스트 전용: 직렬 장치 없이 계약 입력을 주입한다.
                '{"ok":true,"event":"fix","lat":37.1,"lon":127.1,'
                '"acc_m":5.0,"sats":9,"age_s":1}',
                mode="stm32",
            )
            service._handle_line(
                '{"ok":true,"event":"fix","fix":false,"last_age_s":840}',
                mode="stm32",
            )
            snapshot = service.snapshot()

            self.assertFalse(snapshot["fix"])
            self.assertAlmostEqual(snapshot["last_fix"]["lat"], 37.1)
            self.assertGreaterEqual(snapshot["last_age_s"], 840.0)
        finally:
            service.close()

    def test_rmc_does_not_erase_recent_gga_satellite_metadata(self) -> None:
        service = GpsService(NMEA_REPLAY)
        try:
            service._handle_line(
                "$GNGGA,120000.00,3732.7908,N,12704.5428,E,1,10,0.8,35.0,M,0.0,M,,*76",
                mode="air530",
            )
            rmc_body = "GNRMC,120000.00,A,3732.7908,N,12704.5428,E,0.0,0.0,030826,,,A"
            checksum = 0
            for character in rmc_body:
                checksum ^= ord(character)
            service._handle_line(f"${rmc_body}*{checksum:02X}", mode="air530")
            snapshot = service.snapshot()

            self.assertEqual(snapshot["satellites"], 10)
            self.assertEqual(snapshot["hdop"], 0.8)
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
