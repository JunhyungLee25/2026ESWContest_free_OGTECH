#!/usr/bin/env bash
# Record the 6 Korean benchmark utterances. Mic only -- speaker not needed.
#   bash 04_record_set.sh          # 5 s each
#   bash 04_record_set.sh 6        # 6 s each
#   bash 04_record_set.sh 5 3      # re-record only #3
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RES="${HERE}/test_rec"
mkdir -p "${RES}"

SEC="${1:-5}"
ONLY="${2:-}"

# Pick the USB card from /proc/asound instead of blacklisting names.
# The old 'grep -viE hdmi|tegra' filter did NOT catch this board's onboard
# DSP, whose card name is 'APE' -- recording there yields pure silence.
# 'usbid' exists only for USB cards; 'pcm*c' means it can capture.
DEV=""
for D in /proc/asound/card*/; do
  [ -f "${D}usbid" ] || continue
  [ -f "${D}id" ] || continue
  ls -d "${D}"pcm*c >/dev/null 2>&1 || continue
  DEV="plughw:CARD=$(cat "${D}id"),DEV=0"
  break
done
if [ -z "${DEV}" ]; then
  echo "FAIL: no USB capture device. Plug the mic in."
  echo "  check: lsusb ; arecord -l ; dmesg | tail -20"
  exit 1
fi
echo "device : ${DEV}"
echo "seconds: ${SEC}"
echo

# utterance | keywords the gate MUST catch | keywords that MUST NOT appear
# Fields are '|' separated; keywords inside a field are ',' separated.
#
# The third field is the false-positive control (open question #10). A word
# landing there means a misrecognition would route the user to the WRONG card:
#   #14 이 물 -> 이불   whisper small actually did this [실측]
#   #17          -> 버섯/열매 would flip a food question into refuse
#   #21 이불     -> 물   the mirror case; the gate must stay silent here
LINES=(
"여기가 어디야|여기,어디|"
"길을 잃은 것 같아|잃,길|"
"왔던 길로 돌아가고 싶어|돌아,길|"
"이 목적지로 가려면 얼마나 남았어|목적지,얼마나|"
"다음 갈림길까지 얼마나 걸려|갈림길,얼마나|"
"해 지기 전에 돌아갈 수 있어|해,지기,돌아|"
"일몰까지 몇 분 남았어|일몰,남았|"
"오늘 밤 얼마나 추워져|밤,추워|"
"너무 추운데 어떻게 해야 돼|추운,어떻게|"
"저체온증 같은데|저체온|"
"텐트 어디에 쳐야 돼|텐트|"
"여기서 자도 안전해|자도,안전|"
"텐트 안에서 버너 켜도 돼|버너,텐트|"
"이 물 마셔도 돼|물,마셔|이불"
"이 버섯 먹어도 돼|버섯,먹|"
"이 열매 먹을 수 있어|열매,먹|"
"먹을 거 다 떨어졌어|먹을,떨어|버섯,열매"
"멧돼지 소리가 나는데|멧돼지|"
"발목을 삐었어|발목,삐|"
"배터리 얼마나 남았어|배터리,남았|"
"이불 챙겨왔어야 했는데|이불|물,마셔"
)
TOTAL=${#LINES[@]}

: > "${RES}/ko_expected.tsv"
i=0
for row in "${LINES[@]}"; do
  i=$((i + 1))
  rest="${row#*|}"
  say="${row%%|*}"
  keys="${rest%%|*}"
  negs="${rest#*|}"
  printf '%d\t%s\t%s\t%s\n' "${i}" "${say}" "${keys}" "${negs}" >> "${RES}/ko_expected.tsv"

  if [ -n "${ONLY}" ] && [ "${ONLY}" != "${i}" ]; then
    continue
  fi

  wav="${RES}/ko_${i}.wav"
  echo "------------------------------------------------------------"
  echo "[${i}/${TOTAL}]  say:  ${say}"
  # 10-15 cm, not 20: at 20 cm this mic measured rms ~1000, which whisper
  # decoded as garbage. rms >= 3000 is where syllables stopped smearing.
  echo "         mic 10-15 cm away, start speaking immediately."
  read -r -p "         press Enter to record ${SEC}s..." _

  if ! arecord -D "${DEV}" -f S16_LE -r 16000 -c 1 -d "${SEC}" -q "${wav}" 2>/dev/null; then
    for D in "default" "pulse"; do
      arecord -D "${D}" -f S16_LE -r 16000 -c 1 -d "${SEC}" -q "${wav}" 2>/dev/null && break
    done
  fi

  if [ ! -f "${wav}" ]; then
    echo "         FAIL: recording failed. 'pulseaudio -k' then retry."
    exit 1
  fi

  python3 - "${wav}" <<'PY'
import sys, wave, array
with wave.open(sys.argv[1], "rb") as w:
    data = w.readframes(w.getnframes())
a = array.array("h"); a.frombytes(data)
peak = max(max(a), -min(a)) if len(a) else 0
print("         level: peak %d/32767  %s" % (
    peak,
    "SILENT -- raise capture gain in alsamixer (F6 -> F4 -> Space)" if peak < 500
    else ("CLIPPING -- lower gain" if peak > 32000 else "ok")))
PY
done

echo "------------------------------------------------------------"
ls -l "${RES}"/ko_*.wav 2>/dev/null
echo
echo "NEXT: unplug the mic (nothing needs to be plugged), then"
echo "  bash 05_bench.sh base ~/ogtech_ai/stt/whisper.cpp/models/ggml-base.bin -ng -ac 450 -bo 1 -bs 1 -nf"
echo "  WHISPER_PROMPT=\"오지 등산 상황입니다. 목적지, 트레일, 능선, 계곡, 일몰, 야영, 텐트, 식수, 버섯, 저체온, 방향, 거리\" \\"
echo "    bash 05_bench.sh base_prompt ~/ogtech_ai/stt/whisper.cpp/models/ggml-base.bin -ng -ac 450 -bo 1 -bs 1 -nf"
