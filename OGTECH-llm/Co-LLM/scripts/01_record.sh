#!/usr/bin/env bash
# Record only. Speaker does NOT need to be plugged in.
#   bash scripts/01_record.sh            # 5 s
#   bash scripts/01_record.sh 8          # 8 s
#   bash scripts/01_record.sh 5 plughw:CARD=Device,DEV=0
set -u

# Output goes into scripts/test_rec/ -- this folder is self-contained.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${HERE}/test_rec/rec.wav"
SEC="${1:-5}"
DEV="${2:-}"
mkdir -p "$(dirname "${OUT}")"

# Pick the USB card from /proc/asound instead of blacklisting names.
# The old 'grep -viE hdmi|tegra' filter did NOT catch this board's onboard
# DSP, whose card name is 'APE' -- recording there yields pure silence.
# 'usbid' exists only for USB cards; 'pcm*c' means it can capture.
if [ -z "${DEV}" ]; then
  for D in /proc/asound/card*/; do
    [ -f "${D}usbid" ] || continue
    [ -f "${D}id" ] || continue
    ls -d "${D}"pcm*c >/dev/null 2>&1 || continue
    DEV="plughw:CARD=$(cat "${D}id"),DEV=0"
    break
  done
fi

if [ -z "${DEV}" ]; then
  echo "FAIL: no USB capture device found."
  echo "  check:  lsusb ; arecord -l ; dmesg | tail -20"
  echo "  to force a device:  bash scripts/01_record.sh 5 plughw:CARD=<name>,DEV=0"
  exit 1
fi

echo "device : ${DEV}"
echo "file   : ${OUT}"
echo "recording ${SEC}s -- SPEAK NOW"

# plughw: is EXCLUSIVE. If PulseAudio/PipeWire grabbed the USB card first,
# arecord gets EBUSY. Fall back to the shared devices instead of failing.
ERR="${HERE}/test_rec/_arecord_err.log"
: > "${ERR}"
USED=""
for D in "${DEV}" "default" "pulse" "sysdefault"; do
  [ -z "${D}" ] && continue
  echo "--- try ${D}" >> "${ERR}"
  if arecord -D "${D}" -f S16_LE -r 16000 -c 1 -d "${SEC}" -q "${OUT}" 2>>"${ERR}"; then
    USED="${D}"; break
  fi
done

if [ -z "${USED}" ]; then
  echo "FAIL: every device was rejected. arecord said:"
  sed 's/^/  /' "${ERR}"
  echo
  echo "  who holds the card?   fuser -v /dev/snd/*"
  echo "  kill the holder:      pulseaudio -k"
  exit 1
fi

echo "recorded on: ${USED}"
if [ "${USED}" != "${DEV}" ]; then
  echo "  NOTE: '${DEV}' was busy, used '${USED}' instead. Mic itself is fine."
fi

python3 - "${OUT}" <<'PY'
import sys, wave, array
with wave.open(sys.argv[1], "rb") as w:
    data = w.readframes(w.getnframes())
a = array.array("h")
a.frombytes(data)
if not len(a):
    print("FAIL: empty wav"); sys.exit(1)
peak = max(max(a), -min(a))
rms = int((sum(x * x for x in a) / len(a)) ** 0.5)
print("level  : peak %d/32767  rms %d" % (peak, rms))
if peak < 500:
    print("  FAIL: silent. alsamixer -> F6 mic card -> F4 capture -> raise gain, press Space")
elif peak > 32000:
    print("  WARN: clipping. lower capture gain")
else:
    print("  OK")
PY

echo
echo "NEXT: unplug the mic, plug the speaker, then run"
echo "  bash scripts/02_play.sh"
