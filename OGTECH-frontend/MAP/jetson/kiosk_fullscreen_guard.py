#!/usr/bin/env python3
"""키오스크 창이 전체화면에서 벗어나면, 사용자가 그 창을 다시 건드릴 때 전체화면으로 되돌린다.

GNOME(X11) 에서 화면 위쪽 가장자리를 아래로 끌면 창의 전체화면 상태가 풀린다 — 시연 중
Wi-Fi 가 꺼져 있는 것을 상단 메뉴로 보여 줄 때 쓰는 제스처다. Firefox --kiosk 는 이 상태를
스스로 되돌리지 않아 창이 450x120 으로 남는다(2026-09-02 젯슨 실측: 창을 눌러도 그대로).

메뉴를 보여 주는 동안은 건드리지 않는다. 포인터(터치 지점)가 다시 키오스크 창 안에 들어온
순간에만 되돌린다. 창이 포커스를 가졌는지는 기준으로 쓰지 않는다 — GNOME 메뉴가 열린 동안에도
창은 FOCUSED 로 남아 있어, 그걸 기준으로 하면 보여 주려는 메뉴를 덮어 버린다.

젯슨에는 wmctrl 이 없어 EWMH _NET_WM_STATE ClientMessage 를 libX11 로 직접 보낸다.
xdotool 3.2016 은 windowstate 명령이 없다.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess
import sys
import time

WINDOW_NAME = os.environ.get("OGTECH_KIOSK_WINDOW_NAME", "OGTECH")
POLL_SECONDS = 0.4
_NET_WM_STATE_ADD = 1
CLIENT_MESSAGE = 33
SUBSTRUCTURE_NOTIFY_MASK = 1 << 19
SUBSTRUCTURE_REDIRECT_MASK = 1 << 20


def _run(*command: str) -> str:
    try:
        return subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=3
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def parse_shell_vars(text: str) -> dict[str, int]:
    """`xdotool ... --shell` 출력(KEY=값 줄)을 정수 사전으로 만든다."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and value.strip().lstrip("-").isdigit():
            values[key.strip()] = int(value.strip())
    return values


def pointer_inside(pointer: dict[str, int], geometry: dict[str, int]) -> bool:
    try:
        x, y = pointer["X"], pointer["Y"]
        left, top = geometry["X"], geometry["Y"]
        width, height = geometry["WIDTH"], geometry["HEIGHT"]
    except KeyError:
        return False
    return left <= x < left + width and top <= y < top + height


def find_window() -> int | None:
    output = _run("xdotool", "search", "--name", WINDOW_NAME).split()
    return int(output[0]) if output else None


def is_fullscreen(window: int) -> bool:
    return "_NET_WM_STATE_FULLSCREEN" in _run("xprop", "-id", str(window), "_NET_WM_STATE")


def window_geometry(window: int) -> dict[str, int]:
    return parse_shell_vars(_run("xdotool", "getwindowgeometry", "--shell", str(window)))


def pointer_location() -> dict[str, int]:
    return parse_shell_vars(_run("xdotool", "getmouselocation", "--shell"))


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int), ("data", ctypes.c_long * 5),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xclient", _XClientMessageEvent), ("pad", ctypes.c_long * 24)]


def request_fullscreen(window: int) -> bool:
    """창 관리자에게 _NET_WM_STATE_FULLSCREEN 추가를 요청한다(wmctrl -b add,fullscreen 과 같다)."""
    library = ctypes.util.find_library("X11") or "libX11.so.6"
    try:
        x11 = ctypes.CDLL(library)
    except OSError:
        return False
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(None)
    if not display:
        return False
    event = _XEvent()
    event.xclient.type = CLIENT_MESSAGE
    event.xclient.window = window
    event.xclient.message_type = x11.XInternAtom(display, b"_NET_WM_STATE", 0)
    event.xclient.format = 32
    event.xclient.data[0] = _NET_WM_STATE_ADD
    event.xclient.data[1] = x11.XInternAtom(display, b"_NET_WM_STATE_FULLSCREEN", 0)
    event.xclient.data[2] = 0
    event.xclient.data[3] = 1  # 일반 응용 프로그램이 보내는 요청
    x11.XSendEvent(
        display, x11.XDefaultRootWindow(display), 0,
        SUBSTRUCTURE_REDIRECT_MASK | SUBSTRUCTURE_NOTIFY_MASK, ctypes.byref(event),
    )
    x11.XFlush(display)
    x11.XCloseDisplay(display)
    return True


def main() -> int:
    if not os.environ.get("DISPLAY"):
        print("DISPLAY 가 없어 전체화면 감시를 하지 않습니다.", file=sys.stderr)
        return 0
    while True:
        window = find_window()
        if window and not is_fullscreen(window) and pointer_inside(pointer_location(), window_geometry(window)):
            if request_fullscreen(window):
                print(f"키오스크 창 {window} 을 전체화면으로 되돌렸습니다.", flush=True)
                time.sleep(1.5)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
