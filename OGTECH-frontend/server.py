#!/usr/bin/env python3
"""OGTECH 키오스크 UI 정적 서버와 선택적 로컬 API 프록시.

외부 패키지 없이 동작한다. UI 서버는 모델을 직접 적재하지 않으며,
`/backend/*` 요청만 지정된 OGTECH 백엔드로 전달한다.

기본 문서 루트는 `MAP/kiosk`이고 기본 문서는 `video.html`이다.
구 의료 도메인 TEST UI는 2026-08-20에 저장소에서 제거했다.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
UI_ROOT = ROOT / "MAP" / "kiosk"
INDEX_DOCUMENT = "video.html"
MAX_REQUEST_BYTES = 12 * 1024 * 1024
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


@dataclass(frozen=True)
class ServerConfig:
    root: Path
    backend_url: str
    llm_health_url: str
    stt_endpoint: str
    gps_endpoint: str
    modem_endpoint: str
    index_document: str = INDEX_DOCUMENT


class OgtechUiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: ServerConfig) -> None:
        super().__init__(address, OgtechUiHandler)
        self.config = config


class OgtechUiHandler(BaseHTTPRequestHandler):
    server_version = "OgtechKioskUI/1.0"
    protocol_version = "HTTP/1.1"
    _head_only = False

    @property
    def config(self) -> ServerConfig:
        return self.server.config  # type: ignore[attr-defined]

    def do_HEAD(self) -> None:
        self._head_only = True
        self._handle_get()

    def do_GET(self) -> None:
        self._head_only = False
        self._handle_get()

    def _handle_get(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json({"ok": True, "service": "ogtech-kiosk-ui"})
            return
        if parsed.path == "/ui-api/status":
            self._send_json(self._status_payload())
            return
        if parsed.path.startswith("/backend/"):
            self._proxy("GET")
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        self._head_only = False
        parsed = urlparse(self.path)
        if parsed.path.startswith("/backend/"):
            self._proxy("POST")
            return
        self._send_json({"error": "지원하지 않는 경로입니다."}, HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, HEAD, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_static(self, request_path: str) -> None:
        relative = unquote(request_path).lstrip("/") or self.config.index_document
        candidate = (self.config.root / relative).resolve()
        try:
            common = os.path.commonpath((str(self.config.root.resolve()), str(candidate)))
        except ValueError:
            common = ""
        if common != str(self.config.root.resolve()) or not candidate.is_file():
            self._send_json({"error": "파일을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return

        try:
            content = candidate.read_bytes()
        except OSError:
            self._send_json({"error": "파일을 읽을 수 없습니다."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if candidate.suffix == ".html":
            mime = "text/html; charset=utf-8"
        elif candidate.suffix == ".css":
            mime = "text/css; charset=utf-8"
        elif candidate.suffix == ".js":
            mime = "text/javascript; charset=utf-8"

        cache = "no-cache" if candidate.suffix == ".html" else "public, max-age=300"
        self._send_bytes(content, HTTPStatus.OK, mime, {"Cache-Control": cache})

    def _proxy(self, method: str) -> None:
        parsed = urlparse(self.path)
        upstream_path = parsed.path[len("/backend") :]
        if not upstream_path.startswith("/api/"):
            self._send_json({"error": "API 프록시 경로만 허용됩니다."}, HTTPStatus.FORBIDDEN)
            return

        target = f"{self.config.backend_url}{upstream_path}"
        if parsed.query:
            target = f"{target}?{parsed.query}"

        body = None
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length < 0:  # 음수면 rfile.read(-1)이 EOF까지 블록한다(WORKLOG #19)
                self._send_json({"error": "Content-Length가 올바르지 않습니다."}, HTTPStatus.BAD_REQUEST)
                return
            if length > MAX_REQUEST_BYTES:
                self._send_json({"error": "요청 크기는 12MB를 넘을 수 없습니다."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            body = self.rfile.read(length) if length else b""

        headers = {"Accept": self.headers.get("Accept", "application/json")}
        content_type = self.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(target, data=body, method=method, headers=headers)

        try:
            with urlopen(request, timeout=35 if method == "POST" else 3) as response:
                payload = response.read()
                response_headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "content-length"
                }
                content_type = response.headers.get("Content-Type", "application/octet-stream")
                self._send_bytes(payload, response.status, content_type, response_headers)
        except HTTPError as error:
            payload = error.read()
            content_type = error.headers.get("Content-Type", "application/json; charset=utf-8")
            self._send_bytes(payload, error.code, content_type)
        except (URLError, TimeoutError, OSError):
            self._send_json(
                {"error": "OGTECH 백엔드에 연결할 수 없습니다."},
                HTTPStatus.BAD_GATEWAY,
            )

    def _status_payload(self) -> dict[str, Any]:
        backend_ok, backend_detail = probe_url(f"{self.config.backend_url}/api/state")
        llm_ok, llm_detail = probe_url(self.config.llm_health_url)
        return {
            "ui": {"ok": True},
            "backend": {"ok": backend_ok, "detail": backend_detail},
            "llm": {"ok": llm_ok, "detail": llm_detail},
            "integrations": {
                "stt_configured": bool(self.config.stt_endpoint),
                "gps_configured": bool(self.config.gps_endpoint),
                "modem_configured": bool(self.config.modem_endpoint),
            },
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(content, status, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _send_bytes(
        self,
        content: bytes,
        status: int,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if extra_headers:
            for key, value in extra_headers.items():
                if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() not in {"content-length", "content-type"}:
                    self.send_header(key, value)
        self.end_headers()
        if not self._head_only:
            self.wfile.write(content)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stdout.write(f"[TEST_UI] {self.address_string()} - {fmt % args}\n")


def probe_url(url: str, timeout: float = 0.7) -> tuple[bool, str]:
    if not url:
        return False, "not_configured"
    try:
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 400, f"http_{response.status}"
    except HTTPError as error:
        return False, f"http_{error.code}"
    except (URLError, TimeoutError, OSError):
        return False, "unreachable"


def create_server(
    host: str = "127.0.0.1",
    port: int = 8780,
    backend_url: str = "http://127.0.0.1:8765",
    llm_health_url: str = "http://127.0.0.1:8080/health",
    stt_endpoint: str = "",
    gps_endpoint: str = "",
    modem_endpoint: str = "",
    root: str | os.PathLike[str] | None = None,
    index_document: str = INDEX_DOCUMENT,
) -> OgtechUiServer:
    config = ServerConfig(
        root=Path(root).resolve() if root else UI_ROOT,
        index_document=index_document,
        backend_url=backend_url.rstrip("/"),
        llm_health_url=llm_health_url,
        stt_endpoint=stt_endpoint,
        gps_endpoint=gps_endpoint,
        modem_endpoint=modem_endpoint,
    )
    return OgtechUiServer((host, port), config)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OGTECH 키오스크 UI 경량 서버")
    parser.add_argument("--host", default=os.getenv("OGTECH_UI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OGTECH_UI_PORT", "8780")))
    parser.add_argument("--backend", default=os.getenv("OGTECH_BACKEND_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--llm-health", default=os.getenv("OGTECH_LLM_HEALTH_URL", "http://127.0.0.1:8080/health"))
    parser.add_argument("--stt-endpoint", default=os.getenv("OGTECH_STT_ENDPOINT", ""))
    parser.add_argument("--gps-endpoint", default=os.getenv("OGTECH_GPS_ENDPOINT", ""))
    parser.add_argument("--modem-endpoint", default=os.getenv("OGTECH_MODEM_ENDPOINT", ""))
    parser.add_argument(
        "--root",
        default=os.getenv("OGTECH_UI_ROOT", ""),
        help="정적 문서 루트. 비우면 MAP/kiosk 을 쓴다.",
    )
    parser.add_argument(
        "--index",
        default=os.getenv("OGTECH_UI_INDEX", INDEX_DOCUMENT),
        help="기본 문서. 비우면 video.html 을 쓴다.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = create_server(
        host=args.host,
        port=args.port,
        backend_url=args.backend,
        llm_health_url=args.llm_health,
        stt_endpoint=args.stt_endpoint,
        gps_endpoint=args.gps_endpoint,
        modem_endpoint=args.modem_endpoint,
        root=args.root or None,
        index_document=args.index or INDEX_DOCUMENT,
    )
    host, port = server.server_address[:2]
    print(f"OGTECH 키오스크 UI: http://{host}:{port}")
    print("종료: Ctrl+C")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
