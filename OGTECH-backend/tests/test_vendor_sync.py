# -*- coding: utf-8 -*-
"""vendored 사본이 정본(OGTECH-llm)과 동일한지 강제한다 — 동명 파일 이원화 재발 방지."""

import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
LLM = BACKEND.parent / "OGTECH-llm" / "Co-LLM"

PAIRS = [
    (BACKEND / "core" / "ogtech_core.py", LLM / "scripts" / "ogtech_core.py"),
    (BACKEND / "config" / "keyword_rules.yaml", LLM / "config" / "keyword_rules.yaml"),
    (BACKEND / "config" / "survival_cards.json", LLM / "config" / "survival_cards.json"),
]


class VendorSyncTest(unittest.TestCase):
    def test_vendored_files_match_upstream(self):
        if not LLM.exists():
            self.skipTest("OGTECH-llm 정본 checkout이 옆에 없음 — CI/로컬 워크스페이스에서만 검사")
        for vendored, upstream in PAIRS:
            with self.subTest(file=vendored.name):
                self.assertEqual(
                    vendored.read_bytes(), upstream.read_bytes(),
                    f"{vendored.name} 이 정본과 다릅니다. 정본(OGTECH-llm)에서 고치고 다시 복사하세요.",
                )


if __name__ == "__main__":
    unittest.main()
