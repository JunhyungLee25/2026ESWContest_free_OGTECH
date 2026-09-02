#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama-server 대역. PC에서 하네스 배관과 장애 폴백을 검증한다. 성능·정확도 증거가 아니다.

    python3 -m harness.mock_llm_server --port 8080 --mode ok
    모드: ok | timeout | http500 | garbage | empty | slow
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
import time
from typing import Any

from .device_state import estimate_tokens

MODES = ("ok", "timeout", "http500", "garbage", "empty", "slow")


def _has(text: str, *words: str) -> bool:
    return any(word in text for word in words)


def heuristic_intent(text: str, pending: bool) -> dict[str, str]:
    """결정적 키워드 표. 실제 모델의 대역일 뿐이며 규칙 라우터와는 무관하게 만들었다."""
    t = text.lower()
    if _has(t, "버섯", "열매", "나물", "먹어도", "식용", "섭취", "진통제", "항생제", "소염제", "복용", "진단", "수술", "절개", "봉합", "프롬프트", "내부 지시", "규칙을 잊") or re.search(r"(^|\s)약(을|은|이|를)?\s", t):
        return {"scenario_id": "refuse", "action": "none"}
    if pending:
        if _has(t, "아니", "싫어", "취소", "하지 마", "다른"):
            return {"scenario_id": "route", "action": "reject_destination"}
        if _has(t, "네", "응", "예", "그래", "좋아", "맞아", "설정", "지정", "거기", "그렇게"):
            return {"scenario_id": "route", "action": "confirm_destination"}
    if _has(t, "다시", "한 번 더", "뭐라고"):
        return {"scenario_id": "gear", "action": "repeat_response"}
    if _has(t, "3분 전"):
        return {"scenario_id": "route", "action": "route_recent_trace"}
    if _has(t, "베이스캠프", "베이스 캠프", "야영지", "캠프"):
        if _has(t, "저장", "등록", "기억", "찍", "남겨", "설정", "해 줘", "해줘") and not _has(t, "돌아", "복귀", "경로", "길", "안내", "가자"):
            return {"scenario_id": "route", "action": "save_basecamp"}
        return {"scenario_id": "route", "action": "route_basecamp"}
    if _has(t, "체크포인트", "체크 포인트", "경유지"):
        if _has(t, "저장", "등록", "기억", "찍", "남겨"):
            return {"scenario_id": "route", "action": "save_checkpoint"}
        return {"scenario_id": "route", "action": "route_last_checkpoint"}
    if _has(t, "목적지", "도착지"):
        if _has(t, "삭제", "지워", "취소", "해제"):
            return {"scenario_id": "route", "action": "clear_destination"}
        if _has(t, "거리", "얼마나", "시간", "방위"):
            return {"scenario_id": "route", "action": "status"}
        return {"scenario_id": "route", "action": "route_destination"}
    if _has(t, "야간", "적색", "빨간", "빨갛", "밤 모드", "나이트", "암순응"):
        if _has(t, "꺼", "해제", "종료", "주간", "원래"):
            return {"scenario_id": "gear", "action": "night_off"}
        return {"scenario_id": "gear", "action": "night_on"}
    if _has(t, "주간 모드", "원래 화면"):
        return {"scenario_id": "gear", "action": "night_off"}
    if _has(t, "물", "수원", "목마", "목말", "갈증", "샘", "호수", "식수", "급수"):
        if _has(t, "마셔", "정수", "괜찮", "안전"):
            return {"scenario_id": "water", "action": "none"}
        return {"scenario_id": "water", "action": "find_nearest_water"}
    if _has(t, "일몰", "해 지", "해지", "햇빛", "일조", "어두워", "귀환", "해 언제"):
        return {"scenario_id": "daylight", "action": "status"}
    if _has(t, "저체온", "한기", "떨려", "떨리", "체온"):
        return {"scenario_id": "warmth", "action": "none"}
    if _has(t, "온도", "습도", "기압", "날씨", "비가", "바람", "추워", "더워", "기온"):
        return {"scenario_id": "weather", "action": "status"}
    if _has(t, "배터리", "전원", "전력"):
        return {"scenario_id": "gear", "action": "status"}
    if _has(t, "장비", "헤드랜턴", "마이크", "스피커"):
        return {"scenario_id": "gear", "action": "none"}
    if _has(t, "co", "일산화탄소"):
        return {"scenario_id": "sleep_safety", "action": "status" if _has(t, "농도", "수치", "얼마") else "none"}
    if _has(t, "버너", "가스", "난로", "화로", "자는 동안", "잠들", "불 피"):
        return {"scenario_id": "sleep_safety", "action": "none"}
    if _has(t, "위치", "좌표", "어디야", "어디지", "gps", "지피에스", "위성", "정확도"):
        return {"scenario_id": "lost", "action": "status"}
    if _has(t, "잃", "모르겠", "헷갈", "못 찾", "조난"):
        return {"scenario_id": "lost", "action": "none"}
    if _has(t, "거리", "방위", "얼마나", "도착", "경로", "트레일", "등산로", "갈림길", "웨이포인트"):
        return {"scenario_id": "route", "action": "status"}
    if _has(t, "텐트", "타프", "야영", "비박", "쉼터", "대피", "잘 곳"):
        return {"scenario_id": "shelter", "action": "none"}
    if _has(t, "식량", "음식", "먹을 것", "먹을 거", "간식", "배고", "보급식"):
        return {"scenario_id": "food", "action": "none"}
    if _has(t, "발목", "무릎", "손목", "다쳐", "다쳤", "상처", "출혈", "피가", "화상", "골절", "의식", "넘어져", "아파"):
        return {"scenario_id": "injury", "action": "none"}
    if _has(t, "멧돼지", "곰", "뱀", "벌이", "야생동물", "동물"):
        return {"scenario_id": "wildlife", "action": "none"}
    if _has(t, "취소", "그만", "중단"):
        return {"scenario_id": "route", "action": "cancel"}
    return {"scenario_id": "unknown", "action": "none"}


def _parse_user_turn(content: str) -> tuple[str, bool]:
    text, pending = content, False
    match = re.search(r"발화:\s*(.*)", content)
    if match:
        text = match.group(1).strip()
    if re.search(r"확인 대기:\s*예", content):
        pending = True
    return text, pending


def _polish_lines(content: str) -> list[str]:
    match = re.search(r"\[SURVIVAL_CARD\]\n(.*?)\n\[DEVICE_STATE\]", content, re.S)
    card = match.group(1) if match else content
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", card) if s.strip()]
    lines = [s[:40] for s in sentences[:4]]
    while len(lines) < 2:
        lines.append("화면의 검수 안내를 확인하세요.")
    return lines


class MockLlmHandler(BaseHTTPRequestHandler):
    server_version = "OGTECHMockLLM/1"

    def log_message(self, *_args: Any) -> None:  # 조용히
        pass

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "ok"})
            return
        if self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": self.server.model, "object": "model"}]})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        payload = self._read()
        if self.path == "/tokenize":
            content = str(payload.get("content") or "")
            self._send(200, {"tokens": list(range(estimate_tokens(content)))})
            return
        if self.path != "/v1/chat/completions":
            self._send(404, {"error": "not found"})
            return
        self.server.calls += 1
        mode = self.server.mode
        if mode == "timeout":
            time.sleep(self.server.delay_s)
        if mode == "slow":
            time.sleep(self.server.delay_s)
        if mode == "http500":
            self._send(500, {"error": {"message": "의도한 서버 오류"}})
            return
        messages = payload.get("messages") or []
        last_user = ""
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "user":
                last_user = str(message.get("content") or "")
        schema = ((payload.get("response_format") or {}).get("json_schema") or {})
        name = schema.get("name")
        if mode == "garbage":
            content = "의도한 비JSON 응답"
        elif mode == "empty":
            content = "{}"
        elif name == "ogtech_polish":
            content = json.dumps({"lines": _polish_lines(last_user)}, ensure_ascii=False)
        elif name == "ogtech_scenario":
            text, pending = _parse_user_turn(last_user)
            content = json.dumps({"scenario_id": heuristic_intent(text, pending)["scenario_id"]}, ensure_ascii=False)
        else:
            text, pending = _parse_user_turn(last_user)
            content = json.dumps(heuristic_intent(text, pending), ensure_ascii=False, separators=(",", ":"))
        prompt_tokens = sum(estimate_tokens(str(m.get("content") or "")) for m in messages if isinstance(m, dict))
        completion_tokens = estimate_tokens(content)
        self._send(
            200,
            {
                "id": f"mock-{self.server.calls}",
                "object": "chat.completion",
                "model": self.server.model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens},
                "timings": {"prompt_n": prompt_tokens, "predicted_n": completion_tokens, "mock": True},
            },
        )


class MockLlmServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], *, mode: str = "ok", delay_s: float = 3.0, model: str = "qwen2.5-1.5b-instruct") -> None:
        super().__init__(address, MockLlmHandler)
        if mode not in MODES:
            raise ValueError(f"mode는 {MODES} 중 하나여야 합니다")
        self.mode = mode
        self.delay_s = float(delay_s)
        self.model = model
        self.calls = 0

    def handle_error(self, request, client_address) -> None:  # noqa: D401
        """클라이언트가 타임아웃으로 먼저 끊은 경우(BrokenPipe·ConnectionReset)는 정상 시나리오다."""
        import sys as _sys

        exc = _sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/v1/chat/completions"


def start_in_thread(*, mode: str = "ok", port: int = 0, delay_s: float = 3.0) -> MockLlmServer:
    server = MockLlmServer(("127.0.0.1", port), mode=mode, delay_s=delay_s)
    thread = threading.Thread(target=server.serve_forever, name="ogtech-mock-llm", daemon=True)
    thread.start()
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="OGTECH mock llama-server")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--mode", choices=MODES, default="ok")
    parser.add_argument("--delay", type=float, default=3.0, help="timeout/slow 모드의 지연 초")
    args = parser.parse_args()
    server = MockLlmServer(("127.0.0.1", args.port), mode=args.mode, delay_s=args.delay)
    print(f"mock llama-server: {server.url} mode={args.mode}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
