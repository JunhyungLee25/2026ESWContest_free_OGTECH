# Air530·STM32 실장 확인

> 통합 배선과 Jetson 설치의 정본은 [STM32_JETSON_SETUP.md](STM32_JETSON_SETUP.md)다. 이 문서는 GPS만
> 빠르게 확인하는 체크리스트다.

## 최종 데이터 경로

```text
Air530 9600 NMEA → STM32 USART1 → 2초 주기 통합 JSONL + CRC16
→ Jetson USB 직렬 115200 → gps_service.py → /api/device/events → /product/
```

STM32 모드는 연결 직후 `STREAM ON`을 한 번 전송하고 GPS·온습도·CO 통합 텔레메트리를 연속 수신한다
`[출처: gps_service.py]`. 펌웨어 쪽 JSONL 구현은 2026-08-21 완료(호스트 계약 테스트 통과,
실장 `[미검증]` — STM32_JETSON_SETUP.md 6절). `GET_FIX`는 이전 도구 호환용으로 남아 있다.

## 하드웨어 전 확인

```bash
cd OGTECH-frontend/MAP
. .venv/bin/activate
python app.py --gps-mode replay
```

`http://127.0.0.1:8790/product/`을 연다. replay는 고정 샘플이므로 지도 제목 옆 `모의 데이터`가 보여야 한다.
Air530 GGA에 `acc_m`이 없으므로 정확도는 `±—`, 위성 수는 `SAT 10`으로 표시된다
`[출처: sample_data/air530_replay.nmea]`.

## Air530 단독 직결 확인

STM32 펌웨어와 분리해 NMEA만 확인할 때 사용한다.

```bash
python app.py --gps-mode air530 --gps-port /dev/ttyUSB0 --gps-baud 9600
```

- 올바른 NMEA 체크섬만 반영
- GGA fix 좌표와 위성 수 표시
- HDOP를 미터 정확도로 변환하지 않음
- 3초 이상 입력이 멈추면 마지막 확정 좌표 + 경과 시간만 표시 `[출처: gps_service.py]`

## STM32 통합 확인

```bash
python app.py --gps-mode stm32 --gps-port /dev/ttyACM0 --gps-baud 115200
curl -s http://127.0.0.1:8790/api/device | python3 -m json.tool
```

확인할 필드:

```text
gps.connected / gps.fix / gps.satellites / gps.age_s
environment.valid / environment.temp_c / environment.humidity_pct
environment.press_hpa / environment.press_trend
co.valid / co.warming_up / co.ppm / co.alarm
gps.rejected_lines / gps.sequence_gaps
```

`rejected_lines`가 계속 늘면 CRC, baud, 공통 접지, 로직 전압을 확인한다. `sequence_gaps`가 늘면 직렬 누락이나
STM32 재부팅 여부를 확인한다.

## 실장 완료 조건

- GPS 미수신을 추정 좌표로 덮지 않는다.
- 마지막 확정 좌표와 경과 시간이 증가한다.
- 정확도 `±m` 또는 `±—`와 위성 수가 항상 같이 보인다.
- USB 직렬을 뺐다가 다시 꽂으면 2초 재연결 루프가 복구한다 `[출처: gps_service.py]`.
- 실제 센서 + 실제 사용 지역 지도를 쓸 때만 `DEMO`가 사라진다.
- 실제 GPS 트랙과 `/dev/serial/by-id` 개인 식별값은 Git에 커밋하지 않는다.
