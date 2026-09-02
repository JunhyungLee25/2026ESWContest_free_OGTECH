from __future__ import annotations

from math import pi, sin
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from device_monitor import AlertDetector  # noqa: E402
from tts_pipeline import (  # noqa: E402
    TtsPipeline,
    inspect_wav,
    normalize_tts_text,
    split_tts_sentences,
)


def write_tone(path: Path, *, seconds: float = 0.35, rate: int = 16_000) -> None:
    frames = int(seconds * rate)
    raw = b"".join(
        struct.pack("<h", int(7000 * sin(2 * pi * 440 * index / rate)))
        for index in range(frames)
    )
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(raw)


class FakeEngine:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def synth(self, _text: str, out_wav: Path) -> None:
        self.calls.append(self.name)
        if self.name.startswith("broken"):
            raise RuntimeError("의도한 실패")
        write_tone(Path(out_wav))


class TtsPipelineTest(unittest.TestCase):
    def test_korean_pronunciation_normalization(self) -> None:
        normalized = normalize_tts_text(
            "GPS ±4.2 m, CO 10 ppm, 배터리 78%, 귀환 18:30, Base Camp"
        )
        self.assertIn("지피에스", normalized)
        self.assertIn("플러스마이너스 4.2미터", normalized)
        self.assertIn("일산화탄소 10 피피엠", normalized)
        self.assertIn("78퍼센트", normalized)
        self.assertIn("18시 30분", normalized)
        self.assertIn("베이스캠프", normalized)

    def test_failed_preferred_engine_falls_back_once_and_caches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls: list[str] = []
            pipeline = TtsPipeline(
                engine_order=("broken", "clear"),
                engine_factory=lambda name: FakeEngine(name, calls),
                manifest_path=root / "missing.json",
                cache_dir=root / "cache",
            )
            first = pipeline.synthesize("안전한 테스트 문장입니다.", root / "first.wav")

            self.assertEqual(calls, ["broken", "clear"])
            self.assertEqual(first.engine, "clear")
            self.assertTrue(first.degraded)
            self.assertEqual(len(first.errors), 1)
            self.assertGreater(first.metrics.rms_ratio, 0)

            calls.clear()
            second = pipeline.synthesize("안전한 테스트 문장입니다.", root / "second.wav")
            self.assertEqual(calls, [])
            self.assertTrue(second.cached)
            self.assertEqual(second.engine, "clear")

    def test_cache_key_changes_when_voice_speed_changes(self) -> None:
        # 2026-08-30 0.9배속 전환(length_scale 1.1→1.22): 속도·화자 파라미터가 바뀌면 옛 캐시 클립을 재생하면 안 된다.
        import config as C

        pipeline = TtsPipeline(engine_order=("sherpa", "espeak"), use_cache=False)
        before = pipeline._cache_paths("안내 문장")
        old = C.SHERPA_TTS_LENGTH_SCALE
        C.SHERPA_TTS_LENGTH_SCALE = old + 0.1
        try:
            after = pipeline._cache_paths("안내 문장")
        finally:
            C.SHERPA_TTS_LENGTH_SCALE = old
        self.assertNotEqual(before, after)
        self.assertEqual(before, pipeline._cache_paths("안내 문장"))
        self.assertIn("ls=1.22", TtsPipeline.voice_signature(), "제품 기본은 0.9배속(length_scale 1.22)")
        self.assertEqual(C.SHERPA_TTS_SPEED, 1.0, "speed≠1은 sherpa-onnx가 length_scale을 덮어쓴다 — 속도는 length_scale로만")

    def test_known_video_phrase_uses_fixed_clean_clip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = TtsPipeline(use_cache=False).synthesize(
                "네, 목적지로 설정되었습니다.", Path(directory) / "fixed.wav"
            )
            self.assertEqual(result.engine, "fixed")
            self.assertFalse(result.degraded)
            self.assertGreater(result.metrics.duration_s, 0.2)
            self.assertLess(result.metrics.duration_s, 4.0)
            self.assertGreater(inspect_wav(result.path).peak_ratio, 0)

    def test_basecamp_arrival_uses_fixed_video_clip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = TtsPipeline(use_cache=False).synthesize(
                "베이스캠프에 도착하였습니다.", Path(directory) / "fixed.wav"
            )

            self.assertEqual(result.engine, "fixed")
            self.assertLess(result.metrics.duration_s, 4.0)
            self.assertEqual(result.metrics.clipped_ratio, 0.0)

    def test_demo_prefixed_arrival_uses_fixed_clip(self) -> None:
        """device_monitor 의 데모 접두가 붙은 도착 문장도 고정 WAV 로 간다(WORKLOG #32)."""
        device = {
            "demo": True,
            "co": {"alarm": False},
            "trail": {"status": "on_trail"},
            "sun": {"status": "scheduled"},
            "navigation": {
                "arrival": {"arrived": True, "target": {"id": "destination", "kind": "destination"}}
            },
        }
        message = AlertDetector().detect(device)[0]
        self.assertTrue(message.text.startswith("데모 값 기준으로, "))

        with tempfile.TemporaryDirectory() as directory:
            pipeline = TtsPipeline(use_cache=False)
            self.assertIsNotNone(pipeline.fixed_clip_for(message.text))
            result = pipeline.synthesize(message.text, Path(directory) / "fixed.wav")

            self.assertEqual(result.engine, "fixed")
            self.assertFalse(result.degraded)

    def test_product_manifest_has_audible_all_engine_failure_clip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls: list[str] = []
            pipeline = TtsPipeline(
                engine_order=("broken-primary", "broken-secondary"),
                engine_factory=lambda name: FakeEngine(name, calls),
                cache_dir=Path(directory) / "cache",
                use_cache=False,
            )

            result = pipeline.synthesize(
                "고정 클립에 없는 검수 문장입니다.",
                Path(directory) / "fallback.wav",
            )

            self.assertEqual(result.engine, "fixed_fallback")
            self.assertEqual(calls, ["broken-primary", "broken-secondary"])
            self.assertGreater(result.metrics.duration_s, 1.0)
            self.assertEqual(result.metrics.clipped_ratio, 0.0)

    def test_sentence_stream_keeps_decimal_and_creates_ordered_segments(self) -> None:
        self.assertEqual(
            split_tts_sentences("온도는 4.2도입니다. 경로를 확인하세요!"),
            ("온도는 4.2도입니다.", "경로를 확인하세요!"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls: list[str] = []
            pipeline = TtsPipeline(
                engine_order=("clear",),
                engine_factory=lambda name: FakeEngine(name, calls),
                manifest_path=root / "missing.json",
                cache_dir=root / "cache",
                use_cache=False,
            )
            results = tuple(pipeline.synthesize_sentences(
                "첫 문장입니다. 둘째 문장입니다.", root / "speech.wav"
            ))

            self.assertEqual(len(results), 2)
            self.assertEqual(calls, ["clear", "clear"])
            self.assertEqual(results[0].path.name, "speech.part01.wav")
            self.assertEqual(results[1].path.name, "speech.part02.wav")

    def test_all_engines_fail_once_then_use_fixed_failure_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fallback = root / "fallback.wav"
            write_tone(fallback)
            manifest = root / "fixed_audio.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "clips": {},
                        "fallback": {
                            "path": "fallback.wav",
                            "spoken_text": "음성 합성 실패 고정 안내",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            calls: list[str] = []
            pipeline = TtsPipeline(
                engine_order=("broken-primary", "broken-fallback"),
                engine_factory=lambda name: FakeEngine(name, calls),
                manifest_path=manifest,
                cache_dir=root / "cache",
                use_cache=False,
            )

            result = pipeline.synthesize("원래 검수 문장입니다.", root / "out.wav")

            self.assertEqual(calls, ["broken-primary", "broken-fallback"])
            self.assertEqual(result.engine, "fixed_fallback")
            self.assertEqual(result.normalized_text, "음성 합성 실패 고정 안내")
            self.assertTrue(result.degraded)
            self.assertEqual(len(result.errors), 2)
            self.assertTrue(result.path.is_file())


if __name__ == "__main__":
    unittest.main()
