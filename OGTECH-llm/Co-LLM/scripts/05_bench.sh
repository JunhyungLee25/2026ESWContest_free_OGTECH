#!/usr/bin/env bash
# Measure latency + accuracy(keyword gate) + power + thermal in ONE run.
# No audio device is touched. Run with nothing plugged in.
#
#   bash 04_record_set.sh                      # record the 6 utterances first
#
#   bash 05_bench.sh base_cpu   ~/ogtech_ai/stt/whisper.cpp/models/ggml-base.bin  -ng -ac 600 -bo 1 -bs 1
#   bash 05_bench.sh small_cpu  ~/ogtech_ai/stt/whisper.cpp/models/ggml-small.bin -ng -ac 600 -bo 1 -bs 1
#   bash 05_bench.sh base_gpu   ~/ogtech_ai/stt/whisper.cpp/models/ggml-base.bin      -ac 600 -bo 1 -bs 1
#   bash 05_bench.sh small_gpu  ~/ogtech_ai/stt/whisper.cpp/models/ggml-small.bin     -ac 600 -bo 1 -bs 1
#
# CLOCKS=1 applies the burst clock policy (jetson_clocks on -> run -> restore):
#   CLOCKS=1 bash 05_bench.sh base_cpu_clk ~/.../ggml-base.bin -ng -ac 600 -bo 1 -bs 1
#
# Output: test_rec/bench_runs.csv  (one row per utterance)
#         test_rec/bench_power.csv (one row per label)
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RES="${HERE}/test_rec"
mkdir -p "${RES}"

if [ $# -lt 2 ]; then
  echo "usage: bash 05_bench.sh <label> <model.bin> [whisper args...]"
  exit 1
fi

LABEL="$1"; shift
MODEL="$(eval echo "$1")"; shift
EXTRA="$*"

# Kept OUT of the positional args because those are word-split, which would
# shred a prompt containing spaces. Measured: on ggml-base this is what turns
# '목격체' back into '목적지', for +87 ms [실측].
WHISPER_PROMPT="${WHISPER_PROMPT:-}"

# WHISPER_PROMPT_FILE measures the shipped prompt without pasting 14 sentences
# into the shell:
#   WHISPER_PROMPT_FILE=stt_prompt.txt bash 05_bench.sh base_L ~/.../ggml-base.bin ...
# Deliberately NOT defaulted, unlike 03_echo.sh / 06_demo.sh: 'base_none' has to
# stay a clean baseline, and a prompt applied silently is exactly how two
# measurement rounds were lost on 08-05.
if [ -z "${WHISPER_PROMPT}" ] && [ -n "${WHISPER_PROMPT_FILE:-}" ]; then
  # shellcheck source=stt_prompt.sh
  . "${HERE}/stt_prompt.sh"
  WHISPER_PROMPT="$(ogtech_read_prompt "$(ogtech_prompt_file)")" || {
    echo "FAIL: prompt file not found: ${WHISPER_PROMPT_FILE}"; exit 1; }
fi

PROMPT_ARGS=()
[ -n "${WHISPER_PROMPT}" ] && PROMPT_ARGS=(--prompt "${WHISPER_PROMPT}")

BIN="${WHISPER_BIN:-${HOME}/ogtech_ai/stt/whisper.cpp/build/bin/whisper-cli}"
[ -x "${BIN}" ] || BIN="$(dirname "${BIN}")/main"
IDLE_SEC="${IDLE_SEC:-15}"
CLOCKS="${CLOCKS:-0}"

RUNS_CSV="${RES}/bench_runs.csv"
POWER_CSV="${RES}/bench_power.csv"
EXPECT="${RES}/ko_expected.tsv"

# ---- preflight ----------------------------------------------------
[ -x "${BIN}" ]    || { echo "FAIL: whisper binary not found: ${BIN}"; exit 1; }
[ -f "${MODEL}" ]  || { echo "FAIL: model not found: ${MODEL}"; exit 1; }
[ -f "${EXPECT}" ] || { echo "FAIL: run 'bash 04_record_set.sh' first"; exit 1; }
ls "${RES}"/ko_1.wav >/dev/null 2>&1 || { echo "FAIL: no ko_*.wav. run 04_record_set.sh"; exit 1; }

echo "label  : ${LABEL}"
echo "model  : $(basename "${MODEL}")"
echo "args   : ${EXTRA:-（none）}"
echo "clocks : $([ "${CLOCKS}" = "1" ] && echo 'burst (jetson_clocks on/off)' || echo 'as-is')"
[ -n "${WHISPER_PROMPT}" ] && echo "prompt : ${WHISPER_PROMPT}"

# Utterance count comes from the expected file, not a hardcoded 6.
N_UTT="$(wc -l < "${EXPECT}")"

echo
echo "sudo is needed for tegrastats. Caching credentials..."
sudo -v || { echo "FAIL: sudo required"; exit 1; }

# ---- 1. idle baseline ---------------------------------------------
echo "[1/4] idle baseline ${IDLE_SEC}s -- do not touch the machine"
sudo pkill tegrastats 2>/dev/null
# tegrastats APPENDS to --logfile, and creates it as root. A plain ': >' fails
# with Permission denied on the 2nd run, silently mixing in run N-1 samples.
sudo rm -f "${RES}/_tg_idle.log" "${RES}/_tg_run.log"
sudo tegrastats --interval 200 --logfile "${RES}/_tg_idle.log" &
sleep "${IDLE_SEC}"
sudo pkill tegrastats 2>/dev/null
sleep 1

# ---- 2. burst clocks on -------------------------------------------
# jetson_clocks --restore needs a stored snapshot. Store the CURRENT state
# first, otherwise restore fails and the board stays pinned at max forever.
if [ "${CLOCKS}" = "1" ]; then
  # NOTE: plain '[ -f /root/... ]' always says "missing" -- a normal user cannot
  # stat /root. Must test with sudo, or jetson_clocks --store prompts Y/N.
  if ! sudo test -f /root/.jetsonclocks_conf.txt; then
    echo "  storing current clock state for restore..."
    sudo jetson_clocks --store
  fi
  sudo jetson_clocks
fi

# ---- 3. batch inference with power logging ------------------------
echo "[2/4] batch inference (${N_UTT} utterances) with power logging"
sudo tegrastats --interval 200 --logfile "${RES}/_tg_run.log" &
T_START=$(date +%s%N)

TMP_ERR="${RES}/_werr.log"
PASS=0
FP=0
TOTALS=""

for i in $(seq 1 "${N_UTT}"); do
  WAV="${RES}/ko_${i}.wav"
  [ -f "${WAV}" ] || { echo "  #${i} missing -- skipped"; continue; }

  SAY="$(awk -F'\t' -v n="${i}" '$1==n{print $2}' "${EXPECT}")"
  KEYS="$(awk -F'\t' -v n="${i}" '$1==n{print $3}' "${EXPECT}")"
  NEGS="$(awk -F'\t' -v n="${i}" '$1==n{print $4}' "${EXPECT}")"

  # shellcheck disable=SC2086  -- EXTRA is meant to word-split
  TEXT="$("${BIN}" -m "${MODEL}" -f "${WAV}" -l ko -nt ${EXTRA} "${PROMPT_ARGS[@]}" 2>"${TMP_ERR}" | tr '\n' ' ' | sed 's/  */ /g; s/^ //; s/ $//')"

  LD=$(grep -oE 'load time =[ ]*[0-9.]+'   "${TMP_ERR}" | grep -oE '[0-9.]+' | head -1)
  EN=$(grep -oE 'encode time =[ ]*[0-9.]+' "${TMP_ERR}" | grep -oE '[0-9.]+' | head -1)
  TO=$(grep -oE 'total time =[ ]*[0-9.]+'  "${TMP_ERR}" | grep -oE '[0-9.]+' | head -1)
  LD="${LD:-0}"; EN="${EN:-0}"; TO="${TO:-0}"
  TOTALS="${TOTALS} ${TO}"

  # recall: did any expected keyword survive the transcription?
  HIT="X"
  IFS=',' read -r -a KARR <<< "${KEYS}"
  for k in "${KARR[@]}"; do
    [ -n "${k}" ] || continue
    if printf '%s' "${TEXT}" | grep -qF "${k}"; then HIT="O"; break; fi
  done
  [ "${HIT}" = "O" ] && PASS=$((PASS + 1))

  # precision: did a misrecognition invent a keyword belonging to another
  # label? This is the metric open question #10 asks for -- recall alone
  # cannot see '이 물' decoded as '이불' routing a water question elsewhere.
  BAD=""
  IFS=',' read -r -a NARR <<< "${NEGS}"
  for k in "${NARR[@]}"; do
    [ -n "${k}" ] || continue
    if printf '%s' "${TEXT}" | grep -qF "${k}"; then BAD="${BAD}${k} "; fi
  done
  if [ -n "${BAD}" ]; then FP=$((FP + 1)); fi

  printf '  #%-2d %s  total %7s ms  (load %s / encode %s)%s\n' \
    "${i}" "${HIT}" "${TO}" "${LD}" "${EN}" \
    "$([ -n "${BAD}" ] && echo "   FALSE-POSITIVE: ${BAD}")"
  printf '      said : %s\n' "${SAY}"
  printf '      heard: %s\n' "${TEXT:-(empty)}"

  if [ ! -f "${RUNS_CSV}" ]; then
    echo 'label,model,args,prompt,idx,gate,false_pos,ms_load,ms_encode,ms_total,said,heard' > "${RUNS_CSV}"
  fi
  printf '%s,%s,"%s","%s",%d,%s,"%s",%s,%s,%s,"%s","%s"\n' \
    "${LABEL}" "$(basename "${MODEL}")" "${EXTRA}" "${WHISPER_PROMPT}" "${i}" "${HIT}" \
    "${BAD}" "${LD}" "${EN}" "${TO}" "${SAY}" "${TEXT}" >> "${RUNS_CSV}"
done

T_END=$(date +%s%N)
sudo pkill tegrastats 2>/dev/null
sleep 1

# ---- 4. clocks restore --------------------------------------------
if [ "${CLOCKS}" = "1" ]; then
  sudo jetson_clocks --restore
fi

# ---- 5. aggregate --------------------------------------------------
echo "[3/4] thermal"
TEMPS="$(paste -d'=' \
  <(cat /sys/devices/virtual/thermal/thermal_zone*/type 2>/dev/null) \
  <(cat /sys/devices/virtual/thermal/thermal_zone*/temp 2>/dev/null) \
  | awk -F= '{printf "%s:%.1fC ", $1, $2/1000}')"
echo "  ${TEMPS}"

echo "[4/4] power"
WALL_MS=$(( (T_END - T_START) / 1000000 ))

python3 - "${RES}/_tg_idle.log" "${RES}/_tg_run.log" "${LABEL}" "${MODEL}" \
         "${EXTRA}" "${WALL_MS}" "${PASS}" "${TEMPS}" "${POWER_CSV}" "${CLOCKS}" \
         "${FP}" "${N_UTT}" "${WHISPER_PROMPT}" \
         ${TOTALS} <<'PY'
import os, re, statistics, sys

(idle_f, run_f, label, model, extra, wall_ms, npass, temps, out_csv, clocks,
 nfp, nutt, prompt) = sys.argv[1:14]
totals = [float(x) for x in sys.argv[14:] if x]

def rails(path):
    v = []
    pat = re.compile(r'VDD_IN (\d+)')
    try:
        with open(path, errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if m:
                    v.append(int(m.group(1)))
    except OSError:
        pass
    return v

iv, rv = rails(idle_f), rails(run_f)
idle = statistics.mean(iv) if iv else 0.0
act = statistics.mean(rv) if rv else 0.0
peak = max(rv) if rv else 0
wall_s = int(wall_ms) / 1000.0
n = max(len(totals), 1)
# energy attributable to the workload, per query
mwh = (act - idle) * wall_s / 3600.0 / n

med = statistics.median(totals) if totals else 0.0
mx = max(totals or [0])
print("  idle   %.0f mW   active %.0f mW   peak %d mW" % (idle, act, peak))
print("  wall   %.1f s over %d queries" % (wall_s, n))
print("  energy %.2f mWh / query   (active - idle)" % mwh)
print()
# max, not median: the demo bar is 20 consecutive successes, so one outlier
# is a failure. A config can win on median and still be unusable.
print("  RESULT  %-12s recall %s/%s   false-pos %s   median %.0f ms   MAX %.0f ms   %.2f mWh/q"
      % (label, npass, nutt, nfp, med, mx, mwh))

new = not os.path.exists(out_csv)
with open(out_csv, "a", encoding="utf-8") as f:
    if new:
        f.write("label,model,args,prompt,clocks,gate_pass,gate_total,false_pos,"
                "ms_median,ms_min,ms_max,"
                "idle_mW,active_mW,peak_mW,wall_s,mWh_per_query,temps\n")
    f.write('%s,%s,"%s","%s",%s,%s,%s,%s,%.0f,%.0f,%.0f,%.0f,%.0f,%d,%.1f,%.3f,"%s"\n' % (
        label, os.path.basename(model), extra, prompt, clocks, npass, nutt, nfp,
        med, min(totals or [0]), mx,
        idle, act, peak, wall_s, mwh, temps.strip()))
print("  -> %s" % out_csv)
PY

echo
echo "runs  : ${RUNS_CSV}"
echo "power : ${POWER_CSV}"
