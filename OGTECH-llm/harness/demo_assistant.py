# -*- coding: utf-8 -*-
"""DemoAssistant — ProductAssistant 위에 polish 후처리만 얹는다. MAP 명령·카드 선택 로직은 부모 그대로."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .demo_router import DemoRouter
from .paths import ensure_co_llm_on_path
from .polish import Polisher, PolishResult

ensure_co_llm_on_path()
from ogtech_core import CardRenderer, LIFE_PATH_B  # noqa: E402
from product_assistant import AssistantResult, ProductAssistant, VerifiedResponseStore  # noqa: E402


class DemoAssistant(ProductAssistant):
    def __init__(
        self,
        map_client: Any,
        *,
        router: DemoRouter,
        polisher: Polisher | None = None,
        cards: CardRenderer | None = None,
        response_store: VerifiedResponseStore | None = None,
        classifier: Any | None = None,
    ) -> None:
        # classifier는 DemoRouter에 intent가 없을 때만 쓰이는 구 분류기(라벨 1개)다.
        super().__init__(map_client, router=router, cards=cards, classifier=classifier, response_store=response_store)
        self.polisher = polisher
        self.last_polish: PolishResult | None = None

    @staticmethod
    def _polishable(result: AssistantResult) -> bool:
        decision = result.decision
        return (
            decision.map_action is None
            and decision.assistant_action is None
            and decision.path == "A"
            and decision.source in {"rule", "llm"}
            and decision.scenario_id not in LIFE_PATH_B
            and result.source_id.startswith("SAFE-")
        )

    def handle_text(self, text: str) -> AssistantResult:
        result = super().handle_text(text)
        self.last_polish = None
        if self.polisher is None or self.polisher.mode == "off" or not self._polishable(result):
            return result
        polished = self.polisher.polish(result.speech, device=result.device, scenario_id=result.decision.scenario_id)
        self.last_polish = polished
        if polished.spoken_lines:
            return replace(result, speech=" ".join(polished.spoken_lines))
        return result
