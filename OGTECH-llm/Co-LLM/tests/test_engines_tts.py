# -*- coding: utf-8 -*-
"""TTS 엔진 레지스트리·sherpa-onnx 엔진 계약 테스트(실제 모델·라이브러리 없이)."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import types
import unittest
import wave

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import config as C  # noqa: E402
import engines as E  # noqa: E402


class SherpaOnnxTtsTest(unittest.TestCase):
    def test_sherpa_is_registered_and_first_in_default_order(self) -> None:
        self.assertIn("sherpa", E._TTS)
        self.assertEqual(E.make_tts("sherpa").name, "sherpa")
        self.assertEqual(C.TTS_ENGINE_ORDER[0], "sherpa", "실기 기본 목소리는 sherpa-onnx KSS 여성 음성")
        self.assertEqual(C.TTS_ENGINE_ORDER[-1], "espeak", "espeak-ng는 최종 폴백")

    def test_load_without_model_dir_raises_helpful_error(self) -> None:
        old = C.SHERPA_TTS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            C.SHERPA_TTS_DIR = str(Path(tmp) / "missing")
            try:
                with self.assertRaises(RuntimeError) as ctx:
                    E.make_tts("sherpa").load()
            finally:
                C.SHERPA_TTS_DIR = old
        message = str(ctx.exception)
        self.assertIn("sherpa", message.lower())
        self.assertIn("vits-mimic3-ko_KO-kss_low", message)

    def test_synth_writes_pcm16_wav_through_fake_sherpa_module(self) -> None:
        calls = {}

        class _Audio:
            sample_rate = 22050
            samples = [0.0, 0.5, -0.5, 0.25] * 6000  # 24000 samples ≈ 1.09 s

        class _Vits:
            def __init__(self, **kw):
                calls["vits"] = kw

        class _Model:
            def __init__(self, **kw):
                calls["model"] = kw

        class _Cfg:
            def __init__(self, **kw):
                calls["cfg"] = kw

            def validate(self):
                return True

        class _Tts:
            sample_rate = 22050
            num_speakers = 1

            def __init__(self, cfg):
                calls["tts_cfg"] = cfg
                calls["constructed"] = calls.get("constructed", 0) + 1

            def generate(self, text, sid=0, speed=1.0):
                calls.setdefault("generate", []).append((text, sid, speed))
                return _Audio()

        fake = types.ModuleType("sherpa_onnx")
        fake.OfflineTtsVitsModelConfig = _Vits
        fake.OfflineTtsModelConfig = _Model
        fake.OfflineTtsConfig = _Cfg
        fake.OfflineTts = _Tts

        old_dir, old_mod = C.SHERPA_TTS_DIR, sys.modules.get("sherpa_onnx")
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "vits-mimic3-ko_KO-kss_low"
            (model_dir / "espeak-ng-data").mkdir(parents=True)
            (model_dir / "ko_KO-kss_low.onnx").write_bytes(b"onnx")
            (model_dir / "tokens.txt").write_text("_ 0\n", encoding="utf-8")
            C.SHERPA_TTS_DIR = str(model_dir)
            sys.modules["sherpa_onnx"] = fake
            try:
                E.SherpaOnnxTTS.reset_cache()
                engine = E.make_tts("sherpa")
                engine.load()
                out = Path(tmp) / "out" / "say.wav"
                engine.synth("베이스캠프에 도착하였습니다.", out)
                engine.unload()
                # 두 번째 발화(새 엔진 인스턴스)는 상주 모델을 재사용한다.
                with E.make_tts("sherpa") as again:
                    again.synth("목적지에 도착하였습니다.", Path(tmp) / "out" / "again.wav")
                E.SherpaOnnxTTS.reset_cache()
            finally:
                C.SHERPA_TTS_DIR = old_dir
                if old_mod is None:
                    sys.modules.pop("sherpa_onnx", None)
                else:
                    sys.modules["sherpa_onnx"] = old_mod
            with wave.open(str(out), "rb") as stream:
                self.assertEqual(stream.getnchannels(), 1)
                self.assertEqual(stream.getsampwidth(), 2)
                self.assertEqual(stream.getframerate(), 22050)
                self.assertEqual(stream.getnframes(), 24000)

        self.assertEqual(calls["vits"]["model"], str(model_dir / "ko_KO-kss_low.onnx"))
        self.assertEqual(calls["constructed"], 1, "같은 모델은 프로세스 안에서 한 번만 로드(발화마다 3초 재로드 금지)")
        self.assertEqual(calls["vits"]["tokens"], str(model_dir / "tokens.txt"))
        self.assertEqual(calls["vits"]["data_dir"], str(model_dir / "espeak-ng-data"))
        self.assertEqual(calls["model"]["provider"], "cpu")
        self.assertEqual(calls["generate"][0], ("베이스캠프에 도착하였습니다.", C.SHERPA_TTS_SID, C.SHERPA_TTS_SPEED))
        self.assertEqual(calls["generate"][1][0], "목적지에 도착하였습니다.")


if __name__ == "__main__":
    unittest.main()
