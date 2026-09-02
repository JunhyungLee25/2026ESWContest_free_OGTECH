#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""첨부 영상의 10단계 동작을 실제 MAP·음성 코드로 연속 검증한다.

이 하네스는 인터넷 호출, 자유 생성 문장, 임의 좌표 생성을 쓰지 않는다.
시연용 오프라인 그래프와 검수 POI를 강제로 선택해 실행 간 입력을 고정한다.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from time import perf_counter
from typing import Any
from unittest.mock import patch


CO_LLM_ROOT = Path(__file__).resolve().parents[1]
OGTECH_ROOT = CO_LLM_ROOT.parent


def _resolve_map_root() -> Path:
    """지도 엔진 위치를 찾는다.

    지도 엔진의 정본은 OGTECH-frontend/MAP 하나뿐이다. 이 저장소에는 사본을 두지 않는다.
    이 하네스는 두 저장소를 함께 clone한 상태에서 도는 통합 검증용이다.
    """
    override = os.getenv("OGTECH_MAP_ROOT", "").strip()
    candidates = [Path(override)] if override else []
    candidates += [
        OGTECH_ROOT.parent / "OGTECH-frontend" / "MAP",
        OGTECH_ROOT / "MAP",
    ]
    for candidate in candidates:
        if (candidate / "map_engine.py").is_file():
            return candidate.resolve()
    tried = "\n  ".join(str(c) for c in candidates)
    raise SystemExit(
        "지도 엔진을 찾지 못했습니다. OGTECH-frontend 저장소를 이 저장소와 같은 상위 폴더에 "
        "clone하거나 OGTECH_MAP_ROOT로 경로를 지정하세요.\n"
        f"찾아본 경로:\n  {tried}"
    )


MAP_ROOT = _resolve_map_root()
for import_path in (CO_LLM_ROOT, CO_LLM_ROOT / "scripts", MAP_ROOT):
    value = str(import_path)
    if value not in sys.path:
        sys.path.insert(0, value)

from app import GPS_REPLAY, PRODUCT_ROOT, SAMPLE_POINTS, MapRegistry  # noqa: E402
from device_monitor import AlertDetector  # noqa: E402
from gps_service import GpsService  # noqa: E402
from navigation_service import NavigationService  # noqa: E402
from product_assistant import ProductAssistant, VerifiedResponseStore  # noqa: E402
from tts_pipeline import TtsPipeline, inspect_wav  # noqa: E402


KST = timezone(timedelta(hours=9))
DESTINATION_ARRIVAL = "목적지에 도착하였습니다."
BASECAMP_ARRIVAL = "베이스캠프에 도착하였습니다."


class ScenarioFailure(AssertionError):
    """영상 재현의 필수 상태 전이가 어긋났을 때 발생한다."""


class LocalMapAdapter:
    """HTTP 없이 제품 음성 라우터를 실제 NavigationService에 연결한다."""

    def __init__(self, service: NavigationService) -> None:
        self.service = service

    def voice(self) -> dict[str, Any]:
        return self.service.voice_snapshot()

    def device(self) -> dict[str, Any]:
        return self.service.snapshot()

    def command(self, action: str) -> dict[str, Any]:
        return self.service.apply_voice_command({"action": action})


class AlwaysFailEngine:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> "AlwaysFailEngine":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def synth(self, _text: str, _out_wav: Path) -> None:
        raise RuntimeError(f"{self.name} 의도한 통합 실패")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioFailure(message)


def publish_fix(gps: GpsService, *, lat: float, lon: float, accuracy_m: float = 5.0) -> None:
    gps._handle_line(
        json.dumps(
            {
                "ok": True,
                "event": "fix",
                "lat": lat,
                "lon": lon,
                "acc_m": accuracy_m,
                "sats": 10,
                "age_s": 0,
            },
            separators=(",", ":"),
        ),
        mode="stm32",
    )


def blocked_network(*_args: object, **_kwargs: object) -> None:
    raise ScenarioFailure("통합 시나리오가 외부 네트워크 연결을 시도했습니다")


def run_once(registry: MapRegistry, root: Path, run_number: int) -> dict[str, Any]:
    started = perf_counter()
    step_started = started
    step_ms: dict[str, float] = {}

    def mark(step: str) -> None:
        nonlocal step_started
        now = perf_counter()
        step_ms[step] = round((now - step_started) * 1000.0, 2)
        step_started = now

    run_root = root / f"run-{run_number:02d}"
    run_root.mkdir(parents=True, exist_ok=True)
    gps = GpsService(GPS_REPLAY)
    try:
        start = SAMPLE_POINTS["current"]
        publish_fix(gps, lat=float(start["lat"]), lon=float(start["lon"]))
        service = NavigationService(
            registry,
            gps,
            run_root / "waypoints.json",
            local_tz=KST,
        )
        assistant = ProductAssistant(
            LocalMapAdapter(service),
            response_store=VerifiedResponseStore(run_root / "last_response.json"),
        )
        detector = AlertDetector()

        # 1. 베이스캠프는 추정값이 아니라 현재 확정 fix에서 저장한다.
        saved = assistant.handle_text("여기를 베이스캠프로 저장해 줘")
        require(saved.decision.map_action == "save_basecamp", "1단계 베이스캠프 enum 불일치")
        require(saved.map_event is not None and saved.map_event["status"] == "accepted", "1단계 저장 거부")
        basecamp = service.waypoints.snapshot().get("basecamp")
        require(isinstance(basecamp, dict), "1단계 베이스캠프가 저장되지 않음")
        mark("01_save_basecamp")

        # 2~3. 영상의 실제 발화는 음용 판정이 아니라 검수 POI 후보만 연다.
        water = assistant.handle_text("아 너무 목마른데")
        require(water.decision.map_action == "find_nearest_water", "2단계 수원 탐색 enum 불일치")
        require(water.map_event is not None and water.map_event["status"] == "confirmation_required", "2단계 확인 상태 없음")
        require("수질은 확인되지 않았습니다" in water.speech, "3단계 수질 미확인 고지 누락")
        require("마셔도 됩니다" not in water.speech, "3단계 음용 가능을 잘못 확정함")
        require(service.waypoints.snapshot().get("destination") is None, "3단계 확인 전에 목적지가 저장됨")
        require("lat" not in (water.map_event.get("pending_destination") or {}), "3단계 음성 후보에 좌표가 노출됨")
        mark("02_03_water_candidate")

        # 4~5. 명시적 긍정 뒤에만 목적지를 저장하고 MAP ENGINE이 경로를 계산한다.
        confirmed = assistant.handle_text("네")
        require(confirmed.decision.map_action == "confirm_destination", "4단계 확인 enum 불일치")
        require(confirmed.speech == "네, 목적지로 설정되었습니다.", "4단계 고정 확인 문구 불일치")
        route_state = service.snapshot(now=datetime(2026, 8, 9, 12, 0, tzinfo=KST))
        route = route_state["navigation"]["active_route"]
        require(route.get("available") is True, "5단계 경로 계산 실패")
        require(route.get("computed_by") == "map_engine", "5단계 경로 계산 주체 불일치")
        require(route_state["demo"] is True, "5단계 합성 지도 DEMO 배지 계약 누락")
        require(route_state["contract"]["llm_may_generate_coordinates"] is False, "5단계 LLM 좌표 금지 계약 누락")
        confirmed_destination = service.waypoints.snapshot().get("destination") or {}
        require(str(confirmed_destination.get("source", "")).endswith("offline_catalog"), "5단계 목적지 출처가 오프라인 카탈로그가 아님")
        require(service.voice_snapshot().get("pending_destination") is None, "5단계 확인 뒤 후보 상태가 남음")
        initial_distance = float(route["distance_m"])
        initial_eta = int(route["eta_min"])
        coordinates = route.get("coordinates") or []
        require(len(coordinates) >= 3, "5단계 경로 좌표가 이동 검증에 부족함")
        mark("04_05_confirm_route")

        # 6. 경로 중간 fix를 넣어 방위·남은 거리·ETA가 코드로 다시 계산되는지 본다.
        middle = coordinates[len(coordinates) // 2]
        publish_fix(gps, lat=float(middle[1]), lon=float(middle[0]))
        moving_state = service.snapshot(now=datetime(2026, 8, 9, 12, 5, tzinfo=KST))
        moving_route = moving_state["navigation"]["active_route"]
        require(moving_route.get("available") is True, "6단계 이동 후 경로 소실")
        require(float(moving_route["distance_m"]) < initial_distance, "6단계 남은 거리 미갱신")
        require(int(moving_route["eta_min"]) <= initial_eta, "6단계 ETA가 줄지 않음")
        require(0 <= float(moving_route["bearing_deg"]) < 360, "6단계 방위 범위 오류")
        mark("06_moving_update")

        # 7. 목적지 좌표와 충분한 정확도가 함께 들어와야 도착을 확정한다.
        destination = service.waypoints.snapshot().get("destination")
        require(isinstance(destination, dict), "7단계 목적지 상태 소실")
        publish_fix(
            gps,
            lat=float(destination["lat"]),
            lon=float(destination["lon"]),
            accuracy_m=4.0,
        )
        arrived_state = service.snapshot(now=datetime(2026, 8, 9, 12, 20, tzinfo=KST))
        require(arrived_state["navigation"]["arrival"]["arrived"] is True, "7단계 목적지 도착 미검출")
        arrivals = [message for message in detector.detect(arrived_state) if message.kind == "arrival"]
        require(len(arrivals) == 1 and arrivals[0].text.endswith(DESTINATION_ARRIVAL), "7단계 도착 고정 이벤트 누락")
        require(not [message for message in detector.detect(arrived_state) if message.kind == "arrival"], "7단계 도착 음성이 같은 상태에서 중복됨")
        tts = TtsPipeline(use_cache=False, cache_dir=run_root / "tts-cache")
        destination_audio = tts.synthesize(DESTINATION_ARRIVAL, run_root / "destination.wav")
        require(destination_audio.engine == "fixed", "7단계 목적지 도착이 고정 WAV를 쓰지 않음")
        mark("07_destination_arrival")

        # 8. 귀환 권고 시각이 지난 상태는 위험으로 표시하고 베이스캠프 경로를 연다.
        daylight_base = service.snapshot(now=datetime(2026, 8, 9, 12, 20, tzinfo=KST))
        return_by_raw = daylight_base["sun"].get("return_by")
        require(isinstance(return_by_raw, str), "8단계 귀환 권고 시각 계산 실패")
        return_by = datetime.fromisoformat(return_by_raw)
        caution_state = service.snapshot(now=return_by - timedelta(minutes=15))
        require(caution_state["sun"]["level"] == "caution", "8단계 일조 주의 전이 실패")
        danger_time = return_by + timedelta(minutes=1)
        danger_state = service.snapshot(now=danger_time)
        require(danger_state["sun"]["level"] == "danger", "8단계 일조 위험 전이 실패")
        require(danger_state["alert"] and danger_state["alert"]["kind"] == "daylight", "8단계 일조 경보 누락")
        daylight_messages = [message for message in detector.detect(danger_state) if message.kind == "daylight"]
        require(len(daylight_messages) == 1, "8단계 선제 일조 음성 이벤트 누락")
        require("돌아가세요" not in daylight_messages[0].text, "8단계 사용자의 귀환 결정을 대신함")
        require("베이스캠프 경로" in daylight_messages[0].text, "8단계 확인 대상 경로 안내 누락")
        return_route = assistant.handle_text("베이스캠프 복귀 경로 보여 줘")
        require(return_route.decision.map_action == "route_basecamp", "8단계 베이스캠프 enum 불일치")
        return_device = return_route.map_event["device"] if return_route.map_event else {}
        return_target = (return_device.get("navigation") or {}).get("active_route") or {}
        require((return_target.get("target") or {}).get("id") == "basecamp", "8단계 베이스캠프 경로 미선택")
        require(return_target.get("computed_by") == "map_engine", "8단계 베이스캠프 경로 계산 주체 불일치")
        detector.detect(return_device)
        mark("08_daylight_return_route")

        # 9. 저장된 베이스캠프 도착은 별도 고정 음성을 사용한다.
        publish_fix(
            gps,
            lat=float(basecamp["lat"]),
            lon=float(basecamp["lon"]),
            accuracy_m=4.0,
        )
        basecamp_state = service.snapshot(now=datetime(2026, 8, 9, 18, 0, tzinfo=KST))
        require(basecamp_state["navigation"]["arrival"]["arrived"] is True, "9단계 베이스캠프 도착 미검출")
        basecamp_messages = [message for message in detector.detect(basecamp_state) if message.kind == "arrival"]
        require(len(basecamp_messages) == 1 and basecamp_messages[0].text.endswith(BASECAMP_ARRIVAL), "9단계 베이스캠프 도착 이벤트 누락")
        require(not [message for message in detector.detect(basecamp_state) if message.kind == "arrival"], "9단계 베이스캠프 도착 음성이 중복됨")
        basecamp_audio = tts.synthesize(BASECAMP_ARRIVAL, run_root / "basecamp.wav")
        require(basecamp_audio.engine == "fixed", "9단계 베이스캠프 도착이 고정 WAV를 쓰지 않음")
        mark("09_basecamp_arrival")

        # 10. 음성 enum이 서버의 적색 단색 상태를 직접 바꾼다.
        night = assistant.handle_text("야간 모드 켜 줘")
        require(night.decision.map_action == "night_on", "10단계 야간 모드 enum 불일치")
        require(service.snapshot()["interface"]["night"] is True, "10단계 야간 모드 상태 미반영")
        styles = (PRODUCT_ROOT / "styles.css").read_text(encoding="utf-8")
        require(':root[data-night="on"]' in styles, "10단계 적색 단색 CSS 선택자 누락")
        mark("10_voice_night_mode")

        # 명세의 보조 명령: 마지막 SAFE 응답 재생과 목적지·경로 해제.
        repeated = assistant.handle_text("다시 말해 줘")
        require(repeated.speech == night.speech and repeated.source_id == night.source_id, "반복 재생이 마지막 SAFE 응답과 다름")
        cleared = assistant.handle_text("현재 목적지 삭제해 줘")
        require(cleared.decision.map_action == "clear_destination", "목적지 해제 enum 불일치")
        require(service.waypoints.snapshot().get("destination") is None, "목적지 해제 후 저장점 잔존")

        # LLM 강제 종료: 재시도나 자유 문장 없이 unknown 고정 카드로 전환한다.
        def failed_classifier(_text: str) -> str:
            raise RuntimeError("의도한 LLM 중단")

        llm_down = ProductAssistant(LocalMapAdapter(service), classifier=failed_classifier)
        llm_result = llm_down.handle_text("이 상황에서 무엇을 먼저 살펴보면 좋을까")
        require(llm_result.decision.reason in {"invalid_llm_label", "classifier_unavailable"}, "LLM 중단 폴백 사유 불일치")
        require(llm_result.source_id.startswith("SAFE-"), "LLM 중단 뒤 비검수 문장이 선택됨")
        llm_map = llm_down.handle_text("야간 모드 꺼 줘")
        require(llm_map.decision.map_action == "night_off", "LLM 중단 뒤 MAP enum 명령도 중단됨")

        # GPS no-fix: 현재 위치·베이스캠프를 추측해서 만들지 않는다.
        empty_gps = GpsService(GPS_REPLAY)
        try:
            empty_service = NavigationService(
                registry,
                empty_gps,
                run_root / "no-fix-waypoints.json",
                local_tz=KST,
            )
            empty_assistant = ProductAssistant(LocalMapAdapter(empty_service))
            no_fix = empty_assistant.handle_text("여기를 베이스캠프로 저장해 줘")
            no_fix_state = empty_service.snapshot()
            require(no_fix.map_event is not None and no_fix.map_event["status"] == "rejected", "GPS 미수신 저장을 거부하지 않음")
            require(no_fix_state["gps"]["fix"] is False, "GPS 미수신을 fix로 표시함")
            require(no_fix_state["waypoints"]["basecamp"] is None, "GPS 미수신 좌표로 베이스캠프 생성")
            require(no_fix_state["navigation"]["arrival"]["arrived"] is False, "GPS 미수신인데 도착을 확정함")
            no_fix_water = empty_assistant.handle_text("아 너무 목마른데")
            require(no_fix_water.map_event is not None and no_fix_water.map_event["status"] == "rejected", "GPS 미수신 POI 탐색을 거부하지 않음")
        finally:
            empty_gps.close()

        # 하네스에 구성한 테스트 엔진이 모두 실패해도 고정 안내 WAV로 전환한다.
        failed_tts = TtsPipeline(
            engine_order=("failed-primary", "failed-secondary"),
            engine_factory=lambda name: AlwaysFailEngine(name),
            use_cache=False,
            cache_dir=run_root / "failed-tts-cache",
        ).synthesize("고정 목록에 없는 검수 문장입니다.", run_root / "tts-failure.wav")
        require(failed_tts.engine == "fixed_fallback", "TTS 전 엔진 실패 고정 폴백 누락")
        require(failed_tts.degraded is True and len(failed_tts.errors) == 2, "TTS 실패 원인·성능저하 상태 누락")
        require(failed_tts.metrics.clipped_ratio == 0.0, "TTS 실패 안내 WAV 클리핑 검출")
        mark("supplemental_failures")

        elapsed_ms = round((perf_counter() - started) * 1000.0, 2)
        return {
            "run": run_number,
            "elapsed_ms": elapsed_ms,
            "initial_distance_m": initial_distance,
            "moving_distance_m": float(moving_route["distance_m"]),
            "initial_eta_min": initial_eta,
            "moving_eta_min": int(moving_route["eta_min"]),
            "destination_tts": destination_audio.engine,
            "basecamp_tts": basecamp_audio.engine,
            "failure_tts": failed_tts.engine,
            "daylight_level": danger_state["sun"]["level"],
            "night": True,
            "offline": True,
            "step_ms": step_ms,
        }
    finally:
        gps.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="영상 10단계 오프라인 통합 시나리오")
    parser.add_argument("--runs", type=int, default=20, help="연속 실행 횟수, 기본 20")
    parser.add_argument("--json", action="store_true", help="JSON 결과 출력")
    parser.add_argument("--output", type=Path, help="JSON 결과를 저장할 경로")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.runs <= 100:
        raise SystemExit("--runs는 1~100이어야 합니다")

    with ExitStack() as guards:
        guards.enter_context(patch("urllib.request.urlopen", side_effect=blocked_network))
        guards.enter_context(patch("socket.create_connection", side_effect=blocked_network))
        registry = MapRegistry(force_sample=True)
        diagnostics = registry.diagnostics()
        require(diagnostics["map"]["ok"] is True, "자가진단: 시연 지도 그래프 실패")
        require(diagnostics["poi"]["ok"] is True, "자가진단: 시연 POI 카탈로그 실패")
        for audio_name in (
            "destination_confirmed.wav",
            "destination_arrived.wav",
            "return_to_base.wav",
            "tts_unavailable.wav",
        ):
            inspect_wav(PRODUCT_ROOT / audio_name)

        with tempfile.TemporaryDirectory(prefix="ogtech-video-scenario-") as directory:
            root = Path(directory)
            results = [run_once(registry, root, index) for index in range(1, args.runs + 1)]

    summary = {
        "version": 1,
        "runs": args.runs,
        "passed": len(results),
        "failed": 0,
        "offline_guard": True,
        "max_elapsed_ms": max(item["elapsed_ms"] for item in results),
        "max_step_ms": {
            step: max(item["step_ms"][step] for item in results)
            for step in results[0]["step_ms"]
        },
        "fixed_arrival_tts_runs": sum(
            1
            for item in results
            if item["destination_tts"] == "fixed" and item["basecamp_tts"] == "fixed"
        ),
        "configured_engine_failure_fallback_runs": sum(
            1 for item in results if item["failure_tts"] == "fixed_fallback"
        ),
        "results": results,
    }
    serialized = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(args.output)
    if args.json:
        print(serialized, end="")
    else:
        print(
            f"영상 10단계: {summary['passed']}/{summary['runs']} 연속 통과 · "
            f"최대 {summary['max_elapsed_ms']:.2f} ms"
        )
        print(
            f"도착 고정 TTS {summary['fixed_arrival_tts_runs']}/{summary['runs']} · "
            "구성된 테스트 엔진 전부 실패 고정 폴백 "
            f"{summary['configured_engine_failure_fallback_runs']}/{summary['runs']} · "
            "외부 네트워크 호출 0"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
