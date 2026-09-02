"""OGTECH 키오스크 정적 UI에 STM32 UART 실측값을 제공한다.

출처: Jetson `/home/kit/00_TEST/MAP/시연용/uart_server.py`(팀원 작성 2026-08-29, 실동작본)를 2026-08-30 저장소로 반입.
프로토콜: 보드 펌웨어가 1 Hz·115200으로 보내는
  `$SA1,seq,uptime_ms,dht_valid,temp_x10,hum_x10,co_state,co_ppm,gps_state,lat_e7,lon_e7,sats*XX`
  (XX = `$`와 `*` 사이 바이트의 XOR, 16진 2자리). 필드는 OGTECH-embedded `uart4_integration`의 `$OGT1`과 같고
  접두어만 다르다. `$SA1` 펌웨어 소스는 저장소에 없다(WORKLOG #38).
실행: python3 uart_server.py --port /dev/ttyTHS0 --baud 115200 --http-port 8791
  전제: `sudo systemctl disable --now nvgetty`(부팅 시 getty가 ttyTHS0를 점유하는 것 차단), 실행 사용자는 dialout 그룹.
화면: 이 서버가 서빙하는 `index.html`/`app.js`는 `/api/telemetry`를 1초 폴링하는 Jetson 개조본이어야 한다
  (저장소 kiosk/ 본은 아직 폴링하지 않음 — frontend 브랜치 `jetson/live-2026-08-30` 참고, WORKLOG #39).
"""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Any

import serial


ROOT = Path(__file__).resolve().parent


def parse_sa1(line: str) -> dict[str, Any]:
    sentence = line.strip()
    if not sentence.startswith("$SA1,") or "*" not in sentence:
        raise ValueError("SA1 프레임이 아닙니다")
    body, checksum_text = sentence[1:].rsplit("*", 1)
    checksum = 0
    for character in body:
        checksum ^= ord(character)
    if checksum != int(checksum_text[:2], 16):
        raise ValueError("체크섬 불일치")

    fields = body.split(",")
    if len(fields) != 12:
        raise ValueError(f"필드 수 오류: {len(fields)}")
    gps_state = int(fields[8])
    lat_e7 = int(fields[9])
    lon_e7 = int(fields[10])
    return {
        "seq": int(fields[1]),
        "uptime_ms": int(fields[2]),
        "dht_valid": fields[3] == "1",
        "temperature_c": int(fields[4]) / 10.0,
        "humidity_pct": int(fields[5]) / 10.0,
        "co_state": int(fields[6]),
        "co_ppm": int(fields[7]),
        "gps_state": gps_state,
        "latitude": lat_e7 / 10_000_000.0 if gps_state == 2 else None,
        "longitude": lon_e7 / 10_000_000.0 if gps_state == 2 else None,
        "satellites": int(fields[11]),
    }


class TelemetryReader:
    def __init__(self, port: str, baud: int) -> None:
        self.port = port
        self.baud = baud
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {"connected": False, "error": "수신 대기"}
        self.last_received = 0.0

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="stm32-uart").start()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            result = dict(self.data)
            received = self.last_received
        age = max(0.0, time.monotonic() - received) if received else 9999.0
        result["age_s"] = round(age, 1)
        result["connected"] = bool(received and age <= 3.0)
        return result

    def _run(self) -> None:
        while True:
            try:
                with serial.Serial(self.port, self.baud, timeout=1) as uart:
                    while True:
                        raw = uart.readline()
                        if not raw:
                            continue
                        try:
                            parsed = parse_sa1(raw.decode("ascii", errors="ignore"))
                        except (ValueError, UnicodeError):
                            continue
                        with self.lock:
                            self.data = {**parsed, "connected": True, "error": None}
                            self.last_received = time.monotonic()
            except (OSError, serial.SerialException) as exc:
                with self.lock:
                    self.data["connected"] = False
                    self.data["error"] = str(exc)
                time.sleep(2)


class Handler(SimpleHTTPRequestHandler):
    telemetry: TelemetryReader

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/api/telemetry":
            payload = json.dumps(self.telemetry.snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyTHS0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--http-port", type=int, default=8791)
    args = parser.parse_args()

    reader = TelemetryReader(args.port, args.baud)
    reader.start()
    Handler.telemetry = reader
    server = ThreadingHTTPServer(("127.0.0.1", args.http_port), Handler)
    print(f"SafeAid UI: http://127.0.0.1:{args.http_port}")
    print(f"STM32 UART: {args.port} @ {args.baud}")
    server.serve_forever()


if __name__ == "__main__":
    main()
