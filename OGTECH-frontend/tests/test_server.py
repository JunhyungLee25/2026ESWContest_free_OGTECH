from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import create_server  # noqa: E402


class ServerSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(port=0, backend_url="http://127.0.0.1:9", llm_health_url="")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_health(self) -> None:
        with urlopen(f"{self.base_url}/health", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["ok"])

    def test_index_serves_kiosk_ui(self) -> None:
        """기본 문서는 MAP 키오스크 UI(video.html)여야 한다."""
        with urlopen(f"{self.base_url}/", timeout=2) as response:
            index = response.read().decode("utf-8")
        self.assertIn("video_app.js", index)
        self.assertIn("mapCanvas", index)
        with urlopen(f"{self.base_url}/video_app.js", timeout=2) as response:
            self.assertIn("text/javascript", response.headers["Content-Type"])

    def test_legacy_medical_ui_is_gone(self) -> None:
        """구 의료 도메인 TEST UI는 더 이상 서빙되지 않는다."""
        for path in ("/js/app.js", "/js/api.js", "/js/features/inventory.js"):
            with self.assertRaises(HTTPError) as context:
                urlopen(f"{self.base_url}{path}", timeout=2)
            self.assertEqual(context.exception.code, 404)

    def test_status_does_not_fake_connections(self) -> None:
        with urlopen(f"{self.base_url}/ui-api/status", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["ui"]["ok"])
        self.assertFalse(payload["backend"]["ok"])
        self.assertFalse(payload["llm"]["ok"])

    def test_unknown_file_is_not_listed(self) -> None:
        with self.assertRaises(HTTPError) as context:
            urlopen(f"{self.base_url}/missing-file", timeout=2)
        self.assertEqual(context.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
