#!/usr/bin/env bash
# OGTECH 제품 음성 1회 실행. 인자는 product_voice.py에 그대로 전달합니다.
set -eu

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${HERE}/product_voice.py" "$@"
