#ifdef VCU_MVP_IMAGE

#include "board.hpp"
#include "board_safe_state.hpp"
#include "gpio.h"
#include "lhal/stm32/init.hpp"
#include "lhal/stm32/pwm.hpp"
#include "lhal/stm32/system.hpp"
#include "lhal/stm32/usb_cdc.hpp"
#include "longhorn/led.hpp"
#include "mvp.hpp"
#include "tim.h"
#include "usb_device.h"

extern USBD_HandleTypeDef hUsbDeviceFS;

namespace {
// Read the actual output configuration so bench diagnosis does not depend on
// inferring register state from successful HAL calls.
void PwmStatus(void*, longhorn::Console& out, const char*) {
  out.Printf("TIM2 CR1=%08lx CNT=%lu ARR=%lu CCER=%08lx",
             static_cast<unsigned long>(TIM2->CR1),
             static_cast<unsigned long>(TIM2->CNT),
             static_cast<unsigned long>(TIM2->ARR),
             static_cast<unsigned long>(TIM2->CCER));
  out.Printf("CCR1=%lu CCR2=%lu CCR3=%lu",
             static_cast<unsigned long>(TIM2->CCR1),
             static_cast<unsigned long>(TIM2->CCR2),
             static_cast<unsigned long>(TIM2->CCR3));
  out.Printf(
      "GPIOA MODER=%08lx AFRL=%08lx ODR=%08lx TIM2CLK=%lu",
      static_cast<unsigned long>(GPIOA->MODER),
      static_cast<unsigned long>(GPIOA->AFR[0]),
      static_cast<unsigned long>(GPIOA->ODR),
      static_cast<unsigned long>((RCC->APB1ENR1 & RCC_APB1ENR1_TIM2EN) != 0));
}
}  // namespace

int main() {
  lhal::stm32::InitCore();
  MX_GPIO_Init();
  vcu_board::ForceSafeOutputs();
  MX_TIM2_Init();

  // D35 rotated 180 degrees for common-anode wiring: PA0 blue, PA2 green.
  static lhal::stm32::Pwm blue(&htim2, TIM_CHANNEL_1);
  static lhal::stm32::Pwm red(&htim2, TIM_CHANNEL_2);
  static lhal::stm32::Pwm green(&htim2, TIM_CHANNEL_3);
  if (!lhal::IsOk(red.Start()) || !lhal::IsOk(green.Start()) ||
      !lhal::IsOk(blue.Start())) {
    Error_Handler();
  }
  static longhorn::RgbLed led(&red, &green, &blue);
  static lhal::stm32::Clock clock;
  static lhal::stm32::UsbCdc usb(&hUsbDeviceFS);
  // Install the recovery observer before USB can receive its first packet.
  static vcu::Mvp app(&clock, &usb, &led, &vcu_board::EnterBootloader);
  app.AddCommand(
      {"pwm", "TIM2 and LED pin register status", &PwmStatus, nullptr});
  MX_USB_Device_Init();

  for (;;) {
    app.Step();
    clock.DelayMs(1);
  }
}

#endif  // VCU_MVP_IMAGE
