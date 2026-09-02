"""센서 상태, 오프라인 지도, 일출몰, 저장 지점을 하나의 안전한 화면 상태로 조립한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
import json
from math import atan2, ceil, cos, degrees, floor, radians, sin, sqrt
import os
from pathlib import Path
from queue import Empty, Full, Queue
import threading
import time
from typing import Any
from uuid import uuid4

from gps_service import GpsInputError, GpsService
from map_engine import MapValidationError, RouteNotFound, SnapOutOfBounds, haversine_m
from position_history import PositionHistoryStore
from solar_service import calculate_solar_times, clock_or_none, configured_timezone, iso_or_none


WAYPOINT_SCHEMA_VERSION = 1
WAYPOINT_KINDS = {"basecamp", "destination", "checkpoint"}
VOICE_ACTIONS = {
    "save_basecamp",
    "save_checkpoint",
    "route_basecamp",
    "route_destination",
    "route_last_checkpoint",
    "route_recent_trace",
    "clear_destination",
    "find_nearest_water",
    "confirm_destination",
    "reject_destination",
    "night_on",
    "night_off",
    "night_toggle",
    "status",
    "cancel",
}
TRAIL_THRESHOLD_M = float(os.getenv("OGTECH_TRAIL_THRESHOLD_M", "30"))
RETURN_SPEED_MPS = float(os.getenv("OGTECH_RETURN_SPEED_MPS", "0.8"))
RETURN_MARGIN_MIN = int(os.getenv("OGTECH_RETURN_MARGIN_MIN", "30"))
DAYLIGHT_CAUTION_MIN = int(os.getenv("OGTECH_DAYLIGHT_CAUTION_MIN", "30"))
NAVIGATION_SPEED_MPS = float(os.getenv("OGTECH_NAVIGATION_SPEED_MPS", "1.0"))
ARRIVAL_RADIUS_M = float(os.getenv("OGTECH_ARRIVAL_RADIUS_M", "10"))
ARRIVAL_MAX_ACCURACY_M = float(os.getenv("OGTECH_ARRIVAL_MAX_ACCURACY_M", "20"))
ROUTE_CACHE_MAX_CROSS_TRACK_M = 8.0
ROUTE_CACHE_BACKTRACK_TOLERANCE_M = 3.0
ROUTE_CACHE_AMBIGUITY_CROSS_TRACK_M = 0.75
ROUTE_CACHE_AMBIGUITY_PROGRESS_M = 5.0
TRAIL_OUTPUT_KEEPALIVE_S = 2.0


class NavigationInputError(ValueError):
    """저장 지점 또는 항법 요청이 계약을 만족하지 않을 때 발생한다."""


def _coordinate(value: Any, label: str, lower: float, upper: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NavigationInputError(f"{label} 값이 숫자가 아닙니다") from exc
    if not lower <= number <= upper:
        raise NavigationInputError(f"{label} 값이 허용 범위를 벗어났습니다")
    return number


def _bearing_degrees(start: dict[str, Any], target: dict[str, Any]) -> float:
    start_lat = radians(float(start["lat"]))
    target_lat = radians(float(target["lat"]))
    delta_lon = radians(float(target["lon"]) - float(start["lon"]))
    y = sin(delta_lon) * cos(target_lat)
    x = cos(start_lat) * sin(target_lat) - sin(start_lat) * cos(target_lat) * cos(delta_lon)
    return (degrees(atan2(y, x)) + 360.0) % 360.0


def _polyline_progress(
    lon: float,
    lat: float,
    coordinates: list[list[float]],
    *,
    minimum_progress_m: float = 0.0,
) -> tuple[float, float, int, float, float] | None:
    """현재 fix의 경로 진행량을 구한다. 교차·겹침이 모호하면 재경로용 None을 준다."""
    if len(coordinates) < 2:
        return None
    segment_lengths = [
        haversine_m(
            float(coordinates[index][0]),
            float(coordinates[index][1]),
            float(coordinates[index + 1][0]),
            float(coordinates[index + 1][1]),
        )
        for index in range(len(coordinates) - 1)
    ]
    total_m = sum(segment_lengths)
    if total_m <= 0:
        return None
    prefix = [0.0]
    for segment_m in segment_lengths:
        prefix.append(prefix[-1] + segment_m)
    suffix = [0.0] * (len(segment_lengths) + 1)
    for index in range(len(segment_lengths) - 1, -1, -1):
        suffix[index] = suffix[index + 1] + segment_lengths[index]

    candidates: list[tuple[float, float, int, float, float]] = []
    for index, segment_m in enumerate(segment_lengths):
        lon_a, lat_a = map(float, coordinates[index])
        lon_b, lat_b = map(float, coordinates[index + 1])
        reference_lat = radians((lat + lat_a + lat_b) / 3.0)
        lon_scale = 111_320.0 * max(0.15, cos(reference_lat))
        lat_scale = 111_132.0
        ax = (lon_a - lon) * lon_scale
        ay = (lat_a - lat) * lat_scale
        bx = (lon_b - lon) * lon_scale
        by = (lat_b - lat) * lat_scale
        dx = bx - ax
        dy = by - ay
        length_squared = dx * dx + dy * dy
        if length_squared <= 0:
            continue
        projection = max(0.0, min(1.0, -(ax * dx + ay * dy) / length_squared))
        nearest_x = ax + projection * dx
        nearest_y = ay + projection * dy
        cross_track_m = sqrt(nearest_x * nearest_x + nearest_y * nearest_y)
        progress_m = prefix[index] + segment_m * projection
        remaining_m = segment_m * (1.0 - projection) + suffix[index + 1]
        candidates.append(
            (total_m, remaining_m, index + 1, cross_track_m, progress_m)
        )

    eligible = [
        candidate
        for candidate in candidates
        if candidate[4] + ROUTE_CACHE_BACKTRACK_TOLERANCE_M >= minimum_progress_m
    ]
    if not eligible:
        return None
    best_cross_track = min(candidate[3] for candidate in eligible)
    near_ties = [
        candidate
        for candidate in eligible
        if candidate[3]
        <= best_cross_track + ROUTE_CACHE_AMBIGUITY_CROSS_TRACK_M
    ]
    if (
        max(candidate[4] for candidate in near_ties)
        - min(candidate[4] for candidate in near_ties)
        > ROUTE_CACHE_AMBIGUITY_PROGRESS_M
    ):
        return None
    return min(
        near_ties,
        key=lambda candidate: (
            candidate[3],
            abs(candidate[4] - minimum_progress_m),
        ),
    )


class WaypointStore:
    """LLM 도구와 화면이 함께 쓸 수 있는 명시적 저장 지점 계약."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._load_error: str | None = None
        self._state = self._load()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "version": WAYPOINT_SCHEMA_VERSION,
            "basecamp": None,
            "destination": None,
            "checkpoints": [],
            "selected_target": None,
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != WAYPOINT_SCHEMA_VERSION:
                raise NavigationInputError("저장 지점 스키마 버전이 올바르지 않습니다")
            result = self._empty()
            for kind in ("basecamp", "destination"):
                value = payload.get(kind)
                result[kind] = None if value is None else self._validate_point(value, kind)
            checkpoints = payload.get("checkpoints", [])
            if not isinstance(checkpoints, list):
                raise NavigationInputError("체크포인트 목록이 배열이 아닙니다")
            result["checkpoints"] = [self._validate_point(item, "checkpoint") for item in checkpoints]
            selected = payload.get("selected_target")
            if selected is not None and not isinstance(selected, str):
                raise NavigationInputError("선택 지점 ID가 문자열이 아닙니다")
            result["selected_target"] = selected
            return result
        except (OSError, json.JSONDecodeError, NavigationInputError) as exc:
            self._load_error = str(exc)
            return self._empty()

    @staticmethod
    def _validate_point(value: Any, kind: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise NavigationInputError("저장 지점이 객체가 아닙니다")
        return {
            "id": str(value.get("id") or kind),
            "kind": kind,
            "name": str(value.get("name") or ("베이스캠프" if kind == "basecamp" else "목적지" if kind == "destination" else "체크포인트"))[:40],
            "lat": _coordinate(value.get("lat"), "저장 지점 위도", -90, 90),
            "lon": _coordinate(value.get("lon"), "저장 지점 경도", -180, 180),
            "source": str(value.get("source") or "user")[:20],
            "saved_at": str(value.get("saved_at") or ""),
        }

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
        self._load_error = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result = json.loads(json.dumps(self._state, ensure_ascii=False))
            result["load_error"] = self._load_error
            return result

    def _point_by_id(self, identifier: str) -> dict[str, Any] | None:
        if identifier in {"basecamp", "destination"}:
            return self._state.get(identifier)
        return next(
            (item for item in self._state["checkpoints"] if item["id"] == identifier),
            None,
        )

    def selected_point(self) -> dict[str, Any] | None:
        with self._lock:
            selected = self._state.get("selected_target")
            return None if not selected else self._point_by_id(selected)

    def apply(
        self,
        payload: dict[str, Any],
        gps: dict[str, Any],
        *,
        trusted_source: str | None = None,
    ) -> dict[str, Any]:
        action = str(payload.get("action", "")).strip()
        kind = str(payload.get("kind", "")).strip()
        if action not in {"save_current", "set", "select", "remove"}:
            raise NavigationInputError("지원하지 않는 저장 지점 동작입니다")

        with self._lock:
            if action in {"save_current", "set"}:
                if kind not in WAYPOINT_KINDS:
                    raise NavigationInputError("kind는 basecamp, destination, checkpoint 중 하나여야 합니다")
                if action == "save_current":
                    if gps.get("fix") is not True:
                        raise NavigationInputError("현재 GPS fix가 없어 지점을 저장할 수 없습니다")
                    lat = _coordinate(gps.get("lat"), "현재 위도", -90, 90)
                    lon = _coordinate(gps.get("lon"), "현재 경도", -180, 180)
                    source = "sensor" if not gps.get("demo") else "replay"
                else:
                    lat = _coordinate(payload.get("lat"), "지정 위도", -90, 90)
                    lon = _coordinate(payload.get("lon"), "지정 경도", -180, 180)
                    source = trusted_source or "user"
                default_name = "베이스캠프" if kind == "basecamp" else "목적지" if kind == "destination" else "체크포인트"
                point = {
                    "id": kind if kind != "checkpoint" else f"checkpoint-{uuid4().hex[:10]}",
                    "kind": kind,
                    "name": str(payload.get("name") or default_name)[:40],
                    "lat": lat,
                    "lon": lon,
                    "source": source,
                    "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                if kind == "checkpoint":
                    self._state["checkpoints"].append(point)
                    self._state["checkpoints"] = self._state["checkpoints"][-50:]
                else:
                    self._state[kind] = point
                self._state["selected_target"] = point["id"]

            elif action == "select":
                identifier = str(payload.get("id") or kind).strip()
                if not identifier or self._point_by_id(identifier) is None:
                    raise NavigationInputError("선택할 저장 지점이 없습니다")
                self._state["selected_target"] = identifier

            elif action == "remove":
                identifier = str(payload.get("id") or kind).strip()
                if identifier in {"basecamp", "destination"}:
                    self._state[identifier] = None
                else:
                    self._state["checkpoints"] = [
                        item for item in self._state["checkpoints"] if item["id"] != identifier
                    ]
                if self._state.get("selected_target") == identifier:
                    self._state["selected_target"] = None
            self._write()
            return self.snapshot()


class NavigationService:
    """LLM을 거치지 않고 모든 위치·경로·귀환 시각을 코드로 계산한다."""

    def __init__(
        self,
        registry: Any,
        gps: GpsService,
        waypoint_path: str | Path,
        *,
        local_tz: tzinfo | None = None,
        position_history: PositionHistoryStore | None = None,
    ) -> None:
        if (
            TRAIL_THRESHOLD_M <= 0
            or RETURN_SPEED_MPS <= 0
            or NAVIGATION_SPEED_MPS <= 0
            or ARRIVAL_RADIUS_M <= 0
            or ARRIVAL_MAX_ACCURACY_M <= 0
            or RETURN_MARGIN_MIN < 0
            or DAYLIGHT_CAUTION_MIN < 0
        ):
            raise NavigationInputError("항법 임계 환경 변수가 유효하지 않습니다")
        self.registry = registry
        self.gps = gps
        self.waypoints = WaypointStore(waypoint_path)
        self.position_history = position_history or PositionHistoryStore(
            Path(waypoint_path).with_name("position_history.jsonl")
        )
        self.local_tz = local_tz or configured_timezone()
        self._route_lock = threading.RLock()
        self._route_cache: dict[str, dict[str, Any]] = {}
        self._voice_lock = threading.RLock()
        self._voice_subscribers: set[Queue[dict[str, Any]]] = set()
        self._voice_state: dict[str, Any] = {
            "version": 1,
            "sequence": 0,
            "ui": {"night": False},
            "pending_destination": None,
            "last_event": None,
        }
        self._history_stop = threading.Event()
        self._trail_output_lock = threading.RLock()
        self._trail_output_state: dict[str, Any] = {
            "requested": False,
            "status": "inactive",
            "last_action": None,
            "last_request_monotonic": None,
            "transport": "stm32_serial",
            "confirmed": False,
        }
        initial_buttons = (self.gps.snapshot().get("hardware_buttons") or {})
        self._last_button_event_count = int(initial_buttons.get("event_count") or 0)
        self._history_subscriber = self.gps.subscribe()
        self._button_subscriber = self.gps.subscribe_buttons()
        self._history_thread = threading.Thread(
            target=self._record_position_events,
            name="position-history",
            daemon=True,
        )
        self._history_thread.start()

    def _record_position_events(self) -> None:
        while not self._history_stop.is_set():
            try:
                self._history_subscriber.get(timeout=0.5)
            except Empty:
                continue
            gps = self.gps.snapshot()
            self.position_history.record(gps)
            self._update_trail_output(gps)
            while True:
                try:
                    button_event = self._button_subscriber.get_nowait()
                except Empty:
                    break
                self._last_button_event_count = int(
                    button_event.get("event_count") or self._last_button_event_count
                )
                self._handle_physical_button_event(button_event)

    def _handle_physical_button_event(self, event: dict[str, Any]) -> None:
        if event.get("button") != "checkpoint" or event.get("state") != "released":
            return
        event_count = int(event.get("event_count") or 0)
        try:
            self.apply_voice_command(
                {
                    "action": "save_checkpoint",
                    "request_id": f"physical-checkpoint-{event_count}",
                }
            )
        except (NavigationInputError, OSError):
            # 실패 상태는 다음 장치 스냅샷에서 GPS/저장소 상태로 확인한다.
            return

    def _consume_physical_button(self, gps: dict[str, Any]) -> None:
        buttons = gps.get("hardware_buttons") or {}
        event_count = int(buttons.get("event_count") or 0)
        if event_count <= self._last_button_event_count:
            return
        self._last_button_event_count = event_count
        event = buttons.get("last_event") or {}
        self._handle_physical_button_event({"event_count": event_count, **event})

    def _update_trail_output(self, gps: dict[str, Any]) -> None:
        """확정/보수적 이탈 상태를 STM32 진동 watchdog으로 전달한다."""
        location, _ = self._location(gps)
        trail = self._trail(gps, location)
        trail_status = trail.get("status")
        active = trail_status in {"off_trail", "off_trail_estimate"}
        action = (
            "trail_alert"
            if trail_status == "off_trail"
            else "trail_caution"
            if trail_status == "off_trail_estimate"
            else "trail_clear"
        )
        now = time.monotonic()
        with self._trail_output_lock:
            previous = self._trail_output_state.get("last_action")
            previous_at = self._trail_output_state.get("last_request_monotonic")
            keepalive_due = bool(
                active
                and isinstance(previous_at, (int, float))
                and now - float(previous_at) >= TRAIL_OUTPUT_KEEPALIVE_S
            )
            if action == previous and not keepalive_due:
                return
        accepted = self.gps.request_stm32_output(action)
        with self._trail_output_lock:
            self._trail_output_state.update(
                {
                    "requested": bool(active and accepted),
                    "status": trail.get("status"),
                    "last_action": action if accepted else None,
                    "last_request_monotonic": now if accepted else None,
                    "confirmed": False,
                }
            )

    def _trail_output_snapshot(self) -> dict[str, Any]:
        with self._trail_output_lock:
            state = dict(self._trail_output_state)
        state.pop("last_request_monotonic", None)
        return state

    def _device_now(
        self,
        gps: dict[str, Any],
        explicit: datetime | None,
    ) -> tuple[datetime, str]:
        if explicit is not None:
            value = explicit if explicit.tzinfo is not None else explicit.replace(tzinfo=self.local_tz)
            return value.astimezone(self.local_tz), "test_override"
        rtc = gps.get("rtc") or {}
        if rtc.get("valid") is True and not rtc.get("stale") and rtc.get("iso_utc"):
            try:
                value = datetime.fromisoformat(str(rtc["iso_utc"]).replace("Z", "+00:00"))
            except ValueError:
                pass
            else:
                if value.tzinfo is not None:
                    return value.astimezone(self.local_tz), "ds3231"
        return datetime.now(self.local_tz), "system"

    def close(self) -> None:
        self._history_stop.set()
        self.gps.unsubscribe(self._history_subscriber)
        self.gps.unsubscribe_buttons(self._button_subscriber)
        if self._history_thread.is_alive():
            self._history_thread.join(timeout=1.5)
        self.gps.request_stm32_output("trail_clear")

    def apply_waypoint(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.waypoints.apply(payload, self.gps.snapshot())
        return self.snapshot()

    def voice_snapshot(self) -> dict[str, Any]:
        """좌표를 제외한 음성 제어 상태를 화면에 제공한다."""
        with self._voice_lock:
            pending = self._voice_state.get("pending_destination")
            pending_public = None
            if isinstance(pending, dict):
                pending_public = {
                    "id": pending.get("id"),
                    "kind": pending.get("kind"),
                    "name": pending.get("name"),
                    "demo": bool(pending.get("demo")),
                    "potable": pending.get("potable", "unknown"),
                }
            return {
                "version": 1,
                "sequence": self._voice_state["sequence"],
                "ui": dict(self._voice_state["ui"]),
                "pending_destination": pending_public,
                "last_event": (
                    None
                    if self._voice_state["last_event"] is None
                    else dict(self._voice_state["last_event"])
                ),
                "allowed_actions": sorted(VOICE_ACTIONS),
                "contract": {
                    "enum_actions_only": True,
                    "coordinates_accepted_from_voice": False,
                    "route_values_computed_by_map_engine": True,
                },
            }

    def subscribe_voice(self) -> Queue[dict[str, Any]]:
        subscriber: Queue[dict[str, Any]] = Queue(maxsize=4)
        with self._voice_lock:
            self._voice_subscribers.add(subscriber)
        return subscriber

    def unsubscribe_voice(self, subscriber: Queue[dict[str, Any]]) -> None:
        with self._voice_lock:
            self._voice_subscribers.discard(subscriber)

    def _publish_voice(self, event: dict[str, Any]) -> None:
        with self._voice_lock:
            subscribers = tuple(self._voice_subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except Full:
                try:
                    subscriber.get_nowait()
                except Empty:  # 큐가 다른 소비자에 의해 비워진 경우
                    pass
                try:
                    subscriber.put_nowait(event)
                except Full:
                    pass

    def _finish_voice(
        self,
        *,
        action: str,
        status: str,
        message: str,
        request_id: str | None,
    ) -> dict[str, Any]:
        with self._voice_lock:
            self._voice_state["sequence"] += 1
            summary = {
                "sequence": self._voice_state["sequence"],
                "action": action,
                "status": status,
                "message": message,
                "request_id": request_id,
            }
            self._voice_state["last_event"] = summary
            ui = dict(self._voice_state["ui"])
            pending = self._voice_state.get("pending_destination")
            pending_public = None if pending is None else {
                "id": pending.get("id"),
                "kind": pending.get("kind"),
                "name": pending.get("name"),
                "demo": bool(pending.get("demo")),
                "potable": pending.get("potable", "unknown"),
            }
        event = {
            "version": 1,
            **summary,
            "ui": ui,
            "pending_destination": pending_public,
            "device": self.snapshot(),
        }
        self._publish_voice(event)
        return event

    def apply_voice_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """숫자 없는 열거형 명령만 받아 지도·저장 지점·야간 화면을 제어한다."""
        extra = set(payload) - {"action", "request_id"}
        if extra:
            raise NavigationInputError(
                "음성 명령에는 action과 request_id만 사용할 수 있습니다"
            )
        action = str(payload.get("action") or "").strip()
        if action not in VOICE_ACTIONS:
            raise NavigationInputError("지원하지 않는 음성 지도 명령입니다")
        raw_request_id = payload.get("request_id")
        request_id = None if raw_request_id is None else str(raw_request_id).strip()[:64]
        gps = self.gps.snapshot()

        if action in {"save_basecamp", "save_checkpoint"}:
            if gps.get("fix") is not True:
                return self._finish_voice(
                    action=action,
                    status="rejected",
                    message="현재 GPS 수신이 없어 위치를 저장할 수 없습니다. 마지막 확정 좌표와 경과 시간만 확인하세요.",
                    request_id=request_id,
                )
            kind = "basecamp" if action == "save_basecamp" else "checkpoint"
            self.waypoints.apply({"action": "save_current", "kind": kind}, gps)
            message = (
                "현재 GPS 위치를 베이스캠프로 저장했습니다."
                if kind == "basecamp"
                else "현재 GPS 위치를 체크포인트로 저장했습니다."
            )
            return self._finish_voice(
                action=action, status="accepted", message=message, request_id=request_id
            )

        if action == "route_recent_trace":
            if gps.get("fix") is not True:
                return self._finish_voice(
                    action=action,
                    status="rejected",
                    message="현재 GPS 수신이 없어 위치 로그 역추적 경로를 계산할 수 없습니다.",
                    request_id=request_id,
                )
            self.position_history.record(gps)
            logged = self.position_history.point_ago()
            if logged is None:
                return self._finish_voice(
                    action=action,
                    status="rejected",
                    message="3분 전 확정 위치 로그가 아직 충분하지 않습니다.",
                    request_id=request_id,
                )
            self.waypoints.apply(
                {
                    "action": "set",
                    "kind": "checkpoint",
                    "name": "3분 전 위치 로그",
                    "lat": logged["lat"],
                    "lon": logged["lon"],
                },
                gps,
                trusted_source="position_history",
            )
            return self._finish_voice(
                action=action,
                status="accepted",
                message="3분 전 확정 위치 로그 경로를 불러왔습니다. 방위와 거리는 지도 엔진 계산값입니다.",
                request_id=request_id,
            )

        if action in {"route_basecamp", "route_destination", "route_last_checkpoint"}:
            waypoints = self.waypoints.snapshot()
            if action == "route_basecamp":
                target = waypoints.get("basecamp")
            elif action == "route_destination":
                target = waypoints.get("destination")
            else:
                checkpoints = waypoints.get("checkpoints") or []
                target = checkpoints[-1] if checkpoints else None
            if target is None:
                label = "베이스캠프" if action == "route_basecamp" else "목적지" if action == "route_destination" else "체크포인트"
                return self._finish_voice(
                    action=action,
                    status="rejected",
                    message=f"저장된 {label}가 없습니다.",
                    request_id=request_id,
                )
            if gps.get("fix") is not True:
                return self._finish_voice(
                    action=action,
                    status="rejected",
                    message="현재 GPS 수신이 없어 경로를 계산할 수 없습니다. 마지막 확정 좌표와 경과 시간만 확인하세요.",
                    request_id=request_id,
                )
            self.waypoints.apply({"action": "select", "id": target["id"]}, gps)
            return self._finish_voice(
                action=action,
                status="accepted",
                message=f"{target['name']} 경로를 불러왔습니다. 방위와 거리는 지도 엔진 계산값입니다.",
                request_id=request_id,
            )

        if action == "clear_destination":
            waypoints = self.waypoints.snapshot()
            with self._voice_lock:
                self._voice_state["pending_destination"] = None
            if waypoints.get("destination") is None:
                return self._finish_voice(
                    action=action,
                    status="rejected",
                    message="삭제할 목적지가 없습니다.",
                    request_id=request_id,
                )
            self.waypoints.apply(
                {"action": "remove", "kind": "destination"}, gps
            )
            return self._finish_voice(
                action=action,
                status="accepted",
                message="현재 목적지와 목적지 경로를 삭제했습니다.",
                request_id=request_id,
            )

        if action == "find_nearest_water":
            if gps.get("fix") is not True:
                return self._finish_voice(
                    action=action,
                    status="rejected",
                    message="현재 GPS 수신이 없어 가까운 수원 표식을 찾을 수 없습니다.",
                    request_id=request_id,
                )
            poi = self.registry.nearest_poi(
                "water_source", float(gps["lat"]), float(gps["lon"])
            )
            if poi is None:
                return self._finish_voice(
                    action=action,
                    status="rejected",
                    message="현재 오프라인 지도에는 검수된 수원 표식이 없습니다.",
                    request_id=request_id,
                )
            with self._voice_lock:
                self._voice_state["pending_destination"] = poi
            return self._finish_voice(
                action=action,
                status="confirmation_required",
                message=(
                    f"가장 가까운 지도 표식은 {poi['name']}입니다. "
                    "수질은 확인되지 않았습니다. 이 위치를 목적지로 지정할까요?"
                ),
                request_id=request_id,
            )

        if action == "confirm_destination":
            with self._voice_lock:
                pending = self._voice_state.get("pending_destination")
            if not isinstance(pending, dict):
                return self._finish_voice(
                    action=action,
                    status="rejected",
                    message="확인할 목적지 후보가 없습니다.",
                    request_id=request_id,
                )
            self.waypoints.apply(
                {
                    "action": "set",
                    "kind": "destination",
                    "name": pending["name"],
                    "lat": pending["lat"],
                    "lon": pending["lon"],
                },
                gps,
                trusted_source=(
                    "demo_offline_catalog" if pending.get("demo") else "offline_catalog"
                ),
            )
            with self._voice_lock:
                self._voice_state["pending_destination"] = None
            return self._finish_voice(
                action=action,
                status="accepted",
                message="목적지로 설정했습니다. 경로와 방위, 거리는 지도 엔진이 계산해 화면에 표시합니다.",
                request_id=request_id,
            )

        if action in {"reject_destination", "cancel"}:
            with self._voice_lock:
                self._voice_state["pending_destination"] = None
            return self._finish_voice(
                action=action,
                status="accepted",
                message="목적지 지정을 취소했습니다." if action == "reject_destination" else "음성 지도 명령을 취소했습니다.",
                request_id=request_id,
            )

        if action in {"night_on", "night_off", "night_toggle"}:
            with self._voice_lock:
                current = bool(self._voice_state["ui"].get("night"))
                enabled = not current if action == "night_toggle" else action == "night_on"
                self._voice_state["ui"]["night"] = enabled
            return self._finish_voice(
                action=action,
                status="accepted",
                message=f"야간 모드를 {'켰습니다' if enabled else '껐습니다'}.",
                request_id=request_id,
            )

        return self._finish_voice(
            action=action,
            status="accepted",
            message="현재 지도와 장치 상태를 확인했습니다.",
            request_id=request_id,
        )

    @staticmethod
    def _location(gps: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if gps.get("fix") is True:
            return {"lat": gps["lat"], "lon": gps["lon"]}, "current_fix"
        last_fix = gps.get("last_fix")
        if isinstance(last_fix, dict):
            return {"lat": last_fix["lat"], "lon": last_fix["lon"]}, "last_fix"
        return None, "none"

    def _trail(self, gps: dict[str, Any], location: dict[str, Any] | None) -> dict[str, Any]:
        result = {
            "status": "unavailable",
            "offset_m": None,
            "threshold_m": TRAIL_THRESHOLD_M,
            "accuracy_m": gps.get("acc_m") if gps.get("fix") else None,
            "computed_by": "map_engine",
        }
        if location is None:
            return result
        try:
            offset = float(self.registry.trail_offset_m(location["lat"], location["lon"]))
        except (MapValidationError, ValueError):
            return result
        result["offset_m"] = round(offset, 1)
        if gps.get("fix") is not True:
            result["status"] = "last_fix_only"
            return result
        accuracy = gps.get("acc_m")
        if accuracy is None:
            result["status"] = "off_trail_estimate" if offset > TRAIL_THRESHOLD_M * 2.0 else "accuracy_unknown"
            return result
        accuracy = float(accuracy)
        if offset - accuracy > TRAIL_THRESHOLD_M:
            result["status"] = "off_trail"
        elif offset + accuracy <= TRAIL_THRESHOLD_M:
            result["status"] = "on_trail"
        else:
            result["status"] = "uncertain"
        return result

    def _route_to(
        self,
        gps: dict[str, Any],
        target: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if target is None:
            return {"available": False, "reason": "target_missing"}
        if gps.get("fix") is not True:
            return {"available": False, "reason": "gps_fix_required", "target": target}
        map_source = str(self.registry.overview().get("source_name") or "")
        cache_key = str(target.get("id") or target.get("kind") or "target")
        with self._route_lock:
            cached = self._route_cache.get(cache_key)
        cached_projection = None
        if cached:
            cached_projection = _polyline_progress(
                float(gps["lon"]),
                float(gps["lat"]),
                cached["payload"].get("coordinates") or [],
                minimum_progress_m=float(cached.get("progress_m") or 0.0),
            )
        if (
            cached
            and cached["map_source"] == map_source
            and cached["target_lat"] == float(target["lat"])
            and cached["target_lon"] == float(target["lon"])
            and cached_projection is not None
            and cached_projection[3] <= ROUTE_CACHE_MAX_CROSS_TRACK_M
        ):
            result = dict(cached["payload"])
            coordinates = result["coordinates"]
            (
                total_polyline_m,
                remaining_polyline_m,
                next_index,
                _,
                progress_m,
            ) = cached_projection
            original_distance_m = float(cached["payload"]["distance_m"])
            result["distance_m"] = round(
                original_distance_m * remaining_polyline_m / total_polyline_m,
                1,
            )
            next_point = target
            for lon, lat in coordinates[next_index:]:
                if haversine_m(float(gps["lon"]), float(gps["lat"]), lon, lat) > 3.0:
                    next_point = {"lat": lat, "lon": lon}
                    break
            result["bearing_deg"] = round(
                _bearing_degrees(
                    {"lat": gps["lat"], "lon": gps["lon"]},
                    next_point,
                )
            )
            result["straight_m"] = round(
                haversine_m(
                    float(gps["lon"]),
                    float(gps["lat"]),
                    float(target["lon"]),
                    float(target["lat"]),
                ),
                1,
            )
            result["eta_min"] = int(
                ceil(float(result["distance_m"]) / NAVIGATION_SPEED_MPS / 60.0)
            )
            with self._route_lock:
                current_cache = self._route_cache.get(cache_key)
                if current_cache is cached:
                    current_cache["progress_m"] = max(
                        float(current_cache.get("progress_m") or 0.0),
                        progress_m,
                    )
            return result
        try:
            route = self.registry.route_between(
                float(gps["lat"]),
                float(gps["lon"]),
                float(target["lat"]),
                float(target["lon"]),
            )
        except (MapValidationError, RouteNotFound, SnapOutOfBounds, ValueError) as exc:
            return {"available": False, "reason": str(exc), "target": target}
        coordinates = [list(point) for point in route.coordinates]
        current_coordinate = [float(gps["lon"]), float(gps["lat"])]
        target_coordinate = [float(target["lon"]), float(target["lat"])]
        if not coordinates or haversine_m(*current_coordinate, *coordinates[0]) > 1.0:
            coordinates.insert(0, current_coordinate)
        if haversine_m(*coordinates[-1], *target_coordinate) > 1.0:
            coordinates.append(target_coordinate)
        next_point = None
        for lon, lat in route.coordinates:
            if haversine_m(float(gps["lon"]), float(gps["lat"]), lon, lat) > 3.0:
                next_point = {"lat": lat, "lon": lon}
                break
        next_point = next_point or target
        route_distance_m = round(
            route.distance_m
            + float(getattr(route, "start_snap_m", 0.0))
            + float(getattr(route, "goal_snap_m", 0.0)),
            1,
        )
        payload = {
            "available": True,
            "computed_by": "map_engine",
            "target": target,
            "distance_m": route_distance_m,
            "eta_min": int(ceil(route_distance_m / NAVIGATION_SPEED_MPS / 60.0)),
            "straight_m": round(
                haversine_m(float(gps["lon"]), float(gps["lat"]), float(target["lon"]), float(target["lat"])),
                1,
            ),
            "bearing_deg": round(_bearing_degrees({"lat": gps["lat"], "lon": gps["lon"]}, next_point)),
            "coordinates": coordinates,
        }
        with self._route_lock:
            self._route_cache[cache_key] = {
                "map_source": map_source,
                "target_lat": float(target["lat"]),
                "target_lon": float(target["lon"]),
                "start_lat": float(gps["lat"]),
                "start_lon": float(gps["lon"]),
                "progress_m": 0.0,
                "payload": payload,
            }
        return payload

    @staticmethod
    def _arrival(gps: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "arrived": False,
            "status": "route_unavailable",
            "target": route.get("target") if isinstance(route, dict) else None,
            "radius_m": ARRIVAL_RADIUS_M,
            "max_accuracy_m": ARRIVAL_MAX_ACCURACY_M,
            "computed_by": "navigation_service",
        }
        if not route.get("available"):
            return result
        if gps.get("fix") is not True:
            result["status"] = "gps_fix_required"
            return result
        try:
            accuracy = float(gps["acc_m"])
        except (KeyError, TypeError, ValueError):
            result["status"] = "accuracy_required"
            return result
        if accuracy > ARRIVAL_MAX_ACCURACY_M:
            result["status"] = "accuracy_insufficient"
            return result
        try:
            straight_m = float(route["straight_m"])
        except (KeyError, TypeError, ValueError):
            return result
        threshold = max(ARRIVAL_RADIUS_M, accuracy)
        result["threshold_m"] = round(threshold, 1)
        result["straight_m"] = round(straight_m, 1)
        result["arrived"] = straight_m <= threshold
        result["status"] = "arrived" if result["arrived"] else "enroute"
        return result

    def _sun(
        self,
        gps: dict[str, Any],
        location: dict[str, Any] | None,
        basecamp_route: dict[str, Any],
        now: datetime | None,
    ) -> dict[str, Any]:
        if location is None:
            return {
                "computed": False,
                "reference": "none",
                "status": "gps_required",
                "sunrise": None,
                "sunset": None,
                "civil_end": None,
                "return_by": None,
                "remaining_min": None,
                "return_in_min": None,
                "level": "unknown",
            }
        solar = calculate_solar_times(
            location["lat"],
            location["lon"],
            now=now,
            local_tz=self.local_tz,
        )
        current = solar["now"]
        sunset = solar["sunset"]
        result: dict[str, Any] = {
            "computed": solar["computed"],
            "reference": "current_fix" if gps.get("fix") else "last_fix",
            "date": solar["date"],
            "timezone": solar["timezone"],
            "now": iso_or_none(current),
            "sunrise": iso_or_none(solar["sunrise"]),
            "sunrise_clock": clock_or_none(solar["sunrise"]),
            "sunset": iso_or_none(sunset),
            "sunset_clock": clock_or_none(sunset),
            "civil_end": iso_or_none(solar["civil_end"]),
            "civil_end_clock": clock_or_none(solar["civil_end"]),
            "remaining_min": solar["remaining_min"],
            "return_by": None,
            "return_by_clock": None,
            "return_in_min": None,
            "travel_min": None,
            "margin_min": RETURN_MARGIN_MIN,
            "status": "basecamp_required",
            "level": "unknown",
        }
        if gps.get("fix") is not True:
            result["status"] = "last_fix_only"
        elif not basecamp_route.get("available"):
            result["status"] = (
                "basecamp_required"
                if basecamp_route.get("reason") == "target_missing"
                else "route_unavailable"
            )
        elif sunset is not None:
            travel_min = int(ceil(float(basecamp_route["distance_m"]) / RETURN_SPEED_MPS / 60.0))
            return_by = sunset - timedelta(minutes=travel_min + RETURN_MARGIN_MIN)
            return_in_min = int(floor((return_by - current).total_seconds() / 60.0))
            if return_in_min <= 0:
                status = "return_now"
                level = "danger"
            elif return_in_min <= DAYLIGHT_CAUTION_MIN:
                status = "caution"
                level = "caution"
            else:
                status = "scheduled"
                level = "normal"
            result.update(
                {
                    "travel_min": travel_min,
                    "return_by": iso_or_none(return_by),
                    "return_by_clock": clock_or_none(return_by),
                    "return_in_min": return_in_min,
                    "status": status,
                    "level": level,
                }
            )
        else:
            result["status"] = "sun_event_unavailable"
        return result

    @staticmethod
    def _alert(co: dict[str, Any], trail: dict[str, Any], sun: dict[str, Any]) -> dict[str, Any] | None:
        # sound=True 인 경보는 Jetson 스피커가 소리를 낸다(Co-LLM device_monitor.py).
        # 2026-08-31 STM32 부저를 걷어낸 뒤로 소리를 내는 곳은 여기 하나뿐이다.
        stale = bool(co.get("stale"))
        if co.get("alarm") is True and not stale:
            ppm = co.get("ppm")
            value = "—" if ppm is None else f"{float(ppm):.0f}"
            return {
                "kind": "co_alarm",
                "severity": "alarm",
                "text": f"CO 경보 · {value} ppm · 즉시 환기하고 대피하세요",
                "sound": True,
            }
        if co.get("level") == "warning" and not stale:
            ppm = co.get("ppm")
            value = "—" if ppm is None else f"{float(ppm):.0f}"
            return {
                "kind": "co_warning",
                "severity": "caution",
                "text": f"CO 주의 · {value} ppm · 환기하고 상태를 확인하세요",
                "sound": True,
            }
        if trail.get("status") == "off_trail":
            return {
                "kind": "trail",
                "severity": "alarm",
                "text": f"트레일 이탈 · {float(trail['offset_m']):.0f} m · 현재 위치를 확인하세요",
                "sound": False,
            }
        if sun.get("status") == "return_now":
            return {
                "kind": "daylight",
                "severity": "alarm",
                "text": "귀환 권고 시각 도달 · 베이스캠프 경로를 확인하세요",
                "sound": False,
            }
        return None

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        gps = self.gps.snapshot()
        device_now, clock_source = self._device_now(gps, now)
        waypoints = self.waypoints.snapshot()
        selected = self.waypoints.selected_point()
        location, location_kind = self._location(gps)
        trail = self._trail(gps, location)
        physical_output = self._trail_output_snapshot()
        gps_output = gps.get("hardware_output") or {}
        physical_output["confirmed"] = bool(
            gps_output.get("confirmed")
            and physical_output.get("last_action") == gps_output.get("last_requested")
        )
        trail["physical_output"] = physical_output
        active_route = self._route_to(gps, selected)
        arrival = self._arrival(gps, active_route)
        basecamp = waypoints.get("basecamp")
        basecamp_route = active_route if selected and selected.get("id") == "basecamp" else self._route_to(gps, basecamp)
        sun = self._sun(gps, location, basecamp_route, device_now)
        co = gps["co"]
        overview = self.registry.overview()
        stored_points = [waypoints.get("basecamp"), waypoints.get("destination")]
        stored_points.extend(waypoints.get("checkpoints") or [])
        demo_waypoint = any(
            isinstance(point, dict) and str(point.get("source", "")).startswith("demo_")
            for point in stored_points
        )
        demo = bool(gps.get("demo") or overview.get("demo") or demo_waypoint)
        alert = self._alert(co, trail, sun)
        voice = self.voice_snapshot()
        return {
            "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "offline": True,
            "demo": demo,
            "map": {
                "name": overview.get("name") or overview.get("source_name") or "오프라인 보행 지도",
                "source_name": overview.get("source_name"),
                "demo": bool(overview.get("demo")),
            },
            "gps": gps,
            "clock": {
                "source": clock_source,
                "confirmed": clock_source == "ds3231",
                "iso_utc": device_now.astimezone(timezone.utc).isoformat(timespec="seconds"),
                "local_iso": device_now.isoformat(timespec="seconds"),
            },
            "environment": gps["environment"],
            "co": co,
            "power": gps["power"],
            "position_history": self.position_history.summary(),
            "location_reference": location_kind,
            "trail": trail,
            "sun": sun,
            "waypoints": waypoints,
            "navigation": {
                "selected_target": waypoints.get("selected_target"),
                "active_route": active_route,
                "basecamp_route": basecamp_route,
                "arrival": arrival,
            },
            "interface": voice["ui"],
            "voice": voice,
            "alert": alert,
            "llm_read_only": {
                "gps": {
                    "fix": gps.get("fix", False),
                    "lat": gps.get("lat") if gps.get("fix") else None,
                    "lon": gps.get("lon") if gps.get("fix") else None,
                    "acc_m": gps.get("acc_m") if gps.get("fix") else None,
                    "age_s": gps.get("age_s") if gps.get("fix") else gps.get("last_age_s"),
                },
                "time": {
                    "sunset": sun.get("sunset_clock"),
                    "remaining_min": sun.get("remaining_min"),
                    "return_by": sun.get("return_by_clock"),
                },
                "env": {
                    "temp_c": gps["environment"].get("temp_c") if gps["environment"].get("valid") else None,
                    "humidity": gps["environment"].get("humidity_pct") if gps["environment"].get("valid") else None,
                    "press_hpa": (
                        gps["environment"].get("press_hpa")
                        if gps["environment"].get("pressure_valid")
                        else None
                    ),
                    "press_trend": (
                        gps["environment"].get("press_trend")
                        if gps["environment"].get("pressure_valid")
                        else "unknown"
                    ),
                },
                "route": {
                    "status": trail.get("status"),
                    "offset_m": trail.get("offset_m"),
                    "distance_m": active_route.get("distance_m") if active_route.get("available") else None,
                },
            },
            "contract": {
                "map_route_bearing_distance_computed_by_code": True,
                "solar_computed_offline": True,
                "llm_may_select_saved_waypoint_only": True,
                "llm_may_generate_coordinates": False,
                "voice_commands_enum_only": True,
                "recent_trace_route_computed_by_code": True,
                "position_history_coordinates_runtime_only": True,
                "physical_checkpoint_button_uses_current_fix_only": True,
                "voice_commands_accept_coordinates": False,
            },
        }
