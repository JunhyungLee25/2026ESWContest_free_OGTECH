"""저장 지점·경로·귀환 시각 조립 계약 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gps_service import GpsConfiguration, GpsService, encode_stm32_telemetry
from navigation_service import (
    NavigationInputError,
    NavigationService,
    _polyline_progress,
)
from position_history import PositionHistoryStore


ROOT = Path(__file__).resolve().parents[1]
NMEA_REPLAY = ROOT / "sample_data" / "air530_replay.nmea"


class FakeRoute:
    coordinates = (
        (127.0757, 37.5465),
        (127.0758, 37.5466),
        (127.0760, 37.5470),
    )
    distance_m = 480.0


class FakeRegistry:
    def overview(self) -> dict[str, object]:
        return {"name": "테스트 보행 지도", "source_name": "test.graphml", "demo": False}

    def trail_offset_m(self, lat: float, lon: float) -> float:
        return 8.0

    def route_between(
        self,
        start_lat: float,
        start_lon: float,
        goal_lat: float,
        goal_lon: float,
    ) -> FakeRoute:
        return FakeRoute()

    def nearest_poi(self, kind: str, lat: float, lon: float) -> dict[str, object] | None:
        if kind != "water_source":
            return None
        return {
            "id": "water-test",
            "kind": "water_source",
            "name": "테스트 수원 표식",
            "lat": 37.5470,
            "lon": 127.0760,
            "potable": "unknown",
            "demo": False,
        }


class FarFromTrailRegistry(FakeRegistry):
    def trail_offset_m(self, lat: float, lon: float) -> float:
        return 72.0


class NavigationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.gps = GpsService(NMEA_REPLAY)
        self.gps._handle_line(
            '{"ok":true,"event":"fix","lat":37.5465,"lon":127.0757,'
            '"acc_m":5.0,"sats":9,"age_s":0}',
            mode="stm32",
        )
        self.service = NavigationService(
            FakeRegistry(),
            self.gps,
            Path(self.temporary.name) / "waypoints.json",
            local_tz=timezone(timedelta(hours=9)),
        )

    def tearDown(self) -> None:
        self.service.close()
        self.gps.close()
        self.temporary.cleanup()

    def test_basecamp_save_enables_code_computed_return_time(self) -> None:
        result = self.service.apply_waypoint({"action": "save_current", "kind": "basecamp"})
        result = self.service.snapshot(
            now=datetime(2026, 8, 9, 12, 0, tzinfo=timezone(timedelta(hours=9)))
        )

        self.assertEqual(result["waypoints"]["selected_target"], "basecamp")
        self.assertTrue(result["navigation"]["active_route"]["available"])
        self.assertEqual(result["navigation"]["active_route"]["computed_by"], "map_engine")
        self.assertEqual(result["sun"]["travel_min"], 10)
        self.assertEqual(result["sun"]["margin_min"], 30)
        self.assertEqual(result["sun"]["status"], "scheduled")
        self.assertEqual(result["sun"]["level"], "normal")
        self.assertTrue(result["contract"]["llm_may_generate_coordinates"] is False)
        self.assertEqual(result["navigation"]["active_route"]["eta_min"], 8)
        self.assertTrue(result["navigation"]["arrival"]["arrived"])
        self.assertEqual(result["navigation"]["arrival"]["status"], "arrived")

    def test_daylight_levels_follow_code_computed_return_deadline(self) -> None:
        self.service.apply_waypoint({"action": "save_current", "kind": "basecamp"})
        normal = self.service.snapshot(
            now=datetime(2026, 8, 9, 12, 0, tzinfo=timezone(timedelta(hours=9)))
        )
        return_by = datetime.fromisoformat(normal["sun"]["return_by"])

        caution = self.service.snapshot(now=return_by - timedelta(minutes=15))
        danger = self.service.snapshot(now=return_by + timedelta(minutes=1))

        self.assertEqual(normal["sun"]["level"], "normal")
        self.assertEqual(caution["sun"]["status"], "caution")
        self.assertEqual(caution["sun"]["level"], "caution")
        self.assertEqual(danger["sun"]["status"], "return_now")
        self.assertEqual(danger["sun"]["level"], "danger")
        self.assertEqual(danger["alert"]["kind"], "daylight")

    def test_no_fix_cannot_be_saved_as_current_position(self) -> None:
        empty_gps = GpsService(NMEA_REPLAY)
        try:
            service = NavigationService(
                FakeRegistry(),
                empty_gps,
                Path(self.temporary.name) / "empty.json",
                local_tz=timezone.utc,
            )
            with self.assertRaisesRegex(NavigationInputError, "GPS fix"):
                service.apply_waypoint({"action": "save_current", "kind": "checkpoint"})
        finally:
            service.close()
            empty_gps.close()

    def test_recent_trace_routes_only_to_code_selected_history_point(self) -> None:
        clock_now = 200.0
        service = NavigationService(
            FakeRegistry(),
            self.gps,
            Path(self.temporary.name) / "trace-waypoints.json",
            local_tz=timezone(timedelta(hours=9)),
        )
        try:
            service.close()
            history = PositionHistoryStore(
                Path(self.temporary.name) / "trace.jsonl",
                boot_id="test-boot",
                clock=lambda: clock_now,
            )
            service.position_history = history
            self.assertTrue(
                history.record(
                    {"fix": True, "lat": 37.5464, "lon": 127.0756, "acc_m": 5.0, "satellites": 9},
                    now_monotonic=20.0,
                )
            )
            self.assertTrue(
                history.record(
                    {"fix": True, "lat": 37.5465, "lon": 127.0757, "acc_m": 5.0, "satellites": 9},
                    now_monotonic=200.0,
                )
            )

            result = service.apply_voice_command({"action": "route_recent_trace"})

            self.assertEqual(result["status"], "accepted")
            self.assertEqual(
                result["device"]["navigation"]["active_route"]["computed_by"],
                "map_engine",
            )
            checkpoint = result["device"]["waypoints"]["checkpoints"][-1]
            self.assertEqual(checkpoint["source"], "position_history")
            self.assertEqual(checkpoint["name"], "3분 전 위치 로그")
            summary = result["device"]["position_history"]
            self.assertTrue(summary["coordinates_exposed"] is False)
            self.assertNotIn("lat", summary)
            self.assertNotIn("lon", summary)
        finally:
            service.close()

    def test_recent_trace_rejects_when_three_minute_history_is_missing(self) -> None:
        service = NavigationService(
            FakeRegistry(),
            self.gps,
            Path(self.temporary.name) / "empty-trace-waypoints.json",
            local_tz=timezone(timedelta(hours=9)),
        )
        try:
            service.close()
            service.position_history = PositionHistoryStore(
                Path(self.temporary.name) / "empty-trace.jsonl",
                boot_id="test-boot",
                clock=lambda: 200.0,
            )
            result = service.apply_voice_command({"action": "route_recent_trace"})

            self.assertEqual(result["status"], "rejected")
            self.assertIn("3분 전", result["message"])
            self.assertFalse(result["device"]["waypoints"]["checkpoints"])
        finally:
            service.close()

    def test_close_stops_position_history_subscription_thread(self) -> None:
        self.service.close()

        self.assertFalse(self.service._history_thread.is_alive())

    def test_large_trail_offset_requests_stm32_watchdog_vibration(self) -> None:
        self.service.close()
        with self.gps._lock:  # 테스트 전용: 실제 직렬 포트 없이 출력 계약만 확인한다.
            self.gps._configuration = GpsConfiguration(
                mode="stm32", port="test", baud=115200
            )
            self.gps._last_fix["acc_m"] = None
        service = NavigationService(
            FarFromTrailRegistry(),
            self.gps,
            Path(self.temporary.name) / "trail-output.json",
            local_tz=timezone(timedelta(hours=9)),
        )
        try:
            gps = self.gps.snapshot()
            service._update_trail_output(gps)

            trail = service.snapshot()["trail"]
            output = self.gps.snapshot()["hardware_output"]
            self.assertEqual(trail["status"], "off_trail_estimate")
            self.assertTrue(trail["physical_output"]["requested"])
            self.assertFalse(trail["physical_output"]["confirmed"])
            self.assertEqual(output["last_requested"], "trail_caution")
        finally:
            service.close()

    def test_physical_checkpoint_release_saves_once_from_current_fix(self) -> None:
        self.service.close()
        line = encode_stm32_telemetry(
            {
                "v": 1,
                "event": "button",
                "seq": 4,
                "button": "checkpoint",
                "state": "released",
                "held_ms": 280,
            }
        )
        self.gps._handle_line(line, mode="stm32")
        self.service._last_button_event_count = 0

        self.service._consume_physical_button(self.gps.snapshot())
        self.service._consume_physical_button(self.gps.snapshot())

        waypoints = self.service.waypoints.snapshot()
        self.assertEqual(len(waypoints["checkpoints"]), 1)
        self.assertEqual(waypoints["checkpoints"][0]["source"], "sensor")
        self.assertEqual(
            self.service.voice_snapshot()["last_event"]["request_id"],
            "physical-checkpoint-1",
        )

    def test_co_alarm_has_priority_and_requests_sound(self) -> None:
        payload = {
            "v": 1,
            "event": "telemetry",
            "seq": 1,
            "uptime_ms": 400000,
            "gps": {
                "fix": True,
                "lat": 37.5465,
                "lon": 127.0757,
                "acc_m": 5.0,
                "hdop": 0.8,
                "sats": 10,
                "age_s": 0,
            },
            "env": {"valid": True, "temp_c": 22.0, "humidity_pct": 50.0, "age_s": 0},
            "co": {
                "valid": True,
                "warming_up": False,
                "ppm": 112.0,
                "level": "alarm",
                "alarm": True,
                "age_s": 0,
            },
            "power": {"valid": False, "percent": None, "days_left": None},
        }
        self.gps._handle_line(encode_stm32_telemetry(payload), mode="stm32")

        result = self.service.snapshot()

        self.assertEqual(result["alert"]["kind"], "co_alarm")
        self.assertTrue(result["alert"]["sound"])
        self.assertIn("112 ppm", result["alert"]["text"])

    def test_voice_water_destination_requires_explicit_confirmation(self) -> None:
        proposed = self.service.apply_voice_command({"action": "find_nearest_water"})

        self.assertEqual(proposed["status"], "confirmation_required")
        self.assertIn("수질은 확인되지 않았습니다", proposed["message"])
        self.assertIsNone(proposed["device"]["waypoints"]["destination"])
        self.assertNotIn("lat", proposed["pending_destination"])
        self.assertFalse(
            proposed["device"]["contract"]["voice_commands_accept_coordinates"]
        )

        confirmed = self.service.apply_voice_command({"action": "confirm_destination"})

        self.assertEqual(confirmed["status"], "accepted")
        destination = confirmed["device"]["waypoints"]["destination"]
        self.assertEqual(destination["source"], "offline_catalog")
        self.assertEqual(
            confirmed["device"]["waypoints"]["selected_target"], "destination"
        )
        self.assertTrue(confirmed["device"]["navigation"]["active_route"]["available"])

    def test_voice_night_mode_is_server_state(self) -> None:
        enabled = self.service.apply_voice_command({"action": "night_on"})
        self.assertTrue(enabled["ui"]["night"])
        self.assertTrue(enabled["device"]["interface"]["night"])

        disabled = self.service.apply_voice_command({"action": "night_off"})
        self.assertFalse(disabled["ui"]["night"])

    def test_voice_can_clear_active_destination_and_route(self) -> None:
        self.service.apply_voice_command({"action": "find_nearest_water"})
        self.service.apply_voice_command({"action": "confirm_destination"})

        cleared = self.service.apply_voice_command({"action": "clear_destination"})

        self.assertEqual(cleared["status"], "accepted")
        self.assertIsNone(cleared["device"]["waypoints"]["destination"])
        self.assertIsNone(cleared["device"]["waypoints"]["selected_target"])
        self.assertFalse(cleared["device"]["navigation"]["active_route"]["available"])

    def test_cached_route_projects_small_progress_into_distance_and_eta(self) -> None:
        self.service.apply_voice_command({"action": "find_nearest_water"})
        confirmed = self.service.apply_voice_command({"action": "confirm_destination"})
        initial = confirmed["device"]["navigation"]["active_route"]
        self.gps._handle_line(
            '{"ok":true,"event":"fix","lat":37.54655,"lon":127.07575,'
            '"acc_m":5.0,"sats":9,"age_s":0}',
            mode="stm32",
        )

        moved = self.service.snapshot()["navigation"]["active_route"]

        self.assertTrue(moved["available"])
        self.assertLess(moved["distance_m"], initial["distance_m"])
        self.assertLessEqual(moved["eta_min"], initial["eta_min"])

    def test_crossing_route_cache_is_rejected_until_progress_disambiguates_it(self) -> None:
        bow_tie = [
            [-0.0001, -0.0001],
            [0.0001, 0.0001],
            [-0.0001, 0.0001],
            [0.0001, -0.0001],
        ]

        ambiguous = _polyline_progress(0.0, 0.0, bow_tie)
        late_progress = _polyline_progress(
            0.0,
            0.0,
            bow_tie,
            minimum_progress_m=60.0,
        )

        self.assertIsNone(ambiguous)
        self.assertIsNotNone(late_progress)
        assert late_progress is not None
        self.assertLess(late_progress[1], 20.0)
        self.assertGreater(late_progress[4], 60.0)
        self.assertIsNone(
            _polyline_progress(0.0, 0.0, [[0.0, 0.0], [0.0, 0.0]])
        )

    def test_overlapping_route_cache_uses_late_progress_to_disambiguate(self) -> None:
        overlapping = [
            [-0.0002, 0.0],
            [0.0002, 0.0],
            [-0.0001, 0.0],
            [0.0003, 0.0],
        ]

        ambiguous = _polyline_progress(0.0, 0.0, overlapping)
        late_progress = _polyline_progress(
            0.0,
            0.0,
            overlapping,
            minimum_progress_m=85.0,
        )

        self.assertIsNone(ambiguous)
        self.assertIsNotNone(late_progress)
        assert late_progress is not None
        self.assertGreater(late_progress[4], 85.0)
        self.assertLess(late_progress[1], 40.0)

    def test_route_cache_includes_exact_eight_meter_boundary(self) -> None:
        self.service.apply_voice_command({"action": "find_nearest_water"})
        self.service.apply_voice_command({"action": "confirm_destination"})
        with patch(
            "navigation_service._polyline_progress",
            return_value=(100.0, 80.0, 1, 8.0, 20.0),
        ), patch.object(
            self.service.registry,
            "route_between",
            side_effect=AssertionError("정확히 8m에서 재경로하면 안 됨"),
        ):
            route = self.service.snapshot()["navigation"]["active_route"]

        self.assertTrue(route["available"])
        self.assertEqual(route["distance_m"], 384.0)

    def test_route_cache_recomputes_above_eight_meter_boundary(self) -> None:
        self.service.apply_voice_command({"action": "find_nearest_water"})
        self.service.apply_voice_command({"action": "confirm_destination"})
        with patch(
            "navigation_service._polyline_progress",
            return_value=(100.0, 80.0, 1, 8.01, 20.0),
        ), patch.object(
            self.service.registry,
            "route_between",
            wraps=self.service.registry.route_between,
        ) as route_between:
            route = self.service.snapshot()["navigation"]["active_route"]

        self.assertTrue(route["available"])
        route_between.assert_called_once()

    def test_voice_payload_cannot_contain_coordinates(self) -> None:
        with self.assertRaisesRegex(NavigationInputError, "action과 request_id"):
            self.service.apply_voice_command(
                {"action": "route_basecamp", "lat": 37.5, "lon": 127.0}
            )

    def test_every_voice_action_has_a_deterministic_state_transition(self) -> None:
        saved_basecamp = self.service.apply_voice_command({"action": "save_basecamp"})
        saved_checkpoint = self.service.apply_voice_command({"action": "save_checkpoint"})
        routed_basecamp = self.service.apply_voice_command({"action": "route_basecamp"})

        self.assertEqual(saved_basecamp["status"], "accepted")
        self.assertEqual(saved_checkpoint["status"], "accepted")
        self.assertEqual(routed_basecamp["status"], "accepted")

        proposed = self.service.apply_voice_command({"action": "find_nearest_water"})
        self.assertEqual(proposed["status"], "confirmation_required")
        rejected = self.service.apply_voice_command({"action": "reject_destination"})
        self.assertIsNone(rejected["pending_destination"])

        self.service.apply_voice_command({"action": "find_nearest_water"})
        confirmed = self.service.apply_voice_command({"action": "confirm_destination"})
        routed_destination = self.service.apply_voice_command({"action": "route_destination"})
        routed_checkpoint = self.service.apply_voice_command({"action": "route_last_checkpoint"})
        self.assertEqual(confirmed["status"], "accepted")
        self.assertEqual(routed_destination["status"], "accepted")
        self.assertEqual(routed_checkpoint["status"], "accepted")

        toggled = self.service.apply_voice_command({"action": "night_toggle"})
        self.assertTrue(toggled["ui"]["night"])
        self.service.apply_voice_command({"action": "night_off"})
        self.service.apply_voice_command({"action": "night_on"})

        self.service.apply_voice_command({"action": "find_nearest_water"})
        cancelled = self.service.apply_voice_command({"action": "cancel"})
        cleared = self.service.apply_voice_command({"action": "clear_destination"})
        status = self.service.apply_voice_command({"action": "status"})
        self.assertIsNone(cancelled["pending_destination"])
        self.assertEqual(cleared["status"], "accepted")
        self.assertEqual(status["status"], "accepted")

    def test_arrival_is_not_confirmed_without_accuracy(self) -> None:
        self.gps._handle_line(
            '{"ok":true,"event":"fix","lat":37.5465,"lon":127.0757,'
            '"acc_m":null,"sats":9,"age_s":0}',
            mode="stm32",
        )
        result = self.service.apply_waypoint(
            {"action": "save_current", "kind": "destination"}
        )

        self.assertFalse(result["navigation"]["arrival"]["arrived"])
        self.assertEqual(result["navigation"]["arrival"]["status"], "accuracy_required")


if __name__ == "__main__":
    unittest.main()
