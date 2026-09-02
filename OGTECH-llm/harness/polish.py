# -*- coding: utf-8 -*-
"""역할 3 — 검수 카드 문장 2~4줄 다듬기. 기본 shadow(기록만). speak일 때만 스피커 문장을 교체한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .device_state import serialize_device_state
from .guard import compile_forbidden, validate_polish
from .llm_client import LlmClient, LlmResponse
from .paths import CONFIG_DIR, LLM_ROOT, resolve_config_path

MODES = ("off", "shadow", "speak")


@dataclass(frozen=True)
class PolishResult:
    mode: str
    lines: tuple[str, ...] | None
    spoken_lines: tuple[str, ...] | None
    reason: str
    elapsed_s: float
    response: LlmResponse | None = None


class Polisher:
    def __init__(
        self,
        client: LlmClient | None,
        *,
        mode: str,
        system_prompt: str,
        schema: dict[str, Any],
        forbidden: tuple[str, ...] | list[str],
        max_tokens: int = 96,
        timeout_s: float = 1.5,
        device_state_max_tokens: int = 60,
        shadow_log: Path | None = None,
        slot: int = -1,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"polish.mode는 {MODES} 중 하나여야 합니다: {mode}")
        self.client = client
        self.mode = mode
        self.system_prompt = system_prompt.strip()
        self.schema = schema
        self.forbidden = compile_forbidden(tuple(forbidden))
        self.max_tokens = int(max_tokens)
        self.timeout_s = float(timeout_s)
        self.device_state_max_tokens = int(device_state_max_tokens)
        self.shadow_log = shadow_log
        self.slot = int(slot)

    @classmethod
    def from_policy(cls, client: LlmClient | None, policy: dict[str, Any], config_dir: Path = CONFIG_DIR) -> "Polisher":
        section = policy["polish"]
        llm = policy.get("llm") or {}
        system_prompt = resolve_config_path(section["system_prompt_file"], config_dir).read_text(encoding="utf-8")
        schema = json.loads(resolve_config_path(section["schema_file"], config_dir).read_text(encoding="utf-8"))
        forbidden_payload = json.loads(resolve_config_path(section["forbidden_file"], config_dir).read_text(encoding="utf-8"))
        shadow_log = section.get("shadow_log")
        return cls(
            client,
            mode=str(section.get("mode", "shadow")),
            system_prompt=system_prompt,
            schema=schema,
            forbidden=tuple(forbidden_payload.get("patterns") or []),
            max_tokens=int(section.get("max_tokens", 96)),
            timeout_s=float(section.get("timeout_s", 1.5)),
            device_state_max_tokens=int(section.get("device_state_max_tokens", 60)),
            shadow_log=None if not shadow_log else (LLM_ROOT / shadow_log),
            slot=int(llm.get("polish_slot", -1)),
        )

    def build_messages(self, card_text: str, device_state: str, scenario_id: str) -> list[dict[str, str]]:
        # 불변(system) → 준가변(card) → 가변(state) → 지시 순서. §5 프롬프트 조립 순서.
        user = (
            f"[SURVIVAL_CARD]\n{card_text}\n"
            f"[DEVICE_STATE]\n{device_state}\n"
            f"[USER]\n{scenario_id} 카드를 규칙대로 2~4줄로 다듬어라."
        )
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user},
        ]

    def polish(self, card_text: str, *, device: dict[str, Any] | None, scenario_id: str) -> PolishResult:
        if self.mode == "off" or self.client is None:
            return PolishResult(self.mode, None, None, "polish_off", 0.0)
        state = serialize_device_state(device, self.device_state_max_tokens)
        response = self.client.chat_json(
            self.build_messages(card_text, state, scenario_id),
            self.schema,
            max_tokens=self.max_tokens,
            timeout_s=self.timeout_s,
            slot=self.slot,
        )
        if not response.ok:
            verdict_lines, reason = None, f"llm_error:{response.error}"
        else:
            verdict = validate_polish(
                response.content,
                source_text=card_text + "\n" + state,
                forbidden=self.forbidden,
            )
            verdict_lines, reason = verdict.lines, verdict.reason
        spoken = verdict_lines if (verdict_lines is not None and self.mode == "speak") else None
        self._log(card_text, state, scenario_id, response, verdict_lines, reason)
        return PolishResult(self.mode, verdict_lines, spoken, reason, response.elapsed_s, response)

    def _log(
        self,
        card_text: str,
        state: str,
        scenario_id: str,
        response: LlmResponse,
        lines: tuple[str, ...] | None,
        reason: str,
    ) -> None:
        if self.shadow_log is None:
            return
        try:
            self.shadow_log.parent.mkdir(parents=True, exist_ok=True)
            with self.shadow_log.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "mode": self.mode,
                            "scenario_id": scenario_id,
                            "card": card_text,
                            "device_state": state,
                            "raw": response.raw,
                            "lines": lines,
                            "reason": reason,
                            "elapsed_s": round(response.elapsed_s, 3),
                            "usage": response.usage,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            pass
