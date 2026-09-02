# -*- coding: utf-8 -*-
"""DEVICE_STATE 직렬화 — 60토큰 상한 (Co-LLM/docs/00_frozen_decisions.md §5)."""

from __future__ import annotations

import math
import re
from typing import Any

_HANGUL = re.compile(r"[가-힣]")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]")


def estimate_tokens(text: str) -> int:
    """보수적 토큰 추정. 한글 1.3자/tok, 영숫자 3.5자/tok, 그 외 1.5자/tok `[추정]`."""
    value = str(text or "")
    hangul = len(_HANGUL.findall(value))
    ascii_word = len(_ASCII_WORD.findall(value))
    other = max(len(value) - hangul - ascii_word - value.count(" "), 0)
    return int(math.ceil(hangul / 1.3 + ascii_word / 3.5 + other / 1.5))


def _num(value: Any, digits: int = 0) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return f"{number:.{digits}f}"


def _parts(device: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    gps = device.get("gps") or {}
    if gps.get("fix") is True:
        acc = _num(gps.get("acc_m"), 0)
        sats = gps.get("sats") if gps.get("sats") is not None else gps.get("satellites")
        piece = "gps=fix"
        if acc is not None:
            piece += f" acc{acc}m"
        if sats is not None:
            piece += f" sats{sats}"
        parts.append(piece)
    else:
        age = _num(gps.get("last_age_s"), 0)
        parts.append("gps=nofix" + (f" last{age}s" if age is not None else ""))

    sun = device.get("sun") or {}
    if sun.get("computed") or sun.get("remaining_min") is not None:
        remaining = _num(sun.get("remaining_min"), 0)
        piece = "sun="
        if sun.get("sunset_clock"):
            piece += f"set{sun['sunset_clock']} "
        if remaining is not None:
            piece += f"rem{remaining}min "
        if sun.get("return_by_clock"):
            piece += f"return{sun['return_by_clock']} "
        if sun.get("level"):
            piece += f"lvl={sun['level']}"
        parts.append(piece.strip())

    env = device.get("environment") or {}
    if env.get("valid"):
        temp = _num(env.get("temp_c"), 1)
        hum = _num(env.get("humidity_pct"), 0)
        piece = "env="
        if temp is not None:
            piece += f"{temp}C "
        if hum is not None:
            piece += f"{hum}% "
        if env.get("pressure_valid") and _num(env.get("press_hpa"), 1) is not None:
            piece += f"{_num(env.get('press_hpa'), 1)}hPa {env.get('press_trend') or 'unknown'}"
        parts.append(piece.strip())

    co = device.get("co") or {}
    if co.get("valid") and not co.get("stale"):
        ppm = _num(co.get("ppm"), 0)
        piece = "co=" + (f"{ppm}ppm " if ppm is not None else "")
        piece += "alarm" if co.get("alarm") else str(co.get("level") or "normal")
        parts.append(piece.strip())

    power = device.get("power") or {}
    if power.get("valid"):
        pct = _num(power.get("percent"), 0)
        days = _num(power.get("days_left"), 1)
        piece = "pwr="
        if pct is not None:
            piece += f"{pct}% "
        if days is not None:
            piece += f"{days}d"
        parts.append(piece.strip())

    trail = device.get("trail") or {}
    if trail.get("status"):
        offset = _num(trail.get("offset_m"), 0)
        parts.append(f"trail={trail['status']}" + (f" off{offset}m" if offset is not None else ""))

    navigation = device.get("navigation") or {}
    route = navigation.get("active_route") or {}
    if route.get("available"):
        target = (route.get("target") or {}).get("id") or (route.get("target") or {}).get("kind") or "target"
        dist = _num(route.get("distance_m"), 0)
        bearing = _num(route.get("bearing_deg"), 0)
        eta = _num(route.get("eta_min"), 0)
        piece = f"route={target}"
        if dist is not None:
            piece += f" {dist}m"
        if bearing is not None:
            piece += f" {bearing}deg"
        if eta is not None:
            piece += f" eta{eta}min"
        parts.append(piece)
    arrival = navigation.get("arrival") or {}
    if arrival.get("arrived"):
        parts.append("arrived=yes")
    return parts


def serialize_device_state(device: dict[str, Any] | None, max_tokens: int = 60) -> str:
    """앞에서부터 채우고 상한을 넘기면 뒤 항목부터 버린다. 전체 트랙·지도·대화 이력은 넣지 않는다."""
    if not isinstance(device, dict) or not device:
        return "device=unavailable"
    parts = _parts(device)
    if not parts:
        return "device=empty"
    while parts:
        text = " | ".join(parts)
        if estimate_tokens(text) <= max_tokens:
            return text
        parts.pop()
    return "device=truncated"
