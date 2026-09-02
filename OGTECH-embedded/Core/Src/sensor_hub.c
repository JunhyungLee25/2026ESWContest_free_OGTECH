#include "sensor_hub.h"
#include "jetson_link.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define GPS_RING_SIZE          512U
#define CO_RING_SIZE            64U
#define GPS_LINE_SIZE          128U
#define CO_FRAME_SIZE            9U
#define CO_WARMUP_MS         30000U
#define CO_STALE_MS           5000U
#define GPS_STALE_MS          5000U
#define DHT_PERIOD_MS         2000U
#define JETSON_PERIOD_MS      1000U
#define DEBUG_PERIOD_MS       2000U

typedef struct
{
  uint8_t valid;
  int16_t temperature_x10;
  uint16_t humidity_x10;
} DhtData_t;

static UART_HandleTypeDef *gps_uart;
static UART_HandleTypeDef *co_uart;
static UART_HandleTypeDef *debug_uart;
static UART_HandleTypeDef *jetson_uart;

static uint8_t gps_rx_byte;
static uint8_t co_rx_byte;
static volatile uint8_t gps_ring[GPS_RING_SIZE];
static volatile uint16_t gps_head;
static volatile uint16_t gps_tail;
static volatile uint8_t co_ring[CO_RING_SIZE];
static volatile uint16_t co_head;
static volatile uint16_t co_tail;

static char gps_line[GPS_LINE_SIZE];
static uint16_t gps_line_length;
static uint8_t gps_nmea_seen;
static uint8_t gps_fix;
static uint8_t gps_satellites;
static int32_t gps_latitude_e7;
static int32_t gps_longitude_e7;
static uint32_t gps_last_nmea_ms;
static uint32_t gps_last_fix_ms;

static uint8_t co_frame[CO_FRAME_SIZE];
static uint8_t co_frame_length;
static uint8_t co_valid;
static uint16_t co_ppm;
static uint32_t co_last_valid_ms;

static DhtData_t dht;
static uint32_t boot_ms;
static uint32_t last_dht_ms;
static uint32_t last_jetson_ms;
static uint32_t last_debug_ms;
static uint32_t jetson_sequence;

static uint32_t CyclesPerUs(void)
{
  uint32_t value = SystemCoreClock / 1000000U;
  return (value == 0U) ? 1U : value;
}

static void DelayUs(uint32_t microseconds)
{
  uint32_t start = DWT->CYCCNT;
  uint32_t cycles = microseconds * CyclesPerUs();
  while ((uint32_t)(DWT->CYCCNT - start) < cycles) {}
}

static void DhtPinOutput(void)
{
  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = DHT11_DATA_Pin;
  gpio.Mode = GPIO_MODE_OUTPUT_OD;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(DHT11_DATA_GPIO_Port, &gpio);
}

static void DhtPinInput(void)
{
  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = DHT11_DATA_Pin;
  gpio.Mode = GPIO_MODE_INPUT;
  gpio.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(DHT11_DATA_GPIO_Port, &gpio);
}

/* Wait until the pin leaves the specified state. */
static uint8_t DhtWaitWhile(GPIO_PinState state, uint32_t timeout_us)
{
  uint32_t start = DWT->CYCCNT;
  uint32_t timeout_cycles = timeout_us * CyclesPerUs();
  while (HAL_GPIO_ReadPin(DHT11_DATA_GPIO_Port, DHT11_DATA_Pin) == state)
  {
    if ((uint32_t)(DWT->CYCCNT - start) > timeout_cycles)
    {
      return 0U;
    }
  }
  return 1U;
}

static uint8_t DhtRead(DhtData_t *result)
{
  uint8_t bytes[5] = {0U};
  uint8_t success = 0U;

  result->valid = 0U;
  DhtPinOutput();
  HAL_GPIO_WritePin(DHT11_DATA_GPIO_Port, DHT11_DATA_Pin, GPIO_PIN_SET);
  HAL_Delay(2U);
  HAL_GPIO_WritePin(DHT11_DATA_GPIO_Port, DHT11_DATA_Pin, GPIO_PIN_RESET);
  HAL_Delay(20U);
  HAL_GPIO_WritePin(DHT11_DATA_GPIO_Port, DHT11_DATA_Pin, GPIO_PIN_SET);
  DelayUs(30U);
  DhtPinInput();

  if (!DhtWaitWhile(GPIO_PIN_SET, 120U) ||
      !DhtWaitWhile(GPIO_PIN_RESET, 120U) ||
      !DhtWaitWhile(GPIO_PIN_SET, 120U))
  {
    goto cleanup;
  }

  for (uint8_t bit = 0U; bit < 40U; bit++)
  {
    uint32_t high_start;
    uint32_t high_us;
    if (!DhtWaitWhile(GPIO_PIN_RESET, 100U))
    {
      goto cleanup;
    }
    high_start = DWT->CYCCNT;
    if (!DhtWaitWhile(GPIO_PIN_SET, 120U))
    {
      goto cleanup;
    }
    high_us = (uint32_t)(DWT->CYCCNT - high_start) / CyclesPerUs();
    bytes[bit / 8U] <<= 1U;
    if (high_us > 45U)
    {
      bytes[bit / 8U] |= 1U;
    }
  }

  if ((uint8_t)(bytes[0] + bytes[1] + bytes[2] + bytes[3]) != bytes[4])
  {
    goto cleanup;
  }

  result->humidity_x10 = ((uint16_t)bytes[0] * 10U) + bytes[1];
  result->temperature_x10 =
      (int16_t)(((uint16_t)(bytes[2] & 0x7FU) * 10U) + bytes[3]);
  if ((bytes[2] & 0x80U) != 0U)
  {
    result->temperature_x10 = (int16_t)-result->temperature_x10;
  }
  result->valid = 1U;
  success = 1U;

cleanup:
  DhtPinOutput();
  HAL_GPIO_WritePin(DHT11_DATA_GPIO_Port, DHT11_DATA_Pin, GPIO_PIN_SET);
  return success;
}

static void GpsPush(uint8_t value)
{
  uint16_t next = (uint16_t)((gps_head + 1U) % GPS_RING_SIZE);
  if (next != gps_tail)
  {
    gps_ring[gps_head] = value;
    gps_head = next;
  }
}

static uint8_t GpsPop(uint8_t *value)
{
  if (gps_tail == gps_head)
  {
    return 0U;
  }
  *value = gps_ring[gps_tail];
  gps_tail = (uint16_t)((gps_tail + 1U) % GPS_RING_SIZE);
  return 1U;
}

static void CoPush(uint8_t value)
{
  uint16_t next = (uint16_t)((co_head + 1U) % CO_RING_SIZE);
  if (next != co_tail)
  {
    co_ring[co_head] = value;
    co_head = next;
  }
}

static uint8_t CoPop(uint8_t *value)
{
  if (co_tail == co_head)
  {
    return 0U;
  }
  *value = co_ring[co_tail];
  co_tail = (uint16_t)((co_tail + 1U) % CO_RING_SIZE);
  return 1U;
}

static uint8_t CoChecksum(const uint8_t *frame)
{
  uint8_t sum = 0U;
  for (uint8_t index = 1U; index <= 7U; index++)
  {
    sum = (uint8_t)(sum + frame[index]);
  }
  return (uint8_t)(~sum + 1U);
}

static void CoProcess(void)
{
  uint8_t value;
  while (CoPop(&value))
  {
    if (co_frame_length == 0U)
    {
      if (value != 0xFFU)
      {
        continue;
      }
      co_frame[co_frame_length++] = value;
      continue;
    }

    co_frame[co_frame_length++] = value;
    if (co_frame_length < CO_FRAME_SIZE)
    {
      continue;
    }

    if ((co_frame[0] == 0xFFU) && (co_frame[1] == 0x04U) &&
        (co_frame[2] == 0x03U) && (CoChecksum(co_frame) == co_frame[8]))
    {
      co_ppm = (uint16_t)(((uint16_t)co_frame[4] << 8U) | co_frame[5]);
      co_last_valid_ms = HAL_GetTick();
      co_valid = 1U;
      co_frame_length = 0U;
      continue;
    }

    /* Invalid frame: retain bytes beginning at the next possible marker. */
    co_frame_length = 0U;
    for (uint8_t index = 1U; index < CO_FRAME_SIZE; index++)
    {
      if (co_frame[index] == 0xFFU)
      {
        uint8_t remaining = (uint8_t)(CO_FRAME_SIZE - index);
        memmove(co_frame, &co_frame[index], remaining);
        co_frame_length = remaining;
        break;
      }
    }
  }
}

static uint8_t HexNibble(char value, uint8_t *valid)
{
  if ((value >= '0') && (value <= '9'))
  {
    *valid = 1U;
    return (uint8_t)(value - '0');
  }
  if ((value >= 'A') && (value <= 'F'))
  {
    *valid = 1U;
    return (uint8_t)(value - 'A' + 10);
  }
  if ((value >= 'a') && (value <= 'f'))
  {
    *valid = 1U;
    return (uint8_t)(value - 'a' + 10);
  }
  *valid = 0U;
  return 0U;
}

static uint8_t NmeaChecksumValid(const char *line)
{
  const char *star;
  uint8_t checksum = 0U;
  uint8_t high_valid;
  uint8_t low_valid;
  uint8_t expected;

  if ((line == NULL) || (line[0] != '$'))
  {
    return 0U;
  }
  star = strchr(line, '*');
  if ((star == NULL) || (star[1] == '\0') || (star[2] == '\0'))
  {
    return 0U;
  }
  for (const char *cursor = line + 1; cursor < star; cursor++)
  {
    checksum ^= (uint8_t)*cursor;
  }
  expected = (uint8_t)(HexNibble(star[1], &high_valid) << 4U);
  expected |= HexNibble(star[2], &low_valid);
  return (high_valid && low_valid && (checksum == expected)) ? 1U : 0U;
}

static uint8_t NmeaField(const char *line, uint8_t wanted,
                         char *output, uint16_t output_size)
{
  uint8_t field = 0U;
  uint16_t used = 0U;
  const char *cursor = line;
  if ((line == NULL) || (output == NULL) || (output_size == 0U))
  {
    return 0U;
  }
  while ((*cursor != '\0') && (field < wanted))
  {
    if (*cursor++ == ',')
    {
      field++;
    }
  }
  if (field != wanted)
  {
    output[0] = '\0';
    return 0U;
  }
  while ((*cursor != '\0') && (*cursor != ',') && (*cursor != '*') &&
         (*cursor != '\r') && (*cursor != '\n'))
  {
    if (used < (uint16_t)(output_size - 1U))
    {
      output[used++] = *cursor;
    }
    cursor++;
  }
  output[used] = '\0';
  return 1U;
}

static int32_t NmeaCoordinateE7(const char *coordinate, char hemisphere,
                                uint8_t *valid)
{
  const char *dot;
  int before_dot;
  int degree_digits;
  int32_t degrees = 0;
  int32_t minute_whole = 0;
  int32_t minute_fraction_e6 = 0;
  int fraction_digits = 0;
  int64_t result;

  *valid = 0U;
  if ((coordinate == NULL) || ((dot = strchr(coordinate, '.')) == NULL))
  {
    return 0;
  }
  before_dot = (int)(dot - coordinate);
  degree_digits = before_dot - 2;
  if ((degree_digits != 2) && (degree_digits != 3))
  {
    return 0;
  }
  for (int index = 0; index < before_dot; index++)
  {
    if ((coordinate[index] < '0') || (coordinate[index] > '9'))
    {
      return 0;
    }
    if (index < degree_digits)
    {
      degrees = (degrees * 10) + (coordinate[index] - '0');
    }
    else
    {
      minute_whole = (minute_whole * 10) + (coordinate[index] - '0');
    }
  }
  for (dot++; (*dot >= '0') && (*dot <= '9') && (fraction_digits < 6);
       dot++, fraction_digits++)
  {
    minute_fraction_e6 = (minute_fraction_e6 * 10) + (*dot - '0');
  }
  while (fraction_digits++ < 6)
  {
    minute_fraction_e6 *= 10;
  }
  if (minute_whole >= 60)
  {
    return 0;
  }
  result = ((int64_t)degrees * 10000000LL) +
           ((((int64_t)minute_whole * 1000000LL) + minute_fraction_e6) / 6LL);
  if ((hemisphere == 'S') || (hemisphere == 'W'))
  {
    result = -result;
  }
  else if ((hemisphere != 'N') && (hemisphere != 'E'))
  {
    return 0;
  }
  *valid = 1U;
  return (int32_t)result;
}

static void GpsParseLine(char *line)
{
  char latitude[20];
  char longitude[20];
  char north_south[3];
  char east_west[3];
  char fix_quality[4];
  char satellites[4];
  uint8_t latitude_valid;
  uint8_t longitude_valid;
  int fix;
  int sat;

  if (!NmeaChecksumValid(line))
  {
    return;
  }
  gps_nmea_seen = 1U;
  gps_last_nmea_ms = HAL_GetTick();

  /* Accept GGA from any talker: GPGGA, GNGGA, GLGGA, etc. */
  if ((strlen(line) < 6U) || (line[3] != 'G') ||
      (line[4] != 'G') || (line[5] != 'A'))
  {
    return;
  }
  (void)NmeaField(line, 2U, latitude, sizeof(latitude));
  (void)NmeaField(line, 3U, north_south, sizeof(north_south));
  (void)NmeaField(line, 4U, longitude, sizeof(longitude));
  (void)NmeaField(line, 5U, east_west, sizeof(east_west));
  (void)NmeaField(line, 6U, fix_quality, sizeof(fix_quality));
  (void)NmeaField(line, 7U, satellites, sizeof(satellites));
  fix = atoi(fix_quality);
  sat = atoi(satellites);
  if (sat < 0) sat = 0;
  if (sat > 255) sat = 255;
  gps_satellites = (uint8_t)sat;
  if (fix <= 0)
  {
    gps_fix = 0U;
    return;
  }
  gps_latitude_e7 = NmeaCoordinateE7(latitude, north_south[0], &latitude_valid);
  gps_longitude_e7 = NmeaCoordinateE7(longitude, east_west[0], &longitude_valid);
  if (latitude_valid && longitude_valid)
  {
    gps_fix = 1U;
    gps_last_fix_ms = HAL_GetTick();
  }
  else
  {
    gps_fix = 0U;
  }
}

static void GpsProcess(void)
{
  uint8_t value;
  while (GpsPop(&value))
  {
    if (value == '$')
    {
      gps_line_length = 0U;
      gps_line[gps_line_length++] = (char)value;
    }
    else if (gps_line_length == 0U)
    {
      continue;
    }
    else if (value == '\n')
    {
      gps_line[gps_line_length] = '\0';
      GpsParseLine(gps_line);
      gps_line_length = 0U;
    }
    else if (gps_line_length < (GPS_LINE_SIZE - 1U))
    {
      gps_line[gps_line_length++] = (char)value;
    }
    else
    {
      gps_line_length = 0U;
    }
  }
}

static JetsonCoState_t CurrentCoState(uint32_t now)
{
  if ((uint32_t)(now - boot_ms) < CO_WARMUP_MS)
  {
    return JETSON_CO_WARMING_UP;
  }
  if (co_valid && ((uint32_t)(now - co_last_valid_ms) <= CO_STALE_MS))
  {
    return JETSON_CO_VALID;
  }
  return JETSON_CO_STALE;
}

static JetsonGpsState_t CurrentGpsState(uint32_t now)
{
  if (!gps_nmea_seen || ((uint32_t)(now - gps_last_nmea_ms) > GPS_STALE_MS))
  {
    return JETSON_GPS_NOT_FOUND;
  }
  if (!gps_fix || ((uint32_t)(now - gps_last_fix_ms) > GPS_STALE_MS))
  {
    return JETSON_GPS_NO_FIX;
  }
  return JETSON_GPS_FIX;
}

static void DebugPrint(uint32_t now)
{
  char line[240];
  int length = snprintf(
      line, sizeof(line),
      "DHT=%s,T=%d.%dC,H=%u.%u%%,CO_STATE=%u,CO=%uppm,"
      "GPS_STATE=%u,LAT_E7=%ld,LON_E7=%ld,SAT=%u\r\n",
      dht.valid ? "OK" : "ERROR",
      (int)(dht.temperature_x10 / 10),
      abs((int)(dht.temperature_x10 % 10)),
      (unsigned)(dht.humidity_x10 / 10U),
      (unsigned)(dht.humidity_x10 % 10U),
      (unsigned)CurrentCoState(now), (unsigned)co_ppm,
      (unsigned)CurrentGpsState(now),
      (long)gps_latitude_e7, (long)gps_longitude_e7,
      (unsigned)gps_satellites);
  if ((length > 0) && ((size_t)length < sizeof(line)))
  {
    (void)HAL_UART_Transmit(debug_uart, (uint8_t *)line,
                            (uint16_t)length, 100U);
  }
}

static void JetsonSend(uint32_t now)
{
  JetsonTelemetry_t telemetry = {0};
  telemetry.sequence = jetson_sequence;
  telemetry.uptime_ms = now;
  telemetry.dht_valid = dht.valid;
  telemetry.temperature_x10 = dht.temperature_x10;
  telemetry.humidity_x10 = dht.humidity_x10;
  telemetry.co_state = CurrentCoState(now);
  telemetry.co_ppm = co_ppm;
  telemetry.gps_state = CurrentGpsState(now);
  telemetry.latitude_e7 = gps_latitude_e7;
  telemetry.longitude_e7 = gps_longitude_e7;
  telemetry.satellites = gps_satellites;
  if (JetsonLink_Send(jetson_uart, &telemetry) == HAL_OK)
  {
    jetson_sequence++;
  }
}

void SensorHub_Init(UART_HandleTypeDef *gps, UART_HandleTypeDef *co,
                    UART_HandleTypeDef *debug, UART_HandleTypeDef *jetson)
{
  static const char banner[] =
      "\r\nSmartAid start: GPS=USART1, CO=USART2, DEBUG=USART3, JETSON=UART4\r\n";
  gps_uart = gps;
  co_uart = co;
  debug_uart = debug;
  jetson_uart = jetson;
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
#if defined(DWT_LAR)
  DWT->LAR = 0xC5ACCE55U;
#endif
  DWT->CYCCNT = 0U;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  boot_ms = HAL_GetTick();
  last_dht_ms = boot_ms - DHT_PERIOD_MS;
  last_jetson_ms = boot_ms - JETSON_PERIOD_MS;
  last_debug_ms = boot_ms - DEBUG_PERIOD_MS;
  if (HAL_UART_Receive_IT(gps_uart, &gps_rx_byte, 1U) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UART_Receive_IT(co_uart, &co_rx_byte, 1U) != HAL_OK)
  {
    Error_Handler();
  }
  (void)HAL_UART_Transmit(debug_uart, (uint8_t *)banner,
                          (uint16_t)(sizeof(banner) - 1U), 100U);
}

void SensorHub_Poll(void)
{
  uint32_t now;
  GpsProcess();
  CoProcess();
  now = HAL_GetTick();
  if ((uint32_t)(now - last_dht_ms) >= DHT_PERIOD_MS)
  {
    last_dht_ms = now;
    (void)DhtRead(&dht);
    GpsProcess();
    CoProcess();
    now = HAL_GetTick();
  }
  if ((uint32_t)(now - last_jetson_ms) >= JETSON_PERIOD_MS)
  {
    last_jetson_ms = now;
    JetsonSend(now);
  }
  if ((uint32_t)(now - last_debug_ms) >= DEBUG_PERIOD_MS)
  {
    last_debug_ms = now;
    DebugPrint(now);
  }
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *uart)
{
  if (uart->Instance == USART1)
  {
    GpsPush(gps_rx_byte);
    (void)HAL_UART_Receive_IT(gps_uart, &gps_rx_byte, 1U);
  }
  else if (uart->Instance == USART2)
  {
    CoPush(co_rx_byte);
    (void)HAL_UART_Receive_IT(co_uart, &co_rx_byte, 1U);
  }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *uart)
{
  if (uart->Instance == USART1)
  {
    gps_line_length = 0U;
    __HAL_UART_CLEAR_OREFLAG(gps_uart);
    (void)HAL_UART_Receive_IT(gps_uart, &gps_rx_byte, 1U);
  }
  else if (uart->Instance == USART2)
  {
    co_frame_length = 0U;
    __HAL_UART_CLEAR_OREFLAG(co_uart);
    (void)HAL_UART_Receive_IT(co_uart, &co_rx_byte, 1U);
  }
}
