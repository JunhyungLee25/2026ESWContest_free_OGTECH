#!/usr/bin/env bash
set -euo pipefail

# 부팅하면 화면 선택(/select/)이 먼저 뜬다. 터치로 제품 화면(/product/)과
# 촬영 화면(/video/?live=1) 중 하나를 고른다.
# 특정 화면으로 바로 띄우려면 OGTECH_KIOSK_URL 로 덮어쓴다.
# 촬영용 자동 재생은 .../video/?live=1&autoplay=1 (또는 autoplay=loop).
KIOSK_URL="${OGTECH_KIOSK_URL:-http://127.0.0.1:8790/select/}"
# Firefox 전용 프로필: 정전 후 세션 복구·안전 모드·첫 실행 안내가 제품 화면을 가리지 않게 한다.
FIREFOX_PROFILE="${OGTECH_FIREFOX_PROFILE:-${XDG_CONFIG_HOME:-$HOME/.config}/ogtech/firefox-kiosk}"

wait_for_map() {
  local i
  for i in $(seq 1 60); do
    if python3 - "${KIOSK_URL}" <<'PY' 2>/dev/null; then return 0; fi
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=2).read(64)
PY
    sleep 1
  done
  echo "경고: ${KIOSK_URL} 응답을 60초 동안 받지 못했습니다. 브라우저는 그대로 띄웁니다." >&2
}

prepare_firefox_profile() {
  mkdir -p "${FIREFOX_PROFILE}"
  cat > "${FIREFOX_PROFILE}/user.js" <<'PREFS'
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.sessionstore.max_resumed_crashes", 0);
user_pref("toolkit.startup.max_resumed_crashes", -1);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.aboutwelcome.enabled", false);
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("app.update.enabled", false);
user_pref("browser.tabs.warnOnClose", false);
user_pref("dom.disable_beforeunload", true);
user_pref("media.autoplay.default", 0);
user_pref("media.autoplay.blocking_policy", 0);
user_pref("full-screen-api.warning.timeout", 0);
PREFS
  rm -f "${FIREFOX_PROFILE}/.parentlock" "${FIREFOX_PROFILE}/lock" 2>/dev/null || true
}

wait_for_map

# 시연 중 상단 메뉴(Wi-Fi 꺼짐 표시)를 끌어내리면 GNOME 이 전체화면을 풀고 Firefox 는
# 되돌리지 않는다. 사용자가 다시 화면을 건드리면 전체화면으로 되돌린다. 이 서비스와 함께 죽는다.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "${SCRIPT_DIR}/kiosk_fullscreen_guard.py" &

if command -v firefox >/dev/null 2>&1; then
  prepare_firefox_profile
  exec firefox --kiosk --no-remote --profile "${FIREFOX_PROFILE}" "${KIOSK_URL}"
elif command -v chromium-browser >/dev/null 2>&1; then
  CHROMIUM_BIN="chromium-browser"
elif command -v chromium >/dev/null 2>&1; then
  CHROMIUM_BIN="chromium"
else
  echo "Firefox 또는 Chromium 실행 파일을 찾지 못했습니다." >&2
  exit 1
fi

exec "${CHROMIUM_BIN}" \
  --kiosk \
  --no-first-run \
  --disable-session-crashed-bubble \
  --disable-infobars \
  --autoplay-policy=no-user-gesture-required \
  "${KIOSK_URL}"
