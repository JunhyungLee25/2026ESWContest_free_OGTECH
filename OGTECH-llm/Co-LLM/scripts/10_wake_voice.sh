#!/usr/bin/env bash
# '오지야' 호출어 상시 청취 데몬. 인자는 wake_voice.py 에 그대로 전달합니다.
#   bash 10_wake_voice.sh                       # 마이크 대기, 스피커 출력
#   bash 10_wake_voice.sh --no-play             # 무음 검증(WAV 만 생성)
#   bash 10_wake_voice.sh --input-wavs a.wav b.wav --once   # 파일로 대화 재현
set -eu

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${HERE}/wake_voice.py" "$@"
