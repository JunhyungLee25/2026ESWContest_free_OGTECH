"""화면에 뜬 문구를 같은 목소리로 읽어 주는 오프라인 TTS.

sherpa-onnx VITS(mimic3 ko_KO kss_low, 여성 단일 화자)를 인프로세스로 돌린다.
브라우저 speechSynthesis 는 쓰지 않는다 — Jetson Firefox 에서는 espeak 남성
기계음으로 떨어져 제품 음성과 목소리가 갈린다.

발화 파라미터는 Co-LLM 과 같은 값을 쓴다(2026-08-30 사용자 청취 기준 0.9배속).
sherpa-onnx VITS 는 speed != 1.0 이면 length_scale 을 1/speed 로 "대체"하므로
속도는 length_scale 한 곳으로만 조절하고 speed 는 1.0 을 유지한다.

크기도 Co-LLM 과 맞춘다. tts_pipeline.normalize_pcm16 과 같은 기준으로 피크를 0.82 로
올린다 — 이걸 빼면 합성 음성이 녹음 클립(assets/audio/*.wav)보다 4 dB 가량 작아서
같은 화면에서 소리 크기가 널뛴다(합성 원본 피크 실측 0.51, 녹음 0.82).
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import threading
import wave


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


SPEECH_DIR = _env_path(
    "OGTECH_SHERPA_TTS_DIR",
    Path.home() / "safeaid_ai" / "tts" / "sherpa" / "vits-mimic3-ko_KO-kss_low",
)
SPEECH_LENGTH_SCALE = _env_float("OGTECH_SHERPA_TTS_LENGTH_SCALE", 1.22)
SPEECH_NOISE_SCALE = _env_float("OGTECH_SHERPA_TTS_NOISE_SCALE", 0.4)
SPEECH_NOISE_SCALE_W = _env_float("OGTECH_SHERPA_TTS_NOISE_SCALE_W", 0.6)
SPEECH_SID = int(_env_float("OGTECH_SHERPA_TTS_SID", 0))
SPEECH_THREADS = int(_env_float("OGTECH_SHERPA_TTS_THREADS", 4))
# Co-LLM config.TTS_TARGET_PEAK_RATIO / TTS_MAX_GAIN 과 같은 값.
SPEECH_TARGET_PEAK_RATIO = _env_float("OGTECH_TTS_TARGET_PEAK_RATIO", 0.82)
SPEECH_MAX_GAIN = _env_float("OGTECH_TTS_MAX_GAIN", 4.0)

# 화면 문구는 몇십 개로 정해져 있어 캐시가 곧 전부 채워진다. 같은 문장을 다시
# 읽을 때는 합성하지 않는다(Jetson 합성 0.6~1.6 s).
SPEECH_CACHE_MAX = 96
SPEECH_TEXT_MAX = 220


class SpeechUnavailable(RuntimeError):
    """모델이나 패키지가 없어 합성할 수 없다. 화면은 글자만 보여 주면 된다."""


class SpeechService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engine = None
        self._error: str | None = None
        self._cache: dict[str, bytes] = {}
        self._order: list[str] = []

    @property
    def model_dir(self) -> Path:
        return SPEECH_DIR

    def _load(self):
        if self._engine is not None:
            return self._engine
        if self._error is not None:
            raise SpeechUnavailable(self._error)
        model_dir = SPEECH_DIR
        candidates = sorted(model_dir.glob("*.onnx")) if model_dir.is_dir() else []
        tokens = model_dir / "tokens.txt"
        if not candidates or not tokens.exists():
            self._error = f"sherpa-onnx TTS 모델이 없습니다: {model_dir}"
            raise SpeechUnavailable(self._error)
        try:
            import sherpa_onnx
        except ImportError as exc:
            self._error = f"sherpa_onnx 패키지가 없습니다: {exc}"
            raise SpeechUnavailable(self._error) from exc
        data_dir = model_dir / "espeak-ng-data"
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(candidates[0]),
                    tokens=str(tokens),
                    data_dir=str(data_dir) if data_dir.is_dir() else "",
                    noise_scale=SPEECH_NOISE_SCALE,
                    noise_scale_w=SPEECH_NOISE_SCALE_W,
                    length_scale=SPEECH_LENGTH_SCALE,
                ),
                num_threads=SPEECH_THREADS,
                provider="cpu",
            ),
        )
        if not config.validate():
            self._error = f"sherpa-onnx TTS 설정 검증 실패: {model_dir}"
            raise SpeechUnavailable(self._error)
        self._engine = sherpa_onnx.OfflineTts(config)
        return self._engine

    def synthesize(self, text: str) -> bytes:
        cleaned = " ".join((text or "").split())[:SPEECH_TEXT_MAX]
        if not cleaned:
            raise SpeechUnavailable("읽을 문구가 없습니다")
        key = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            engine = self._load()
            audio = engine.generate(cleaned, sid=SPEECH_SID, speed=1.0)
            samples = getattr(audio, "samples", [])
            if len(samples) == 0:
                raise SpeechUnavailable(f"빈 오디오: {cleaned[:30]}")
            data = _pcm16_wav(samples, audio.sample_rate)
            self._cache[key] = data
            self._order.append(key)
            while len(self._order) > SPEECH_CACHE_MAX:
                self._cache.pop(self._order.pop(0), None)
            return data


def _peak_gain(samples) -> float:
    """녹음 클립과 같은 피크로 올리는 배율. 무음이면 1.0 을 돌려준다."""
    peak = 0.0
    for value in samples:
        magnitude = abs(float(value))
        if magnitude > peak:
            peak = magnitude
    if peak <= 0.0:
        return 1.0
    return min(SPEECH_MAX_GAIN, SPEECH_TARGET_PEAK_RATIO / peak)


def _pcm16_wav(samples, sample_rate: int) -> bytes:
    gain = _peak_gain(samples)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        frames = bytearray()
        for value in samples:
            amplified = float(value) * gain
            clipped = -1.0 if amplified < -1.0 else (1.0 if amplified > 1.0 else amplified)
            frames += int(clipped * 32767.0).to_bytes(2, "little", signed=True)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


# 화면에 자주 뜨는 고정 문구. 서버가 뜨자마자 미리 합성해 캐시에 넣어 두면
# 시연 중 첫 소리가 6초 늦는 일이 없다(Jetson 실측: 모델 로드 포함 5.9 s → 0.18 s).
WARMUP_PHRASES = (
    "목적지에 도착하였습니다.",
    "Base Camp에 도착하였습니다.",
    "베이스캠프가 등록되었습니다.",
    "베이스캠프 복귀 경로가 설정되었습니다.",
    "베이스캠프 귀환 경로를 불러왔습니다.",
    "현재 위치를 베이스캠프로 저장했습니다.",
    "현재 위치를 체크포인트로 저장했습니다.",
    "지도에서 목적지를 터치하세요.",
    "목적지를 지정했습니다.",
    "목적지 지정을 취소했습니다.",
    "야간 모드가 활성화되었습니다.",
    "야간 모드가 해제되었습니다.",
    "경로를 벗어났습니다. 현재 위치와 경로를 확인하세요.",
    "일몰 시간이 지났습니다. 귀환 권고 시각과 베이스캠프 경로를 확인하세요.",
)


def warmup(service: "SpeechService") -> None:
    """백그라운드에서 고정 문구를 미리 합성한다. 실패는 조용히 넘긴다."""
    def run() -> None:
        for phrase in WARMUP_PHRASES:
            try:
                service.synthesize(phrase)
            except Exception:
                return  # 모델이 없으면 더 시도하지 않는다
    thread = threading.Thread(target=run, name="tts-warmup", daemon=True)
    thread.start()
    return thread
