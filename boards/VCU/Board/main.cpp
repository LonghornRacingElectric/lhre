// VCU firmware entry point — hand-written, not CubeMX-generated. Everything
// under Core/ is CubeMX-owned and safe to regenerate; everything under
// Board/ is ours and CubeMX never touches it.
//
// Structure: clock config, Error_Handler(), and (once peripherals are added
// in the .ioc) the MX_*_Init() functions come from the CubeMX-generated
// Core/ sources — lhal::stm32::InitCore() runs the clock config. Pin mux
// and handles not yet in the .ioc are configured at the ST HAL level here.
// Application logic lives in App/vcu_app.* against LHAL interfaces only, so
// it also runs on the host (see :vcu_app_test and :vcu_sim).

#include "main.h"

#include <cstdio>
#include <cstring>

#include "FreeRTOS.h"
#include "lhal/stm32/gpio.hpp"
#include "lhal/stm32/init.hpp"
#include "lhal/stm32/system.hpp"
#include "lhal/stm32/uart.hpp"
#include "lhre/build_info.hpp"
#include "task.h"
#include "vcu_app.hpp"

namespace {

// TODO(vcu): point this at the real status LED once the board pinout is
// final. PA5 is the usual Nucleo dev-board LED.
GPIO_TypeDef* const kStatusLedPort = GPIOA;
constexpr uint16_t kStatusLedPin = GPIO_PIN_5;

// Debug UART — only compiled once the UART module is enabled, i.e. once the
// peripheral is added in CubeMX (hal_conf.h is CubeMX-owned and mirrors the
// .ioc). TODO(vcu): add LPUART1 to VCU.ioc — with per-peripheral file
// generation on, CubeMX will emit MX_LPUART1_UART_Init() + the hlpuart1
// handle, and this hand-rolled init can be replaced by those.
// PA2/PA3 is the ST-LINK virtual COM port on the Nucleo dev board.
void ConfigureGpio() {
  __HAL_RCC_GPIOA_CLK_ENABLE();

  GPIO_InitTypeDef init = {};
  init.Pin = kStatusLedPin;
  init.Mode = GPIO_MODE_OUTPUT_PP;
  init.Pull = GPIO_NOPULL;
  init.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(kStatusLedPort, &init);
}

#ifdef HAL_UART_MODULE_ENABLED
UART_HandleTypeDef g_debug_uart_handle;
constexpr uint32_t kDebugUartBaud = 115200;

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
#endif  // HAL_UART_MODULE_ENABLED

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
  lhal::stm32::InitCore();  // HAL_Init + CubeMX-generated SystemClock_Config
  ConfigureGpio();

  // Everything the tasks touch must be static: the Cortex-M port reclaims
  // main()'s stack for interrupts when the scheduler starts.
  static lhal::stm32::Clock clock;
  static lhal::stm32::Gpio status_led(kStatusLedPort, kStatusLedPin);

  vcu::Peripherals peripherals;
  peripherals.clock = &clock;
  peripherals.status_led = &status_led;

#ifdef HAL_UART_MODULE_ENABLED
  ConfigureDebugUart();
  static lhal::stm32::Uart debug_uart(&g_debug_uart_handle);
  PrintBootBanner(debug_uart);
  peripherals.debug_uart = &debug_uart;
#endif

  static vcu::App app(peripherals);
  app.StartTasks();
  vTaskStartScheduler();

  Error_Handler();  // the scheduler only returns if it failed to start
}

// Error_Handler() and assert_failed() come from the CubeMX-generated
// Core/Src/main.c; customize them there inside USER CODE sections.
