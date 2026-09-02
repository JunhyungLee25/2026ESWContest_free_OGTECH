from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import voice_loop  # noqa: E402


class VoiceLoopSafetyTest(unittest.TestCase):
    def test_refuse_rule_never_calls_classifier(self):
        def fail_if_called(_text):
            self.fail("refuse 발화를 LLM 분류기로 보내면 안 됩니다")

        text, decision, elapsed, _note = voice_loop.safe_bench_response(
            "이 버섯 먹어도 돼", use_classifier=True, classifier_fn=fail_if_called
        )

        self.assertEqual(decision.scenario_id, "refuse")
        self.assertEqual(elapsed, 0.0)
        self.assertIn("판단할 수 없습니다", text)

    def test_classifier_output_selects_card_but_never_becomes_spoken_text(self):
        generated = "이 문장은 절대로 스피커로 나가면 안 됩니다"

        def classifier(_text):
            return "water", generated

        text, decision, _elapsed, note = voice_loop.safe_bench_response(
            "도움이 필요해", use_classifier=True, classifier_fn=classifier
        )

        self.assertEqual(decision.scenario_id, "water")
        self.assertEqual(note, generated)
        self.assertNotIn(generated, text)
        self.assertIn("물", text)


if __name__ == "__main__":
    unittest.main()
