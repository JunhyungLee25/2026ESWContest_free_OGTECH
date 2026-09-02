#!/usr/bin/env bash
# systemd ExecStartPost: llama-server health 대기 후 intent 프리픽스 워밍업을 반드시 성공시킨다.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PORT="${OGTECH_LLM_PORT:-8080}"
URL="http://127.0.0.1:${PORT}/v1/chat/completions"
RESULT_DIR="${ROOT_DIR}/results"
LOG_PATH="${RESULT_DIR}/preflight.log"

mkdir -p "${RESULT_DIR}"

python3 - "${PORT}" <<'PY'
import json
import sys
import time
import urllib.request

port = sys.argv[1]
deadline = time.monotonic() + 120.0
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.0) as response:
            if json.loads(response.read().decode("utf-8")).get("status") == "ok":
                raise SystemExit(0)
    except Exception:
        time.sleep(1.0)
raise SystemExit("llama-server health 대기 120초 초과")
PY

cd "${ROOT_DIR}"
python3 -m harness.preflight --llm "${URL}" >"${LOG_PATH}" 2>&1
