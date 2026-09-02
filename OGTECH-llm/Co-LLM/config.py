# -*- coding: utf-8 -*-
"""Co-LLM 음성 배관 테스트 설정.

엔진을 바꾸는 곳은 이 파일 한 곳뿐입니다.
STT_ENGINE / TTS_ENGINE 두 줄만 고치고 scripts/voice_loop.py 를 다시 실행합니다.

경로에 ~ 를 써도 됩니다. 아래 _p() 가 펼쳐 줍니다.
"""

import os
from pathlib import Path


def _p(path_str):
    return str(Path(os.path.expanduser(path_str)))


def _ai_path(rel):
    """모델 기본 경로. ~/ogtech_ai/<rel> 우선, 없고 구 설치 ~/safeaid_ai/<rel> 가 있으면 그쪽.

    환경변수(OGTECH_*) 덮어쓰기는 호출부의 os.environ.get 이 먼저 잡습니다.
    """
    new = Path(os.path.expanduser("~/ogtech_ai")) / rel
    old = Path(os.path.expanduser("~/safeaid_ai")) / rel
    if not new.exists() and old.exists():
        return str(old)
    return str(new)


# 출력물은 전부 scripts/test_rec/ 안에 남습니다.
# scripts 폴더만 젯슨으로 옮겨도 그 안에서 완결되도록 한 것입니다.
CO_LLM_DIR = Path(__file__).resolve().parent
# 코드 트리가 읽기 전용이면 OGTECH_RESULT_DIR 로 옮깁니다(락·캐시·녹음이 모두 따라갑니다).
_result_env = os.environ.get("OGTECH_RESULT_DIR", "").strip()
RESULT_DIR = Path(_p(_result_env)) if _result_env else CO_LLM_DIR / "scripts" / "test_rec"
SAMPLE_DIR = RESULT_DIR


# =============================================================
# 1. 오디오 장치
#    scripts/00_check_audio.sh 가 찍어 준 이름을 그대로 넣습니다.
#    hw: 가 아니라 plughw: 를 씁니다 (리샘플링 자동).
#    카드 번호(hw:1,0)는 USB 꽂는 순서에 따라 바뀌므로 쓰지 않습니다.
# =============================================================
MIC_DEVICE = os.environ.get(
    "OGTECH_MIC_DEVICE", "plughw:CARD=Device,DEV=0"
)  # Adafruit 3367 Mini USB Microphone
SPK_DEVICE = os.environ.get(
    "OGTECH_SPK_DEVICE", "plughw:CARD=UACDemoV10,DEV=0"
)  # Adafruit 3369 Mini USB Stereo Speaker

REC_SECONDS = 5          # 한 번에 녹음할 초. --seconds 로 덮어쓸 수 있습니다
REC_RATE = 16000         # STT 3안 모두 16 kHz 를 요구합니다. 바꾸지 마세요
REC_CHANNELS = 1


# =============================================================
# 2. 엔진 선택  <-- 테스트할 때 고치는 곳은 여기 두 줄입니다
# =============================================================
STT_ENGINE = "whisper_cpp"    # whisper_cpp | sherpa_onnx | faster_whisper
# 제품 기본 목소리는 sherpa-onnx VITS(mimic3 ko_KO kss_low, 여성 단일 화자)입니다 — 2026-08-30 Jetson 실기 채택.
# MeloTTS(가장 자연스럽지만 torch 필요)·Piper는 설치돼 있으면 다음 후보이고, espeak-ng는 청취 명료도
# 실패 `[실측]` 때문에 최종 비상 폴백으로만 씁니다.
TTS_ENGINE_ORDER = tuple(
    item.strip()
    for item in os.environ.get("OGTECH_TTS_ORDER", "sherpa,melotts,piper,espeak").split(",")
    if item.strip()
)
TTS_ENGINE = os.environ.get("OGTECH_TTS_ENGINE", TTS_ENGINE_ORDER[0])


# =============================================================
# 3. STT 설정
# =============================================================

# --- 1안: whisper.cpp -----------------------------------------
# 구버전은 바이너리 이름이 whisper-cli 가 아니라 main 입니다.
WHISPER_CPP_BIN = _p(os.environ.get(
    "OGTECH_WHISPER_CPP_BIN", _ai_path("stt/whisper.cpp/build/bin/whisper-cli")
))
# small 이 아니라 base 입니다. 1,494 ms vs 3,468 ms `[실측]` 이고 경로 B 예산이
# 2.0초입니다. 모델 확정은 미결 #8 — 21문장 벤치가 닫습니다.
WHISPER_CPP_MODEL = _p(os.environ.get(
    "OGTECH_WHISPER_CPP_MODEL", _ai_path("stt/whisper.cpp/models/ggml-base.bin")
))
WHISPER_CPP_THREADS = 6      # 4 -> 6 은 -9% `[실측]`. nproc 6 이 상한
WHISPER_CPP_LANG = "ko"

# docs/00_frozen_decisions.md §5에서 동결된 플래그입니다. 튜닝 손잡이가 아닙니다.
#   -ac 450  30초 멜 윈도 패딩이 지연의 81%. 16,933 -> 1,494 ms `[실측]`
#            300 으로 내리면 환각과 12.6초 폭주 `[실측]`
#   -bo 1 -bs 1  beam 은 +33% 지연에 출력 변화 0 `[실측]`
#   --vad -vm    P5 벤치 `base_cpu_vad` = 최종 선정 구성. 무음 구간의 디코더 반복이
#                최댓값을 3,363 -> 1,495 ms 로 줄였습니다 `[실측]`
#                (docs/measurements.csv · docs/decision_matrix.csv).
#                판정은 중앙값이 아니라 최댓값이므로 경로 B 예산 2.0초를 통과하는
#                구성은 이것뿐입니다.
# -ng(GPU 미사용)는 설정이 아니라 고정이므로 engines.py 안에 박아 둡니다.
#
# VAD 모델은 whisper.cpp 본체와 따로 내려받습니다.
#   cd ~/ogtech_ai/stt/whisper.cpp && bash ./models/download-vad-model.sh silero-v5.1.2
# 파일이 없으면 engines.py 가 --vad 를 자동으로 빼고 경고합니다(런타임 보호).
WHISPER_VAD_MODEL = _p(os.environ.get(
    "OGTECH_WHISPER_VAD_MODEL",
    _ai_path("stt/whisper.cpp/models/ggml-silero-v5.1.2.bin"),
))
WHISPER_CPP_FLAGS = [
    "-ac", "450", "-bo", "1", "-bs", "1", "-nf",
    "--vad", "-vm", WHISPER_VAD_MODEL,
]

# 호출어 데몬(scripts/wake_voice.py)이 마이크 스트림을 발화 단위로 끊는 Silero VAD(sherpa-onnx ONNX).
# whisper.cpp 의 ggml VAD 와는 다른 파일입니다. 없으면 에너지 기반 분절로 내려가며 한 번 경고합니다.
#   curl -L -o ~/ogtech_ai/stt/silero_vad.onnx \
#     https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
VAD_ONNX_MODEL = _p(os.environ.get("OGTECH_VAD_ONNX", _ai_path("stt/silero_vad.onnx")))

# 초기 프롬프트(컨텍스트 바이어싱)의 정본은 scripts/stt_prompt.txt 한 곳입니다.
# 셸 스크립트(03/06)와 이 파이썬 경로가 같은 파일을 읽으므로 사본이 갈라지지 않습니다.
# 환경변수 WHISPER_PROMPT 가 있으면 그쪽이 이깁니다(빈 문자열 = 프롬프트 끄기).
STT_PROMPT_FILE = Path(os.environ.get(
    "WHISPER_PROMPT_FILE", str(CO_LLM_DIR / "scripts" / "stt_prompt.txt")))
if not STT_PROMPT_FILE.is_absolute() and not STT_PROMPT_FILE.exists():
    STT_PROMPT_FILE = CO_LLM_DIR / "scripts" / STT_PROMPT_FILE.name


def _read_prompt(path):
    """'#' 주석과 빈 줄을 버리고 나머지 줄을 공백으로 이어 한 줄로 만듭니다."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""
    out = []
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return " ".join(out)


_env_prompt = os.environ.get("WHISPER_PROMPT")
WHISPER_CPP_PROMPT = (
    _env_prompt if _env_prompt is not None else _read_prompt(STT_PROMPT_FILE)
)

# --- 2안: sherpa-onnx 한국어 zipformer (오프라인) ---------------
# 압축을 푼 뒤 실제 파일명을 확인해서 맞추세요. epoch/avg 숫자가 다를 수 있습니다.
SHERPA_DIR = _p(os.environ.get(
    "OGTECH_SHERPA_DIR", _ai_path("stt/sherpa-onnx-zipformer-korean-2024-06-24")
))
SHERPA_ENCODER = "encoder-epoch-99-avg-1.int8.onnx"
SHERPA_DECODER = "decoder-epoch-99-avg-1.onnx"
SHERPA_JOINER = "joiner-epoch-99-avg-1.int8.onnx"
SHERPA_TOKENS = "tokens.txt"
SHERPA_THREADS = 4

# --- 3안: faster-whisper ---------------------------------------
FW_MODEL = "small"       # tiny | base | small | medium
FW_DEVICE = "cuda"       # cuda 가 죽으면 cpu
FW_COMPUTE = "float16"   # cpu 일 때는 int8
FW_BEAM = 1              # 기본값 5 는 느립니다


# =============================================================
# 4. TTS 설정
# =============================================================

# --- 1안: espeak-ng --------------------------------------------
ESPEAK_BIN = "espeak-ng"
ESPEAK_VOICE = "ko"
ESPEAK_SPEED = 150       # 130~170. 야외에서는 느린 쪽이 알아듣기 쉽습니다

# --- 2안: piper ------------------------------------------------
PIPER_BIN = "piper"
PIPER_MODEL = _p(os.environ.get("OGTECH_PIPER_MODEL", _ai_path("tts/piper/ko.onnx")))

# --- 3안: MeloTTS-Korean ---------------------------------------
MELO_LANGUAGE = "KR"
MELO_DEVICE = "cpu"      # "cuda:0" 로 바꿔 비교해 보세요
MELO_SPEED = 1.0

# --- 4안: sherpa-onnx VITS (mimic3 ko_KO kss_low, 여성 음성) ----
# pip3 install --user sherpa-onnx 뒤 모델 폴더를 내려받습니다(README "Jetson ALSA·systemd 설치" 참조):
#   https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-mimic3-ko_KO-kss_low.tar.bz2
# 폴더 안의 *.onnx, tokens.txt, espeak-ng-data/ 를 씁니다. 실행은 CPU(ONNX Runtime)이며 GPU를 건드리지 않습니다.
# KSS 데이터셋 계열 음성은 비상업 조건(CC BY-NC-SA)이 붙을 수 있으니 배포 전 라이선스를 확인합니다.
SHERPA_TTS_DIR = _p(os.environ.get(
    "OGTECH_SHERPA_TTS_DIR", _ai_path("tts/sherpa/vits-mimic3-ko_KO-kss_low")
))
SHERPA_TTS_MODEL = os.environ.get("OGTECH_SHERPA_TTS_MODEL", "")  # 비우면 폴더의 *.onnx 하나를 자동 선택
SHERPA_TTS_THREADS = 4
# 발화마다 모델을 다시 읽지 않고 프로세스 안에 상주(첫 소리 -3 s, 메모리 약 150 MB). 0/false 로 끄면 온디맨드.
SHERPA_TTS_KEEP_LOADED = os.environ.get("OGTECH_SHERPA_TTS_KEEP_LOADED", "1").strip().lower() not in {"0", "false", "no"}
SHERPA_TTS_SID = int(os.environ.get("OGTECH_SHERPA_TTS_SID", "0"))
# 주의: sherpa-onnx VITS는 speed≠1.0이면 length_scale을 1/speed로 "대체"한다(곱하지 않음, Jetson 실측 2026-08-30:
# ls 1.1에서 speed 0.9 → +4%, 0.8 → +14%). 속도는 SHERPA_TTS_LENGTH_SCALE 한 곳으로만 조절하고 speed는 1.0을 유지한다.
SHERPA_TTS_SPEED = float(os.environ.get("OGTECH_SHERPA_TTS_SPEED", "1.0"))
# Jetson 실기 청취 튜닝(2026-08-30 오전): 잡음 스케일을 낮추고 10% 느리게(1.1) — 야외 명료도 우선.
# 2026-08-30 오후 사용자 청취: 1.1도 너무 빨라 알아듣기 어려움 → 0.9배속 = 1.1/0.9 ≈ 1.22 (발화 길이 약 +11%).
SHERPA_TTS_NOISE_SCALE = float(os.environ.get("OGTECH_SHERPA_TTS_NOISE_SCALE", "0.4"))
SHERPA_TTS_NOISE_SCALE_W = float(os.environ.get("OGTECH_SHERPA_TTS_NOISE_SCALE_W", "0.6"))
SHERPA_TTS_LENGTH_SCALE = float(os.environ.get("OGTECH_SHERPA_TTS_LENGTH_SCALE", "1.22"))

# 제품 TTS 품질 게이트. 생성 WAV가 이 계약을 벗어나면 다음 엔진으로 한 번만 폴백합니다.
TTS_FIXED_AUDIO_MANIFEST = CO_LLM_DIR / "config" / "fixed_audio.json"
TTS_CACHE_DIR = RESULT_DIR / "tts_cache"
LAST_VERIFIED_RESPONSE_PATH = RESULT_DIR / "last_verified_response.json"
TTS_MAX_TEXT_CHARS = 420
TTS_MIN_DURATION_S = 0.15
TTS_MAX_DURATION_S = 45.0
TTS_MIN_PEAK_RATIO = 0.001
TTS_MIN_RMS_RATIO = 0.0001
TTS_MAX_CLIPPED_RATIO = 0.02
TTS_TARGET_PEAK_RATIO = 0.82
TTS_MAX_GAIN = 4.0

# 데모 지도(demo=true) 안내 문장의 접두. device_monitor·ogtech_core 가 붙이고,
# tts_pipeline 은 고정 WAV 조회 때 이 접두를 뗀 문장으로도 찾습니다.
DEMO_SPEECH_PREFIX = "데모 값 기준으로, "


# =============================================================
# 5. LLM (경로 A 에서만 사용)
#    llama-server 직결입니다. 제품의 backend(8765) 가 아닙니다.
# =============================================================
LLM_URL = os.environ.get("OGTECH_LLM_URL", "http://127.0.0.1:8080/v1/chat/completions")
LLM_MODEL = os.environ.get("OGTECH_LLM_MODEL", "qwen2.5-1.5b-instruct")
LLM_CLASSIFY_TIMEOUT_S = 2.0

SCENARIO_IDS = [
    "lost", "route", "daylight", "weather", "shelter", "warmth", "water",
    "food", "sleep_safety", "injury", "wildlife", "gear", "refuse", "unknown",
]
CLASSIFIER_SYSTEM = (
    "사용자 발화를 OGTECH 시나리오 하나로만 분류하세요. "
    "lost 길 잃음, route 항법, daylight 일조, weather 현장 기상, shelter 야영지, "
    "warmth 추위, water 물, food 식량, sleep_safety 수면 중 연소·CO, injury 부상, "
    "wildlife 야생동물, gear 장비, refuse 식용·진단·약물 금지, unknown 불명입니다. "
    "지침이나 설명을 생성하지 말고 JSON 스키마만 따르세요."
)
CLASSIFIER_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "ogtech_scenario",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"scenario_id": {"type": "string", "enum": SCENARIO_IDS}},
            "required": ["scenario_id"],
            "additionalProperties": False,
        },
    },
}

MAP_API_URL = os.environ.get("OGTECH_MAP_URL", "http://127.0.0.1:8790")
MAP_API_TIMEOUT_S = 2.0
PIPELINE_LOCK_PATH = Path(
    os.environ.get("OGTECH_PIPELINE_LOCK", str(RESULT_DIR / "audio_pipeline.lock"))
)
PIPELINE_LOCK_TIMEOUT_S = 30.0

# 서브프로세스 상한(초). 멈춘 arecord/aplay/whisper-cli/espeak/piper 가 락을 쥔 채
# 영구 대기하지 않게 합니다. 초과는 engines.py 가 RuntimeError 로 바꿉니다.
SUBPROCESS_TIMEOUT_STT_S = 60.0            # whisper-cli
SUBPROCESS_TIMEOUT_TTS_S = 60.0            # piper
SUBPROCESS_TIMEOUT_ESPEAK_S = 30.0         # espeak-ng
SUBPROCESS_TIMEOUT_PLAY_EXTRA_S = 5.0      # arecord: 녹음 초 + 이 값 / aplay: WAV 길이 + 이 값
SUBPROCESS_TIMEOUT_PLAY_FALLBACK_S = 30.0  # aplay: WAV 길이를 읽지 못할 때 가정하는 길이

# =============================================================
# 6. 예산 (docs/00_frozen_decisions.md §2·§4)
# =============================================================
BUDGET_PATH_B_S = 2.0    # 경로 B: 키워드 게이트 -> 고정 카드 -> TTS
BUDGET_PATH_A_S = 3.5    # 경로 A: 라벨 분류 -> 검수 카드 -> TTS
MEM_GATE_MB = 1024       # MemAvailable 게이트
