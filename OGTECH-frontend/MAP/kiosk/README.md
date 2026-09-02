# OGTECH 제품 지도 화면 — 7인치 1024×600

`/product/`에서 실행되는 실제 제품 화면이다. 같은 폴더 위 `MAP/static/`은 지도 업로드와 직렬 연결을
확인하는 개발자 도구이며 그대로 유지한다.

## 표시 데이터

- STM32 텔레메트리: GPS, DHT11 온도·습도, ZE16B-CO, 향후 전원 계측
- 코드 계산: 트레일 선분 이탈 거리, 목적지·베이스캠프 경로·방위·거리
- 오프라인 계산: 현재 좌표의 일출·일몰·시민박명, 베이스캠프 귀환 권고 시각
- 저장 지점: 목적지, 베이스캠프, 체크포인트

현재 실장 부품은 DHT11(온·습도)·ZE16B-CO(CO)·Air530(GNSS)이다. 이전 계획의 SHT40·ZE07-CO는 교체됐고
BMP390(기압)·DS3231(RTC)은 아직 미연결이다 `[출처: OGTECH-embedded/README.md]`. 텔레메트리 필드
이름(`env.sht_valid` 등)은 옛 부품 이름을 그대로 쓰지만 데이터 출처는 위 현행 부품이다.

화면은 `/api/device/events`의 Server-Sent Events를 받으므로 상시 폴링하지 않는다. 좌표·경로·방위·거리·
귀환 시각은 Python 지도/태양 계산 코드가 만들며 LLM은 생성할 수 없다.

음성 지도 명령은 `/api/voice/commands`로 들어오고 `/api/voice/events` SSE로 화면에 반영된다. API는 숫자
없는 열거형 `action`과 선택적 `request_id`만 받는다. 음성 계층이 좌표·거리·방위를 보내면 요청을 거부한다.

## 실행

```bash
cd OGTECH-frontend/MAP
. .venv/bin/activate
python app.py --gps-mode stm32 --gps-port /dev/ttyACM0 --gps-baud 115200
```

Chromium에서 `http://127.0.0.1:8790/video/?live=1`을 연다(정본 제품 화면).

전체 배선·Jetson 설치·자동 시작 절차는 [STM32_JETSON_SETUP.md](../STM32_JETSON_SETUP.md)를 따른다.

> 펌웨어가 `gps_service.py`와 같은 JSONL+CRC16 프로토콜을 쓰도록 2026-08-21에 맞췄다(호스트
> 계약 테스트 통과). 실물 보드 연동은 아직 `[미검증]`이라, JSONL 펌웨어를 플래시한 보드가 있어야
> `--gps-mode stm32`에서 실제 센서 값이 올라온다. 검증 근거와 남은 절차는 위 문서 6절에 있다.

## DEMO 표식

기존 상단 오른쪽 고정 `DEMO` 칸은 제거했고 그 자리에 실제 환경 계기를 넣었다. 다음 중 하나라도 모의값이면
지도 제목 옆에 작은 앰버 `DEMO` 태그가 나타난다.

환경 계기는 온도, 상대습도, 기압과 추세(`↑/→/↓`), CO를 한 줄에 표시한다. BMP390 값이 오지 않으면
기압 자리는 회색 `P —`로 남으며 정상값으로 꾸미지 않는다.

- NMEA replay 모드
- 저장소에 포함된 건국대 샘플 지도
- `poi_catalog.json`의 시연 수원 표식을 목적지로 사용한 상태

실제 STM32 입력과 사용 지역의 실제 지도를 모두 쓰면 태그가 사라진다. 샘플·재생 값을 쓰면서 태그를
숨기는 동작은 지원하지 않는다.

## 7인치 규격

- 화면: 1024×600, 상단 84 px / 지도 420 px / 하단 96 px `[출처: styles.css]`
- 하단 터치 버튼: 높이 96 px = 약 14.4 mm `[출처: 169.5 PPI 기준]`
- 본문: 20 px 이상, 스텐실 라벨만 15 px
- 외부 폰트·CDN·프레임워크 없음
- blur/filter 없음, Canvas 2D 직접 렌더링

## 조작

- `목적지`: 누른 뒤 지도를 터치해 지정
- `체크포인트`: 현재 live GPS 위치 저장
- `베이스캠프`: 없으면 현재 위치 저장, 있으면 귀환 경로 선택
- `야간 모드`: 적색 단색 전환

부팅 화면은 “이 장치는 구조 요청 수단이 아니다”라는 한계를 5초 동안 반드시 표시한다. 배경 클릭이나
Escape로 닫을 수 없고, 별도 비상 통신 수단 준비를 확인한 뒤에만 지도 조작이 열린다.

## 음성 제어

- `save_basecamp`, `save_checkpoint`: 현재 GPS fix만 저장
- `route_basecamp`, `route_destination`, `route_last_checkpoint`: 저장된 ID만 선택
- `find_nearest_water`: 현재 위치와 로컬 POI로 최근접 표식을 코드 계산
- `confirm_destination`, `reject_destination`: 2단계 확인
- `night_on`, `night_off`, `night_toggle`: 화면 적색 모드
- `status`: 현재 장치 상태 이벤트 표시

수원 검색은 수질이나 음용 가능을 판정하지 않는다. 서버는 “수질은 확인되지 않았다”는 고정 문구를 먼저
말하고, 사용자가 확인하기 전에는 목적지를 저장하지 않는다.

GPS fix가 없으면 현재 위치 저장을 거부한다. 마지막 확정 좌표는 회색과 경과 시간으로만 표시하고 새 경로·
방위를 확정값처럼 계산하지 않는다.

## 파일

- `index.html`, `styles.css`, `live_app.js`: 실제 제품 화면
- `konkuk_map.js`: 서버가 없을 때의 정적 샘플 지도 폴백
- `poi_catalog.json`: 활성 지도 파일명과 일치할 때만 쓰는 시연 POI. 포함 시 `DEMO`
- `app.js`: 이전 촬영 장면 재생 코드. 제품 화면에서는 로드하지 않으며 이력·비교용으로만 남김
- `build_map_data.py`: GraphML 샘플을 정적 폴백 데이터로 변환
