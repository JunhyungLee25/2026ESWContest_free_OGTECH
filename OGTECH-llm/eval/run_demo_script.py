#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""시연 대사 정본(config/demo_script.json)을 하네스 전체 경로로 실행해 기대값과 대조한다.

    python3 eval/run_demo_script.py                       # LLM 없음(규칙·오버레이만), 1회
    python3 eval/run_demo_script.py --llm mock --runs 20  # mock LLM, 20회 동일성
    python3 eval/run_demo_script.py --llm mock --mock-mode timeout   # LLM 장애 리허설
    python3 eval/run_demo_script.py --llm http://127.0.0.1:8080/v1/chat/completions   # Jetson

통과 기준: 모든 D턴(원문+변형)이 규칙/오버레이에서 기대 동작으로 확정, 기대 문장 일치,
LLM 필요 턴은 LLM 부재·장애 시 고정 카드, N회 출력 동일.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness import DemoAssistant, build_harness  # noqa: E402
from harness.fake_map import FakeMapClient  # noqa: E402
from harness.mock_llm_server import MODES, start_in_thread  # noqa: E402


def _decision_tuple(decision: Any) -> tuple[str, str | None, str | None]:
    return decision.scenario_id, decision.map_action, decision.assistant_action


def run_script(harness, script: dict[str, Any], *, llm_available: bool) -> dict[str, Any]:
    client = FakeMapClient()
    assistant = DemoAssistant(client, router=harness.router, polisher=harness.polisher)
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    llm_calls_on_canonical = 0

    for turn in script["turns"]:
        expect = turn["expect"]
        pending = bool(turn.get("pending"))
        wanted = (expect["scenario_id"], expect.get("map_action"), expect.get("assistant_action"))
        accepted = {wanted} | {
            (alt["scenario_id"], alt.get("map_action"), alt.get("assistant_action"))
            for alt in (turn.get("alt_expect") or [])
        }
        llm_required = turn.get("llm") == "required"

        # 원문 턴: 실제 순차 상태에서 전체 파이프라인 실행
        if pending and client.pending is None:
            client.pending = {"id": "demo-water-ilgam", "kind": "water_source", "name": "일감호 주변 수원 표식"}
        result = assistant.handle_text(turn["utterance"])
        trace = dict(harness.router.last_trace)
        got = _decision_tuple(result.decision)
        row: dict[str, Any] = {
            "id": turn["id"],
            "utterance": turn["utterance"],
            "stage": trace.get("stage"),
            "reason": result.decision.reason,
            "scenario_id": got[0],
            "map_action": got[1],
            "assistant_action": got[2],
            "source_id": result.source_id,
            "speech": result.speech,
            "ok": True,
            "variants": [],
            "observe": [],
        }
        if llm_required:
            if trace.get("stage") not in {"llm", "fixed", "classifier"}:
                row["ok"] = False
                failures.append(f"{turn['id']}: LLM 필요 턴이 규칙에서 확정됨 ({trace.get('stage')})")
            if not llm_available:
                if result.decision.reason not in set(expect.get("reason_when_llm_down") or []):
                    row["ok"] = False
                    failures.append(f"{turn['id']}: LLM 부재 폴백 사유 불일치 {result.decision.reason}")
                if got != wanted:
                    row["ok"] = False
                    failures.append(f"{turn['id']}: LLM 부재 시 기대 {wanted} 실제 {got}")
            if not result.source_id.startswith("SAFE-"):
                row["ok"] = False
                failures.append(f"{turn['id']}: 비검수 문장 {result.source_id}")
            if expect.get("speech") and not llm_available and result.speech != expect["speech"]:
                row["ok"] = False
                failures.append(f"{turn['id']}: 문장 불일치\n  기대 {expect['speech']}\n  실제 {result.speech}")
        else:
            if trace.get("stage") not in {"rule", "overlay"}:
                row["ok"] = False
                failures.append(f"{turn['id']}: 규칙이 아니라 {trace.get('stage')}에서 확정됨 — 시연 대사는 규칙 확정이 조건")
                if trace.get("stage") == "llm":
                    llm_calls_on_canonical += 1
            if got not in accepted:
                row["ok"] = False
                failures.append(f"{turn['id']}: 기대 {wanted} 실제 {got} ({result.decision.reason})")
            if expect.get("speech") and result.speech != expect["speech"]:
                row["ok"] = False
                failures.append(f"{turn['id']}: 문장 불일치\n  기대 {expect['speech']}\n  실제 {result.speech}")
            if expect.get("speech_contains") and expect["speech_contains"] not in result.speech:
                row["ok"] = False
                failures.append(f"{turn['id']}: 문장에 '{expect['speech_contains']}' 없음")

        # 변형: 라우팅 단계만 비교(상태는 원문 턴 이전 상태와 동일하게 pending만 맞춤)
        for text in turn.get("variants") or []:
            decision = harness.router.decide(text, pending_confirmation=pending)
            stage = harness.router.last_trace.get("stage")
            v_ok = _decision_tuple(decision) in accepted and stage in {"rule", "overlay"}
            row["variants"].append({"text": text, "stage": stage, "reason": decision.reason, "decision": list(_decision_tuple(decision)), "ok": v_ok})
            if not v_ok:
                row["ok"] = False
                failures.append(f"{turn['id']} 변형 {text!r}: {stage} {_decision_tuple(decision)} ({decision.reason})")
        for text in turn.get("observe") or []:
            decision = harness.router.resolve(text, pending_confirmation=pending)
            row["observe"].append({"text": text, "stage": harness.router.last_trace.get("stage"), "reason": decision.reason, "decision": list(_decision_tuple(decision))})
        rows.append(row)

    observed_defects = []
    for item in script.get("known_rule_defects_observe") or []:
        decision = harness.router.decide(item["text"], pending_confirmation=bool(item.get("pending")))
        expected = item.get("expected_after_fix")
        hit = expected in (decision.scenario_id, decision.map_action, decision.assistant_action, decision.source)
        row = {**item, "actual": list(_decision_tuple(decision)), "reason": decision.reason, "ok": hit}
        observed_defects.append(row)
        if item.get("assert") and not hit:
            failures.append(f"정본 규칙 회귀 WORKLOG#{item['worklog']}: {item['text']!r} -> {_decision_tuple(decision)} ({decision.reason}) / 기대 {expected}")

    return {
        "rows": rows,
        "failures": failures,
        "observed_rule_defects": observed_defects,
        "signature": [(r["id"], r["scenario_id"], r["map_action"], r["assistant_action"], r["speech"]) for r in rows],
        "llm_calls_on_canonical": llm_calls_on_canonical,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OGTECH 시연 대사 하네스 검증")
    parser.add_argument("--script", default=str(ROOT / "config" / "demo_script.json"))
    parser.add_argument("--policy", default=None)
    parser.add_argument("--llm", default="none", help="none | mock | <chat completions URL>")
    parser.add_argument("--mock-mode", choices=MODES, default="ok")
    parser.add_argument("--mock-delay", type=float, default=3.0)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--polish", choices=["off", "shadow", "speak"], default=None, help="정책의 polish.mode 덮어쓰기")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.runs <= 100:
        raise SystemExit("--runs는 1~100")
    script = json.loads(Path(args.script).read_text(encoding="utf-8"))

    mock = None
    llm_url: str | None = None
    intent_enabled: bool | None = None
    llm_available = False
    if args.llm == "none":
        intent_enabled = False
    elif args.llm == "mock":
        mock = start_in_thread(mode=args.mock_mode, delay_s=args.mock_delay)
        llm_url = mock.url
        llm_available = args.mock_mode in {"ok", "slow"}
    else:
        llm_url = args.llm
        llm_available = True

    try:
        harness = build_harness(args.policy, llm_url=llm_url, intent_enabled=intent_enabled, polish_mode=args.polish)
        if harness.intent is not None and llm_available:
            harness.warmup()
        results = [run_script(harness, script, llm_available=llm_available) for _ in range(args.runs)]
    finally:
        if mock is not None:
            mock.shutdown()
            mock.server_close()

    signatures = {json.dumps(r["signature"], ensure_ascii=False) for r in results}
    reproducible = len(signatures) == 1
    first = results[0]
    failures = list(first["failures"])
    if not reproducible:
        failures.append(f"{args.runs}회 출력이 동일하지 않음 ({len(signatures)}가지)")
    summary = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "llm": args.llm if args.llm != "mock" else f"mock:{args.mock_mode}",
        "evidence": "mock 또는 LLM 없음 — 배관 검증. 실제 모델 결과가 아님" if args.llm in {"none", "mock"} else "실제 llama-server 응답",
        "runs": args.runs,
        "reproducible": reproducible,
        "turns": len(first["rows"]),
        "variants": sum(len(r["variants"]) for r in first["rows"]),
        "passed_turns": sum(1 for r in first["rows"] if r["ok"]),
        "failures": failures,
        "llm_calls_on_canonical": first["llm_calls_on_canonical"],
        "observed_rule_defects": first["observed_rule_defects"],
        "rows": first["rows"],
    }
    serialized = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    if args.json:
        print(serialized, end="")
    else:
        for row in first["rows"]:
            mark = "OK  " if row["ok"] else "FAIL"
            print(f"{mark} {row['id']} [{row['stage']}] {row['utterance']!r} -> {row['scenario_id']}/{row['map_action']}/{row['assistant_action']}")
            print(f"       {row['speech']}")
            bad = [v for v in row["variants"] if not v["ok"]]
            print(f"       변형 {len(row['variants']) - len(bad)}/{len(row['variants'])}" + ("" if not bad else " — 실패: " + ", ".join(repr(v['text']) for v in bad)))
            for item in row["observe"]:
                print(f"       관찰 {item['text']!r} -> {item['decision']} [{item['stage']}:{item['reason']}]")
        print("---")
        for item in first["observed_rule_defects"]:
            mark = "OK  " if item.get("ok") else "FAIL"
            print(f"{mark} 정본 규칙 WORKLOG#{item['worklog']}: {item['text']!r} -> {item['actual']} ({item['reason']}) / 기대 {item['expected_after_fix']}")
        print("---")
        print(f"LLM={summary['llm']} · 턴 {summary['passed_turns']}/{summary['turns']} · 변형 {summary['variants']} · {args.runs}회 동일={reproducible}")
        for failure in failures:
            print("FAIL:", failure)
        print("결과:", "통과" if not failures else "실패")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
