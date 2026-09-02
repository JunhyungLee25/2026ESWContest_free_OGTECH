# -*- coding: utf-8 -*-
"""STT 오인식 보정 사전. 라우팅 입력에만 쓰고 스피커 문장에는 쓰지 않는다."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .paths import CONFIG_DIR, resolve_config_path


@dataclass(frozen=True)
class LexiconRule:
    pattern: re.Pattern[str]
    replace: str
    source: str


def load_lexicon(path: str | Path | None = None) -> tuple[LexiconRule, ...]:
    target = resolve_config_path(path or "stt_lexicon.json", CONFIG_DIR)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError(f"지원하지 않는 STT 사전 버전입니다: {target}")
    rules: list[LexiconRule] = []
    for item in payload.get("rules") or []:
        if not isinstance(item, dict) or not item.get("pattern"):
            raise ValueError("STT 사전 항목에는 pattern이 필요합니다")
        rules.append(
            LexiconRule(
                pattern=re.compile(str(item["pattern"])),
                replace=str(item.get("replace", "")),
                source=str(item.get("source", "")),
            )
        )
    return tuple(rules)


def apply_lexicon(text: str, rules: tuple[LexiconRule, ...] | list[LexiconRule]) -> str:
    value = str(text or "")
    for rule in rules:
        value = rule.pattern.sub(rule.replace, value)
    return re.sub(r"\s+", " ", value).strip()
