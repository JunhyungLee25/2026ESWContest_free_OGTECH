#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OGTECH 제품 음성 경로의 결정 규칙과 검수 카드 렌더러.

LLM은 규칙이 놓친 저위험 발화를 14개 라벨 중 하나로 분류할 때만 사용한다.
사용자에게 말하는 문장은 이 모듈의 고정 카드 또는 장치 코드 계산값 템플릿에서만 나온다.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "config" / "keyword_rules.yaml"
CARDS_PATH = ROOT / "config" / "survival_cards.json"

SCENARIO_IDS = (
    "lost",
    "route",
    "daylight",
    "weather",
    "shelter",
    "warmth",
    "water",
    "food",
    "sleep_safety",
    "injury",
    "wildlife",
    "gear",
    "refuse",
    "unknown",
)
LIFE_PATH_B = frozenset(
    {"lost", "daylight", "warmth", "sleep_safety", "injury", "refuse"}
)


class VoiceContractError(ValueError):
    """음성 규칙 또는 카드 파일이 제품 계약을 만족하지 않을 때 발생한다."""


@dataclass(frozen=True)
class RouteDecision:
    scenario_id: str
    map_action: str | None
    path: str
    source: str
    reason: str
    matched_scenarios: tuple[str, ...] = ()
    assistant_action: str | None = None


@dataclass(frozen=True)
class RenderedCard:
    scenario_id: str
    source_id: str
    path: str
    text: str
    demo: bool


def normalize_utterance(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or "")).lower().strip()
    normalized = re.sub(r"[\t\r\n]+", " ", normalized)
    normalized = re.sub(r"[?!.,;:~…·]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VoiceContractError(f"설정 파일을 읽을 수 없습니다: {path}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise VoiceContractError(f"지원하지 않는 설정 버전입니다: {path}")
    return payload


def _matches(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text) is not None for pattern in patterns)


class RuleRouter:
    """거짓 양성 억제를 우선하는 키워드·정규식 라우터."""

    def __init__(self, rules_path: str | Path = RULES_PATH) -> None:
        self.rules = _load_object(Path(rules_path))
        self.refuse_patterns = list(self.rules.get("refuse_patterns") or [])
        self.assistant_rules = list(self.rules.get("assistant_rules") or [])
        self.map_rules = list(self.rules.get("map_rules") or [])
        self.scenario_rules = list(self.rules.get("scenario_rules") or [])
        confirmation = self.rules.get("confirmation") or {}
        self.yes_patterns = list(confirmation.get("yes_patterns") or [])
        self.no_patterns = list(confirmation.get("no_patterns") or [])
        self._validate()

    def _validate(self) -> None:
        for pattern in self.refuse_patterns + self.yes_patterns + self.no_patterns:
            re.compile(pattern)
        for group in self.assistant_rules + self.map_rules + self.scenario_rules:
            if not isinstance(group, dict):
                raise VoiceContractError("음성 규칙 항목은 객체여야 합니다")
            scenario = group.get("scenario_id")
            if scenario not in SCENARIO_IDS:
                raise VoiceContractError(f"허용되지 않은 scenario_id입니다: {scenario}")
            patterns = group.get("patterns")
            if not isinstance(patterns, list) or not patterns:
                raise VoiceContractError("각 음성 규칙에는 patterns가 필요합니다")
            for pattern in patterns + list(group.get("exclude_patterns") or []):
                re.compile(pattern)

    def decide(self, text: str, *, pending_confirmation: bool = False) -> RouteDecision:
        utterance = normalize_utterance(text)
        if not utterance:
            return RouteDecision("unknown", None, "B", "fixed", "empty")

        # 식용·약물·진단 금지는 다른 모든 일치보다 먼저 고정 거부 카드로 보낸다.
        if _matches(self.refuse_patterns, utterance):
            return RouteDecision("refuse", None, "B", "rule", "refuse_priority", ("refuse",))

        if pending_confirmation:
            if _matches(self.no_patterns, utterance):
                return RouteDecision(
                    "route", "reject_destination", "B", "rule", "confirmation_no", ("route",)
                )
            if _matches(self.yes_patterns, utterance):
                return RouteDecision(
                    "route", "confirm_destination", "B", "rule", "confirmation_yes", ("route",)
                )

        for rule in self.assistant_rules:
            patterns = list(rule["patterns"])
            excludes = list(rule.get("exclude_patterns") or [])
            if _matches(patterns, utterance) and not _matches(excludes, utterance):
                scenario = str(rule["scenario_id"])
                return RouteDecision(
                    scenario,
                    None,
                    "B",
                    "rule",
                    "assistant_action",
                    (scenario,),
                    str(rule["action"]),
                )

        for rule in self.map_rules:
            patterns = list(rule["patterns"])
            excludes = list(rule.get("exclude_patterns") or [])
            if _matches(patterns, utterance) and not _matches(excludes, utterance):
                return RouteDecision(
                    str(rule["scenario_id"]),
                    str(rule["action"]),
                    "B",
                    "rule",
                    "map_enum_action",
                    (str(rule["scenario_id"]),),
                )

        matched: list[str] = []
        for rule in self.scenario_rules:
            patterns = list(rule["patterns"])
            excludes = list(rule.get("exclude_patterns") or [])
            if _matches(patterns, utterance) and not _matches(excludes, utterance):
                scenario = str(rule["scenario_id"])
                if scenario not in matched:
                    matched.append(scenario)

        if len(matched) == 1:
            scenario = matched[0]
            return RouteDecision(
                scenario,
                None,
                "B" if scenario in LIFE_PATH_B else "A",
                "rule",
                "single_scenario",
                tuple(matched),
            )
        if len(matched) > 1 and any(item in LIFE_PATH_B for item in matched):
            # 생명 관련 다중 매칭을 LLM으로 보내지 않는다. 한 가지씩 다시 묻는 고정 카드가 안전하다.
            return RouteDecision(
                "unknown", None, "B", "fixed", "ambiguous_life_scenario", tuple(matched)
            )
        return RouteDecision(
            "unknown",
            None,
            "A",
            "llm_required",
            "ambiguous" if matched else "no_rule_match",
            tuple(matched),
        )

    def resolve(
        self,
        text: str,
        *,
        pending_confirmation: bool = False,
        classifier: Callable[[str], Any] | None = None,
    ) -> RouteDecision:
        decision = self.decide(text, pending_confirmation=pending_confirmation)
        if decision.source != "llm_required" or classifier is None:
            if decision.source == "llm_required":
                return RouteDecision(
                    "unknown", None, "B", "fixed", "classifier_unavailable", decision.matched_scenarios
                )
            return decision
        try:
            classified = classifier(text)
            if isinstance(classified, tuple):
                classified = classified[0]
            scenario = str(classified or "").strip()
        except Exception:  # 분류 실패는 재시도 없이 즉시 고정 폴백한다.
            scenario = ""
        if scenario not in SCENARIO_IDS:
            return RouteDecision("unknown", None, "B", "fixed", "invalid_llm_label")
        if scenario == "unknown":
            return RouteDecision("unknown", None, "B", "fixed", "llm_unknown")
        if scenario in LIFE_PATH_B:
            # 규칙이 놓친 생명 관련 발화를 LLM 판단으로 확정하지 않는다.
            return RouteDecision("unknown", None, "B", "fixed", "llm_life_label_blocked")
        return RouteDecision(scenario, None, "A", "llm", "validated_llm_label", (scenario,))


class CardRenderer:
    """검수 카드와 코드 계산 장치값만으로 최종 발화문을 만든다."""

    def __init__(self, cards_path: str | Path = CARDS_PATH) -> None:
        payload = _load_object(Path(cards_path))
        if payload.get("review_status") != "contract_checked":
            raise VoiceContractError("안전 계약 검수를 통과하지 않은 카드는 사용할 수 없습니다")
        cards = payload.get("cards")
        if not isinstance(cards, dict) or set(cards) != set(SCENARIO_IDS):
            raise VoiceContractError("14개 시나리오 카드가 모두 필요합니다")
        self.cards: dict[str, dict[str, Any]] = cards

    @staticmethod
    def _number(value: Any, digits: int = 0) -> str | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return f"{number:.{digits}f}"

    def _dynamic(self, scenario_id: str, device: dict[str, Any]) -> list[str] | None:
        gps = device.get("gps") or {}
        if scenario_id == "lost":
            if gps.get("fix") is True:
                accuracy = self._number(gps.get("acc_m"), 1)
                satellites = gps.get("satellites")
                accuracy_text = (
                    f"플러스마이너스 {accuracy}미터" if accuracy is not None else "확인 불가"
                )
                satellite_text = (
                    f"{satellites}개" if satellites is not None else "확인 불가"
                )
                return [
                    "GPS가 현재 위치를 수신 중입니다.",
                    f"정확도는 {accuracy_text}이며 위성은 {satellite_text}입니다.",
                    "좌표는 화면에서 정확도와 함께 확인하세요.",
                ]
            age = self._number(gps.get("last_age_s"), 0)
            if isinstance(gps.get("last_fix"), dict):
                age_text = (
                    f"마지막 확정 위치는 {age}초 전 값입니다."
                    if age is not None
                    else "마지막 확정 위치의 경과 시간은 확인할 수 없습니다."
                )
                return [
                    "현재 GPS가 미수신입니다.",
                    age_text,
                    "위치를 추정하지 않으며 좌표는 화면에서 확인하세요.",
                ]

        if scenario_id == "route":
            navigation = device.get("navigation") or {}
            arrival = navigation.get("arrival") or {}
            if arrival.get("arrived"):
                target = arrival.get("target") or {}
                if target.get("id") == "basecamp" or target.get("kind") == "basecamp":
                    return ["베이스캠프에 도착하였습니다."]
                return ["목적지에 도착하였습니다."]
            lines: list[str] = []
            trail = device.get("trail") or {}
            offset = self._number(trail.get("offset_m"), 0)
            if trail.get("status") == "off_trail" and offset is not None:
                lines.append(f"지도 엔진 기준 트레일에서 {offset}미터 벗어난 상태입니다.")
            elif trail.get("status") == "on_trail":
                lines.append("현재 위치는 트레일 허용 범위 안입니다.")
            elif trail.get("status") == "off_trail_estimate" and offset is not None:
                lines.append(
                    f"트레일에서 {offset}미터 벗어난 것으로 추정되지만 현재 상태는 확정할 수 없습니다."
                )
            elif trail.get("status") in {"accuracy_unknown", "uncertain"}:
                distance_text = (
                    f"트레일까지 계산 거리는 {offset}미터이지만 "
                    if offset is not None
                    else ""
                )
                lines.append(
                    distance_text + "GPS 정확도가 없어 트레일 상태를 확정할 수 없습니다."
                )
            elif trail.get("status") == "last_fix_only":
                lines.append(
                    "현재 GPS가 미수신이므로 트레일 상태를 확정하지 않고 마지막 위치만 표시합니다."
                )
            route = navigation.get("active_route") or {}
            if route.get("available") and gps.get("fix") is True:
                bearing = self._number(route.get("bearing_deg"), 0)
                distance = self._number(route.get("distance_m"), 0)
                accuracy = self._number(gps.get("acc_m"), 1)
                if bearing is not None and distance is not None:
                    accuracy_line = (
                        f"GPS 정확도는 플러스마이너스 {accuracy}미터입니다."
                        if accuracy is not None
                        else "GPS 정확도는 확인할 수 없습니다."
                    )
                    lines.extend([
                        f"지도 엔진 계산 방위는 {bearing}도입니다.",
                        f"경로 거리는 {distance}미터입니다.",
                        accuracy_line,
                    ])
            if lines:
                return lines[:4]

        if scenario_id == "daylight":
            sun = device.get("sun") or {}
            remaining = self._number(sun.get("remaining_min"), 0)
            return_by = sun.get("return_by_clock")
            if sun.get("computed") and remaining is not None:
                reference = "마지막 확정 위치 기준입니다." if sun.get("reference") == "last_fix" else "현재 GPS 위치 기준입니다."
                remaining_number = float(remaining)
                remaining_line = (
                    f"일몰 후 {abs(remaining_number):.0f}분 지났습니다."
                    if remaining_number < 0
                    else f"일몰까지 {remaining_number:.0f}분 남았습니다."
                )
                lines = [remaining_line, reference]
                if return_by:
                    lines.append(f"등록된 베이스캠프 기준 귀환 권고 시각은 {return_by}입니다.")
                level = str(sun.get("level") or "unknown")
                if level == "danger":
                    lines.append("귀환 권고 시각에 도달했습니다. 베이스캠프 경로를 확인하세요.")
                elif level == "caution":
                    lines.append("귀환 권고 시각이 가까워졌습니다. 베이스캠프 경로를 확인하세요.")
                elif level == "normal":
                    lines.append("현재는 귀환 주의 임계 전입니다.")
                return lines

        if scenario_id == "weather":
            environment = device.get("environment") or {}
            if environment.get("valid"):
                temp = self._number(environment.get("temp_c"), 1)
                humidity = self._number(environment.get("humidity_pct"), 0)
                pressure = self._number(environment.get("press_hpa"), 1)
                if temp is not None and humidity is not None:
                    lines = [
                        f"현장 센서 온도는 {temp}도, 습도는 {humidity}퍼센트입니다.",
                    ]
                    if pressure is not None:
                        trend = {
                            "rising": "상승",
                            "steady": "유지",
                            "falling": "하강",
                        }.get(str(environment.get("press_trend")), "확인 불가")
                        lines.append(
                            f"현장 기압은 {pressure}헥토파스칼이며 추세는 {trend}입니다."
                        )
                    lines.append("이 값은 예보가 아닌 국지 추정 입력입니다.")
                    return lines

        if scenario_id == "gear":
            power = device.get("power") or {}
            if power.get("valid"):
                percent = self._number(power.get("percent"), 0)
                days = self._number(power.get("days_left"), 1)
                if percent is not None:
                    lines = [f"실제 전원 계측값은 {percent}퍼센트입니다."]
                    if days is not None:
                        lines.append(f"현재 운용 패턴 기준 예상 잔여는 {days}일입니다.")
                    return lines

        if scenario_id == "sleep_safety":
            co = device.get("co") or {}
            if co.get("valid") and not co.get("stale"):
                ppm = self._number(co.get("ppm"), 0)
                if ppm is not None:
                    return [
                        f"현재 일산화탄소 센서 계측값은 {ppm}피피엠입니다.",
                        "텐트나 차량 안에서는 연소 기구를 켜지 마세요.",
                        "센서 감시는 예방을 대신하지 않습니다.",
                    ]
        return None

    def render(self, scenario_id: str, device: dict[str, Any] | None = None) -> RenderedCard:
        if scenario_id not in SCENARIO_IDS:
            scenario_id = "unknown"
        card = self.cards[scenario_id]
        lines = self._dynamic(scenario_id, device or {}) or list(card["sentences"])
        demo = bool((device or {}).get("demo"))
        if demo and lines:
            lines[0] = "데모 값 기준으로, " + str(lines[0]).strip()
        text = " ".join(str(line).strip() for line in lines if str(line).strip())
        return RenderedCard(
            scenario_id=scenario_id,
            source_id=str(card["source_id"]),
            path=str(card["path"]),
            text=text,
            demo=demo,
        )
