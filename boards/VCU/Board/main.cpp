// VCU firmware entry point — hand-written, not CubeMX-generated. Everything
// under Core/ is CubeMX-owned and safe to regenerate (run post_cubemx.sh
// afterwards); everything under Board/ is ours and CubeMX never touches it.
//
// Structure: peripheral bring-up (clocks, pin mux, handles) stays at the ST
// HAL level in this file; application logic lives in App/vcu_app.* against
// LHAL interfaces only, so it also runs on the host (see :vcu_app_test and
// :vcu_sim).

#include "main.h"

#include <cstdio>
#include <cstring>

#include "lhal/stm32/gpio.hpp"
#include "lhal/stm32/system.hpp"
#include "lhal/stm32/uart.hpp"
#include "lhre/build_info.hpp"
#include "vcu_app.hpp"

namespace {

// TODO(vcu): point this at the real status LED once the board pinout is
// final. PA5 is the usual Nucleo dev-board LED.
GPIO_TypeDef* const kStatusLedPort = GPIOA;
constexpr uint16_t kStatusLedPin = GPIO_PIN_5;

// Debug UART. TODO(vcu): confirm against the real board pinout — LPUART1 on
// PA2/PA3 is the ST-LINK virtual COM port on the Nucleo dev board.
UART_HandleTypeDef g_debug_uart_handle;
constexpr uint32_t kDebugUartBaud = 115200;

void ConfigureSystemClock() {
  RCC_OscInitTypeDef osc = {};
  RCC_ClkInitTypeDef clk = {};

  HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1);

  osc.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  osc.HSIState = RCC_HSI_ON;
  osc.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  osc.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&osc) != HAL_OK) {
    Error_Handler();
  }

  clk.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                  RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  clk.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
  clk.AHBCLKDivider = RCC_SYSCLK_DIV1;
  clk.APB1CLKDivider = RCC_HCLK_DIV1;
  clk.APB2CLKDivider = RCC_HCLK_DIV1;
  if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_0) != HAL_OK) {
    Error_Handler();
  }
}

void ConfigureGpio() {
  __HAL_RCC_GPIOA_CLK_ENABLE();

  GPIO_InitTypeDef init = {};
  init.Pin = kStatusLedPin;
  init.Mode = GPIO_MODE_OUTPUT_PP;
  init.Pull = GPIO_NOPULL;
  init.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(kStatusLedPort, &init);
}

void ConfigureDebugUart() {
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_LPUART1_CLK_ENABLE();

  GPIO_InitTypeDef init = {};
  init.Pin = GPIO_PIN_2 | GPIO_PIN_3;
  init.Mode = GPIO_MODE_AF_PP;
  init.Pull = GPIO_NOPULL;
  init.Speed = GPIO_SPEED_FREQ_LOW;
  init.Alternate = GPIO_AF12_LPUART1;
  HAL_GPIO_Init(GPIOA, &init);

  g_debug_uart_handle.Instance = LPUART1;
  g_debug_uart_handle.Init.BaudRate = kDebugUartBaud;
  g_debug_uart_handle.Init.WordLength = UART_WORDLENGTH_8B;
  g_debug_uart_handle.Init.StopBits = UART_STOPBITS_1;
  g_debug_uart_handle.Init.Parity = UART_PARITY_NONE;
  g_debug_uart_handle.Init.Mode = UART_MODE_TX_RX;
  g_debug_uart_handle.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  g_debug_uart_handle.Init.ClockPrescaler = UART_PRESCALER_DIV1;
  if (HAL_UART_Init(&g_debug_uart_handle) != HAL_OK) {
    Error_Handler();
  }
}

void PrintBootBanner(lhal::Uart& uart) {
  char line[96];
  int len =
      std::snprintf(line, sizeof(line), "\r\nVCU %s (%.12s%s)\r\n",
                    lhre::kBuildInfo.git_describe, lhre::kBuildInfo.git_sha,
                    lhre::kBuildInfo.dirty ? "-dirty" : "");
  if (len > 0) {
    uart.Write(reinterpret_cast<const uint8_t*>(line), static_cast<size_t>(len),
               /*timeout_ms=*/100);
  }
}

// Hook for the future "build info" CAN response frame: 7 ASCII bytes of the
// commit SHA plus a dirty flag. Wire this as the reply payload once the CAN
// request dispatcher lands; when a CAN spec hash is added to lhre::BuildInfo,
// it belongs in (a second frame of) this response too.
[[maybe_unused]] void FillBuildInfoFrame(uint8_t out[8]) {
  std::memcpy(out, lhre::kBuildInfo.git_sha, 7);
  out[7] = lhre::kBuildInfo.dirty ? 1 : 0;
}

}  // namespace

int main() {
  HAL_Init();
  ConfigureSystemClock();
  ConfigureGpio();
  ConfigureDebugUart();

  lhal::stm32::Clock clock;
  lhal::stm32::Gpio status_led(kStatusLedPort, kStatusLedPin);
  lhal::stm32::Uart debug_uart(&g_debug_uart_handle);

  PrintBootBanner(debug_uart);

  vcu::Peripherals peripherals;
  peripherals.clock = &clock;
  peripherals.status_led = &status_led;

  vcu::App app(peripherals);
  while (true) {
    app.Step();
  }
}

extern "C" void Error_Handler(void) {
  __disable_irq();
  while (true) {
  }
}

#ifdef USE_FULL_ASSERT
extern "C" void assert_failed(uint8_t* file, uint32_t line) {
  (void)file;
  (void)line;
  Error_Handler();
}
#endif
