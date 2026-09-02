#!/usr/bin/env bash
# Play only. Mic does NOT need to be plugged in.
#   bash scripts/02_play.sh              # play what 01_record.sh recorded
#   bash scripts/02_play.sh --tts        # Korean TTS test instead (espeak-ng)
#   bash scripts/02_play.sh some.wav
#   bash scripts/02_play.sh plughw:CARD=UACDemoV10,DEV=0
#   bash scripts/02_play.sh --tts plughw:CARD=UACDemoV10,DEV=0
# Arguments are recognised by shape, so the order does not matter.
set -u

# Output goes into scripts/test_rec/ -- this folder is self-contained.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RES="${HERE}/test_rec"
mkdir -p "${RES}"

MODE="play"
WAV=""
DEV=""
for A in "$@"; do
  case "${A}" in
    --tts)                                 MODE="tts" ;;
    plughw:*|hw:*|default|pulse|sysdefault) DEV="${A}" ;;
    *)                                     WAV="${A}" ;;
  esac
done
WAV="${WAV:-${RES}/rec.wav}"

# Pick the USB card from /proc/asound instead of blacklisting names.
# The old 'grep -viE hdmi|tegra' filter did NOT catch this board's cards:
# HDMI audio is card name 'HDA' and the onboard DSP is 'APE'.
# 'usbid' exists only for USB cards; 'pcm*p' means it can play back.
if [ -z "${DEV}" ]; then
  for D in /proc/asound/card*/; do
    [ -f "${D}usbid" ] || continue
    [ -f "${D}id" ] || continue
    ls -d "${D}"pcm*p >/dev/null 2>&1 || continue
    DEV="plughw:CARD=$(cat "${D}id"),DEV=0"
    break
  done
fi

if [ -z "${DEV}" ]; then
  echo "FAIL: no USB playback device found."
  echo "  A USB speaker must be plugged in. HDMI (CARD=HDA) is NOT used on purpose --"
  echo "  playing there is silent unless the monitor has speakers."
  echo "  check:  lsusb ; aplay -l ; dmesg | tail -20"
  echo "  to force a device:  bash scripts/02_play.sh plughw:CARD=<name>,DEV=0"
  exit 1
fi

if [ "${MODE}" = "tts" ]; then
  if ! command -v espeak-ng >/dev/null 2>&1; then
    echo "FAIL: sudo apt install -y espeak-ng"
    exit 1
  fi
  WAV="${RES}/tts.wav"
  espeak-ng -v ko -s 150 -w "${WAV}" --stdin <<'TXT'
해가 지기까지 40분 남았습니다. 귀환 권고 시각과 베이스캠프 경로를 확인하세요.
TXT
  echo "(robotic voice is EXPECTED -- espeak-ng is the baseline, not the final engine)"
fi

if [ ! -f "${WAV}" ]; then
  echo "FAIL: no such file: ${WAV}"
  echo "  run 'bash scripts/01_record.sh' first"
  exit 1
fi

echo "device : ${DEV}"
echo "file   : ${WAV}"

# plughw: is EXCLUSIVE. If PulseAudio/PipeWire grabbed the USB card first,
# aplay gets EBUSY. Fall back to the shared devices instead of failing.
ERR="${RES}/_aplay_err.log"
: > "${ERR}"
USED=""
for D in "${DEV}" "default" "pulse" "sysdefault"; do
  [ -z "${D}" ] && continue
  echo "--- try ${D}" >> "${ERR}"
  if aplay -D "${D}" -q "${WAV}" 2>>"${ERR}"; then USED="${D}"; break; fi
done

if [ -z "${USED}" ]; then
  echo "FAIL: every device was rejected. aplay said:"
  sed 's/^/  /' "${ERR}"
  echo
  echo "  who holds the card?   fuser -v /dev/snd/*"
  echo "  kill the holder:      pulseaudio -k   (or: pkill aplay)"
  echo "  'Invalid argument' -> use plughw: not hw:"
  exit 1
fi

echo "played : ${USED}"
if [ "${USED}" != "${DEV}" ]; then
  echo
  echo "  NOTE: '${DEV}' was busy -- something else owns the card (usually PulseAudio)."
  echo "        It played through '${USED}' instead, so the speaker itself is FINE."
  echo "        To get exclusive plughw: access back, release it from PulseAudio:"
  echo "          pactl list short sinks"
  echo "          pactl suspend-sink <sink-name> 1"
  echo "        Permanent:  echo 'autospawn = no' >> ~/.config/pulse/client.conf && pulseaudio -k"
fi

echo "done. heard nothing?"
echo "  alsamixer -> F6 pick the USB card -> M to unmute -> raise Master/PCM"
# Always print the INTENDED device, never a 'default'/'pulse' fallback --
# those are shared aliases and resolve to a different card on the next boot.
echo "  SPK_DEVICE = \"${DEV}\"   <- put this in config.py"
