# -*- coding: utf-8 -*-
"""테스트 공용: 경로 설정과 LLM 클라이언트 대역."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.llm_client import LlmResponse  # noqa: E402


class StubLlmClient:
    """chat_json이 미리 정한 응답을 돌려준다. 호출 수를 센다."""

    def __init__(self, content: dict[str, Any] | None = None, *, ok: bool = True, error: str | None = None, raw: str | None = None) -> None:
        self.content = content
        self.ok = ok
        self.error = error
        self.raw = raw
        self.calls: list[list[dict[str, str]]] = []
        self.url = "http://127.0.0.1:0/v1/chat/completions"

    def chat_json(self, messages, schema, *, max_tokens, timeout_s=None, slot=-1) -> LlmResponse:
        self.calls.append(messages)
        if not self.ok:
            return LlmResponse(False, None, "", self.error or "stub_error", 0.01)
        import json

        raw = self.raw if self.raw is not None else json.dumps(self.content, ensure_ascii=False)
        return LlmResponse(True, self.content, raw, None, 0.01, {"prompt_tokens": 1, "completion_tokens": 1}, {})

    def health(self, timeout_s: float = 1.0) -> bool:
        return True

    def tokenize(self, text: str, timeout_s: float = 5.0) -> int | None:
        return len(text)
