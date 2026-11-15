// DUI firmware entry point — hand-written, not CubeMX-generated.
// Everything under Core/ and USB_Device/ is CubeMX-owned and safe to
// regenerate; everything under Board/ is ours and CubeMX never touches it.
// Application logic lives in App/ against LHAL interfaces only, so it also
// runs on the host (see :dui_app_test, :driver_inputs_test, :dui_sim).

#include "main.h"

#include "FreeRTOS.h"
#include "driver_inputs.hpp"
#include "dui_app.hpp"
#include "gpio.h"
#include "lhal/stm32/gpio.hpp"
#include "lhal/stm32/init.hpp"
#include "lhal/stm32/pwm.hpp"
#include "lhal/stm32/system.hpp"
#include "lhal/stm32/usb_cdc.hpp"
#include "task.h"
#include "tim.h"
#include "usb_device.h"

// Defined by the CubeMX-generated USB_Device/App/usb_device.c, which
// doesn't declare it in its header.
extern "C" USBD_HandleTypeDef hUsbDeviceFS;

namespace {

// RGB LED D11 runs on TIM2, configured in DUI.ioc (PWM generation, CubeMX's
// default active-high polarity; RgbLed inverts for the common-anode LED).
// Channels follow the schematic's net names:
//   PA1 = TIM2_CH2 = LED_R,  PA0 = TIM2_CH1 = LED_G,  PA2 = TIM2_CH3 = LED_B
// Rev A caveat: D11's footprint assumes a common-anode part (anodes on pins
// 1-3), but the fitted part has independent dies (B 1-2, R 3-4, G 5-6), so
// the on-board LED can't light (see README). The pins themselves are right
// and verified toggling; a bodge LED from 3.3 V to each resistor's MCU end
// shows the rainbow. If green and blue look swapped on the fixed board,
// swap the two channels below.
constexpr uint32_t kLedRedChannel = TIM_CHANNEL_2;
constexpr uint32_t kLedGreenChannel = TIM_CHANNEL_1;
constexpr uint32_t kLedBlueChannel = TIM_CHANNEL_3;

// Driver inputs, from the Rev A schematic (MCU + Switches sheets), indexed
// by dui::Input. All active-high with an external 10 kΩ pull-down and
// 100 nF to GND, so the MCU pins need no internal pull.
struct InputPin {
  GPIO_TypeDef* port;
  uint16_t pin;
};
const InputPin kInputPins[dui::kInputCount] = {
    {GPIOB, GPIO_PIN_15},  // kButton1          SW1
    {GPIOB, GPIO_PIN_14},  // kButton2          SW2
    {GPIOB, GPIO_PIN_2},   // kTopLeftUp        SW5
    {GPIOA, GPIO_PIN_15},  // kTopLeftDown      SW5
    {GPIOB, GPIO_PIN_0},   // kTopRightUp       SW8
    {GPIOB, GPIO_PIN_1},   // kTopRightDown     SW8
    {GPIOA, GPIO_PIN_6},   // kBottomLeftUp     SW6
    {GPIOA, GPIO_PIN_5},   // kBottomLeftDown   SW6
    {GPIOA, GPIO_PIN_8},   // kBottomRightUp    SW7
    {GPIOA, GPIO_PIN_7},   // kBottomRightDown  SW7
};

// Not in the .ioc, so configured at the ST HAL level here. NOPULL matters
// for PA15: it comes out of reset as JTDI with an internal pull-up, which
// would fight the 10 kΩ pull-down and read pressed forever.
void ConfigureInputs() {
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  for (const InputPin& in : kInputPins) {
    GPIO_InitTypeDef init = {};
    init.Pin = in.pin;
    init.Mode = GPIO_MODE_INPUT;
    init.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(in.port, &init);
  }
}

}  // namespace

int main() {
  lhal::stm32::InitCore();  // HAL_Init + CubeMX-generated SystemClock_Config
  MX_GPIO_Init();
  MX_TIM2_Init();  // also muxes PA0-PA2 to TIM2 (HAL_TIM_MspPostInit)
  ConfigureInputs();

  // Everything the tasks touch must be static: the Cortex-M port reclaims
  // main()'s stack for interrupts when the scheduler starts.
  static lhal::stm32::Clock clock;
  static lhal::stm32::Pwm led_red(&htim2, kLedRedChannel);
  static lhal::stm32::Pwm led_green(&htim2, kLedGreenChannel);
  static lhal::stm32::Pwm led_blue(&htim2, kLedBlueChannel);
  static lhal::stm32::Gpio inputs[dui::kInputCount] = {
      lhal::stm32::Gpio(kInputPins[0].port, kInputPins[0].pin),
      lhal::stm32::Gpio(kInputPins[1].port, kInputPins[1].pin),
      lhal::stm32::Gpio(kInputPins[2].port, kInputPins[2].pin),
      lhal::stm32::Gpio(kInputPins[3].port, kInputPins[3].pin),
      lhal::stm32::Gpio(kInputPins[4].port, kInputPins[4].pin),
      lhal::stm32::Gpio(kInputPins[5].port, kInputPins[5].pin),
      lhal::stm32::Gpio(kInputPins[6].port, kInputPins[6].pin),
      lhal::stm32::Gpio(kInputPins[7].port, kInputPins[7].pin),
      lhal::stm32::Gpio(kInputPins[8].port, kInputPins[8].pin),
      lhal::stm32::Gpio(kInputPins[9].port, kInputPins[9].pin),
  };

  // USB serial console. UsbCdc must exist before MX_USB_Device_Init so the
  // first packets are routed to it (see lhal/stm32/usb_cdc.hpp).
  static lhal::stm32::UsbCdc console(&hUsbDeviceFS);
  MX_USB_Device_Init();

  dui::Peripherals peripherals;
  peripherals.clock = &clock;
  peripherals.led_red = &led_red;
  peripherals.led_green = &led_green;
  peripherals.led_blue = &led_blue;
  peripherals.led_active_low = true;
  for (size_t i = 0; i < dui::kInputCount; ++i) {
    peripherals.inputs[i] = &inputs[i];
  }
  peripherals.console = &console;

  // Construct the app (which sets every LED channel to "off") before
  // starting the outputs: a channel started at CubeMX's default 0% duty
  // would hold its pin low, i.e. fully lit.
  static dui::App app(peripherals);
  if (!lhal::IsOk(led_red.Start()) || !lhal::IsOk(led_green.Start()) ||
      !lhal::IsOk(led_blue.Start())) {
    Error_Handler();
  }

  app.StartTasks();
  vTaskStartScheduler();

  Error_Handler();  // the scheduler only returns if it failed to start
}

// Error_Handler() and assert_failed() come from the CubeMX-generated
// Core/Src/main.c; customize them there inside USER CODE sections.
