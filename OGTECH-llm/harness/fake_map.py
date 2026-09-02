# -*- coding: utf-8 -*-
"""평가·테스트용 MAP API 대역. 실제 MAP 서버의 action/status 계약만 흉내 낸다. 좌표는 노출하지 않는다."""

from __future__ import annotations

import copy
from typing import Any


class FakeMapClient:
    def __init__(self, *, fix: bool = True, demo: bool = False) -> None:
        self.pending: dict[str, Any] | None = None
        self.night = False
        self.basecamp: dict[str, Any] | None = None
        self.destination: dict[str, Any] | None = None
        self.checkpoints: list[dict[str, Any]] = []
        self.actions: list[str] = []
        self.state: dict[str, Any] = {
            "demo": demo,
            "gps": {"fix": fix, "acc_m": 5.0, "satellites": 9} if fix else {"fix": False, "last_fix": {"lat": 0.0, "lon": 0.0}, "last_age_s": 73},
            "navigation": {"active_route": {"available": False}, "arrival": {"arrived": False}},
            "sun": {"computed": True, "reference": "current_fix", "remaining_min": 41, "return_by_clock": "18:52", "level": "caution"},
            "environment": {"valid": True, "temp_c": 30.0, "humidity_pct": 55.0},
            "power": {"valid": False},
            "co": {"valid": True, "stale": False, "ppm": 0, "alarm": False, "level": "normal"},
            "trail": {"status": "on_trail", "offset_m": 2.0},
        }

    def clone(self) -> "FakeMapClient":
        return copy.deepcopy(self)

    def voice(self) -> dict[str, Any]:
        pending = None
        if self.pending is not None:
            pending = {k: self.pending[k] for k in ("id", "kind", "name")}
        return {"pending_destination": pending, "ui": {"night": self.night}}

    def device(self) -> dict[str, Any]:
        state = copy.deepcopy(self.state)
        state["interface"] = {"night": self.night}
        return state

    def command(self, action: str) -> dict[str, Any]:
        self.actions.append(action)
        fix = self.state["gps"].get("fix") is True
        status = "accepted"
        if action in {"save_basecamp", "save_checkpoint"}:
            if not fix:
                status = "rejected"
            elif action == "save_basecamp":
                self.basecamp = {"id": "basecamp", "kind": "basecamp", "name": "베이스캠프"}
            else:
                self.checkpoints.append({"id": f"cp-{len(self.checkpoints) + 1}", "kind": "checkpoint"})
        elif action == "find_nearest_water":
            if fix:
                self.pending = {"id": "demo-water-ilgam", "kind": "water_source", "name": "일감호 주변 수원 표식"}
                status = "confirmation_required"
            else:
                status = "rejected"
        elif action == "confirm_destination":
            if self.pending is None:
                status = "rejected"
            else:
                self.destination = dict(self.pending)
                self.pending = None
                self.state["navigation"]["active_route"] = {
                    "available": True, "bearing_deg": 292, "distance_m": 231, "eta_min": 4,
                    "target": {"id": self.destination["id"], "kind": "destination"},
                }
        elif action in {"reject_destination", "cancel"}:
            self.pending = None
        elif action == "clear_destination":
            self.pending = None
            if self.destination is None:
                status = "rejected"
            self.destination = None
            self.state["navigation"]["active_route"] = {"available": False}
        elif action == "route_basecamp":
            status = "accepted" if (self.basecamp and fix) else "rejected"
        elif action == "route_destination":
            status = "accepted" if (self.destination and fix) else "rejected"
        elif action == "route_last_checkpoint":
            status = "accepted" if (self.checkpoints and fix) else "rejected"
        elif action == "route_recent_trace":
            status = "accepted" if fix else "rejected"
        elif action in {"night_on", "night_off", "night_toggle"}:
            self.night = (not self.night) if action == "night_toggle" else action == "night_on"
        elif action == "status":
            status = "accepted"
        else:
            status = "rejected"
        return {"action": action, "status": status, "message": "지도 대역 응답", "device": self.device()}
