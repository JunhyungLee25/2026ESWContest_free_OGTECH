from __future__ import annotations

import argparse
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import device_monitor  # noqa: E402
from device_monitor import (  # noqa: E402
    CO_ALARM_REPEAT_S,
    CO_WARNING_REPEAT_S,
    AlertDetector,
    alert_tone,
)


def base_device() -> dict[str, object]:
    return {
        "demo": False,
        "co": {"alarm": False, "stale": False},
        "trail": {"status": "on_trail"},
        "sun": {"status": "scheduled"},
        "navigation": {"arrival": {"arrived": False, "target": None}},
    }


class AlertDetectorTest(unittest.TestCase):
    def test_transition_is_announced_once_and_rearms_after_clear(self) -> None:
        detector = AlertDetector()
        device = base_device()
        device["trail"] = {"status": "off_trail"}

        first = detector.detect(device)
        second = detector.detect(device)
        device["trail"] = {"status": "on_trail"}
        detector.detect(device)
        device["trail"] = {"status": "off_trail"}
        third = detector.detect(device)

        self.assertEqual([item.kind for item in first], ["trail"])
        self.assertEqual(second, [])
        self.assertEqual([item.kind for item in third], ["trail"])

    def test_co_has_priority_when_multiple_events_start_together(self) -> None:
        detector = AlertDetector()
        device = base_device()
        device.update(
            {
                "demo": True,
                "co": {"alarm": True, "stale": False, "ppm": 112.4},
                "trail": {"status": "off_trail"},
                "sun": {"status": "return_now"},
            }
        )

        messages = detector.detect(device)

        self.assertEqual([item.kind for item in messages[:3]], ["co_alarm", "trail", "daylight"])
        self.assertIn("112피피엠", messages[0].text)
        # CO 는 지도가 샘플이어도 실측 ppm 이라 데모 접두사를 붙이지 않는다.
        self.assertFalse(messages[0].text.startswith("데모 값"))
        self.assertTrue(all(item.text.startswith("데모 값") for item in messages[1:]))

    def test_accuracy_unknown_large_offset_is_spoken_as_possibility(self) -> None:
        detector = AlertDetector()
        device = base_device()
        device["trail"] = {"status": "off_trail_estimate", "offset_m": 72.0}

        messages = detector.detect(device)

        self.assertEqual([item.kind for item in messages], ["trail"])
        self.assertIn("가능성", messages[0].text)
        self.assertIn("정확도는 확인되지", messages[0].text)
        self.assertNotIn("이탈 경보입니다", messages[0].text)

    def test_arrival_phrase_distinguishes_destination_and_basecamp(self) -> None:
        detector = AlertDetector()
        destination = base_device()
        destination["navigation"] = {
            "arrival": {
                "arrived": True,
                "target": {"id": "destination", "kind": "destination"},
            }
        }
        first = detector.detect(destination)
        self.assertEqual(first[0].text, "목적지에 도착하였습니다.")

        cleared = base_device()
        detector.detect(cleared)
        basecamp = base_device()
        basecamp["navigation"] = {
            "arrival": {
                "arrived": True,
                "target": {"id": "basecamp", "kind": "basecamp"},
            }
        }
        second = detector.detect(basecamp)
        self.assertEqual(second[0].text, "베이스캠프에 도착하였습니다.")

    def test_co_alarm_is_not_repeated_while_detector_state_is_retained(self) -> None:
        detector = AlertDetector()
        device = base_device()
        device["co"] = {"alarm": True, "stale": False, "ppm": 250}

        first = detector.detect(device)
        second = detector.detect(device)

        self.assertEqual([item.kind for item in first], ["co_alarm"])
        self.assertEqual(second, [])

    def test_co_alarm_without_numeric_ppm_is_spoken_as_unknown(self) -> None:
        for ppm in (None, "n/a"):
            detector = AlertDetector()
            device = base_device()
            device["co"] = {"alarm": True, "stale": False, "ppm": ppm}

            messages = detector.detect(device)

            self.assertEqual([item.kind for item in messages], ["co_alarm"], ppm)
            self.assertIn("확인 불가", messages[0].text)


class CoAlarmSoundTest(unittest.TestCase):
    """부저를 걷어낸 뒤 CO 경보음·음성은 이 데몬만 낸다(2026-08-31)."""

    def test_alarm_message_carries_tone_and_action_instead_of_buzzer_claim(self) -> None:
        device = base_device()
        device["co"] = {"alarm": True, "stale": False, "ppm": 120}

        message = AlertDetector().detect(device, now=0.0)[0]

        self.assertEqual(message.kind, "co_alarm")
        self.assertEqual(message.sound, "alarm")
        self.assertIn("즉시 환기하고 대피하세요", message.text)
        self.assertNotIn("물리 경보", message.text)  # 부저는 더 이상 없다

    def test_warning_level_is_announced_with_its_own_tone(self) -> None:
        device = base_device()
        device["co"] = {"alarm": False, "level": "warning", "stale": False, "ppm": 41}

        messages = AlertDetector().detect(device, now=0.0)

        self.assertEqual([item.kind for item in messages], ["co_warning"])
        self.assertEqual(messages[0].sound, "warning")
        self.assertIn("주의", messages[0].text)

    def test_real_measurement_is_never_announced_as_demo(self) -> None:
        """device.demo 는 "지도가 샘플"이라는 뜻이다 — ppm 은 어느 쪽이든 센서 실측이다."""
        device = base_device()
        device["demo"] = True
        device["co"] = {"alarm": True, "stale": False, "ppm": 150}

        message = AlertDetector().detect(device, now=0.0)[0]

        self.assertTrue(message.text.startswith("일산화탄소 경보입니다."), message.text)

    def test_alarm_repeats_while_it_lasts_and_stops_after_clear(self) -> None:
        detector = AlertDetector()
        device = base_device()
        device["co"] = {"alarm": True, "stale": False, "ppm": 120}

        self.assertEqual(len(detector.detect(device, now=0.0)), 1)
        self.assertEqual(detector.detect(device, now=CO_ALARM_REPEAT_S - 0.1), [])
        self.assertEqual(len(detector.detect(device, now=CO_ALARM_REPEAT_S)), 1)
        self.assertEqual(len(detector.detect(device, now=2 * CO_ALARM_REPEAT_S)), 1)

        cleared = base_device()
        self.assertEqual(detector.detect(cleared, now=3 * CO_ALARM_REPEAT_S), [])
        self.assertEqual(detector.detect(cleared, now=9_000.0), [])

    def test_warning_repeats_less_often_than_alarm(self) -> None:
        detector = AlertDetector()
        device = base_device()
        device["co"] = {"alarm": False, "level": "warning", "stale": False, "ppm": 41}

        detector.detect(device, now=0.0)
        self.assertEqual(detector.detect(device, now=CO_ALARM_REPEAT_S), [])
        self.assertEqual(len(detector.detect(device, now=CO_WARNING_REPEAT_S)), 1)

    def test_stale_sensor_does_not_keep_shouting(self) -> None:
        detector = AlertDetector()
        device = base_device()
        device["co"] = {"alarm": True, "stale": True, "ppm": 120}

        self.assertEqual(detector.detect(device, now=0.0), [])

    def test_tone_is_a_playable_non_silent_wav(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(device_monitor.C, "RESULT_DIR", Path(directory)):
                for kind in ("alarm", "warning"):
                    path = alert_tone(kind)
                    with wave.open(str(path), "rb") as handle:
                        frames = handle.getnframes()
                        self.assertEqual(handle.getsampwidth(), 2)
                        self.assertEqual(handle.getnchannels(), 1)
                        self.assertGreater(frames / handle.getframerate(), 0.3)
                        self.assertTrue(any(handle.readframes(frames)))
                    self.assertEqual(alert_tone(kind), path)  # 두 번째부터는 재사용


class WarmTtsTest(unittest.TestCase):
    """첫 경보가 모델 로드를 기다리느라 비프 뒤 6초를 잠자코 있지 않게 한다."""

    def test_fixed_sentences_are_synthesized_up_front(self) -> None:
        asked: list[str] = []

        class Pipeline:
            def synthesize_sentences(self, text, _output):
                asked.append(text)
                return iter(())

        device_monitor.warm_tts(Pipeline(), Path("/tmp/warmup.wav"))


        self.assertEqual(asked, list(device_monitor.WARMUP_SENTENCES))
        self.assertIn("일산화탄소 경보입니다.", asked)

    def test_model_is_loaded_up_front_when_sherpa_is_used(self) -> None:
        """캐시가 차 있어도 ppm 문장은 매번 새로 합성한다 — 모델이 올라와 있어야 한다."""
        loaded = []

        class Engine:
            def load(self):
                loaded.append(True)

        class Pipeline:
            engine_order = ("sherpa", "espeak")

            def synthesize_sentences(self, _text, _output):
                return iter(())

        with patch.object(device_monitor.E, "SherpaOnnxTTS", Engine):
            device_monitor.warm_tts(Pipeline(), Path("/tmp/warmup.wav"))
        self.assertEqual(len(loaded), 1)

    def test_model_load_failure_does_not_stop_the_daemon(self) -> None:
        class Engine:
            def load(self):
                raise RuntimeError("모델 없음")

        class Pipeline:
            engine_order = ("sherpa",)

            def synthesize_sentences(self, _text, _output):
                return iter(())

        stderr = io.StringIO()
        with patch.object(device_monitor.E, "SherpaOnnxTTS", Engine), \
                contextlib.redirect_stderr(stderr):
            device_monitor.warm_tts(Pipeline(), Path("/tmp/warmup.wav"))
        self.assertIn("음성 모델 예열 실패", stderr.getvalue())

    def test_failure_is_reported_and_does_not_raise(self) -> None:
        class Pipeline:
            def synthesize_sentences(self, _text, _output):
                raise RuntimeError("모델 없음")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            device_monitor.warm_tts(Pipeline(), Path("/tmp/warmup.wav"))

        self.assertIn("음성 예열 실패", stderr.getvalue())


class _FakeSpeech:
    path = Path("/nonexistent/alert.wav")


class _FakePipeline:
    def __init__(self, **_kwargs) -> None:
        pass

    def synthesize_sentences(self, _text, _output):
        yield _FakeSpeech()


class DeviceMonitorMainTest(unittest.TestCase):
    def test_playback_failure_keeps_detector_state(self) -> None:
        """재생 실패가 데몬을 죽여 새 detector 가 경보를 재발화하는 루프를 막는다(WORKLOG #28)."""
        co = base_device()
        co["co"] = {"alarm": True, "stale": False, "ppm": 120}
        trail = dict(co)
        trail["trail"] = {"status": "off_trail"}
        batches = iter([[co, co, trail]])

        def fake_events(_url):
            try:
                batch = next(batches)
            except StopIteration:
                raise KeyboardInterrupt  # 두 번째 연결 시도에서 데몬을 끝낸다
            for device in batch:
                yield device

        args = argparse.Namespace(
            map_url="http://127.0.0.1:8790", tts_order="clear",
            no_tts=False, no_play=False, once=False,
        )
        played: list[Path] = []

        def failing_play(path):
            played.append(path)
            raise subprocess.CalledProcessError(1, ["aplay"])

        stderr = io.StringIO()
        with patch.object(device_monitor, "parse_args", return_value=args), \
                patch.object(device_monitor, "device_events", fake_events), \
                patch.object(device_monitor, "TtsPipeline", _FakePipeline), \
                patch.object(device_monitor, "exclusive_pipeline", contextlib.nullcontext), \
                patch.object(device_monitor.E, "play", failing_play), \
                patch.object(device_monitor.time, "sleep", lambda _s: None), \
                contextlib.redirect_stderr(stderr), \
                contextlib.redirect_stdout(io.StringIO()):
            code = device_monitor.main()

        self.assertEqual(code, 0)
        # CO 1회 + 트레일 1회. 두 번째 CO 프레임은 같은 detector 가 중복으로 보지 않는다.
        self.assertEqual(len(played), 2)
        self.assertEqual(stderr.getvalue().count("알림 재생 실패"), 2)


if __name__ == "__main__":
    unittest.main()
