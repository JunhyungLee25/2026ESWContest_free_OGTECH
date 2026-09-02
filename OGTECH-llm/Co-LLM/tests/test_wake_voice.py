from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402
from ogtech_core import RouteDecision, RuleRouter  # noqa: E402
from product_assistant import AssistantResult  # noqa: E402
from wake_voice import (  # noqa: E402
    RATE,
    WINDOW,
    Dialogue,
    EnergySegmenter,
    FileSource,
    Reply,
    Segment,
    _yes_no,
    compose_reply,
    load_config,
    match_wake,
    write_wav,
)

CONFIG = load_config(ROOT / "config" / "wake_voice.json")
WAKE = CONFIG["wake"]
SCRIPT = CONFIG["script"]


def _segment(speech_s: float, start_s: float = 0.0) -> Segment:
    return Segment(np.zeros(int(speech_s * RATE), dtype=np.float32), start_s, start_s + speech_s)


class MatchWakeTest(unittest.TestCase):
    def test_bare_wake_word_has_no_remainder(self) -> None:
        hit = match_wake("오지야!", WAKE)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.remainder, "")

    def test_wake_word_followed_by_command(self) -> None:
        hit = match_wake("오지야 근처에 있는 호수 위치 알려줘.", WAKE)
        self.assertEqual(hit.variant, "오지야")
        self.assertEqual(hit.remainder, "근처에 있는 호수 위치 알려줘")

    def test_spaced_transcription_still_matches(self) -> None:
        hit = match_wake("오 지야 현재 온도와 습도를 알려줘", WAKE)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.remainder, "현재 온도와 습도를 알려줘")

    def test_leading_filler_is_ignored(self) -> None:
        self.assertIsNotNone(match_wake("아 오지야", WAKE))

    def test_exact_variant_requires_whole_utterance(self) -> None:
        self.assertIsNotNone(match_wake("오지", WAKE))
        self.assertIsNone(match_wake("오지 않아", WAKE))
        self.assertIsNone(match_wake("오징어 먹고 싶다", WAKE))

    def test_unrelated_text_is_not_a_wake(self) -> None:
        self.assertIsNone(match_wake("오늘 날씨 좋다", WAKE))
        self.assertIsNone(match_wake("", WAKE))
        self.assertIsNone(match_wake("[BLANK_AUDIO]", WAKE))


class EnergySegmenterTest(unittest.TestCase):
    @staticmethod
    def _tone(seconds: float, amplitude: float = 0.3) -> np.ndarray:
        t = np.arange(int(seconds * RATE)) / RATE
        return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    @staticmethod
    def _silence(seconds: float) -> np.ndarray:
        return np.zeros(int(seconds * RATE), dtype=np.float32)

    def _run(self, stream: np.ndarray, **kw) -> list[Segment]:
        seg = EnergySegmenter(**kw)
        out = []
        for i in range(0, len(stream), WINDOW * 4):
            out.extend(seg.feed(stream[i:i + WINDOW * 4]))
        out.extend(seg.flush())
        return out

    def test_two_bursts_become_two_segments_with_rolls(self) -> None:
        stream = np.concatenate([self._silence(1.0), self._tone(0.7), self._silence(1.0), self._tone(3.0), self._silence(1.0)])
        segments = self._run(stream)
        self.assertEqual(len(segments), 2)
        first, second = segments
        self.assertAlmostEqual(first.start_s, 1.0, delta=0.1)
        self.assertAlmostEqual(first.speech_s, 0.7, delta=0.1)
        self.assertAlmostEqual(second.start_s, 2.7, delta=0.1)
        self.assertAlmostEqual(second.speech_s, 3.0, delta=0.1)
        # 앞 0.25 s + 뒤 0.15 s 여유분이 붙는다
        self.assertAlmostEqual(len(first.samples) / RATE, 0.7 + 0.4, delta=0.1)

    def test_too_short_burst_is_dropped(self) -> None:
        stream = np.concatenate([self._silence(1.0), self._tone(0.1), self._silence(1.0)])
        self.assertEqual(self._run(stream), [])

    def test_overlong_speech_is_cut_at_max(self) -> None:
        stream = np.concatenate([self._silence(0.5), self._tone(5.0), self._silence(1.0)])
        segments = self._run(stream, max_speech_s=2.0)
        self.assertGreaterEqual(len(segments), 2)
        self.assertAlmostEqual(segments[0].speech_s, 2.0, delta=0.1)


def _result(action, status, *, scenario="water", speech="canonical", device=None, pending=None, event_extra=None):
    event = None
    if action is not None:
        event = {"action": action, "status": status}
        if pending is not None:
            event["pending_destination"] = pending
        if event_extra:
            event.update(event_extra)
    decision = RouteDecision(scenario, action, "A", "rule", "test")
    return AssistantResult(heard="x", decision=decision, speech=speech, source_id="SAFE-TEST", map_event=event, device=device)


class ComposeReplyTest(unittest.TestCase):
    def test_lake_candidate_without_distance_uses_script_wording(self) -> None:
        result = _result("find_nearest_water", "confirmation_required", pending={"name": "일감호 주변 수원 표식"}, device={"demo": True})
        reply = compose_reply(result, SCRIPT)
        self.assertEqual(reply.pending, "map")
        self.assertEqual(reply.source, "lake_map")
        self.assertEqual(reply.speech, "네, 가까운 곳에 호수가 있습니다. 목적지로 지정해 드릴까요?")

    def test_lake_distance_is_spoken_as_500m_floor_or_rounded_up(self) -> None:
        result = _result("find_nearest_water", "confirmation_required", pending={"name": "호수"}, device={"demo": False})
        self.assertEqual(compose_reply(result, SCRIPT, distance_m=141.0).speech, "네, 500미터 이내에 호수가 있습니다. 목적지로 지정해 드릴까요?")
        self.assertEqual(compose_reply(result, SCRIPT, distance_m=742.0).speech, "네, 800미터 이내에 호수가 있습니다. 목적지로 지정해 드릴까요?")
        # 지도 이벤트에 거리가 있으면 그것이 우선한다
        with_event = _result("find_nearest_water", "confirmation_required", pending={"name": "호수", "distance_m": 431.0})
        self.assertEqual(compose_reply(with_event, SCRIPT, distance_m=900.0).speech, "네, 500미터 이내에 호수가 있습니다. 목적지로 지정해 드릴까요?")

    def test_lake_rejected_without_fix_falls_back_to_scripted_pending(self) -> None:
        result = _result("find_nearest_water", "rejected", device={"demo": True})
        reply = compose_reply(result, SCRIPT, distance_m=141.0)
        self.assertEqual(reply.pending, "scripted")
        self.assertEqual(reply.source, "lake_scripted")
        self.assertEqual(reply.speech, "네, 500미터 이내에 호수가 있습니다. 목적지로 지정해 드릴까요?")
        self.assertNotIn("데모", reply.speech)

    def test_confirmed_destination_uses_script_line(self) -> None:
        result = _result("confirm_destination", "accepted", scenario="route", speech="네, 목적지로 설정되었습니다.")
        reply = compose_reply(result, SCRIPT)
        self.assertEqual(reply.speech, "목적지가 설정되었습니다.")
        self.assertEqual(reply.source, "lake_confirmed")
        self.assertIsNone(reply.pending)

    def test_lake_rejected_stays_canonical_when_script_disabled(self) -> None:
        script = json.loads(json.dumps(SCRIPT))
        script["lake"]["no_fix"] = "reject"
        result = _result("find_nearest_water", "rejected", speech="가까운 수원 표식을 찾지 못했습니다.")
        reply = compose_reply(result, script)
        self.assertEqual(reply.source, "canonical")
        self.assertIsNone(reply.pending)

    def test_weather_reads_screen_values_without_demo_prefix(self) -> None:
        device = {"demo": True, "environment": {"valid": True, "stale": False, "temp_c": 24.1, "humidity_pct": 79.0}}
        result = _result("status", "accepted", scenario="weather", device=device, speech="데모 값 기준으로, 현장 센서 온도는 …")
        reply = compose_reply(result, SCRIPT)
        self.assertEqual(reply.speech, "네, 온도는 24점 1도입니다. 습도는 79퍼센트입니다.")
        self.assertIsNone(reply.pending)

    def test_weather_with_stale_sensor_keeps_canonical_card(self) -> None:
        device = {"demo": True, "environment": {"valid": True, "stale": True, "temp_c": 24.1, "humidity_pct": 79.0}}
        result = _result("status", "accepted", scenario="weather", device=device, speech="canonical weather")
        self.assertEqual(compose_reply(result, SCRIPT).speech, "canonical weather")

    def test_canonical_reply_detects_map_pending(self) -> None:
        result = _result("save_basecamp", "accepted", speech="저장했습니다.", pending={"name": "x"})
        self.assertEqual(compose_reply(result, SCRIPT).pending, "map")
        result = _result(None, None, speech="카드")
        self.assertIsNone(compose_reply(result, SCRIPT).pending)


class YesNoTest(unittest.TestCase):
    def test_uses_canonical_confirmation_patterns(self) -> None:
        router = RuleRouter()
        self.assertTrue(_yes_no(router, "어"))
        self.assertTrue(_yes_no(router, "네."))
        self.assertFalse(_yes_no(router, "아니"))
        self.assertIsNone(_yes_no(router, "근처에 호수 있어"))


class FakeIO:
    """대화 상태기계 검증용 가짜 STT·응답·스피커."""

    def __init__(self, transcripts: dict[float, str], replies: dict[str, Reply]) -> None:
        self.transcripts = transcripts      # speech_s → 받아쓴 문장 (테스트는 길이로 발화를 구분한다)
        self.replies = replies
        self.spoken: list[str] = []
        self.handled: list[tuple[str, str | None]] = []
        self.events: list[dict] = []
        self.phases: list[str] = []

    def transcribe(self, segment: Segment, phase: str) -> str:
        self.phases.append(phase)
        return self.transcripts[round(segment.speech_s, 2)]

    def handle(self, text: str, pending: str | None) -> Reply:
        self.handled.append((text, pending))
        return self.replies[text]

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def log(self, event: dict) -> None:
        self.events.append(event)


class DialogueTest(unittest.TestCase):
    LAKE = "근처에 있는 호수 위치 알려줘"
    TEMP = "현재 온도와 습도를 알려줘"

    def _dialogue(self, io: FakeIO) -> Dialogue:
        return Dialogue(CONFIG, transcribe=io.transcribe, handle=io.handle, speak=io.speak, log=io.log)

    def test_script_one_lake_then_confirm(self) -> None:
        io = FakeIO(
            {0.6: "오지야", 2.4: self.LAKE, 0.4: "어"},
            {
                self.LAKE: Reply("네, 가까운 곳에 호수가 있습니다. 목적지로 지정해 드릴까요?", "map", "lake_map"),
                "네": Reply("네, 목적지로 설정되었습니다.", None, "canonical"),   # 짧은 긍정 "어"는 "네" 로 정규화돼 넘어온다
            },
        )
        d = self._dialogue(io)
        d.on_segment(_segment(0.6, 1.0), 1.7)
        self.assertEqual(io.spoken, ["네, 무엇을 도와드릴까요?"])
        self.assertEqual(d.state, Dialogue.AWAIT_COMMAND)
        d.on_segment(_segment(2.4, 3.0), 5.5)
        self.assertEqual(d.state, Dialogue.AWAIT_CONFIRM)
        self.assertEqual(d.pending, "map")
        d.on_segment(_segment(0.4, 7.0), 7.5)
        self.assertEqual(io.handled, [(self.LAKE, None), ("네", "map")])
        self.assertEqual(io.spoken[-1], "네, 목적지로 설정되었습니다.")
        self.assertEqual(d.state, Dialogue.FOLLOWUP)
        d.on_tick(7.5 + CONFIG["timeouts"]["followup_wait_s"] + 0.1)
        self.assertEqual(d.state, Dialogue.IDLE)
        self.assertEqual(d.sessions, 1)

    def test_script_two_temperature_humidity(self) -> None:
        io = FakeIO({0.6: "오지야", 2.1: self.TEMP}, {self.TEMP: Reply("네, 온도는 24.1도, 습도는 79퍼센트입니다.", None, "weather_screen")})
        d = self._dialogue(io)
        d.on_segment(_segment(0.6, 1.0), 1.7)
        d.on_segment(_segment(2.1, 3.0), 5.2)
        self.assertEqual(io.spoken, ["네, 무엇을 도와드릴까요?", "네, 온도는 24.1도, 습도는 79퍼센트입니다."])
        self.assertEqual(d.state, Dialogue.FOLLOWUP)
        self.assertEqual(io.phases, ["wake", "command"])   # 대기 중엔 호출어 프롬프트, 세션 중엔 명령 프롬프트

    def test_wake_and_command_in_one_breath_skips_greeting(self) -> None:
        io = FakeIO({2.9: "오지야 " + self.LAKE}, {self.LAKE: Reply("답", "map", "lake_map")})
        d = self._dialogue(io)
        d.on_segment(_segment(2.9, 1.0), 4.0)
        self.assertEqual(io.spoken, ["답"])
        self.assertEqual(io.handled, [(self.LAKE, None)])
        self.assertEqual(d.state, Dialogue.AWAIT_CONFIRM)

    def test_idle_ignores_long_segments_without_transcribing(self) -> None:
        io = FakeIO({}, {})
        d = self._dialogue(io)
        d.on_segment(_segment(6.0, 1.0), 7.0)          # transcripts 에 없으므로 호출되면 KeyError
        self.assertEqual(d.state, Dialogue.IDLE)
        self.assertEqual(io.events[-1]["event"], "ignored_length")

    def test_idle_non_wake_text_does_nothing(self) -> None:
        io = FakeIO({1.2: "오늘 날씨 좋다"}, {})
        d = self._dialogue(io)
        d.on_segment(_segment(1.2, 1.0), 2.3)
        self.assertEqual(d.state, Dialogue.IDLE)
        self.assertEqual(io.spoken, [])
        self.assertEqual(io.events[-1]["event"], "no_wake")

    def test_await_command_times_out_silently(self) -> None:
        io = FakeIO({0.6: "오지야"}, {})
        d = self._dialogue(io)
        d.on_segment(_segment(0.6, 1.0), 1.7)
        d.on_tick(1.7 + CONFIG["timeouts"]["command_wait_s"] + 0.1)
        self.assertEqual(d.state, Dialogue.IDLE)
        self.assertEqual(io.spoken, ["네, 무엇을 도와드릴까요?"])
        self.assertEqual(io.events[-1]["event"], "timeout")

    def test_empty_transcription_keeps_waiting(self) -> None:
        io = FakeIO({0.6: "오지야", 0.5: ""}, {})
        d = self._dialogue(io)
        d.on_segment(_segment(0.6, 1.0), 1.7)
        d.on_segment(_segment(0.5, 3.0), 3.6)
        self.assertEqual(d.state, Dialogue.AWAIT_COMMAND)

    def test_scripted_pending_is_passed_to_handler(self) -> None:
        io = FakeIO({0.6: "오지야", 2.4: self.LAKE, 0.4: "어"}, {self.LAKE: Reply("대본", "scripted", "lake_scripted"), "네": Reply("네, 목적지로 설정되었습니다.", None, "scripted_confirm")})
        d = self._dialogue(io)
        d.on_segment(_segment(0.6, 1.0), 1.7)
        d.on_segment(_segment(2.4, 3.0), 5.5)
        d.on_segment(_segment(0.4, 7.0), 7.5)
        self.assertEqual(io.handled[-1], ("네", "scripted"))
        self.assertIsNone(d.pending)


class FileSourceAndWavTest(unittest.TestCase):
    def test_file_source_virtual_clock_and_window_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.wav"
            write_wav(path, np.zeros(int(0.5 * RATE), dtype=np.float32), min_s=0.0)
            source = FileSource([str(path), str(path)], lead_s=0.5, gap_s=1.0, tail_s=0.5)
            frames = list(source.frames())
            self.assertTrue(all(len(f) == WINDOW for f in frames))
            # 조각(선행·파일·간격·파일·후행 5개)마다 마지막 창을 채우므로 최대 5창까지 길어진다
            self.assertAlmostEqual(source.now(), 0.5 + 0.5 + 1.0 + 0.5 + 0.5, delta=5 * WINDOW / RATE)

    def test_write_wav_pads_short_clips_for_whisper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_wav(Path(tmp) / "s.wav", np.zeros(int(0.3 * RATE), dtype=np.float32))
            with wave.open(str(path), "rb") as w:
                self.assertGreaterEqual(w.getnframes() / w.getframerate(), 1.6)
                self.assertEqual(w.getframerate(), RATE)


if __name__ == "__main__":
    unittest.main()


from wake_voice import apply_wake_lexicon, collapse_repeats, yes_no  # noqa: E402


class PostProcessingTest(unittest.TestCase):
    def test_collapse_repeated_sentences(self) -> None:
        raw = " ".join(["오지야, 근처옷이 있어."] * 20)
        self.assertEqual(collapse_repeats(raw), "오지야, 근처옷이 있어.")

    def test_collapse_repeated_words_without_punctuation(self) -> None:
        self.assertEqual(collapse_repeats("그는 그는 그는 그는 그는 그는"), "그는")
        self.assertEqual(collapse_repeats("근처 호수 위치 알려 줘."), "근처 호수 위치 알려 줘.")

    def test_lexicon_fixes_measured_misrecognitions(self) -> None:
        rules = CONFIG["lexicon"]
        self.assertEqual(apply_wake_lexicon("근처에 있는 포스 위치 알려 줘.", rules), "근처에 있는 호수 위치 알려 줘.")
        self.assertEqual(apply_wake_lexicon("근처에 있는 포수 위치 알려 줘.", rules), "근처에 있는 호수 위치 알려 줘.")
        self.assertEqual(apply_wake_lexicon("탄제운동화습도를 알려줘.", rules), "탄제온도와 습도를 알려줘.")
        self.assertEqual(apply_wake_lexicon("내 설정해줘.", rules), "네 설정해줘.")

    def test_confirmation_vocabulary(self) -> None:
        conf = CONFIG["confirmation"]
        for text in ("어", "네.", "네 설정해줘", "그래, 거기로 했어", "지정해 줘", "오케이"):
            self.assertTrue(yes_no(conf, text), text)
        for text in ("아니", "취소", "설정하지 마"):
            self.assertFalse(yes_no(conf, text), text)
        self.assertIsNone(yes_no(conf, "근처 호수 위치 알려 줘"))

    def test_command_only_variant_needs_a_command(self) -> None:
        self.assertIsNone(match_wake("어디야", WAKE))
        hit = match_wake("어디야 근처 호수 위치 알려 줘", WAKE)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.remainder, "근처 호수 위치 알려 줘")

    def test_measured_wake_misrecognitions_match(self) -> None:
        for text in ("위야.", "오기야. 아잉.", "우디야 근처에 있는 호술이 차이오죠.", "옥이."):
            self.assertIsNotNone(match_wake(text, WAKE), text)


from wake_voice import haversine_m, lake_distance_phrase, load_water_pois, nearest_poi, strip_demo_prefix, waypoint_payload  # noqa: E402


class LakeGeometryTest(unittest.TestCase):
    REFERENCE = SCRIPT["lake"]["no_fix_reference"]

    def test_catalog_water_poi_is_within_500m_of_reference(self) -> None:
        pois = load_water_pois(SCRIPT["lake"]["poi_catalog"])
        self.assertTrue(pois, "저장소의 poi_catalog.json 을 찾지 못했습니다")
        poi, distance = nearest_poi(pois, self.REFERENCE["lat"], self.REFERENCE["lon"])
        self.assertIn("수원", poi["name"])
        self.assertGreater(distance, 50)
        self.assertLess(distance, 500)
        self.assertEqual(lake_distance_phrase(SCRIPT["lake"], distance), "네, 500미터 이내에 호수가 있습니다. 목적지로 지정해 드릴까요?")

    def test_haversine_matches_known_scale(self) -> None:
        self.assertAlmostEqual(haversine_m(37.54, 127.07, 37.55, 127.07), 1111.9, delta=5)

    def test_waypoint_payload_matches_screen_touch_shape(self) -> None:
        payload = waypoint_payload({"lat": 37.5405, "lon": 127.0794, "name": "x"})
        self.assertEqual(payload, {"action": "set", "kind": "destination", "lat": 37.5405, "lon": 127.0794})

    def test_demo_prefix_is_stripped_everywhere(self) -> None:
        self.assertEqual(strip_demo_prefix(C.DEMO_SPEECH_PREFIX + "요청을 분류하지 못했습니다."), "요청을 분류하지 못했습니다.")
        self.assertEqual(strip_demo_prefix("네, 온도는 24.1도입니다."), "네, 온도는 24.1도입니다.")


class SpeechTimingTest(unittest.TestCase):
    """실기 재현: 인사말 재생에 5 s 가 걸려도 명령 대기 창은 말이 끝난 뒤부터 8 s 이상이어야 한다."""

    def test_wait_window_starts_after_greeting_finishes(self) -> None:
        clock = {"t": 1.7}
        io = FakeIO({0.6: "오지야", 1.65: "온도랑 습도 알려줘"}, {"온도랑 습도 알려줘": Reply("답", None, "weather_screen")})

        def speak(text: str) -> None:
            io.spoken.append(text)
            clock["t"] += 5.0            # 합성 + 재생

        d = Dialogue(CONFIG, transcribe=io.transcribe, handle=io.handle, speak=speak, log=io.log, clock=lambda: clock["t"])
        d.on_segment(_segment(0.6, 1.0), 1.7)
        self.assertEqual(d.state, Dialogue.AWAIT_COMMAND)
        # 인사말이 끝난 6.7 s 뒤 사용자가 2 s 말하고 VAD 가 0.5 s 뒤 닫음 → 9.2 s 시점에 도착해도 살아 있어야 한다
        d.on_segment(_segment(1.65, 7.5), 9.2)
        self.assertEqual(io.handled, [("온도랑 습도 알려줘", None)])
        self.assertEqual(d.state, Dialogue.FOLLOWUP)

    def test_without_clock_behaviour_is_unchanged(self) -> None:
        io = FakeIO({0.6: "오지야"}, {})
        d = Dialogue(CONFIG, transcribe=io.transcribe, handle=io.handle, speak=io.speak, log=io.log)
        d.on_segment(_segment(0.6, 1.0), 1.7)
        self.assertAlmostEqual(d.deadline, 1.7 + CONFIG["timeouts"]["command_wait_s"])


from wake_voice import join_wavs, speech_phrases  # noqa: E402


class PhraseAndJoinTest(unittest.TestCase):
    def test_phrases_split_on_commas_with_normal_speed_head(self) -> None:
        phrases = speech_phrases("네, 온도는 27점 1도입니다. 습도는 71퍼센트입니다.", 1.22)
        self.assertEqual([p[0] for p in phrases], ["네.", "온도는 27점 1도입니다.", "습도는 71퍼센트입니다."])
        self.assertAlmostEqual(phrases[0][1], 1 / 1.22, places=3)
        self.assertEqual([p[1] for p in phrases[1:]], [1.0, 1.0])
        self.assertEqual([p[2] for p in phrases], [0.0, 0.2, 0.3])

    def test_phrases_sentence_pause_is_longer(self) -> None:
        phrases = speech_phrases("네, 500미터 이내에 호수가 있습니다. 목적지로 지정해 드릴까요?", 1.22)
        self.assertEqual([p[0] for p in phrases], ["네.", "500미터 이내에 호수가 있습니다.", "목적지로 지정해 드릴까요?"])
        self.assertEqual(phrases[2][2], 0.3)
        self.assertEqual(speech_phrases("목적지가 설정되었습니다.", 1.22), [("목적지가 설정되었습니다.", 1.0, 0.0)])

    def test_join_wavs_inserts_pauses_into_single_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = write_wav(Path(tmp) / "a.wav", np.ones(int(0.5 * RATE), dtype=np.float32) * 0.1, min_s=0.0)
            b = write_wav(Path(tmp) / "b.wav", np.ones(int(1.0 * RATE), dtype=np.float32) * 0.1, min_s=0.0)
            out = join_wavs([(a, 0.0), (b, 0.15)], Path(tmp) / "out.wav", lead_s=0.2, tail_s=0.15)
            with wave.open(str(out), "rb") as w:
                self.assertAlmostEqual(w.getnframes() / w.getframerate(), 0.2 + 0.5 + 0.15 + 1.0 + 0.15, places=2)


class ShortConfirmTest(unittest.TestCase):
    def test_short_empty_utterance_after_question_counts_as_yes(self) -> None:
        io = FakeIO({0.6: "오지야", 2.4: DialogueTest.LAKE, 0.4: ""}, {DialogueTest.LAKE: Reply("호수?", "scripted", "lake_scripted"), "네": Reply("목적지가 설정되었습니다.", None, "scripted_confirm")})
        d = Dialogue(CONFIG, transcribe=io.transcribe, handle=io.handle, speak=io.speak, log=io.log)
        d.on_segment(_segment(0.6, 1.0), 1.7)
        d.on_segment(_segment(2.4, 3.0), 5.5)
        d.on_segment(_segment(0.4, 7.0), 7.5)
        self.assertEqual(io.handled[-1], ("네", "scripted"))
        self.assertEqual(io.spoken[-1], "목적지가 설정되었습니다.")

    def test_short_empty_utterance_without_question_is_ignored(self) -> None:
        io = FakeIO({0.6: "오지야", 0.4: ""}, {})
        d = Dialogue(CONFIG, transcribe=io.transcribe, handle=io.handle, speak=io.speak, log=io.log)
        d.on_segment(_segment(0.6, 1.0), 1.7)
        d.on_segment(_segment(0.4, 3.0), 3.5)
        self.assertEqual(io.handled, [])
        self.assertEqual(d.state, Dialogue.AWAIT_COMMAND)


from wake_voice import Runner  # noqa: E402


class RunnerSurfaceTest(unittest.TestCase):
    """Runner 를 만들지 않고도(엔진·지도 필요) 실행 경로가 부르는 메서드가 전부 있는지 본다 — 편집 중 메서드가 지워진 회귀(2026-09-02)."""

    def test_runner_has_every_method_the_loop_calls(self) -> None:
        for name in ("transcribe", "speak", "handle", "warm_tts", "run", "log", "_reference_position", "_lake_candidate", "_finish", "_phrases"):
            self.assertTrue(callable(getattr(Runner, name, None)), name)

    def test_lake_candidate_uses_reference_when_no_fix(self) -> None:
        stub = Runner.__new__(Runner)          # __init__ 은 엔진·지도 서버가 필요하므로 건너뛴다
        stub.script = SCRIPT
        stub.pois = load_water_pois(SCRIPT["lake"]["poi_catalog"])
        poi, distance, basis = stub._lake_candidate({"gps": {"fix": False}})
        self.assertEqual(basis, "no_fix_reference")
        self.assertLess(distance, 500)
        poi, distance, basis = stub._lake_candidate({"gps": {"fix": True, "lat": 37.5405, "lon": 127.0794}})
        self.assertEqual(basis, "gps_fix")
        self.assertLess(distance, 20)


class ShortVowelConfirmTest(unittest.TestCase):
    def _run(self, heard: str, speech_s: float = 0.4):
        io = FakeIO({0.6: "오지야", 2.4: DialogueTest.LAKE, speech_s: heard}, {DialogueTest.LAKE: Reply("호수?", "scripted", "lake_scripted"), "네": Reply("목적지가 설정되었습니다.", None, "scripted_confirm"), heard: Reply("분류 실패", None, "canonical")})
        d = Dialogue(CONFIG, transcribe=io.transcribe, handle=io.handle, speak=io.speak, log=io.log)
        d.on_segment(_segment(0.6, 1.0), 1.7)
        d.on_segment(_segment(2.4, 3.0), 5.5)
        d.on_segment(_segment(speech_s, 7.0), 7.0 + speech_s + 0.5)
        return io

    def test_misheard_short_vowels_count_as_yes(self) -> None:
        for heard in ("아오.", "오.", "어.", "응", "아", "우"):
            io = self._run(heard)
            self.assertEqual(io.handled[-1], ("네", "scripted"), heard)
            self.assertEqual(io.spoken[-1], "목적지가 설정되었습니다.", heard)

    def test_short_negative_is_not_turned_into_yes(self) -> None:
        io = self._run("아니", 0.5)
        self.assertEqual(io.handled[-1], ("아니", "scripted"))

    def test_longer_utterance_is_a_normal_command(self) -> None:
        io = self._run("근처 호수", 1.4)
        self.assertEqual(io.handled[-1], ("근처 호수", "scripted"))


from wake_voice import QuietMapClient  # noqa: E402


class QuietMapClientTest(unittest.TestCase):
    def _client(self, device):
        client = QuietMapClient("http://127.0.0.1:8790")
        client.device = lambda: device
        client.sent = []
        def _request(method, path, payload=None):
            client.sent.append((method, path, payload))
            return {"action": payload["action"], "status": "accepted", "device": device}
        client._request = _request
        return client

    def test_status_and_water_without_fix_never_reach_the_server(self) -> None:
        client = self._client({"gps": {"fix": False}, "environment": {"valid": True}})
        self.assertEqual(client.command("status")["status"], "accepted")
        water = client.command("find_nearest_water")
        self.assertEqual(water["status"], "rejected")
        self.assertEqual(client.sent, [])

    def test_water_with_fix_and_side_effect_actions_go_to_the_server(self) -> None:
        client = self._client({"gps": {"fix": True, "lat": 37.54, "lon": 127.07}})
        client.command("find_nearest_water")
        client.command("night_on")
        client.command("confirm_destination")
        self.assertEqual([p["action"] for _m, _p, p in client.sent], ["find_nearest_water", "night_on", "confirm_destination"])


from wake_voice import spoken_temperature  # noqa: E402


class SpokenTemperatureTest(unittest.TestCase):
    def test_integer_and_decimal_forms(self) -> None:
        self.assertEqual(spoken_temperature(28.0), "28")
        self.assertEqual(spoken_temperature(24.1), "24점 1")
        self.assertEqual(spoken_temperature(27.96), "28")
        self.assertEqual(spoken_temperature(-3.5), "-3점 5")

