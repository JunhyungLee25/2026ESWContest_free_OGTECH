# -*- coding: utf-8 -*-
"""OGTECH 시연용 LLM 하네스.

    from harness import build_harness, DemoAssistant
    H = build_harness()                       # config/harness_policy.json
    assistant = DemoAssistant(map_client, router=H.router, polisher=H.polisher)
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .demo_assistant import DemoAssistant
from .demo_router import DemoRouter
from .intent import IntentResolver
from .llm_client import LlmClient
from .normalize import load_lexicon
from .paths import CONFIG_DIR, LLM_ROOT, RESULTS_DIR, resolve_config_path
from .polish import Polisher

DEFAULT_POLICY = CONFIG_DIR / "harness_policy.json"


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    target = resolve_config_path(path or DEFAULT_POLICY, CONFIG_DIR)
    policy = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or policy.get("version") != 1:
        raise ValueError(f"지원하지 않는 하네스 정책 버전입니다: {target}")
    return policy


@dataclass
class DemoHarness:
    policy: dict[str, Any]
    client: LlmClient | None
    intent: IntentResolver | None
    polisher: Polisher | None
    router: DemoRouter

    def warmup(self):
        if self.intent is None:
            return None
        return self.intent.warmup()


def build_harness(
    policy_path: str | Path | None = None,
    *,
    llm_url: str | None = None,
    intent_enabled: bool | None = None,
    polish_mode: str | None = None,
    config_dir: Path = CONFIG_DIR,
) -> DemoHarness:
    policy = load_policy(policy_path)
    llm = dict(policy.get("llm") or {})
    if llm_url:
        llm["url"] = llm_url
        llm.pop("health_url", None)
        llm.pop("tokenize_url", None)
    policy["llm"] = llm
    if intent_enabled is not None:
        policy.setdefault("intent", {})["enabled"] = bool(intent_enabled)
    if polish_mode is not None:
        policy.setdefault("polish", {})["mode"] = polish_mode

    client: LlmClient | None = None
    need_client = bool(policy["intent"].get("enabled")) or policy["polish"].get("mode", "off") != "off"
    if need_client:
        client = LlmClient(
            llm["url"],
            model=str(llm.get("model", "qwen2.5-1.5b-instruct")),
            timeout_s=float(llm.get("timeout_s", 2.0)),
            temperature=float(llm.get("temperature", 0.0)),
            seed=int(llm.get("seed", 0)),
            cache_prompt=bool(llm.get("cache_prompt", True)),
            health_url=llm.get("health_url"),
            tokenize_url=llm.get("tokenize_url"),
        )
    intent = IntentResolver.from_policy(client, policy, config_dir) if (client and policy["intent"].get("enabled")) else None
    polisher = Polisher.from_policy(client, policy, config_dir) if policy["polish"].get("mode", "off") != "off" else None

    lexicon = ()
    if (policy.get("stt_lexicon") or {}).get("enabled", True):
        lexicon = load_lexicon(resolve_config_path((policy.get("stt_lexicon") or {}).get("file", "stt_lexicon.json"), config_dir))
    overlay = None
    if (policy.get("demo_rules") or {}).get("enabled", True):
        overlay = resolve_config_path((policy.get("demo_rules") or {}).get("file", "keyword_rules_demo.yaml"), config_dir)
    router = DemoRouter(overlay_path=overlay, lexicon=lexicon, intent=intent)
    return DemoHarness(policy=policy, client=client, intent=intent, polisher=polisher, router=router)


__all__ = [
    "CONFIG_DIR",
    "LLM_ROOT",
    "RESULTS_DIR",
    "DemoAssistant",
    "DemoHarness",
    "DemoRouter",
    "IntentResolver",
    "LlmClient",
    "Polisher",
    "build_harness",
    "load_policy",
]
