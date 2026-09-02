from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

from run_hardware_acceptance import ObservationLog, _read_input, evaluate  # noqa: E402


def valid_run(run: int, *, path: str = "B") -> dict[str, object]:
    release = 1_000_000_000 + run * 10_000_000
    budget_ms = 3_500.0 if path == "A" else 2_000.0
    delay_ms = 3_500.0 if path == "A" else 1_500.0
    return {
        "event": "run",
        "run": run,
        "path": path,
        "observed": True,
        "simulated": False,
        "evidence_origin": "hardware_observation",
        "complete": True,
        "button_release_monotonic_ns": release,
        "button_release_source": "gpio",
        "first_sound_monotonic_ns": release + int(delay_ms * 1_000_000),
        "button_release_to_first_sound_ms": budget_ms if path == "A" else delay_ms,
        "first_sound_source": "loopback",
        "first_sound_method": "loopback_onset",
        "loopback_observed": True,
        "stt_exit_code": 0,
        "stt_false_positive_keywords": [],
        "stt_expected_keywords_missed": [],
        "stt_recall_ok": True,
        "mem_available_min_mb": 2048,
        "swap_before_mb": 0,
        "swap_after_mb": 0,
        "swap_delta_mb": 0,
        "stt_tts_overlap": False,
        "stt_start_monotonic_ns": release + 10_000,
        "stt_end_monotonic_ns": release + 200_000_000,
        "tts_start_monotonic_ns": release + 300_000_000,
        "tts_end_monotonic_ns": release + int(delay_ms * 1_000_000),
        "external_connections": 0,
        "tts_pcm_valid": True,
        "tts_playback_observed": True,
        "tts_engine": "melotts",
    }


def valid_stt_matrix() -> dict[str, object]:
    return {
        "stt_cases": 21,
        "stt_false_positive_keywords": [],
        "stt_expected_keywords_missed": [],
        "stt_recall_ok": True,
        "observed": True,
    }


class HardwareAcceptanceTest(unittest.TestCase):
    def test_twenty_complete_observed_runs_pass_at_inclusive_budgets(self) -> None:
        log = ObservationLog(20)
        log.stt_matrix.update(valid_stt_matrix())
        for run in range(1, 20):
            log.apply(valid_run(run))
        log.apply(valid_run(20, path="A"))

        report = evaluate(log)

        self.assertTrue(report["pass"])
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["runs_passed"], 20)
        self.assertEqual(report["summary"]["max_button_release_to_first_sound_ms"], 3500.0)

    def test_missing_observation_never_passes(self) -> None:
        log = ObservationLog(20)
        log.apply(valid_run(1))

        report = evaluate(log)

        self.assertFalse(report["pass"])
        self.assertEqual(report["status"], "unverified")
        self.assertEqual(report["summary"]["missing_runs"], list(range(2, 21)))

    def test_safety_criteria_fail_closed(self) -> None:
        log = ObservationLog(1)
        log.stt_matrix.update(valid_stt_matrix())
        record = valid_run(1)
        record.update(
            {
                "stt_false_positive_keywords": ["버섯"],
                "mem_available_min_mb": 900,
                "swap_before_mb": 0,
                "swap_after_mb": 4,
                "stt_tts_overlap": True,
                "external_connections": 1,
            }
        )
        log.apply(record)

        report = evaluate(log)

        self.assertFalse(report["pass"])
        reasons = report["run_verdicts"][0]["reasons"]
        self.assertTrue(any("false positive" in reason for reason in reasons))
        self.assertTrue(any("1GB" in reason for reason in reasons))
        self.assertTrue(any("swap" in reason for reason in reasons))
        self.assertTrue(any("overlap" in reason or "겹칩니다" in reason for reason in reasons))
        self.assertTrue(any("외부 연결" in reason for reason in reasons))

    def test_test_fixture_is_unverified_even_if_values_look_complete(self) -> None:
        log = ObservationLog(1)
        fixture = ROOT / "tests" / "fixtures" / "hardware_acceptance_sample.jsonl"
        _read_input(fixture, log)

        report = evaluate(log)

        self.assertFalse(report["pass"])
        self.assertEqual(report["status"], "unverified")
        self.assertTrue(any("simulated" in reason for reason in report["run_verdicts"][0]["reasons"]))

    def test_json_document_input_is_supported(self) -> None:
        log = ObservationLog(1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.json"
            path.write_text(
                json.dumps({"stt_matrix": valid_stt_matrix(), "runs": [valid_run(1)]}, ensure_ascii=False),
                encoding="utf-8",
            )
            _read_input(path, log)

        report = evaluate(log)
        self.assertTrue(report["pass"])
        self.assertEqual(report["status"], "pass")

    def test_jsonl_stt_matrix_event_has_no_run_number(self) -> None:
        log = ObservationLog(1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.jsonl"
            lines = [
                {"event": "stt_matrix", **valid_stt_matrix()},
                valid_run(1),
            ]
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in lines),
                encoding="utf-8",
            )
            _read_input(path, log)

        report = evaluate(log)
        self.assertTrue(report["pass"])
        self.assertEqual(report["stt_matrix_verdict"]["cases"], 21)


if __name__ == "__main__":
    unittest.main()
