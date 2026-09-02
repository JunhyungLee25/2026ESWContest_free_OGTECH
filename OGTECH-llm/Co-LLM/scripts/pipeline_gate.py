#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STT·LLM·TTS 제품 파이프라인의 프로세스 간 단일 실행 게이트."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import threading
import time

import config as C


_WINDOWS_LOCK = threading.Lock()


@contextmanager
def exclusive_pipeline(timeout_s: float = C.PIPELINE_LOCK_TIMEOUT_S):
    """Jetson에서는 flock, 개발 Windows에서는 프로세스 내 Lock을 사용한다."""
    if os.name != "posix":
        acquired = _WINDOWS_LOCK.acquire(timeout=timeout_s)
        if not acquired:
            raise RuntimeError("음성 파이프라인 잠금 대기 시간이 초과되었습니다")
        try:
            yield
        finally:
            _WINDOWS_LOCK.release()
        return

    import fcntl

    path = Path(C.PIPELINE_LOCK_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("음성 파이프라인 잠금 대기 시간이 초과되었습니다")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
