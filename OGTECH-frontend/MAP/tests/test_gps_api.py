"""로컬 GNSS API와 지도 경로 연계 통합 테스트."""

from __future__ import annotations

from array import array
from http import HTTPStatus
from http.client import HTTPConnection
from urllib.parse import quote
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest

from app import AppHandler, build_server
from gps_service import GpsConfiguration, encode_stm32_telemetry


class GpsApiIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.server = build_server(
            "127.0.0.1",
            0,
            gps_configuration={"mode": "replay"},
            waypoint_path=Path(self.temporary.name) / "waypoints.json",
            force_sample_map=True,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)
        self.temporary.cleanup()

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json"}
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5.0)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def fetch_text(self, path: str) -> str:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5.0)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read().decode("utf-8")
        finally:
            connection.close()
        self.assertEqual(response.status, 200, f"{path} 응답 {response.status}")
        return body

    def test_replay_fix_routes_without_masquerading_as_live_sensor(self) -> None:
        status, error = self.request(
            "POST",
            "/api/gps/configure",
            {"mode": "air530", "port": "/dev/null", "baud": "invalid"},
        )
        self.assertEqual(status, 422)
        self.assertIn("baud", str(error["error"]))

        deadline = time.monotonic() + 2.0
        gps: dict[str, object] = {}
        while time.monotonic() < deadline:
            status, gps = self.request("GET", "/api/gps")
            if gps.get("fix") is True:
                break
            time.sleep(0.02)
        self.assertEqual(status, 200)
        self.assertTrue(gps["fix"])
        self.assertTrue(gps["demo"])

        _, map_overview = self.request("GET", "/api/map")
        points = map_overview["suggested_points"]
        assert isinstance(points, dict)
        destination = points["destination"]
        status, route = self.request(
            "POST",
            "/api/route",
            {
                "current": {"lat": gps["lat"], "lon": gps["lon"]},
                "destination": destination,
                "accuracy_m": gps.get("acc_m"),
                "satellites": gps.get("satellites", 0),
                "age_s": gps.get("age_s", 0),
                "source": "demo",
                "fix": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(route["contract"]["map_and_route_computed_by_code"])
        self.assertFalse(route["device_state"]["gps"]["fix"])
        self.assertTrue(route["demo"])

        status, stopped = self.request("POST", "/api/gps/stop", {})
        self.assertEqual(status, 200)
        self.assertEqual(stopped["mode"], "off")
        self.assertFalse(stopped["fix"])

    def test_product_screen_shares_markup_with_filming_screen(self) -> None:
        """제품 화면과 촬영 화면은 같은 마크업·CSS·그리기 코드를 쓴다.

        2026-08-30 사용자 지시: 디자인은 예외 없이 같아야 한다. 화면 코드를 한 벌만
        두어 둘이 갈라지지 않게 한다. 차이는 데이터뿐이다 — 제품 화면은 좌표·경로가
        STM32 실측이고, 촬영 화면은 시나리오 고정값에 촬영용 기능이 붙는다.
        """
        product = self.fetch_text("/product/")
        video = self.fetch_text("/video/")
        self.assertEqual(product, video, "두 화면 마크업이 다릅니다")

        # 같은 계기 5칸·같은 지도·같은 조작 버튼을 쓴다.
        for marker in (
            'id="glanceLocation"', 'id="glanceSun"', 'id="glanceCoordinate"',
            'id="glanceEnv"', 'id="glanceCo"',
            'id="currentLatitude"', 'id="currentLongitude"',
            'id="mapCanvas"', 'id="readout"', 'id="statusToast"',
            "video_styles.css", "video_app.js", "video_map.js",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, product)

        # 제품 화면 경로로도 같은 자산이 내려가야 한다.
        for path in (
            "/product/video_app.js", "/product/video_map.js",
            "/product/video_styles.css", "/product/styles.css",
        ):
            with self.subTest(path=path):
                self.fetch_text(path)

        # 화면 코드가 경로로 모드를 가른다.
        app = self.fetch_text("/product/video_app.js")
        self.assertIn('window.location.pathname.startsWith("/product")', app)
        # 제품 화면은 좌표를 꾸며내지 않는다.
        self.assertIn('latitude.textContent = "좌표 없음"', app)

    def test_diagnostics_rejects_truncated_wav_header(self) -> None:
        invalid = Path(self.temporary.name) / "invalid.wav"
        invalid.write_bytes(b"RIFF" + (b"\x00" * 42))

        self.assertFalse(AppHandler._wav_ready(invalid))

    def test_button_api_exposes_only_validated_enum_event(self) -> None:
        self.server.gps._handle_line(
            encode_stm32_telemetry(
                {
                    "v": 1,
                    "event": "button",
                    "seq": 2,
                    "button": "voice",
                    "state": "pressed",
                    "held_ms": 0,
                }
            ),
            mode="stm32",
        )

        status, buttons = self.request("GET", "/api/buttons")

        self.assertEqual(status, 200)
        self.assertEqual(buttons["last_event"]["button"], "voice")
        self.assertFalse(buttons["coordinates_accepted"])
        self.assertNotIn("lat", json.dumps(buttons))
        self.assertNotIn("lon", json.dumps(buttons))

        status, error = self.request("POST", "/api/power/shutdown-ack", {})
        self.assertEqual(status, 422)
        self.assertIn("종료 대기", str(error["error"]))
        status, error = self.request("POST", "/api/power/shutdown-cancel", {})
        self.assertEqual(status, 422)
        self.assertIn("ACK", str(error["error"]))

    def test_power_api_only_accepts_confirmed_pending_transaction(self) -> None:
        self.server.gps.stop()
        with self.server.gps._lock:  # 테스트 전용: 직렬 장치 없이 상태 머신만 검증한다.
            self.server.gps._configuration = GpsConfiguration(
                mode="stm32", port="test", baud=115200
            )
            self.server.gps._reset_state("stm32")
            self.server.gps._state["connected"] = True

        requested = encode_stm32_telemetry(
            {
                "v": 1,
                "event": "power",
                "seq": 1,
                "state": "shutdown_requested",
                "gate_on": True,
                "shutdown_pending": True,
            }
        )
        self.server.gps._handle_line(requested, mode="stm32")

        status, ack = self.request("POST", "/api/power/shutdown-ack", {})
        self.assertEqual(status, 200)
        self.assertTrue(ack["queued"])

        acknowledged = encode_stm32_telemetry(
            {
                "v": 1,
                "event": "power",
                "seq": 2,
                "state": "shutdown_ack",
                "gate_on": True,
                "shutdown_pending": True,
            }
        )
        self.server.gps._handle_line(acknowledged, mode="stm32")
        status, cancel = self.request("POST", "/api/power/shutdown-cancel", {})
        self.assertEqual(status, 200)
        self.assertTrue(cancel["queued"])

    def test_voice_map_api_uses_enum_action_and_confirmation(self) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            _, device = self.request("GET", "/api/device")
            if isinstance(device.get("gps"), dict) and device["gps"].get("fix") is True:
                break
            time.sleep(0.02)

        status, error = self.request(
            "POST",
            "/api/voice/commands",
            {"action": "route_basecamp", "lat": 37.5, "lon": 127.0},
        )
        self.assertEqual(status, 422)
        self.assertIn("action과 request_id", str(error["error"]))

        status, proposed = self.request(
            "POST", "/api/voice/commands", {"action": "find_nearest_water"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(proposed["status"], "confirmation_required")
        self.assertIsNone(proposed["device"]["waypoints"]["destination"])
        self.assertNotIn("lat", proposed["pending_destination"])

        status, confirmed = self.request(
            "POST", "/api/voice/commands", {"action": "confirm_destination"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(confirmed["status"], "accepted")
        self.assertEqual(
            confirmed["device"]["waypoints"]["destination"]["source"],
            "demo_offline_catalog",
        )
        self.assertTrue(confirmed["device"]["demo"])

        status, night = self.request(
            "POST", "/api/voice/commands", {"action": "night_on"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(night["ui"]["night"])

        status, voice = self.request("GET", "/api/voice")
        self.assertEqual(status, 200)
        self.assertTrue(voice["contract"]["enum_actions_only"])
        self.assertFalse(voice["contract"]["coordinates_accepted_from_voice"])

    def test_video_screen_uses_konkuk_pois_and_glance_layout(self) -> None:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5.0)
        try:
            connection.request("GET", "/video/")
            response = connection.getresponse()
            html = response.read().decode("utf-8")
        finally:
            connection.close()
        self.assertEqual(response.status, 200)
        # 2026-08-29: CO 농도 칸의 'CO 전용 · DEMO' 문구 제거. 화면 어디에도 DEMO 배지가 없다.
        self.assertNotIn("CO 전용", html)
        self.assertEqual(html.count("DEMO"), 0)
        self.assertIn('id="coValue">0 ppm', html)
        # 온도·습도는 각각 id를 가진 별도 요소이고, 온도는 data-level로 색을 정한다.
        self.assertIn('id="envTemperature" class="env-temperature" data-level="warm"', html)
        self.assertIn('id="envHumidity" class="sub env-humidity"', html)
        # 경로 이탈 경고 배너는 숨김 상태로 존재한다.
        self.assertIn('id="routeAlert" role="alert" hidden', html)
        self.assertIn("경로 이탈", html)
        # 하단 버튼 4개는 그대로다.
        for button in ("btnDestination", "btnCheckpoint", "btnBasecamp", "btnNight"):
            self.assertEqual(html.count(f'id="{button}"'), 1)
        self.assertIn("video_app.js", html)
        self.assertIn("공학관", html)
        self.assertIn("일감호", html)
        self.assertIn("LOCATION · TIME", html)
        self.assertIn("서울 광진구", html)
        self.assertIn("대한민국", html)
        self.assertIn("CURRENT COORD", html)
        self.assertNotIn("POSITION", html)
        self.assertNotIn("AGE 1s", html)
        self.assertNotIn("±4.2 m", html)
        self.assertNotIn("tag-offline", html)
        self.assertNotIn("map-topline", html)
        self.assertIn("30.0°", html)
        self.assertIn("55% RH", html)
        self.assertIn("btnCheckpoint", html)
        self.assertIn("btnBasecamp", html)
        self.assertIn("btnNight", html)
        self.assertIn("arrivalCard", html)
        self.assertIn('id="readoutLabel">목적지', html)
        self.assertIn("readoutEta", html)
        self.assertIn("readoutRemainingTime", html)
        self.assertIn("30.0°C (86.0°F)", html)
        self.assertIn("CO 농도", html)
        self.assertIn("0 ppm", html)
        self.assertIn("목적지에 도착하였습니다.", html)
        # 재생은 Web Audio 가 한다. <audio> 요소는 두지 않는다 — 젯슨(L4T aarch64)
        # Firefox 는 미디어 디코더가 죽어 있어 <audio> 로는 소리가 나지 않는다.
        self.assertNotIn("<audio", html)
        self.assertNotIn("dialogueCard", html)
        self.assertNotIn("나 목마른데 물 마실 곳 찾아줘", html)

        connection = HTTPConnection("127.0.0.1", self.port, timeout=5.0)
        try:
            connection.request("GET", "/video/video_app.js")
            response = connection.getresponse()
            video_app = response.read().decode("utf-8")
        finally:
            connection.close()
        self.assertEqual(response.status, 200)
        self.assertIn('timeZone: "Asia/Seoul"', video_app)
        # 고정 녹음 세 개는 그리기 코드가 파일 이름으로 직접 받아 재생한다.
        self.assertIn('file: "destination_set.wav"', video_app)
        self.assertIn('file: "destination_arrived.wav"', video_app)
        self.assertIn('file: "return_to_base.wav"', video_app)
        self.assertNotIn("daylight_detail.wav", video_app)
        self.assertIn('second: "2-digit"', video_app)
        self.assertIn("speedMps: 1.4", video_app)
        self.assertIn("function saveCheckpoint()", video_app)
        self.assertIn("function showBasecampRoute()", video_app)
        self.assertIn("async function startAutoDemo()", video_app)
        self.assertIn('event.key === "a" || event.key === "A"', video_app)
        self.assertIn("const AUTO_DEMO_DELAYS_MS", video_app)
        self.assertIn("function routeOnTrails(", video_app)
        self.assertIn("function selectMapDestination(", video_app)
        self.assertIn('canvas.addEventListener("click", selectMapDestination)', video_app)
        self.assertIn("귀환 권고 시각과 베이스캠프 경로를 확인하세요", video_app)
        self.assertNotIn("돌아가세요", video_app)
        self.assertNotIn("CO 전용 · DEMO", video_app)
        self.assertIn("const ROUTE_DEVIATION_THRESHOLD_M = 30", video_app)
        self.assertIn("function routeOffsetMeters(", video_app)
        self.assertIn("function routeDeviation(", video_app)
        self.assertIn("경로 이탈 · ${Math.round(deviation.offsetM)} m · 현재 위치와 경로를 확인하세요", video_app)
        self.assertIn("function temperatureLevel(", video_app)
        self.assertIn('event.key === "d" || event.key === "D"', video_app)
        self.assertIn('playFixedAudio("arrival")', video_app)
        self.assertIn('playFixedAudio("basecamp")', video_app)
        self.assertIn("베이스캠프가 등록되었습니다.", video_app)
        self.assertIn("베이스캠프 복귀 경로가 설정되었습니다.", video_app)
        self.assertIn("야간 모드가 활성화되었습니다.", video_app)
        self.assertIn("가장 가까운 지점에 호수가 있습니다.", video_app)
        self.assertIn("이곳을 목적지로 지정할까요?", video_app)
        self.assertIn("네, 목적지로 설정되었습니다.", video_app)
        self.assertIn("Base Camp에 도착하였습니다.", video_app)
        self.assertIn("function solarEventUtcHour(", video_app)
        self.assertIn("function solarEventDate(", video_app)
        self.assertIn("function todayDaylight(", video_app)
        self.assertIn("function daylightForDisplay()", video_app)
        self.assertIn("function daylightWarningText()", video_app)
        self.assertIn("function formatDaylightStatus(daylight)", video_app)
        self.assertIn("일몰 시간이 지났습니다. 귀환 권고 시각과 베이스캠프 경로를 확인하세요.", video_app)
        self.assertIn("분 초과", video_app)
        self.assertIn("Math.ceil(Math.abs(differenceMs) / 60000)", video_app)
        self.assertIn("귀환 권고 시각과 베이스캠프 경로를 확인하세요.", video_app)
        self.assertNotIn("돌아가세요", video_app)
        self.assertNotIn("VIDEO_SUNRISE", video_app)
        self.assertNotIn('sunset: "19:32"', video_app)
        self.assertIn("function formatDaylightRemaining(minutes)", video_app)
        self.assertIn("function setDaylightGlance(scene)", video_app)
        self.assertIn("function formatCoordinate(value)", video_app)
        self.assertIn("function setCurrentCoordinateGlance(current)", video_app)
        self.assertIn('document.querySelector("#locationClock")', video_app)
        self.assertNotIn('document.querySelector("#mapName")', video_app)
        self.assertIn("const etaTimeFormatter", video_app)
        self.assertIn("remainingSeconds / 60", video_app)
        self.assertIn("예상 도착", video_app)
        self.assertNotIn('"#glanceRoute"', video_app)
        self.assertIn('const targetLabel = scene.target === "basecamp" ? "BASE CAMP" : "목적지"', video_app)
        self.assertIn('document.querySelector("#currentLatitude")', video_app)
        self.assertIn('document.querySelector("#currentLongitude")', video_app)
        self.assertNotIn("LLM 숫자 생성 안 함", video_app)
        self.assertNotIn("지도 엔진 경로", video_app)
        self.assertIn('routeSub: "목적지"', video_app)
        self.assertIn('routeSub: "BASE CAMP"', video_app)
        self.assertNotIn("일감호 목적지", video_app)
        self.assertNotIn("일감호 경로 이동 재생", video_app)
        self.assertNotIn("BASE CAMP 복귀 경로 재생", video_app)

        for audio_name in (
            "destination_set.wav",
            "destination_arrived.wav",
            "return_to_base.wav",
        ):
            connection = HTTPConnection("127.0.0.1", self.port, timeout=5.0)
            try:
                connection.request("GET", f"/video/{audio_name}")
                response = connection.getresponse()
                audio = response.read()
            finally:
                connection.close()
            self.assertEqual(response.status, 200)
            self.assertGreater(len(audio), 1_000)
            self.assertEqual(audio[:4], b"RIFF")

        connection = HTTPConnection("127.0.0.1", self.port, timeout=5.0)
        try:
            connection.request("GET", "/video/daylight_detail.wav")
            response = connection.getresponse()
            response.read()
        finally:
            connection.close()
        self.assertEqual(response.status, 404)

        connection = HTTPConnection("127.0.0.1", self.port, timeout=5.0)
        try:
            connection.request("GET", "/video/video_map.js")
            response = connection.getresponse()
            map_data = response.read().decode("utf-8")
        finally:
            connection.close()
        self.assertEqual(response.status, 200)
        self.assertIn("relation/7885627", map_data)
        self.assertIn("way/369210727", map_data)
        self.assertIn("map_engine.find_route (A*)", map_data)

    def test_keep_alive_second_request_still_gets_error_json(self) -> None:
        """같은 소켓의 두 번째 요청에서도 422 JSON이 와야 한다(WORKLOG #16, 무응답 회귀)."""
        status, stopped = self.request("POST", "/api/gps/stop", {})
        self.assertEqual(status, 200)
        self.assertFalse(stopped["fix"])

        connection = HTTPConnection("127.0.0.1", self.port, timeout=3.0)
        try:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read().decode("utf-8"))["status"], "ok")

            body = json.dumps({"action": "save_current", "kind": "checkpoint"}).encode("utf-8")
            connection.request(
                "POST",
                "/api/waypoints",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()  # 수정 전: 응답 없이 timeout
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()
        self.assertEqual(response.status, 422)
        self.assertIn("fix", str(payload["error"]))


    def test_no_screen_ever_renders_the_word_demo(self) -> None:
        """화면 규칙: 사용자에게 보이는 문자열에 DEMO 를 넣지 않는다.

        2026-08-30 사용자 지시(재발 방지). 모의 데이터 경고 자체는 유지하되
        표기는 한글 `모의 데이터` 또는 SAMPLE 을 쓴다. DEMO_MAP·
        AUTO_DEMO_DELAYS_MS 처럼 식별자에 붙은 형태와 API 상태값
        "demo"(소문자)는 화면에 그대로 나오지 않으므로 대상이 아니다.
        """
        # 식별자의 일부가 아닌 홑낱말 DEMO 만 잡는다.
        word = re.compile(r"(?<![A-Za-z0-9_])DEMO(?![A-Za-z0-9_])")
        served = (
            "/",
            "/select/",
            "/product/",
            "/video/",
            "/app.js",
            "/product/video_app.js",
            "/video/video_app.js",
            "/video/video_map.js",
            "/styles.css",
            "/product/styles.css",
            "/video/video_styles.css",
        )
        for path in served:
            with self.subTest(path=path):
                found = word.findall(self.fetch_text(path))
                self.assertEqual(found, [], f"{path} 에 DEMO 문구가 있습니다")


    def test_screen_select_offers_both_screens(self) -> None:
        """부팅 화면(/select/)에서 제품·촬영 화면을 터치로 고를 수 있어야 한다.

        2026-08-30 사용자 요구: 자동 부팅이라 한쪽이 뜨면 다른 쪽을 못 골랐다.
        """
        html = self.fetch_text("/select/")
        self.assertIn('href="/product/"', html)
        self.assertIn('href="/video/?live=1"', html)
        self.assertIn("화면을 선택하세요", html)

    def test_every_touch_target_is_visible(self) -> None:
        """터치 대상은 화면에 보이는 형태여야 한다.

        아무 그림도 그리지 않는 클릭 영역은 사용자가 찾을 수 없고, 실수로 눌렸을 때
        화면이 왜 바뀌었는지 설명할 방법이 없다. 조작 수단은 눈에 보이게 둔다.
        """
        # 배경도 테두리도 없이 클릭만 받는 요소를 만드는 코드를 잡는다.
        invisible = re.compile(
            r'background:\s*"transparent"[^}]*cursor:\s*"pointer"'
            r'|cursor:\s*"pointer"[^}]*background:\s*"transparent"'
        )
        served = (
            "/select/",
            "/product/",
            "/product/video_app.js",
            "/video/",
            "/video/video_app.js",
        )
        for path in served:
            with self.subTest(path=path):
                self.assertEqual(
                    invisible.findall(self.fetch_text(path)),
                    [],
                    f"{path} 에 보이지 않는 클릭 영역이 있습니다",
                )


    def test_screen_text_is_spoken_by_the_product_voice(self) -> None:
        """화면에 뜨는 문구는 제품과 같은 목소리로 읽어 준다.

        2026-08-30 사용자 지시: 기계음 남성(브라우저 speechSynthesis)이 아니라
        sherpa KSS 여성 0.9배속이어야 하고, 텍스트가 나오는 곳에는 음성도 나와야 한다.
        모델이 없는 환경에서는 503 을 주고 화면은 글자만 보여 준다.
        """
        app = self.fetch_text("/video/video_app.js")
        # 브라우저 TTS 는 쓰지 않는다(젯슨 Firefox 에서 espeak 남성으로 떨어진다).
        self.assertNotIn("SpeechSynthesisUtterance", app)
        self.assertNotIn("window.speechSynthesis", app)
        # 재생은 Web Audio 로 하고 WAV 는 우리가 직접 뜯는다. 2026-08-31 실측:
        # 젯슨 Firefox 는 <audio> 가 MEDIA_ERR_DECODE, decodeAudioData 가
        # EncodingError 로 떨어진다(오실레이터는 정상 → 출력이 아니라 디코더 문제).
        self.assertNotIn("new Audio(", app)
        self.assertNotIn("ctx.decodeAudioData(", app)
        self.assertIn("function decodeWav(", app)
        self.assertIn("ctx.createBuffer(channels, frames, rate)", app)
        self.assertIn("createBufferSource", app)
        # 토스트·경고 배너·도착 카드가 모두 음성을 탄다.
        self.assertIn("/api/tts?text=", app)
        self.assertIn("speak(message);", app)
        # CO 경보만 예외 — 소리는 Jetson 데몬이 내고 화면은 글자만 띄운다(2026-08-31).
        self.assertIn('announce("alert", live.alertSpoken ? alertText : "")', app)
        self.assertIn('!String(alert.kind || "").startsWith("co_")', app)
        # 배너는 서버가 주는 alert.text 를 쓴다 — alert.message 를 읽어 CO 경보에도
        # 일조 경고를 띄우고 읽던 회귀를 막는다.
        self.assertNotIn("alert.message", app)
        self.assertIn('announce("arrival", arrivalText)', app)
        self.assertIn('announce("routeAlert"', app)
        # 자동 시연이 합성 문장을 중간에 자르지 않는다(일조 경고는 남은 분이 들어가
        # 길이가 매번 달라진다). 고정 대기 뒤 남은 길이만큼 더 기다린다.
        self.assertIn("function speechRemainingMs()", app)
        self.assertIn("waitForAutoDemo(runId, speechRemainingMs())", app)

        connection = HTTPConnection("127.0.0.1", self.port, timeout=20.0)
        try:
            connection.request("GET", "/api/tts?text=" + quote("도착하였습니다"))
            response = connection.getresponse()
            body = response.read()
        finally:
            connection.close()
        self.assertEqual(response.status, HTTPStatus.OK)
        if response.headers.get("Content-Type") == "audio/wav":
            self.assertEqual(body[:4], b"RIFF")
        else:
            # 개발 PC 에는 모델이 없다. 오류가 아니라 "음성 없음"으로 답해서
            # 키오스크 콘솔에 리소스 오류가 쌓이지 않게 한다.
            payload = json.loads(body.decode("utf-8"))
            self.assertFalse(payload["available"])
            self.assertIn("reason", payload)

    def test_voice_answers_read_the_values_on_screen(self) -> None:
        """음성 문답은 계기판에 떠 있는 값을 그대로 읽는다.

        마이크로 물어본 것에 답하는 장면(2026-09-02 촬영 요청)이다. 답변 숫자는
        화면이 이미 가진 값이어야 하고, 값이 없으면 없다고 답해야 한다 —
        답변용 숫자를 따로 만들어 내면 화면과 음성이 서로 다른 말을 한다.
        문장은 값만 짧게 말한다.
        """
        app = self.fetch_text("/video/video_app.js")
        self.assertIn("function environmentAnswerText()", app)
        self.assertIn("function coAnswerText()", app)
        self.assertIn("function daylightAnswerText()", app)
        self.assertIn("const VOICE_ANSWERS", app)
        self.assertIn("function answerAloud(kind)", app)
        # 값만 짧게 말한다. "현장 센서"·"계측값" 같은 꾸밈말은 붙이지 않는다(2026-09-02 지시).
        self.assertIn("현재 온도는 ${spokenDecimal(temperatureC, 1)}도, ", app)
        self.assertNotIn("현장 센서", app)
        self.assertNotIn("센서 계측값", app)
        self.assertIn("습도는 ${Math.round(humidityPct)}퍼센트입니다.", app)
        self.assertIn("현재 일산화탄소는 ${Math.round(co.ppm)}피피엠입니다.${level}", app)
        self.assertIn("일몰까지 ${spokenDaylightRemaining(minutes)} 남았습니다.", app)
        self.assertIn("일몰 후 ${spokenDaylightRemaining(minutes)} 지났습니다.", app)
        # 값이 없으면 없다고 답한다(마지막 값이나 시나리오 값으로 메우지 않는다)
        self.assertIn("온도와 습도 값이 아직 없습니다.", app)
        self.assertIn("일산화탄소 값이 아직 없습니다.", app)
        self.assertIn("일산화탄소 센서는 예열 중입니다.", app)
        self.assertIn("일몰 시간을 아직 계산하지 못했습니다.", app)
        # 물어본 순간 기다리지 않도록 값이 바뀔 때마다 미리 합성해 둔다
        self.assertIn("async function prefetchSpeech(text)", app)
        self.assertIn("function warmVoiceAnswers()", app)
        self.assertIn("warmVoiceAnswers();", app)
        # 계기판 표기와 답변이 같은 숫자를 쓴다
        self.assertIn("function spokenDaylightRemaining(minutes)", app)
        self.assertIn("return `${spokenDaylightRemaining(minutes)} 남음`;", app)
        # 촬영 화면 전용이다. 제품 화면의 답은 음성 계층이 만든다.
        self.assertIn('event.key === "w" || event.key === "W"', app)
        self.assertIn('event.key === "o" || event.key === "O"', app)
        self.assertIn('event.key === "s" || event.key === "S"', app)
        html = self.fetch_text("/video/")
        self.assertIn("음성 문답", html)

    def test_basecamp_appears_only_after_it_is_registered(self) -> None:
        """베이스캠프는 버튼을 눌러 등록하기 전에는 지도에 없다.

        2026-09-02 사용자 지시. 화면을 켜자마자 공학관 옆이 베이스캠프로 찍혀 있으면
        등록하지 않은 지점을 등록된 것처럼 보여 주는 것이다. 체크포인트도 같다 —
        누른 순간의 현재 위치가 저장 지점이 된다.
        """
        app = self.fetch_text("/video/video_app.js")
        self.assertIn("basecampRegistered: false", app)
        self.assertIn("state.basecampRegistered ? state.map.basecamp : null", app)
        self.assertNotIn("const basecamp = LIVE_MODE ? live.basecamp : state.map.basecamp;", app)
        self.assertIn("if (state.sceneKey === 1 || !state.basecampRegistered) {", app)
        # 저장은 소리로도 알려 준다(showToast 가 같은 문구를 읽는다).
        self.assertIn("현재 위치를 체크포인트로 저장했습니다.", app)
        self.assertIn("베이스캠프가 등록되었습니다.", app)
        self.assertIn("WARM_SPEECH_PHRASES", app)
        # 처음으로 되돌리면 저장한 지점도 함께 지워진다.
        self.assertIn("function resetDemo()", app)
        self.assertIn("state.checkpoint = null;", app)
        self.assertIn("state.basecampRegistered = false;", app)
        self.assertIn('event.key === "r" || event.key === "R"', app)
        self.assertIn("    resetDemo();", app)

    def test_synthesized_speech_is_as_loud_as_the_recorded_clips(self) -> None:
        """합성 음성 크기를 녹음 클립과 맞춘다.

        정규화가 없으면 합성 원본 피크가 0.5 근처라 녹음 클립(0.82)보다 4 dB 가량
        작다. 같은 화면에서 소리 크기가 널뛰지 않게 Co-LLM tts_pipeline 과 같은
        기준(TTS_TARGET_PEAK_RATIO 0.82, 최대 배율 4.0)으로 올린다.
        """
        import speech_service

        self.assertAlmostEqual(speech_service.SPEECH_TARGET_PEAK_RATIO, 0.82)
        self.assertAlmostEqual(speech_service.SPEECH_MAX_GAIN, 4.0)
        quiet = [0.0, 0.1, -0.205, 0.05]
        data = speech_service._pcm16_wav(quiet, 22050)
        samples = array("h")
        samples.frombytes(data[44:])
        self.assertAlmostEqual(max(abs(v) for v in samples) / 32768.0, 0.82, places=3)
        # 무음을 증폭해 잡음을 만들지 않는다.
        silent = speech_service._pcm16_wav([0.0, 0.0], 22050)
        silent_samples = array("h")
        silent_samples.frombytes(silent[44:])
        self.assertEqual(max(abs(v) for v in silent_samples), 0)


if __name__ == "__main__":
    unittest.main()
