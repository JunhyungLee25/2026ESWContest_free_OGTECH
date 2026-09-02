"""Air530 NMEA와 STM32 센서 텔레메트리를 받는 오프라인 직렬 서비스."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
from queue import Empty, Full, Queue
import re
import threading
import time
from typing import Any

from co_alarm import CoAlarmJudge


FIX_STALE_AFTER_S = 3.0
SENSOR_STALE_AFTER_S = 3.0
SERIAL_RECONNECT_AFTER_S = 2.0
MAX_SERIAL_LINE_BYTES = 4096
SUPPORTED_MODES = {"off", "replay", "air530", "stm32"}
TELEMETRY_VERSION = 1
STM32_OUTPUT_COMMANDS = {
    "trail_alert": b"ALERT TRAIL ON\n",
    "trail_caution": b"ALERT TRAIL CAUTION\n",
    "trail_clear": b"ALERT TRAIL OFF\n",
}
STM32_POWER_COMMANDS = {
    "ack": b"POWER OFF ACK\n",
    "cancel": b"POWER OFF CANCEL\n",
}
_CRC_SUFFIX = re.compile(r'^(.*),"crc16":"([0-9A-Fa-f]{4})"}$')
# CSV 텔레메트리 접두어: OGTECH-embedded uart4_integration `$OGT1`, 현재 실장 펌웨어 `$SA1`(필드 동일).
OGT1_PREFIXES = ("$OGT1,", "$SA1,")
_OGT1_INT = re.compile(r"-?[0-9]{1,10}")


class GpsInputError(ValueError):
    """GNSS 또는 센서 입력 문장이 계약을 만족하지 않을 때 발생한다."""


def _number(
    value: Any,
    label: str,
    *,
    lower: float | None = None,
    upper: float | None = None,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GpsInputError(f"{label} 값이 숫자가 아닙니다") from exc
    if not isfinite(number):
        raise GpsInputError(f"{label} 값이 유한수가 아닙니다")
    if lower is not None and number < lower:
        raise GpsInputError(f"{label} 값이 허용 범위보다 작습니다")
    if upper is not None and number > upper:
        raise GpsInputError(f"{label} 값이 허용 범위보다 큽니다")
    return number


def _optional_number(
    value: Any,
    label: str,
    *,
    lower: float | None = None,
    upper: float | None = None,
) -> float | None:
    if value is None or value == "":
        return None
    return _number(value, label, lower=lower, upper=upper)


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise GpsInputError(f"{label} 값이 bool이 아닙니다")
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GpsInputError(f"{label} 객체가 없습니다")
    return value


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    """STM32와 공유하는 CRC-16/CCITT-FALSE를 계산한다."""

    crc = initial
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def encode_stm32_telemetry(payload: dict[str, Any]) -> str:
    """테스트·도구에서 펌웨어와 같은 CRC가 붙은 JSON 한 줄을 만든다."""

    if "crc16" in payload:
        raise GpsInputError("crc16은 인코더가 추가합니다")
    base = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    if not base.endswith("}"):
        raise GpsInputError("텔레메트리 루트는 객체여야 합니다")
    checksum = crc16_ccitt(base.encode("ascii"))
    return f'{base[:-1]},"crc16":"{checksum:04X}"}}'


def _nmea_coordinate(raw: str, hemisphere: str, *, latitude: bool) -> float:
    if not raw or hemisphere not in ({"N", "S"} if latitude else {"E", "W"}):
        raise GpsInputError("NMEA 좌표 또는 반구 값이 없습니다")
    value = _number(raw, "NMEA 좌표", lower=0)
    degrees = int(value // 100)
    minutes = value - degrees * 100
    if minutes >= 60:
        raise GpsInputError("NMEA 분 값이 60 이상입니다")
    coordinate = degrees + minutes / 60
    if hemisphere in {"S", "W"}:
        coordinate = -coordinate
    limit = 90 if latitude else 180
    if not -limit <= coordinate <= limit:
        raise GpsInputError("NMEA 좌표가 유효 범위를 벗어났습니다")
    return coordinate


def _nmea_body(line: str) -> str:
    sentence = line.strip()
    if not sentence.startswith("$") or "*" not in sentence:
        raise GpsInputError("NMEA 시작 문자 또는 체크섬이 없습니다")
    body, checksum_text = sentence[1:].rsplit("*", 1)
    if len(checksum_text) < 2:
        raise GpsInputError("NMEA 체크섬 길이가 잘못됐습니다")
    checksum = 0
    for character in body:
        checksum ^= ord(character)
    try:
        expected = int(checksum_text[:2], 16)
    except ValueError as exc:
        raise GpsInputError("NMEA 체크섬이 16진수가 아닙니다") from exc
    if checksum != expected:
        raise GpsInputError("NMEA 체크섬이 일치하지 않습니다")
    return body


class NmeaParser:
    """Air530에서 필요한 GGA·RMC 문장만 엄격하게 읽는다."""

    def parse(self, line: str) -> dict[str, Any] | None:
        body = _nmea_body(line)
        fields = body.split(",")
        sentence_type = fields[0][-3:]
        if sentence_type == "GGA":
            return self._parse_gga(fields)
        if sentence_type == "RMC":
            return self._parse_rmc(fields)
        return None

    @staticmethod
    def _parse_gga(fields: list[str]) -> dict[str, Any]:
        if len(fields) < 10:
            raise GpsInputError("GGA 필드가 부족합니다")
        quality = int(_number(fields[6] or 0, "GGA fix quality", lower=0, upper=8))
        satellites = int(_number(fields[7] or 0, "GGA 위성 수", lower=0, upper=99))
        hdop = _optional_number(fields[8], "GGA HDOP", lower=0)
        result: dict[str, Any] = {
            "fix": quality > 0,
            "utc": fields[1] or None,
            "satellites": satellites,
            "hdop": hdop,
            "acc_m": None,
            "accuracy_kind": "unknown",
            "sentence": "GGA",
        }
        if quality > 0:
            result["lat"] = _nmea_coordinate(fields[2], fields[3], latitude=True)
            result["lon"] = _nmea_coordinate(fields[4], fields[5], latitude=False)
            result["alt_m"] = _optional_number(fields[9], "GGA 고도")
        return result

    @staticmethod
    def _parse_rmc(fields: list[str]) -> dict[str, Any]:
        if len(fields) < 10:
            raise GpsInputError("RMC 필드가 부족합니다")
        fix = fields[2] == "A"
        result: dict[str, Any] = {
            "fix": fix,
            "utc": fields[1] or None,
            "date": fields[9] or None,
            "speed_knots": _optional_number(fields[7], "RMC 속도", lower=0),
            "course_deg": _optional_number(fields[8], "RMC 진행각", lower=0, upper=360),
            "acc_m": None,
            "accuracy_kind": "unknown",
            "sentence": "RMC",
        }
        if fix:
            result["lat"] = _nmea_coordinate(fields[3], fields[4], latitude=True)
            result["lon"] = _nmea_coordinate(fields[5], fields[6], latitude=False)
        return result


def _parse_telemetry_gps(value: Any) -> dict[str, Any]:
    gps = _object(value, "STM32 gps")
    fix = _boolean(gps.get("fix"), "STM32 gps.fix")
    result: dict[str, Any] = {
        "fix": fix,
        "satellites": int(_number(gps.get("sats", 0), "STM32 위성 수", lower=0, upper=99)),
        "hdop": _optional_number(gps.get("hdop"), "STM32 HDOP", lower=0, upper=99.9),
        "acc_m": _optional_number(gps.get("acc_m"), "STM32 정확도", lower=0, upper=10_000),
        "age_s": _optional_number(gps.get("age_s"), "STM32 좌표 경과 시간", lower=0),
        "last_age_s": _optional_number(gps.get("last_age_s"), "STM32 마지막 좌표 경과 시간", lower=0),
        "accuracy_kind": "reported" if gps.get("acc_m") is not None else "unknown",
        "sentence": "STM32_TELEMETRY",
    }
    if fix:
        result["lat"] = _number(gps.get("lat"), "STM32 위도", lower=-90, upper=90)
        result["lon"] = _number(gps.get("lon"), "STM32 경도", lower=-180, upper=180)
    return result


def _parse_telemetry_environment(value: Any) -> dict[str, Any]:
    env = _object(value, "STM32 env")
    valid = _boolean(env.get("valid"), "STM32 env.valid")
    sht_valid = _boolean(env.get("sht_valid", valid), "STM32 env.sht_valid")
    if sht_valid != valid:
        raise GpsInputError("STM32 env.valid와 sht_valid가 일치하지 않습니다")
    inferred_pressure_valid = env.get("press_hpa") is not None
    pressure_valid = _boolean(
        env.get("pressure_valid", inferred_pressure_valid),
        "STM32 env.pressure_valid",
    )
    pressure_trend = str(env.get("press_trend") or "unknown").strip().lower()
    if pressure_trend not in {"rising", "steady", "falling", "unknown"}:
        raise GpsInputError("STM32 press_trend 값이 올바르지 않습니다")
    result: dict[str, Any] = {
        "valid": valid,
        "sht_valid": sht_valid,
        "pressure_valid": pressure_valid,
        "temp_c": None,
        "humidity_pct": None,
        "press_hpa": None,
        "press_trend": pressure_trend,
        "age_s": _optional_number(env.get("age_s"), "STM32 환경 경과 시간", lower=0),
        "press_age_s": _optional_number(
            env.get("press_age_s"), "STM32 기압 경과 시간", lower=0
        ),
        "bmp_address": None,
    }
    if valid:
        result["temp_c"] = _number(env.get("temp_c"), "STM32 온도", lower=-45, upper=130)
        result["humidity_pct"] = _number(env.get("humidity_pct"), "STM32 습도", lower=0, upper=100)
    elif env.get("temp_c") is not None or env.get("humidity_pct") is not None:
        raise GpsInputError("유효하지 않은 STM32 SHT40에 계측값이 있습니다")
    if pressure_valid:
        result["press_hpa"] = _optional_number(
            env.get("press_hpa"), "STM32 기압", lower=300, upper=1100
        )
        if result["press_hpa"] is None:
            raise GpsInputError("유효한 STM32 BMP390에 기압값이 없습니다")
        address = env.get("bmp_address")
        if address is not None:
            address_number = _number(address, "STM32 BMP390 주소", lower=0, upper=255)
            if not address_number.is_integer():
                raise GpsInputError("STM32 BMP390 주소가 정수가 아닙니다")
            parsed_address = int(address_number)
            if parsed_address not in {0x76, 0x77}:
                raise GpsInputError("STM32 BMP390 주소가 0x76/0x77이 아닙니다")
            result["bmp_address"] = parsed_address
    else:
        if env.get("press_hpa") is not None or pressure_trend != "unknown":
            raise GpsInputError("유효하지 않은 STM32 BMP390에 기압값 또는 추세가 있습니다")
        result["press_trend"] = "unknown"
    return result


def _parse_telemetry_rtc(value: Any) -> dict[str, Any]:
    if value is None:
        return {"valid": False, "iso_utc": None, "age_s": None}
    rtc = _object(value, "STM32 rtc")
    valid = _boolean(rtc.get("valid"), "STM32 rtc.valid")
    age_s = _optional_number(rtc.get("age_s"), "STM32 RTC 경과 시간", lower=0)
    iso_utc = rtc.get("iso_utc")
    if not valid:
        if iso_utc is not None:
            raise GpsInputError("유효하지 않은 STM32 RTC에 시각 값이 있습니다")
        return {"valid": False, "iso_utc": None, "age_s": age_s}
    if not isinstance(iso_utc, str) or len(iso_utc) > 40:
        raise GpsInputError("유효한 STM32 RTC에 ISO UTC 시각이 없습니다")
    try:
        parsed = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GpsInputError("STM32 RTC ISO UTC 형식이 올바르지 않습니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise GpsInputError("STM32 RTC 시각은 UTC여야 합니다")
    if not 2025 <= parsed.year <= 2100:
        raise GpsInputError("STM32 RTC 연도가 허용 범위를 벗어났습니다")
    return {
        "valid": True,
        "iso_utc": parsed.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "age_s": age_s,
    }


def _parse_telemetry_co(value: Any) -> dict[str, Any]:
    co = _object(value, "STM32 co")
    valid = _boolean(co.get("valid"), "STM32 co.valid")
    warming_up = _boolean(co.get("warming_up", False), "STM32 co.warming_up")
    alarm = _boolean(co.get("alarm", False), "STM32 co.alarm")
    level = str(co.get("level", "unknown"))
    if level not in {"unknown", "normal", "warning", "alarm"}:
        raise GpsInputError("STM32 CO level 값이 올바르지 않습니다")
    if alarm and level != "alarm":
        raise GpsInputError("STM32 CO alarm과 level이 일치하지 않습니다")
    ppm = _optional_number(co.get("ppm"), "STM32 CO 농도", lower=0, upper=10_000)
    if valid and ppm is None:
        raise GpsInputError("유효한 STM32 CO 값에 ppm이 없습니다")
    return {
        "valid": valid,
        "warming_up": warming_up,
        "ppm": ppm,
        "level": level,
        "alarm": alarm,
        "age_s": _optional_number(co.get("age_s"), "STM32 CO 경과 시간", lower=0),
    }


def _parse_telemetry_power(value: Any) -> dict[str, Any]:
    if value is None:
        return {"valid": False, "percent": None, "days_left": None}
    power = _object(value, "STM32 power")
    valid = _boolean(power.get("valid"), "STM32 power.valid")
    gate_on = power.get("jetson_gate_on")
    shutdown_pending = power.get("shutdown_pending")
    return {
        "valid": valid,
        "percent": _optional_number(power.get("percent"), "STM32 배터리", lower=0, upper=100),
        "days_left": _optional_number(power.get("days_left"), "STM32 배터리 잔여 일수", lower=0),
        "jetson_gate_on": (
            None if gate_on is None else _boolean(gate_on, "STM32 Jetson gate")
        ),
        "shutdown_pending": (
            None
            if shutdown_pending is None
            else _boolean(shutdown_pending, "STM32 종료 대기")
        ),
    }


def parse_stm32_telemetry(line: str) -> dict[str, Any] | None:
    """CRC가 붙은 STM32 `event=telemetry` 한 줄을 검증·정규화한다."""

    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise GpsInputError("STM32 응답이 JSON이 아닙니다") from exc
    if not isinstance(payload, dict):
        raise GpsInputError("STM32 응답 루트가 객체가 아닙니다")
    if payload.get("event") != "telemetry":
        return None

    match = _CRC_SUFFIX.fullmatch(line.strip())
    if not match:
        raise GpsInputError("STM32 텔레메트리에 마지막 crc16 필드가 없습니다")
    base = match.group(1) + "}"
    expected = int(match.group(2), 16)
    actual = crc16_ccitt(base.encode("ascii"))
    if actual != expected:
        raise GpsInputError("STM32 텔레메트리 CRC16이 일치하지 않습니다")

    version = int(_number(payload.get("v"), "STM32 프로토콜 버전", lower=1, upper=255))
    if version != TELEMETRY_VERSION:
        raise GpsInputError(f"지원하지 않는 STM32 프로토콜 버전입니다: {version}")
    sequence = int(_number(payload.get("seq"), "STM32 시퀀스", lower=0, upper=4_294_967_295))
    uptime_ms = int(_number(payload.get("uptime_ms"), "STM32 가동 시간", lower=0))
    return {
        "version": version,
        "protocol": "v1",
        "sequence": sequence,
        "uptime_ms": uptime_ms,
        "gps": _parse_telemetry_gps(payload.get("gps")),
        "rtc": _parse_telemetry_rtc(payload.get("rtc")),
        "environment": _parse_telemetry_environment(payload.get("env")),
        "co": _parse_telemetry_co(payload.get("co")),
        "power": _parse_telemetry_power(payload.get("power")),
    }


def _ogt1_int(
    value: str,
    label: str,
    *,
    lower: int | None = None,
    upper: int | None = None,
) -> int:
    if not _OGT1_INT.fullmatch(value):
        raise GpsInputError(f"{label} 값이 정수가 아닙니다")
    number = int(value)
    if lower is not None and number < lower:
        raise GpsInputError(f"{label} 값이 허용 범위보다 작습니다")
    if upper is not None and number > upper:
        raise GpsInputError(f"{label} 값이 허용 범위보다 큽니다")
    return number


def parse_stm32_ogt1(line: str) -> dict[str, Any] | None:
    """`$OGT1,…*XX` / `$SA1,…*XX` CSV 텔레메트리 한 줄을 v1 JSON과 같은 모양으로 정규화한다.

    `$SA1,seq,uptime_ms,dht_valid,temp_x10,hum_x10,co_state,co_ppm,gps_state,lat_e7,lon_e7,sats*XX`
    XX는 `$`와 `*` 사이 문자의 XOR(16진 2자리). co_state 0=WARMING_UP 1=VALID 2=STALE,
    gps_state 0=NOT_FOUND 1=NO_FIX 2=FIX. CSV에는 RTC·기압·전원·CO 경보·정확도가 없으므로
    해당 항목은 valid=False/None으로 두고 값을 만들어 내지 않는다.
    접두어가 다르면 None(JSON v1 경로로 넘긴다).

    CO 경보(level·alarm)는 이 함수가 채우지 않는다 — 한 줄만 봐서는 지속 시간을 알 수 없다.
    GpsService._apply_telemetry가 CoAlarmJudge로 판정해 덮어쓴다.
    """

    sentence = line.strip()
    if not sentence.startswith(OGT1_PREFIXES):
        return None
    if "*" not in sentence:
        raise GpsInputError("STM32 OGT1 체크섬 구분자가 없습니다")
    body, checksum_text = sentence[1:].rsplit("*", 1)
    if len(checksum_text) != 2:
        raise GpsInputError("STM32 OGT1 체크섬 길이가 잘못됐습니다")
    try:
        expected = int(checksum_text, 16)
    except ValueError as exc:
        raise GpsInputError("STM32 OGT1 체크섬이 16진수가 아닙니다") from exc
    checksum = 0
    for character in body:
        checksum ^= ord(character)
    if checksum != expected:
        raise GpsInputError("STM32 OGT1 체크섬이 일치하지 않습니다")
    fields = body.split(",")
    if len(fields) != 12:
        raise GpsInputError(f"STM32 OGT1 필드 수가 12가 아닙니다: {len(fields)}")

    sequence = _ogt1_int(fields[1], "STM32 시퀀스", lower=0, upper=4_294_967_295)
    uptime_ms = _ogt1_int(fields[2], "STM32 가동 시간", lower=0, upper=4_294_967_295)
    dht_valid = _ogt1_int(fields[3], "STM32 dht_valid", lower=0, upper=1) == 1
    temp_x10 = _ogt1_int(fields[4], "STM32 온도 x10")
    hum_x10 = _ogt1_int(fields[5], "STM32 습도 x10")
    co_state = _ogt1_int(fields[6], "STM32 co_state", lower=0, upper=2)
    co_ppm = _ogt1_int(fields[7], "STM32 CO 농도")
    gps_state = _ogt1_int(fields[8], "STM32 gps_state", lower=0, upper=2)
    lat_e7 = _ogt1_int(fields[9], "STM32 위도 e7")
    lon_e7 = _ogt1_int(fields[10], "STM32 경도 e7")
    satellites = _ogt1_int(fields[11], "STM32 위성 수", lower=0, upper=99)

    fix = gps_state == 2
    gps: dict[str, Any] = {
        "fix": fix,
        "satellites": satellites,
        "hdop": None,
        "acc_m": None,
        "age_s": None,
        "last_age_s": None,
        "accuracy_kind": "unknown",
        "sentence": "STM32_OGT1",
    }
    if fix:  # NOT_FOUND/NO_FIX의 lat/lon 필드는 무시한다(좌표 아님)
        gps["lat"] = _number(lat_e7 / 10_000_000, "STM32 위도", lower=-90, upper=90)
        gps["lon"] = _number(lon_e7 / 10_000_000, "STM32 경도", lower=-180, upper=180)
    environment: dict[str, Any] = {
        "valid": dht_valid,
        "sht_valid": dht_valid,
        "pressure_valid": False,
        "temp_c": None,
        "humidity_pct": None,
        "press_hpa": None,
        "press_trend": "unknown",
        "age_s": None,
        "press_age_s": None,
        "bmp_address": None,
    }
    if dht_valid:
        environment["temp_c"] = _number(temp_x10 / 10, "STM32 온도", lower=-45, upper=130)
        environment["humidity_pct"] = _number(hum_x10 / 10, "STM32 습도", lower=0, upper=100)
    co_valid = co_state == 1
    return {
        "version": TELEMETRY_VERSION,  # _apply_telemetry가 요구 — OGT"1"/SA"1"의 판 번호
        "protocol": "ogt1",
        "sequence": sequence,
        "uptime_ms": uptime_ms,
        "gps": gps,
        "rtc": {"valid": False, "iso_utc": None, "age_s": None},
        "environment": environment,
        "co": {
            "valid": co_valid,
            "warming_up": co_state == 0,
            "ppm": _number(co_ppm, "STM32 CO 농도", lower=0, upper=10_000) if co_valid else None,
            "level": "normal" if co_valid else "unknown",  # CSV에 경보·임계 정보 없음
            "alarm": False,
            "age_s": None,
        },
        "power": {
            "valid": False,
            "percent": None,
            "days_left": None,
            "jetson_gate_on": None,
            "shutdown_pending": None,
        },
    }


def parse_stm32_button(line: str) -> dict[str, Any] | None:
    """CRC가 붙은 STM32 물리 버튼 pressed/released 이벤트를 검증한다."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise GpsInputError("STM32 응답이 JSON이 아닙니다") from exc
    if not isinstance(payload, dict):
        raise GpsInputError("STM32 응답 루트가 객체가 아닙니다")
    if payload.get("event") != "button":
        return None
    match = _CRC_SUFFIX.fullmatch(line.strip())
    if not match:
        raise GpsInputError("STM32 버튼 이벤트에 마지막 crc16 필드가 없습니다")
    base = match.group(1) + "}"
    expected = int(match.group(2), 16)
    if crc16_ccitt(base.encode("ascii")) != expected:
        raise GpsInputError("STM32 버튼 이벤트 CRC16이 일치하지 않습니다")
    version = int(_number(payload.get("v"), "STM32 프로토콜 버전", lower=1, upper=255))
    if version != TELEMETRY_VERSION:
        raise GpsInputError(f"지원하지 않는 STM32 프로토콜 버전입니다: {version}")
    sequence = int(
        _number(payload.get("seq"), "STM32 버튼 시퀀스", lower=0, upper=4_294_967_295)
    )
    button = str(payload.get("button") or "")
    state = str(payload.get("state") or "")
    if button not in {"power", "checkpoint", "voice"}:
        raise GpsInputError("STM32 버튼 종류가 올바르지 않습니다")
    if state not in {"pressed", "released"}:
        raise GpsInputError("STM32 버튼 상태가 올바르지 않습니다")
    held_ms = int(
        _number(payload.get("held_ms"), "STM32 버튼 누름 시간", lower=0, upper=3_600_000)
    )
    if state == "pressed" and held_ms != 0:
        raise GpsInputError("STM32 버튼 pressed 이벤트의 held_ms는 0이어야 합니다")
    return {
        "version": version,
        "sequence": sequence,
        "button": button,
        "state": state,
        "held_ms": held_ms,
    }


def parse_stm32_output(line: str) -> dict[str, Any] | None:
    """CRC가 붙은 STM32 트레일 진동 출력 ACK를 검증한다."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise GpsInputError("STM32 응답이 JSON이 아닙니다") from exc
    if not isinstance(payload, dict):
        raise GpsInputError("STM32 응답 루트가 객체가 아닙니다")
    if payload.get("event") != "output":
        return None
    match = _CRC_SUFFIX.fullmatch(line.strip())
    if not match:
        raise GpsInputError("STM32 출력 ACK에 마지막 crc16 필드가 없습니다")
    base = match.group(1) + "}"
    expected = int(match.group(2), 16)
    if crc16_ccitt(base.encode("ascii")) != expected:
        raise GpsInputError("STM32 출력 ACK CRC16이 일치하지 않습니다")
    version = int(_number(payload.get("v"), "STM32 프로토콜 버전", lower=1, upper=255))
    if version != TELEMETRY_VERSION:
        raise GpsInputError(f"지원하지 않는 STM32 프로토콜 버전입니다: {version}")
    sequence = int(
        _number(payload.get("seq"), "STM32 출력 시퀀스", lower=0, upper=4_294_967_295)
    )
    output = str(payload.get("output") or "")
    level = str(payload.get("level") or "")
    active = _boolean(payload.get("active"), "STM32 출력 active")
    if output != "trail" or level not in {"off", "caution", "alert"}:
        raise GpsInputError("STM32 출력 ACK 종류가 올바르지 않습니다")
    if active != (level != "off"):
        raise GpsInputError("STM32 출력 ACK level과 active가 일치하지 않습니다")
    watchdog_ms = int(
        _number(
            payload.get("watchdog_ms"),
            "STM32 출력 watchdog",
            lower=100,
            upper=60_000,
        )
    )
    return {
        "version": version,
        "sequence": sequence,
        "output": output,
        "level": level,
        "active": active,
        "watchdog_ms": watchdog_ms,
        "action": {
            "alert": "trail_alert",
            "caution": "trail_caution",
            "off": "trail_clear",
        }[level],
    }


def parse_stm32_power_event(line: str) -> dict[str, Any] | None:
    """CRC가 붙은 STM32 Jetson 전원 gate 상태 이벤트를 검증한다."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise GpsInputError("STM32 응답이 JSON이 아닙니다") from exc
    if not isinstance(payload, dict):
        raise GpsInputError("STM32 응답 루트가 객체가 아닙니다")
    if payload.get("event") != "power":
        return None
    match = _CRC_SUFFIX.fullmatch(line.strip())
    if not match:
        raise GpsInputError("STM32 전원 이벤트에 마지막 crc16 필드가 없습니다")
    base = match.group(1) + "}"
    if crc16_ccitt(base.encode("ascii")) != int(match.group(2), 16):
        raise GpsInputError("STM32 전원 이벤트 CRC16이 일치하지 않습니다")
    version = int(_number(payload.get("v"), "STM32 프로토콜 버전", lower=1, upper=255))
    if version != TELEMETRY_VERSION:
        raise GpsInputError(f"지원하지 않는 STM32 프로토콜 버전입니다: {version}")
    sequence = int(
        _number(payload.get("seq"), "STM32 전원 시퀀스", lower=0, upper=4_294_967_295)
    )
    state = str(payload.get("state") or "")
    if state not in {
        "gate_on",
        "gate_off",
        "shutdown_requested",
        "shutdown_ack",
        "shutdown_cancelled",
        "shutdown_timeout",
        "status",
    }:
        raise GpsInputError("STM32 전원 이벤트 상태가 올바르지 않습니다")
    gate_on = _boolean(payload.get("gate_on"), "STM32 전원 gate_on")
    pending = _boolean(payload.get("shutdown_pending"), "STM32 전원 shutdown_pending")
    required = {
        "gate_on": (True, False),
        "gate_off": (False, False),
        "shutdown_requested": (True, True),
        "shutdown_ack": (True, True),
        "shutdown_cancelled": (True, False),
        "shutdown_timeout": (True, False),
    }.get(state)
    if required is not None and (gate_on, pending) != required:
        raise GpsInputError("STM32 전원 이벤트 상태와 gate 값이 일치하지 않습니다")
    return {
        "version": version,
        "sequence": sequence,
        "state": state,
        "gate_on": gate_on,
        "shutdown_pending": pending,
    }


def parse_stm32_fix(line: str) -> dict[str, Any] | None:
    """기존 `event=fix` 응답과 새 텔레메트리의 GPS 부분을 검증한다."""

    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise GpsInputError("STM32 응답이 JSON이 아닙니다") from exc
    if not isinstance(payload, dict):
        raise GpsInputError("STM32 응답 루트가 객체가 아닙니다")
    if payload.get("event") == "telemetry":
        telemetry = parse_stm32_telemetry(line)
        return None if telemetry is None else telemetry["gps"]
    if payload.get("event") != "fix":
        return None
    if payload.get("ok") is not True:
        raise GpsInputError(str(payload.get("reason", "STM32 fix 요청 실패")))

    inferred_fix = payload.get("lat") is not None and payload.get("lon") is not None
    fix = payload.get("fix", inferred_fix) is True
    if not fix:
        return {
            "fix": False,
            "last_age_s": _optional_number(payload.get("last_age_s"), "마지막 좌표 경과 시간", lower=0),
            "sentence": "STM32_JSON",
        }

    acc_m = _optional_number(payload.get("acc_m"), "STM32 정확도", lower=0)
    return {
        "fix": True,
        "lat": _number(payload.get("lat"), "STM32 위도", lower=-90, upper=90),
        "lon": _number(payload.get("lon"), "STM32 경도", lower=-180, upper=180),
        "acc_m": acc_m,
        "accuracy_kind": "reported" if acc_m is not None else "unknown",
        "satellites": int(_number(payload.get("sats", 0), "STM32 위성 수", lower=0, upper=99)),
        "age_s": _optional_number(payload.get("age_s"), "STM32 좌표 경과 시간", lower=0),
        "sentence": "STM32_JSON",
    }


@dataclass(frozen=True)
class GpsConfiguration:
    mode: str = "off"
    port: str = ""
    baud: int = 0
    replay_path: str = ""


class GpsService:
    """직렬 수신 스레드와 화면용 최신 센서 상태를 관리한다."""

    def __init__(self, default_replay_path: str | Path) -> None:
        self.default_replay_path = Path(default_replay_path)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._subscribers: set[Queue[dict[str, Any]]] = set()
        self._button_subscribers: set[Queue[dict[str, Any]]] = set()
        self._stm32_pending_output: tuple[int, str] | None = None
        self._stm32_pending_power_command: tuple[int, str] | None = None
        self._stm32_output_request_sequence = 0
        self._configuration = GpsConfiguration()
        self._nmea_parser = NmeaParser()
        self._reset_state("off")

    def _reset_state(self, mode: str) -> None:
        self._last_fix: dict[str, Any] | None = None
        self._last_fix_monotonic: float | None = None
        self._current_fix = False
        self._last_environment: dict[str, Any] | None = None
        self._last_environment_monotonic: float | None = None
        self._last_rtc: dict[str, Any] | None = None
        self._last_rtc_monotonic: float | None = None
        self._last_co: dict[str, Any] | None = None
        self._last_co_monotonic: float | None = None
        self._last_power: dict[str, Any] | None = None
        self._last_power_monotonic: float | None = None
        self._last_power_event_monotonic: float | None = None
        # `$SA1`/`$OGT1` CSV에는 경보 필드가 없다. 부저를 걷어낸 뒤로 경보음은 Jetson이
        # 내므로 판정도 여기서 한다(co_alarm.CoAlarmJudge, 임계는 펌웨어와 동일).
        self._co_judge = CoAlarmJudge()
        self._state: dict[str, Any] = {
            "mode": mode,
            "source": mode if mode != "off" else "none",
            "connected": False,
            "error": None,
            "received_lines": 0,
            "rejected_lines": 0,
            "reported_last_age_s": None,
            "telemetry_version": None,
            "telemetry_protocol": None,  # "v1"(JSONL+CRC16) · "ogt1"($OGT1/$SA1 CSV+XOR)
            "telemetry_sequence": None,
            "telemetry_uptime_ms": None,
            "sequence_gaps": 0,
            "output_last_requested": None,
            "output_last_sent": None,
            "output_sent_count": 0,
            "output_error": None,
            "output_last_ack": None,
            "button_event_count": 0,
            "last_button": None,
            "last_power_event": None,
            "power_ack_requested": False,
            "power_ack_sent_count": 0,
            "power_cancel_requested": False,
            "power_cancel_sent_count": 0,
            "power_transaction_phase": "idle",
        }

    def _clear_output_queue(self) -> None:
        self._stm32_pending_output = None
        self._stm32_pending_power_command = None

    def request_stm32_output(self, action: str) -> bool:
        """검수된 열거형 출력만 STM32 직렬 송신 큐에 넣는다.

        반환값은 하드웨어 작동 확인이 아니라 STM32 모드 송신 큐 접수 여부다.
        """
        if action not in STM32_OUTPUT_COMMANDS:
            raise GpsInputError("지원하지 않는 STM32 출력 명령입니다")
        with self._lock:
            if self._configuration.mode != "stm32":
                return False
            self._stm32_output_request_sequence += 1
            self._stm32_pending_output = (
                self._stm32_output_request_sequence,
                action,
            )
            self._state["output_last_requested"] = action
            self._state["output_error"] = None
        return True

    def request_stm32_power_shutdown_ack(self) -> bool:
        """CRC로 확인한 실제 STM32 종료 대기 상태에만 ACK를 큐잉한다."""
        with self._lock:
            if (
                not self._stm32_shutdown_pending_locked()
                or self._state["power_transaction_phase"]
                not in {"idle", "pending"}
            ):
                return False
            self._stm32_output_request_sequence += 1
            self._stm32_pending_power_command = (
                self._stm32_output_request_sequence,
                "ack",
            )
            self._state["power_ack_requested"] = True
            self._state["power_transaction_phase"] = "ack_queued"
            return True

    def request_stm32_power_shutdown_cancel(self) -> bool:
        """현재 소프트웨어 전원 transaction의 pending/arm 상태를 fail-safe 취소한다."""
        with self._lock:
            if (
                self._configuration.mode != "stm32"
                or self._state["power_transaction_phase"]
                not in {"pending", "ack_queued", "armed"}
            ):
                return False
            self._stm32_output_request_sequence += 1
            self._stm32_pending_power_command = (
                self._stm32_output_request_sequence,
                "cancel",
            )
            self._state["power_cancel_requested"] = True
            self._state["power_transaction_phase"] = "cancel_queued"
            return True

    def _stm32_shutdown_pending_locked(self) -> bool:
        """가장 최근 CRC 이벤트/텔레메트리의 gate-on pending만 인정한다."""
        if (
            self._configuration.mode != "stm32"
            or self._state.get("connected") is not True
        ):
            return False
        now = time.monotonic()
        observations: list[tuple[float, bool, bool]] = []
        event = self._state.get("last_power_event")
        if isinstance(event, dict) and self._last_power_event_monotonic is not None:
            observations.append(
                (
                    self._last_power_event_monotonic,
                    event.get("gate_on") is True,
                    event.get("shutdown_pending") is True,
                )
            )
        if self._last_power is not None and self._last_power_monotonic is not None:
            observations.append(
                (
                    self._last_power_monotonic,
                    self._last_power.get("jetson_gate_on") is True,
                    self._last_power.get("shutdown_pending") is True,
                )
            )
        if not observations:
            return False
        observed_at, gate_on, pending = max(observations, key=lambda item: item[0])
        return now - observed_at <= SENSOR_STALE_AFTER_S and gate_on and pending

    def configure(
        self,
        *,
        mode: str,
        port: str = "",
        baud: int | None = None,
        replay_path: str | Path | None = None,
    ) -> dict[str, Any]:
        if mode not in SUPPORTED_MODES:
            raise GpsInputError(f"지원하지 않는 GPS 모드입니다: {mode}")
        if mode in {"air530", "stm32"} and not port:
            raise GpsInputError("직렬 포트 경로가 필요합니다")
        selected_baud = 0 if mode == "off" else int(baud or (115200 if mode == "stm32" else 9600))
        if mode != "off" and selected_baud <= 0:
            raise GpsInputError("baud는 0보다 커야 합니다")
        selected_replay = Path(replay_path or self.default_replay_path)

        self.stop()
        with self._lock:
            self._clear_output_queue()
            self._configuration = GpsConfiguration(
                mode=mode,
                port=port,
                baud=selected_baud,
                replay_path=str(selected_replay),
            )
            self._reset_state(mode)
            self._stop_event = threading.Event()

        if mode == "off":
            self._publish()
            return self.snapshot()
        target = self._run_replay if mode == "replay" else self._run_serial
        self._thread = threading.Thread(target=target, name=f"sensor-{mode}", daemon=True)
        self._thread.start()
        return self.snapshot()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.5)
        self._thread = None

    def close(self) -> None:
        self.stop()

    @staticmethod
    def _aged_sensor(
        value: dict[str, Any] | None,
        received_at: float | None,
        now: float,
        empty: dict[str, Any],
    ) -> dict[str, Any]:
        if value is None or received_at is None:
            return dict(empty)
        result = dict(value)
        age_s = max(0.0, now - received_at) + float(value.get("device_age_s") or 0.0)
        result.pop("device_age_s", None)
        result["age_s"] = round(age_s, 1)
        result["stale"] = age_s > SENSOR_STALE_AFTER_S
        if result["stale"]:
            result["valid"] = False
            if "sht_valid" in result:
                result["sht_valid"] = False
            if "pressure_valid" in result:
                result["pressure_valid"] = False
                result["press_trend"] = "unknown"
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            result = dict(self._state)
            result["configuration"] = {
                "mode": self._configuration.mode,
                "port": self._configuration.port,
                "baud": self._configuration.baud,
            }
            if self._last_fix and self._last_fix_monotonic is not None:
                local_age = max(0.0, now - self._last_fix_monotonic)
                device_age = self._last_fix.get("device_age_s") or 0.0
                age_s = round(local_age + device_age, 1)
                last_fix = {key: value for key, value in self._last_fix.items() if key != "device_age_s"}
                last_fix["age_s"] = age_s
                result["last_fix"] = last_fix
                live = self._current_fix and local_age <= FIX_STALE_AFTER_S
                result["fix"] = live
                if live:
                    result.update(last_fix)
                else:
                    result["last_age_s"] = age_s
            else:
                result["fix"] = False
                result["last_fix"] = None
                if result.get("reported_last_age_s") is not None:
                    result["last_age_s"] = result["reported_last_age_s"]

            result["environment"] = self._aged_sensor(
                self._last_environment,
                self._last_environment_monotonic,
                now,
                {
                    "valid": False,
                    "sht_valid": False,
                    "pressure_valid": False,
                    "temp_c": None,
                    "humidity_pct": None,
                    "press_hpa": None,
                    "press_trend": "unknown",
                    "age_s": None,
                    "press_age_s": None,
                    "bmp_address": None,
                    "stale": True,
                },
            )
            result["rtc"] = self._aged_sensor(
                self._last_rtc,
                self._last_rtc_monotonic,
                now,
                {
                    "valid": False,
                    "iso_utc": None,
                    "age_s": None,
                    "stale": True,
                },
            )
            result["co"] = self._aged_sensor(
                self._last_co,
                self._last_co_monotonic,
                now,
                {
                    "valid": False,
                    "warming_up": False,
                    "ppm": None,
                    "level": "unknown",
                    "alarm": False,
                    "age_s": None,
                    "stale": True,
                },
            )
            result["power"] = self._aged_sensor(
                self._last_power,
                self._last_power_monotonic,
                now,
                {
                    "valid": False,
                    "percent": None,
                    "days_left": None,
                    "jetson_gate_on": None,
                    "shutdown_pending": None,
                    "age_s": None,
                    "stale": True,
                },
            )
            result["demo"] = self._configuration.mode == "replay"
            result["hardware_output"] = {
                "transport": "stm32_serial"
                if self._configuration.mode == "stm32"
                else "unavailable",
                "last_requested": result.pop("output_last_requested", None),
                "last_sent": result.pop("output_last_sent", None),
                "sent_count": result.pop("output_sent_count", 0),
                "last_ack": result.pop("output_last_ack", None),
                "confirmed": False,
                "error": result.pop("output_error", None),
            }
            ack = result["hardware_output"]["last_ack"] or {}
            result["hardware_output"]["confirmed"] = bool(
                ack.get("action")
                and ack.get("action") == result["hardware_output"]["last_requested"]
            )
            result["hardware_buttons"] = {
                "transport": "stm32_serial"
                if self._configuration.mode == "stm32"
                else "unavailable",
                "event_count": result.pop("button_event_count", 0),
                "last_event": result.pop("last_button", None),
                "coordinates_accepted": False,
            }
            result["hardware_power"] = {
                "transport": "stm32_serial"
                if self._configuration.mode == "stm32"
                else "unavailable",
                "last_event": result.pop("last_power_event", None),
                "ack_requested": result.pop("power_ack_requested", False),
                "ack_sent_count": result.pop("power_ack_sent_count", 0),
                "cancel_requested": result.pop("power_cancel_requested", False),
                "cancel_sent_count": result.pop("power_cancel_sent_count", 0),
                "transaction_phase": result.pop("power_transaction_phase", "idle"),
                "gate_wiring_verified": False,
            }
            result.pop("reported_last_age_s", None)
            return result

    def subscribe(self) -> Queue[dict[str, Any]]:
        subscriber: Queue[dict[str, Any]] = Queue(maxsize=1)
        with self._lock:
            self._subscribers.add(subscriber)
        subscriber.put_nowait(self.snapshot())
        return subscriber

    def unsubscribe(self, subscriber: Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def subscribe_buttons(self) -> Queue[dict[str, Any]]:
        """초기 stale 값을 재생하지 않고 새 물리 버튼 edge만 전달한다."""
        subscriber: Queue[dict[str, Any]] = Queue(maxsize=16)
        with self._lock:
            self._button_subscribers.add(subscriber)
        return subscriber

    def unsubscribe_buttons(self, subscriber: Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._button_subscribers.discard(subscriber)

    def ports(self) -> list[dict[str, str]]:
        try:
            from serial.tools import list_ports  # type: ignore
        except ImportError:
            return []
        return [
            {
                "device": str(port.device),
                "description": str(port.description or ""),
                "hwid": str(port.hwid or ""),
            }
            for port in list_ports.comports()
        ]

    def _run_replay(self) -> None:
        path = Path(self._configuration.replay_path)
        try:
            lines = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            if not lines:
                raise GpsInputError("NMEA 재생 파일이 비어 있습니다")
            self._set_connection(True, None)
            index = 0
            while not self._stop_event.is_set():
                self._handle_line(lines[index % len(lines)], mode="replay")
                index += 1
                self._stop_event.wait(1.0)
        except Exception as exc:
            self._set_connection(False, str(exc))

    def _run_serial(self) -> None:
        try:
            import serial  # type: ignore
        except ImportError:
            self._set_connection(False, "pyserial이 설치되지 않았습니다")
            return
        configuration = self._configuration
        while not self._stop_event.is_set():
            try:
                with serial.Serial(configuration.port, configuration.baud, timeout=0.25) as connection:
                    self._set_connection(True, None)
                    if configuration.mode == "stm32":
                        connection.write(b"STREAM ON\n")
                        connection.flush()
                    while not self._stop_event.is_set():
                        if configuration.mode == "stm32":
                            self._drain_stm32_outputs(connection)
                        raw = connection.readline(MAX_SERIAL_LINE_BYTES)
                        if raw:
                            self._handle_line(
                                raw.decode("ascii", errors="replace").strip(),
                                mode=configuration.mode,
                            )
                        else:
                            self._publish()
            except Exception as exc:
                self._set_connection(False, str(exc))
                self._stop_event.wait(SERIAL_RECONNECT_AFTER_S)

    def _drain_stm32_outputs(self, connection: Any) -> None:
        """최신 idempotent 출력 상태 하나를 보내며 실패 시 pending을 보존한다."""
        with self._lock:
            power_pending = self._stm32_pending_power_command
            trail_pending = self._stm32_pending_output
        if power_pending is not None:
            _, power_action = power_pending
            connection.write(STM32_POWER_COMMANDS[power_action])
            connection.flush()
            with self._lock:
                if self._stm32_pending_power_command == power_pending:
                    self._stm32_pending_power_command = None
                self._state[f"power_{power_action}_sent_count"] += 1
            return
        pending = trail_pending
        if pending is None:
            return
        _, action = pending
        connection.write(STM32_OUTPUT_COMMANDS[action])
        connection.flush()
        with self._lock:
            if self._stm32_pending_output == pending:
                self._stm32_pending_output = None
            self._state["output_last_sent"] = action
            self._state["output_sent_count"] += 1
            self._state["output_error"] = None

    def _apply_fix(self, parsed: dict[str, Any]) -> None:
        self._current_fix = parsed.get("fix") is True
        if self._current_fix:
            previous = self._last_fix or {}
            self._last_fix = {
                "lat": parsed["lat"],
                "lon": parsed["lon"],
                "acc_m": parsed.get("acc_m", previous.get("acc_m")),
                "accuracy_kind": parsed.get("accuracy_kind", previous.get("accuracy_kind", "unknown")),
                "satellites": parsed.get("satellites", previous.get("satellites")),
                "hdop": parsed.get("hdop", previous.get("hdop")),
                "alt_m": parsed.get("alt_m", previous.get("alt_m")),
                "utc": parsed.get("utc", previous.get("utc")),
                "device_age_s": parsed.get("age_s") or 0.0,
            }
            self._last_fix_monotonic = time.monotonic()
            self._state["reported_last_age_s"] = None
        elif parsed.get("last_age_s") is not None:
            reported_age = parsed["last_age_s"]
            self._state["reported_last_age_s"] = reported_age
            if self._last_fix is not None:
                self._last_fix["device_age_s"] = reported_age
                self._last_fix_monotonic = time.monotonic()

    def _apply_telemetry(self, telemetry: dict[str, Any]) -> None:
        previous_sequence = self._state.get("telemetry_sequence")
        previous_uptime = self._state.get("telemetry_uptime_ms")
        sequence = telemetry["sequence"]
        uptime = telemetry["uptime_ms"]
        if (
            previous_sequence is not None
            and previous_uptime is not None
            and uptime >= previous_uptime
            and sequence != ((int(previous_sequence) + 1) & 0xFFFFFFFF)
        ):
            self._state["sequence_gaps"] += 1
        self._state["telemetry_version"] = telemetry["version"]
        self._state["telemetry_protocol"] = telemetry.get("protocol", "v1")
        self._state["telemetry_sequence"] = sequence
        self._state["telemetry_uptime_ms"] = uptime
        self._apply_fix(telemetry["gps"])
        now = time.monotonic()
        self._last_rtc = dict(telemetry["rtc"])
        self._last_rtc["device_age_s"] = self._last_rtc.pop("age_s") or 0.0
        self._last_rtc_monotonic = now
        self._last_environment = dict(telemetry["environment"])
        self._last_environment["device_age_s"] = self._last_environment.pop("age_s") or 0.0
        self._last_environment_monotonic = now
        self._last_co = dict(telemetry["co"])
        self._last_co["device_age_s"] = self._last_co.pop("age_s") or 0.0
        self._last_co_monotonic = now
        if self._state["telemetry_protocol"] == "ogt1":
            # CSV는 ppm만 준다. 경보 상태는 Jetson이 만들고, 그 값으로 스피커가 울린다.
            level = self._co_judge.update(
                now,
                valid=self._last_co.get("valid") is True,
                ppm=self._last_co.get("ppm"),
            )
            if level != "none":  # "none"이면 파서가 넣은 normal/unknown을 그대로 둔다
                self._last_co["level"] = level
            self._last_co["alarm"] = level == "alarm"
        self._last_power = dict(telemetry["power"])
        self._last_power["device_age_s"] = 0.0
        self._last_power_monotonic = now
        if (
            self._last_power.get("shutdown_pending") is False
            and self._state.get("power_transaction_phase") != "idle"
        ):
            self._stm32_pending_power_command = None
            self._state["power_ack_requested"] = False
            self._state["power_cancel_requested"] = False
            self._state["power_transaction_phase"] = "idle"

    def _handle_line(self, line: str, *, mode: str) -> None:
        if not line:
            return
        with self._lock:
            self._state["received_lines"] += 1
        try:
            telemetry = None
            if mode == "stm32":
                # 현재 실장 펌웨어의 `$SA1`/`$OGT1` CSV를 JSON v1보다 먼저 시도한다(접두어 불일치면 None).
                telemetry = parse_stm32_ogt1(line)
                if telemetry is None:
                    telemetry = parse_stm32_telemetry(line)
            button = (
                parse_stm32_button(line)
                if mode == "stm32" and telemetry is None
                else None
            )
            output = (
                parse_stm32_output(line)
                if mode == "stm32" and telemetry is None and button is None
                else None
            )
            power_event = (
                parse_stm32_power_event(line)
                if mode == "stm32"
                and telemetry is None
                and button is None
                and output is None
                else None
            )
            parsed = None
            if (
                telemetry is None
                and button is None
                and output is None
                and power_event is None
            ):
                parsed = parse_stm32_fix(line) if mode == "stm32" else self._nmea_parser.parse(line)
        except (GpsInputError, UnicodeEncodeError) as exc:
            with self._lock:
                self._state["rejected_lines"] += 1
                self._state["error"] = str(exc)
            self._publish()
            return
        if (
            telemetry is None
            and button is None
            and output is None
            and power_event is None
            and parsed is None
        ):
            return
        button_event = None
        with self._lock:
            self._state["error"] = None
            if telemetry is not None:
                self._apply_telemetry(telemetry)
            elif button is not None:
                self._state["button_event_count"] += 1
                self._state["last_button"] = dict(button)
                button_event = {
                    "event_count": self._state["button_event_count"],
                    **button,
                    "coordinates_exposed": False,
                }
            elif output is not None:
                self._state["output_last_ack"] = dict(output)
            elif power_event is not None:
                self._state["last_power_event"] = dict(power_event)
                self._last_power_event_monotonic = time.monotonic()
                power_state = power_event.get("state")
                if power_state == "shutdown_requested":
                    self._state["power_ack_requested"] = False
                    self._state["power_cancel_requested"] = False
                    self._state["power_transaction_phase"] = "pending"
                elif power_state == "shutdown_ack":
                    self._state["power_ack_requested"] = False
                    self._state["power_transaction_phase"] = "armed"
                elif power_state in {
                    "shutdown_cancelled",
                    "shutdown_timeout",
                    "gate_off",
                    "gate_on",
                }:
                    self._stm32_pending_power_command = None
                    self._state["power_ack_requested"] = False
                    self._state["power_cancel_requested"] = False
                    self._state["power_transaction_phase"] = "idle"
                elif power_state == "status" and not power_event.get(
                    "shutdown_pending"
                ):
                    self._state["power_ack_requested"] = False
                    self._state["power_cancel_requested"] = False
                    self._state["power_transaction_phase"] = "idle"
            elif parsed is not None:
                self._apply_fix(parsed)
        if button_event is not None:
            self._publish_button(button_event)
        self._publish()

    def _set_connection(self, connected: bool, error: str | None) -> None:
        with self._lock:
            self._state["connected"] = connected
            self._state["error"] = error
            if not connected:
                self._current_fix = False
        self._publish()

    def _publish(self) -> None:
        snapshot = self.snapshot()
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(snapshot)
            except Full:
                try:
                    subscriber.get_nowait()
                except Empty:
                    pass
                try:
                    subscriber.put_nowait(snapshot)
                except Full:
                    pass

    def _publish_button(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._button_subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(dict(event))
            except Full:
                try:
                    subscriber.get_nowait()
                except Empty:
                    pass
                try:
                    subscriber.put_nowait(dict(event))
                except Full:
                    pass
