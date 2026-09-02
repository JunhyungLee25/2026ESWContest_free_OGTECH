"""같은 부팅 위치 이력과 역추적 대상 선택의 안전 경계 테스트."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from position_history import PositionHistoryStore


class PositionHistoryStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "position_history.jsonl"
        self.now = 200.0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _store(self, *, boot_id: str = "boot-a") -> PositionHistoryStore:
        return PositionHistoryStore(
            self.path,
            boot_id=boot_id,
            clock=lambda: self.now,
            retention_s=600.0,
            target_age_s=180.0,
            target_gap_s=25.0,
        )

    @staticmethod
    def _fix(lat: float = 37.5, lon: float = 127.0) -> dict[str, object]:
        return {"fix": True, "lat": lat, "lon": lon, "acc_m": 4.0, "satellites": 9}

    def test_same_boot_persists_and_target_window_is_plus_minus_twenty_five_seconds(self) -> None:
        first = self._store()
        self.assertTrue(first.record(self._fix(), now_monotonic=45.0))
        self.assertTrue(first.record(self._fix(37.6), now_monotonic=200.0))

        reloaded = self._store()
        selected = reloaded.point_ago()

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["age_s"], 155.0)
        self.assertEqual(selected["target_gap_s"], 25.0)
        self.assertTrue(reloaded.summary()["recent_trace_ready"])

    def test_history_rejects_no_fix_and_outside_target_window(self) -> None:
        store = self._store()
        self.assertFalse(store.record({"fix": False}, now_monotonic=20.0))
        self.assertFalse(self.path.exists())
        self.assertTrue(store.record(self._fix(), now_monotonic=46.0))

        self.assertIsNone(store.point_ago())
        summary = store.summary()
        self.assertTrue(summary["coordinates_exposed"] is False)
        self.assertNotIn("lat", summary)
        self.assertNotIn("lon", summary)

    def test_other_boot_old_and_corrupt_records_are_filtered(self) -> None:
        self.now = 1000.0
        valid = {
            "version": 1,
            "boot_id": "boot-a",
            "monotonic_s": 820.0,
            "recorded_at": "2026-08-19T00:00:00+00:00",
            "lat": 37.5,
            "lon": 127.0,
            "accuracy_m": 4.0,
            "satellites": 9,
        }
        other_boot = dict(valid, boot_id="boot-b")
        too_old = dict(valid, monotonic_s=200.0)
        self.path.write_text(
            "\n".join(
                [
                    json.dumps(valid),
                    json.dumps(other_boot),
                    json.dumps(too_old),
                    "{not-json}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        store = self._store()

        summary = store.summary()
        self.assertEqual(summary["sample_count"], 1)
        self.assertTrue(summary["recent_trace_ready"])
        persisted = self.path.read_text(encoding="utf-8")
        self.assertNotIn("boot-b", persisted)
        self.assertNotIn("not-json", persisted)


if __name__ == "__main__":
    unittest.main()
