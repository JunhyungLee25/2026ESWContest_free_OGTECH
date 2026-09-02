#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""실제 Jetson 음성 인수 관측값을 fail-closed 방식으로 판정한다.

이 모듈은 GPIO, ALSA, loopback, tegrastats를 직접 조작하지 않는다.
실물에서 수집한 관측값을 JSON 또는 JSONL로 받아 증거를 원자적으로 저장한다.
테스트 fixture와 시뮬레이션 표시는 절대로 통과시키지 않는다.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable


SCHEMA_VERSION = 1
DEFAULT_RUNS = 20
MEMORY_FLOOR_MB = 1024
PATH_B_BUDGET_MS = 2000.0
PATH_A_BUDGET_MS = 3500.0
OBSERVATION_ORIGINS = frozenset({"hardware_observation", "operator_observation"})


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _int(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """부분 결과도 남도록 같은 디렉터리에서 flush/fsync 후 replace한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
        suffix=".tmp", delete=False,
    )
    temporary = Path(handle.name)
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temporary, path)
    finally:
        if not handle.closed:
            handle.close()
        temporary.unlink(missing_ok=True)


class ObservationLog:
    """한 줄씩 들어오는 실제 관측 이벤트를 run 레코드로 누적한다."""

    EVENT_FIELDS = {
        "run": (),
        "stt_matrix": (
            "stt_cases", "stt_false_positive_keywords",
            "stt_expected_keywords_missed", "stt_recall_ok", "observed",
        ),
        "button_release": (
            "button_release_monotonic_ns", "button_release_source", "observed",
        ),
        "first_sound": (
            "first_sound_monotonic_ns", "first_sound_source", "first_sound_method",
            "loopback_observed", "observed",
        ),
        "stt": (
            "stt_exit_code", "stt_false_positive_keywords",
            "stt_expected_keywords_missed", "stt_recall_ok", "observed",
        ),
        "resources": (
            "mem_available_min_mb", "swap_before_mb", "swap_after_mb",
            "swap_delta_mb", "observed",
        ),
        "concurrency": (
            "stt_tts_overlap", "stt_start_monotonic_ns", "stt_end_monotonic_ns",
            "tts_start_monotonic_ns", "tts_end_monotonic_ns", "observed",
        ),
        "network": ("external_connections", "observed"),
        "complete": (),
    }

    def __init__(self, requested_runs: int) -> None:
        self.requested_runs = requested_runs
        self.records: dict[int, dict[str, Any]] = {}
        self.errors: list[str] = []
        self.input_metadata: dict[str, Any] = {}
        self.stt_matrix: dict[str, Any] = {}

    def apply(self, payload: dict[str, Any], line_number: int | None = None) -> None:
        prefix = f"line {line_number}: " if line_number is not None else ""
        if not isinstance(payload, dict):
            self.errors.append(prefix + "이벤트가 JSON 객체가 아닙니다")
            return
        event = str(payload.get("event") or "run")
        if event == "stt_matrix":
            self.stt_matrix.update({key: value for key, value in payload.items() if key not in {"event", "run"}})
            return
        raw_run = payload.get("run")
        run = _int(raw_run)
        if run is None or not 1 <= run <= self.requested_runs:
            self.errors.append(prefix + f"run은 1~{self.requested_runs} 정수여야 합니다")
            return
        if event not in self.EVENT_FIELDS:
            self.errors.append(prefix + f"알 수 없는 event: {event}")
            return
        record = self.records.setdefault(run, {"run": run, "event_count": 0})
        record["event_count"] = int(record.get("event_count", 0)) + 1
        if event in {"run", "complete"}:
            record.update({key: value for key, value in payload.items() if key not in {"event", "run"}})
        else:
            allowed = set(self.EVENT_FIELDS[event])
            record.update({key: value for key, value in payload.items() if key in allowed})
        if event == "complete":
            record["complete"] = True

    def apply_document(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            self.errors.append("입력 문서가 JSON 객체가 아닙니다")
            return
        self.input_metadata = dict(payload.get("metadata") or {})
        if isinstance(payload.get("stt_matrix"), dict):
            self.stt_matrix.update(payload["stt_matrix"])
        if payload.get("evidence_origin") is not None:
            self.input_metadata["evidence_origin"] = payload["evidence_origin"]
        if payload.get("simulated") is not None:
            self.input_metadata["simulated"] = payload["simulated"]
        runs = payload.get("runs")
        if not isinstance(runs, list):
            self.apply(payload)
            return
        for item in runs:
            if isinstance(item, dict):
                merged = dict(item)
                merged.setdefault("event", "run")
                for key, value in self.input_metadata.items():
                    merged.setdefault(key, value)
                self.apply(merged)
            else:
                self.errors.append("runs 배열에 JSON 객체가 아닌 항목이 있습니다")


def _base_report(requested_runs: int, log: ObservationLog) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "ogtech_hardware_acceptance",
        "status": "in_progress",
        "pass": False,
        "runs_requested": requested_runs,
        "criteria": {
            "path_a_max_ms": PATH_A_BUDGET_MS,
            "path_b_max_ms": PATH_B_BUDGET_MS,
            "false_positive_keywords": 0,
            "min_mem_available_mb": MEMORY_FLOOR_MB,
            "swap_increase_mb": 0,
            "stt_tts_overlap": False,
            "external_connections": 0,
            "first_sound_method": "loopback_onset",
        },
        "input_metadata": log.input_metadata,
        "stt_matrix": log.stt_matrix,
        "runs": [log.records[key] for key in sorted(log.records)],
        "errors": list(log.errors),
    }


def _check_run(record: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    checks: dict[str, bool] = {}
    unverified = False

    def require(name: str, condition: bool, reason: str, *, missing: bool = False) -> None:
        nonlocal unverified
        checks[name] = bool(condition)
        if not condition:
            reasons.append(reason)
            unverified = unverified or missing

    require("observed", record.get("observed") is True, "실물 관측 observed=true가 없습니다", missing=True)
    require("not_simulated", record.get("simulated") is False, "simulated=false가 명시되지 않았습니다", missing=record.get("simulated") is None)
    require(
        "evidence_origin",
        record.get("evidence_origin") in OBSERVATION_ORIGINS,
        "hardware/operator 관측 출처가 아닙니다",
        missing=True,
    )
    require("complete", record.get("complete") is True, "run complete 이벤트가 없습니다", missing=True)
    path = str(record.get("path") or "").upper()
    require("path", path in {"A", "B"}, "path는 A 또는 B여야 합니다")

    release = _int(record.get("button_release_monotonic_ns"))
    first_sound = _int(record.get("first_sound_monotonic_ns"))
    require("button_release", release is not None and release > 0, "실제 버튼 해제 시각이 없습니다", missing=True)
    require("first_sound", first_sound is not None and first_sound > 0, "실제 첫 소리 시각이 없습니다", missing=True)
    if release is not None and first_sound is not None:
        delta_ms = (first_sound - release) / 1_000_000.0
        require("timestamp_order", delta_ms >= 0, "첫 소리 시각이 버튼 해제보다 빠릅니다")
        if delta_ms >= 0:
            budget = PATH_A_BUDGET_MS if path == "A" else PATH_B_BUDGET_MS
            require("first_sound_budget", delta_ms <= budget, f"버튼 해제부터 첫 소리 {delta_ms:.3f}ms가 예산을 초과했습니다")
            supplied = _number(record.get("button_release_to_first_sound_ms"))
            if supplied is not None:
                require("timing_consistent", abs(supplied - delta_ms) <= 1.0, "제공된 지연과 timestamp 계산값이 다릅니다")
        else:
            delta_ms = None
    else:
        delta_ms = None
    require("button_source", record.get("button_release_source") in {"gpio", "operator"}, "버튼 해제 관측 출처가 gpio/operator가 아닙니다", missing=True)
    require("loopback_source", record.get("first_sound_source") == "loopback", "첫 소리 출처가 loopback이 아닙니다", missing=True)
    require("loopback_method", record.get("first_sound_method") == "loopback_onset", "첫 소리 측정법이 loopback_onset이 아닙니다", missing=True)
    require("loopback_observed", record.get("loopback_observed") is True, "실제 loopback 관측이 없습니다", missing=True)

    false_positive = record.get("stt_false_positive_keywords")
    missed = record.get("stt_expected_keywords_missed")
    require("false_positive_zero", isinstance(false_positive, list) and not false_positive, "STT false positive가 1건 이상입니다", missing=not isinstance(false_positive, list))
    require("stt_recall", isinstance(missed, list) and not missed and record.get("stt_recall_ok") is True, "STT 안전 키워드 재현율이 100%가 아닙니다", missing=not isinstance(missed, list) or record.get("stt_recall_ok") is None)
    require("stt_exit", _int(record.get("stt_exit_code")) == 0, "STT 종료 코드가 0이 아닙니다", missing=record.get("stt_exit_code") is None)

    memory = _number(record.get("mem_available_min_mb"))
    require("memory_floor", memory is not None and memory >= MEMORY_FLOOR_MB, "최저 MemAvailable이 1GB 미만입니다", missing=memory is None)
    swap_before = _number(record.get("swap_before_mb"))
    swap_after = _number(record.get("swap_after_mb"))
    require("swap_observed", swap_before is not None and swap_after is not None, "swap 시작·종료 관측값이 없습니다", missing=True)
    if swap_before is not None and swap_after is not None:
        delta = swap_after - swap_before
        require("swap_not_increased", delta <= 0, f"swap이 {delta:.1f}MB 증가했습니다")
        supplied_delta = _number(record.get("swap_delta_mb"))
        if supplied_delta is not None:
            require("swap_consistent", abs(supplied_delta - delta) <= 0.01, "swap_delta_mb가 시작·종료값과 다릅니다")

    overlap = record.get("stt_tts_overlap")
    require("stt_tts_non_overlap", overlap is False, "STT/TTS overlap이 true입니다", missing=overlap is None)
    intervals = [
        _int(record.get(name))
        for name in ("stt_start_monotonic_ns", "stt_end_monotonic_ns", "tts_start_monotonic_ns", "tts_end_monotonic_ns")
    ]
    if all(value is not None for value in intervals):
        stt_start, stt_end, tts_start, tts_end = intervals
        derived_overlap = bool(stt_start < tts_end and tts_start < stt_end)
        require("interval_non_overlap", not derived_overlap, "STT/TTS timestamp 구간이 겹칩니다")
        require("overlap_flag_consistent", overlap is derived_overlap, "stt_tts_overlap 플래그가 timestamp와 다릅니다")
    else:
        require("intervals_observed", False, "STT/TTS 시작·종료 timestamp가 없습니다", missing=True)

    external = _int(record.get("external_connections"))
    require("external_network_zero", external == 0, "외부 연결 수가 0이 아닙니다", missing=external is None)
    require("tts_pcm", record.get("tts_pcm_valid") is True, "TTS PCM 품질 검증이 통과하지 않았습니다", missing=record.get("tts_pcm_valid") is None)
    require("tts_playback", record.get("tts_playback_observed") is True, "실제 TTS 재생이 확인되지 않았습니다", missing=record.get("tts_playback_observed") is None)
    require("tts_engine", isinstance(record.get("tts_engine"), str) and bool(record["tts_engine"].strip()), "사용된 TTS 엔진이 기록되지 않았습니다", missing=True)

    return {
        "run": record.get("run"),
        "status": "pass" if not reasons else ("unverified" if unverified else "fail"),
        "pass": not reasons,
        "button_release_to_first_sound_ms": None if delta_ms is None else round(delta_ms, 3),
        "checks": checks,
        "reasons": reasons,
    }


def evaluate(log: ObservationLog) -> dict[str, Any]:
    report = _base_report(log.requested_runs, log)
    matrix = log.stt_matrix
    matrix_cases = _int(matrix.get("stt_cases"))
    matrix_false_positive = matrix.get("stt_false_positive_keywords")
    matrix_missed = matrix.get("stt_expected_keywords_missed")
    matrix_complete = all(
        key in matrix
        for key in (
            "stt_cases", "stt_false_positive_keywords",
            "stt_expected_keywords_missed", "stt_recall_ok", "observed",
        )
    )
    matrix_ok = (
        matrix.get("observed") is True
        and matrix_cases == 21
        and isinstance(matrix_false_positive, list) and not matrix_false_positive
        and isinstance(matrix_missed, list) and not matrix_missed
        and matrix.get("stt_recall_ok") is True
    )
    report["stt_matrix_verdict"] = {
        "pass": matrix_ok,
        "cases": matrix_cases,
        "false_positive_keywords": matrix_false_positive,
        "expected_keywords_missed": matrix_missed,
        "reasons": [] if matrix_ok else ["실제 Jetson STT 21문장·false positive 0 증거가 없습니다"],
    }
    expected = set(range(1, log.requested_runs + 1))
    actual = set(log.records)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        report["errors"].append(f"누락된 run: {missing}")
    if extra:
        report["errors"].append(f"범위를 벗어난 run: {extra}")
    checks = [_check_run(log.records[run]) for run in sorted(actual & expected)]
    report["run_verdicts"] = checks
    delays = [item["button_release_to_first_sound_ms"] for item in checks if item["button_release_to_first_sound_ms"] is not None]
    report["summary"] = {
        "runs_present": len(actual & expected),
        "runs_passed": sum(1 for item in checks if item["pass"]),
        "runs_requested": log.requested_runs,
        "missing_runs": missing,
        "max_button_release_to_first_sound_ms": max(delays) if delays else None,
    }
    if not matrix_ok:
        report["errors"].append("STT matrix가 21문장·false positive 0 조건을 충족하지 않습니다")
    if not checks or missing or extra or report["errors"]:
        report["status"] = "unverified" if not checks or missing or not matrix_complete else "fail"
    elif any(item["status"] == "unverified" for item in checks):
        report["status"] = "unverified"
    elif all(item["pass"] for item in checks) and len(checks) == log.requested_runs:
        report["status"] = "pass"
    else:
        report["status"] = "fail"
    report["pass"] = report["status"] == "pass"
    return report


def _read_input(path: Path, log: ObservationLog) -> None:
    if path.suffix.lower() == ".json":
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.errors.append(f"JSON 입력을 읽지 못했습니다: {exc}")
            return
        log.apply_document(document)
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        log.errors.append(f"JSONL 입력을 읽지 못했습니다: {exc}")
        return
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.errors.append(f"line {line_number}: JSON 오류: {exc.msg}")
            continue
        log.apply(payload, line_number)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="실제 Jetson 음성 인수 관측값 fail-closed 판정")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--events", type=Path, help="run/button_release/first_sound 등 JSONL 관측 이벤트")
    source.add_argument("--input", type=Path, help="runs 배열을 가진 JSON 증거 문서")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="필수 연속 run 수, 기본 20")
    parser.add_argument("--output", type=Path, required=True, help="원자적으로 저장할 결과 JSON")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.runs <= 100:
        raise SystemExit("--runs는 1~100이어야 합니다")
    log = ObservationLog(args.runs)
    source = args.events or args.input
    assert source is not None
    if source.suffix.lower() == ".json":
        _read_input(source, log)
        _atomic_write(args.output, evaluate(log))
    else:
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            log.errors.append(f"JSONL 입력을 읽지 못했습니다: {exc}")
            lines = []
        try:
            for line_number, raw in enumerate(lines, start=1):
                if not raw.strip():
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    log.errors.append(f"line {line_number}: JSON 오류: {exc.msg}")
                    continue
                log.apply(payload, line_number)
                _atomic_write(args.output, evaluate(log))
        except KeyboardInterrupt:
            log.errors.append("operator가 JSONL 입력을 중단했습니다")
    report = evaluate(log)
    _atomic_write(args.output, report)
    print(json.dumps({"status": report["status"], "pass": report["pass"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
