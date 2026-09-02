#!/usr/bin/env bash
# 0-stage loopback check: record 5s from USB mic, play it back on USB speaker.
# ASCII only on purpose (terminal encoding safety).
#
# usage:
#   bash scripts/00_check_audio.sh                  # mic + speaker loopback
#   bash scripts/00_check_audio.sh --spk-only       # speaker only (no mic needed)
#   bash scripts/00_check_audio.sh plughw:CARD=Device,DEV=0 plughw:CARD=UACDemoV10,DEV=0
#   bash scripts/00_check_audio.sh --spk-only plughw:CARD=UACDemoV10,DEV=0

set -u

SPK_ONLY=0
if [ "${1:-}" = "--spk-only" ]; then
  SPK_ONLY=1
  shift
fi

# Output goes into scripts/test_rec/ -- this folder is self-contained.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${HERE}/test_rec"
WAV="${OUT_DIR}/loopback_check.wav"
SECONDS_TO_REC=5
mkdir -p "${OUT_DIR}"

hr() { printf '%s\n' "------------------------------------------------------------"; }

hr
echo "[1] USB devices"
hr
lsusb || echo "  (lsusb not found: sudo apt install usbutils)"

hr
echo "[2] capture cards  (arecord -l)"
hr
arecord -l || true

hr
echo "[3] playback cards  (aplay -l)"
hr
aplay -l || true

# Pick the USB card from /proc/asound instead of blacklisting names.
# On this board the built-in cards are 'HDA' (HDMI audio) and 'APE' (onboard
# DSP); neither contains the strings 'hdmi' or 'tegra', so a name blacklist
# silently selects them and the test appears to fail with no sound.
# 'usbid' exists only for USB cards. $1 is 'p' (playback) or 'c' (capture).
pick_usb() {
  local D
  for D in /proc/asound/card*/; do
    [ -f "${D}usbid" ] || continue
    [ -f "${D}id" ] || continue
    ls -d "${D}"pcm*"$1" >/dev/null 2>&1 || continue
    echo "plughw:CARD=$(cat "${D}id"),DEV=0"
    return 0
  done
  return 1
}

hr
echo "[4] usable NAMES -- copy these into config.py"
hr
echo "  MIC candidates:"
arecord -L 2>/dev/null | grep -E '^plughw:CARD=' | sed 's/^/    /' || true
echo "  SPK candidates (USB only -- HDA is HDMI audio, APE is the onboard DSP):"
aplay -L 2>/dev/null | grep -E '^plughw:CARD=' | sed 's/^/    /' || true
echo
echo "  NOTE: use plughw: (not hw:) so ALSA resamples to 16000 Hz mono."
echo "  NOTE: card NUMBERS (hw:1,0) change on every boot. Use CARD=<name>."

# ---- pick devices -------------------------------------------------
if [ "${SPK_ONLY}" -eq 1 ]; then
  MIC=""
  SPK="${1:-}"
else
  MIC="${1:-}"
  SPK="${2:-}"
  if [ -z "${MIC}" ]; then
    MIC="$(pick_usb c)"
  fi
fi
if [ -z "${SPK}" ]; then
  SPK="$(pick_usb p)"
fi

hr
echo "[5] selected"
hr
[ "${SPK_ONLY}" -eq 1 ] || echo "  MIC_DEVICE = \"${MIC}\""
echo "  SPK_DEVICE = \"${SPK}\""

if [ -z "${SPK}" ]; then
  echo
  echo "  FAIL: no USB playback device found. Plug the speaker in, then re-run."
  echo "        If it is plugged in, check: lsusb ; dmesg | tail -30"
  exit 1
fi

# ---- speaker-only path --------------------------------------------
if [ "${SPK_ONLY}" -eq 1 ]; then
  hr
  echo "[6] test tone -- 440 Hz sine, left then right"
  hr
  speaker-test -D "${SPK}" -c 2 -t sine -f 440 -l 1 >/dev/null 2>&1 \
    && echo "  tone sent" \
    || echo "  WARN: speaker-test failed (continuing to the wav test)"

  SYS_WAV=/usr/share/sounds/alsa/Front_Center.wav
  if [ -f "${SYS_WAV}" ]; then
    hr
    echo "[7] system wav -- a human voice should say 'Front Center'"
    hr
    aplay -D "${SPK}" "${SYS_WAV}" || true
  fi

  hr
  echo "[8] Korean TTS -- espeak-ng"
  hr
  if command -v espeak-ng >/dev/null 2>&1; then
    espeak-ng -v ko -s 150 -w "${OUT_DIR}/spk_check.wav" --stdin <<'TXT'
해가 지기까지 40분 남았습니다. 귀환 권고 시각과 베이스캠프 경로를 확인하세요.
TXT
    aplay -D "${SPK}" "${OUT_DIR}/spk_check.wav" || true
    echo "  robotic voice is EXPECTED. espeak-ng is the baseline, not the final engine."
  else
    echo "  espeak-ng not installed:  sudo apt install -y espeak-ng"
  fi

  hr
  echo "DONE (speaker only). If you heard all three, the speaker side PASSED."
  echo
  echo "Put this line into Co-LLM/config.py :"
  echo "    SPK_DEVICE = \"${SPK}\""
  echo
  echo "Nothing was heard?  ->  alsamixer  ->  F6 pick the USB card  ->  M to unmute, raise Master/PCM"
  echo "'Device or resource busy'  ->  pulseaudio -k"
  hr
  exit 0
fi

if [ -z "${MIC}" ]; then
  echo
  echo "  FAIL: no USB capture device found. Plug the mic in, or run:"
  echo "        bash scripts/00_check_audio.sh --spk-only"
  exit 1
fi

if [ "${MIC}" = "${SPK}" ]; then
  echo
  echo "  WARN: mic and speaker resolved to the SAME name."
  echo "        Pass them explicitly:"
  echo "        bash scripts/00_check_audio.sh <mic-name> <spk-name>"
fi

# ---- record -------------------------------------------------------
hr
echo "[6] recording ${SECONDS_TO_REC}s -- SPEAK NOW"
hr
if ! arecord -D "${MIC}" -f S16_LE -r 16000 -c 1 -d "${SECONDS_TO_REC}" "${WAV}"; then
  echo
  echo "  FAIL: arecord failed."
  echo "    'Device or resource busy'  -> pulseaudio -k"
  echo "    'Invalid argument'         -> use plughw: not hw:"
  exit 1
fi

ls -l "${WAV}"

# ---- level check --------------------------------------------------
hr
echo "[7] level check"
hr
if command -v python3 >/dev/null 2>&1; then
  python3 - "${WAV}" <<'PY'
import sys, wave, array
with wave.open(sys.argv[1], "rb") as w:
    data = w.readframes(w.getnframes())
a = array.array("h")
a.frombytes(data)
if len(a) == 0:
    print("  FAIL: empty wav")
    sys.exit(0)
peak = max(max(a), -min(a))
rms = int((sum(x * x for x in a) / len(a)) ** 0.5)
print("  peak = %d / 32767   rms = %d" % (peak, rms))
if peak < 500:
    print("  FAIL: essentially silent. alsamixer -> F6 pick mic card -> F4 capture -> raise gain, press Space")
elif peak > 32000:
    print("  WARN: clipping. lower capture gain in alsamixer")
else:
    print("  OK: level looks usable")
PY
fi

# ---- play back ----------------------------------------------------
hr
echo "[8] playing it back -- you should hear yourself"
hr
if ! aplay -D "${SPK}" "${WAV}"; then
  echo
  echo "  FAIL: aplay failed."
  echo "    no sound but no error -> alsamixer, unmute Master/PCM (press M)"
  echo "    underrun / reboot     -> USB current. lower volume, other port, powered hub"
  exit 1
fi

hr
echo "DONE. If you heard yourself, stage 0 PASSED."
echo
echo "Put these two lines into Co-LLM/config.py :"
echo "    MIC_DEVICE = \"${MIC}\""
echo "    SPK_DEVICE = \"${SPK}\""
hr
