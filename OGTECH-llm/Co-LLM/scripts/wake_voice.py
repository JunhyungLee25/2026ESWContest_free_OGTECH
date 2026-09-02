#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""'오지야' 호출어 상시 청취 데몬.

마이크를 계속 듣되, VAD(sherpa-onnx Silero)가 끊어 준 짧은 발화만 whisper 에 넣어 호출어인지 본다.
호출어가 잡히면 인사말을 읽고, 이어지는 발화는 기존 제품 경로(product_voice._build_assistant →
DemoAssistant → 검수 카드·지도 명령)에 그대로 넘긴다. 화면(/video/)은 건드리지 않는다 —
지도 명령 결과는 /api/voice 이벤트로 이미 화면에 간다.

상태: idle ─호출어→ await_command ─질문 응답→ await_confirm ┐
                                  └─일반 응답→ followup     ┴─시간 초과→ idle

파일 입력(--input-wavs)은 마이크 대신 WAV 들을 무음 간격으로 이어 같은 경로를 태우는 검증 모드다.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator, Optional
import urllib.error
import urllib.parse
import urllib.request
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402
import engines as E  # noqa: E402
from ogtech_core import normalize_utterance  # noqa: E402
from pipeline_gate import exclusive_pipeline  # noqa: E402
from product_assistant import LOCAL_HOSTS, AssistantResult, MapApiClient, MapApiError  # noqa: E402
import product_voice  # noqa: E402
from tts_pipeline import TtsPipeline  # noqa: E402

RATE = 16000
WINDOW = 512                      # Silero 창 크기(32 ms). 마이크·파일 모두 이 단위로 넣는다
MIN_WHISPER_S = 1.6               # whisper.cpp 는 1초 미만 입력을 거부한다 — 뒤를 무음으로 채운다
DEFAULT_CONFIG = C.CO_LLM_DIR / "config" / "wake_voice.json"


# =============================================================
# 발화 분절
# =============================================================

@dataclass
class Segment:
    samples: Any            # float32 numpy, 16 kHz 모노, 앞뒤 여유분 포함
    start_s: float          # 스트림 기준 발화 시작(여유분 제외)
    end_s: float            # 스트림 기준 발화 끝(여유분 제외)

    @property
    def speech_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


class _RingSegmenter:
    """스트림 링버퍼 + 앞뒤 여유분 절단. 실제 판정은 하위 클래스가 창 단위로 한다."""

    name = "base"

    def __init__(self, *, pre_roll_s: float = 0.25, post_roll_s: float = 0.15, ring_s: float = 40.0) -> None:
        import numpy as np

        self._np = np
        self.pre = int(pre_roll_s * RATE)
        self.post = int(post_roll_s * RATE)
        self.ring = np.zeros(int(ring_s * RATE), dtype=np.float32)
        self.total = 0                         # 지금까지 넣은 샘플 수 = 스트림 시각
        self._carry = np.zeros(0, dtype=np.float32)

    def now_s(self) -> float:
        return self.total / RATE

    def _push(self, chunk) -> None:
        n = len(chunk)
        if n >= len(self.ring):
            self.ring[:] = chunk[-len(self.ring):]
        else:
            head = self.total % len(self.ring)
            first = min(n, len(self.ring) - head)
            self.ring[head:head + first] = chunk[:first]
            if first < n:
                self.ring[: n - first] = chunk[first:]
        self.total += n

    def _slice(self, start: int, end: int):
        start = max(start, self.total - len(self.ring), 0)
        end = min(max(end, start), self.total)
        idx = self._np.arange(start, end) % len(self.ring)
        return self.ring[idx].copy()

    def _emit(self, start: int, end: int) -> Segment:
        samples = self._slice(start - self.pre, end + self.post)
        return Segment(samples, start / RATE, end / RATE)

    def feed(self, chunk) -> list[Segment]:
        chunk = self._np.asarray(chunk, dtype=self._np.float32).reshape(-1)
        out: list[Segment] = []
        data = self._np.concatenate([self._carry, chunk]) if len(self._carry) else chunk
        offset = 0
        while len(data) - offset >= WINDOW:
            window = data[offset:offset + WINDOW]
            start_index = self.total
            self._push(window)
            for seg_start, seg_end in self._process(window, start_index):
                out.append(self._emit(seg_start, seg_end))
            offset += WINDOW
        self._carry = data[offset:].copy()
        return out

    def flush(self) -> list[Segment]:
        return []

    def reset(self) -> None:
        pass

    def _process(self, window, start_index: int) -> list[tuple[int, int]]:
        raise NotImplementedError


class SileroSegmenter(_RingSegmenter):
    """sherpa-onnx Silero VAD. 확률 임계 0.5, 0.5초 무음이면 발화 종료."""

    name = "silero"

    def __init__(
        self,
        model_path: str,
        *,
        min_silence_s: float = 0.5,
        min_speech_s: float = 0.2,
        max_speech_s: float = 8.0,
        threshold: float = 0.5,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        import sherpa_onnx

        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(model_path)
        cfg.silero_vad.threshold = float(threshold)
        cfg.silero_vad.min_silence_duration = float(min_silence_s)
        cfg.silero_vad.min_speech_duration = float(min_speech_s)
        if hasattr(cfg.silero_vad, "max_speech_duration"):
            cfg.silero_vad.max_speech_duration = float(max_speech_s)
        cfg.sample_rate = RATE
        if int(cfg.silero_vad.window_size) != WINDOW:
            raise RuntimeError("Silero 창 크기가 %d 이 아닙니다: %s" % (WINDOW, cfg.silero_vad.window_size))
        self.vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=max_speech_s + 10.0)
        self._base = 0          # reset() 뒤 sherpa 의 start 가 0 부터 다시 세므로 절대 위치로 보정

    def _drain(self) -> list[tuple[int, int]]:
        out = []
        while not self.vad.empty():
            seg = self.vad.front
            start = self._base + int(seg.start)
            out.append((start, start + len(seg.samples)))
            self.vad.pop()
        return out

    def _process(self, window, start_index: int) -> list[tuple[int, int]]:
        self.vad.accept_waveform(window)
        return self._drain()

    def flush(self) -> list[Segment]:
        if hasattr(self.vad, "flush"):
            self.vad.flush()
        return [self._emit(s, e) for s, e in self._drain()]

    def reset(self) -> None:
        self.vad.reset()
        self._base = self.total


class EnergySegmenter(_RingSegmenter):
    """RMS 에너지 분절(폴백·단위 테스트용). 잡음 바닥을 EMA 로 따라가며 그 4배 또는 절대 하한을 넘으면 발화."""

    name = "energy"

    def __init__(
        self,
        *,
        min_threshold: float = 0.015,
        ratio: float = 4.0,
        start_windows: int = 3,
        min_silence_s: float = 0.5,
        min_speech_s: float = 0.2,
        max_speech_s: float = 8.0,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.min_threshold = min_threshold
        self.ratio = ratio
        self.start_windows = start_windows
        self.silence_windows = max(1, int(min_silence_s * RATE / WINDOW))
        self.min_speech = int(min_speech_s * RATE)
        self.max_speech = int(max_speech_s * RATE)
        self.floor = 0.003
        self._loud_run = 0
        self._quiet_run = 0
        self._speech_start: Optional[int] = None
        self._last_loud_end = 0

    def _process(self, window, start_index: int) -> list[tuple[int, int]]:
        rms = float(self._np.sqrt(self._np.mean(window * window)) if len(window) else 0.0)
        threshold = max(self.min_threshold, self.floor * self.ratio)
        loud = rms > threshold
        out: list[tuple[int, int]] = []
        if self._speech_start is None:
            if loud:
                self._loud_run += 1
                if self._loud_run >= self.start_windows:
                    self._speech_start = start_index - (self._loud_run - 1) * WINDOW
                    self._last_loud_end = start_index + WINDOW
                    self._quiet_run = 0
            else:
                self._loud_run = 0
                self.floor = 0.98 * self.floor + 0.02 * rms
            return out
        if loud:
            self._quiet_run = 0
            self._last_loud_end = start_index + WINDOW
        else:
            self._quiet_run += 1
        ended = self._quiet_run >= self.silence_windows
        overlong = start_index + WINDOW - self._speech_start >= self.max_speech
        if ended or overlong:
            end = self._last_loud_end if ended else start_index + WINDOW
            if end - self._speech_start >= self.min_speech:
                out.append((self._speech_start, end))
            self._speech_start = None
            self._loud_run = 0
            self._quiet_run = 0
        return out

    def flush(self) -> list[Segment]:
        if self._speech_start is None:
            return []
        start, end = self._speech_start, max(self._last_loud_end, self.total)
        self.reset()
        return [self._emit(start, end)] if end - start >= self.min_speech else []

    def reset(self) -> None:
        self._speech_start = None
        self._loud_run = 0
        self._quiet_run = 0


def make_segmenter(kind: str, vad_model: str, **kw: Any) -> _RingSegmenter:
    if kind in ("auto", "silero"):
        if vad_model and os.path.exists(vad_model):
            try:
                return SileroSegmenter(vad_model, **kw)
            except Exception as exc:  # noqa: BLE001 — 모델·모듈 문제는 폴백 후 경고
                if kind == "silero":
                    raise
                print("[WARN] Silero VAD 를 열지 못해 에너지 분절로 내려갑니다: %s" % exc, file=sys.stderr)
        elif kind == "silero":
            raise RuntimeError("Silero VAD 모델이 없습니다: %s (OGTECH_VAD_ONNX)" % vad_model)
        else:
            print("[WARN] Silero VAD 모델이 없어 에너지 분절로 내려갑니다: %s" % vad_model, file=sys.stderr)
    return EnergySegmenter(**kw)


# =============================================================
# 오디오 입력
# =============================================================

class MicSource:
    """arecord 를 raw PCM 스트림으로 띄워 512샘플씩 돌려준다. now() 는 벽시계."""

    def __init__(self, device: str) -> None:
        self.device = device
        self.process: Optional[subprocess.Popen] = None

    def frames(self) -> Iterator[Any]:
        import numpy as np

        cmd = ["arecord", "-D", self.device, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw", "-q"]
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        assert self.process.stdout is not None
        need = WINDOW * 2
        try:
            while True:
                buf = self.process.stdout.read(need)
                if not buf:
                    break
                if len(buf) < need:
                    buf = buf + b"\0" * (need - len(buf))
                yield np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
        finally:
            self.close()
        code = self.process.returncode
        raise RuntimeError("arecord 가 끝났습니다(종료 코드 %s). 마이크 장치 %s 를 확인하세요" % (code, self.device))

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    @staticmethod
    def now() -> float:
        return time.monotonic()


class FileSource:
    """WAV 들을 무음 간격으로 이어 마이크처럼 흘린다. now() 는 흘려보낸 샘플 수 기준 가상 시각."""

    def __init__(self, paths: list[str], *, lead_s: float = 1.0, gap_s: float = 1.5, tail_s: float = 12.0) -> None:
        self.paths = [Path(p) for p in paths]
        self.lead_s, self.gap_s, self.tail_s = lead_s, gap_s, tail_s
        self.emitted = 0

    def now(self) -> float:
        return self.emitted / RATE

    def _wav(self, path: Path):
        import numpy as np

        with wave.open(str(path), "rb") as w:
            if w.getframerate() != RATE or w.getsampwidth() != 2:
                raise RuntimeError("16 kHz 16bit WAV 만 넣을 수 있습니다: %s" % path)
            channels = w.getnchannels()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        return data

    def frames(self) -> Iterator[Any]:
        import numpy as np

        def chunks(data):
            for i in range(0, len(data), WINDOW):
                piece = data[i:i + WINDOW]
                if len(piece) < WINDOW:
                    piece = np.concatenate([piece, np.zeros(WINDOW - len(piece), dtype=np.float32)])
                self.emitted += WINDOW
                yield piece

        def silence(seconds):
            return np.zeros(int(seconds * RATE), dtype=np.float32)

        yield from chunks(silence(self.lead_s))
        for index, path in enumerate(self.paths):
            if index:
                yield from chunks(silence(self.gap_s))
            yield from chunks(self._wav(path))
        yield from chunks(silence(self.tail_s))


# =============================================================
# 받아쓰기 후처리
# =============================================================

_SENTENCE_SPLIT = re.compile(r"(?<=[.?!,])\s+")


def collapse_repeats(text: str) -> str:
    """whisper 가 같은 어절을 반복 생성한 출력에서 연속 중복 문장을 하나로 접는다."""
    pieces = [piece.strip() for piece in _SENTENCE_SPLIT.split(str(text or "").strip()) if piece.strip()]
    kept: list[str] = []
    for piece in pieces:
        if kept and normalize_utterance(piece) == normalize_utterance(kept[-1]):
            continue
        kept.append(piece)
    joined = " ".join(kept)
    # 마침표 없이 같은 어절이 이어질 때: 앞 절반이 뒤 절반과 같으면 절반으로 줄인다
    words = joined.split(" ")
    for size in range(1, len(words) // 2 + 1):
        if len(words) % size == 0 and len(words) // size >= 3 and all(words[i:i + size] == words[:size] for i in range(0, len(words), size)):
            return " ".join(words[:size])
    return joined


def apply_wake_lexicon(text: str, rules: list[dict[str, Any]]) -> str:
    """데몬 전용 STT 보정. 라우팅 전에만 쓰고 스피커 문장에는 쓰지 않는다."""
    value = str(text or "")
    for rule in rules or []:
        pattern = str(rule.get("pattern") or "")
        if pattern:
            value = re.sub(pattern, str(rule.get("replace") or ""), value)
    return value


def yes_no(confirmation: dict[str, Any], text: str) -> Optional[bool]:
    norm = normalize_utterance(text)
    if not norm:
        return None
    for pattern in confirmation.get("no_patterns") or []:
        if re.search(pattern, norm):
            return False
    for pattern in confirmation.get("yes_patterns") or []:
        if re.search(pattern, norm):
            return True
    return None


# =============================================================
# 호출어 판정
# =============================================================

@dataclass(frozen=True)
class WakeHit:
    variant: str
    remainder: str


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def match_wake(text: str, wake_cfg: dict[str, Any]) -> Optional[WakeHit]:
    """정규화한 발화가 호출어로 시작하면 (변형, 나머지 문장) 을 돌려준다.

    prefix_variants 는 뒤에 명령이 붙어도 되고, exact_variants 는 발화 전체가 그것일 때만 인정한다
    ('오지' 를 접두로 허용하면 '오지 않아' 가 호출어가 된다).
    """
    norm = normalize_utterance(text)
    if not norm:
        return None
    fillers = set(str(f).strip() for f in wake_cfg.get("leading_fillers") or [] if str(f).strip())
    words = norm.split(" ")
    while len(words) > 1 and words[0] in fillers:
        words = words[1:]
    norm = " ".join(words)
    compact = _compact(norm)
    exact = [_compact(str(v).lower()) for v in wake_cfg.get("exact_variants") or []]
    if compact in exact:
        return WakeHit(compact, "")
    prefixes = [(_compact(str(v).lower()), False) for v in wake_cfg.get("prefix_variants") or []]
    prefixes += [(_compact(str(v).lower()), True) for v in wake_cfg.get("command_only_variants") or []]
    prefixes.sort(key=lambda item: len(item[0]), reverse=True)
    for variant, command_only in prefixes:
        if not variant or not compact.startswith(variant):
            continue
        if command_only and len(compact) <= len(variant):
            continue
        # 공백을 무시하고 변형 글자 수만큼 소비한 뒤 남은 원문이 명령이다
        consumed = 0
        cut = 0
        for cut, char in enumerate(norm):
            if char != " ":
                consumed += 1
            if consumed == len(variant):
                cut += 1
                break
        remainder = norm[cut:].strip(" ,")
        return WakeHit(variant, remainder)
    return None


# =============================================================
# 응답 문구 (시연 대본 오버레이)
# =============================================================

@dataclass(frozen=True)
class Reply:
    speech: str
    pending: Optional[str]      # None | "map"(지도 서버가 후보 보유) | "scripted"(데몬이 대본상 후보 보유)
    source: str                 # canonical | lake_map | lake_scripted | weather_screen | scripted_confirm | scripted_reject


def _pending_distance_m(map_event: Optional[dict[str, Any]]) -> Optional[float]:
    pending = (map_event or {}).get("pending_destination")
    if not isinstance(pending, dict):
        return None
    try:
        value = float(pending.get("distance_m"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _round_up_100(value: float) -> int:
    return max(100, int(math.ceil(value / 100.0) * 100))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def load_water_pois(paths: list[str]) -> list[dict[str, Any]]:
    """지도 서버와 같은 poi_catalog.json 에서 water_source 표식만 읽는다. 첫 번째로 존재하는 경로를 쓴다."""
    for raw in paths or []:
        path = Path(os.path.expanduser(str(raw)))
        if not path.is_absolute():
            path = (Path(__file__).resolve().parent / path).resolve()
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pois = []
        for item in payload.get("pois") or []:
            if not isinstance(item, dict) or item.get("kind") != "water_source":
                continue
            try:
                lat, lon = float(item["lat"]), float(item["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(lat) and math.isfinite(lon):
                pois.append({"id": item.get("id"), "name": str(item.get("name") or "수원 표식"), "lat": lat, "lon": lon})
        if pois:
            return pois
    return []


def nearest_poi(pois: list[dict[str, Any]], lat: float, lon: float) -> Optional[tuple[dict[str, Any], float]]:
    best = None
    for poi in pois:
        distance = haversine_m(lat, lon, poi["lat"], poi["lon"])
        if best is None or distance < best[1]:
            best = (poi, distance)
    return best


def lake_distance_phrase(lake: dict[str, Any], distance_m: Optional[float]) -> str:
    if distance_m is None or not lake.get("with_distance"):
        return str(lake.get("without_distance") or "")
    floor = int(lake.get("distance_floor_m") or 0)
    spoken = max(floor, _round_up_100(distance_m)) if floor else _round_up_100(distance_m)
    return str(lake["with_distance"]).format(distance_m=spoken)


def spoken_temperature(temp_c: float) -> str:
    """화면 값(소수 1자리)을 TTS 가 또렷이 읽는 꼴로: 28.0 → "28", 24.1 → "24점 1" (sherpa KSS 는 "24.1"을 못 읽는다, 2026-09-02 실측)."""
    rounded = round(float(temp_c), 1)
    whole = int(rounded) if rounded >= 0 else -int(-rounded)
    tenth = int(round(abs(rounded - whole) * 10))
    return str(whole) if tenth == 0 else "%d점 %d" % (whole, tenth)


def strip_demo_prefix(text: str) -> str:
    value = str(text or "")
    prefix = C.DEMO_SPEECH_PREFIX
    while prefix and prefix in value:
        value = value.replace(prefix, "", 1)
    return value.strip()


def compose_reply(result: AssistantResult, script: dict[str, Any], *, distance_m: Optional[float] = None) -> Reply:
    """제품 경로가 확정한 응답 위에 시연 대본 문구를 얹는다. 숫자는 지도·센서·표식 좌표에서만 온다."""
    action = result.decision.map_action
    event = result.map_event or {}
    status = str(event.get("status") or "")
    device = result.device or {}
    lake = script.get("lake") or {}

    if action == "find_nearest_water" and lake:
        if status == "confirmation_required":
            distance = _pending_distance_m(result.map_event)
            if distance is None:
                distance = distance_m
            return Reply(lake_distance_phrase(lake, distance) or result.speech, "map", "lake_map")
        if status == "rejected" and lake.get("no_fix") == "scripted" and lake.get("without_distance"):
            return Reply(lake_distance_phrase(lake, distance_m), "scripted", "lake_scripted")

    if action == "confirm_destination" and status == "accepted" and lake.get("confirmed"):
        return Reply(str(lake["confirmed"]), None, "lake_confirmed")

    if action == "status" and result.decision.scenario_id == "weather" and script.get("weather"):
        env = device.get("environment") or {}
        if env.get("valid") and not env.get("stale"):
            try:
                temp = float(env.get("temp_c"))
                humidity = float(env.get("humidity_pct"))
            except (TypeError, ValueError):
                temp = humidity = float("nan")
            if math.isfinite(temp) and math.isfinite(humidity):
                text = str(script["weather"]).format(temp_c=spoken_temperature(temp), humidity_pct=int(round(humidity)))
                return Reply(text, None, "weather_screen")

    pending = "map" if isinstance(event.get("pending_destination"), dict) else None
    return Reply(result.speech, pending, "canonical")


_PHRASE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|,\s+")


def speech_phrases(text: str, normal_length_scale: float, slow_speed: float = 1.0) -> list[tuple[str, float, float]]:
    """(구절, sherpa speed, 앞에 둘 무음 초). 머리의 "네"는 정본 속도, 나머지는 느리게. 구절 사이 0.1 s, 문장 사이 0.3 s.

    긴 문장을 한 번에 합성하면 발음이 뭉개진다(2026-09-02 실기 보고). 구절마다 따로 합성하고 사이에 호흡을 넣는다.
    """
    value = str(text or "").strip()
    out: list[tuple[str, float, float]] = []
    head = re.match(r"^네[,.]?\s+(.+)$", value)
    if head and normal_length_scale:
        out.append(("네.", round(1.0 / normal_length_scale, 4), 0.0))
        value = head.group(1).strip()
        first_pause = 0.2
    else:
        first_pause = 0.0
    pieces = [piece.strip() for piece in _PHRASE_SPLIT.split(value) if piece.strip()]
    previous_end_sentence = False
    for index, piece in enumerate(pieces):
        pause = first_pause if index == 0 else (0.3 if previous_end_sentence else 0.1)
        out.append((piece, slow_speed, pause))
        previous_end_sentence = piece[-1] in ".!?。！？"
    return out


def join_wavs(paths: list[tuple[Path, float]], out_wav: Path, *, lead_s: float = 0.2, tail_s: float = 0.15) -> Optional[Path]:
    """(경로, 앞 무음 초) 클립들을 무음으로 이어 한 WAV 로 만든다. 샘플레이트·채널이 다르면 None(따로 재생)."""
    import numpy as np

    rate = None
    chunks = []
    for path, pause in paths:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                return None
            if rate is None:
                rate = w.getframerate()
            elif w.getframerate() != rate:
                return None
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        chunks.append(np.zeros(int((lead_s if not chunks else pause) * rate), dtype=np.int16))
        chunks.append(data)
    if rate is None:
        return None
    chunks.append(np.zeros(int(tail_s * rate), dtype=np.int16))
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.concatenate(chunks).tobytes())
    return out_wav


class QuietMapClient(MapApiClient):
    """부작용 없는 명령은 서버를 거치지 않는다 — 화면에 지도 서버의 음성 이벤트 토스트("GPS 수신이 없어…", "상태를 확인했습니다")가
    데몬의 말과 겹쳐 뜨지 않게 한다(2026-09-02 실기). 응답 모양은 서버의 /api/voice/commands 와 같다.
    · find_nearest_water: GPS fix 가 없으면 서버가 거부하므로 같은 rejected 를 로컬에서 만든다(대본 경로로 이어진다)
    · status: 서버 쪽 동작이 없으므로 /api/device 스냅샷으로 accepted 를 만든다
    나머지(목적지 확인·야간 모드·저장 등)는 서버에 그대로 보낸다."""

    def command(self, action: str) -> dict[str, Any]:
        if action == "status":
            device = self.device()
            return {"action": action, "status": "accepted", "message": "현재 지도와 장치 상태를 확인했습니다.", "device": device}
        if action == "find_nearest_water":
            device = self.device()
            if (device.get("gps") or {}).get("fix") is not True:
                return {"action": action, "status": "rejected", "message": "현재 GPS 수신이 없어 가까운 수원 표식을 찾을 수 없습니다.", "device": device}
        return super().command(action)


def waypoint_payload(poi: dict[str, Any]) -> dict[str, Any]:
    """화면 터치(selectLiveDestination)와 같은 모양. 좌표는 지도 서버와 같은 표식 목록에서만 온다."""
    return {"action": "set", "kind": "destination", "lat": float(poi["lat"]), "lon": float(poi["lon"])}


def post_waypoint(base_url: str, payload: dict[str, Any], *, timeout_s: float = 2.0) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise MapApiError("웨이포인트 등록은 로컬 HTTP 주소만 사용할 수 있습니다")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/waypoints",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error")
        except Exception:  # noqa: BLE001
            detail = None
        raise MapApiError(str(detail or "지도 API 오류 %s" % exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise MapApiError("지도 서버 연결 실패: %s" % exc) from exc
    return body if isinstance(body, dict) else {}


# =============================================================
# 대화 상태기계
# =============================================================

class Dialogue:
    """분절된 발화를 받아 호출어 → 인사 → 명령 → 확인 흐름을 진행한다. 입출력은 전부 주입한다."""

    IDLE, AWAIT_COMMAND, AWAIT_CONFIRM, FOLLOWUP = "idle", "await_command", "await_confirm", "followup"

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        transcribe: Callable[[Segment, str], str],   # (segment, "wake"|"command") → 받아쓴 문장
        handle: Callable[[str, Optional[str]], Reply],
        speak: Callable[[str], None],
        log: Callable[[dict[str, Any]], None],
        clock: Optional[Callable[[], float]] = None,   # 말하고 난 뒤의 시각. 없으면 on_segment 의 now 를 쓴다
    ) -> None:
        self.cfg = cfg
        self.clock = clock
        self.wake_cfg = cfg.get("wake") or {}
        self.timeouts = cfg.get("timeouts") or {}
        self.greeting = str(cfg.get("greeting") or "네, 무엇을 도와드릴까요?")
        self.transcribe = transcribe
        self.handle = handle
        self.speak = speak
        self.log = log
        self.state = self.IDLE
        self.deadline: Optional[float] = None
        self.pending: Optional[str] = None
        self.sessions = 0           # idle 로 돌아온 세션 수 (--once 용)

    def _after_speech(self, now: float) -> float:
        """대기 창은 말이 끝난 시점부터 잰다. 인사말 합성·재생(수 초)이 8초 안에 포함되면 사용자가 말을 끝내기 전에 만료된다(2026-09-02 실기)."""
        if self.clock is None:
            return now
        try:
            return max(now, float(self.clock()))
        except Exception:  # noqa: BLE001
            return now

    def _enter(self, state: str, now: float) -> None:
        self.state = state
        wait = {
            self.AWAIT_COMMAND: self.timeouts.get("command_wait_s", 8.0),
            self.AWAIT_CONFIRM: self.timeouts.get("confirm_wait_s", 8.0),
            self.FOLLOWUP: self.timeouts.get("followup_wait_s", 6.0),
        }.get(state)
        self.deadline = None if wait is None else now + float(wait)
        if state == self.IDLE:
            self.pending = None
            self.sessions += 1

    def on_tick(self, now: float) -> None:
        if self.state != self.IDLE and self.deadline is not None and now >= self.deadline:
            self.log({"t": now, "event": "timeout", "state": self.state, "pending": self.pending})
            self._enter(self.IDLE, now)

    def on_segment(self, segment: Segment, now: float) -> None:
        self.on_tick(now)
        speech_s = segment.speech_s
        if self.state == self.IDLE:
            low = float(self.wake_cfg.get("min_speech_s", 0.25))
            high = float(self.wake_cfg.get("max_speech_s", 4.0))
            if not (low <= speech_s <= high):
                self.log({"t": now, "event": "ignored_length", "state": self.state, "speech_s": round(speech_s, 2)})
                return
            text = self.transcribe(segment, "wake")
            hit = match_wake(text, self.wake_cfg)
            if hit is None:
                self.log({"t": now, "event": "no_wake", "state": self.state, "text": text, "speech_s": round(speech_s, 2)})
                return
            self.log({"t": now, "event": "wake", "text": text, "variant": hit.variant, "remainder": hit.remainder})
            if hit.remainder:
                self._command(hit.remainder, now)
                return
            self.speak(self.greeting)
            self._enter(self.AWAIT_COMMAND, self._after_speech(now))
            return

        text = self.transcribe(segment, "command")
        normalized = normalize_utterance(text)
        confirmation = self.cfg.get("confirmation") or {}
        short_max = float(confirmation.get("short_utterance_max_s", 0.0) or 0.0)
        if self.state == self.AWAIT_CONFIRM and self.pending and 0 < speech_s <= short_max:
            # "어"·"응" 같은 한 음절은 whisper 가 빈 문자열이나 "아오"·"오" 로 내놓는다(2026-09-02 실기).
            # 확인 질문 직후의 짧은 응답은 부정어가 아니면 긍정으로 본다. 부정은 "아니"·"취소"처럼 말한다.
            verdict = yes_no(confirmation, normalized)
            if verdict is True or (verdict is None and len(_compact(normalized)) <= int(confirmation.get("short_utterance_max_chars", 2))):
                self.log({"t": now, "event": "short_confirm", "state": self.state, "text": text, "speech_s": round(speech_s, 2)})
                self._command("네", now)
                return
        if not normalized:
            self.log({"t": now, "event": "empty", "state": self.state, "speech_s": round(speech_s, 2)})
            return
        hit = match_wake(text, self.wake_cfg)
        if hit is not None:
            if hit.remainder:
                self._command(hit.remainder, now)
            else:
                self.speak(self.greeting)
                self._enter(self.AWAIT_COMMAND, self._after_speech(now))
            return
        self._command(text, now)

    def _command(self, text: str, now: float) -> None:
        reply = self.handle(text, self.pending)
        self.log({"t": now, "event": "command", "state": self.state, "text": text, "speech": reply.speech, "source": reply.source, "pending": reply.pending})
        self.speak(reply.speech)
        self.pending = reply.pending
        self._enter(self.AWAIT_CONFIRM if reply.pending else self.FOLLOWUP, self._after_speech(now))


# =============================================================
# 실행기
# =============================================================

def load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("wake_voice 설정 버전이 1 이 아닙니다: %s" % path)
    if not (payload.get("wake") or {}).get("prefix_variants"):
        raise ValueError("wake.prefix_variants 가 비어 있습니다: %s" % path)
    return payload


def write_wav(path: Path, samples, *, min_s: float = MIN_WHISPER_S) -> Path:
    import numpy as np

    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    need = int(min_s * RATE)
    if len(data) < need:
        data = np.concatenate([data, np.zeros(need - len(data), dtype=np.float32)])
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())
    return path


def _yes_no(router: Any, text: str) -> Optional[bool]:
    norm = normalize_utterance(text)
    for pattern in getattr(router, "no_patterns", []) or []:
        if re.search(pattern, norm):
            return False
    for pattern in getattr(router, "yes_patterns", []) or []:
        if re.search(pattern, norm):
            return True
    return None


class Runner:
    def __init__(self, args: argparse.Namespace, cfg: dict[str, Any]) -> None:
        self.args = args
        self.cfg = cfg
        self.script = cfg.get("script") or {}
        self.result_dir = Path(C.RESULT_DIR)
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = Path(args.log_jsonl) if args.log_jsonl else self.result_dir / "wake_events.jsonl"
        self.client = QuietMapClient(args.map_url, timeout_s=C.MAP_API_TIMEOUT_S)
        self.assistant = product_voice._build_assistant(self.client)
        extra_flags = [str(f) for f in cfg.get("stt_extra_flags") or []]
        self.stt = E.WhisperCppSTT(extra_flags=extra_flags) if args.stt == "whisper_cpp" else E.make_stt(args.stt)
        self.stt.load()
        self.lexicon = list(cfg.get("lexicon") or [])
        self.confirmation = dict(cfg.get("confirmation") or {})
        # 단계별 초기 프롬프트: 대기 중엔 호출어 어휘, 세션 중엔 시연 명령 어휘를 정본 프롬프트 뒤에 덧붙인다.
        # 이 프로세스의 whisper 호출에만 적용되고 제품 경로(product_voice)의 프롬프트는 그대로다.
        base_prompt = C.WHISPER_CPP_PROMPT
        self.prompts = {}
        for phase in ("wake", "command"):
            extra = str(cfg.get("stt_prompt_extra_%s" % phase) or "").strip()
            self.prompts[phase] = (base_prompt + " " + extra).strip() if extra else base_prompt
        tts_cfg = cfg.get("tts") or {}
        if tts_cfg.get("length_scale"):
            C.SHERPA_TTS_LENGTH_SCALE = float(tts_cfg["length_scale"])   # 이 프로세스의 sherpa 합성만 느리게
        lake = self.script.get("lake") or {}
        self.pois = load_water_pois(list(lake.get("poi_catalog") or []))
        self.scripted_poi: Optional[dict[str, Any]] = None
        self.strip_demo = bool(cfg.get("strip_demo_prefix", True))
        self.pipeline: Optional[TtsPipeline] = None
        if not args.no_tts:
            order = tuple(item.strip() for item in args.tts_order.split(",") if item.strip())
            self.pipeline = TtsPipeline(engine_order=order)
        self.ignore_until = 0.0
        self.now: Callable[[], float] = time.monotonic
        self._seg_index = 0

    # --- 입출력 --------------------------------------------------
    def log(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        tag = str(event.get("event", "")).upper()
        summary = {k: v for k, v in event.items() if k in ("text", "variant", "remainder", "speech", "source", "pending", "state", "speech_s")}
        print("[%s] %s" % (tag[:5].ljust(5), json.dumps(summary, ensure_ascii=False)), flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def transcribe(self, segment: Segment, phase: str = "wake") -> str:
        self._seg_index += 1
        path = write_wav(self.result_dir / ("wake_seg_%d.wav" % (self._seg_index % 8)), segment.samples)
        C.WHISPER_CPP_PROMPT = self.prompts.get(phase, self.prompts["wake"])
        started = time.monotonic()
        raw = self.stt.transcribe(path)
        elapsed = time.monotonic() - started
        text = apply_wake_lexicon(collapse_repeats(raw), self.lexicon)
        print("[STT ] %.2fs  %.2fs  %r%s" % (segment.speech_s, elapsed, raw, "" if text == raw else " → %r" % text), flush=True)
        return text

    def _phrases(self, text: str) -> list[tuple[str, float, float]]:
        tts_cfg = self.cfg.get("tts") or {}
        return speech_phrases(text, float(tts_cfg.get("normal_length_scale") or 0))

    def speak(self, text: str) -> None:
        print("[SAY ] %s" % text, flush=True)
        if self.pipeline is None:
            return
        clips: list[tuple[Path, float]] = []
        rates_ok = True
        for index, (phrase, speed, pause) in enumerate(self._phrases(text)):
            out = self.result_dir / ("wake_clip_%02d.wav" % index)
            C.SHERPA_TTS_SPEED = speed          # 캐시 키(voice_signature)에 speed 가 들어가므로 클립이 섞이지 않는다
            try:
                result = self.pipeline.synthesize(phrase, out)
            finally:
                C.SHERPA_TTS_SPEED = 1.0
            if result.engine != "sherpa":
                rates_ok = False
            clips.append((Path(result.path), pause))
        combined = None
        if rates_ok:
            tts_cfg = self.cfg.get("tts") or {}
            combined = join_wavs(clips, self.result_dir / "wake_say.wav", lead_s=float(tts_cfg.get("lead_silence_s", 0.2)))
        if combined is not None:
            print("[TTS ] %d구절 → 한 WAV %.2f s" % (len(clips), E.wav_duration_s(combined)), flush=True)
            if not self.args.no_play:
                E.play(combined)
        else:
            for path, _pause in clips:          # 엔진이 섞이면(폴백) 그냥 차례로 재생한다
                if not self.args.no_play:
                    E.play(path)
        guard = float((self.cfg.get("timeouts") or {}).get("after_speech_guard_s", 0.4))
        self.ignore_until = self.now() + guard

    def _reference_position(self, device: Optional[dict[str, Any]]) -> Optional[tuple[float, float, str]]:
        """거리 계산 기준점: GPS fix → (없으면) 설정의 no_fix_reference."""
        gps = (device or {}).get("gps") or {}
        try:
            if gps.get("fix") is True:
                return float(gps["lat"]), float(gps["lon"]), "gps_fix"
        except (KeyError, TypeError, ValueError):
            pass
        ref = (self.script.get("lake") or {}).get("no_fix_reference") or {}
        try:
            return float(ref["lat"]), float(ref["lon"]), "no_fix_reference"
        except (KeyError, TypeError, ValueError):
            return None

    def _lake_candidate(self, device: Optional[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], Optional[float], str]:
        reference = self._reference_position(device)
        if reference is None or not self.pois:
            return None, None, "none"
        found = nearest_poi(self.pois, reference[0], reference[1])
        if found is None:
            return None, None, "none"
        return found[0], found[1], reference[2]

    def _finish(self, reply: Reply) -> Reply:
        if self.strip_demo:
            reply = Reply(strip_demo_prefix(reply.speech), reply.pending, reply.source)
        return reply

    def handle(self, text: str, pending: Optional[str]) -> Reply:
        lake = self.script.get("lake") or {}
        verdict = yes_no(self.confirmation, text) if pending else None
        if verdict is None and pending:
            verdict = _yes_no(getattr(self.assistant, "router", None), text)
        if pending == "scripted":
            if verdict is True:
                poi, self.scripted_poi = self.scripted_poi, None
                placed = False
                if poi is not None and lake.get("set_destination_without_fix", True):
                    try:
                        post_waypoint(self.args.map_url, waypoint_payload(poi), timeout_s=C.MAP_API_TIMEOUT_S)
                        placed = True
                    except MapApiError as exc:
                        print("[WARN] 목적지 등록 실패: %s" % exc, file=sys.stderr, flush=True)
                print("[MAP ] 대본 확인 · 목적지 등록 %s · %s" % ("성공" if placed else "안 함", (poi or {}).get("name", "-")), flush=True)
                return self._finish(Reply(str(lake.get("confirmed") or "목적지가 설정되었습니다."), None, "scripted_confirm"))
            if verdict is False:
                self.scripted_poi = None
                return self._finish(Reply(str(lake.get("rejected") or "목적지 지정을 취소했습니다."), None, "scripted_reject"))
            self.scripted_poi = None
        elif pending == "map" and verdict is not None:
            text = "네" if verdict else "아니"      # 정본 라우터의 확인 패턴에 맞는 낱말로 넘긴다
        memory = E.mem_available_mb()
        if 0 <= memory < C.MEM_GATE_MB:
            print("[WARN] MemAvailable %d MB — 1 GB 게이트 아래에서 실행합니다" % memory, file=sys.stderr, flush=True)
        try:
            result = self.assistant.handle_text(text)
        except (MapApiError, RuntimeError, ValueError) as exc:
            return Reply("오프라인 지도 서버와 연결할 수 없습니다. 지도 화면의 연결 상태를 확인하세요. (%s)" % type(exc).__name__, None, "error")
        trace = dict(getattr(getattr(self.assistant, "router", None), "last_trace", None) or {})
        print("[ROUTE] %s · %s · map=%s · %s" % (result.decision.scenario_id, result.decision.source, result.decision.map_action or "-", trace.get("stage")), flush=True)
        distance_m = None
        if result.decision.map_action == "find_nearest_water":
            poi, distance_m, basis = self._lake_candidate(result.device)
            self.scripted_poi = poi
            print("[LAKE] 표식 %s · 거리 %s m · 기준 %s" % ((poi or {}).get("name", "-"), "-" if distance_m is None else int(distance_m), basis), flush=True)
        return self._finish(compose_reply(result, self.script, distance_m=distance_m))

    def warm_tts(self) -> None:
        if self.pipeline is None:
            return
        texts = [str(self.cfg.get("greeting") or "")]
        lake = self.script.get("lake") or {}
        texts += [str(lake.get(k) or "") for k in ("without_distance", "confirmed", "rejected")]
        if lake.get("with_distance") and lake.get("distance_floor_m"):
            texts.append(str(lake["with_distance"]).format(distance_m=int(lake["distance_floor_m"])))
        out = self.result_dir / "wake_warm.wav"
        for text in [t for t in texts if t]:
            for phrase, speed, _pause in self._phrases(text):
                C.SHERPA_TTS_SPEED = speed
                try:
                    self.pipeline.synthesize(phrase, out)
                finally:
                    C.SHERPA_TTS_SPEED = 1.0

    # --- 루프 ------------------------------------------------------
    def run(self) -> int:
        seg_kw = dict(
            min_silence_s=float(self.args.min_silence_s),
            min_speech_s=0.2,
            max_speech_s=float(self.args.max_speech_s),
        )
        segmenter = make_segmenter(self.args.segmenter, self.args.vad_model, **seg_kw)
        dialogue = Dialogue(self.cfg, transcribe=self.transcribe, handle=self.handle, speak=self.speak, log=self.log, clock=lambda: self.now())
        lock_wait = float(self.args.lock_wait_s)

        def process(segment: Segment, now: float) -> None:
            if segment.start_s < self.ignore_until:
                self.log({"t": now, "event": "ignored_during_speech", "state": dialogue.state, "speech_s": round(segment.speech_s, 2)})
                return
            try:
                with exclusive_pipeline(timeout_s=lock_wait if dialogue.state == Dialogue.IDLE else max(lock_wait, 10.0)):
                    dialogue.on_segment(segment, now)
            except RuntimeError as exc:
                self.log({"t": now, "event": "lock_busy", "state": dialogue.state, "error": str(exc)})

        if self.args.input_wavs:
            source = FileSource(self.args.input_wavs, lead_s=self.args.lead_s, gap_s=self.args.gap_s, tail_s=self.args.tail_s)
            self.now = source.now
            print("[WAKE] 파일 입력 %d개 · 분절 %s · 호출어 대기" % (len(self.args.input_wavs), segmenter.name), flush=True)
            for chunk in source.frames():
                for segment in segmenter.feed(chunk):
                    process(segment, source.now())
                dialogue.on_tick(source.now())
                if self.args.once and dialogue.sessions >= 1 and dialogue.state == Dialogue.IDLE:
                    break
            for segment in segmenter.flush():
                process(segment, source.now())
            dialogue.on_tick(source.now() + 60.0)
            return 0

        source = MicSource(self.args.mic_device)
        self.now = segmenter.now_s
        pending_segments: "queue.Queue[Segment]" = queue.Queue()
        failure: list[BaseException] = []

        level_every = float(self.args.level_log_s)
        level = {"peak": 0.0, "since": time.monotonic()}

        def listen() -> None:
            try:
                for chunk in source.frames():
                    for segment in segmenter.feed(chunk):
                        pending_segments.put(segment)
                    if level_every > 0:
                        peak = float(abs(chunk).max()) if len(chunk) else 0.0
                        level["peak"] = max(level["peak"], peak)
                        if time.monotonic() - level["since"] >= level_every:
                            # 마이크가 살아 있는지 보여 주는 줄. peak 가 계속 0 이면 장치·게인을 의심한다
                            print("[MIC ] %.0fs 동안 peak %d/32767 · 스트림 %.0fs" % (level_every, int(level["peak"] * 32767), segmenter.now_s()), flush=True)
                            level["peak"], level["since"] = 0.0, time.monotonic()
            except BaseException as exc:  # noqa: BLE001 — 주 스레드에서 같은 오류로 끝낸다
                failure.append(exc)

        thread = threading.Thread(target=listen, name="ogtech-wake-listener", daemon=True)
        thread.start()
        print("[WAKE] 마이크 %s · 분절 %s · 호출어 대기 (%s)" % (self.args.mic_device, segmenter.name, "무음 검증" if self.args.no_play else "스피커 출력"), flush=True)
        try:
            while not failure:
                try:
                    segment = pending_segments.get(timeout=0.25)
                except queue.Empty:
                    dialogue.on_tick(segmenter.now_s())
                    if self.args.once and dialogue.sessions >= 1 and dialogue.state == Dialogue.IDLE:
                        return 0
                    continue
                process(segment, segmenter.now_s())
                if self.args.once and dialogue.sessions >= 1 and dialogue.state == Dialogue.IDLE:
                    return 0
        finally:
            source.close()
        raise failure[0]


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OGTECH '오지야' 호출어 데몬")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="wake_voice.json 경로")
    parser.add_argument("--map-url", default=C.MAP_API_URL)
    parser.add_argument("--stt", default=C.STT_ENGINE)
    parser.add_argument("--tts-order", default=",".join(C.TTS_ENGINE_ORDER))
    parser.add_argument("--mic-device", default=C.MIC_DEVICE)
    parser.add_argument("--vad-model", default=C.VAD_ONNX_MODEL, help="Silero VAD ONNX 경로")
    parser.add_argument("--segmenter", choices=("auto", "silero", "energy"), default="auto")
    parser.add_argument("--min-silence-s", type=float, default=0.5, help="이만큼 조용하면 발화 종료")
    parser.add_argument("--max-speech-s", type=float, default=8.0, help="한 발화 최대 길이")
    parser.add_argument("--lock-wait-s", type=float, default=0.5, help="idle 에서 파이프라인 잠금을 기다리는 시간")
    parser.add_argument("--no-tts", action="store_true", help="문장 확정까지만 (합성·재생 없음)")
    parser.add_argument(
        "--no-play",
        action="store_true",
        default=os.environ.get("OGTECH_WAKE_NO_PLAY", "").strip().lower() in {"1", "true", "yes"},
        help="WAV 는 만들되 스피커로 내지 않음 (환경변수 OGTECH_WAKE_NO_PLAY=1 과 같음)",
    )
    parser.add_argument("--once", action="store_true", help="세션 하나가 idle 로 돌아오면 종료")
    parser.add_argument("--input-wavs", nargs="+", help="마이크 대신 16 kHz WAV 들을 순서대로 흘린다")
    parser.add_argument("--lead-s", type=float, default=1.0)
    parser.add_argument("--gap-s", type=float, default=1.5)
    parser.add_argument("--tail-s", type=float, default=12.0)
    parser.add_argument("--log-jsonl", help="이벤트 JSONL 경로 (기본 RESULT_DIR/wake_events.jsonl)")
    parser.add_argument("--level-log-s", type=float, default=30.0, help="마이크 peak 를 이 주기(초)로 찍는다. 0 이면 끔")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        cfg = load_config(Path(args.config))
        runner = Runner(args, cfg)
        runner.warm_tts()
        return runner.run()
    except KeyboardInterrupt:
        return 0
    except (MapApiError, RuntimeError, FileNotFoundError, ValueError, OSError) as exc:
        print("호출어 데몬 실행 실패: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
