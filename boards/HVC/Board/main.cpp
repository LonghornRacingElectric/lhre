// HVC firmware entry point — hand-written, not CubeMX-generated.
// Everything under Core/ is CubeMX-owned and safe to regenerate; everything
// under Board/ is ours and CubeMX never touches it. Application logic lives
// in App/hvc_app.* against LHAL interfaces only, so it also runs on the
// host (see :hvc_app_test and :hvc_sim). boards/VCU/Board/main.cpp
// shows fuller bring-up: debug UART, CAN, boot banner.

#include "main.h"

#include "FreeRTOS.h"
#include "task.h"
#include "adbms_chain.hpp"
#include "adc.h"  // CubeMX: hadc2 + MX_ADC2_Init()
#include "diff_adc.hpp"
#include "gpio.h"  // CubeMX: MX_GPIO_Init() — pin modes + initial levels
#include "lhal/stm32/gpio.hpp"
#include "lhal/stm32/init.hpp"
#include "lhal/stm32/pwm.hpp"
#include "lhal/stm32/system.hpp"
#include "lhal/stm32/usb_cdc.hpp"
#include "spi.h"         // CubeMX: hspi2/hspi4 + MX_SPI2_Init/MX_SPI4_Init
#include "spi_register_bus.hpp"
#include "tim.h"         // CubeMX: htim2 + MX_TIM2_Init()
#include "usb_device.h"  // CubeMX: MX_USB_Device_Init()
#include "hvc_app.hpp"

extern "C" USBD_HandleTypeDef hUsbDeviceFS;  // defined in usb_device.c

static lhal::stm32::UsbCdc* g_stdout = nullptr;

// printf -> USB-C terminal, for the ADBMS library's own prints (the 2026
// LVBMS did this with __io_putchar). Overrides Core/Src/syscalls.c's weak
// _write, which would call an __io_putchar nobody defines.
extern "C" int _write(int, char* ptr, int len) {
  if (g_stdout != nullptr) {
    g_stdout->Write(reinterpret_cast<const uint8_t*>(ptr),
                    static_cast<size_t>(len), 100);
  }
  return len;
}

int main() {
  lhal::stm32::InitCore();  // HAL_Init + CubeMX-generated SystemClock_Config
  MX_GPIO_Init();
  MX_TIM2_Init();  // RGB LED PWM: CH1 red (PA0), CH2 green (PA1), CH3 blue (PA2)
  MX_SPI2_Init();  // BMB chain (ADBMS6830 via isoSPI); CS on PB13, idle high
  MX_SPI4_Init();  // IMU (ASM330LHB); CS on PE11, idle high from MX_GPIO_Init
  MX_ADC2_Init();  // pack+ sense, differential (see pack_sense.hpp)

  // Everything the tasks touch must be static: the Cortex-M port reclaims
  // main()'s stack for interrupts when the scheduler starts.
  static lhal::stm32::Clock clock;
  static lhal::stm32::Pwm led_red(&htim2, TIM_CHANNEL_1);
  static lhal::stm32::Pwm led_green(&htim2, TIM_CHANNEL_2);
  static lhal::stm32::Pwm led_blue(&htim2, TIM_CHANNEL_3);
  led_red.Start();
  led_green.Start();
  led_blue.Start();

  // Debug shell over USB-C. UsbCdc must exist before MX_USB_Device_Init so
  // reception is routed from the first packet.
  static lhal::stm32::UsbCdc usb(&hUsbDeviceFS);
  MX_USB_Device_Init();
  g_stdout = &usb;

  // Shutdown sense, high = leg live.
  static lhal::stm32::Gpio leg2(GPIOC, GPIO_PIN_12);
  static lhal::stm32::Gpio leg3(GPIOD, GPIO_PIN_12);
  static lhal::stm32::Gpio leg4(GPIOE, GPIO_PIN_0);
  static lhal::stm32::Gpio leg5(GPIOD, GPIO_PIN_14);
  static lhal::stm32::Gpio leg15(GPIOD, GPIO_PIN_15);

  static lhal::stm32::Gpio air_minus(GPIOE, GPIO_PIN_1);
  static lhal::stm32::Gpio air_plus_closed(GPIOD, GPIO_PIN_9);
  static lhal::stm32::Gpio air_minus_closed(GPIOD, GPIO_PIN_10);
  static lhal::stm32::Gpio bms_error(GPIOE, GPIO_PIN_2);
  static lhal::stm32::Gpio imd_error(GPIOD, GPIO_PIN_6);
  static lhal::stm32::Gpio lamp_test(GPIOA, GPIO_PIN_4);
  static hvc::board::SpiRegisterBus imu(&hspi4, GPIOE, GPIO_PIN_11);
  static hvc::board::AdbmsChain bms;
  // PC5 = ADC2_IN11 (+), PB2 = ADC2_IN12 (-).
  static hvc::board::StmDiffAdc pack_sense(&hadc2, ADC_CHANNEL_11);
  const bool pack_sense_ok = pack_sense.Init();
  // PA6 = ADC2_IN3 (+), PA7 = ADC2_IN4 (-): pack current shunt.
  static hvc::board::StmDiffAdc current_sense(&hadc2, ADC_CHANNEL_3);
  const bool current_sense_ok = current_sense.Init();

  hvc::Peripherals peripherals;
  peripherals.clock = &clock;
  peripherals.console = &usb;
  peripherals.led_red = &led_red;
  peripherals.led_green = &led_green;
  peripherals.led_blue = &led_blue;
  peripherals.leg2 = &leg2;
  peripherals.leg3 = &leg3;
  peripherals.leg4 = &leg4;
  peripherals.leg5 = &leg5;
  peripherals.leg15 = &leg15;
  peripherals.air_minus = &air_minus;
  peripherals.air_plus_closed = &air_plus_closed;
  peripherals.air_minus_closed = &air_minus_closed;
  peripherals.bms_error = &bms_error;
  peripherals.imd_error = &imd_error;
  peripherals.lamp_test = &lamp_test;
  peripherals.imu = &imu;
  peripherals.bms = &bms;
  peripherals.pack_sense = pack_sense_ok ? &pack_sense : nullptr;
  peripherals.current_sense = current_sense_ok ? &current_sense : nullptr;

  static hvc::App app(peripherals);
  app.StartTasks();
  vTaskStartScheduler();

  Error_Handler();  // the scheduler only returns if it failed to start
}

// Error_Handler() and assert_failed() come from the CubeMX-generated
// Core/Src/main.c; customize them there inside USER CODE sections.
