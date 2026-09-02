#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""오프라인 한국어 TTS 품질·폴백·캐시 파이프라인."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
import tempfile
import wave
from typing import Any, Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402


def _numpy_pcm16(raw: bytes):
    """numpy가 있으면 PCM 연산을 C 루프로 넘긴다. 미설치 환경은 기존 array 경로를 쓴다."""
    try:
        import numpy as np
    except ImportError:
        return None
    return np.frombuffer(raw, dtype="<i2").astype(np.int32)


@dataclass(frozen=True)
class WavMetrics:
    sample_rate: int
    channels: int
    duration_s: float
    peak_ratio: float
    rms_ratio: float
    clipped_ratio: float


@dataclass(frozen=True)
class SynthesisResult:
    path: Path
    engine: str
    normalized_text: str
    metrics: WavMetrics
    cached: bool
    degraded: bool
    errors: tuple[str, ...]


def normalize_tts_text(text: str) -> str:
    """한국어 엔진이 영문 약어·기호·단위를 문자 이름으로 읽지 않게 바꾼다."""
    value = str(text or "").strip()
    replacements = (
        (r"\bBase\s*Camp\b", "베이스캠프"),
        (r"\bGPS\b", "지피에스"),
        (r"\bCO\b", "일산화탄소"),
        (r"\bppm\b", "피피엠"),
        (r"°\s*C", "도"),
        (r"±", "플러스마이너스 "),
        (r"(\d+(?:\.\d+)?)\s*%", r"\1퍼센트"),
        (r"(\d+(?:\.\d+)?)\s*m\b", r"\1미터"),
        (r"\b(\d{1,2}):(\d{2})\b", r"\1시 \2분"),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    value = re.sub(r"[*_#<>`]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        raise ValueError("합성할 문장이 비어 있습니다")
    if len(value) > C.TTS_MAX_TEXT_CHARS:
        raise ValueError(f"TTS 문장은 {C.TTS_MAX_TEXT_CHARS}자 이하여야 합니다")
    return value


def split_tts_sentences(text: str) -> tuple[str, ...]:
    """검수 카드 문장을 TTS 재생 단위로 나누되 숫자 소수점은 건드리지 않는다."""
    value = str(text or "").strip()
    if not value:
        raise ValueError("합성할 문장이 비어 있습니다")
    parts = tuple(
        item.strip()
        for item in re.split(r"(?<=[.!?。！？])\s+", value)
        if item.strip()
    )
    if not parts:
        raise ValueError("합성할 문장이 비어 있습니다")
    return parts


def inspect_wav(path: str | Path) -> WavMetrics:
    wav_path = Path(path)
    with wave.open(str(wav_path), "rb") as stream:
        channels = stream.getnchannels()
        sample_rate = stream.getframerate()
        sample_width = stream.getsampwidth()
        frames = stream.getnframes()
        compression = stream.getcomptype()
        raw = stream.readframes(frames)
    if sample_width != 2 or compression != "NONE":
        raise RuntimeError("TTS 출력은 16비트 PCM WAV여야 합니다")
    if channels not in {1, 2} or not 8_000 <= sample_rate <= 96_000:
        raise RuntimeError("TTS WAV 채널 또는 샘플레이트가 허용 범위를 벗어났습니다")
    numpy_samples = _numpy_pcm16(raw)
    samples = None
    if numpy_samples is None:
        samples = array("h")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
    sample_count = len(numpy_samples) if numpy_samples is not None else len(samples)
    if sample_count == 0 or frames <= 0:
        raise RuntimeError("TTS WAV에 오디오 프레임이 없습니다")
    if numpy_samples is not None:
        import numpy as np

        absolute = np.abs(numpy_samples)
        peak = int(absolute.max())
        rms = float(np.sqrt(np.mean(numpy_samples.astype(np.float64) ** 2)))
        clipped = int(np.count_nonzero(absolute >= 32760))
    else:
        peak = max(abs(int(sample)) for sample in samples)
        square_sum = sum(int(sample) * int(sample) for sample in samples)
        rms = math.sqrt(square_sum / sample_count)
        clipped = sum(1 for sample in samples if abs(int(sample)) >= 32760)
    duration = frames / float(sample_rate)
    metrics = WavMetrics(
        sample_rate=sample_rate,
        channels=channels,
        duration_s=duration,
        peak_ratio=peak / 32768.0,
        rms_ratio=rms / 32768.0,
        clipped_ratio=clipped / sample_count,
    )
    if not C.TTS_MIN_DURATION_S <= duration <= C.TTS_MAX_DURATION_S:
        raise RuntimeError("TTS WAV 길이가 허용 범위를 벗어났습니다")
    if metrics.peak_ratio < C.TTS_MIN_PEAK_RATIO or metrics.rms_ratio < C.TTS_MIN_RMS_RATIO:
        raise RuntimeError("TTS WAV가 무음에 가깝습니다")
    if metrics.clipped_ratio > C.TTS_MAX_CLIPPED_RATIO:
        raise RuntimeError("TTS WAV의 클리핑 비율이 너무 높습니다")
    return metrics


def normalize_pcm16(source: str | Path, destination: str | Path) -> WavMetrics:
    """생성 음성을 피크 기준으로 완만하게 정규화해 USB 스피커 편차를 줄인다."""
    source_path = Path(source)
    destination_path = Path(destination)
    with wave.open(str(source_path), "rb") as stream:
        params = stream.getparams()
        raw = stream.readframes(stream.getnframes())
    if params.sampwidth != 2 or params.comptype != "NONE":
        raise RuntimeError("정규화는 16비트 PCM WAV만 지원합니다")
    numpy_samples = _numpy_pcm16(raw)
    samples = None
    if numpy_samples is None:
        samples = array("h")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        peak = max((abs(int(sample)) for sample in samples), default=0)
    else:
        import numpy as np

        peak = int(np.abs(numpy_samples).max()) if len(numpy_samples) else 0
    if peak <= 0:
        raise RuntimeError("TTS WAV가 무음입니다")
    target_peak = int(32767 * C.TTS_TARGET_PEAK_RATIO)
    gain = min(C.TTS_MAX_GAIN, target_peak / peak)
    if numpy_samples is not None:
        import numpy as np

        normalized_bytes = np.clip(
            np.rint(numpy_samples.astype(np.float32) * gain), -32768, 32767
        ).astype("<i2").tobytes()
    else:
        normalized = array("h", (
            max(-32768, min(32767, int(round(int(sample) * gain)))) for sample in samples
        ))
        if sys.byteorder != "little":
            normalized.byteswap()
        normalized_bytes = normalized.tobytes()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(f".{destination_path.name}.normalize.tmp")
    with wave.open(str(temporary), "wb") as stream:
        stream.setparams(params)
        stream.writeframes(normalized_bytes)
    temporary.replace(destination_path)
    return inspect_wav(destination_path)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.copy.tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)


class TtsPipeline:
    """고정 녹음 → MeloTTS → Piper → espeak 순으로 한 번씩만 시도한다."""

    def __init__(
        self,
        *,
        engine_order: tuple[str, ...] | None = None,
        engine_factory: Callable[[str], Any] | None = None,
        manifest_path: str | Path = C.TTS_FIXED_AUDIO_MANIFEST,
        cache_dir: str | Path = C.TTS_CACHE_DIR,
        use_cache: bool = True,
    ) -> None:
        self.engine_order = tuple(engine_order or C.TTS_ENGINE_ORDER)
        if not self.engine_order:
            raise ValueError("TTS 엔진 우선순위가 비어 있습니다")
        self.engine_factory = engine_factory
        self.manifest_path = Path(manifest_path)
        self.cache_dir = Path(cache_dir)
        self.use_cache = use_cache
        self.fallback_audio: Path | None = None
        self.fallback_text = "음성 합성에 실패했습니다. 화면의 검수된 안내를 확인하세요."
        self.fixed_audio = self._load_manifest()

    def _load_manifest(self) -> dict[str, Path]:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        clips = payload.get("clips") if payload.get("version") == 1 else None
        if not isinstance(clips, dict):
            return {}
        fallback = payload.get("fallback")
        if isinstance(fallback, dict):
            fallback_path = (self.manifest_path.parent / str(fallback.get("path") or "")).resolve()
            fallback_text = str(fallback.get("spoken_text") or "").strip()
            if fallback_path.is_file() and fallback_text:
                self.fallback_audio = fallback_path
                self.fallback_text = fallback_text
        result: dict[str, Path] = {}
        for text, raw_path in clips.items():
            path = (self.manifest_path.parent / str(raw_path)).resolve()
            if path.is_file():
                result[str(text).strip()] = path
        return result

    def fixed_clip_for(self, text: str) -> Path | None:
        """고정 WAV 조회. 데모 접두(config.DEMO_SPEECH_PREFIX)가 붙은 문장은 뗀 키로도 찾는다."""
        original = str(text or "").strip()
        fixed = self.fixed_audio.get(original)
        if fixed is None and original.startswith(C.DEMO_SPEECH_PREFIX):
            fixed = self.fixed_audio.get(original[len(C.DEMO_SPEECH_PREFIX):].strip())
        return fixed

    def _factory(self, name: str) -> Any:
        if self.engine_factory is not None:
            return self.engine_factory(name)
        from engines import make_tts

        return make_tts(name)

    @staticmethod
    def voice_signature() -> str:
        """목소리를 바꾸는 합성 파라미터. 캐시 키에 넣어 속도·화자 변경 뒤 옛 클립이 재생되지 않게 한다."""
        return "sherpa sid=%s speed=%s ls=%s ns=%s nsw=%s" % (
            C.SHERPA_TTS_SID, C.SHERPA_TTS_SPEED, C.SHERPA_TTS_LENGTH_SCALE,
            C.SHERPA_TTS_NOISE_SCALE, C.SHERPA_TTS_NOISE_SCALE_W,
        )

    def _cache_paths(self, normalized_text: str) -> tuple[Path, Path]:
        identity = "|".join(self.engine_order) + "\n" + self.voice_signature() + "\n" + normalized_text
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.wav", self.cache_dir / f"{key}.json"

    def synthesize(self, text: str, out_wav: str | Path) -> SynthesisResult:
        original = str(text or "").strip()
        normalized = normalize_tts_text(original)
        destination = Path(out_wav)

        fixed = self.fixed_clip_for(original)
        if fixed is not None:
            metrics = inspect_wav(fixed)
            _atomic_copy(fixed, destination)
            return SynthesisResult(
                destination, "fixed", normalized, metrics, False, False, ()
            )

        cache_wav, cache_meta = self._cache_paths(normalized)
        if self.use_cache and cache_wav.is_file() and cache_meta.is_file():
            try:
                metadata = json.loads(cache_meta.read_text(encoding="utf-8"))
                metrics = inspect_wav(cache_wav)
                _atomic_copy(cache_wav, destination)
                return SynthesisResult(
                    destination,
                    str(metadata["engine"]),
                    normalized,
                    metrics,
                    True,
                    bool(metadata.get("degraded")),
                    (),
                )
            except (OSError, KeyError, json.JSONDecodeError, RuntimeError):
                pass

        errors: list[str] = []
        destination.parent.mkdir(parents=True, exist_ok=True)
        for index, engine_name in enumerate(self.engine_order):
            temporary_handle = tempfile.NamedTemporaryFile(
                prefix="ogtech-tts-", suffix=".wav", dir=destination.parent, delete=False
            )
            temporary_path = Path(temporary_handle.name)
            temporary_handle.close()
            temporary_path.unlink(missing_ok=True)
            try:
                with self._factory(engine_name) as engine:
                    engine.synth(normalized, temporary_path)
                inspect_wav(temporary_path)
                metrics = normalize_pcm16(temporary_path, destination)
                degraded = index > 0 or engine_name == "espeak"
                if self.use_cache:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    _atomic_copy(destination, cache_wav)
                    cache_meta.write_text(
                        json.dumps(
                            {"version": 1, "engine": engine_name, "degraded": degraded},
                            ensure_ascii=False,
                            indent=2,
                        ) + "\n",
                        encoding="utf-8",
                    )
                return SynthesisResult(
                    destination,
                    engine_name,
                    normalized,
                    metrics,
                    False,
                    degraded,
                    tuple(errors),
                )
            except (Exception, SystemExit) as exc:  # 다음 엔진으로 한 번만 폴백한다.
                # SystemExit: engines.make_tts 의 엔진 이름 오타. 데몬을 죽이지 않고 폴백한다.
                errors.append(f"{engine_name}: {exc}")
            finally:
                temporary_path.unlink(missing_ok=True)
        if self.fallback_audio is not None:
            metrics = inspect_wav(self.fallback_audio)
            _atomic_copy(self.fallback_audio, destination)
            return SynthesisResult(
                destination,
                "fixed_fallback",
                self.fallback_text,
                metrics,
                False,
                True,
                tuple(errors),
            )
        raise RuntimeError("모든 TTS 엔진이 실패했습니다: " + " | ".join(errors))

    def synthesize_sentences(
        self, text: str, out_wav: str | Path
    ) -> Iterator[SynthesisResult]:
        """첫 문장부터 독립 WAV로 만들어 재생 큐가 즉시 소비할 수 있게 한다."""
        destination = Path(out_wav)
        sentences = split_tts_sentences(text)
        for index, sentence in enumerate(sentences, start=1):
            segment_path = (
                destination
                if len(sentences) == 1
                else destination.with_name(
                    f"{destination.stem}.part{index:02d}{destination.suffix or '.wav'}"
                )
            )
            result = self.synthesize(sentence, segment_path)
            yield result
            if result.engine == "fixed_fallback":
                return
