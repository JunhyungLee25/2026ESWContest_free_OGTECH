"""OGTECH Kit 오프라인 지도 변환 검증 웹앱."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from math import isfinite
import mimetypes
import os
from pathlib import Path
from queue import Empty
import shutil
import sys
import threading
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4
import wave

from map_engine import (
    DEFAULT_MAX_SNAP_M,
    MapValidationError,
    OfflineMap,
    RouteNotFound,
    SnapOutOfBounds,
    haversine_m,
    load_map_source,
    load_runtime,
)
from gps_service import GpsInputError, GpsService
from navigation_service import NavigationInputError, NavigationService
from speech_service import SpeechService, SpeechUnavailable, warmup as warmup_speech


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
PRODUCT_ROOT = ROOT / "kiosk"
RUNTIME_ROOT = ROOT / "runtime"
UPLOAD_ROOT = RUNTIME_ROOT / "uploads"
ACTIVE_MAP = RUNTIME_ROOT / "active_map.json"
SAMPLE_MAP = ROOT / "sample_data" / "konkuk_walk.graphml"
GPS_REPLAY = ROOT / "sample_data" / "air530_replay.nmea"
WAYPOINTS_PATH = RUNTIME_ROOT / "waypoints.json"

# 화면 문구를 읽어 주는 오프라인 TTS. 프로세스 하나에 모델 하나만 상주한다.
SPEECH = SpeechService()
POI_CATALOG_PATH = PRODUCT_ROOT / "poi_catalog.json"
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
ALLOWED_SUFFIXES = {".graphml", ".osm", ".xml"}

SAMPLE_POINTS = {
    "current": {"lat": 37.5465126, "lon": 127.0757141},
    "destination": {"lat": 37.5405289551, "lon": 127.0794396497},
}


class MapRegistry:
    """현재 활성 지도와 런타임 파일 교체를 직렬화한다."""

    def __init__(self, *, force_sample: bool = False) -> None:
        self._lock = threading.RLock()
        self._force_sample = force_sample
        self._import_status: dict[str, Any] = {
            "state": "idle",
            "stage": "지도 업로드 대기",
            "percent": 0,
        }
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        self._map = self._load_initial_map()
        self._overview_cache = self._make_overview(self._map)
        self._poi_catalog = self._load_poi_catalog()

    @staticmethod
    def _load_poi_catalog() -> dict[str, Any]:
        """시연 지도와 출처가 일치할 때만 쓸 로컬 POI 카탈로그를 읽는다."""
        try:
            payload = json.loads(POI_CATALOG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "map_sources": [], "demo": False, "pois": []}
        if payload.get("version") != 1 or not isinstance(payload.get("pois"), list):
            return {"version": 1, "map_sources": [], "demo": False, "pois": []}
        return payload

    def _load_initial_map(self) -> OfflineMap:
        if self._force_sample:
            return load_map_source(SAMPLE_MAP)
        if ACTIVE_MAP.exists():
            try:
                return load_runtime(ACTIVE_MAP)
            except MapValidationError as exc:
                print(f"활성 런타임 지도 복구 실패, 샘플로 전환: {exc}")
        offline_map = load_map_source(SAMPLE_MAP)
        offline_map.write_runtime(ACTIVE_MAP)
        return offline_map

    @staticmethod
    def _make_overview(offline_map: OfflineMap) -> dict[str, Any]:
        result = offline_map.overview()
        if offline_map.source_name == SAMPLE_MAP.name:
            result["suggested_points"] = SAMPLE_POINTS
        result["demo"] = offline_map.source_name == SAMPLE_MAP.name
        return result

    def overview(self) -> dict[str, Any]:
        with self._lock:
            return self._overview_cache

    def import_status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._import_status)

    def update_import(self, *, state: str, stage: str, percent: int) -> None:
        with self._lock:
            self._import_status = {
                "state": state,
                "stage": stage,
                "percent": max(0, min(100, percent)),
            }

    def fail_import(self, message: str) -> None:
        self.update_import(state="failed", stage=message, percent=0)

    def activate(self, source_path: Path, original_name: str) -> dict[str, Any]:
        safe_name = _safe_filename(original_name)
        try:
            self.update_import(state="processing", stage="그래프 구조와 연결망 검사 중", percent=30)
            converted = load_map_source(source_path)
            converted.source_name = safe_name
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            stored_name = f"{stamp}_{uuid4().hex[:8]}_{safe_name}"
            self.update_import(state="processing", stage="원본 지도 보관 중", percent=60)
            shutil.copy2(source_path, UPLOAD_ROOT / stored_name)
            self.update_import(state="processing", stage="Jetson 런타임 지도 저장 중", percent=75)
            converted.write_runtime(ACTIVE_MAP)
            self.update_import(state="processing", stage="전체 영역 화면 표본 생성 중", percent=90)
            overview = self._make_overview(converted)
            with self._lock:
                self._map = converted
                self._overview_cache = overview
                self._import_status = {
                    "state": "complete",
                    "stage": (
                        f"변환 완료 · 노드 {converted.graph.number_of_nodes():,} · "
                        f"엣지 {converted.graph.number_of_edges():,}"
                    ),
                    "percent": 100,
                }
            return overview
        except Exception as exc:
            self.fail_import(str(exc))
            raise

    def route(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = _point(payload.get("current"), "현재 위치")
        destination = _point(payload.get("destination"), "목적지")
        raw_accuracy = payload.get("accuracy_m", 10)
        accuracy_m = (
            None
            if raw_accuracy is None
            else _finite_number(raw_accuracy, "정확도", 0, 10_000)
        )
        max_snap_m = _finite_number(
            payload.get("max_snap_m", DEFAULT_MAX_SNAP_M),
            "최대 스냅 거리",
            1,
            2_000,
        )
        source = payload.get("source", "demo")
        if source not in {"demo", "sensor"}:
            raise MapValidationError("위치 출처는 demo 또는 sensor여야 합니다")
        sensor_fix = source == "sensor" and payload.get("fix") is True
        satellites = int(_finite_number(payload.get("satellites", 0), "위성 수", 0, 99))
        age_s = _finite_number(payload.get("age_s", 0), "좌표 경과 시간", 0, 86_400)

        with self._lock:
            result = self._map.find_route(
                start_lat=current["lat"],
                start_lon=current["lon"],
                goal_lat=destination["lat"],
                goal_lon=destination["lon"],
                max_snap_m=max_snap_m,
            )
            # /api/device와 같은 선분 거리. start_snap_m은 최근접 노드 거리라 직선 보행로 중간에서 과장된다(WORKLOG #17).
            offset_m = self._map.trail_offset_m(current["lat"], current["lon"])

        route_payload = result.as_dict()
        next_point = result.coordinates[1] if len(result.coordinates) > 1 else result.coordinates[0]
        next_wp_m = haversine_m(
            current["lon"], current["lat"], next_point[0], next_point[1]
        )
        on_trail = offset_m <= max(20.0, accuracy_m or 0.0)
        device_state = {
            "gps": {
                "fix": sensor_fix,
                "lat": current["lat"],
                "lon": current["lon"],
                "acc_m": accuracy_m,
                "satellites": satellites,
                "age_s": age_s,
                "source": source,
            },
            "route": {
                "on_trail": on_trail,
                "offset_m": round(offset_m, 1),
                "next_wp_m": round(next_wp_m, 1),
            },
        }
        return {
            "demo": source != "sensor",
            "current": current,
            "destination": destination,
            "route": route_payload,
            "device_state": device_state,
            "contract": {
                "map_and_route_computed_by_code": True,
                "llm_may_only_read_device_state": True,
                "llm_route_generation_allowed": False,
            },
        }

    def trail_offset_m(self, lat: float, lon: float) -> float:
        with self._lock:
            return self._map.trail_offset_m(lat, lon)

    def route_between(
        self,
        start_lat: float,
        start_lon: float,
        goal_lat: float,
        goal_lon: float,
    ) -> Any:
        with self._lock:
            return self._map.find_route(
                start_lat=start_lat,
                start_lon=start_lon,
                goal_lat=goal_lat,
                goal_lon=goal_lon,
            )

    def nearest_poi(self, kind: str, lat: float, lon: float) -> dict[str, Any] | None:
        """현재 활성 지도와 일치하는 로컬 표식 중 가장 가까운 항목을 코드로 고른다."""
        if kind not in {"water_source"}:
            raise MapValidationError("지원하지 않는 POI 종류입니다")
        with self._lock:
            active_source = self._map.source_name
            catalog = self._poi_catalog
            catalog_sources = catalog.get("map_sources")
            if not isinstance(catalog_sources, list):
                catalog_sources = [catalog.get("map_source")]
            if active_source not in catalog_sources:
                return None
            candidates: list[dict[str, Any]] = []
            for raw in catalog.get("pois", []):
                if not isinstance(raw, dict) or raw.get("kind") != kind:
                    continue
                try:
                    poi_lat = _finite_number(raw.get("lat"), "POI 위도", -90, 90)
                    poi_lon = _finite_number(raw.get("lon"), "POI 경도", -180, 180)
                except MapValidationError:
                    continue
                item = dict(raw)
                item["lat"] = poi_lat
                item["lon"] = poi_lon
                item["distance_m"] = round(haversine_m(lon, lat, poi_lon, poi_lat), 1)
                item["demo"] = bool(catalog.get("demo"))
                candidates.append(item)
            return min(candidates, key=lambda item: item["distance_m"], default=None)

    def diagnostics(self) -> dict[str, Any]:
        """활성 지도와 그 지도에 귀속된 POI 카탈로그를 읽기 전용으로 점검한다."""
        with self._lock:
            overview = dict(self._overview_cache)
            active_source = self._map.source_name
            catalog = dict(self._poi_catalog)
        statistics = overview.get("statistics") or {}
        node_count = int(statistics.get("nodes") or 0)
        edge_count = int(statistics.get("edges") or 0)
        raw_sources = catalog.get("map_sources")
        catalog_sources = (
            raw_sources
            if isinstance(raw_sources, list)
            else [catalog.get("map_source")]
        )
        poi_count = 0
        if active_source in catalog_sources:
            for item in catalog.get("pois") or []:
                if not isinstance(item, dict) or item.get("kind") != "water_source":
                    continue
                try:
                    lat = float(item["lat"])
                    lon = float(item["lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                if isfinite(lat) and isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                    poi_count += 1
        return {
            "map": {
                "ok": node_count > 0 and edge_count > 0,
                "source_name": active_source,
                "nodes": node_count,
                "edges": edge_count,
                "demo": bool(overview.get("demo")),
            },
            "poi": {
                "ok": active_source in catalog_sources and poi_count > 0,
                "active_source_match": active_source in catalog_sources,
                "water_source_count": poi_count,
                "demo": bool(catalog.get("demo")),
            },
        }


def _safe_filename(value: str) -> str:
    basename = Path(unquote(value)).name
    cleaned = "".join(
        character if character.isalnum() or character in {".", "-", "_"} else "_"
        for character in basename
    )
    return cleaned[:120] or "offline_map.graphml"


def _finite_number(value: Any, label: str, lower: float, upper: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MapValidationError(f"{label} 값이 숫자가 아닙니다") from exc
    if not isfinite(number) or not lower <= number <= upper:
        raise MapValidationError(f"{label} 값이 허용 범위를 벗어났습니다")
    return number


def _point(value: Any, label: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise MapValidationError(f"{label} 객체가 없습니다")
    return {
        "lat": _finite_number(value.get("lat"), f"{label} 위도", -90, 90),
        "lon": _finite_number(value.get("lon"), f"{label} 경도", -180, 180),
    }


class AppHandler(BaseHTTPRequestHandler):
    """외부 프레임워크 없이 정적 앱과 JSON API를 제공한다."""

    registry: MapRegistry
    gps: GpsService
    navigation: NavigationService
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - 표준 라이브러리 규약
        self._response_started = False  # keep-alive는 같은 핸들러를 재사용 — 요청마다 초기화(WORKLOG #16)
        try:
            path = urlsplit(self.path).path
            if path == "/api/health":
                self._json(HTTPStatus.OK, {"status": "ok", "offline": True})
                return
            if path == "/api/map":
                self._json(HTTPStatus.OK, self.registry.overview())
                return
            if path == "/api/import-status":
                self._json(HTTPStatus.OK, self.registry.import_status())
                return
            if path == "/api/gps":
                self._json(HTTPStatus.OK, self.gps.snapshot())
                return
            if path == "/api/gps/ports":
                self._json(HTTPStatus.OK, {"ports": self.gps.ports()})
                return
            if path == "/api/gps/events":
                self._gps_events()
                return
            if path == "/api/buttons":
                self._json(HTTPStatus.OK, self.gps.snapshot()["hardware_buttons"])
                return
            if path == "/api/buttons/events":
                self._button_events()
                return
            if path == "/api/device":
                self._json(HTTPStatus.OK, self.navigation.snapshot())
                return
            if path == "/api/tts":
                self._tts(urlsplit(self.path).query)
                return
            if path == "/api/diagnostics":
                self._json(HTTPStatus.OK, self._diagnostics())
                return
            if path == "/api/device/events":
                self._device_events()
                return
            if path == "/api/waypoints":
                self._json(HTTPStatus.OK, self.navigation.waypoints.snapshot())
                return
            if path == "/api/voice":
                self._json(HTTPStatus.OK, self.navigation.voice_snapshot())
                return
            if path == "/api/voice/events":
                self._voice_events()
                return
            if path == "/":
                path = "/index.html"
            self._static(path)

        except (
            GpsInputError,
            NavigationInputError,
            MapValidationError,
            RouteNotFound,
            SnapOutOfBounds,
        ) as exc:
            self._error_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # 클라이언트가 먼저 끊음 - SSE 스트림 등
        except Exception as exc:
            print(f"요청 처리 실패: {exc}")
            self._error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "요청 처리 중 내부 오류가 발생했습니다"},
            )

    @staticmethod
    def _wav_ready(path: Path) -> bool:
        try:
            with wave.open(str(path), "rb") as stream:
                return (
                    stream.getcomptype() == "NONE"
                    and stream.getsampwidth() == 2
                    and stream.getnchannels() in {1, 2}
                    and stream.getnframes() > 0
                )
        except (OSError, EOFError, wave.Error):
            return False

    def _diagnostics(self) -> dict[str, Any]:
        map_checks = self.registry.diagnostics()
        gps = self.gps.snapshot()
        now = datetime.now(self.navigation.local_tz)
        system_clock_ok = 2025 <= now.year <= 2100 and now.utcoffset() is not None
        rtc = gps.get("rtc") or {}
        rtc_ready = bool(rtc.get("valid") and not rtc.get("stale") and rtc.get("iso_utc"))
        required_audio = {
            "목적지 확인": PRODUCT_ROOT / "destination_confirmed.wav",
            "목적지 도착": PRODUCT_ROOT / "destination_arrived.wav",
            "베이스캠프 도착": PRODUCT_ROOT / "return_to_base.wav",
            "TTS 실패 안내": PRODUCT_ROOT / "tts_unavailable.wav",
        }
        audio_states = {
            label: self._wav_ready(path) for label, path in required_audio.items()
        }
        environment = gps.get("environment") or {}
        co = gps.get("co") or {}
        sensor_ready = bool(
            environment.get("valid")
            and environment.get("pressure_valid")
            and not environment.get("stale")
            and co.get("valid")
            and not co.get("stale")
        )
        gps_state = (
            "demo"
            if gps.get("fix") is True and gps.get("demo")
            else "pass"
            if gps.get("fix") is True
            else "waiting"
        )
        sensor_state = (
            "demo"
            if sensor_ready and gps.get("demo")
            else "pass"
            if sensor_ready
            else "waiting"
        )
        checks = [
            {
                "id": "map",
                "label": "오프라인 지도",
                "state": (
                    "demo"
                    if map_checks["map"]["ok"] and map_checks["map"]["demo"]
                    else "pass"
                    if map_checks["map"]["ok"]
                    else "fail"
                ),
                "detail": (
                    f"노드 {map_checks['map']['nodes']:,} · "
                    f"엣지 {map_checks['map']['edges']:,}"
                ),
            },
            {
                "id": "poi",
                "label": "검수 POI",
                "state": (
                    "demo"
                    if map_checks["poi"]["ok"] and map_checks["poi"]["demo"]
                    else "pass"
                    if map_checks["poi"]["ok"]
                    else "fail"
                ),
                "detail": f"수원 표식 {map_checks['poi']['water_source_count']}개",
            },
            {
                "id": "clock",
                "label": "DS3231 RTC",
                "state": "pass" if rtc_ready else "waiting" if system_clock_ok else "fail",
                "detail": (
                    f"STM32 확인 · {rtc.get('iso_utc')}"
                    if rtc_ready
                    else "RTC 미연동 · 시스템만"
                    if system_clock_ok
                    else "RTC 미연동 · 시스템 시각도 유효하지 않음"
                ),
            },
            {
                "id": "gps",
                "label": "GPS",
                "state": gps_state,
                "detail": (
                    f"수신 · 위성 {gps.get('satellites') or '—'}개"
                    if gps.get("fix") is True
                    else "fix 대기 · 위치 추정 안 함"
                ),
            },
            {
                "id": "sensors",
                "label": "환경 센서",
                "state": sensor_state,
                "detail": (
                    "SHT40·BMP390·CO 계측 수신"
                    if sensor_ready
                    else " · ".join(
                        (
                            f"SHT40 {'확인' if environment.get('valid') else '대기'}",
                            f"BMP390 {'확인' if environment.get('pressure_valid') else '대기'}",
                            f"CO {'확인' if co.get('valid') else '대기'}",
                        )
                    )
                ),
            },
            {
                "id": "audio",
                "label": "고정 음성 파일",
                "state": "pass" if all(audio_states.values()) else "fail",
                "detail": (
                    f"PCM {sum(audio_states.values())}/{len(audio_states)} · 출력 미검사"
                ),
            },
        ]
        critical_ids = {"map", "poi", "clock", "audio"}
        critical_fail = any(
            item["state"] == "fail" for item in checks if item["id"] in critical_ids
        )
        if critical_fail:
            overall = "degraded"
        elif any(item["state"] == "demo" for item in checks):
            overall = "demo"
        elif any(item["state"] == "waiting" for item in checks):
            overall = "waiting"
        else:
            overall = "ready"
        return {
            "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "offline": True,
            "overall": overall,
            "checks": checks,
            "audio_files": audio_states,
            "contract": {
                "gps_wait_is_not_a_position_fix": True,
                "sensor_wait_is_not_reported_as_live": True,
                "network_required": False,
                "rtc_is_not_claimed_without_stm32_evidence": True,
                "fixed_audio_check_is_file_only": True,
            },
        }

    def do_POST(self) -> None:  # noqa: N802 - 표준 라이브러리 규약
        self._response_started = False  # keep-alive 재사용 핸들러 — 요청마다 초기화(WORKLOG #16)
        path = urlsplit(self.path).path
        try:
            if path == "/api/gps/configure":
                payload = self._read_json()
                mode = str(payload.get("mode", "off"))
                port = str(payload.get("port", "")).strip()
                raw_baud = payload.get("baud")
                try:
                    baud = int(raw_baud) if raw_baud not in {None, ""} else None
                except (TypeError, ValueError) as exc:
                    raise GpsInputError("baud는 정수여야 합니다") from exc
                snapshot = self.gps.configure(mode=mode, port=port, baud=baud)
                self._json(HTTPStatus.ACCEPTED, snapshot)
                return
            if path == "/api/gps/stop":
                self._discard_body()
                snapshot = self.gps.configure(mode="off")
                self._json(HTTPStatus.OK, snapshot)
                return
            if path == "/api/maps/import":
                self._import_map()
                return
            if path == "/api/route":
                payload = self._read_json()
                self._json(HTTPStatus.OK, self.registry.route(payload))
                return
            if path == "/api/waypoints":
                payload = self._read_json()
                self._json(HTTPStatus.OK, self.navigation.apply_waypoint(payload))
                return
            if path == "/api/voice/commands":
                payload = self._read_json()
                self._json(HTTPStatus.OK, self.navigation.apply_voice_command(payload))
                return
            if path == "/api/power/shutdown-ack":
                payload = self._read_json()
                if payload:
                    raise GpsInputError("전원 종료 ACK에는 인자를 사용할 수 없습니다")
                if not self.gps.request_stm32_power_shutdown_ack():
                    raise GpsInputError(
                        "검증된 STM32 종료 대기 상태가 없어 종료 ACK를 보낼 수 없습니다"
                    )
                self._json(
                    HTTPStatus.OK,
                    {
                        "queued": True,
                        "command": "power_shutdown_ack",
                        "coordinates_accepted": False,
                        "gate_cut_requires_stm32_pending_request": True,
                    },
                )
                return
            if path == "/api/power/shutdown-cancel":
                payload = self._read_json()
                if payload:
                    raise GpsInputError("전원 종료 취소에는 인자를 사용할 수 없습니다")
                if not self.gps.request_stm32_power_shutdown_cancel():
                    raise GpsInputError(
                        "취소할 STM32 전원 종료 transaction이 없습니다 (POWER OFF ACK 대기 없음)"
                    )
                self._json(
                    HTTPStatus.OK,
                    {
                        "queued": True,
                        "command": "power_shutdown_cancel",
                        "coordinates_accepted": False,
                        "cancels_only_current_power_transaction": True,
                    },
                )
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "API 경로가 없습니다"})
        except (
            GpsInputError,
            NavigationInputError,
            MapValidationError,
            RouteNotFound,
            SnapOutOfBounds,
        ) as exc:
            self._error_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            print(f"요청 처리 실패: {exc}")
            self._error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "요청 처리 중 내부 오류가 발생했습니다"},
            )

    def _read_length(self, maximum: int) -> int:
        raw = self.headers.get("Content-Length")
        try:
            length = int(raw or "")
        except ValueError as exc:
            raise MapValidationError("Content-Length가 올바르지 않습니다") from exc
        if length <= 0:
            raise MapValidationError("빈 요청은 처리할 수 없습니다")
        if length > maximum:
            raise MapValidationError(f"파일 크기는 {maximum // (1024 * 1024)}MB 이하여야 합니다")
        return length

    def _read_json(self) -> dict[str, Any]:
        length = self._read_length(MAX_JSON_BYTES)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MapValidationError("JSON 요청 형식이 올바르지 않습니다") from exc
        if not isinstance(payload, dict):
            raise MapValidationError("JSON 루트는 객체여야 합니다")
        return payload

    def _discard_body(self) -> None:
        raw = self.headers.get("Content-Length", "0")
        try:
            length = int(raw)
        except ValueError as exc:
            raise MapValidationError("Content-Length가 올바르지 않습니다") from exc
        if not 0 <= length <= MAX_JSON_BYTES:
            raise MapValidationError("요청 본문 크기가 허용 범위를 벗어났습니다")
        if length:
            self.rfile.read(length)

    def _import_map(self) -> None:
        length = self._read_length(MAX_UPLOAD_BYTES)
        encoded_name = self.headers.get("X-Filename", "offline_map.graphml")
        filename = _safe_filename(encoded_name)
        if Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
            raise MapValidationError("지원 형식은 .graphml, .osm, .xml입니다")
        self.registry.update_import(
            state="processing", stage="지도 파일 수신 중", percent=10
        )
        temporary = RUNTIME_ROOT / f"upload-{uuid4().hex}{Path(filename).suffix.lower()}"
        try:
            temporary.write_bytes(self.rfile.read(length))
            self.registry.update_import(
                state="processing", stage="파일 수신 완료 · 변환 준비 중", percent=20
            )
            overview = self.registry.activate(temporary, filename)
        except Exception as exc:
            self.registry.fail_import(str(exc))
            raise
        finally:
            temporary.unlink(missing_ok=True)
        self._json(HTTPStatus.CREATED, overview)

    def _tts(self, query: str) -> None:
        """화면에 뜬 문구를 제품 음성과 같은 목소리로 합성해 돌려준다.

        브라우저 speechSynthesis 를 쓰지 않는 이유는 speech_service 머리말에 적었다.
        모델이 없으면 503 을 주고, 화면은 글자만 보여 주면 된다(음성은 보조 수단).
        """
        text = ""
        for pair in query.split("&"):
            name, _, value = pair.partition("=")
            if name == "text":
                text = unquote(value.replace("+", " "))
                break
        try:
            data = SPEECH.synthesize(text)
        except SpeechUnavailable as exc:
            # 모델이 없는 환경은 오류가 아니라 "이 장치에는 음성이 없다"는 답이다.
            # 4xx/5xx 로 돌려주면 키오스크 콘솔이 리소스 오류로 계속 더럽혀진다.
            self._json(HTTPStatus.OK, {"available": False, "reason": str(exc)})
            return
        except Exception as exc:  # 합성 실패가 화면을 멈추게 하지 않는다
            self._json(HTTPStatus.OK, {"available": False, "reason": f"음성 합성 실패: {exc}"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, request_path: str) -> None:
        mapping = {
            "/index.html": STATIC_ROOT / "index.html",
            "/app.js": STATIC_ROOT / "app.js",
            "/styles.css": STATIC_ROOT / "styles.css",
            "/select": PRODUCT_ROOT / "select.html",
            "/select/": PRODUCT_ROOT / "select.html",
            "/select/index.html": PRODUCT_ROOT / "select.html",
            "/select/styles.css": PRODUCT_ROOT / "styles.css",
            # 제품 화면과 촬영 화면은 같은 마크업·CSS·그리기 코드를 쓴다. 디자인이
            # 갈리지 않게 화면 코드를 한 벌만 둔다(video_app.js 가 경로로 모드를 가른다).
            "/product": PRODUCT_ROOT / "video.html",
            "/product/": PRODUCT_ROOT / "video.html",
            "/product/index.html": PRODUCT_ROOT / "video.html",
            "/product/styles.css": PRODUCT_ROOT / "styles.css",
            "/product/video_styles.css": PRODUCT_ROOT / "video_styles.css",
            "/product/video_app.js": PRODUCT_ROOT / "video_app.js",
            "/product/video_map.js": PRODUCT_ROOT / "video_map.js",
            "/product/destination_set.wav": PRODUCT_ROOT / "destination_set.wav",
            "/product/destination_arrived.wav": PRODUCT_ROOT / "destination_arrived.wav",
            "/product/return_to_base.wav": PRODUCT_ROOT / "return_to_base.wav",
            "/video": PRODUCT_ROOT / "video.html",
            "/video/": PRODUCT_ROOT / "video.html",
            "/video/index.html": PRODUCT_ROOT / "video.html",
            "/video/styles.css": PRODUCT_ROOT / "styles.css",
            "/video/video_styles.css": PRODUCT_ROOT / "video_styles.css",
            "/video/video_app.js": PRODUCT_ROOT / "video_app.js",
            "/video/video_map.js": PRODUCT_ROOT / "video_map.js",
            "/video/destination_set.wav": PRODUCT_ROOT / "destination_set.wav",
            "/video/destination_arrived.wav": PRODUCT_ROOT / "destination_arrived.wav",
            "/video/return_to_base.wav": PRODUCT_ROOT / "return_to_base.wav",
        }
        target = mapping.get(request_path)
        if not target or not target.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "파일이 없습니다"})
            return
        data = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _gps_events(self) -> None:
        subscriber = self.gps.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    payload = subscriber.get(timeout=10.0)
                except Empty:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    encoded = json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    self.wfile.write(b"data: " + encoded + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.gps.unsubscribe(subscriber)

    def _device_events(self) -> None:
        subscriber = self.gps.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    subscriber.get(timeout=10.0)
                except Empty:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    encoded = json.dumps(
                        self.navigation.snapshot(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    self.wfile.write(b"data: " + encoded + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.gps.unsubscribe(subscriber)

    def _button_events(self) -> None:
        """좌표 없이 검증된 새 물리 버튼 edge만 전달한다."""
        subscriber = self.gps.subscribe_buttons()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    payload = subscriber.get(timeout=10.0)
                except Empty:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    encoded = json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    self.wfile.write(b"data: " + encoded + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.gps.unsubscribe_buttons(subscriber)

    def _voice_events(self) -> None:
        subscriber = self.navigation.subscribe_voice()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    payload = subscriber.get(timeout=10.0)
                except Empty:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    encoded = json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    self.wfile.write(b"data: " + encoded + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.navigation.unsubscribe_voice(subscriber)

    def end_headers(self) -> None:
        # 응답 헤더가 한 번이라도 나가면 같은 요청 안에서 _json 재응답을 막는다(SSE 스트림 보호).
        # do_GET/do_POST 진입 시 리셋되므로 keep-alive 다음 요청에는 영향을 주지 않는다.
        self._response_started = True
        super().end_headers()

    def _error_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        if getattr(self, "_response_started", False):
            return  # 이미 응답(헤더)이 나간 뒤 — 프로토콜을 오염시키지 않고 조용히 종료
        self._json(status, payload)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        if urlsplit(self.path).path == "/api/import-status":
            return
        print(f"{self.address_string()} - {format % args}")


class GpsAppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[AppHandler],
        gps: GpsService,
        navigation: NavigationService,
    ) -> None:
        self.gps = gps
        self.navigation = navigation
        super().__init__(address, handler)

    def server_close(self) -> None:
        self.navigation.close()
        self.gps.close()
        super().server_close()

    def handle_error(self, request: Any, client_address: tuple[str, int]) -> None:
        """SSE 탭 종료에서 생기는 정상 연결 해제는 traceback으로 남기지 않는다."""
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def build_server(
    host: str,
    port: int,
    gps_configuration: dict[str, Any] | None = None,
    waypoint_path: str | Path | None = None,
    *,
    force_sample_map: bool = False,
) -> ThreadingHTTPServer:
    registry = MapRegistry(force_sample=force_sample_map)
    gps = GpsService(GPS_REPLAY)
    configuration = gps_configuration or {"mode": "off"}
    gps.configure(
        mode=str(configuration.get("mode", "off")),
        port=str(configuration.get("port", "")),
        baud=configuration.get("baud"),
    )
    navigation = NavigationService(
        registry,
        gps,
        waypoint_path or WAYPOINTS_PATH,
    )
    handler = type(
        "ConfiguredAppHandler",
        (AppHandler,),
        {"registry": registry, "gps": gps, "navigation": navigation},
    )
    return GpsAppServer((host, port), handler, gps, navigation)


def main() -> None:
    parser = argparse.ArgumentParser(description="오프라인 지도 변환 검증 웹앱")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument(
        "--gps-mode",
        choices=("off", "replay", "air530", "stm32"),
        default=os.getenv("OGTECH_GPS_MODE", "off"),
    )
    parser.add_argument("--gps-port", default=os.getenv("OGTECH_GPS_PORT", ""))
    parser.add_argument(
        "--gps-baud",
        type=int,
        default=int(os.getenv("OGTECH_GPS_BAUD", "0")),
    )
    args = parser.parse_args()
    server = build_server(
        args.host,
        args.port,
        gps_configuration={
            "mode": args.gps_mode,
            "port": args.gps_port,
            "baud": args.gps_baud or None,
        },
    )
    # 시연 중 첫 소리가 늦지 않도록 고정 문구를 미리 합성해 둔다.
    warmup_speech(SPEECH)
    print(f"화면 선택: http://{args.host}:{args.port}/select/")
    print(f"지도 검증 도구: http://{args.host}:{args.port}/")
    print(f"OGTECH 제품 화면: http://{args.host}:{args.port}/product/")
    print(f"촬영 전용 화면: http://{args.host}:{args.port}/video/")
    print("종료: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
