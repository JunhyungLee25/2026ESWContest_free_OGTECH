# -*- coding: utf-8 -*-
"""기존 engines.classify_scenario 호환 진입점 — 라벨 1개만 필요할 때.

    from harness.classify import classify_scenario
    ProductAssistant(client, classifier=classify_scenario)
"""

from __future__ import annotations

from . import build_harness

_HARNESS = None


def classify_scenario(user_text: str) -> tuple[str, str]:
    global _HARNESS
    if _HARNESS is None:
        _HARNESS = build_harness()
    if _HARNESS.intent is None:
        return "unknown", "intent_disabled"
    result = _HARNESS.intent.resolve(_HARNESS.router.prepare(str(user_text or "")), pending_confirmation=False)
    verdict = result.verdict
    if not verdict.accepted:
        return "unknown", verdict.reason
    return verdict.scenario_id, result.note
