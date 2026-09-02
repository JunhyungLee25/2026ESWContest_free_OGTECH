#!/usr/bin/env python3
"""물리 전원 길게 누름을 Jetson 정상 종료와 STM32 gate ACK로 연결한다."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from typing import Any, Callable, Iterator
import urllib.error
import urllib.parse
import urllib.request


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
POWER_HOLD_MIN_MS = 2000


class PowerButtonDetector:
    def accept(self, event: dict[str, Any]) -> bool:
        return bool(
            event.get("button") == "power"
            and event.get("state") == "released"
            and event.get("coordinates_exposed") is False
            and int(event.get("held_ms") or 0) >= POWER_HOLD_MIN_MS
        )


class PowerAckConfirmationError(RuntimeError):
    """ACK POST 이후 응답을 확인하지 못해 STM32 arm 여부가 불확실한 상태."""


def _validated_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("전원 제어 API는 로컬 HTTP 주소만 사용할 수 있습니다")
    return base_url.rstrip("/")


def button_events(base_url: str) -> Iterator[dict[str, Any]]:
    request = urllib.request.Request(
        _validated_base_url(base_url) + "/api/buttons/events",
        headers={"Accept": "text/event-stream"},
    )
    with urllib.request.urlopen(request, timeout=30.0) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[5:].strip())
            if isinstance(payload, dict):
                yield payload


def _read_power_event(base: str) -> dict[str, Any]:
    with urllib.request.urlopen(base + "/api/gps", timeout=2.0) as response:
        gps = json.loads(response.read().decode("utf-8"))
    if not isinstance(gps, dict):
        raise RuntimeError("MAP GPS 응답이 객체가 아닙니다")
    event = ((gps.get("hardware_power") or {}).get("last_event") or {})
    return event if isinstance(event, dict) else {}


def _wait_for_power_event(
    base: str,
    *,
    expected_state: str,
    expected_pending: bool,
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        event = _read_power_event(base)
        if (
            event.get("state") == expected_state
            and event.get("gate_on") is True
            and event.get("shutdown_pending") is expected_pending
        ):
            return event
        time.sleep(0.1)
    raise RuntimeError(f"STM32 {expected_state} 수신을 확인하지 못했습니다")


def _post_power_command(base: str, endpoint: str) -> None:
    request = urllib.request.Request(
        base + endpoint,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2.0) as response:
        queued = json.loads(response.read().decode("utf-8"))
    if not isinstance(queued, dict) or queued.get("queued") is not True:
        raise RuntimeError("STM32 전원 명령이 송신 큐에 들어가지 않았습니다")


def request_power_ack(base_url: str, *, timeout_s: float = 4.0) -> dict[str, Any]:
    base = _validated_base_url(base_url)
    _wait_for_power_event(
        base,
        expected_state="shutdown_requested",
        expected_pending=True,
        timeout_s=timeout_s,
    )
    try:
        _post_power_command(base, "/api/power/shutdown-ack")
        return _wait_for_power_event(
            base,
            expected_state="shutdown_ack",
            expected_pending=True,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        # POST가 서버/직렬 장치에 도달했는지는 오류 종류만으로 알 수 없다.
        raise PowerAckConfirmationError(
            "STM32 ACK 결과가 불확실해 gate 차단 예약 취소가 필요합니다"
        ) from exc


def request_power_cancel(base_url: str, *, timeout_s: float = 4.0) -> dict[str, Any]:
    base = _validated_base_url(base_url)
    _post_power_command(base, "/api/power/shutdown-cancel")
    return _wait_for_power_event(
        base,
        expected_state="shutdown_cancelled",
        expected_pending=False,
        timeout_s=timeout_s,
    )


def perform_poweroff(
    base_url: str,
    systemctl: str,
    *,
    run_command: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """STM32를 arm한 뒤 systemd job 완료를 기다리고 불확실/실패 시 취소한다."""
    try:
        ack = request_power_ack(base_url)
    except PowerAckConfirmationError as ack_error:
        try:
            request_power_cancel(base_url)
        except Exception as cancel_error:
            raise RuntimeError(
                "STM32 ACK 확인과 gate 차단 예약 취소가 모두 실패했습니다"
            ) from cancel_error
        raise RuntimeError(
            "STM32 ACK를 확인하지 못해 gate 차단 예약을 취소했습니다"
        ) from ack_error
    try:
        run_command(
            [systemctl, "poweroff"],
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as poweroff_error:
        try:
            request_power_cancel(base_url)
        except (
            OSError,
            RuntimeError,
            urllib.error.URLError,
            json.JSONDecodeError,
            ValueError,
        ) as cancel_error:
            raise RuntimeError(
                "systemd 종료 요청과 STM32 gate 차단 예약 취소가 모두 실패했습니다"
            ) from cancel_error
        raise RuntimeError(
            "systemd 종료 요청이 실패해 STM32 gate 차단 예약을 취소했습니다"
        ) from poweroff_error
    return ack


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OGTECH 물리 전원 정상 종료 브리지")
    parser.add_argument("--map-url", default="http://127.0.0.1:8790")
    parser.add_argument("--systemctl", default="/usr/bin/systemctl")
    parser.add_argument("--no-poweroff", action="store_true", help="이벤트만 확인하고 종료하지 않음")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    detector = PowerButtonDetector()
    print("물리 전원 버튼 대기: 2초 길게 누르면 정상 종료를 요청합니다")
    while True:
        try:
            for event in button_events(args.map_url):
                if not detector.accept(event):
                    continue
                if args.no_poweroff:
                    print("[POWER] 길게 누름 확인 · no-poweroff 모드")
                    if args.once:
                        return 0
                    continue
                ack = perform_poweroff(args.map_url, args.systemctl)
                print(
                    "[POWER] STM32 ACK 확인 · systemd 종료 예약 · "
                    f"gate_on={ack.get('gate_on')}"
                )
                return 0
            raise TimeoutError("물리 버튼 SSE 연결이 종료되었습니다")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            print(f"전원 버튼 연결 대기: {exc}")
            time.sleep(2.0)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"전원 정상 종료 실패: {exc}")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
