#!/usr/bin/env bash
# Jetson SSH에서 전체 화면 시연을 시작한다. Firefox 키오스크 창이 앞에 있어야 한다.
set -euo pipefail

if ! command -v xdotool >/dev/null 2>&1; then
  echo "xdotool이 없습니다. sudo apt install -y xdotool 을 먼저 실행하세요." >&2
  exit 1
fi

DISPLAY="${DISPLAY:-:0}" \
XAUTHORITY="${XAUTHORITY:-/home/kit/.Xauthority}" \
xdotool key --clearmodifiers a
