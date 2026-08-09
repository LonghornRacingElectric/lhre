// VCU firmware entry point — hand-written, not CubeMX-generated. Everything
// under Core/ is CubeMX-owned and safe to regenerate (run post_cubemx.sh
// afterwards); everything under Board/ is ours and CubeMX never touches it.
//
// Structure: peripheral bring-up (clocks, pin mux, handles) stays at the ST
// HAL level in this file; application logic lives in App/vcu_app.* against
// LHAL interfaces only, so it also runs on the host (see :vcu_app_test and
// :vcu_sim).

#include "main.h"

#include "lhal/stm32/gpio.hpp"
#include "lhal/stm32/system.hpp"
#include "vcu_app.hpp"

namespace {

// TODO(vcu): point this at the real status LED once the board pinout is
// final. PA5 is the usual Nucleo dev-board LED.
GPIO_TypeDef* const kStatusLedPort = GPIOA;
constexpr uint16_t kStatusLedPin = GPIO_PIN_5;

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

}  // namespace

int main() {
  HAL_Init();
  ConfigureSystemClock();
  ConfigureGpio();

  lhal::stm32::Clock clock;
  lhal::stm32::Gpio status_led(kStatusLedPort, kStatusLedPin);

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
