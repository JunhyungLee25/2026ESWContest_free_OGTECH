
from __future__ import annotations

import unittest

from _support import ROOT  # noqa: F401

from harness.normalize import apply_lexicon, load_lexicon


class LexiconTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_lexicon()

    def test_known_misrecognitions_are_repaired(self) -> None:
        self.assertEqual(apply_lexicon("목격체까지 얼마나 남았어", self.rules), "목적지까지 얼마나 남았어")
        self.assertEqual(apply_lexicon("헨트 안에서 번호 켜도 돼", self.rules), "텐트 안에서 버너 켜도 돼")
        self.assertEqual(apply_lexicon("베이스 캠프 복귀 경로", self.rules), "베이스캠프 복귀 경로")
        self.assertEqual(apply_lexicon("야간모드 켜줘", self.rules), "야간 모드 켜줘")

    def test_burner_fix_needs_combustion_context(self) -> None:
        # unknown-09 평가 케이스 '버너 번호가 적혀 있어'는 그대로 두어야 한다.
        self.assertEqual(apply_lexicon("버너 번호가 적혀 있어", self.rules), "버너 번호가 적혀 있어")
        self.assertEqual(apply_lexicon("전화 번호 알려 줘", self.rules), "전화 번호 알려 줘")

    def test_idempotent_and_whitespace_normalized(self) -> None:
        once = apply_lexicon("  헨트  안에서   번호 켜 ", self.rules)
        self.assertEqual(once, "텐트 안에서 버너 켜")
        self.assertEqual(apply_lexicon(once, self.rules), once)

    def test_refuse_words_untouched(self) -> None:
        text = "이 버섯 먹어도 돼"
        self.assertEqual(apply_lexicon(text, self.rules), text)


if __name__ == "__main__":
    unittest.main()
