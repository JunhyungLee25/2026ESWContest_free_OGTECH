# -*- coding: utf-8 -*-
"""llama-server(OpenAI 호환) 클라이언트. 단일 호출, 재시도 없음, 로컬 주소만."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import socket
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class LlmResponse:
    ok: bool
    content: dict[str, Any] | None
    raw: str
    error: str | None
    elapsed_s: float
    usage: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, Any] = field(default_factory=dict)


def _check_local(url: str, label: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise ValueError(f"{label}는 로컬 HTTP 주소만 허용합니다: {url}")


class LlmClient:
    def __init__(
        self,
        url: str,
        *,
        model: str,
        timeout_s: float = 2.0,
        temperature: float = 0.0,
        seed: int = 0,
        cache_prompt: bool = True,
        health_url: str | None = None,
        tokenize_url: str | None = None,
    ) -> None:
        _check_local(url, "LLM 서버 주소")
        self.url = url
        self.model = model
        self.timeout_s = float(timeout_s)
        self.temperature = float(temperature)
        self.seed = int(seed)
        self.cache_prompt = bool(cache_prompt)
        base = url.split("/v1/")[0]
        self.health_url = health_url or (base + "/health")
        self.tokenize_url = tokenize_url or (base + "/tokenize")
        _check_local(self.health_url, "health 주소")
        _check_local(self.tokenize_url, "tokenize 주소")

    def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        *,
        max_tokens: int,
        timeout_s: float | None = None,
        slot: int = -1,
    ) -> LlmResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": int(max_tokens),
            "stream": False,
            "seed": self.seed,
            "cache_prompt": self.cache_prompt,
            "response_format": {"type": "json_schema", "json_schema": schema},
        }
        if slot >= 0:
            payload["id_slot"] = int(slot)
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, OSError) as exc:
            elapsed = time.monotonic() - started
            return LlmResponse(False, None, "", f"{type(exc).__name__}: {exc}"[:200], elapsed)
        elapsed = time.monotonic() - started
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return LlmResponse(False, None, json.dumps(body, ensure_ascii=False)[:200], "malformed_response", elapsed)
        if not isinstance(content, str):
            return LlmResponse(False, None, "", "content_not_string", elapsed)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return LlmResponse(False, None, content[:200], "content_not_json", elapsed)
        if not isinstance(parsed, dict):
            return LlmResponse(False, None, content[:200], "content_not_object", elapsed)
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        timings = body.get("timings") if isinstance(body.get("timings"), dict) else {}
        return LlmResponse(True, parsed, content, None, elapsed, usage, timings)

    def health(self, timeout_s: float = 1.0) -> bool:
        try:
            with urllib.request.urlopen(self.health_url, timeout=timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, OSError):
            return False
        return isinstance(body, dict) and body.get("status") == "ok"

    def tokenize(self, text: str, timeout_s: float = 5.0) -> int | None:
        request = urllib.request.Request(
            self.tokenize_url,
            data=json.dumps({"content": text}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, OSError):
            return None
        tokens = body.get("tokens") if isinstance(body, dict) else None
        return len(tokens) if isinstance(tokens, list) else None
