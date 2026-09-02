# -*- coding: utf-8 -*-
"""DemoRouter — STT 사전 → 정본 규칙 → 시연 오버레이 → LLM 의도(enum 2개) 순으로 RouteDecision을 만든다.

ProductAssistant(router=DemoRouter(...))로 꽂으면 handle_text는 무수정으로 동작한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .intent import IntentResolver
from .normalize import LexiconRule, apply_lexicon
from .paths import ensure_co_llm_on_path

ensure_co_llm_on_path()
from ogtech_core import RULES_PATH, RouteDecision, RuleRouter  # noqa: E402


class DemoRouter(RuleRouter):
    def __init__(
        self,
        *,
        rules_path: str | Path | None = None,
        overlay_path: str | Path | None = None,
        lexicon: tuple[LexiconRule, ...] | list[LexiconRule] = (),
        intent: IntentResolver | None = None,
    ) -> None:
        super().__init__(rules_path or RULES_PATH)
        self.overlay = RuleRouter(overlay_path) if overlay_path else None
        self.lexicon = tuple(lexicon)
        self.intent = intent
        self.last_trace: dict[str, Any] = {}

    def prepare(self, text: str) -> str:
        return apply_lexicon(text, self.lexicon) if self.lexicon else str(text or "").strip()

    def decide(self, text: str, *, pending_confirmation: bool = False) -> RouteDecision:
        fixed = self.prepare(text)
        decision = super().decide(fixed, pending_confirmation=pending_confirmation)
        stage = "rule"
        if decision.source == "llm_required" and self.overlay is not None:
            overlay = self.overlay.decide(fixed, pending_confirmation=pending_confirmation)
            if overlay.source == "rule":
                decision = RouteDecision(
                    overlay.scenario_id,
                    overlay.map_action,
                    overlay.path,
                    "rule",
                    "demo_overlay:" + overlay.reason,
                    overlay.matched_scenarios,
                    overlay.assistant_action,
                )
                stage = "overlay"
        if decision.source == "llm_required":
            stage = "llm_required"
        self.last_trace = {"input": text, "normalized": fixed, "stage": stage, "reason": decision.reason}
        return decision

    def resolve(
        self,
        text: str,
        *,
        pending_confirmation: bool = False,
        classifier: Callable[[str], Any] | None = None,
    ) -> RouteDecision:
        decision = self.decide(text, pending_confirmation=pending_confirmation)
        if decision.source != "llm_required":
            return decision
        if self.intent is None:
            # 하네스 의도 분류가 꺼져 있으면 기존 계약(라벨 1개 classifier)으로 내려간다.
            resolved = super().resolve(self.prepare(text), pending_confirmation=pending_confirmation, classifier=classifier)
            self.last_trace.update(stage="classifier" if classifier else "fixed", reason=resolved.reason)
            return resolved
        result = self.intent.resolve(self.prepare(text), pending_confirmation=pending_confirmation)
        verdict = result.verdict
        self.last_trace.update(
            stage="llm",
            reason=verdict.reason,
            llm_raw=None if result.response is None else result.response.raw,
            llm_error=None if result.response is None else result.response.error,
            llm_elapsed_s=None if result.response is None else round(result.response.elapsed_s, 3),
        )
        matched = (verdict.scenario_id,) if verdict.accepted else decision.matched_scenarios
        return RouteDecision(
            verdict.scenario_id,
            verdict.map_action,
            verdict.path,
            verdict.source,
            verdict.reason,
            matched,
            verdict.assistant_action,
        )
