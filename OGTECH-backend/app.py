#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OGTECH 백엔드 — 안전 분기 규칙 엔진 HTTP 서비스 (:8765).

정본 분기 엔진(OGTECH-llm Co-LLM/scripts/ogtech_core.py)의 vendored 사본(core/)을
표준 라이브러리 http.server 로 노출한다. LLM 은 이 서비스에 없다:
- 경로 B(생명 관련·refuse)는 규칙이 확정하고 검수된 고정 카드를 돌려준다.
- 규칙이 못 정한 발화(llm_required)는 LLM 없이 확정하지 않고 unknown 고정 카드로
  폴백한다(classifier_unavailable). LLM 다듬기(경로 A)는 Co-LLM 파이프라인의 몫이다.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from core.ogtech_core import (  # noqa: E402
    CardRenderer,
    RuleRouter,
    SCENARIO_IDS,
    VoiceContractError,
)

MAX_BODY_BYTES = 64 * 1024


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class BackendHandler(BaseHTTPRequestHandler):
    """규칙 엔진은 초기화 후 읽기 전용이라 핸들러 간 공유에 락이 필요 없다."""

    router: RuleRouter
    renderer: CardRenderer
    protocol_version = "HTTP/1.1"

    # ---------- helpers ----------
    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if int(status) >= 400:
            # 오류 응답 시 미소비 요청 본문이 keep-alive 커넥션의 다음 요청으로 오파싱되지 않도록 닫는다.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else 0
        except (TypeError, ValueError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Content-Length가 정수가 아닙니다")
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError(HTTPStatus.BAD_REQUEST, "본문 크기가 허용 범위를 벗어났습니다")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON 본문이 필요합니다")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "본문이 유효한 JSON이 아닙니다")
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON 객체가 필요합니다")
        return payload

    def _require_text(self, payload: dict) -> str:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "text 필드(비어 있지 않은 문자열)가 필요합니다")
        return text

    # ---------- routes ----------
    def do_GET(self) -> None:  # noqa: N802 - 표준 라이브러리 규약
        try:
            path = urlsplit(self.path).path
            if path == "/api/health":
                self._send_json(HTTPStatus.OK, {
                    "status": "ok",
                    "engine": "rule_router",
                    "scenarios": len(SCENARIO_IDS),
                    "llm": False,
                })
                return
            if path.startswith("/api/card/"):
                scenario_id = path.rsplit("/", 1)[-1]
                if scenario_id not in SCENARIO_IDS:
                    raise ApiError(HTTPStatus.NOT_FOUND, f"알 수 없는 scenario_id: {scenario_id}")
                card = self.renderer.render(scenario_id)
                self._send_json(HTTPStatus.OK, asdict(card))
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "알 수 없는 경로입니다")
        except ApiError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except VoiceContractError as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        except Exception:  # noqa: BLE001 - 어떤 예외도 응답 없이 죽지 않는다
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"})

    def do_POST(self) -> None:  # noqa: N802 - 표준 라이브러리 규약
        try:
            path = urlsplit(self.path).path
            if path == "/api/classify":
                payload = self._read_json()
                text = self._require_text(payload)
                decision = self.router.resolve(text)  # classifier 없음 → 생명/미확정은 고정 폴백
                self._send_json(HTTPStatus.OK, asdict(decision))
                return
            if path == "/api/respond":
                payload = self._read_json()
                text = self._require_text(payload)
                device = payload.get("device")
                if device is not None and not isinstance(device, dict):
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "device는 JSON 객체여야 합니다")
                decision = self.router.resolve(text)
                card = self.renderer.render(decision.scenario_id, device)
                self._send_json(HTTPStatus.OK, {
                    "decision": asdict(decision),
                    "card": asdict(card),
                })
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "알 수 없는 경로입니다")
        except ApiError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except VoiceContractError as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        except Exception:  # noqa: BLE001
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"})

    def log_message(self, fmt: str, *args) -> None:  # 콘솔 소음 억제
        pass


def build_server(host: str, port: int) -> ThreadingHTTPServer:
    handler = BackendHandler
    handler.router = RuleRouter()
    handler.renderer = CardRenderer()
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="OGTECH 백엔드 규칙 엔진 서비스")
    parser.add_argument("--host", default=os.environ.get("OGTECH_BACKEND_HOST", "127.0.0.1"),
                        help="바인드 주소 (기본 127.0.0.1 — 키오스크 로컬 전용)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("OGTECH_BACKEND_PORT", "8765")))
    args = parser.parse_args()
    server = build_server(args.host, args.port)
    print(f"OGTECH backend rule engine on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
