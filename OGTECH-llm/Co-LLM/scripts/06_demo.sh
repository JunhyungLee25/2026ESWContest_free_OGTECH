#!/usr/bin/env bash
# OGTECH demo, one shot:  record N seconds -> STT -> TTS -> speaker.
#
# This is 01_record.sh + 03_echo.sh + 02_play.sh collapsed into one command.
# Mic AND speaker stay plugged in at the same time -- they are two different
# USB cards ('Device' and 'UACDemoV10'), so nothing is swapped between steps.
#
#   bash 06_demo.sh                 # record 4 s
#   bash 06_demo.sh 6               # record 6 s   <- seconds is the 1st argument
#   bash 06_demo.sh 4 --say "해가 지기까지 40분 남았습니다. 귀환 권고 시각과 베이스캠프 경로를 확인하세요."
#   bash 06_demo.sh 4 --no-prompt   # domain prompt OFF, for an A/B take
#   bash 06_demo.sh 4 --keep        # keep demo_0001.wav.. instead of overwriting
#   bash 06_demo.sh 4 plughw:CARD=Device,DEV=0 plughw:CARD=UACDemoV10,DEV=0
#
#   WHISPER_PROMPT_FILE=stt_prompt_s.txt bash 06_demo.sh 4    # short prompt
#   OGTECH_STT_MODEL=~/ogtech_ai/stt/whisper.cpp/models/ggml-small.bin \
#     bash 06_demo.sh 4                                       # small instead of base
#
# --say speaks a fixed sentence instead of what was heard. That is the 경로 B
# shape (검수된 고정 카드 -> TTS), except the operator picks the card by hand:
# the keyword gate itself is still open question #9, so this script does NOT
# pretend to route. Without --say it echoes the transcription, which is what
# proves the STT prompt is doing its job.
#
# Prompt, flags, model and binary all come from stt_prompt.sh + stt_prompt.txt
# in this folder. Nothing is typed per run and the setting survives a reboot.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RES="${HERE}/test_rec"
mkdir -p "${RES}"

# ---- arguments ----------------------------------------------------
SEC=""
SAY=""
USE_PROMPT=1
KEEP=0
MIC_DEV=""
SPK_DEV=""

while [ $# -gt 0 ]; do
  case "$1" in
    --say)        [ $# -ge 2 ] || { echo "FAIL: --say needs a sentence"; exit 1; }
                  SAY="$2"; shift; shift ;;
    --no-prompt)  USE_PROMPT=0; shift ;;
    --keep)       KEEP=1; shift ;;
    -h|--help)    sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    plughw:*|hw:*|default|pulse|sysdefault)
                  # first device-looking argument is the mic, second the speaker
                  if [ -z "${MIC_DEV}" ]; then MIC_DEV="$1"; else SPK_DEV="$1"; fi
                  shift ;;
    *[!0-9.]*)    echo "FAIL: unknown argument: $1"; exit 1 ;;
    *)            SEC="$1"; shift ;;
  esac
done
SEC="${SEC:-4}"

ESPEAK_SPEED="${ESPEAK_SPEED:-140}"
BUDGET_MS=2000          # 경로 B budget, ../docs/00_frozen_decisions.md §4

# ---- settings (file, not env -- survives the next login) -----------
# shellcheck source=stt_prompt.sh
. "${HERE}/stt_prompt.sh"

WHISPER_BIN="${OGTECH_STT_BIN}"
WHISPER_MODEL="${OGTECH_STT_MODEL}"

# WHISPER_PROMPT (env) wins over the file. '+set' not ':-' on purpose:
# WHISPER_PROMPT="" must mean "no prompt", not "fall back to the file".
PROMPT=""
if [ "${USE_PROMPT}" = "1" ]; then
  PFILE="$(ogtech_prompt_file)"
  if [ "${WHISPER_PROMPT+set}" = "set" ]; then
    PROMPT="${WHISPER_PROMPT}"
    PFILE="(env WHISPER_PROMPT)"
  else
    PROMPT="$(ogtech_read_prompt "${PFILE}")" || {
      echo "FAIL: prompt file not found: ${PFILE}"
      echo "  put stt_prompt.txt next to this script, or use --no-prompt"
      exit 1
    }
  fi
else
  PFILE="(--no-prompt)"
fi
PROMPT_ARGS=()
[ -n "${PROMPT}" ] && PROMPT_ARGS=(--prompt "${PROMPT}")

# ---- devices ------------------------------------------------------
# Whitelist USB cards via /proc/asound instead of blacklisting names.
# The blacklist 'grep -viE hdmi|tegra' did NOT catch this board's built-ins:
# HDMI audio is card name 'HDA' and the onboard DSP is 'APE'. Recording on
# APE yields pure silence; playing on HDA is silent unless the monitor has
# speakers. 'usbid' exists only for USB cards; pcm*c = capture, pcm*p = play.
pick_usb() {   # $1 = c | p
  local D
  for D in /proc/asound/card*/; do
    [ -f "${D}usbid" ] || continue
    [ -f "${D}id" ] || continue
    ls -d "${D}"pcm*"$1" >/dev/null 2>&1 || continue
    printf 'plughw:CARD=%s,DEV=0' "$(cat "${D}id")"
    return 0
  done
  return 1
}

[ -n "${MIC_DEV}" ] || MIC_DEV="$(pick_usb c)" || true
[ -n "${SPK_DEV}" ] || SPK_DEV="$(pick_usb p)" || true

if [ -z "${MIC_DEV}" ]; then
  echo "FAIL: no USB capture device. Plug the mic in."
  echo "  check:  lsusb ; arecord -l ; dmesg | tail -20"
  exit 1
fi
if [ -z "${SPK_DEV}" ]; then
  echo "FAIL: no USB playback device. Plug the speaker in."
  echo "  HDMI (CARD=HDA) is excluded on purpose -- it is silent here."
  echo "  check:  lsusb ; aplay -l"
  exit 1
fi

# ---- preflight ----------------------------------------------------
if [ ! -x "${WHISPER_BIN}" ]; then
  ALT="$(dirname "${WHISPER_BIN}")/main"    # older builds name it 'main'
  if [ -x "${ALT}" ]; then
    WHISPER_BIN="${ALT}"
  else
    echo "FAIL: whisper.cpp binary not found:"
    echo "  ${WHISPER_BIN}"
    echo "  ${ALT}"
    echo "  build it (02_install_a_to_z.md step 2) or set OGTECH_STT_BIN"
    exit 1
  fi
fi
[ -f "${WHISPER_MODEL}" ] || {
  echo "FAIL: model not found: ${WHISPER_MODEL}"
  echo "  cd ~/ogtech_ai/stt/whisper.cpp && bash ./models/download-ggml-model.sh base"
  exit 1
}
command -v espeak-ng >/dev/null 2>&1 || { echo "FAIL: sudo apt install -y espeak-ng"; exit 1; }

# ---- filenames ----------------------------------------------------
if [ "${KEEP}" = "1" ]; then
  N=1
  while [ -f "$(printf '%s/demo_%04d.wav' "${RES}" "${N}")" ]; do N=$((N + 1)); done
  IN="$(printf '%s/demo_%04d.wav' "${RES}" "${N}")"
  OUT="$(printf '%s/demo_%04d_say.wav' "${RES}" "${N}")"
else
  IN="${RES}/demo.wav"
  OUT="${RES}/demo_say.wav"
fi
TXT="${RES}/demo.txt"

echo "------------------------------------------------------------"
echo "mic    : ${MIC_DEV}"
echo "spk    : ${SPK_DEV}"
echo "model  : $(basename "${WHISPER_MODEL}")   flags: ${OGTECH_STT_FLAGS} -t ${OGTECH_STT_THREADS}"
if [ -n "${PROMPT}" ]; then
  echo "prompt : $(basename "${PFILE}")"
  ogtech_prompt_stat "${PROMPT}" | sed 's/^/         /'
  echo "         ${PROMPT}"
else
  echo "prompt : none  ${PFILE}"
fi
echo "------------------------------------------------------------"

# ---- 1. record ----------------------------------------------------
echo ">> SPEAK NOW -- ${SEC}s, mic 10-15 cm away"
ERR="${RES}/_arecord_err.log"
: > "${ERR}"
USED=""
for D in "${MIC_DEV}" "default" "pulse" "sysdefault"; do
  echo "--- try ${D}" >> "${ERR}"
  if arecord -D "${D}" -f S16_LE -r 16000 -c 1 -d "${SEC}" -q "${IN}" 2>>"${ERR}"; then
    USED="${D}"; break
  fi
done
T_REC_END=$(date +%s%N)

if [ -z "${USED}" ]; then
  echo "FAIL: recording failed on every device. arecord said:"
  sed 's/^/  /' "${ERR}"
  echo "  who holds the card?  fuser -v /dev/snd/*      release:  pulseaudio -k"
  exit 1
fi
echo ">> recorded (${USED})"

# rms, not just peak: rms 1681 already smeared consonants (텐트 -> 헨트) [실측],
# and rms >= 3000 is where that stopped. Gain is already at max (numid=3 = 16),
# so the only remaining lever is speaking closer / louder.
python3 - "${IN}" <<'PY'
import sys, wave, array
with wave.open(sys.argv[1], "rb") as w:
    a = array.array("h"); a.frombytes(w.readframes(w.getnframes()))
if not len(a):
    print("   level : FAIL empty wav"); sys.exit(0)
peak = max(max(a), -min(a))
rms = int((sum(x * x for x in a) / len(a)) ** 0.5)
tag = "ok"
if peak < 500:      tag = "SILENT -- amixer -c 0 cset numid=3 16"
elif peak > 32000:  tag = "CLIPPING -- back off"
elif rms < 3000:    tag = "WEAK -- speak closer/louder, consonants will smear"
print("   level : peak %d/32767  rms %d  %s" % (peak, rms, tag))
PY

# ---- 2. STT -------------------------------------------------------
t0=$(date +%s%N)
# shellcheck disable=SC2086  -- OGTECH_STT_FLAGS is meant to word-split
RAW="$("${WHISPER_BIN}" -m "${WHISPER_MODEL}" -f "${IN}" \
        -l ko -t "${OGTECH_STT_THREADS}" ${OGTECH_STT_FLAGS} "${PROMPT_ARGS[@]}" \
        -nt -np 2>"${RES}/_whisper_err.log")"
RC=$?
t1=$(date +%s%N)

# A crash also prints nothing. Report THAT, not "empty transcription" --
# blaming the mic for a SIGSEGV sent a whole investigation the wrong way once.
if [ "${RC}" -ne 0 ]; then
  echo "FAIL: whisper-cli exited ${RC} (it crashed; it did not transcribe)."
  [ "${RC}" -eq 139 ] && echo "  139 = SIGSEGV, usually CUDA OOM. Keep -ng in OGTECH_STT_FLAGS."
  tail -n 5 "${RES}/_whisper_err.log" | sed 's/^/    /'
  exit 1
fi

HEARD="$(printf '%s\n' "${RAW}" \
  | grep -vxE '\s*(\[BLANK_AUDIO\]|\[BLANK\]|\[음악\]|\(음악\)|\[박수\]|\(박수\))\s*' \
  | tr '\n' ' ' | sed 's/  */ /g; s/^ //; s/ $//')"
MS_STT=$(( (t1 - t0) / 1000000 ))

if [ -z "${HEARD}" ]; then
  echo "FAIL: empty transcription (exit 0, so whisper ran and heard nothing)."
  echo "  listen to it yourself:  aplay -D ${SPK_DEV} ${IN}"
  echo "  stderr: ${RES}/_whisper_err.log"
  exit 1
fi
printf '%s\n' "${HEARD}" > "${TXT}"

# ---- 3. TTS -------------------------------------------------------
# espeak-ng is the fallback engine, not the final one: listeners could not
# make out the Korean [실측]. Engine choice is open question #2.
SPEAK="${SAY:-${HEARD}}"
t2=$(date +%s%N)
printf '%s\n' "${SPEAK}" | espeak-ng -v ko -s "${ESPEAK_SPEED}" -w "${OUT}" --stdin
t3=$(date +%s%N)
MS_TTS=$(( (t3 - t2) / 1000000 ))

# ---- 4. play ------------------------------------------------------
# The measured span is 'record end -> first sound', because that is the part
# the user actually waits through (../docs/00_frozen_decisions.md §5 측정 기준).
T_FIRST=$(date +%s%N)
MS_TOTAL=$(( (T_FIRST - T_REC_END) / 1000000 ))

PERR="${RES}/_aplay_err.log"
: > "${PERR}"
PUSED=""
for D in "${SPK_DEV}" "default" "pulse" "sysdefault"; do
  echo "--- try ${D}" >> "${PERR}"
  if aplay -D "${D}" -q "${OUT}" 2>>"${PERR}"; then PUSED="${D}"; break; fi
done
if [ -z "${PUSED}" ]; then
  echo "FAIL: playback failed on every device. aplay said:"
  sed 's/^/  /' "${PERR}"
  echo "  'Invalid argument' -> use plughw:, not hw:"
  exit 1
fi

# ---- 5. verdict ---------------------------------------------------
VERDICT="OK"
[ "${MS_TOTAL}" -gt "${BUDGET_MS}" ] && VERDICT="OVER"
echo "------------------------------------------------------------"
echo " heard : ${HEARD}"
[ -n "${SAY}" ] && echo " spoke : ${SPEAK}   (--say, fixed card)"
printf ' STT %d ms   TTS %d ms   rec-end -> first sound %d ms   budget %d ms   %s\n' \
  "${MS_STT}" "${MS_TTS}" "${MS_TOTAL}" "${BUDGET_MS}" "${VERDICT}"
echo " wav   : ${IN}"
echo "------------------------------------------------------------"
if [ "${VERDICT}" = "OVER" ]; then
  echo " over budget. judge by the MAX over 20 takes, not the median -- run-to-run"
  echo " spread is up to 1.7x [실측], so one slow take is the number that counts."
fi
