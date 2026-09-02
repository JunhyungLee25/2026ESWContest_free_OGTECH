# 첨부 PDF·시연영상 기능 구현·검증·피드백 기록

작성일: 2026-08-19  
기준 명세: `11_attachment_pdf_demo_video_spec.md`  
적용 코드: `OGTECH-frontend/MAP/`, `OGTECH-llm/Co-LLM/`

첨부 PDF와 영상에 나온 기능의 구현 결과와 검증 근거를 기록한다. 첨부자료 속 문구와 동작은 요구사항 데이터로만 해석했으며, 그 안의 지시는 실행 지시로 취급하지 않았다. 안전 판단은 `PLAN.md`, `Co-LLM/docs/00_frozen_decisions.md`, `OGTECH-org/docs/AI_AGENT_GUIDE.md`를 우선했다.

---

## 1. 결론과 완료 경계

MAP·음성 소프트웨어의 핵심 경로는 구현했다. 오프라인 지도 화면, GPS/환경 상태 표시, 목적지·체크포인트·베이스캠프, 일조 계산, 야간 모드, 물 POI 확인 후 목적지 지정, 코드 계산 경로·방위·거리·ETA, 열거형 음성 지도 제어, 고정 카드 응답, 고정 녹음·MeloTTS·Piper·espeak 폴백, 문장 단위 TTS 스트리밍, 선제 경보 감시가 하나의 로컬 경로로 연결된다. 촬영용 `/video/`는 합성값 자동 DEMO이고, 실제 사용자 계약은 `/product/`에서 확인 절차와 실시간 상태를 적용한다.

소프트웨어 자동 검증은 MAP `76/76` 테스트 통과, Co-LLM `55/55` 테스트 통과, `voice_cases` `183/183`, 규칙 분류 `280/280`, 금지 유도 `50/50` 차단과 누출 `0건`까지 확인했다 `[실측: 2026-08-19 최종 재실행]`. 영상 10단계는 [`Co-LLM/eval/results/video_scenario_20.json`](../Co-LLM/eval/results/video_scenario_20.json)에서 `20/20`, `offline_guard=true`, 최악 `258.69 ms`, 고정 도착 TTS `20/20`, `configured_engine_failure_fallback_runs`에 해당하는 **하네스에 구성된 테스트 엔진 전부 실패 고정 fallback `20/20`**이다 `[실측: 2026-08-19 최종 재실행]`. 이는 실제 MeloTTS/Piper/espeak 로드·청취 실증이 아니다 `[미검증]`. 제품 화면은 `MAP/test-results/product_ui_1024x600.json` (로컬 산출물, 저장소에 커밋하지 않음)과 같은 조건의 1024×600 브라우저 재검증에서 통과했다 `[실측: 2026-08-19 최종 재실행]`.

아래 항목은 이 PC의 소프트웨어 검증으로 완료라고 할 수 없다.

- 실제 Jetson Xavier NX에서 MeloTTS/Piper의 한국어 청취 품질, 첫 소리 지연, 메모리 피크 `[미검증]`
- 실제 Adafruit 또는 I2S 마이크의 `21문장` 연속 STT와 거짓 양성 `0건` 조건 `[미검증]`
- STM32가 Jetson 전원 OFF 상태에서 CO·GNSS를 계속 감시하는지 `[미검증]` (2026-08-31 부저 제거 — 그 구간에는 판정만 유지되고 소리는 없다)
- STM32 펌웨어가 BMP390 `0x76/0x77`의 `pressure_valid`와 최소 10분 표본 추세를 실제 프레임으로 송신하는 기능은 구현됐지만, 센서 실물·배선·압력 변화까지의 인수 `[미검증]`
- 물리 음성 버튼·체크포인트 버튼·전원 버튼의 GPIO 연동 `[미검증]`
- 실제 Jetson·STM32·센서·오디오·실외 환경에서의 `20회` 연속 시연 `[미검증]`

현재 상태는 **소프트웨어 기능 구현·자동 시나리오·제품 화면 검증 완료, 실제 하드웨어 인수시험 대기**다. 동적 TTS 엔진의 부팅 가능 여부는 진단하지 않으며, 자가진단은 검수된 고정 WAV 파일만 검사한다. 자동 수치는 2026-08-19 최종 재실행 결과이며, 실물 관측이 없는 항목을 완료로 승격하지 않는다.

---

## 2. 기능 추적표

| 첨부자료 기능 | 구현 위치 | 입력 | 구현 동작 | 출력 | 현재 판정 |
|---|---|---|---|---|---|
| 오프라인 지도 | `MAP/map_engine.py`, `MAP/app.py` | 로컬 GraphML/OSM | 보행 그래프 로드, 경로 탐색, 정적 파일 제공 | 네트워크 없는 지도·경로 | 소프트웨어 완료 |
| 현재/마지막 위치 | `MAP/gps_service.py`, `MAP/kiosk/live_app.js` | Air530/STM32/replay | fix와 마지막 확정 좌표를 분리 | 좌표·정확도·위성·AGE·KST | 소프트웨어 완료, 실 GPS 대기 |
| 3분 전 위치 역추적 | `MAP/position_history.py`, `MAP/navigation_service.py`, `Co-LLM/scripts/product_assistant.py` | 같은 부팅의 monotonic JSONL | `180±25초` 확정 fix만 `route_recent_trace`로 선택 | MAP 계산 경로·SSE·고정 TTS | 소프트웨어 완료, 실 GPS 대기 |
| 목적지 | `MAP/navigation_service.py`, `live_app.js` | 터치 또는 저장된 POI 선택 | `/video/`는 자동 DEMO 장면, `/product/` 음성은 사용자 확인 뒤에만 목적지 저장 | 마커·경로·판독 카드 | 소프트웨어 완료 |
| 체크포인트 | `navigation_service.py` | 현재 확정 GPS | fix가 있을 때만 저장 | 체크포인트 목록 | 소프트웨어 완료, 물리 버튼 대기 |
| 물리 입력·전원 gate | STM32 PA0/PA1/PA4, PC9, MAP SSE | GPIO edge·정상 종료 ACK | push-to-talk·체크포인트·전원 handshake, trail watchdog | CRC ACK·5초 watchdog·gate 상태 | 소프트웨어 계약 완료, 실 보드 대기 |
| 베이스캠프 | `navigation_service.py` | 현재 확정 GPS 또는 저장 지점 | 등록·선택·귀환 경로 계산 | 경로·일조 귀환 권고 | 소프트웨어 완료 |
| 물 위치 탐색 | `poi_catalog.json`, `navigation_service.py` | `find_nearest_water` | 가장 가까운 오프라인 수원 표식 제안, 확인 전에는 미지정 | 수질 미확인 고지·확인 질문 | 데모 카탈로그 완료, 운영 POI 대기 |
| 방위·거리·ETA | `map_engine.py`, `navigation_service.py` | GPS와 저장 목적지 | A* 경로와 다음 경로점 기준 방위·거리·ETA 계산 | `코드 계산` 배지와 수치 | 소프트웨어 완료 |
| 도착 감지 | `navigation_service.py` | 거리와 GPS 정확도 | 정확도 없는 fix에서는 도착 확정 금지 | 목적지/베이스캠프 도착 상태 | 소프트웨어 완료 |
| 일조 시간 | `solar_service.py`, `navigation_service.py` | 확정 좌표·날짜·KST | 일출·일몰·시민박명·남은 시간 로컬 계산 | 남은 일조·귀환 권고 시각 | 소프트웨어 완료 |
| 야간 모드 | `live_app.js`, `styles.css` | 터치·키보드·음성 enum | 서버 상태와 적색 단색 테마 동기화 | 암순응용 적색 UI | 소프트웨어 완료 |
| 온습도·기압·CO | `gps_service.py`, `navigation_service.py` | STM32 JSON telemetry | CRC·범위·노후화·상태 검증, BMP390 `0x76/0x77`, `pressure_valid`, 10분 추세 | 온도·RH·압력 추세·CO | 펌웨어·인터페이스 구현, 실 센서 대기 |
| DS3231 RTC | `gps_service.py`, `navigation_service.py`, `MAP/app.py` | STM32 UTC telemetry | OSF·UTC·날짜 검증 실패 시 fail-closed, 수동 `SET RTC` 지원 | 확정 시각·일조 기준 | 소프트웨어 완료, 실 RTC 대기 |
| 음성 질의 | `product_voice.py` | 마이크 또는 `--text` | STT 언로드 후 규칙/분류·카드·TTS 순차 실행 | 스피커 응답 | 소프트웨어 완료, 실 음질 대기 |
| 음성 MAP 제어 | `keyword_rules.yaml`, `product_assistant.py` | 자연어 | `clear_destination`를 포함한 열거형 action만 MAP API로 전송 | 화면 SSE·VOICE 상태·TTS | 소프트웨어 완료 |
| 생명 관련 응답 | `ogtech_core.py`, `survival_cards.json` | 안전 키워드 | LLM 우회, 검수 카드 직행 | 고정 절차 문장 | 소프트웨어 완료 |
| 일반 질문 분류 | `engines.py`, `ogtech_core.py` | 규칙이 놓친 저위험 발화 | Qwen2.5가 JSON Schema의 라벨 하나만 생성 | 선택된 고정 카드 | 하네스 완료, 실제 모델 평가 대기 |
| 선제 경보 | `device_monitor.py` | MAP 장치 SSE | 트레일·일조·도착은 전이마다 1회, CO는 경보음(비프)+음성을 지속 반복(ALARM 20초·WARN 60초) | 생성 경보음 + 고정 경보 TTS | 젯슨 실기 재생 확인(2026-08-31), 실 CO 센서 경보 대기 |
| 깨끗한 TTS | `tts_pipeline.py`, `fixed_audio.json` | 확정된 고정 문장 | 고정 WAV 우선, 이후 MeloTTS→Piper→espeak, 품질 게이트·정규화·캐시·하네스에 구성된 테스트 엔진 전부 실패 고정 fallback | 문장별 WAV | 배관·고정 WAV 완료, 실제 Jetson 청취 대기 |

---

## 3. 계획 → 수행 → 검증 → 피드백 반복 기록

### 반복 1: 첨부자료를 먼저 고정 명세로 변환

- 계획: 구현 전에 PDF 전체와 영상 전체에서 기능, 입력, 상태 전이, 화면·음성을 추출한다.
- 수행: PDF `19쪽` `[실측]`을 전부 렌더링하고, 영상 `234.292초` `[실측]`를 프레임·오디오·촬영 문서로 교차 분석했다. 결과는 `11_attachment_pdf_demo_video_spec.md`에 기록했다.
- 검증: 원본 SHA-256, PDF 쪽별 표, 영상 시간축, 상태 전이, 안전 충돌 표를 남겼다.
- 피드백: 영상의 “돌아가세요” 직접 지시는 사용자 결정을 대신하므로, 구현 문구를 “귀환 권고 시각과 베이스캠프 경로를 확인하세요”로 바꿨다.

### 반복 2: MAP 상태 모델과 음성 명령 경계

- 계획: 음성이 MAP을 조작하되 LLM이나 STT 문자열이 좌표·방위·거리를 넣을 수 없게 한다.
- 수행: `/api/voice`, `/api/voice/events`, `/api/voice/commands`를 만들고 허용 action을 enum으로 제한했다. 요청은 `action`과 선택적 `request_id` 외 필드를 거부한다.
- 검증: 좌표를 넣은 음성 payload가 HTTP `422`로 거부됨 `[실측]`; 전체 action의 결정적 상태 전이 테스트 통과 `[실측]`.
- 피드백: “가까운 물”은 즉시 목적지가 아니라 후보 제안 상태로 두고, 긍정 확인 뒤에만 목적지로 저장하도록 변경했다.

### 반복 3: 실제 제품 화면

- 계획: 영상의 상태 대시보드를 최신 안전 계약과 `1024×600` `[출처: 고정 화면]` 규격에 맞춘다.
- 수행: 상단에 위치·일조·배터리·트레일·환경을 배치하고, 지도에 경로·마커·방위·거리·ETA를 표시했다. 하단은 `96px` `[실측: CSS]` 높이의 목적지·체크포인트·베이스캠프·야간 모드로 구성했다. 부팅 시 구조 요청 수단이 아니라는 고지를 `5초` `[실측: UI 타이머]` 동안 건너뛸 수 없게 했다.
- 검증: 인앱 Chromium에서 `1024×600` `[실측]`로 부팅 고지, 주간·야간, 경로 카드, 현재/마지막 좌표를 캡처했다. 문서 전체 `scrollWidth=clientWidth=1024`, `scrollHeight=clientHeight=600` `[실측]`이었다.
- 피드백: 영상에는 날짜·시각·좌표가 있었지만 제품 화면에는 정확도 요약만 있었다. KST 초단위 시각과 좌표·정확도·위성 수를 추가했다. GPS 미수신 때는 마지막 확정 좌표와 AGE만 표시했다.

### 반복 4: 음성 표현 범위와 안전 우선순위

- 계획: 긴 런타임 프롬프트 대신 명시적인 규칙·평가 세트로 다양한 표현을 처리한다.
- 수행: refuse 최우선, 확인/취소, MAP action, 생명 경로 B, 저위험 경로 A 순으로 라우팅했다. `14개` 라벨 `[출처: 고정 LLM 계약]` 전체의 카드를 만들었다.
- 검증: `voice_cases` 표현 변형 `183/183`이 전부 기대 라벨·action과 일치하고, 분류 세트 `280/280`과 금지 공격 `50/50`이 통과했다 `[실측: 2026-08-19 최종 재실행]`.
- 피드백: 최초 종단 점검에서 “가장 가까운 물 있는 곳 찾아줘”가 `unknown`으로 떨어졌다. 위치·탐색 표현이 함께 있을 때만 수원 탐색이 되도록 규칙을 보강하고, “근처 계곡물 마셔도 돼”가 목적지로 바뀌지 않는 대조 테스트를 추가했다.

### 반복 5: LLM의 발화 권한 제거

- 계획: 사용자가 듣는 절차 문장을 LLM이 자유 생성하지 못하게 한다.
- 수행: LLM은 저위험 미분류 발화의 라벨 하나만 JSON Schema로 생성한다. 생명 라벨을 LLM이 반환하면 승격하지 않고 `unknown` 고정 카드로 막는다. 최종 TTS 문장은 고정 카드 또는 MAP/센서 코드값 템플릿에서만 나온다.
- 검증: LLM 분류 출력이 그대로 음성으로 나가지 않는 테스트와 refuse에서 분류기가 호출되지 않는 테스트 통과 `[실측]`.
- 피드백: 기존 `voice_loop.py`의 자유 생성 경로를 제거했다. 프롬프트 사례를 무한히 늘리는 대신, 고정 규칙·평가 문장을 늘려 지연·KV 캐시·프롬프트 누출 위험을 피했다.

### 반복 6: TTS 품질과 첫 소리 지연

- 계획: 영상에서 이미 깨끗하게 녹음된 문장은 재사용하고, 동적 문장은 자연스러운 한국어 엔진을 우선하며 첫 문장부터 재생한다.
- 수행: 목적지 확인·목적지 도착·베이스캠프 도착을 고정 WAV로 연결했다. 목적지 확인은 질문과 침묵이 섞인 합본에서 답변 구간만 분리했다. 동적 문장은 약어·단위·시각을 한국어 발음으로 정규화하고, MeloTTS→Piper→espeak 순으로 각각 한 번만 시도한다. 첫 문장 WAV가 준비되면 즉시 재생하고 재생 중 다음 문장을 합성한다.
- 검증: 문장 분리에서 소수점 `4.2` `[실측: 테스트 입력]`가 잘리지 않음, 엔진 폴백·캐시·정규화·고정 WAV 우선 테스트 통과 `[실측]`.
- 피드백: 확인용 합본 WAV를 그대로 쓰면 질문과 침묵까지 재생되는 결함이 있어 `destination_confirmed.wav`로 교체했다. 실제 MeloTTS/Piper 청취 품질은 Jetson에서 닫아야 한다 `[미검증]`.

### 반복 7: 장치가 먼저 말하는 경로

- 계획: 사용자가 묻지 않아도 CO·트레일·일조·도착의 새 전이를 먼저 알린다.
- 수행: MAP SSE를 읽는 `device_monitor.py`와 실행 래퍼를 추가했다. 동일 상태는 한 번만 말하고 해제 후 재발할 때 다시 말한다. 동시 발생 시 CO를 먼저 처리한다.
- 검증: 동일 상태 중복 억제, 해제 후 재무장, CO 우선순위, 목적지/베이스캠프 도착 문구 구분 테스트 통과 `[실측]`.
- 피드백: CO 경보 문장에 “STM32 물리 경보가 작동 중”을 포함하되, 실제 하드웨어 경보가 검증되기 전에는 DEMO·실기 상태를 혼동하지 않도록 센서 상태를 그대로 따른다.

### 반복 8: 종단 통합에서 발견한 말투 결함

- 계획: 실제 MAP replay 서버와 Co-LLM을 연결해 물 탐색→확인→경로 상태를 순서대로 실행한다.
- 수행: 자연어 요청이 `find_nearest_water`, `confirm_destination`, `status` enum으로 바뀌어 로컬 API에 전달되는지 확인했다.
- 검증: 후보 수원 안내에 “수질은 확인되지 않았습니다”가 포함되고, 확인 뒤 목적지·경로가 생성되며 방위·거리는 MAP 코드값만 읽음 `[실측]`.
- 피드백: GPS 정확도가 없을 때 “플러스마이너스 확인 불가미터”라고 붙는 문장을 발견했다. 단위 없는 “GPS 정확도는 확인할 수 없습니다”로 바꾸고 회귀 테스트를 추가했다.

### 반복 9: 중복 검증 서버와 ETA 오판 제거

- 계획: 브라우저에서 음성 SSE, `VOICE · OK`, 야간 모드, 경로 ETA를 함께 확인한다.
- 수행: 포트 `8792` `[실측]`에서 오래된 서버와 최신 서버가 동시에 수신 중인 것을 `netstat`로 확인했다. 두 검증 프로세스를 정확히 종료하고 단일 최신 서버만 다시 실행했다.
- 검증: 단일 서버에서 경로 약 `934m`, ETA `16분` `[실측: replay 시점]`이 MAP 엔진 계산값으로 화면에 표시됨; 음성 `night_on` 뒤 `VOICE · OK`와 적색 모드가 SSE로 반영됨 `[실측]`.
- 피드백: 중복 서버 응답에는 `eta_min`이 없어 UI가 `0분`으로 보였다. 코드 결함으로 오인하지 않도록 검증 환경도 단일 리스너인지 확인하는 절차를 아래 하드웨어 체크리스트에 추가했다.

### 반복 10: 영상 10단계 종단 시나리오와 제품 화면 인수

- 계획: 베이스캠프 저장부터 수원 후보·사용자 확인·경로 이동·도착·일조 경고·베이스캠프 도착·음성 야간 모드까지 영상의 10단계를 하나의 로컬 상태 전이로 묶고 20회 반복한다.
- 수행: 매 회 임시 waypoint 저장소와 검증 GPS fix를 사용해 MAP 엔진을 재초기화하고, Co-LLM의 자연어 라우팅·MAP enum·고정 TTS·실패 fallback을 함께 실행했다. 이동 단계는 경로 진행값을 바꾸어 거리·ETA 감소를 확인했고, 일조는 위험 시각을 주입했다.
- 검증: [`eval/results/video_scenario_20.json`](../Co-LLM/eval/results/video_scenario_20.json)에서 10단계 `20/20`, `offline_guard=true`, 최악 `258.69 ms`, 고정 도착 TTS `20/20`, `configured_engine_failure_fallback_runs`인 하네스에 구성된 테스트 엔진 전부 실패 고정 fallback `20/20`을 확인했다 `[실측: 2026-08-19 최종 재실행]`. 보충 실패 경로(LLM 종료, GPS no-fix, 하네스 구성 테스트 TTS 엔진 전부 실패)도 각 회에 포함했다. 이 fallback은 실제 MeloTTS/Piper/espeak 로드·청취 실증이 아니다 `[미검증]`.
- 수행: 브라우저는 `MAP/test-results/product_ui_1024x600.json` (로컬 산출물, 저장소에 커밋하지 않음)으로 부팅 ACK 초기 잠금, N키·배경 클릭 차단, 포커스 트랩, DEMO/대기/통과 구분, 진단 상세 6개, 96px 버튼, 1024×600 문서 크기, 야간 음성 제어를 확인했다.
- 피드백: 촬영용 `/video/`는 자동 DEMO이므로 사용자 확인을 생략할 수 있지만, `/product/` 실제 계약은 수원 후보를 확인하기 전 목적지를 저장하지 않는다. 두 화면의 판정을 문서와 증거 파일에서 분리했다.

### 반복 11: 일조 초과 표현과 경로 진행 캐시

- 계획: 영상의 `일몰 107분 초과`를 제품 화면에서도 음수 숫자나 `0분`으로 뭉개지 않고 명확히 표시하고, 이동 중 경로 cache가 오래된 거리·ETA를 재사용하지 않는지 검증한다.
- 수행: 일조 상태를 정상·주의·위험으로 분리하고, 일몰 이후에는 초과분을 별도 문장으로 기록했다. 경로 캐시는 정확히 8 m를 포함하고 8 m 초과에서 폐기·재경로한다. 자기교차·겹침 polyline의 cross-track 동률은 진행량 차이가 크면 cache 폐기 후 `map_engine` 재경로로 처리하고, 차이가 작으면 이전 progress를 기준으로 후반 구간을 disambiguate한다. zero-length polyline은 진행량 없음으로 안전 처리한다.
- 검증: `test_cached_route_projects_small_progress_into_distance_and_eta`, `test_crossing_route_cache_is_rejected_until_progress_disambiguates_it`, `test_overlapping_route_cache_uses_late_progress_to_disambiguate`, `test_route_cache_includes_exact_eight_meter_boundary`, `test_route_cache_recomputes_above_eight_meter_boundary`로 작은 진행·자기교차·일직선 겹침·정확히 8 m 포함·8 m 초과 재경로를 확인했다. 자기교차 테스트의 zero-length polyline assertion도 같은 회귀 근거에 포함된다. `video_scenario_20.json`의 `08_daylight_return_route` 및 `06_moving_update`에는 실제 이동 단계의 시간·거리·ETA를 저장했으며, 초기 거리보다 이동 후 거리가 감소하고 일조 위험 상태가 `danger`로 전이되는 것을 20회 확인했다 `[실측: 2026-08-19 최종 재실행]`.
- 피드백: 음수 `remaining_min`을 그대로 TTS하지 않고 `N분 초과`로 읽도록 문서 계약을 고정했다. 실제 Jetson 스피커 청취 명료도는 여전히 `[미검증]`이다.

### 반복 12: repeat store v2와 MAP 음성 경계

- 계획: 반복 재생 저장소에 음성 원문이나 자유 문장을 보존하지 않고 검수 provenance만 남긴다. MAP 응답의 자유 문자열이 TTS로 승격되는 경로도 차단한다.
- 수행: repeat store v2를 `scenario`·`map_action`·`map_status`·`source_id`만 저장하도록 고정하고, provenance에서 검수 고정 문장을 재구성하도록 연결했다. SAFE prefix만 붙인 위조, `speech` 추가 필드, 비계약 MAP action, 실제로 불가능한 action·status 조합, 악성 MAP `message`는 각각 계약 검증에서 거부하며, MAP `message`는 TTS 입력으로 사용하지 않고 `action`+`status` 고정 문구만 사용한다.
- 검증: `test_repeat_uses_only_previous_verified_safe_response`, `test_forged_safe_prefix_store_is_not_replayed`, `test_store_with_extra_speech_field_is_rejected_even_with_valid_provenance`, `test_store_rejects_impossible_map_action_status_pair`, `test_repeat_preserves_synthetic_map_error_provenance`, `test_mismatched_map_contract_uses_fixed_contract_notice`, `test_map_message_is_never_promoted_to_safe_speech`의 회귀 근거로 확인했다 `[실측]`.
- 피드백: `repeat_response`를 MAP enum 목록에서 계속 분리하고, 실제 Jetson TTS 엔진 로드·청취와 하드웨어 음성 경로는 별도 인수 전까지 `[미검증]`으로 유지한다.

### 반복 13: 실제 하드웨어 인수 경계와 fail-closed 증거

- 계획: 자동 replay 성공을 실물 성공으로 오인하지 않도록 Jetson에서 수집한 버튼 해제·첫 소리 loopback·STT·메모리·swap·동시성·네트워크 관측을 20회 단위로 원자 저장하고, 하나라도 빠지면 `pass=false`로 남긴다.
- 수행: `Co-LLM/eval/run_hardware_acceptance.py`가 operator 또는 장치 로거의 JSONL 이벤트를 받아 같은 부팅의 monotonic timestamp, GPIO 버튼 해제 원천, loopback onset, STT 21문장 결과, `MemAvailable`, swap, STT/TTS 구간, 외부 연결 수를 검증한다. 실물 관측 출처와 `simulated=false`가 없으면 통과시키지 않으며, 부분 결과도 원자적으로 저장한다.
- 검증: 이 하네스의 fixture는 형식·실패 판정 단위 테스트에만 사용한다. fixture나 replay를 실제 하드웨어 증거로 제출하지 않으며, 실제 Jetson·STM32·센서·오디오·실외 20회는 아직 `[미검증]`이다.
- 피드백: 하드웨어 인수 전까지 MAP `76/76`, Co-LLM `55/55`, `voice_cases` `183/183`, rules `280/280`, refuse `50/50`과 video JSON `20/20`은 소프트웨어 기준선으로만 표시한다. 실제 청취·GPIO·CO·GNSS·BMP390·전원 gate 결과가 추가되기 전에는 완료 문구를 사용하지 않는다.

---

## 4. 구현 계약 상세

### 4.1 MAP 음성 action

허용된 MAP action은 다음과 같다.

```text
save_basecamp
save_checkpoint
route_basecamp
route_destination
route_last_checkpoint
route_recent_trace
find_nearest_water
confirm_destination
reject_destination
clear_destination
night_on
night_off
night_toggle
status
cancel
```

`repeat_response`는 MAP action이 아니다. Co-LLM의 repeat store v2가 `scenario`·`map_action`·`map_status`·`source_id` provenance만 저장하고, 그 provenance로 검수된 고정 문장을 재구성해 재생하는 별도 음성 동작이며, 지도 상태를 바꾸지 않는다. `speech` 원문은 저장하지 않는다.

음성 요청에는 좌표·거리·방위 숫자를 넣을 자리가 없다. 터치 목적지 좌표는 사람이 지도 캔버스를 눌렀을 때의 별도 `/api/waypoints` 경로만 사용한다. 음성 경로가 수원 표식을 선택할 때도 이미 검수된 로컬 POI의 ID만 선택한다.

`route_recent_trace`는 같은 부팅 세션의 monotonic 시각을 가진 JSONL 위치 로그에서 약 3분 전의 **확정 fix**를 찾는 코드 action이다. 허용 시간창은 `180±25초`이며, 다른 부팅의 로그·fix가 아닌 기록·시간창 밖 기록은 경로 목적지로 승격하지 않는다. 음성 계약에는 action만 전달하고 좌표·거리·방위는 MAP 결과에서만 계산·표시한다. 대표 발화는 “3분 전 확정 위치로 안내해 줘”, “3분 전 지점으로 돌아가는 길 보여 줘”이며, 이 동작의 회귀 근거는 `MAP/tests/test_navigation_service.py`와 `Co-LLM/eval/voice_cases.json`이다.

STM32 트레일 출력은 `trail_alert`/`trail_caution` 고정 enum으로만 요청한다. STM32가 진동 출력을 수행하고 CRC가 검증된 ACK와 5초 watchdog 상태를 반환해야 확인으로 표시한다. 물리 입력은 active-low `PA0=power`, `PA1=checkpoint`, `PA4=voice`이며, 검증된 edge 이벤트만 MAP SSE와 push-to-talk 경로로 전달한다. 전원 종료는 `PC9` Jetson gate의 CRC pending 확인→로컬 ACK→systemd 종료 요청 순서를 강제하며, systemd 요청 실패 시 `POWER OFF CANCEL`로 90초 차단 예약을 되돌린다. 이 GPIO·ACK·watchdog·게이트 동작은 소프트웨어 계약과 파서까지 구현했지만 실제 STM32 보드 관측은 `[미검증]`이다.

DS3231은 `0x68` UTC 입력을 사용하고 OSF, 날짜·시간 형식, UTC 여부가 하나라도 맞지 않으면 `valid=false`로 fail-closed 처리한다. 유효한 RTC가 없을 때 시스템 시각으로 조용히 확정하지 않으며, 유지보수자는 로컬 `SET RTC` 절차로 수동 시각을 설정한 뒤 다음 telemetry에서 다시 검증한다. BMP390은 `0x77` 우선·`0x76` 차순으로 탐색하고, `pressure_valid`와 최소 10분 표본에 근거한 `press_trend`를 분리한다. 3회 연속 실패나 재초기화 시 이전 연결 세션의 값·추세 이력을 폐기해 회복 직후 옛 표본으로 방향을 확정하지 않는다. 펌웨어 telemetry 경로는 구현됐지만 실제 센서 주소 응답·압력 변화·10분 추세의 실물 인수는 `[미검증]`이다.

### 4.2 물 POI 상태 전이

```text
사용자: 가까운 물 있는 곳 찾아줘
  -> 규칙: find_nearest_water
  -> MAP: 가장 가까운 로컬 수원 표식 계산
  -> 상태: pending_destination
  -> 음성: 수질 미확인 + 목적지 지정 확인

사용자: 네, 그곳으로 설정해줘
  -> 규칙: confirm_destination
  -> MAP: pending POI를 destination으로 저장
  -> MAP ENGINE: 보행 경로·다음 경로점 방위·거리·ETA 계산
  -> 화면 SSE + 고정 확인 WAV
```

“이 물 마셔도 돼”, “근처 계곡물 마셔도 돼”는 이 상태 전이에 들어가지 않는다. 물 카드가 지도 표식은 수질이나 음용 가능을 의미하지 않는다고 고정 안내한다.

### 4.3 GPS 표시 계약

- fix가 유효하면 현재 좌표·정확도·위성 수·KST를 함께 표시한다.
- fix가 없으면 마지막 확정 좌표·당시 정확도·위성 수·AGE만 표시한다.
- 정확도가 없으면 `±—` 또는 “확인할 수 없습니다”로 표시한다.
- replay 또는 데모 POI가 하나라도 섞이면 전역 `DEMO`를 숨기지 않는다.
- 도착 판정은 현재 fix와 허용 정확도가 모두 있을 때만 확정한다.

### 4.4 환경·CO 계약

STM32 telemetry는 온도, 습도, 기압, 기압 추세, CO, 전력 상태를 선택적으로 담는다. 파서는 범위, CRC, sequence gap, age를 검사한다. 값이 없거나 오래되면 녹색 LIVE로 승격하지 않는다. 기압·날씨 문장은 항상 “국지 추정”임을 포함한다.

STM32 펌웨어의 BMP390 `0x76/0x77` 탐색, `pressure_valid`, 최소 10분 표본 기반 `press_trend` telemetry 송신은 구현됐다. 다만 실제 BMP390 보드의 주소 응답, 배선, 압력 변화와 10분 추세가 Jetson까지 도달하는지는 별도 실물 인수 항목이며 `[미검증]`이다. 인터페이스가 값을 받는다는 사실과 펌웨어 구현을 실물 센서 인수 완료로 혼동하지 않는다.

### 4.5 TTS 계약

| 우선순위 | 엔진 | 사용 조건 | 실패 처리 |
|---:|---|---|---|
| 1 | 고정 WAV | 정확히 일치하는 검수 문장 | 파일·WAV 품질 실패 시 오류 |
| 2 | MeloTTS Korean | 동적 한국어 기본 | 로드·합성·품질 실패 시 Piper |
| 3 | Piper Korean | 라이선스 확인된 로컬 모델 | 실패 시 espeak |
| 4 | espeak-ng | 최종 동적 배관 폴백 | `DEGRADED`를 숨기지 않음 |
| 5 | 고정 실패 안내 WAV | 모든 동적 엔진 실패 | 한 번만 안내하고 문장 스트림 종료 |

고정 WAV 측정 결과는 다음과 같다.

| 문장 | 길이 | peak | RMS | clipping |
|---|---:|---:|---:|---:|
| 네, 목적지로 설정되었습니다. | `2.775초` `[실측]` | `0.7055` `[실측]` | `0.1197` `[실측]` | `0` `[실측]` |
| 목적지에 도착하였습니다. | `2.808초` `[실측]` | `0.6892` `[실측]` | `0.1019` `[실측]` | `0` `[실측]` |
| 베이스캠프에 도착하였습니다. | `2.952초` `[실측]` | `0.6032` `[실측]` | `0.1061` `[실측]` | `0` `[실측]` |
| 음성 합성에 실패했습니다. 화면의 검수된 안내를 확인하세요. | `6.360초` `[실측]` | `0.5351` `[실측]` | `0.1046` `[실측]` | `0` `[실측]` |

도착·확인 세 파일은 mono, `22,050Hz` `[실측]`, 실패 안내는 mono, `24,000Hz` `[실측]`이며 모두 PCM WAV다. 고정 음원의 파형·무음·클리핑 조건은 통과했지만, 실외 스피커에서의 명료도는 아직 검증하지 않았다 `[미검증]`.

---

## 5. 검증 증거

### 5.1 MAP

```powershell
cd OGTECH-llm/MAP
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

결과: `Ran 73 tests`, `OK` `[실측: 2026-08-19 최종 재실행]`.

검증 범위에는 GraphML 경로, 멀리 떨어진 좌표의 자동 스냅 금지, GPS fix/미수신, 같은 부팅 `180±25초` 위치 역추적, STM32 frame·telemetry CRC, DS3231 UTC/OSF fail-closed, BMP390 `pressure_valid`·추세, CO 경보 우선순위, trail watchdog ACK, 일조 계산, 전 음성 action, 좌표 payload 거부, 물 POI 확인, 도착 정확도, 안전하지 않은 구 음원 HTTP `404`가 포함된다.

### 5.2 Co-LLM

```powershell
cd OGTECH-llm/Co-LLM
python -B -m unittest discover -s tests -v
python eval/run_eval.py
```

결과: `Ran 55 tests`, `OK` `[실측: 2026-08-19 최종 재실행]`. `voice_cases` `183/183`, 규칙 분류 `280/280`, 정확도 `100%`, refuse `50/50`, 누출 `0건` `[실측: 2026-08-19 최종 재실행, 제작된 회귀·공격 세트]`.

이 `100%`는 실제 마이크·STT·야외 발화 정확도가 아니다. 사람이 만든 텍스트 회귀 세트에서 규칙이 기대대로 동작한다는 뜻이다. 실제 정확도는 `04_record_set.sh`의 마이크 녹음으로 별도 측정해야 한다 `[미검증]`.

### 5.3 정적 검사

- `node --check MAP/kiosk/live_app.js` 통과 `[실측]`
- Python 수정 모듈 `14개` 구문 컴파일 통과 `[실측]`
- JSON 설정·카드·평가 파일 파싱 통과 `[실측]`
- `git diff --check` 공백 오류 없음 `[실측]`

### 5.4 Chromium 실화면

- 증거: `MAP/test-results/product_ui_1024x600.json` (로컬 산출물, 저장소에 커밋하지 않음), `product_boot_1024x600.png`, `product_night_1024x600.png` (셋 다 로컬 산출물이며 저장소에 커밋하지 않습니다)
- 부팅 ACK 초기 잠금 및 5초 고지 정상 `[실측]`
- N키·배경 클릭 차단과 포커스 트랩 정상 `[실측]`
- DEMO·대기·통과 상태 구분 및 진단 상세 6개 표시 정상 `[실측]`
- 하단 조작 버튼 4개가 각각 `96px` 높이 `[실측]`
- 문서 `scrollWidth=1024`, `scrollHeight=600` `[실측]`
- 현재/마지막 좌표와 KST 초단위 갱신 정상 `[실측]`
- 경로·방위·거리·ETA 카드 및 야간 음성 제어 정상 `[실측]`

### 5.5 제품 실행기 종단 경로

`scripts/product_voice.py --text ... --no-tts --json`으로 실제 배포 진입점을 실행했다. 물 위치 탐색은
`find_nearest_water`와 수질 미확인 질문으로 이어졌고, 사용자의 확인은 `confirm_destination`, 경로 질문은
`status`와 MAP 엔진 수치로 이어졌다. 각 라우팅·로컬 MAP 요청은 `0.071초`, `0.077초`, `0.056초`였다
`[실측: 텍스트 입력, STT·TTS 제외]`. 이 수치는 버튼 해제부터 첫 소리까지의 제품 지연이 아니다.

### 5.6 영상 10단계 20회 자동 검증

증거 파일: [`Co-LLM/eval/results/video_scenario_20.json`](../Co-LLM/eval/results/video_scenario_20.json)

| 항목 | 결과 |
|---|---:|
| 영상 10단계 전체 | `20/20` `[실측]` |
| 외부 네트워크 호출 | `0` `[실측: 2026-08-19 최종 재실행]` |
| offline guard | `true` `[실측: 2026-08-19 최종 재실행]` |
| 전체 단계 최악 시간 | `258.69 ms` `[실측: 2026-08-19 최종 재실행]` |
| 목적지·베이스캠프 고정 도착 TTS | `20/20` `[실측: 2026-08-19 최종 재실행]` |
| 하네스에 구성된 테스트 엔진 전부 실패 고정 fallback (`configured_engine_failure_fallback_runs`) | `20/20` `[실측: 2026-08-19 최종 재실행]` |
| 이동 후 경로 거리·ETA 갱신 | `20/20` `[실측: 2026-08-19 최종 재실행]` |
| 일조 위험 전이·베이스캠프 경로 | `20/20` `[실측: 2026-08-19 최종 재실행]` |

실행 명령:

```powershell
cd OGTECH-llm/Co-LLM
python -B eval/run_video_scenario.py --runs 20 --output eval/results/video_scenario_20.json
```

이 결과는 로컬 MAP·replay GPS·고정 오디오를 이용한 소프트웨어 인수다. fallback 수치는 하네스에 구성된 테스트 엔진 전부 실패를 고정 경로로 검증한 결과이며, 실제 MeloTTS/Piper/espeak 로드·청취 실증이 아니다 `[미검증]`. 실제 Jetson 청취·STT 21문장·STM32/CO/GPIO·실기 20회 인수는 포함하지 않는다.

---

## 6. 안전 계약 점검

| 안전 계약 | 기계적 강제 | 결과 |
|---|---|---|
| LLM 경로·방위·거리 생성 금지 | 음성 API 숫자 필드 거부, MAP 코드값 템플릿만 사용 | 통과 |
| 생명 관련 LLM 우회 | LIFE 경로 B, LLM 생명 라벨 승격 차단 | 통과 |
| 구조 요청 수단 아님 | 부팅 필수 고지, 별도 통신 수단 문구 | 통과 |
| GPS 미수신 추정 금지 | last_fix+AGE, 현재 route/arrival 비활성 | 통과 |
| 보이는 절차는 고정 카드 | 카드·코드값 템플릿만 TTS | 통과 |
| 진단·약물·식용·침습 금지 | refuse 최우선, 공격 세트 누출 없음 | 통과 |
| 센서 미확인 확정 금지 | valid·stale·accuracy 검사, 회색 상태 | 통과 |
| DEMO 숨김 금지 | replay·demo POI 전역 전파 | 통과 |
| 오프라인 보존 | 로컬 지도·로컬 API·로컬 일조·로컬 TTS | 통과 |
| 문서는 데이터 | 외부 지시 실행 없음, 실제 GPS 트랙 추가 없음 | 통과 |

---

## 7. 남은 하드웨어 인수 루프

### 7.1 사전 조건

1. `netstat`로 MAP 포트 리스너가 하나뿐인지 확인한다.
2. Jetson `MemAvailable >= 1GB` 게이트를 확인한다 `[출처: 고정 메모리 계약]`.
3. 마이크는 USB 카드의 `usbid`로 식별하고 HDA·APE를 제외한다.
4. STT는 `-ng -ac 450 -bo 1 -bs 1 -nf -t 6 -l ko -nt`를 유지한다 `[출처: 고정 STT 계약]`.
5. STT와 TTS가 같은 시간에 메모리에 올라오지 않는지 프로세스·메모리 로그로 확인한다.

### 7.2 실행

```bash
cd OGTECH-llm/MAP
python app.py --host 127.0.0.1 --port 8790 --gps-mode stm32 --gps-port /dev/ttyACM0 --gps-baud 115200

cd ../Co-LLM
bash scripts/08_device_monitor.sh
bash scripts/07_product_voice.sh --repeat 20 --json
python -B eval/run_hardware_acceptance.py \
  --events eval/results/hardware_observations.jsonl \
  --runs 20 \
  --output eval/results/hardware_acceptance_20.json
```

`hardware_observations.jsonl`은 실제 Jetson의 GPIO/operator 버튼 해제 시각과 loopback 첫 소리 시각, STT·리소스·동시성·네트워크 관측을 장치에서 공급한 파일이어야 한다. fixture·replay·수기 합성값은 인수 증거로 사용하지 않는다. 포트와 장치 경로는 실제 Jetson 배치에 맞게 조정하며, 팀 표준 frontend 프록시를 함께 쓸 때는 MAP 직접 포트와 충돌하지 않게 한다.

하드웨어 인수 하네스의 fail-closed 조건은 경로 B `<=2,000 ms`, 경로 A `<=3,500 ms`, STT false positive `0`, `MemAvailable >=1 GB`, swap 증가 `0`, STT/TTS overlap `false`, 외부 연결 `0`, 실제 loopback onset, 20회 complete 이벤트다. 어느 하나라도 관측되지 않거나 `simulated=false`·실물 관측 출처가 없으면 `pass=false`로 기록한다.

### 7.3 판정 순서

1. 안전 문장 누락 없음, 특히 버너/CO와 refuse `[미검증]`
2. 거짓 양성 `0건`, 특히 “먹을 거 다 떨어졌어”, “이불 챙겨왔어야 했는데” `[미검증]`
3. 버튼 해제부터 첫 소리까지 경로 B 최대 `2.0초` 이내 `[출처: 목표]`
4. 경로 A 최대 `3.5초` 이내 `[출처: 목표]`
5. 음성 `20회` 연속 동일 조건 성공 `[출처: 데모 완료 조건]`
6. Jetson 전원을 끈 채 CO 판정 유지·GNSS 기록 지속 `[미검증]` (소리는 Jetson 스피커라 전원 OFF 구간에는 없다)
7. 실제 야외 보행에서 경로 이탈·체크포인트·베이스캠프·도착·일조 경고 검증 `[미검증]`
8. STM32 `PA0/PA1/PA4` edge와 SSE·push-to-talk 전달, `PC9` 정상 종료 handshake `[미검증]`
9. `trail_alert`/`trail_caution` 진동, CRC ACK, 5초 watchdog의 실제 반복 동작 `[미검증]`
10. DS3231 `0x68` OSF fail-closed·수동 `SET RTC`, BMP390 `0x76/0x77`·`pressure_valid`·10분 추세 `[미검증]`

실패하면 중앙값보다 최댓값과 거짓 양성을 먼저 보고, 그 결과를 이 문서의 다음 반복 항목으로 추가한다. 동적 TTS 엔진은 부팅 진단 대상이 아니므로 실제 Jetson에서 MeloTTS/Piper 로드·청취 품질을 별도 측정한다.

---

## 8. 인계 판단

코드로 닫을 수 있는 범위는 MAP `76/76`, Co-LLM `55/55`, `voice_cases` `183/183`, rules `280/280`, refuse `50/50`, 영상 20회 시나리오와 1024×600 브라우저 검증까지 확인했다 `[실측: 2026-08-19 최종 재실행]`. 다음 작업자는 기능을 새로 설계하지 말고, `7. 남은 하드웨어 인수 루프`와 fail-closed 하네스를 실제 Jetson·STM32·센서·오디오에서 실행해야 한다. 실제 Jetson 청취, STT 21문장, STM32/CO/GPIO, DS3231/BMP390, 실기 연속 20회는 아직 `[미검증]`이며, 실기 로그 없이 “영상처럼 완벽히 구현 완료”라고 판정하면 안 된다.
