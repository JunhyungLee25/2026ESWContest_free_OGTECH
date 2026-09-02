#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""지도 SSE를 감시해 CO·트레일·일조·도착 전이를 먼저 말하는 제품 데몬.

2026-08-31부터 CO 경보음도 여기서 낸다. 종전에는 STM32 부저(PB0)가 울렸으나 부저를
걷어냈고, 소리는 이 데몬이 스피커로 낸다 — 경보음(비프) 한 번 뒤에 음성 안내가 붙고,
경보가 지속되는 동안 반복한다. 키오스크 화면은 배너만 띄우고 읽지 않는다(중복 발화와
브라우저(pulse)·aplay 장치 경합을 피한다).
"""

from __future__ import annotations

import argparse
from array import array
from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterator
import urllib.error
import urllib.parse
import urllib.request
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402
import engines as E  # noqa: E402
from pipeline_gate import exclusive_pipeline  # noqa: E402
from product_assistant import LOCAL_HOSTS, MapApiError  # noqa: E402
from tts_pipeline import TtsPipeline  # noqa: E402


# 경보가 걸려 있는 동안 다시 알린다. 부저는 계속 울렸으므로 한 번만 말하고 끝내지 않는다.
CO_ALARM_REPEAT_S = 20.0
CO_WARNING_REPEAT_S = 60.0

# 경보음: (주파수 Hz, 반복 수, 소리 길이 s, 사이 간격 s). 부저 패턴을 스피커로 옮긴 것이다.
TONE_SPECS = {
    "alarm": (1000.0, 3, 0.18, 0.12),
    "warning": (660.0, 2, 0.15, 0.15),
}
TONE_RATE_HZ = 22050
TONE_GAIN = 0.55
TONE_FADE_S = 0.005  # 뚝 끊기며 나는 클릭음을 없앤다


@dataclass(frozen=True)
class ProactiveMessage:
    kind: str
    source_id: str
    text: str
    sound: str = ""  # TONE_SPECS 키. 비면 음성만 내보낸다


# 경보 문장 중 ppm 을 뺀 고정 부분. 데몬이 뜰 때 미리 합성해 캐시에 넣어 둔다 —
# 젯슨 실측(2026-08-31)으로 첫 합성은 모델을 올리느라 6.6 s, 캐시가 차면 0.5 s 였다.
# 경보가 났는데 비프 뒤 6초를 잠자코 있으면 소리를 놓친다.
WARMUP_SENTENCES = (
    "일산화탄소 경보입니다.",
    "즉시 환기하고 대피하세요.",
    "일산화탄소 주의입니다.",
    "환기하고 상태를 확인하세요.",
)


def warm_tts(pipeline: TtsPipeline, output: Path) -> None:
    """모델을 올리고 고정 문장을 미리 합성한다. 실패는 기록만 하고 감시는 그대로 시작한다."""
    # 캐시가 이미 차 있으면 합성이 그냥 넘어가 모델은 안 올라온다. 그런데 ppm 문장은
    # 값이 매번 달라 캐시가 없다 — 모델을 여기서 올려 두지 않으면 첫 경보가 "일산화탄소
    # 경보입니다" 뒤에 3.8초를 쉰다(젯슨 실측 2026-08-31).
    if "sherpa" in getattr(pipeline, "engine_order", ()):
        try:
            E.SherpaOnnxTTS().load()
        except (RuntimeError, OSError) as exc:
            print(f"음성 모델 예열 실패(경보는 그대로 동작): {exc}", file=sys.stderr)
    for sentence in WARMUP_SENTENCES:
        try:
            for _ in pipeline.synthesize_sentences(sentence, output):
                pass
        except (subprocess.SubprocessError, RuntimeError, ValueError, OSError) as exc:
            print(f"음성 예열 실패(경보는 그대로 동작): {exc}", file=sys.stderr)
            return


def alert_tone(kind: str) -> Path:
    """경보음 WAV를 한 번 만들어 두고 재사용한다. 없는 종류면 KeyError."""
    frequency, beeps, on_s, off_s = TONE_SPECS[kind]
    path = C.RESULT_DIR / f"alert_tone_{kind}.wav"
    if path.exists():
        return path
    fade = max(1, int(TONE_RATE_HZ * TONE_FADE_S))
    samples = array("h")
    for beep in range(beeps):
        count = int(TONE_RATE_HZ * on_s)
        for index in range(count):
            gain = TONE_GAIN
            if index < fade:
                gain *= index / fade
            elif index >= count - fade:
                gain *= (count - index) / fade
            samples.append(
                int(32767.0 * gain * math.sin(2.0 * math.pi * frequency * index / TONE_RATE_HZ))
            )
        if beep < beeps - 1:
            samples.extend([0] * int(TONE_RATE_HZ * off_s))
    path.parent.mkdir(parents=True, exist_ok=True)
    # 쓰다 만 파일을 재생하지 않도록 임시 파일에 쓴 뒤 바꿔 끼운다.
    temporary = path.with_name(path.name + ".tmp")
    with wave.open(str(temporary), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(TONE_RATE_HZ)
        handle.writeframes(samples.tobytes())
    temporary.replace(path)
    return path


class AlertDetector:
    """동일 상태 반복 전송을 막고, 해제 후 재발할 때만 다시 알린다."""

    def __init__(self) -> None:
        self.active: dict[str, str | None] = {
            "co_alarm": None,
            "trail": None,
            "daylight": None,
            "arrival": None,
        }
        self._co_repeat_at: float | None = None

    @staticmethod
    def _demo_prefix(device: dict[str, Any]) -> str:
        return C.DEMO_SPEECH_PREFIX if device.get("demo") else ""

    def detect(self, device: dict[str, Any], now: float | None = None) -> list[ProactiveMessage]:
        now = time.monotonic() if now is None else now
        messages: list[ProactiveMessage] = []
        prefix = self._demo_prefix(device)
        co = device.get("co") or {}
        stale = co.get("stale") is True
        if co.get("alarm") is True and not stale:
            co_key = "alarm"
        elif co.get("level") == "warning" and not stale:
            co_key = "warning"
        else:
            co_key = None
        # 경보는 지속되는 동안 반복한다 — 부저를 대신하는 소리라 한 번 말하고 끝내지 않는다.
        repeat_due = (
            co_key is not None
            and self._co_repeat_at is not None
            and now >= self._co_repeat_at
        )
        if co_key and (self.active["co_alarm"] != co_key or repeat_due):
            ppm = co.get("ppm")
            try:
                value = "확인 불가" if ppm is None else str(round(float(ppm)))
            except (TypeError, ValueError):  # 숫자가 아닌 ppm 도 데몬을 죽이지 않는다
                value = "확인 불가"
            if co_key == "alarm":
                kind, headline, action = "co_alarm", "일산화탄소 경보입니다.", "즉시 환기하고 대피하세요."
                self._co_repeat_at = now + CO_ALARM_REPEAT_S
            else:
                kind, headline, action = "co_warning", "일산화탄소 주의입니다.", "환기하고 상태를 확인하세요."
                self._co_repeat_at = now + CO_WARNING_REPEAT_S
            # CO 만 데모 접두사를 붙이지 않는다. device.demo 는 "지도가 샘플"이라는 뜻이고
            # ppm 은 어느 지도를 띄웠든 STM32 센서 실측이다. 진짜 경보를 "데모 값 기준으로"
            # 라고 말하면 사람이 대피하지 않는다.
            messages.append(
                ProactiveMessage(
                    kind,
                    "SAFE-PROACTIVE-CO",
                    f"{headline} 센서 계측은 {value}피피엠입니다. {action}",
                    sound=co_key,
                )
            )
        if co_key is None:
            self._co_repeat_at = None
        self.active["co_alarm"] = co_key

        trail = device.get("trail") or {}
        trail_status = trail.get("status")
        trail_key = (
            str(trail_status)
            if trail_status in {"off_trail", "off_trail_estimate"}
            else None
        )
        if trail_key and self.active["trail"] != trail_key:
            text = (
                "트레일 이탈 경보입니다. 지도에서 현재 위치와 GPS 정확도를 확인하세요."
                if trail_key == "off_trail"
                else "트레일 이탈 가능성이 큽니다. GPS 정확도는 확인되지 않았으므로 지도에서 현재 위치를 확인하세요."
            )
            messages.append(
                ProactiveMessage(
                    "trail",
                    "SAFE-PROACTIVE-TRAIL",
                    prefix + text,
                )
            )
        self.active["trail"] = trail_key

        sun = device.get("sun") or {}
        daylight_key = "return_now" if sun.get("status") == "return_now" else None
        if daylight_key and self.active["daylight"] != daylight_key:
            messages.append(
                ProactiveMessage(
                    "daylight",
                    "SAFE-PROACTIVE-DAYLIGHT",
                    prefix + "귀환 권고 시각에 도달했습니다. 베이스캠프 경로를 화면에서 확인하세요.",
                )
            )
        self.active["daylight"] = daylight_key

        navigation = device.get("navigation") or {}
        arrival = navigation.get("arrival") or {}
        target = arrival.get("target") or {}
        arrival_key = (
            str(target.get("id") or target.get("kind") or "target")
            if arrival.get("arrived")
            else None
        )
        if arrival_key and self.active["arrival"] != arrival_key:
            is_basecamp = target.get("id") == "basecamp" or target.get("kind") == "basecamp"
            messages.append(
                ProactiveMessage(
                    "arrival",
                    "SAFE-PROACTIVE-ARRIVAL",
                    prefix
                    + ("베이스캠프에 도착하였습니다." if is_basecamp else "목적지에 도착하였습니다."),
                )
            )
        self.active["arrival"] = arrival_key
        return messages


def device_events(base_url: str) -> Iterator[dict[str, Any]]:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise MapApiError("장치 이벤트는 로컬 HTTP 주소만 사용할 수 있습니다")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/device/events",
        headers={"Accept": "text/event-stream"},
    )
    with urllib.request.urlopen(request, timeout=30.0) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[5:].strip())
            if isinstance(payload, dict):
                yield payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OGTECH 선제 음성 알림 데몬")
    parser.add_argument("--map-url", default=C.MAP_API_URL)
    parser.add_argument("--tts-order", default=",".join(C.TTS_ENGINE_ORDER))
    parser.add_argument(
        "--no-tts", action="store_true", help="문장만 출력하고 합성하지 않음(경보음은 그대로 재생)"
    )
    parser.add_argument("--no-play", action="store_true")
    parser.add_argument("--once", action="store_true", help="첫 알림을 처리한 뒤 종료")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    detector = AlertDetector()
    order = tuple(item.strip() for item in args.tts_order.split(",") if item.strip())
    pipeline = TtsPipeline(engine_order=order)
    output = C.RESULT_DIR / "proactive_alert.wav"
    if not args.no_tts:
        warm_tts(pipeline, C.RESULT_DIR / "warmup.wav")
    print("선제 알림 감시 시작: CO 경보음·음성 · 트레일 이탈 · 귀환 권고 · 도착")
    while True:
        try:
            for device in device_events(args.map_url):
                for message in detector.detect(device):
                    print(f"[{message.kind}] {message.text}")
                    if not (args.no_tts and args.no_play):
                        try:
                            with exclusive_pipeline():
                                # 경보음 먼저, 이어서 음성. 락 하나 안에 묶어야 그 사이로
                                # 마이크 녹음(physical_voice)이 끼어들지 않는다.
                                if message.sound and not args.no_play:
                                    E.play(alert_tone(message.sound))
                                if not args.no_tts:
                                    for result in pipeline.synthesize_sentences(message.text, output):
                                        if not args.no_play:
                                            E.play(result.path)
                        except (subprocess.SubprocessError, RuntimeError, ValueError, OSError) as exc:
                            # 재생 1회 실패로 데몬이 죽으면 재시작된 새 detector 가 활성 경보를
                            # 다시 발화한다(크래시-재발화 루프). 기록만 남기고 상태는 유지한다.
                            print(f"[{message.kind}] 알림 재생 실패: {exc}", file=sys.stderr)
                            if args.once:
                                return 1
                            time.sleep(2.0)
                    if args.once:
                        return 0
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, MapApiError) as exc:
            print(f"지도 이벤트 연결 대기: {exc}", file=sys.stderr)
            time.sleep(2.0)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
