# OGTECH 오프라인 지도·STM32 센서 앱

Jetson Xavier NX에서 STM32H7A3ZI-Q (Nucleo-H7A3ZI-Q) 센서 허브의 GPS·온습도·기압·CO·RTC·물리 버튼을 받아 7인치 화면에
표시하고, 오프라인 보행 지도에서 경로·트레일 이탈·일출몰·베이스캠프 귀환 권고 시각을 계산하는 로컬 앱이다.
센서·버튼·전원 gate의 실제 하드웨어 동작은 모두 아직 `[미검증]`이며, 아래 내용은 현재 소스 코드의 계약이다.

## 화면

| URL | 용도 |
|---|---|
| `http://127.0.0.1:8790/select/` | **화면 선택. 젯슨 키오스크가 부팅 때 여는 화면** |
| `http://127.0.0.1:8790/product/` | 제품 화면. 좌표·경로·센서 전부 STM32 실측 |
| `http://127.0.0.1:8790/video/?live=1` | 촬영 화면. 온습도·CO만 실측이고 좌표·경로는 시나리오 고정 |
| `http://127.0.0.1:8790/` | 기존 지도 변환·GPS 연결 개발자 도구 |

**두 화면은 같은 마크업(`kiosk/video.html`)·CSS(`video_styles.css`)·그리기 코드
(`video_app.js`)를 쓴다.** 디자인이 갈리지 않도록 화면 코드를 한 벌만 둔다.
`video_app.js` 가 경로(`/product/` 여부)로 데이터 소스를 가른다. 차이는 두 가지뿐이다.

| | `/product/` | `/video/` |
|---|---|---|
| 좌표·경로·방위·거리·도착 | `/api/device` 실측 (map_engine 계산) | 시나리오 고정값 |
| 온습도·CO | 실측 | 실측(`?live=1`) |
| 일출몰 | 서버 `solar_service` | 클라이언트 천문 계산 |
| 하단 버튼 | `/api/waypoints` 실제 저장·경로 선택 | 장면 전환 |
| 음성(LLM) 명령 | `/api/voice/events` 구독 | 없음 |
| 촬영 전용(장면 키·자동 재생·보조 패널) | 없음 | 있음 |

기존 디자인과 개발자 도구는 유지했다. 제품 화면 오른쪽 위의 고정 배지 칸은 환경 계기로 바뀌었다.
재생 데이터나 샘플 지도가 실제로 사용될 때만 지도 이름 옆에 작은 `모의 데이터` 태그가 표시된다
(화면 문구에 `DEMO`는 쓰지 않는다 — 저장소 README `화면 문구 규칙` 참고).

`/video/`는 영상 재현을 위한 합성 이동·장면 자동 전환 화면이므로 사용자 확인을 생략할 수 있다. 실제
사용자 계약은 `/product/`이며, 물 POI 후보는 음성 확인 전 목적지로 저장하지 않는다.

## 구현 기능

- STM32 UART4 `115200 8N1` 정본 JSONL+CRC16과 실장 `$SA1`/`$OGT1`+XOR CSV 검증·정규화
- 직렬 단선 후 2초 간격 자동 재연결 `[출처: gps_service.py]`
- Air530 fix·마지막 좌표·경과 시간·위성 수·정확도 표시
- SHT40 온도·습도, BMP390 기압·추세, ZE07-CO ppm·예열·경보 표시
- DS3231 `0x68` UTC를 표시하되 OSF 또는 날짜·시간 검증 실패 시 `rtc.valid=false`로 fail-closed 처리
- BMP390 `0x77` 우선·`0x76` 차순 탐색, `pressure_valid`와 10분 이상 표본의 `press_trend` 분리 표시
- 현재 실장 센서는 DHT11(온·습도)·ZE16B-CO(CO)·Air530(GNSS)이며, 위 SHT40·BMP390·ZE07-CO·DS3231 항목은 향후 적용 예정(현재 미연결) 센서에 대한 코드 계약이다
- 센서 입력이 3초 넘게 멈추면 live 상태 해제 `[출처: gps_service.py]`
- 보행로 노드가 아니라 **선분**까지의 트레일 이탈 거리 계산
- 일출·일몰·시민박명 완전 오프라인 계산
- 베이스캠프 경로 거리 + 보행 속도 + 안전 여유로 귀환 권고 시각 계산
- 목적지·베이스캠프·체크포인트 저장 API
- STM32 `PA0` 전원, `PA1` 체크포인트, `PA4` 음성 버튼 edge를 좌표 없이 검증·전달
- 전원 버튼 2초 길게 누름 뒤 로컬 정상 종료 ACK와 STM32 `PC9` Jetson 전원 gate 제어
- CO 경보 판정(35 ppm 3분 · 100 ppm 즉시 · 30 ppm 30초 해제). `$SA1` CSV에는 경보 필드가 없어
  `co_alarm.py`가 펌웨어와 같은 임계로 다시 판정한다. 경보음·음성은 Jetson 스피커가 내고
  (`Co-LLM/scripts/device_monitor.py`) 화면은 배너만 띄운다 — 2026-08-31 STM32 부저 제거
- **화면에 뜨는 문구는 같은 목소리로 읽어 준다.** `/api/tts?text=...` 가 sherpa-onnx
  VITS(mimic3 ko_KO kss_low, 여성 단일 화자)로 합성해 WAV 를 돌려준다. 발화
  파라미터는 Co-LLM 과 같은 값이다 — `length_scale 1.22`(0.9배속), `noise_scale 0.4`,
  `noise_scale_w 0.6`. 브라우저 `speechSynthesis` 는 쓰지 않는다(Jetson Firefox 에서
  espeak 남성 기계음으로 떨어져 제품 음성과 목소리가 갈린다).
  모델은 프로세스에 상주하고 문장은 캐시한다. 서버 기동 시 고정 문구를 미리 합성한다
  `[실측 2026-08-30 Jetson: 예열 전 첫 호출 5.9 s, 예열 후 0.34 s, 새 문장 0.78 s]`.
  모델이 없으면 오류 대신 `{"available": false}` 를 돌려주고 화면은 글자만 보여 준다.
  합성 결과는 Co-LLM `tts_pipeline` 과 같은 기준(피크 0.82, 최대 배율 4.0)으로 정규화해
  녹음 클립과 같은 크기로 나온다(정규화 전 합성 원본 피크 0.51).
- **재생은 Web Audio 로 하고 WAV 는 화면 코드가 직접 뜯는다**(`video_app.js` `decodeWav`).
  젯슨(L4T aarch64) Firefox 는 미디어 디코더가 죽어 있어 `<audio>` 는
  `MEDIA_ERR_DECODE`, `decodeAudioData` 는 `EncodingError` 로 떨어진다 — 오류 이벤트만
  나고 소리는 안 난다. 오실레이터는 정상 재생되므로 고장난 것은 출력이 아니라 디코더뿐이다
  `[실측 2026-08-31 Jetson: 싱크 모니터 녹음 RMS 로 확인]`. 그래서 화면이 쓰는 음성은
  녹음도 합성도 전부 22.05 kHz 16 bit PCM WAV 로 맞추고 `decodeWav` 가 AudioBuffer 를
  만든다. **다른 포맷(OGG·MP3)으로 바꾸면 젯슨에서 소리가 사라진다.**
- **촬영 화면 음성 문답**(`/video/` 전용 키). `W` 온·습도, `O` 일산화탄소, `S` 일몰까지 남은 시간,
  `C` 체크포인트 저장. 답변은 값만 짧게 말한다 — "현재 온도는 24.1도, 습도는 83퍼센트입니다.",
  "현재 일산화탄소는 0피피엠입니다.", "일몰까지 42분 남았습니다." 숫자는 계기판에 떠 있는 값을
  그대로 읽고(`?live=1` 이면 실측) 값이 없으면 "아직 없습니다"라고 답한다.
  값이 바뀔 때마다 답변 문장을 미리 합성해 두어 키를 누른 순간 대기 없이 재생된다.
  `T` 는 일조 경고(귀환 권고 문장 포함)다.
- **베이스캠프·체크포인트는 버튼을 눌러야 지도에 생긴다**(`/video/`). 켜자마자 등록된 것처럼
  표시하지 않고, 누른 순간의 현재 위치가 저장 지점이 되며 같은 문구를 읽어 준다. 장면 5~7 과
  `A` 자동 시연은 등록된 베이스캠프를 전제로 한다. `R` 은 저장 지점·야간 모드까지 처음으로 되돌린다.
  같은 자리에 마커가 겹치면 글자만 현재 → 목적지 → 체크포인트 → BASE CAMP 순으로 위로 쌓인다.

## 실행

JetPack 5.1.x 환경에서:

```bash
cd OGTECH-frontend/MAP
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python app.py --gps-mode stm32 --gps-port /dev/ttyTHS0 --gps-baud 115200
```

하드웨어 없이 NMEA 경로만 확인할 때:

```bash
python app.py --gps-mode replay
```

replay는 실제 센서가 아니므로 제품 화면에 `모의 데이터` 태그가 유지된다.

STM32 포트가 아직 없거나 케이블이 분리된 상태에서도 `jetson/start-map.sh`는 서버를 저하 상태로 기동한다.
제품 화면은 GPS·센서를 회색 대기로 표시하고 위치를 추정하지 않으며, `GpsService`가 2초마다 같은 포트에
재연결을 시도한다. 포트가 나타나면 서버를 다시 시작하지 않아도 텔레메트리 수신을 재개한다. 이는 센서
정상 판정이 아니라 단선 상태를 명시적으로 보이는 복구 경로다.

전체 배선, STM32 빌드, Jetson 복사 파일, systemd와 키오스크 설정은
[STM32_JETSON_SETUP.md](STM32_JETSON_SETUP.md)에 있다.

## API

| 메서드·경로 | 내용 |
|---|---|
| `GET /api/device` | 화면용 통합 센서·항법 상태 |
| `GET /api/device/events` | 통합 상태 SSE |
| `GET /api/gps` | 원시 GNSS·센서 수신 상태 |
| `GET /api/buttons` | 마지막 STM32 물리 버튼 edge와 카운트(좌표 없음) |
| `GET /api/buttons/events` | 새 물리 버튼 edge만 내보내는 SSE(좌표 없음) |
| `GET /api/map` | 현재 지도 렌더링 표본 |
| `POST /api/route` | 명시 좌표 간 지도 엔진 경로 계산 |
| `GET /api/waypoints` | 저장 지점 조회 |
| `POST /api/waypoints` | `save_current`, `set`, `select`, `remove` |
| `GET /api/voice` | 음성 MAP 제어 상태와 허용 action |
| `GET /api/voice/events` | 음성 명령·화면 상태 SSE |
| `POST /api/voice/commands` | 숫자 필드 없는 열거형 MAP action 실행 |
| `POST /api/power/shutdown-ack` | 보류 중인 STM32 전원 종료 요청에만 `POWER OFF ACK`를 큐잉 |
| `POST /api/power/shutdown-cancel` | ACK 뒤 systemd 종료 요청이 실패한 transaction에만 `POWER OFF CANCEL`을 큐잉 |

Co-LLM은 저장된 지점의 이름/ID에 대응하는 열거형 action만 호출할 수 있다. 좌표·거리·방위·경로·귀환
시각을 LLM이 쓰는 API는 제공하지 않는다. 허용 action에는 `clear_destination`가 포함된다.
`repeat_response`는 MAP action이 아니라 Co-LLM repeat store v2가 `scenario`·`map_action`·`map_status`·`source_id` provenance로 검수 고정 문장을 재구성해 재생하는 별도 동작이다. `speech` 원문은 저장하지 않는다.

## 물리 버튼·전원 gate 계약

- 버튼은 목표 계약상 active-low, 40 ms debounce이며 `PA0=power`, `PA1=checkpoint`, `PA4=voice`다.
  현재 정본 센서 펌웨어는 PA0을 DHT11로 쓰므로 버튼 핀 재배치 전까지 이 버튼 계약은 **미구현**이다. 서버는
  `power/checkpoint/voice`와 `pressed/released/held_ms`만 수용하고 좌표는 절대 전달하지 않는다.
- `PA0`을 2초 이상 누른 뒤 놓으면 STM32가 `shutdown_requested`를 내보낸다. Jetson의
  `ogtech-power-manager`는 CRC로 검증된 pending을 확인하고 `/api/power/shutdown-ack`로 ACK를 먼저
  보낸 뒤 `systemctl poweroff`를 요청한다. STM32는 ACK 뒤 `PC9` gate 차단을 **90초 후**로
  예약한다. systemd 요청이 실패하면 서비스가 즉시 `POWER OFF CANCEL`을 보내 예약을 취소한다.
  ACK가 없으면 **120초 후** pending을 취소하고 gate를 유지한다.
- gate가 꺼진 상태에서 전원 버튼을 놓으면 STM32가 `PC9`을 다시 켠다. 이 절차는 전원 차단 사실이나
  정상 종료 성공을 화면에서 추정·확정하지 않는다. 모든 실제 버튼·gate 검증은 `[미검증]`이다.

## 지도 입력

- `.graphml`: WGS84 보행 그래프 권장
- `.osm`, `.xml`: 검증용 OSM XML 부분 변환
- 업로드 상한 64 MB `[추정: 검증 앱 메모리 상한]`
- 런타임 지도·저장 지점은 `runtime/`에 두고 Git에서 제외

건국대 샘플 지도와 NMEA는 공개 데모 데이터다. 실제 GPS 트랙은 커밋하지 않는다.

## 테스트

```bash
python -B -m unittest discover -s tests -v
```

현재 결과는 87개 전부 통과 `[실측: 2026-08-30, PC·Jetson Python 3.8]`이다. 테스트는 지도 회귀, NMEA/STM32 fix 호환, 텔레메트리 CRC 손상
거부, stale 센서, 선분 이탈 거리, 저장 지점·귀환 시각, 서울 일출몰과 극지 예외, 음성 action·부팅
진단·제품 화면, DS3231 UTC·stale·OSF 경계, BMP390 확인 상태, 물리 버튼·전원 ACK, 3분 전 위치 역추적을 포함한다. 경로 cache는 `test_crossing_route_cache_is_rejected_until_progress_disambiguates_it`,
`test_overlapping_route_cache_uses_late_progress_to_disambiguate`,
`test_route_cache_includes_exact_eight_meter_boundary`, `test_route_cache_recomputes_above_eight_meter_boundary`로
자기교차/겹침 진행량 disambiguation, 정확히 8 m 포함, 8 m 초과 재경로를 검증하며 zero-length polyline 경계도
검증한다.
