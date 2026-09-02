#!/usr/bin/env bash
# Shared STT settings. SOURCE this, do not execute it.
#
#   . "$(dirname "${BASH_SOURCE[0]}")/stt_prompt.sh"
#   PROMPT="$(ogtech_read_prompt "$(ogtech_prompt_file)")"
#
# Why a file and not an env var: an env var lives for exactly one command.
# Anything that must still be there after the next ssh login lives here.
# Env vars still win, so a one-off A/B test needs no edit.

OGTECH_STT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Frozen in ../docs/00_frozen_decisions.md §5 'STT 실행 구성'. Not tuning knobs:
#   -ng      GPU path SIGSEGVs on this board (cudaMalloc 91 MiB) [실측]
#   -ac 450  30s mel padding is 81% of the latency. 16,933 -> 1,494 ms [실측]
#            300 is too low: hallucination + a 12.6 s runaway [실측]
#   -bo 1 -bs 1  beam search costs +33% and changed nothing [실측]
#   -nf      no fallback re-decode
#   --vad    P5 bench 'base_cpu_vad' is the selected config: silence-region
#            decoder loops pushed the max to 3,363 ms, VAD brings it to
#            1,495 ms [실측]. 판정은 중앙값이 아니라 최댓값입니다.
# Do not tune one of these at a time. Run-to-run spread is up to 1.7x [실측],
# so a single run cannot tell two configs apart.
#
# The VAD model is a separate download:
#   cd ~/ogtech_ai/stt/whisper.cpp && bash ./models/download-vad-model.sh silero-v5.1.2
# Passing --vad without it makes whisper-cli die on model load, so the flags are
# only added when the file is actually there -- the same rule engines.py uses.
OGTECH_STT_VAD_MODEL="${OGTECH_STT_VAD_MODEL:-${HOME}/ogtech_ai/stt/whisper.cpp/models/ggml-silero-v5.1.2.bin}"

if [ -z "${OGTECH_STT_FLAGS:-}" ]; then
  OGTECH_STT_FLAGS="-ng -ac 450 -bo 1 -bs 1 -nf"
  if [ -f "${OGTECH_STT_VAD_MODEL}" ]; then
    OGTECH_STT_FLAGS="${OGTECH_STT_FLAGS} --vad -vm ${OGTECH_STT_VAD_MODEL}"
  else
    echo "WARN: VAD model not found: ${OGTECH_STT_VAD_MODEL}" >&2
    echo "      Running 후보 B (no VAD). 최댓값이 경로 B 예산 2.0초를 넘길 수 있습니다 [실측]." >&2
    echo "      Fix: cd ~/ogtech_ai/stt/whisper.cpp && bash ./models/download-vad-model.sh silero-v5.1.2" >&2
  fi
fi

# 4 -> 6 threads is only -9% [실측]; 6 is what nproc gives on MODE_15W_6CORE.
OGTECH_STT_THREADS="${OGTECH_STT_THREADS:-6}"

# base, not small: 1,494 ms vs 3,468 ms [실측], and 경로 B has a 2.0 s budget.
# Model choice is still open question #8 -- the 21-utterance bench decides it.
# Override for one run:  OGTECH_STT_MODEL=~/.../ggml-small.bin bash 06_demo.sh
OGTECH_STT_MODEL="${OGTECH_STT_MODEL:-${HOME}/ogtech_ai/stt/whisper.cpp/models/ggml-base.bin}"
OGTECH_STT_BIN="${OGTECH_STT_BIN:-${HOME}/ogtech_ai/stt/whisper.cpp/build/bin/whisper-cli}"

# Which prompt file. Accepts a bare name ('stt_prompt_s.txt'), which is
# resolved next to this script, or an absolute path.
ogtech_prompt_file() {
  local f="${WHISPER_PROMPT_FILE:-${OGTECH_STT_DIR}/stt_prompt.txt}"
  if [ ! -f "${f}" ] && [ -f "${OGTECH_STT_DIR}/${f}" ]; then
    f="${OGTECH_STT_DIR}/${f}"
  fi
  printf '%s' "${f}"
}

# Sentence lines -> one argv string.
#   - '#' lines and blank lines are dropped
#   - CR is stripped: this repo is edited on Windows and copied to the Jetson,
#     and a trailing \r inside --prompt would be passed to the decoder as text
# Returns non-zero if the file is missing, so callers can fail loudly.
ogtech_read_prompt() {
  [ -f "$1" ] || return 1
  tr -d '\r' < "$1" \
    | sed 's/[[:space:]]*#.*$//' \
    | grep -v '^[[:space:]]*$' \
    | tr '\n' ' ' \
    | sed 's/  */ /g; s/^ //; s/ $//'
}

# whisper.cpp caps the initial prompt at n_text_ctx/2 = 224 tokens [실측] and
# drops the overflow silently -- a truncated prompt is not the prompt that was
# measured, and nothing on stdout says so.
#
# The token count cannot be read back from the CLI, so this prints a range
# instead of pretending to know: Korean costs roughly 1.2-1.9 whisper tokens
# per syllable [추정]. It only shouts when even the low end is over the cap.
#
# 'wc -m' is not used: it counts bytes when the ssh session has no UTF-8
# locale, which turns 157 characters into 367 and fires a false alarm.
ogtech_prompt_stat() {
  printf '%s' "$1" | python3 -c '
import re, sys
# .buffer + explicit utf-8: sys.stdin follows the locale, and a session
# without a UTF-8 locale silently mojibakes Korean into a wrong count.
p = sys.stdin.buffer.read().decode("utf-8", "replace")
han = len(re.findall(r"[가-힣]", p))
dot = p.count(".")
lo, hi = int(han * 1.2) + dot * 2, int(han * 1.9) + dot * 2
print("%d sentences, %d chars, ~%d-%d tok of 224 max" % (dot, len(p), lo, hi))
if lo > 224:
    print("  WARN: over the cap for sure. whisper will drop part of it.")
    print("        Trim stt_prompt.txt, or WHISPER_PROMPT_FILE=stt_prompt_s.txt")
'
}
