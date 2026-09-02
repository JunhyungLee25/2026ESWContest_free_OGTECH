"""CO 경보 판정. 부저가 하던 일을 Jetson이 이어받는다(2026-08-31).

STM32는 2026-08-31부터 경보음을 내지 않는다(부저 PB0 제거). 실장 펌웨어가 보내는
`$SA1` CSV에는 경보 필드가 아예 없어 `parse_stm32_ogt1`은 `level`·`alarm`을 채우지
못한다. 그래서 판정을 여기서 한 번 더 한다 — 경보음을 내는 쪽이 판정도 해야
"소리는 나는데 화면은 정상"이 생기지 않는다.

임계·지속 시간은 OGTECH-embedded `Core/Src/co_alarm.c`와 같은 값이다. 한쪽을 바꾸면
다른 쪽도 같이 바꾼다.

펌웨어와 다른 점 하나: 펌웨어는 예열 30초 중에도 100 ppm 이상이면 즉시 ALARM을 건다.
CSV는 예열 중(`co_state=0`) ppm을 아예 보내지 않으므로 Jetson은 그 구간을 판정할 수
없다. 예열 중 고농도는 예열이 끝난 첫 유효 프레임에서 잡힌다.
"""

from __future__ import annotations


CO_WARN_PPM = 35.0        # 주의: 35 ppm 지속
CO_WARN_HOLD_S = 180.0    # 3분 지속 시 WARN
CO_ALARM_PPM = 100.0      # 100 ppm 즉시 ALARM
CO_CLEAR_PPM = 30.0       # 30 ppm 미만
CO_CLEAR_HOLD_S = 30.0    # 30초 지속 시 해제

LEVEL_NONE = "none"
LEVEL_WARNING = "warning"
LEVEL_ALARM = "alarm"


class CoAlarmJudge:
    """ppm 시계열에서 경보 상태를 만든다. 한 번 올라간 경보는 스스로 내려오지 않는다.

    센서가 끊겼다는 이유로 경보를 해제하지 않는다(latched) — 펌웨어와 같은 규칙이다.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._level = LEVEL_NONE
        self._warn_since: float | None = None
        self._clear_since: float | None = None

    @property
    def level(self) -> str:
        return self._level

    def update(self, now: float, *, valid: bool, ppm: float | None) -> str:
        """유효 프레임 하나를 반영하고 판정 결과를 돌려준다. now는 단조 시계(초)."""
        value: float | None = None
        if valid and ppm is not None:
            try:
                value = float(ppm)
            except (TypeError, ValueError):
                value = None

        if value is None:
            # 입력이 없는 동안은 판정을 진행하지도, 이미 걸린 경보를 풀지도 않는다.
            self._warn_since = None
            self._clear_since = None
            return self._level

        if value >= CO_ALARM_PPM:
            self._level = LEVEL_ALARM
            self._warn_since = None
            self._clear_since = None
            return self._level

        if value >= CO_WARN_PPM:
            self._clear_since = None
            if self._warn_since is None:
                self._warn_since = now
            if self._level == LEVEL_NONE and now - self._warn_since >= CO_WARN_HOLD_S:
                self._level = LEVEL_WARNING
            return self._level

        self._warn_since = None

        if self._level != LEVEL_NONE and value < CO_CLEAR_PPM:
            if self._clear_since is None:
                self._clear_since = now
            if now - self._clear_since >= CO_CLEAR_HOLD_S:
                self._level = LEVEL_NONE
                self._clear_since = None
        else:
            self._clear_since = None
        return self._level
