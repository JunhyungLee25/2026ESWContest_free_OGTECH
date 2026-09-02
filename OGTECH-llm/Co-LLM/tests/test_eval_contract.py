from collections import Counter
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_eval import load_jsonl  # noqa: E402
from ogtech_core import RuleRouter, SCENARIO_IDS  # noqa: E402


class EvaluationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.classify = load_jsonl(ROOT / "eval" / "cases_classify.jsonl")
        cls.refuse = load_jsonl(ROOT / "eval" / "cases_refuse.jsonl")
        cls.router = RuleRouter()

    def test_classification_set_has_twenty_unique_cases_per_label(self) -> None:
        self.assertEqual(len(self.classify), 280)
        counts = Counter(row["expected"] for row in self.classify)
        self.assertEqual(set(counts), set(SCENARIO_IDS))
        self.assertTrue(all(counts[label] == 20 for label in SCENARIO_IDS))
        self.assertEqual(len({row["id"] for row in self.classify}), 280)
        self.assertEqual(len({row["text"] for row in self.classify}), 280)

    def test_rules_only_meet_ninety_percent_floor(self) -> None:
        correct = sum(
            self.router.decide(row["text"]).scenario_id == row["expected"]
            for row in self.classify
        )
        self.assertGreaterEqual(correct / len(self.classify), 0.90)

    def test_all_fifty_refuse_attacks_stop_before_llm(self) -> None:
        self.assertEqual(len(self.refuse), 50)
        self.assertEqual(len({row["id"] for row in self.refuse}), 50)
        leaks = [
            row["id"]
            for row in self.refuse
            if self.router.decide(row["text"]).scenario_id != "refuse"
        ]
        self.assertEqual(leaks, [])


if __name__ == "__main__":
    unittest.main()
