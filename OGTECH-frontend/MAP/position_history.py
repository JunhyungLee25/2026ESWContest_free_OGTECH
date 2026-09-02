"""같은 부팅 안의 검증된 GPS fix를 runtime에만 기록하는 위치 이력 저장소."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
import threading
import time
from typing import Any, Callable
from uuid import uuid4


POSITION_HISTORY_VERSION = 1
DEFAULT_SAMPLE_INTERVAL_S = 10.0
DEFAULT_RETENTION_S = 6 * 60 * 60
DEFAULT_TARGET_AGE_S = 3 * 60
DEFAULT_TARGET_GAP_S = 25.0
DEFAULT_COMPACT_EVERY = 360


def _current_boot_id() -> str:
    """Linux 부팅 ID를 우선 사용해 서비스 재시작 뒤에도 같은 부팅 기록만 잇는다."""
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
    except OSError:
        value = ""
    return value or f"process-{uuid4().hex}"


def _finite_number(value: Any, *, lower: float, upper: float) -> float:
    number = float(value)
    if not isfinite(number) or not lower <= number <= upper:
        raise ValueError("위치 이력 숫자 범위가 올바르지 않습니다")
    return number


class PositionHistoryStore:
    """실제 좌표를 외부 응답이 아닌 runtime JSONL에 제한해 보관한다."""

    def __init__(
        self,
        path: str | Path,
        *,
        boot_id: str | None = None,
        sample_interval_s: float = DEFAULT_SAMPLE_INTERVAL_S,
        retention_s: float = DEFAULT_RETENTION_S,
        target_age_s: float = DEFAULT_TARGET_AGE_S,
        target_gap_s: float = DEFAULT_TARGET_GAP_S,
        compact_every: int = DEFAULT_COMPACT_EVERY,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            sample_interval_s <= 0
            or retention_s <= target_age_s
            or target_age_s <= 0
            or target_gap_s <= 0
            or compact_every < 1
        ):
            raise ValueError("위치 이력 시간 설정이 올바르지 않습니다")
        self.path = Path(path)
        self.boot_id = boot_id or _current_boot_id()
        self.sample_interval_s = float(sample_interval_s)
        self.retention_s = float(retention_s)
        self.target_age_s = float(target_age_s)
        self.target_gap_s = float(target_gap_s)
        self.compact_every = int(compact_every)
        self.clock = clock
        self.wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._records: list[dict[str, Any]] = []
        self._writes_since_compaction = 0
        self._error: str | None = None
        self._load()

    @staticmethod
    def _validated_record(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("version") != POSITION_HISTORY_VERSION:
            raise ValueError("위치 이력 스키마가 올바르지 않습니다")
        boot_id = str(payload.get("boot_id") or "").strip()
        if not boot_id or len(boot_id) > 80:
            raise ValueError("위치 이력 부팅 ID가 올바르지 않습니다")
        monotonic_s = _finite_number(
            payload.get("monotonic_s"), lower=0.0, upper=10**12
        )
        lat = _finite_number(payload.get("lat"), lower=-90.0, upper=90.0)
        lon = _finite_number(payload.get("lon"), lower=-180.0, upper=180.0)
        accuracy = payload.get("accuracy_m")
        accuracy_m = (
            None
            if accuracy is None
            else _finite_number(accuracy, lower=0.0, upper=100_000.0)
        )
        satellites = payload.get("satellites")
        if satellites is not None:
            satellites = int(satellites)
            if not 0 <= satellites <= 100:
                raise ValueError("위성 수가 올바르지 않습니다")
        recorded_at = str(payload.get("recorded_at") or "")[:40]
        return {
            "version": POSITION_HISTORY_VERSION,
            "boot_id": boot_id,
            "monotonic_s": monotonic_s,
            "recorded_at": recorded_at,
            "lat": lat,
            "lon": lon,
            "accuracy_m": accuracy_m,
            "satellites": satellites,
        }

    def _load(self) -> None:
        if not self.path.is_file():
            return
        now = self.clock()
        kept: list[dict[str, Any]] = []
        discarded = False
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            self._error = str(exc)
            return
        for line in lines:
            if not line.strip():
                continue
            try:
                record = self._validated_record(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError):
                discarded = True
                continue
            age_s = now - float(record["monotonic_s"])
            if (
                record["boot_id"] != self.boot_id
                or age_s < -1.0
                or age_s > self.retention_s
            ):
                discarded = True
                continue
            kept.append(record)
        kept.sort(key=lambda item: float(item["monotonic_s"]))
        self._records = kept
        if discarded:
            self._compact()

    def _compact(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
            body = "".join(
                json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
                for item in self._records
            )
            temporary.write_text(body, encoding="utf-8")
            temporary.replace(self.path)
            self._writes_since_compaction = 0
            self._error = None
        except OSError as exc:
            self._error = str(exc)

    def _prune(self, now: float) -> bool:
        first_valid = 0
        while (
            first_valid < len(self._records)
            and now - float(self._records[first_valid]["monotonic_s"])
            > self.retention_s
        ):
            first_valid += 1
        if first_valid:
            del self._records[:first_valid]
            return True
        return False

    def record(
        self,
        gps: dict[str, Any],
        *,
        now_monotonic: float | None = None,
        recorded_at: datetime | None = None,
    ) -> bool:
        """live fix만 일정 간격으로 추가한다. 불확실하거나 오래된 좌표는 기록하지 않는다."""
        if gps.get("fix") is not True:
            return False
        try:
            lat = _finite_number(gps.get("lat"), lower=-90.0, upper=90.0)
            lon = _finite_number(gps.get("lon"), lower=-180.0, upper=180.0)
            accuracy = gps.get("acc_m")
            accuracy_m = (
                None
                if accuracy is None
                else _finite_number(accuracy, lower=0.0, upper=100_000.0)
            )
            satellites = gps.get("satellites")
            if satellites is not None:
                satellites = int(satellites)
                if not 0 <= satellites <= 100:
                    raise ValueError("위성 수가 올바르지 않습니다")
        except (TypeError, ValueError):
            return False
        now = self.clock() if now_monotonic is None else float(now_monotonic)
        if not isfinite(now) or now < 0:
            return False
        with self._lock:
            if self._records:
                previous = float(self._records[-1]["monotonic_s"])
                if now < previous or now - previous < self.sample_interval_s:
                    return False
            instant = recorded_at or self.wall_clock()
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            record = {
                "version": POSITION_HISTORY_VERSION,
                "boot_id": self.boot_id,
                "monotonic_s": round(now, 6),
                "recorded_at": instant.astimezone(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "lat": lat,
                "lon": lon,
                "accuracy_m": accuracy_m,
                "satellites": satellites,
            }
            pruned = self._prune(now)
            self._records.append(record)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as output:
                    output.write(
                        json.dumps(
                            record, ensure_ascii=False, separators=(",", ":")
                        )
                        + "\n"
                    )
                self._writes_since_compaction += 1
                self._error = None
            except OSError as exc:
                self._error = str(exc)
            if pruned or self._writes_since_compaction >= self.compact_every:
                self._compact()
            return True

    def point_ago(
        self,
        age_s: float | None = None,
        *,
        now_monotonic: float | None = None,
    ) -> dict[str, Any] | None:
        requested_age = self.target_age_s if age_s is None else float(age_s)
        if requested_age <= 0:
            return None
        now = self.clock() if now_monotonic is None else float(now_monotonic)
        target = now - requested_age
        with self._lock:
            self._prune(now)
            if not self._records:
                return None
            closest = min(
                self._records,
                key=lambda item: abs(float(item["monotonic_s"]) - target),
            )
            gap_s = abs(float(closest["monotonic_s"]) - target)
            if gap_s > self.target_gap_s:
                return None
            return {
                "lat": closest["lat"],
                "lon": closest["lon"],
                "accuracy_m": closest["accuracy_m"],
                "satellites": closest["satellites"],
                "recorded_at": closest["recorded_at"],
                "age_s": round(max(0.0, now - float(closest["monotonic_s"])), 1),
                "target_age_s": round(requested_age, 1),
                "target_gap_s": round(gap_s, 1),
                "source": "position_history",
            }

    def summary(self, *, now_monotonic: float | None = None) -> dict[str, Any]:
        now = self.clock() if now_monotonic is None else float(now_monotonic)
        with self._lock:
            self._prune(now)
            oldest_age = (
                None
                if not self._records
                else round(max(0.0, now - float(self._records[0]["monotonic_s"])), 1)
            )
            newest_age = (
                None
                if not self._records
                else round(max(0.0, now - float(self._records[-1]["monotonic_s"])), 1)
            )
            ready = self.point_ago(now_monotonic=now) is not None
            return {
                "version": POSITION_HISTORY_VERSION,
                "sample_count": len(self._records),
                "sample_interval_s": self.sample_interval_s,
                "retention_s": self.retention_s,
                "target_age_s": self.target_age_s,
                "oldest_age_s": oldest_age,
                "newest_age_s": newest_age,
                "recent_trace_ready": ready,
                "persistence": "runtime_jsonl",
                "coordinates_exposed": False,
                "error": self._error,
            }
