#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 의도 호출 지연 실측 — 프리픽스 토큰 수, cold(워밍업) 1회, warm N회 최댓값·중앙값.

    python3 eval/latency_bench.py --llm http://127.0.0.1:8080/v1/chat/completions --runs 20 --output results/latency_intent_jetson.json

판정은 중앙값이 아니라 최댓값(동결 §5 측정 기준). 실제 서버의 timings(prompt_ms/predicted_ms)가 있으면 함께 기록한다.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness import build_harness  # noqa: E402
from harness.mock_llm_server import start_in_thread  # noqa: E402

DEFAULT_TEXTS = (
    "야간 모드로 바꿔 줘",
    "베이스캠프까지 얼마나 남았어",
    "이 상황에서 무엇을 먼저 살펴보면 좋을까",
    "텐트 안에서 버너 켜도 될까",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="OGTECH LLM 의도 지연 벤치")
    parser.add_argument("--llm", default="mock")
    parser.add_argument("--policy", default=None)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--text", action="append", default=None)
    parser.add_argument("--budget-s", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    texts = tuple(args.text) if args.text else DEFAULT_TEXTS

    mock = None
    llm_url = args.llm
    if args.llm == "mock":
        mock = start_in_thread(mode="ok")
        llm_url = mock.url
    try:
        harness = build_harness(args.policy, llm_url=llm_url, intent_enabled=True, polish_mode="off")
        intent, client = harness.intent, harness.client
        assert intent is not None and client is not None
        if not client.health():
            raise SystemExit("health 실패 — llama-server가 떠 있지 않습니다")
        prefix = "\n".join(m["content"] for m in intent.prefix_messages())
        prefix_tokens = client.tokenize(prefix)
        started = time.monotonic()
        warm = intent.warmup()
        cold_s = time.monotonic() - started
        samples = []
        for index in range(args.runs):
            text = texts[index % len(texts)]
            result = intent.resolve(text, pending_confirmation=False)
            response = result.response
            samples.append(
                {
                    "text": text,
                    "elapsed_s": round(response.elapsed_s, 3) if response else None,
                    "ok": bool(response and response.ok),
                    "raw": response.raw if response else None,
                    "usage": response.usage if response else {},
                    "timings": response.timings if response else {},
                    "verdict": [result.verdict.scenario_id, result.verdict.map_action, result.verdict.reason],
                }
            )
        elapsed = [s["elapsed_s"] for s in samples if s["elapsed_s"] is not None]
        report = {
            "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "llm": "mock" if mock else args.llm,
            "evidence": "mock — 수치 무의미" if mock else "실제 llama-server 실측",
            "prefix_tokens": prefix_tokens,
            "prefix_chars": len(prefix),
            "cold_warmup_s": round(cold_s, 3),
            "cold_warmup_ok": bool(warm.ok),
            "cold_usage": warm.usage,
            "runs": len(samples),
            "warm_max_s": round(max(elapsed), 3) if elapsed else None,
            "warm_median_s": round(statistics.median(elapsed), 3) if elapsed else None,
            "budget_s": args.budget_s,
            "within_budget": bool(elapsed) and max(elapsed) <= args.budget_s,
            "errors": sum(1 for s in samples if not s["ok"]),
            "samples": samples,
        }
    finally:
        if mock is not None:
            mock.shutdown()
            mock.server_close()
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(f"LLM={report['llm']} ({report['evidence']})")
    print(f"프리픽스 {report['prefix_tokens']} tok / {report['prefix_chars']} chars · cold 워밍업 {report['cold_warmup_s']} s (ok={report['cold_warmup_ok']})")
    print(f"warm {report['runs']}회: 최댓값 {report['warm_max_s']} s · 중앙값 {report['warm_median_s']} s · 예산 {args.budget_s} s → {'통과' if report['within_budget'] else '초과'} · 오류 {report['errors']}")
    return 0 if report["within_budget"] and report["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
