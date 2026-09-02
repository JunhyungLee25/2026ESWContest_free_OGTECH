#!/usr/bin/env bash
# rec.wav -> STT(whisper.cpp) -> text -> TTS(espeak-ng) -> echo.wav
#
# No audio device is touched here. Run it with NOTHING plugged in.
#   bash 01_record.sh          (mic plugged)
#   bash 03_echo.sh            (nothing plugged)
#   bash 02_play.sh test_rec/echo.wav      (speaker plugged)
#
#   bash 03_echo.sh                        # uses test_rec/rec.wav
#   bash 03_echo.sh test_rec/rec_1.wav
#   WHISPER_MODEL=~/ogtech_ai/stt/whisper.cpp/models/ggml-base.bin bash 03_echo.sh
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RES="${HERE}/test_rec"
mkdir -p "${RES}"

IN="${1:-${RES}/rec.wav}"
OUT="${RES}/echo.wav"
TXT="${RES}/echo.txt"

# Shared settings live in stt_prompt.sh so they survive the next login.
# shellcheck source=stt_prompt.sh
. "${HERE}/stt_prompt.sh"

WHISPER_BIN="${WHISPER_BIN:-${OGTECH_STT_BIN}}"
WHISPER_MODEL="${WHISPER_MODEL:-${HOME}/ogtech_ai/stt/whisper.cpp/models/ggml-small.bin}"
WHISPER_THREADS="${WHISPER_THREADS:-${OGTECH_STT_THREADS}}"
ESPEAK_SPEED="${ESPEAK_SPEED:-140}"

# -ng = do NOT use the GPU. This is not a tuning knob:
#   1. ../docs/00_frozen_decisions.md §5 pins STT to the CPU backend (74M params -- kernel launch
#      overhead dominates, and GPU measured 2.7x worse latency).
#   2. Xavier shares one memory pool between "VRAM" and system RAM. Without
#      -ng whisper.cpp took the CUDA path, failed to cudaMalloc 91 MiB and
#      died with SIGSEGV -- printing nothing, which this script used to
#      misreport as an empty transcription (i.e. as a microphone fault).
# -ac 450 and the greedy flags are now the frozen set (../docs/00_frozen_decisions.md §5), so they
# come from stt_prompt.sh rather than being retyped. Override for one run:
#   WHISPER_FLAGS="-ng" bash 03_echo.sh
WHISPER_FLAGS="${WHISPER_FLAGS:-${OGTECH_STT_FLAGS}}"

# Domain vocabulary hint. Kept OUT of WHISPER_FLAGS because that variable is
# deliberately word-split, which would shred a phrase containing spaces.
# Biases the decoder toward wilderness nouns (목적지 was decoded as 목격체).
# Costs a handful of decoder tokens, not encoder time.
# Default is the file, so it applies without being typed. '+set' not ':-':
# WHISPER_PROMPT="" must mean "no prompt", not "fall back to the file".
if [ "${WHISPER_PROMPT+set}" != "set" ]; then
  WHISPER_PROMPT="$(ogtech_read_prompt "$(ogtech_prompt_file)")" || WHISPER_PROMPT=""
fi
PROMPT_ARGS=()
[ -n "${WHISPER_PROMPT}" ] && PROMPT_ARGS=(--prompt "${WHISPER_PROMPT}")

# ---- preflight ----------------------------------------------------
if [ ! -f "${IN}" ]; then
  echo "FAIL: no input wav: ${IN}"
  echo "  run 'bash 01_record.sh' first (mic plugged in)"
  exit 1
fi

if [ ! -x "${WHISPER_BIN}" ]; then
  # older builds name it 'main'
  ALT="$(dirname "${WHISPER_BIN}")/main"
  if [ -x "${ALT}" ]; then
    WHISPER_BIN="${ALT}"
  else
    echo "FAIL: whisper.cpp binary not found:"
    echo "  ${WHISPER_BIN}"
    echo "  ${ALT}"
    echo "  build it first -- see 02_install_a_to_z.md step 2, or override:"
    echo "  WHISPER_BIN=/path/to/whisper-cli bash 03_echo.sh"
    exit 1
  fi
fi

if [ ! -f "${WHISPER_MODEL}" ]; then
  echo "FAIL: model not found: ${WHISPER_MODEL}"
  echo "  cd ~/ogtech_ai/stt/whisper.cpp && bash ./models/download-ggml-model.sh small"
  exit 1
fi

if ! command -v espeak-ng >/dev/null 2>&1; then
  echo "FAIL: sudo apt install -y espeak-ng"
  exit 1
fi

echo "in     : ${IN}"
echo "model  : $(basename "${WHISPER_MODEL}")"
echo "binary : $(basename "${WHISPER_BIN}")"

# ---- 1. STT -------------------------------------------------------
echo "flags  : ${WHISPER_FLAGS}"
[ -n "${WHISPER_PROMPT}" ] && echo "prompt : ${WHISPER_PROMPT}"

t0=$(date +%s%N)
# shellcheck disable=SC2086  -- WHISPER_FLAGS is meant to word-split
RAW="$("${WHISPER_BIN}" -m "${WHISPER_MODEL}" -f "${IN}" \
        -l ko -t "${WHISPER_THREADS}" ${WHISPER_FLAGS} "${PROMPT_ARGS[@]}" \
        -nt -np 2>"${RES}/_whisper_err.log")"
RC=$?
t1=$(date +%s%N)

# A crash also produces no stdout. Report THAT, not "empty transcription" --
# blaming the microphone for a SIGSEGV sends the whole investigation the
# wrong way. 139 = 128 + SIGSEGV.
if [ "${RC}" -ne 0 ]; then
  echo "FAIL: whisper-cli exited ${RC} (it crashed; it did not transcribe)."
  [ "${RC}" -eq 139 ] && echo "  139 = SIGSEGV. Usually CUDA OOM on this board."
  echo "  last lines of ${RES}/_whisper_err.log :"
  tail -n 5 "${RES}/_whisper_err.log" | sed 's/^/    /'
  echo
  echo "  'cudaMalloc failed' or 'NvMapMemAllocInternalTagged: error 12'"
  echo "  -> the GPU path is being used. Keep -ng in WHISPER_FLAGS."
  exit 1
fi

# drop blank/noise markers and squeeze whitespace
HEARD="$(printf '%s\n' "${RAW}" \
  | grep -vxE '\s*(\[BLANK_AUDIO\]|\[BLANK\]|\[음악\]|\(음악\)|\[박수\]|\(박수\))\s*' \
  | tr '\n' ' ' | sed 's/  */ /g; s/^ //; s/ $//')"

echo "STT    : $(( (t1 - t0) / 1000000 )) ms"

if [ -z "${HEARD}" ]; then
  echo "FAIL: empty transcription."
  echo "  1) listen to the recording yourself:  bash 02_play.sh ${IN}"
  echo "  2) if you cannot understand it either, it is a MIC problem, not STT"
  echo "     -> alsamixer, capture gain, speak within 20 cm"
  echo "  3) whisper stderr: ${RES}/_whisper_err.log"
  exit 1
fi

printf '%s\n' "${HEARD}" > "${TXT}"
echo "heard  : ${HEARD}"

# ---- 2. TTS -------------------------------------------------------
t2=$(date +%s%N)
espeak-ng -v ko -s "${ESPEAK_SPEED}" -w "${OUT}" --stdin < "${TXT}"
t3=$(date +%s%N)

echo "TTS    : $(( (t3 - t2) / 1000000 )) ms"
echo "total  : $(( (t3 - t0) / 1000000 )) ms   (STT + TTS, playback excluded)"
echo "out    : ${OUT}"
echo
echo "NEXT: plug the speaker, then"
echo "  bash 02_play.sh ${OUT}        # what the machine understood"
echo "  bash 02_play.sh ${IN}         # your original voice, for comparison"
