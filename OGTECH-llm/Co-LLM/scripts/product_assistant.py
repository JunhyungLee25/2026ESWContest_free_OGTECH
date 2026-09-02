#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OGTECH 제품용 텍스트 라우팅과 로컬 지도 API 연결."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

from ogtech_core import CardRenderer, RouteDecision, RuleRouter, SCENARIO_IDS


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_MAP_ACTIONS = frozenset(
    {
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
)
MAP_RESPONSE_STATUSES_BY_ACTION = {
    action: frozenset({"accepted", "rejected"}) for action in SAFE_MAP_ACTIONS
}
MAP_RESPONSE_STATUSES_BY_ACTION["find_nearest_water"] = frozenset(
    {"confirmation_required", "rejected"}
)
SYNTHETIC_MAP_STATUSES = frozenset({"map_offline", "invalid_contract"})
MAP_ACCEPTED_SPEECH = {
    "save_basecamp": "현재 GPS 위치를 베이스캠프로 저장했습니다.",
    "save_checkpoint": "현재 GPS 위치를 체크포인트로 저장했습니다.",
    "route_basecamp": "베이스캠프 경로를 불러왔습니다. 방위와 거리는 지도 엔진 계산값입니다.",
    "route_destination": "목적지 경로를 불러왔습니다. 방위와 거리는 지도 엔진 계산값입니다.",
    "route_last_checkpoint": "최근 체크포인트 경로를 불러왔습니다. 방위와 거리는 지도 엔진 계산값입니다.",
    "route_recent_trace": "최근 3분 전 확정 위치 경로를 불러왔습니다. 지도 화면의 계산 결과를 확인하세요.",
    "clear_destination": "현재 목적지와 목적지 경로를 삭제했습니다.",
    "confirm_destination": "네, 목적지로 설정되었습니다.",
    "reject_destination": "목적지 지정을 취소했습니다.",
    "night_on": "야간 모드를 켰습니다.",
    "night_off": "야간 모드를 껐습니다.",
    "night_toggle": "야간 모드 상태를 전환했습니다.",
    "status": "현재 지도와 장치 상태를 확인했습니다.",
    "cancel": "음성 지도 명령을 취소했습니다.",
}
MAP_REJECTED_SPEECH = {
    "save_basecamp": "현재 GPS 수신이 없어 베이스캠프를 저장할 수 없습니다. 마지막 확정 좌표와 경과 시간만 확인하세요.",
    "save_checkpoint": "현재 GPS 수신이 없어 체크포인트를 저장할 수 없습니다. 마지막 확정 좌표와 경과 시간만 확인하세요.",
    "route_basecamp": "베이스캠프 경로를 열지 못했습니다. GPS 수신과 저장 지점을 확인하세요.",
    "route_destination": "목적지 경로를 열지 못했습니다. GPS 수신과 저장 지점을 확인하세요.",
    "route_last_checkpoint": "최근 체크포인트 경로를 열지 못했습니다. GPS 수신과 저장 지점을 확인하세요.",
    "route_recent_trace": "최근 3분 전 확정 위치 경로를 열지 못했습니다. GPS 로그와 저장 상태를 확인하세요.",
    "clear_destination": "삭제할 목적지가 없습니다.",
    "find_nearest_water": "가까운 수원 표식을 찾지 못했습니다. GPS 수신과 오프라인 POI를 확인하세요.",
    "confirm_destination": "확인할 목적지 후보가 없습니다.",
    "reject_destination": "취소할 목적지 후보가 없습니다.",
    "status": "현재 지도와 장치 상태를 읽지 못했습니다.",
    "cancel": "취소할 음성 지도 명령이 없습니다.",
}


def _safe_map_speech(action: str, status: str) -> str:
    """MAP 응답의 자유 문자열을 읽지 않고 action·status 조합만 고정 문장으로 바꾼다."""
    if status == "map_offline":
        return "오프라인 지도 서버와 연결할 수 없습니다. 지도 화면의 연결 상태를 확인하세요."
    if status == "invalid_contract":
        return "지도 서버 응답을 확인할 수 없습니다. 지도 화면의 상태를 직접 확인하세요."
    if action == "find_nearest_water" and status == "confirmation_required":
        return (
            "가장 가까운 검수 수원 표식을 찾았습니다. "
            "수질은 확인되지 않았습니다. 이 위치를 목적지로 지정할까요?"
        )
    if status == "accepted":
        return MAP_ACCEPTED_SPEECH.get(action, "지도 명령을 처리했습니다.")
    if status == "rejected":
        return MAP_REJECTED_SPEECH.get(
            action, "지도 명령을 처리하지 못했습니다. 지도 화면의 상태를 확인하세요."
        )
    return "지도 서버 응답을 확인할 수 없습니다. 지도 화면의 상태를 직접 확인하세요."


def _map_provenance_pair_allowed(action: str, status: str) -> bool:
    """실제 MAP 응답 또는 로컬 오류 래퍼가 만들 수 있는 action·status 조합만 허용한다."""
    response_statuses = MAP_RESPONSE_STATUSES_BY_ACTION.get(action)
    return response_statuses is not None and (
        status in response_statuses or status in SYNTHETIC_MAP_STATUSES
    )


def _expected_source_id(
    scenario_id: str,
    map_action: str | None,
    map_status: str | None,
) -> str | None:
    if scenario_id not in SCENARIO_IDS:
        return None
    if map_action is None:
        if map_status is not None:
            return None
        return CardRenderer().render(scenario_id, None).source_id
    if map_status is None or not _map_provenance_pair_allowed(map_action, map_status):
        return None
    if map_status == "map_offline":
        return "SAFE-SYSTEM-MAP-OFFLINE"
    if map_status == "invalid_contract":
        return "SAFE-SYSTEM-MAP-CONTRACT"
    if map_action == "status" and map_status == "accepted":
        return CardRenderer().render(scenario_id, None).source_id
    return f"SAFE-MAP-{map_action.upper()}"


class MapApiError(RuntimeError):
    """로컬 지도 서버가 없거나 안전 계약과 다른 응답을 보낼 때 발생한다."""


@dataclass(frozen=True)
class AssistantResult:
    heard: str
    decision: RouteDecision
    speech: str
    source_id: str
    map_event: dict[str, Any] | None
    device: dict[str, Any] | None


@dataclass(frozen=True)
class StoredResponse:
    scenario_id: str
    map_action: str | None
    map_status: str | None
    source_id: str


class VerifiedResponseStore:
    """자유 문장이 아니라 검수 응답의 구조화 provenance만 저장한다."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read(self) -> StoredResponse | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        expected_keys = {
            "version",
            "scenario_id",
            "map_action",
            "map_status",
            "source_id",
        }
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 2
            or set(payload) != expected_keys
        ):
            return None
        scenario_id = str(payload.get("scenario_id") or "").strip()
        raw_action = payload.get("map_action")
        map_action = None if raw_action is None else str(raw_action).strip()
        raw_status = payload.get("map_status")
        map_status = None if raw_status is None else str(raw_status).strip()
        source_id = str(payload.get("source_id") or "").strip()
        try:
            expected_source = _expected_source_id(
                scenario_id, map_action, map_status
            )
        except (OSError, ValueError):
            return None
        if expected_source is None or source_id != expected_source:
            return None
        return StoredResponse(
            scenario_id=scenario_id,
            map_action=map_action,
            map_status=map_status,
            source_id=source_id,
        )

    def write(self, response: AssistantResult) -> None:
        scenario_id = response.decision.scenario_id
        map_action = response.decision.map_action
        if map_action is None:
            map_status = None
        elif response.source_id == "SAFE-SYSTEM-MAP-OFFLINE":
            map_status = "map_offline"
        elif response.source_id == "SAFE-SYSTEM-MAP-CONTRACT":
            map_status = "invalid_contract"
        elif isinstance(response.map_event, dict):
            map_status = str(response.map_event.get("status") or "").strip()
        else:
            raise ValueError("MAP 응답 provenance를 확인할 수 없습니다")
        source_id = response.source_id.strip()
        expected_source = _expected_source_id(scenario_id, map_action, map_status)
        if expected_source is None or source_id != expected_source:
            raise ValueError("검수된 응답 provenance만 반복 저장소에 기록할 수 있습니다")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": 2,
                    "scenario_id": scenario_id,
                    "map_action": map_action,
                    "map_status": map_status,
                    "source_id": source_id,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


class MapApiClient:
    def __init__(self, base_url: str, *, timeout_s: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
            raise MapApiError("지도 API는 로컬 HTTP 주소만 사용할 수 있습니다")
        self.timeout_s = timeout_s

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error")
            except Exception:
                detail = None
            raise MapApiError(str(detail or f"지도 API 오류 {exc.code}")) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MapApiError(f"지도 서버 연결 실패: {exc}") from exc
        if not isinstance(body, dict):
            raise MapApiError("지도 API 응답이 객체가 아닙니다")
        return body

    def device(self) -> dict[str, Any]:
        return self._request("GET", "/api/device")

    def voice(self) -> dict[str, Any]:
        return self._request("GET", "/api/voice")

    def command(self, action: str) -> dict[str, Any]:
        # 좌표·거리·방위 필드를 추가할 수 없는 고정 형태다.
        return self._request("POST", "/api/voice/commands", {"action": action})


class ProductAssistant:
    """STT 문자열을 안전 경로로 보내 최종 TTS 문장을 확정한다."""

    def __init__(
        self,
        map_client: MapApiClient,
        *,
        router: RuleRouter | None = None,
        cards: CardRenderer | None = None,
        classifier: Callable[[str], Any] | None = None,
        response_store: VerifiedResponseStore | None = None,
    ) -> None:
        self.map = map_client
        self.router = router or RuleRouter()
        self.cards = cards or CardRenderer()
        self.classifier = classifier
        self.response_store = response_store

    def _finish(
        self, result: AssistantResult, *, remember: bool = True
    ) -> AssistantResult:
        if remember and self.response_store is not None:
            try:
                self.response_store.write(result)
            except (OSError, ValueError):
                # 반복 저장소 장애가 현재의 검수 응답을 막아서는 안 된다.
                pass
        return result

    def handle_text(self, text: str) -> AssistantResult:
        try:
            voice_state = self.map.voice()
            pending = isinstance(voice_state.get("pending_destination"), dict)
        except MapApiError:
            pending = False

        decision = self.router.resolve(
            text,
            pending_confirmation=pending,
            classifier=self.classifier,
        )

        if decision.assistant_action == "repeat_response":
            stored = None if self.response_store is None else self.response_store.read()
            if stored is None:
                return self._finish(
                    AssistantResult(
                        heard=text,
                        decision=decision,
                        speech="이전에 재생한 검수 응답이 없습니다.",
                        source_id="SAFE-SYSTEM-NO-REPEAT",
                        map_event=None,
                        device=None,
                    ),
                    remember=False,
                )
            if stored.map_action is not None and not (
                stored.map_action == "status" and stored.map_status == "accepted"
            ):
                return self._finish(
                    AssistantResult(
                        heard=text,
                        decision=decision,
                        speech=_safe_map_speech(
                            stored.map_action, str(stored.map_status or "")
                        ),
                        source_id=stored.source_id,
                        map_event=None,
                        device=None,
                    ),
                    remember=False,
                )
            try:
                repeat_device = self.map.device()
            except MapApiError:
                repeat_device = None
            repeated = self.cards.render(stored.scenario_id, repeat_device)
            return self._finish(
                AssistantResult(
                    heard=text,
                    decision=decision,
                    speech=repeated.text,
                    source_id=repeated.source_id,
                    map_event=None,
                    device=repeat_device,
                ),
                remember=False,
            )

        map_event: dict[str, Any] | None = None
        device: dict[str, Any] | None = None
        if decision.map_action:
            try:
                map_event = self.map.command(decision.map_action)
            except MapApiError:
                return self._finish(
                    AssistantResult(
                        heard=text,
                        decision=decision,
                        speech=(
                            "오프라인 지도 서버와 연결할 수 없습니다. "
                            "지도 화면의 연결 상태를 확인하세요."
                        ),
                        source_id="SAFE-SYSTEM-MAP-OFFLINE",
                        map_event=None,
                        device=None,
                    )
                )
            status = str(map_event.get("status") or "")
            event_action = str(map_event.get("action") or "")
            status_allowed = status in MAP_RESPONSE_STATUSES_BY_ACTION.get(
                decision.map_action, frozenset()
            )
            if event_action != decision.map_action or not status_allowed:
                return self._finish(
                    AssistantResult(
                        heard=text,
                        decision=decision,
                        speech=_safe_map_speech(
                            decision.map_action, "invalid_contract"
                        ),
                        source_id="SAFE-SYSTEM-MAP-CONTRACT",
                        map_event=None,
                        device=None,
                    )
                )
            raw_device = map_event.get("device")
            device = raw_device if isinstance(raw_device, dict) else None
            if status != "accepted" or decision.map_action != "status":
                return self._finish(
                    AssistantResult(
                        heard=text,
                        decision=decision,
                        speech=_safe_map_speech(decision.map_action, status),
                        source_id=f"SAFE-MAP-{decision.map_action.upper()}",
                        map_event=map_event,
                        device=device,
                    )
                )

        if device is None:
            try:
                device = self.map.device()
            except MapApiError:
                device = None
        rendered = self.cards.render(decision.scenario_id, device)
        return self._finish(
            AssistantResult(
                heard=text,
                decision=decision,
                speech=rendered.text,
                source_id=rendered.source_id,
                map_event=map_event,
                device=device,
            )
        )
