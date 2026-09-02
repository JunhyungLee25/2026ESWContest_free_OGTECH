# MAP 폴더 사용법 — 지도 화면 실행

> **2026-08-30 정정:** 정본 제품 화면은 `http://127.0.0.1:8790/video/?live=1`이고 젯슨
> 키오스크도 이 화면을 연다. `/product/`은 지도 엔진·음성·웨이포인트까지 붙은 구 화면이다. 촬영용 최신 순서와
> STM32 단독 GPS 기록 한계는 [VIDEO_DEMO_2026-08-09.md](kiosk/VIDEO_DEMO_2026-08-09.md)를 따른다.
> 실제 배선·연동은 [STM32_JETSON_SETUP.md](STM32_JETSON_SETUP.md)를 따른다.

Jetson에 SSH로 접속해서 쓰는 절차입니다. 노트북에서 `ssh kit@<젯슨IP>` 로 붙은 뒤 그대로 따라 하면 됩니다.

## 표기 약속

| 표기 | 뜻 |
|---|---|
| `{위치}` | 명령을 치기 **전에 있어야 하는 폴더**. 다르면 파일을 못 찾습니다 |
| `{실행}` | 실제로 타이핑하는 명령 |
| `{화면}` | Chromium에서 열 주소 |

## 여기 두 개가 들어 있습니다 — 뭘 실행할지부터 고릅니다

| 무엇 | 언제 쓰나 | `{위치}` |
|---|---|---|
| **A. 오늘 영상 화면** | **공학관↔일감호 컷 편집용 DEMO.** 합성값 표시 | `/home/kit/00_TEST/MAP/kiosk/video.html` |
| **A-실제. 정본 제품 화면** | STM32 온·습도·CO 실값을 받는 정본 화면 | `http://127.0.0.1:8790/video/?live=1` |
| **B. 지도 변환 검증 앱** | GraphML 넣어 보기, GPS 연결 확인. 개발자 도구 | `/home/kit/00_TEST/MAP` |

**시연 영상을 찍는 거라면 A만 보면 됩니다.** A는 서버도 GPS도 필요 없습니다.

---

## 0. 처음 한 번만 — 파일 확인

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/MAP` |
| `{실행}` | `ls` |

```bash
cd /home/kit/00_TEST/MAP
ls
```

아래가 보여야 합니다.

```
app.py  gps_service.py  map_engine.py  requirements.txt
kiosk  runtime  sample_data  static  tests
```

`kiosk` 폴더 안도 확인합니다. 오늘 촬영에는 **`video.html`, `video_map.js`, WAV 네 개**가 필요합니다.

```bash
ls /home/kit/00_TEST/MAP/kiosk
```

```
VIDEO_DEMO_2026-08-09.md  video.html  video_app.js  video_map.js
video_styles.css  destination_set.wav  destination_arrived.wav
return_to_base.wav  daylight_detail.wav
```

---

# A. 시연용 화면 (촬영용)

**서버가 필요 없습니다.** 지도 데이터가 이미 정적 파일로 구워져 있어서 백엔드 기동에 촬영 일정이 묶이지 않습니다.

## A-1. 젯슨 화면에 직접 띄우기 (촬영은 이 방법)

SSH로 명령을 치지만 화면은 젯슨에 달린 7인치에 나옵니다. 그래서 **`DISPLAY=:0` 을 반드시 앞에 붙입니다.** 빼면 "cannot open display" 로 죽습니다.

| | |
|---|---|
| `{위치}` | 아무 데나 (절대 경로로 엽니다) |
| `{실행}` | 아래 한 줄 |
| `{화면}` | 젯슨에 연결된 7인치 |

```bash
DISPLAY=:0 chromium-browser --kiosk --window-size=1024,600 file:///home/kit/00_TEST/MAP/kiosk/video.html
```

`chromium-browser` 가 없다고 나오면 `chromium` 으로 바꿔서 다시 칩니다.

끄기: 젯슨 키보드에서 `Alt+F4`, 또는 SSH에서

```bash
pkill chromium
```

## A-2. 로컬 서버로 띄우기 (노트북 브라우저에서 볼 때)

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/MAP/kiosk` |
| `{실행}` | `python3 -m http.server 8791 --bind 127.0.0.1` |
| `{화면}` | `http://127.0.0.1:8791/video.html` |

```bash
cd /home/kit/00_TEST/MAP/kiosk
python3 -m http.server 8791 --bind 127.0.0.1
```

이 창은 켜 둔 채로 둡니다. 끄려면 `Ctrl+C`.

> `--bind 127.0.0.1` 이라 젯슨 밖에서는 안 보입니다. 노트북 브라우저로 보려면 SSH 포트 포워딩을 겁니다 — 노트북에서 `ssh -L 8791:127.0.0.1:8791 kit@<젯슨IP>` 로 접속한 뒤 노트북 브라우저에서 `http://127.0.0.1:8791/video.html` 을 엽니다.

## A-3. 촬영 장면 전환 — 숫자키

브라우저 창이 선택된 상태에서 키보드를 누릅니다. 한 번 클릭해 두면 됩니다.

| 키 | 장면 | 보여 주는 것 |
|---:|---|---|
| `1` | BASE CAMP 시작 | 공학관 뒤편 시작점 |
| `2` | 음성 요청 | 검수 POI 일감호 지정 + 코드 계산 경로 |
| `3` | 목적지 이동 | 마커가 일감호 방향으로 약 58초 이동 |
| `4` | 일감호 도착 | 일감호 북쪽 산책로 도착 화면 |
| `5` | 일조 경고 | 고정 문구·고정 음성으로 BASE CAMP 복귀 안내 |
| `6` | 복귀 이동 | 마커가 공학관 쪽 BASE CAMP로 약 58초 이동 |
| `7` | BASE CAMP 도착 | 복귀 완료 화면 |
| `Space` | 다음 장면 | 1→7 순서로 전환 |
| `B` | 음성 재생 | 목적지 또는 일조 경고 고정 음성을 다시 재생 |
| `R` | 처음 | 장면 1로 초기화 |
| `N` | 야간 모드 | 적색 단색 |
| `H` | 촬영 보조 패널 숨기기 | **촬영 전에 반드시 누릅니다** |

- 오른쪽 보조 패널은 **장치 UI가 아닙니다.** 화면에 남으면 안 되므로 `H` 로 숨기고 찍습니다.
- `3`, `6`은 GPS가 아니라 경로 위를 빠르게 재생하는 **합성 궤적**입니다. 그래서 `DEMO` 배지가 계속 떠 있고, 이 배지는 지우지 않습니다.
- 화면 아래는 `목적지 / 체크포인트 / 베이스캠프 / 야간 모드`입니다. 체크포인트는 현재 위치를 저장하고,
  베이스캠프는 현재 표시 위치에서 복귀 경로를 보여 줍니다.
- 상단 `SEOUL TIME`은 Jetson 시스템 시각을 `Asia/Seoul`로 변환해 자동 갱신합니다. 인터넷은 필요 없지만
  촬영 전에 DS3231에서 시스템 시각이 동기화됐는지 확인합니다.
- 온습도는 오늘 영상에서 `30.0°C / 55% RH` 고정 시나리오 값입니다.

---

# B. 지도 변환 검증 앱 (개발자용)

GraphML/OSM 파일을 넣어 변환이 되는지, GPS 입력이 붙는지 확인하는 앱입니다. **촬영에는 안 씁니다.**

## B-1. 처음 한 번만 — 가상환경 만들기

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/MAP` |
| `{실행}` | 아래 3줄 |

```bash
cd /home/kit/00_TEST/MAP
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

인터넷이 없으면 `wheels` 폴더를 미리 받아 와서 설치합니다.

```bash
cd /home/kit/00_TEST/MAP
. .venv/bin/activate
python -m pip install --no-index --find-links wheels -r requirements.txt
```

## B-2. 실행

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/MAP` |
| `{실행}` | `. .venv/bin/activate` → `python app.py` |
| `{화면}` | `http://127.0.0.1:8790/` |

```bash
cd /home/kit/00_TEST/MAP
. .venv/bin/activate
python app.py
```

이렇게 뜨면 성공입니다.

```
지도 검증 앱: http://127.0.0.1:8790
종료: Ctrl+C
```

**터미널은 켜 둔 채로 둡니다.** 브라우저를 젯슨 화면에 띄우려면 SSH 창을 하나 더 열어서:

```bash
DISPLAY=:0 chromium-browser --kiosk http://127.0.0.1:8790/
```

GPS 하드웨어 없이 NMEA를 반복 재생하려면:

```bash
cd /home/kit/00_TEST/MAP
. .venv/bin/activate
python app.py --gps-mode replay
```

`--gps-mode` 는 `off`(기본) / `replay` / `air530` / `stm32` 중 하나입니다. 포트를 바꾸려면 `--port 8795`.

## B-3. 화면 사용 순서

1. 시작하면 건국대 샘플 GraphML이 자동으로 변환되고 데모 현재 위치·목적지가 표시됩니다.
2. `오프라인 지도 넣기` 로 새 GraphML 또는 OSM XML을 넣습니다.
3. `현재 위치 지정` / `목적지 지정` 을 누르고 지도를 터치하면 경로를 계산합니다.
4. 오른쪽 `LLM READ-ONLY INPUT` 에서 LLM에 넘어가는 제한된 상태만 확인합니다.
5. `GPS 연결` 에서 NMEA 재생 / Air530 직결 / STM32 입력을 고릅니다.

## B-4. 테스트

| | |
|---|---|
| `{위치}` | `/home/kit/00_TEST/MAP` |
| `{실행}` | `python -B -m unittest discover -s tests -v` |

```bash
cd /home/kit/00_TEST/MAP
. .venv/bin/activate
python -B -m unittest discover -s tests -v
```

---

## 포트 정리

| 포트 | 무엇 | 실행 명령 |
|---:|---|---|
| 8790 | 지도 변환 검증 앱 (B) | `python app.py` |
| 8791 | 시연용 화면 정적 서버 (A-2) | `python3 -m http.server 8791` |

같은 포트를 두 번 띄우면 `Address already in use` 가 납니다. 남아 있는 것을 먼저 끕니다.

```bash
pkill -f "app.py"
pkill -f "http.server"
```

---

## 안 될 때

| 증상 | 원인 | 할 일 |
|---|---|---|
| `cannot open display` | SSH에서 Chromium을 그냥 실행 | 앞에 `DISPLAY=:0` 을 붙입니다 |
| `chromium-browser: command not found` | 실행 파일 이름이 다름 | `chromium` 으로 바꿔서 실행 |
| `Address already in use` | 이전 프로세스가 살아 있음 | 위 `pkill` 두 줄 |
| 지도가 가상 언덕처럼 나옴 | `konkuk_map.js` 가 없음 | 0장으로 돌아가 파일 확인 |
| `ModuleNotFoundError: networkx` | 가상환경을 활성화 안 함 | `. .venv/bin/activate` 를 먼저 칩니다 |
| 노드/엣지가 계속 933·2668 | 이전 앱이 아직 떠 있음 | `pkill -f app.py` 후 다시 실행 |
| 화면 오른쪽에 패널이 보임 | 촬영 보조 패널 | `H` 를 눌러 숨깁니다 |

관련 문서: [README.md](README.md) 검증 앱 상세 · [kiosk/README.md](kiosk/README.md) 화면 설계 근거 · [프런트엔드 README](../README.md) UI 물리 스케일과 색 규율
