#!/usr/bin/env bash
# OGTECH 선제 음성 경보 데몬. 인자는 device_monitor.py에 그대로 전달합니다.
set -eu

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${HERE}/device_monitor.py" "$@"
