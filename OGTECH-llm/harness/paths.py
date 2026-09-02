# -*- coding: utf-8 -*-
"""하네스 경로와 Co-LLM 모듈 import 경로 설정."""

from __future__ import annotations

from pathlib import Path
import sys

HARNESS_DIR = Path(__file__).resolve().parent
LLM_ROOT = HARNESS_DIR.parent
CO_LLM_DIR = LLM_ROOT / "Co-LLM"
CO_LLM_SCRIPTS = CO_LLM_DIR / "scripts"
CONFIG_DIR = LLM_ROOT / "config"
RESULTS_DIR = LLM_ROOT / "results"


def ensure_co_llm_on_path() -> None:
    """Co-LLM/scripts(ogtech_core·product_assistant)와 Co-LLM(config.py)을 import 가능하게 한다."""
    for path in (CO_LLM_SCRIPTS, CO_LLM_DIR):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def resolve_config_path(name_or_path: str | Path, config_dir: Path = CONFIG_DIR) -> Path:
    path = Path(name_or_path)
    if not path.is_absolute():
        path = config_dir / path
    return path
