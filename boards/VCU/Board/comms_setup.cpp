// FDCAN and debug UART details VCU.ioc doesn't express yet (see "IOC gaps"
// in the board README).

#include "board.hpp"
#include "fdcan.h"

namespace vcu_board {

UART_HandleTypeDef huart3;

namespace {

constexpr uint32_t kDebugUartBaud = 115200;

}  // namespace

void ConfigureCan() {
  // Use the default accept-all RX filter. Enable Critical-bus retransmission.
  CLEAR_BIT(hfdcan2.Instance->CCCR, FDCAN_CCCR_DAR);
  // CubeMX only enables FDCAN1 IRQs. Priority 5 permits FreeRTOS calls.
  HAL_NVIC_SetPriority(FDCAN2_IT0_IRQn, 5, 0);
  HAL_NVIC_EnableIRQ(FDCAN2_IT0_IRQn);
  HAL_NVIC_SetPriority(FDCAN2_IT1_IRQn, 5, 0);
  HAL_NVIC_EnableIRQ(FDCAN2_IT1_IRQn);
}

void ConfigureDebugUart() {
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_USART3_CLK_ENABLE();

  GPIO_InitTypeDef init = {};
  init.Pin = GPIO_PIN_10 | GPIO_PIN_11;  // VCP_TX, VCP_RX (STDC14 14/13)
  init.Mode = GPIO_MODE_AF_PP;
  init.Pull = GPIO_NOPULL;
  init.Speed = GPIO_SPEED_FREQ_LOW;
  init.Alternate = GPIO_AF7_USART3;
  HAL_GPIO_Init(GPIOC, &init);

  huart3.Instance = USART3;
  huart3.Init.BaudRate = kDebugUartBaud;
  huart3.Init.WordLength = UART_WORDLENGTH_8B;
  huart3.Init.StopBits = UART_STOPBITS_1;
  huart3.Init.Parity = UART_PARITY_NONE;
  huart3.Init.Mode = UART_MODE_TX_RX;
  huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart3.Init.OverSampling = UART_OVERSAMPLING_16;
  huart3.Init.ClockPrescaler = UART_PRESCALER_DIV1;
  if (HAL_UART_Init(&huart3) != HAL_OK) {
    Error_Handler();
  }
  // 8-deep RX FIFO: headroom between shell polls.
  HAL_UARTEx_EnableFifoMode(&huart3);
}

}  // namespace vcu_board
