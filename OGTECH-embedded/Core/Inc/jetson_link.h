#ifndef JETSON_LINK_H
#define JETSON_LINK_H

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"
#include <stdint.h>

typedef enum
{
  JETSON_CO_WARMING_UP = 0,
  JETSON_CO_VALID = 1,
  JETSON_CO_STALE = 2
} JetsonCoState_t;

typedef enum
{
  JETSON_GPS_NOT_FOUND = 0,
  JETSON_GPS_NO_FIX = 1,
  JETSON_GPS_FIX = 2
} JetsonGpsState_t;

typedef struct
{
  uint32_t sequence;
  uint32_t uptime_ms;
  uint8_t dht_valid;
  int16_t temperature_x10;
  uint16_t humidity_x10;
  JetsonCoState_t co_state;
  uint16_t co_ppm;
  JetsonGpsState_t gps_state;
  int32_t latitude_e7;
  int32_t longitude_e7;
  uint8_t satellites;
} JetsonTelemetry_t;

/* $SA1,...*CS\r\n, where CS is XOR of the bytes between '$' and '*'. */
HAL_StatusTypeDef JetsonLink_Send(UART_HandleTypeDef *uart,
                                  const JetsonTelemetry_t *telemetry);

#ifdef __cplusplus
}
#endif

#endif
