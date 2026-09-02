# -*- coding: utf-8 -*-
"""LLM 출력 검증. enum·생명 라벨·pending·정책 허용 목록·금지어·숫자를 코드에서 강제한다."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .paths import ensure_co_llm_on_path

ensure_co_llm_on_path()
from ogtech_core import LIFE_PATH_B, SCENARIO_IDS  # noqa: E402

NONE_ACTION = "none"
REPEAT_ACTION = "repeat_response"
STATUS_ACTION = "status"
MAP_ACTIONS_LLM = (
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
    "status",
    "cancel",
)
ALL_ACTIONS = (NONE_ACTION,) + MAP_ACTIONS_LLM + (REPEAT_ACTION,)
# 정본 keyword_rules.yaml의 map_rules가 쓰는 scenario_id와 같게 맞춘다.
ACTION_SCENARIO = {
    "save_basecamp": "route",
    "save_checkpoint": "route",
    "route_basecamp": "route",
    "route_destination": "route",
    "route_last_checkpoint": "route",
    "route_recent_trace": "route",
    "clear_destination": "route",
    "find_nearest_water": "water",
    "confirm_destination": "route",
    "reject_destination": "route",
    "night_on": "gear",
    "night_off": "gear",
    "cancel": "route",
}
STATUS_SCENARIOS = frozenset({"lost", "route", "daylight", "weather", "gear", "sleep_safety"})
PENDING_ONLY_ACTIONS = frozenset({"confirm_destination", "reject_destination"})


@dataclass(frozen=True)
class IntentVerdict:
    scenario_id: str
    map_action: str | None
    assistant_action: str | None
    path: str
    source: str
    reason: str
    accepted: bool


def _fixed(reason: str) -> IntentVerdict:
    return IntentVerdict("unknown", None, None, "B", "fixed", reason, False)


def validate_intent(
    parsed: Any,
    *,
    pending_confirmation: bool,
    allow_actions: frozenset[str] | set[str],
    allow_life_status_readout: bool = False,
) -> IntentVerdict:
    if not isinstance(parsed, dict) or set(parsed) != {"scenario_id", "action"}:
        return _fixed("schema_validation_failed")
    scenario = parsed.get("scenario_id")
    action = parsed.get("action")
    if not isinstance(scenario, str) or scenario not in SCENARIO_IDS:
        return _fixed("schema_validation_failed")
    if not isinstance(action, str) or action not in ALL_ACTIONS:
        return _fixed("schema_validation_failed")

    if action == REPEAT_ACTION:
        return IntentVerdict("gear", None, REPEAT_ACTION, "B", "llm", "validated_llm_assistant_action", True)

    if action in PENDING_ONLY_ACTIONS and not pending_confirmation:
        action = NONE_ACTION
    if action != NONE_ACTION and action not in allow_actions:
        action = NONE_ACTION

    if action == STATUS_ACTION:
        if scenario not in STATUS_SCENARIOS:
            action = NONE_ACTION
        elif scenario in LIFE_PATH_B and not allow_life_status_readout:
            return _fixed("llm_life_label_blocked")
        else:
            return IntentVerdict(scenario, STATUS_ACTION, None, "A", "llm", "validated_llm_map_action", True)

    if action != NONE_ACTION:
        canonical = ACTION_SCENARIO[action]
        return IntentVerdict(canonical, action, None, "A", "llm", "validated_llm_map_action", True)

    if scenario == "unknown":
        return _fixed("llm_unknown")
    if scenario in LIFE_PATH_B:
        return _fixed("llm_life_label_blocked")
    return IntentVerdict(scenario, None, None, "A", "llm", "validated_llm_label", True)


# ---------------------------------------------------------------- polish guard

_DIGITS = re.compile(r"\d+(?:[.:]\d+)?")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class PolishVerdict:
    lines: tuple[str, ...] | None
    reason: str

    @property
    def ok(self) -> bool:
        return self.lines is not None


def compile_forbidden(patterns: list[str] | tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


def validate_polish(
    parsed: Any,
    *,
    source_text: str,
    forbidden: tuple[re.Pattern[str], ...],
    max_line_chars: int = 40,
    min_lines: int = 2,
    max_lines: int = 4,
) -> PolishVerdict:
    if not isinstance(parsed, dict) or set(parsed) != {"lines"}:
        return PolishVerdict(None, "schema_validation_failed")
    raw_lines = parsed.get("lines")
    if not isinstance(raw_lines, list) or not (min_lines <= len(raw_lines) <= max_lines):
        return PolishVerdict(None, "line_count")
    lines: list[str] = []
    for item in raw_lines:
        if not isinstance(item, str):
            return PolishVerdict(None, "line_type")
        line = re.sub(r"\s+", " ", item).strip()
        if not line or len(line) > max_line_chars or _CONTROL.search(line):
            return PolishVerdict(None, "line_length")
        for number in _DIGITS.findall(line):
            if number not in source_text:
                return PolishVerdict(None, f"new_number:{number}")
        for pattern in forbidden:
            if pattern.search(line):
                return PolishVerdict(None, f"forbidden:{pattern.pattern}")
        lines.append(line)
    return PolishVerdict(tuple(lines), "ok")
