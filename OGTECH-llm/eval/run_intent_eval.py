#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 의도 분류기 단독 평가 — 규칙을 우회하고 모델에만 묻는다.

    python3 eval/run_intent_eval.py --llm mock
    python3 eval/run_intent_eval.py --llm http://127.0.0.1:8080/v1/chat/completions --output results/intent_eval_jetson.json

입력: Co-LLM/eval/cases_classify.jsonl(280) · voice_cases.json(183, map_action 포함) · cases_refuse.jsonl(50).
few-shot에 그대로 들어 있는 문장은 정확도 계산에서 제외하고 개수만 보고한다.
게이트(PLAN §2.2): 14라벨 정확도 ≥ 90%. refuse는 정본 규칙이 먼저 잡으므로 모델 단독 값은 정보용이다.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness import build_harness  # noqa: E402
from harness.guard import validate_intent  # noqa: E402
from harness.mock_llm_server import start_in_thread  # noqa: E402

CO_LLM_EVAL = ROOT / "Co-LLM" / "eval"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            rows.append(json.loads(raw))
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OGTECH LLM 의도 단독 평가")
    parser.add_argument("--llm", default="mock", help="mock | <chat completions URL>")
    parser.add_argument("--policy", default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--report-only", action="store_true", help="게이트 미달이어도 종료 코드 0")
    parser.add_argument("--limit", type=int, default=0, help="빠른 점검용 케이스 수 제한(0=전부)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mock = None
    llm_url = None
    if args.llm == "mock":
        mock = start_in_thread(mode="ok")
        llm_url = mock.url
    else:
        llm_url = args.llm
    try:
        harness = build_harness(args.policy, llm_url=llm_url, intent_enabled=True, polish_mode="off")
        intent = harness.intent
        assert intent is not None
        fewshot_texts = {str(row["user"]) for row in intent.fewshot}
        harness.warmup()

        classify = load_jsonl(CO_LLM_EVAL / "cases_classify.jsonl")
        refuse = load_jsonl(CO_LLM_EVAL / "cases_refuse.jsonl")
        voice = json.loads((CO_LLM_EVAL / "voice_cases.json").read_text(encoding="utf-8"))["cases"]
        if args.limit:
            classify, refuse, voice = classify[: args.limit], refuse[: args.limit], voice[: args.limit]

        latencies: list[float] = []
        errors = 0

        def ask(text: str, pending: bool):
            nonlocal errors
            result = intent.resolve(harness.router.prepare(text), pending_confirmation=pending)
            if result.response is not None:
                latencies.append(result.response.elapsed_s)
                if not result.response.ok:
                    errors += 1
            return result

        # 1) 14라벨 — guard 이전의 원시 라벨로 채점(모델 능력), guard 이후 채택 여부는 별도 집계
        confusion: dict[str, Counter[str]] = defaultdict(Counter)
        raw_correct = 0
        scored = 0
        skipped_fewshot = 0
        label_failures = []
        for row in classify:
            if row["text"] in fewshot_texts:
                skipped_fewshot += 1
                continue
            result = ask(row["text"], False)
            raw = (result.response.content or {}) if (result.response and result.response.ok) else {}
            raw_label = raw.get("scenario_id") if isinstance(raw, dict) else None
            actual = raw_label if isinstance(raw_label, str) else "unknown"
            confusion[row["expected"]][actual] += 1
            scored += 1
            if actual == row["expected"]:
                raw_correct += 1
            else:
                label_failures.append({"id": row["id"], "text": row["text"], "expected": row["expected"], "actual": actual, "guard": result.verdict.reason})

        # 2) 지도 동작 — voice_cases의 map_action/assistant_action 기대와 guard 통과 후 결과 비교
        action_total = 0
        action_correct = 0
        action_failures = []
        for case in voice:
            if case["text"] in fewshot_texts:
                skipped_fewshot += 1
                continue
            pending = bool(case.get("pending"))
            result = ask(case["text"], pending)
            verdict = result.verdict
            expected_action = case.get("map_action")
            expected_assistant = case.get("assistant_action")
            if expected_action is None and expected_assistant is None:
                continue
            action_total += 1
            if verdict.map_action == expected_action and verdict.assistant_action == expected_assistant:
                action_correct += 1
            else:
                action_failures.append({"text": case["text"], "pending": pending, "expected": [expected_action, expected_assistant], "actual": [verdict.map_action, verdict.assistant_action], "reason": verdict.reason, "raw": None if result.response is None else result.response.raw})

        # 3) refuse — 모델 단독 인식률(정보용). 제품에서는 정본 규칙이 먼저 막는다.
        refuse_hits = 0
        refuse_scored = 0
        for row in refuse:
            if row["text"] in fewshot_texts:
                skipped_fewshot += 1
                continue
            result = ask(row["text"], False)
            raw = (result.response.content or {}) if (result.response and result.response.ok) else {}
            refuse_scored += 1
            if isinstance(raw, dict) and raw.get("scenario_id") == "refuse":
                refuse_hits += 1

        accuracy = raw_correct / scored if scored else 0.0
        action_accuracy = action_correct / action_total if action_total else 0.0
        latency = {
            "calls": len(latencies),
            "max_s": round(max(latencies), 3) if latencies else None,
            "median_s": round(statistics.median(latencies), 3) if latencies else None,
            "errors": errors,
        }
        report = {
            "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "llm": "mock" if mock else args.llm,
            "evidence": "mock — 배관 검증. 실제 모델 결과가 아님" if mock else "실제 llama-server 응답",
            "label_cases_scored": scored,
            "label_accuracy": round(accuracy, 4),
            "label_gate_0_90": accuracy >= 0.90,
            "label_failures": label_failures,
            "confusion": {label: dict(counter) for label, counter in confusion.items()},
            "action_cases_scored": action_total,
            "action_accuracy": round(action_accuracy, 4),
            "action_failures": action_failures,
            "refuse_model_only_hits": f"{refuse_hits}/{refuse_scored}",
            "fewshot_overlap_skipped": skipped_fewshot,
            "latency": latency,
        }
    finally:
        if mock is not None:
            mock.shutdown()
            mock.server_close()

    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    if args.json:
        print(serialized, end="")
    else:
        print(f"LLM={report['llm']} ({report['evidence']})")
        print(f"14라벨(원시): {raw_correct}/{scored} = {accuracy * 100:.2f}%  게이트 ≥90%: {'통과' if report['label_gate_0_90'] else '미달'}")
        print(f"지도 동작(guard 후): {action_correct}/{action_total} = {action_accuracy * 100:.2f}%")
        print(f"refuse 모델 단독 인식: {report['refuse_model_only_hits']} (정보용)")
        print(f"few-shot 겹침 제외: {skipped_fewshot} · 지연 max {latency['max_s']} s / median {latency['median_s']} s · 오류 {errors}")
        for item in label_failures[:40]:
            print(f"  라벨 실패 {item['id']}: {item['expected']} -> {item['actual']} | {item['text']}")
        for item in action_failures[:40]:
            print(f"  동작 실패 {item['expected']} -> {item['actual']} ({item['reason']}) | {item['text']}")
    if args.report_only:
        return 0
    return 0 if report["label_gate_0_90"] and errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
