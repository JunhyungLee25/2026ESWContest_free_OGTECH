"""네트워크 없이 현재 좌표의 일출·일몰·시민박명 시간을 계산한다."""

from __future__ import annotations

from datetime import date, datetime, time as datetime_time, timedelta, timezone, tzinfo
from math import acos, asin, atan, cos, degrees, floor, radians, sin, tan
import os
from typing import Any


OFFICIAL_ZENITH_DEG = 90.833
CIVIL_ZENITH_DEG = 96.0


class SolarCalculationError(ValueError):
    """좌표·시간대 설정 또는 태양 사건 계산이 유효하지 않을 때 발생한다."""


def configured_timezone() -> tzinfo:
    """환경 변수, IANA 이름, 운영체제 현지 시간대 순서로 시간대를 고른다."""

    raw_offset = os.getenv("OGTECH_UTC_OFFSET_MIN", "").strip()
    if raw_offset:
        try:
            minutes = int(raw_offset)
        except ValueError as exc:
            raise SolarCalculationError("OGTECH_UTC_OFFSET_MIN은 분 단위 정수여야 합니다") from exc
        if not -14 * 60 <= minutes <= 14 * 60:
            raise SolarCalculationError("UTC 오프셋은 -14시간부터 +14시간 사이여야 합니다")
        return timezone(timedelta(minutes=minutes))

    timezone_name = os.getenv("OGTECH_TIMEZONE", "").strip()
    if timezone_name:
        try:
            from zoneinfo import ZoneInfo  # Python 3.9+

            return ZoneInfo(timezone_name)
        except (ImportError, KeyError) as exc:
            raise SolarCalculationError(
                "OGTECH_TIMEZONE을 읽지 못했습니다. Jetson 시스템 시간대를 설정하거나 "
                "OGTECH_UTC_OFFSET_MIN을 사용하세요"
            ) from exc

    return datetime.now().astimezone().tzinfo or timezone.utc


def _normalized_degrees(value: float) -> float:
    return value % 360.0


def _event_utc_hour(
    local_date: date,
    latitude: float,
    longitude: float,
    *,
    zenith_deg: float,
    sunrise: bool,
) -> float | None:
    """NOAA가 공개한 근사 절차로 해당 날짜의 UTC 시각(시간)을 구한다."""

    day_of_year = local_date.timetuple().tm_yday
    longitude_hour = longitude / 15.0
    approximate = day_of_year + ((6.0 if sunrise else 18.0) - longitude_hour) / 24.0
    mean_anomaly = 0.9856 * approximate - 3.289
    true_longitude = _normalized_degrees(
        mean_anomaly
        + 1.916 * sin(radians(mean_anomaly))
        + 0.020 * sin(radians(2.0 * mean_anomaly))
        + 282.634
    )

    right_ascension = _normalized_degrees(
        degrees(atan(0.91764 * tan(radians(true_longitude))))
    )
    longitude_quadrant = floor(true_longitude / 90.0) * 90.0
    ascension_quadrant = floor(right_ascension / 90.0) * 90.0
    right_ascension = (right_ascension + longitude_quadrant - ascension_quadrant) / 15.0

    sin_declination = 0.39782 * sin(radians(true_longitude))
    cos_declination = cos(asin(sin_declination))
    denominator = cos_declination * cos(radians(latitude))
    if abs(denominator) < 1e-12:
        return None
    cosine_hour = (
        cos(radians(zenith_deg))
        - sin_declination * sin(radians(latitude))
    ) / denominator
    if cosine_hour > 1.0 or cosine_hour < -1.0:
        return None

    hour_angle = (
        360.0 - degrees(acos(cosine_hour))
        if sunrise
        else degrees(acos(cosine_hour))
    ) / 15.0
    local_mean_time = hour_angle + right_ascension - 0.06571 * approximate - 6.622
    return (local_mean_time - longitude_hour) % 24.0


def _event_datetime(
    local_date: date,
    latitude: float,
    longitude: float,
    local_tz: tzinfo,
    *,
    zenith_deg: float,
    sunrise: bool,
) -> datetime | None:
    utc_hour = _event_utc_hour(
        local_date,
        latitude,
        longitude,
        zenith_deg=zenith_deg,
        sunrise=sunrise,
    )
    if utc_hour is None:
        return None
    seconds = int(round(utc_hour * 3600.0))
    utc_midnight = datetime.combine(local_date, datetime_time.min, tzinfo=timezone.utc)
    candidates = [
        (utc_midnight + timedelta(days=day_shift, seconds=seconds)).astimezone(local_tz)
        for day_shift in (-1, 0, 1)
    ]
    for candidate in candidates:
        if candidate.date() == local_date:
            return candidate
    local_noon = datetime.combine(local_date, datetime_time(12, 0), tzinfo=local_tz)
    return min(candidates, key=lambda candidate: abs((candidate - local_noon).total_seconds()))


def calculate_solar_times(
    latitude: float,
    longitude: float,
    *,
    now: datetime | None = None,
    local_tz: tzinfo | None = None,
) -> dict[str, Any]:
    """현재 좌표의 현지 일출·일몰·시민박명 종료를 반환한다."""

    if not -90.0 <= float(latitude) <= 90.0:
        raise SolarCalculationError("위도가 유효 범위를 벗어났습니다")
    if not -180.0 <= float(longitude) <= 180.0:
        raise SolarCalculationError("경도가 유효 범위를 벗어났습니다")
    selected_tz = local_tz or configured_timezone()
    current = now or datetime.now(selected_tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=selected_tz)
    else:
        current = current.astimezone(selected_tz)
    local_date = current.date()
    sunrise = _event_datetime(
        local_date,
        float(latitude),
        float(longitude),
        selected_tz,
        zenith_deg=OFFICIAL_ZENITH_DEG,
        sunrise=True,
    )
    sunset = _event_datetime(
        local_date,
        float(latitude),
        float(longitude),
        selected_tz,
        zenith_deg=OFFICIAL_ZENITH_DEG,
        sunrise=False,
    )
    civil_end = _event_datetime(
        local_date,
        float(latitude),
        float(longitude),
        selected_tz,
        zenith_deg=CIVIL_ZENITH_DEG,
        sunrise=False,
    )
    remaining_min = None
    if sunset is not None:
        remaining_min = int((sunset - current).total_seconds() // 60)
    return {
        "computed": sunrise is not None and sunset is not None,
        "date": local_date.isoformat(),
        "timezone": str(selected_tz),
        "now": current,
        "sunrise": sunrise,
        "sunset": sunset,
        "civil_end": civil_end,
        "remaining_min": remaining_min,
    }


def iso_or_none(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat(timespec="seconds")


def clock_or_none(value: datetime | None) -> str | None:
    return None if value is None else value.strftime("%H:%M")
