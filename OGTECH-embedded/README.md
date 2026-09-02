# OGTECH STM32

STM32H7A3에서 GPS, 온습도, CO 센서 값을 읽고 Jetson으로 전송하는 코드입니다.

## 파일 구조

```text
Core/
├─ Inc/
│  ├─ jetson_link.h
│  ├─ main.h
│  ├─ sensor_hub.h
│  └─ stm32h7xx_it.h
└─ Src/
   ├─ jetson_link.c
   ├─ main.c
   ├─ sensor_hub.c
   ├─ stm32h7xx_hal_msp.c
   └─ stm32h7xx_it.c
```

## 주요 기능

- Air530 GPS 수신
- DHT11 온도·습도 측정
- ZE16B-CO 일산화탄소 농도 수신
- 센서 상태를 1초마다 Jetson으로 전송
- XOR 체크섬으로 전송 오류 확인

## 연결 정보

- GPS: `USART1`, 9600 baud
- CO 센서: `USART2`, 9600 baud
- 디버그 출력: `USART3`, 115200 baud
- Jetson: `UART4`, 115200 baud
- 전송 형식: `$SA1,...*XX`

Jetson에서는 [`OGTECH-frontend/MAP/gps_service.py`](../OGTECH-frontend/MAP/gps_service.py)가 센서 데이터를 받아 화면과 지도 기능에 연결합니다.
