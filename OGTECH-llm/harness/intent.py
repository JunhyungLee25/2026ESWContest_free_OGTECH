# -*- coding: utf-8 -*-
"""역할 1+2 — 발화 → {scenario_id, action} 한 번 호출. 출력은 guard가 검증한다."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .guard import IntentVerdict, _fixed, validate_intent
from .llm_client import LlmClient, LlmResponse
from .paths import CONFIG_DIR, resolve_config_path


def format_user_turn(text: str, pending: bool) -> str:
    return f"발화: {text}\n확인 대기: {'예' if pending else '아니오'}"


def load_fewshot(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict) or "user" not in row or "assistant" not in row:
            raise ValueError(f"{path.name}:{number} few-shot 형식 오류")
        rows.append(row)
    return rows


@dataclass(frozen=True)
class IntentResult:
    text: str
    pending: bool
    verdict: IntentVerdict
    response: LlmResponse | None

    @property
    def note(self) -> str:
        if self.response is None:
            return "intent_disabled"
        if not self.response.ok:
            return f"llm_error={self.response.error}"
        usage = self.response.usage or {}
        return "프롬프트 %s tok / 생성 %s tok / %.3f s" % (
            usage.get("prompt_tokens", "?"),
            usage.get("completion_tokens", "?"),
            self.response.elapsed_s,
        )


class IntentResolver:
    def __init__(
        self,
        client: LlmClient,
        *,
        system_prompt: str,
        fewshot: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int = 32,
        max_utterance_chars: int = 240,
        allow_actions: frozenset[str] | set[str],
        allow_life_status_readout: bool = False,
        timeout_s: float | None = None,
        warmup_timeout_s: float = 20.0,
        slot: int = -1,
    ) -> None:
        self.client = client
        self.system_prompt = system_prompt.strip()
        self.fewshot = list(fewshot)
        self.schema = schema
        self.max_tokens = int(max_tokens)
        self.max_utterance_chars = int(max_utterance_chars)
        self.allow_actions = frozenset(allow_actions)
        self.allow_life_status_readout = bool(allow_life_status_readout)
        self.timeout_s = timeout_s
        self.warmup_timeout_s = float(warmup_timeout_s)
        self.slot = int(slot)

    @classmethod
    def from_policy(cls, client: LlmClient, policy: dict[str, Any], config_dir: Path = CONFIG_DIR) -> "IntentResolver":
        section = policy["intent"]
        llm = policy.get("llm") or {}
        system_prompt = resolve_config_path(section["system_prompt_file"], config_dir).read_text(encoding="utf-8")
        fewshot = load_fewshot(resolve_config_path(section["fewshot_file"], config_dir))
        schema = json.loads(resolve_config_path(section["schema_file"], config_dir).read_text(encoding="utf-8"))
        return cls(
            client,
            system_prompt=system_prompt,
            fewshot=fewshot,
            schema=schema,
            max_tokens=int(section.get("max_tokens", 32)),
            max_utterance_chars=int(section.get("max_utterance_chars", 240)),
            allow_actions=frozenset(section.get("allow_actions") or []),
            allow_life_status_readout=bool(section.get("allow_life_status_readout", False)),
            timeout_s=llm.get("timeout_s"),
            warmup_timeout_s=float(llm.get("warmup_timeout_s", 20.0)),
            slot=int(llm.get("intent_slot", -1)),
        )

    # 고정 프리픽스(system + few-shot). 매 호출 동일해야 KV 캐시가 산다.
    def prefix_messages(self) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": self.system_prompt}]
        for row in self.fewshot:
            messages.append({"role": "user", "content": format_user_turn(str(row["user"]), bool(row.get("pending")))})
            messages.append({"role": "assistant", "content": json.dumps(row["assistant"], ensure_ascii=False, separators=(",", ":"))})
        return messages

    def build_messages(self, text: str, pending: bool) -> list[dict[str, str]]:
        clipped = str(text or "")[: self.max_utterance_chars]
        return self.prefix_messages() + [{"role": "user", "content": format_user_turn(clipped, pending)}]

    def resolve(self, text: str, *, pending_confirmation: bool = False) -> IntentResult:
        response = self.client.chat_json(
            self.build_messages(text, pending_confirmation),
            self.schema,
            max_tokens=self.max_tokens,
            timeout_s=self.timeout_s,
            slot=self.slot,
        )
        if not response.ok:
            return IntentResult(text, pending_confirmation, _fixed("classifier_failed_no_retry"), response)
        verdict = validate_intent(
            response.content,
            pending_confirmation=pending_confirmation,
            allow_actions=self.allow_actions,
            allow_life_status_readout=self.allow_life_status_readout,
        )
        return IntentResult(text, pending_confirmation, verdict, response)

    def warmup(self) -> LlmResponse:
        """프리픽스를 슬롯 KV 캐시에 올린다. 결과는 버린다."""
        return self.client.chat_json(
            self.build_messages("워밍업", False),
            self.schema,
            max_tokens=self.max_tokens,
            timeout_s=self.warmup_timeout_s,
            slot=self.slot,
        )
