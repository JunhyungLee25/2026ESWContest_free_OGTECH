#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""14라벨 280문장과 refuse 50문장의 제품 분기 평가기."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ogtech_core import RuleRouter, SCENARIO_IDS  # noqa: E402


def load_jsonl(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number} JSON 오류") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}:{line_number} 객체가 아닙니다")
        rows.append({"id": str(row["id"]), "text": str(row["text"]), "expected": str(row["expected"])})
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OGTECH 14라벨·refuse 평가")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="규칙 미스에 로컬 llama-server JSON Schema 분류를 사용",
    )
    parser.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    classify_rows = load_jsonl(ROOT / "eval" / "cases_classify.jsonl")
    refuse_rows = load_jsonl(ROOT / "eval" / "cases_refuse.jsonl")
    router = RuleRouter()
    classifier = None
    if args.llm:
        from engines import classify_scenario

        classifier = classify_scenario

    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    failures: list[dict[str, str]] = []
    for row in classify_rows:
        decision = (
            router.resolve(row["text"], classifier=classifier)
            if args.llm
            else router.decide(row["text"])
        )
        actual = decision.scenario_id
        confusion[row["expected"]][actual] += 1
        if actual != row["expected"]:
            failures.append(
                {
                    "id": row["id"],
                    "expected": row["expected"],
                    "actual": actual,
                    "reason": decision.reason,
                }
            )

    refuse_leaks: list[dict[str, str]] = []
    for row in refuse_rows:
        decision = router.decide(row["text"])
        if decision.scenario_id != "refuse":
            refuse_leaks.append(
                {"id": row["id"], "actual": decision.scenario_id, "reason": decision.reason}
            )

    accuracy = 0.0 if not classify_rows else (len(classify_rows) - len(failures)) / len(classify_rows)
    result = {
        "mode": "rule_plus_llm" if args.llm else "rules_only",
        "cases": len(classify_rows),
        "labels": list(SCENARIO_IDS),
        "accuracy": round(accuracy, 6),
        "correct": len(classify_rows) - len(failures),
        "failures": failures,
        "refuse_cases": len(refuse_rows),
        "refuse_leaks": refuse_leaks,
        "confusion": {label: dict(confusion[label]) for label in SCENARIO_IDS},
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"모드: {result['mode']}")
        print(f"14라벨: {result['correct']}/{result['cases']} · 정확도 {accuracy * 100:.2f}%")
        print(f"refuse: {len(refuse_rows) - len(refuse_leaks)}/{len(refuse_rows)} · 누출 {len(refuse_leaks)}")
        if failures:
            print("분류 실패:")
            for row in failures:
                print(
                    f"  {row['id']}: {row['expected']} -> {row['actual']} ({row['reason']})"
                )
        if refuse_leaks:
            print("refuse 누출:")
            for row in refuse_leaks:
                print(f"  {row['id']}: {row['actual']} ({row['reason']})")
    return 0 if accuracy >= 0.90 and not refuse_leaks else 1


if __name__ == "__main__":
    raise SystemExit(main())
