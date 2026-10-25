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

int main() {
  lhal::stm32::InitCore();
  MX_GPIO_Init();
  vcu_board::ForceSafeOutputs();
  MX_TIM2_Init();

  // Rev A: PA0 green, PA1 red, PA2 blue; active-low PWM is set by the IOC.
  static lhal::stm32::Pwm green(&htim2, TIM_CHANNEL_1);
  static lhal::stm32::Pwm red(&htim2, TIM_CHANNEL_2);
  static lhal::stm32::Pwm blue(&htim2, TIM_CHANNEL_3);
  if (!lhal::IsOk(red.Start()) || !lhal::IsOk(green.Start()) ||
      !lhal::IsOk(blue.Start())) {
    Error_Handler();
  }
  static longhorn::RgbLed led(&red, &green, &blue);
  static lhal::stm32::Clock clock;
  static lhal::stm32::UsbCdc usb(&hUsbDeviceFS);
  // Install the recovery observer before USB can receive its first packet.
  static vcu::Mvp app(&clock, &usb, &led, &vcu_board::EnterBootloader);
  MX_USB_Device_Init();

  for (;;) {
    app.Step();
    clock.DelayMs(1);
  }
}

#endif  // VCU_MVP_IMAGE
