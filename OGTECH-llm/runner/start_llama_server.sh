#!/usr/bin/env bash
# OGTECH llama-server 실행기 (Jetson Xavier NX).
#   bash runner/start_llama_server.sh              # 백그라운드 기동 → health 대기 → 워밍업(preflight)
#   bash runner/start_llama_server.sh --foreground # systemd용. 서버를 전면에서 실행(워밍업은 별도)
#
# 옵션은 config/llama_server.args(동결 §5), 모델·별칭·포트는 환경변수(runner/llm.env.example).
set -eu

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
ARGS_FILE="${OGTECH_LLM_ARGS_FILE:-${ROOT}/config/llama_server.args}"

OGTECH_LLM_BIN="${OGTECH_LLM_BIN:-}"
if [ -z "${OGTECH_LLM_BIN}" ]; then
  if command -v llama-server >/dev/null 2>&1; then
    OGTECH_LLM_BIN="$(command -v llama-server)"
  else
    OGTECH_LLM_BIN="${HOME}/ogtech_ai/llm/llama.cpp/build/bin/llama-server"
  fi
fi
OGTECH_LLM_MODEL_PATH="${OGTECH_LLM_MODEL_PATH:-${HOME}/ogtech_ai/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf}"
OGTECH_LLM_ALIAS="${OGTECH_LLM_ALIAS:-qwen2.5-1.5b-instruct}"
OGTECH_LLM_PORT="${OGTECH_LLM_PORT:-8080}"
OGTECH_LLM_LOG="${OGTECH_LLM_LOG:-${ROOT}/results/llama_server.log}"

FOREGROUND=0
[ "${1:-}" = "--foreground" ] && FOREGROUND=1

[ -x "${OGTECH_LLM_BIN}" ] || { echo "FAIL: llama-server 바이너리가 없습니다: ${OGTECH_LLM_BIN}  (OGTECH_LLM_BIN)"; exit 1; }
[ -f "${OGTECH_LLM_MODEL_PATH}" ] || { echo "FAIL: 모델 파일이 없습니다: ${OGTECH_LLM_MODEL_PATH}  (OGTECH_LLM_MODEL_PATH)"; exit 1; }
[ -f "${ARGS_FILE}" ] || { echo "FAIL: 옵션 파일이 없습니다: ${ARGS_FILE}"; exit 1; }

# '#' 주석과 빈 줄 제거, CR 제거 후 단어 단위로 펼친다.
ARGS=()
while IFS= read -r line; do
  line="${line%%#*}"
  line="$(printf '%s' "${line}" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  [ -z "${line}" ] && continue
  # shellcheck disable=SC2206
  ARGS+=(${line})
done < "${ARGS_FILE}"

CMD=("${OGTECH_LLM_BIN}" -m "${OGTECH_LLM_MODEL_PATH}" --alias "${OGTECH_LLM_ALIAS}" --port "${OGTECH_LLM_PORT}" "${ARGS[@]}")
echo "llama-server: ${CMD[*]}"

if [ "${FOREGROUND}" = "1" ]; then
  exec "${CMD[@]}"
fi

mkdir -p "$(dirname "${OGTECH_LLM_LOG}")"
nohup "${CMD[@]}" >"${OGTECH_LLM_LOG}" 2>&1 &
PID=$!
echo "pid ${PID} · log ${OGTECH_LLM_LOG}"

# health 대기 (최대 120 s — 모델 mmap+mlock 시간)
python3 - "${OGTECH_LLM_PORT}" <<'PY'
import json, sys, time, urllib.request
port = sys.argv[1]
deadline = time.monotonic() + 120
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.0) as r:
            if json.loads(r.read().decode()).get("status") == "ok":
                print("health: ok"); sys.exit(0)
    except Exception:
        pass
    time.sleep(1.0)
print("FAIL: health 대기 초과"); sys.exit(1)
PY

# 워밍업 + 프리픽스 토큰 수 + 샘플 의도 (JSON 출력)
cd "${ROOT}" && python3 -m harness.preflight --llm "http://127.0.0.1:${OGTECH_LLM_PORT}/v1/chat/completions"
