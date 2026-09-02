# -*- coding: utf-8 -*-
"""STT/TTS 엔진 어댑터.

엔진마다 인터페이스 3개만 맞춥니다.

    with make_stt() as stt:
        text = stt.transcribe(wav_path)

    with make_tts() as tts:
        tts.synth(text, out_wav)

`with` 블록을 벗어나면 언로드됩니다.
../docs/00_frozen_decisions.md §2 규칙: STT와 TTS를 동시에 메모리에 올리지 않습니다.

CLI 서브프로세스 방식(whisper.cpp / piper / espeak-ng)은 프로세스가 끝나면
메모리가 커널에 반납되므로 이 규칙을 공짜로 지킵니다. 대신 호출마다 모델을
다시 읽습니다(첫 호출은 디스크, 이후는 페이지 캐시).

파이썬 인프로세스 방식(sherpa-onnx / faster-whisper / MeloTTS)은 unload() 에서
참조를 끊고 gc 를 직접 돌립니다.
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402


# =============================================================
# 공용 유틸
# =============================================================

def mem_available_mb():
    """MemAvailable(MB). ../docs/00_frozen_decisions.md §2 게이트는 1 GB 입니다."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return -1


_VAD_WARNED = [False]


def whisper_flags(flags=None, vad_model=None):
    """동결 플래그를 돌려주되, VAD 모델이 없으면 `--vad` 를 빼고 경고합니다.

    ../docs/00_frozen_decisions.md §5의 최종 선정 구성은 `--vad -vm ggml-silero-v5.1.2.bin`
    입니다(측정 근거: docs/measurements.csv `base_cpu_vad`). 그런데 VAD 모델은
    whisper.cpp 본체와 따로 내려받는 파일이라 없을 수 있고, 없는 채로 `--vad` 를
    넘기면 whisper-cli 가 모델 로드에 실패해 그대로 죽습니다.

    그래서 기본값은 선정 구성 그대로 두되, 파일이 실제로 있을 때만 넘깁니다.
    빠지면 후보 B(`-ac 450 -nf`)로 내려가며 중앙값은 비슷하지만 최댓값이
    3,363 ms 까지 튑니다 `[실측]`. 조용히 내려가면 안 되므로 한 번 경고합니다.
    """
    flags = list(C.WHISPER_CPP_FLAGS if flags is None else flags)
    vad_model = C.WHISPER_VAD_MODEL if vad_model is None else vad_model
    if "--vad" not in flags:
        return flags
    if vad_model and os.path.exists(vad_model):
        return flags

    if not _VAD_WARNED[0]:
        _VAD_WARNED[0] = True
        sys.stderr.write(
            "WARN: VAD 모델이 없어 --vad 를 뺍니다: %s\n"
            "      최종 선정 구성(base_cpu_vad)이 아니라 후보 B로 동작합니다. "
            "최댓값이 경로 B 예산 2.0초를 넘길 수 있습니다 `[실측]`.\n"
            "      복구: cd ~/ogtech_ai/stt/whisper.cpp && "
            "bash ./models/download-vad-model.sh silero-v5.1.2\n" % vad_model
        )

    out = []
    drop_value = False
    for item in flags:
        if drop_value:
            drop_value = False
            continue
        if item == "--vad":
            continue
        if item in ("-vm", "--vad-model"):
            drop_value = True
            continue
        out.append(item)
    return out


def _run(cmd, timeout_s, what, **kwargs):
    """subprocess.run + timeout. 초과는 RuntimeError 로 바꿔 호출자의 기존 처리에 태웁니다.

    run() 은 TimeoutExpired 를 올리기 전에 자식을 kill 하므로 고아 프로세스는 남지 않습니다.
    """
    try:
        return subprocess.run(cmd, timeout=timeout_s, **kwargs)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "%s 가 %.0f초 안에 끝나지 않아 중단했습니다" % (what, timeout_s)
        ) from None


def record(out_wav, seconds=None, device=None):
    """arecord 로 16 kHz 모노 wav 를 녹음합니다."""
    seconds = C.REC_SECONDS if seconds is None else seconds
    device = C.MIC_DEVICE if device is None else device
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "arecord", "-D", device,
        "-f", "S16_LE",
        "-r", str(C.REC_RATE),
        "-c", str(C.REC_CHANNELS),
        "-d", str(seconds),
        "-q",
        str(out_wav),
    ]
    _run(cmd, float(seconds) + C.SUBPROCESS_TIMEOUT_PLAY_EXTRA_S, "arecord", check=True)
    return str(out_wav)


def play(wav, device=None):
    device = C.SPK_DEVICE if device is None else device
    try:
        duration = wav_duration_s(wav)
    except (OSError, wave.Error, EOFError, ZeroDivisionError):
        duration = C.SUBPROCESS_TIMEOUT_PLAY_FALLBACK_S
    _run(
        ["aplay", "-D", device, "-q", str(wav)],
        duration + C.SUBPROCESS_TIMEOUT_PLAY_EXTRA_S, "aplay", check=True,
    )


def wav_duration_s(wav):
    with wave.open(str(wav), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def read_wav_float(path):
    """(sample_rate, float32 numpy 모노 배열) 을 돌려줍니다. sherpa-onnx 용."""
    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise RuntimeError("16bit PCM wav 만 지원합니다: %s" % path)
        rate = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(w.getnframes())

    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return rate, data


class _Engine(object):
    """load/unload 를 with 로 강제하는 최소 베이스."""

    name = "base"

    def load(self):
        pass

    def unload(self):
        pass

    def __enter__(self):
        self.load()
        return self

    def __exit__(self, *exc):
        self.unload()
        gc.collect()
        return False


# =============================================================
# STT
# =============================================================

class WhisperCppSTT(_Engine):
    """1안. CLI 서브프로세스. 언로드가 자동입니다.

    extra_flags 는 동결 플래그 뒤에 덧붙는 호출자 전용 옵션입니다(호출어 데몬이 config/wake_voice.json 의 stt_extra_flags 를 넘긴다).
    제품 경로(product_voice/physical_voice)는 비워 두어 동결 구성 그대로 돕니다.
    """

    name = "whisper_cpp"

    def __init__(self, extra_flags=None):
        self.extra_flags = [str(f) for f in (extra_flags or [])]

    def load(self):
        if not os.path.exists(C.WHISPER_CPP_BIN):
            raise RuntimeError(
                "whisper.cpp 바이너리가 없습니다: %s\n"
                "  구버전은 이름이 whisper-cli 가 아니라 main 입니다. "
                "config.py 의 WHISPER_CPP_BIN 을 고치세요." % C.WHISPER_CPP_BIN
            )
        if not os.path.exists(C.WHISPER_CPP_MODEL):
            raise RuntimeError("모델이 없습니다: %s" % C.WHISPER_CPP_MODEL)

    def transcribe(self, wav):
        cmd = [
            C.WHISPER_CPP_BIN,
            "-m", C.WHISPER_CPP_MODEL,
            "-f", str(wav),
            "-l", C.WHISPER_CPP_LANG,
            "-t", str(C.WHISPER_CPP_THREADS),
            # -ng: GPU 미사용. 설정 항목이 아니라 고정입니다.
            # ../docs/00_frozen_decisions.md §5 실행 타깃 원칙이 STT를 CPU로 못박았고,
            # Xavier에서 GPU 경로는 통합 메모리 91 MiB 할당에 실패해
            # cudaMalloc OOM -> SIGSEGV 로 죽습니다 [실측].
            "-ng",
        ]
        # -ac 450 등 동결된 플래그. 빠지면 같은 오디오가 1.5초가 아니라
        # 7.7초 걸립니다 `[실측]` — 경로 B 예산 2.0초를 혼자 넘깁니다.
        # whisper_flags() 는 VAD 모델이 없을 때만 --vad 를 빼고 경고합니다.
        cmd += whisper_flags()
        cmd += self.extra_flags
        # 도메인 프롬프트. 셸 스크립트와 같은 stt_prompt.txt 를 읽습니다.
        if C.WHISPER_CPP_PROMPT:
            cmd += ["--prompt", C.WHISPER_CPP_PROMPT]
        cmd += [
            "-nt",   # no timestamps
            "-np",   # no prints
        ]
        out = _run(
            cmd, C.SUBPROCESS_TIMEOUT_STT_S, "whisper-cli",
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.decode("utf-8", "replace")
        return _clean_asr_text(out)


class SherpaOnnxSTT(_Engine):
    """2안. 한국어 전용 zipformer(오프라인). CPU, 저메모리."""

    name = "sherpa_onnx"

    def __init__(self):
        self.rec = None

    def load(self):
        import sherpa_onnx

        d = Path(C.SHERPA_DIR)
        need = [C.SHERPA_ENCODER, C.SHERPA_DECODER, C.SHERPA_JOINER, C.SHERPA_TOKENS]
        missing = [n for n in need if not (d / n).exists()]
        if missing:
            raise RuntimeError(
                "sherpa 모델 파일이 없습니다: %s\n"
                "  압축을 푼 폴더의 실제 파일명을 확인하고 config.py 를 고치세요.\n"
                "  (epoch/avg 숫자가 배포판마다 다릅니다)" % ", ".join(missing)
            )
        self.rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(d / C.SHERPA_ENCODER),
            decoder=str(d / C.SHERPA_DECODER),
            joiner=str(d / C.SHERPA_JOINER),
            tokens=str(d / C.SHERPA_TOKENS),
            num_threads=C.SHERPA_THREADS,
            sample_rate=C.REC_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
        )

    def transcribe(self, wav):
        rate, samples = read_wav_float(wav)
        s = self.rec.create_stream()
        s.accept_waveform(rate, samples)
        self.rec.decode_stream(s)
        return _clean_asr_text(s.result.text)

    def unload(self):
        self.rec = None


class FasterWhisperSTT(_Engine):
    """3안. CTranslate2 백엔드. aarch64 설치 리스크가 있습니다."""

    name = "faster_whisper"

    def __init__(self):
        self.model = None

    def load(self):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            C.FW_MODEL, device=C.FW_DEVICE, compute_type=C.FW_COMPUTE
        )

    def transcribe(self, wav):
        segs, _info = self.model.transcribe(
            str(wav), language="ko", beam_size=C.FW_BEAM, vad_filter=False
        )
        return _clean_asr_text("".join(s.text for s in segs))

    def unload(self):
        self.model = None
        _empty_cuda_cache()


_STT = {
    "whisper_cpp": WhisperCppSTT,
    "sherpa_onnx": SherpaOnnxSTT,
    "faster_whisper": FasterWhisperSTT,
}


def make_stt(name=None):
    name = C.STT_ENGINE if name is None else name
    if name not in _STT:
        raise SystemExit("모르는 STT 엔진: %s  (가능: %s)" % (name, ", ".join(_STT)))
    return _STT[name]()


# =============================================================
# TTS
# =============================================================

class EspeakTTS(_Engine):
    """1안. 지연 하한선이자 최종 폴백. 음질은 기대하지 마세요."""

    name = "espeak"

    def synth(self, text, out_wav):
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            C.ESPEAK_BIN,
            "-v", C.ESPEAK_VOICE,
            "-s", str(C.ESPEAK_SPEED),
            "-w", str(out_wav),
            "--stdin",
        ]
        _run(
            cmd, C.SUBPROCESS_TIMEOUT_ESPEAK_S, "espeak-ng",
            input=text.encode("utf-8"), check=True, stderr=subprocess.PIPE,
        )


class PiperTTS(_Engine):
    """2안. 단일 ONNX. 한국어는 커뮤니티 모델입니다 (04_tts_candidates.md 참조)."""

    name = "piper"

    def load(self):
        if not os.path.exists(C.PIPER_MODEL):
            raise RuntimeError(
                "piper 모델이 없습니다: %s\n"
                "  ko.onnx 와 ko.onnx.json 두 파일이 한 쌍입니다." % C.PIPER_MODEL
            )

    def synth(self, text, out_wav):
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        last = None
        # 옵션명이 버전에 따라 바뀝니다. 둘 다 시도합니다.
        for flag in ("--output_file", "--output-file"):
            cmd = [C.PIPER_BIN, "--model", C.PIPER_MODEL, flag, str(out_wav)]
            p = _run(
                cmd, C.SUBPROCESS_TIMEOUT_TTS_S, "piper",
                input=text.encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            if p.returncode == 0 and os.path.exists(out_wav):
                return
            last = p.stderr.decode("utf-8", "replace")
        raise RuntimeError("piper 실행 실패:\n%s" % last)


class MeloTTS(_Engine):
    """3안. 공식 한국어. 가장 자연스럽고 가장 무겁습니다."""

    name = "melotts"

    def __init__(self):
        self.model = None
        self.spk = None

    def load(self):
        from melo.api import TTS

        self.model = TTS(language=C.MELO_LANGUAGE, device=C.MELO_DEVICE)
        self.spk = self.model.hps.data.spk2id

    def synth(self, text, out_wav):
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        key = C.MELO_LANGUAGE if C.MELO_LANGUAGE in self.spk else list(self.spk)[0]
        self.model.tts_to_file(text, self.spk[key], str(out_wav), speed=C.MELO_SPEED)

    def unload(self):
        self.model = None
        self.spk = None
        _empty_cuda_cache()


def _write_pcm16_wav(out_wav, samples, sample_rate):
    """float(-1..1) 샘플을 16비트 모노 PCM WAV로 저장합니다. numpy가 있으면 벡터화, 없으면 array 경로."""
    import wave

    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    try:
        import numpy as np

        pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        frames = (pcm * 32767.0).astype("<i2").tobytes()
    except ImportError:
        import array

        buf = array.array("h", (int(max(-1.0, min(1.0, float(x))) * 32767.0) for x in samples))
        if sys.byteorder != "little":
            buf.byteswap()
        frames = buf.tobytes()
    with wave.open(str(out_wav), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(int(sample_rate))
        stream.writeframes(frames)


class SherpaOnnxTTS(_Engine):
    """4안. sherpa-onnx VITS(mimic3 ko_KO kss_low, 여성 단일 화자). ONNX Runtime CPU 인프로세스.

    2026-08-30 Jetson 실기 채택. 모델 로드 약 3초, 문장당 합성 0.6~1.6초 `[실측]`.
    """

    name = "sherpa"
    # 모델 상주 캐시: 63 MB ONNX를 발화마다 다시 읽으면 첫 소리가 약 3초 늦어진다(Jetson 실측).
    # STT/TTS 온디맨드 원칙의 예외이며, 메모리 약 150 MB로 LLM 상주 예산과 겹치지 않는다.
    _cache = {}

    def __init__(self):
        self.tts = None

    @classmethod
    def reset_cache(cls):
        cls._cache.clear()

    def _model_files(self):
        model_dir = Path(C.SHERPA_TTS_DIR)
        model = Path(C.SHERPA_TTS_MODEL) if C.SHERPA_TTS_MODEL else None
        if model is None:
            candidates = sorted(model_dir.glob("*.onnx")) if model_dir.is_dir() else []
            model = candidates[0] if candidates else model_dir / "model.onnx"
        return model_dir, model, model_dir / "tokens.txt", model_dir / "espeak-ng-data"

    def load(self):
        model_dir, model, tokens, data_dir = self._model_files()
        if not model.exists() or not tokens.exists():
            raise RuntimeError(
                "sherpa-onnx TTS 모델이 없습니다: %s\n"
                "  vits-mimic3-ko_KO-kss_low 폴더(*.onnx, tokens.txt, espeak-ng-data/)를 %s 에 두거나\n"
                "  OGTECH_SHERPA_TTS_DIR 로 위치를 지정하세요." % (model, model_dir)
            )
        cached = self._cache.get(str(model)) if C.SHERPA_TTS_KEEP_LOADED else None
        if cached is not None:
            self.tts = cached
            return
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError("sherpa_onnx 파이썬 패키지가 없습니다: pip3 install --user sherpa-onnx (%s)" % exc)
        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(model),
            tokens=str(tokens),
            data_dir=str(data_dir) if data_dir.is_dir() else "",
            noise_scale=C.SHERPA_TTS_NOISE_SCALE,
            noise_scale_w=C.SHERPA_TTS_NOISE_SCALE_W,
            length_scale=C.SHERPA_TTS_LENGTH_SCALE,
        )
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=vits, num_threads=C.SHERPA_TTS_THREADS, provider="cpu",
            ),
        )
        if not config.validate():
            raise RuntimeError("sherpa-onnx TTS 설정 검증 실패: %s" % model_dir)
        self.tts = sherpa_onnx.OfflineTts(config)
        if C.SHERPA_TTS_KEEP_LOADED:
            self._cache[str(model)] = self.tts

    def synth(self, text, out_wav):
        if self.tts is None:
            self.load()
        audio = self.tts.generate(text, sid=C.SHERPA_TTS_SID, speed=C.SHERPA_TTS_SPEED)
        samples = audio.samples
        if len(samples) == 0:
            raise RuntimeError("sherpa-onnx가 빈 오디오를 돌려줬습니다: %r" % text[:40])
        _write_pcm16_wav(out_wav, samples, audio.sample_rate)

    def unload(self):
        # 상주 캐시를 쓰면 참조만 놓는다(모델은 프로세스가 끝날 때까지 유지).
        self.tts = None


_TTS = {
    "sherpa": SherpaOnnxTTS,
    "espeak": EspeakTTS,
    "piper": PiperTTS,
    "melotts": MeloTTS,
}


def make_tts(name=None):
    name = C.TTS_ENGINE if name is None else name
    if name not in _TTS:
        raise SystemExit("모르는 TTS 엔진: %s  (가능: %s)" % (name, ", ".join(_TTS)))
    return _TTS[name]()


# =============================================================
# LLM (경로 A)
# =============================================================

def classify_scenario(user_text):
    """14개 라벨 JSON Schema 분류. 실패 시 재시도 없이 unknown을 돌려준다."""
    import http.client
    import json
    import urllib.error
    import urllib.request

    payload = {
        "model": C.LLM_MODEL,
        "messages": [
            {"role": "system", "content": C.CLASSIFIER_SYSTEM},
            {"role": "user", "content": str(user_text or "")[:240]},
        ],
        "temperature": 0.0,
        "max_tokens": 16,
        "stream": False,
        "response_format": C.CLASSIFIER_RESPONSE_FORMAT,
    }
    request = urllib.request.Request(
        C.LLM_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=C.LLM_CLASSIFY_TIMEOUT_S) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if set(parsed) != {"scenario_id"} or parsed["scenario_id"] not in C.SCENARIO_IDS:
            return "unknown", "schema_validation_failed"
        usage = body.get("usage") or {}
        return parsed["scenario_id"], "프롬프트 %s tok / 생성 %s tok" % (
            usage.get("prompt_tokens", "?"), usage.get("completion_tokens", "?")
        )
    except (
        urllib.error.URLError, TimeoutError, http.client.IncompleteRead,
        KeyError, IndexError, TypeError, json.JSONDecodeError,
    ):  # TypeError: content: null 등 형 불일치 / IncompleteRead: 응답 중간 끊김
        return "unknown", "classifier_failed_no_retry"

# =============================================================
# 내부
# =============================================================

_NOISE = {"[BLANK_AUDIO]", "[음악]", "(음악)", "[박수]", "(박수)", "[BLANK]"}


def _clean_asr_text(raw):
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line in _NOISE:
            continue
        lines.append(line)
    return " ".join(lines).strip()


def _empty_cuda_cache():
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass
