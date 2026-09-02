#ifndef SENSOR_HUB_H
#define SENSOR_HUB_H

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"

void SensorHub_Init(UART_HandleTypeDef *gps_uart,
                    UART_HandleTypeDef *co_uart,
                    UART_HandleTypeDef *debug_uart,
                    UART_HandleTypeDef *jetson_uart);
void SensorHub_Poll(void);

#ifdef __cplusplus
}
#endif

#endif
