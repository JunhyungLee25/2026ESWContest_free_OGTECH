#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STM32 물리 음성 버튼 edge로 push-to-talk 제품 파이프라인을 실행한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Iterator
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402
from pipeline_gate import exclusive_pipeline  # noqa: E402
from product_assistant import LOCAL_HOSTS, MapApiError  # noqa: E402
from product_voice import run_once  # noqa: E402


MIN_HOLD_MS = 250
MAX_HOLD_MS = 15_000


class VoiceButtonState:
    """pressed/released 쌍만 1회 세션으로 바꾸는 작은 상태기계."""

    def __init__(self) -> None:
        self.active = False

    def accept(self, event: dict[str, Any]) -> str | None:
        if (
            event.get("button") != "voice"
            or event.get("coordinates_exposed") is not False
        ):
            return None
        state = event.get("state")
        if state == "pressed" and not self.active:
            self.active = True
            return "start"
        if state != "released" or not self.active:
            return None
        self.active = False
        held_ms = int(event.get("held_ms") or 0)
        return "finish" if MIN_HOLD_MS <= held_ms <= MAX_HOLD_MS else "discard"


class ArecordSession:
    """버튼을 누르는 동안 16 kHz mono PCM WAV를 기록한다."""

    def __init__(self, path: Path, *, device: str, max_seconds: int = 15) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [
                "arecord",
                "-D",
                device,
                "-f",
                "S16_LE",
                "-r",
                str(C.REC_RATE),
                "-c",
                str(C.REC_CHANNELS),
                "-d",
                str(max_seconds),
                "-q",
                str(path),
            ],
            stdin=subprocess.DEVNULL,
        )

    def stop(self) -> Path:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()  # 고아 arecord 가 마이크를 계속 쥐지 않게 한다
                    self.process.wait()
        if self.process.returncode not in {0, -signal.SIGINT}:
            raise RuntimeError(f"arecord 종료 코드가 올바르지 않습니다: {self.process.returncode}")
        if not self.path.is_file() or self.path.stat().st_size <= 44:
            raise RuntimeError("물리 버튼 녹음 WAV가 비어 있습니다")
        return self.path

    def discard(self) -> None:
        try:
            self.stop()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass


def button_events(base_url: str) -> Iterator[dict[str, Any]]:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise MapApiError("물리 버튼 이벤트는 로컬 HTTP 주소만 사용할 수 있습니다")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/buttons/events",
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
    parser = argparse.ArgumentParser(description="OGTECH STM32 물리 음성 버튼 데몬")
    parser.add_argument("--map-url", default=C.MAP_API_URL)
    parser.add_argument("--stt", default=C.STT_ENGINE)
    parser.add_argument("--tts-order", default=",".join(C.TTS_ENGINE_ORDER))
    parser.add_argument("--mic-device", default=C.MIC_DEVICE)
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument("--no-play", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def _voice_args(args: argparse.Namespace, wav: Path, release_ns: int) -> argparse.Namespace:
    return argparse.Namespace(
        text=None,
        input_wav=str(wav),
        release_monotonic_ns=release_ns,
        seconds=C.REC_SECONDS,
        repeat=1,
        stt=args.stt,
        tts_order=args.tts_order,
        map_url=args.map_url,
        no_tts=args.no_tts,
        no_play=args.no_play,
        json=True,
    )


def main() -> int:
    args = parse_args()
    state = VoiceButtonState()
    recorder: ArecordSession | None = None
    run_index = 0
    print("물리 음성 버튼 대기: 누르는 동안 녹음, 놓으면 STT→안전 분기→TTS")
    while True:
        try:
            for event in button_events(args.map_url):
                transition = state.accept(event)
                if transition == "start":
                    # press 즉시 녹음한다. 파이프라인 락은 여기서 잡지 않는다 — device_monitor 가
                    # 경보를 재생 중이면 락 대기(최대 30 s) 동안 발화 앞부분이 녹음에서 빠진다.
                    run_index += 1
                    path = C.RESULT_DIR / f"physical_voice_{run_index:04d}.wav"
                    recorder = ArecordSession(path, device=args.mic_device)
                    print(f"[BUTTON] pressed · 녹음 시작 · event={event.get('event_count')}")
                    continue
                if transition not in {"finish", "discard"}:
                    continue
                release_ns = time.monotonic_ns()
                try:
                    if recorder is None:
                        continue
                    if transition == "discard":
                        recorder.discard()
                        print("[BUTTON] 너무 짧거나 긴 발화는 처리하지 않았습니다")
                    else:
                        wav = recorder.stop()
                        # 락은 STT→TTS 구간에만 건다. 대기 중에도 녹음 파일은 이미 닫혀 있다.
                        with exclusive_pipeline():
                            row = run_once(_voice_args(args, wav, release_ns), run_index)
                        print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                        if args.once:
                            return 0
                finally:
                    recorder = None
            raise TimeoutError("물리 버튼 SSE 연결이 종료되었습니다")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, MapApiError) as exc:
            print(f"물리 버튼 연결 대기: {exc}", file=sys.stderr)
            if recorder is not None:
                recorder.discard()
                recorder = None
            state.active = False
            time.sleep(2.0)
        except (OSError, RuntimeError, FileNotFoundError, ValueError, subprocess.SubprocessError) as exc:
            print(f"물리 음성 처리 실패: {exc}", file=sys.stderr)
            if recorder is not None:
                recorder.discard()
                recorder = None
            state.active = False
            if args.once:
                return 1
        except KeyboardInterrupt:
            if recorder is not None:
                recorder.discard()
            return 0

if __name__ == "__main__":
    raise SystemExit(main())
