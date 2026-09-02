#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama-server 사전 점검: health → 프리픽스 토큰 수 → 워밍업 → 샘플 의도. JSON 한 덩어리를 출력한다.

    python3 -m harness.preflight [--policy config/harness_policy.json] [--llm http://127.0.0.1:8080/v1/chat/completions]
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import build_harness


def main() -> int:
    parser = argparse.ArgumentParser(description="OGTECH LLM 하네스 사전 점검")
    parser.add_argument("--policy", default=None)
    parser.add_argument("--llm", default=None, help="chat completions 주소 덮어쓰기")
    parser.add_argument("--sample", default="야간 모드 켜 줘")
    parser.add_argument("--no-warmup", action="store_true")
    args = parser.parse_args()

    harness = build_harness(args.policy, llm_url=args.llm)
    report: dict[str, object] = {"ok": False}
    if harness.client is None or harness.intent is None:
        report["error"] = "intent가 정책에서 꺼져 있습니다"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    report["url"] = harness.client.url
    report["health"] = harness.client.health()
    if not report["health"]:
        report["error"] = "health 실패 — llama-server가 떠 있지 않습니다"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    prefix = "\n".join(m["content"] for m in harness.intent.prefix_messages())
    report["prefix_tokens"] = harness.client.tokenize(prefix)
    report["prefix_chars"] = len(prefix)
    if not args.no_warmup:
        started = time.monotonic()
        warm = harness.warmup()
        report["warmup"] = {
            "ok": bool(warm and warm.ok),
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": None if warm is None else warm.error,
            "usage": {} if warm is None else warm.usage,
        }
    result = harness.intent.resolve(args.sample, pending_confirmation=False)
    report["sample"] = {
        "text": args.sample,
        "raw": None if result.response is None else result.response.raw,
        "scenario_id": result.verdict.scenario_id,
        "map_action": result.verdict.map_action,
        "reason": result.verdict.reason,
        "elapsed_s": None if result.response is None else round(result.response.elapsed_s, 3),
    }
    report["ok"] = bool(result.verdict.accepted)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
