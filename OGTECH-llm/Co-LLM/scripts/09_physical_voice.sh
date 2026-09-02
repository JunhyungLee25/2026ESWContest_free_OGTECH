#!/usr/bin/env bash
# STM32 물리 음성 버튼 push-to-talk 데몬. 인자는 Python 실행기에 그대로 전달합니다.
set -eu

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${HERE}/physical_voice.py" "$@"
