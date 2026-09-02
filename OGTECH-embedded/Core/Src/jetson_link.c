#include "jetson_link.h"

#include <stdio.h>
#include <string.h>

#define JETSON_PAYLOAD_SIZE    192U
#define JETSON_FRAME_SIZE      208U
#define JETSON_TX_TIMEOUT_MS    50U

static uint8_t JetsonLink_Xor(const char *text)
{
  uint8_t checksum = 0U;
  while (*text != '\0')
  {
    checksum ^= (uint8_t)*text++;
  }
  return checksum;
}

HAL_StatusTypeDef JetsonLink_Send(UART_HandleTypeDef *uart,
                                  const JetsonTelemetry_t *telemetry)
{
  char payload[JETSON_PAYLOAD_SIZE];
  char frame[JETSON_FRAME_SIZE];
  int payload_length;
  int frame_length;

  if ((uart == NULL) || (telemetry == NULL))
  {
    return HAL_ERROR;
  }

  payload_length = snprintf(
      payload, sizeof(payload),
      "SA1,%lu,%lu,%u,%d,%u,%u,%u,%u,%ld,%ld,%u",
      (unsigned long)telemetry->sequence,
      (unsigned long)telemetry->uptime_ms,
      (unsigned)telemetry->dht_valid,
      (int)telemetry->temperature_x10,
      (unsigned)telemetry->humidity_x10,
      (unsigned)telemetry->co_state,
      (unsigned)telemetry->co_ppm,
      (unsigned)telemetry->gps_state,
      (long)telemetry->latitude_e7,
      (long)telemetry->longitude_e7,
      (unsigned)telemetry->satellites);

  if ((payload_length < 0) || ((size_t)payload_length >= sizeof(payload)))
  {
    return HAL_ERROR;
  }

  frame_length = snprintf(frame, sizeof(frame), "$%s*%02X\r\n", payload,
                          (unsigned)JetsonLink_Xor(payload));
  if ((frame_length < 0) || ((size_t)frame_length >= sizeof(frame)))
  {
    return HAL_ERROR;
  }

  return HAL_UART_Transmit(uart, (uint8_t *)frame,
                           (uint16_t)frame_length, JETSON_TX_TIMEOUT_MS);
}
