// VCU Rev A firmware entry point — hand-written, not CubeMX-generated.
// Everything under Core/ is CubeMX-owned; everything under Board/ is ours.
//
// Order matters for safety: reset cause first (before anything clears the
// flags), then clocks, then every output pin into its safe state before
// any peripheral or task can touch it. The application (App/) only ever
// sees LHAL adapters; the vehicle image (vcu::App) and the bring-up test
// image (vcu::TestApp, built as :vcu_test with VCU_TEST_IMAGE) share this
// bring-up.

#include "main.h"

#include <cstring>

#include "FreeRTOS.h"
#include "adc.h"
#include "board.hpp"
#include "board_safe_state.hpp"
#include "dac.h"
#include "dma.h"
#include "fdcan.h"
#include "gpio.h"
#include "lhal/stm32/adc.hpp"
#include "lhal/stm32/can.hpp"
#include "lhal/stm32/dac.hpp"
#include "lhal/stm32/gpio.hpp"
#include "lhal/stm32/init.hpp"
#include "lhal/stm32/pwm.hpp"
#include "lhal/stm32/reset.hpp"
#include "lhal/stm32/system.hpp"
#include "lhal/stm32/uart.hpp"
#include "lhal/stm32/usb_cdc.hpp"
#include "lhal/stm32/watchdog.hpp"
#include "lhre/build_info.hpp"
#include "task.h"
#include "tim.h"
#include "usb_device.h"
#include "vcu_io.hpp"
#ifdef VCU_TEST_IMAGE
#include "vcu_test_app.hpp"
#else
#include "vcu_app.hpp"
#endif

// Defined in the CubeMX-generated USB_Device/App/usb_device.c.
extern USBD_HandleTypeDef hUsbDeviceFS;

namespace {

// RM0440: VREFINT calibration, measured at 3.0 V / 30 degC (12-bit).
const volatile uint16_t* const kVrefintCal =
    reinterpret_cast<const volatile uint16_t*>(0x1FFF75AAu);

class Board final : public vcu::BoardServices {
 public:
  void EnterBootloader() override { vcu_board::EnterBootloader(); }
  uint32_t LoadFaultLatch() override { return vcu_board::LoadFaultLatch(); }
  void SaveFaultLatch(uint32_t value) override {
    vcu_board::SaveFaultLatch(value);
  }
};

// Hook for the future "build info" CAN response frame: 7 ASCII bytes of the
// commit SHA plus a dirty flag.
[[maybe_unused]] void FillBuildInfoFrame(uint8_t out[8]) {
  std::memcpy(out, lhre::kBuildInfo.git_sha, 7);
  out[7] = lhre::kBuildInfo.dirty ? 1 : 0;
}

}  // namespace

int main() {
  // Before HAL_Init: nothing may clear RCC->CSR's reset flags first.
  const lhal::ResetCause reset_cause = lhal::stm32::ReadAndClearResetCause();

  lhal::stm32::InitCore();  // HAL_Init + CubeMX-generated SystemClock_Config

  // Pins: CubeMX drives every output low first, which is safe for
  // Linelock_Ctrl, Amp_Shutdown_SW and the STB pins; BMS/IMD_Error_SW go
  // straight back high (LED on, the board default) — the .ioc should set
  // them high initially (IOC changes needed #9).
  MX_GPIO_Init();
  vcu_board::ForceSafeOutputs();

  // Keep the IWDG frozen while a debugger halts the core.
  DBGMCU->APB1FZR1 |= DBGMCU_APB1FZR1_DBG_IWDG_STOP;

  MX_DMA_Init();
  MX_ADC1_Init();
  MX_ADC2_Init();
  MX_FDCAN1_Init();
  MX_FDCAN2_Init();
  MX_TIM2_Init();
  MX_DAC1_Init();
  vcu_board::ConfigureAnalog();
  vcu_board::ConfigureCan();
  vcu_board::ConfigureDebugUart();

  // Everything the tasks touch must be static: the Cortex-M port reclaims
  // main()'s stack for interrupts when the scheduler starts.
  static lhal::stm32::Clock clock;
  static Board board;

  static lhal::stm32::DmaAdc adc1(&hadc1, vcu_board::g_adc1_buffer,
                                  vcu::kAdc1Channels);
  static lhal::stm32::DmaAdc adc2(&hadc2, vcu_board::g_adc2_buffer,
                                  vcu::kAdc2Channels);
  if (!lhal::IsOk(adc1.Start()) || !lhal::IsOk(adc2.Start())) {
    Error_Handler();
  }
  vcu_board::StartAdcTrigger();

  static lhal::stm32::DmaDac audio_dac(&hdac1, DAC_CHANNEL_1,
                                       &vcu_board::htim7);

  // Critical = FDCAN2 (PB12/PB13), Data = FDCAN1 (PD0/PD1) on Rev A.
  static lhal::stm32::Can critical_can(&hfdcan2);
  static lhal::stm32::Can data_can(&hfdcan1);
  if (!lhal::IsOk(critical_can.Start()) || !lhal::IsOk(data_can.Start())) {
    Error_Handler();
  }

  // Status RGB on TIM2 CH1-3, OC polarity low (common anode): duty is
  // brightness. TODO(verify) D35's pinout and colour order on the bench.
  static lhal::stm32::Pwm led_g(&htim2, TIM_CHANNEL_1);  // PA0 LED_G
  static lhal::stm32::Pwm led_r(&htim2, TIM_CHANNEL_2);  // PA1 LED_R
  static lhal::stm32::Pwm led_b(&htim2, TIM_CHANNEL_3);  // PA2 LED_B
  led_r.Start();
  led_g.Start();
  led_b.Start();

  static lhal::stm32::Uart debug_uart(&vcu_board::huart3);
  static lhal::stm32::UsbCdc usb(&hUsbDeviceFS);  // before MX_USB_Device_Init
  MX_USB_Device_Init();
  static lhal::stm32::Iwdg watchdog;

  static lhal::stm32::Gpio b1_disconnect(GPIOD, GPIO_PIN_13);
  static lhal::stm32::Gpio b1_overvoltage(GPIOD, GPIO_PIN_11);
  static lhal::stm32::Gpio b2_disconnect(GPIOD, GPIO_PIN_14);
  static lhal::stm32::Gpio b2_overvoltage(GPIOD, GPIO_PIN_15);
  static lhal::stm32::Gpio hard_braking(GPIOE, GPIO_PIN_7);
  static lhal::stm32::Gpio combined_condition(GPIOE, GPIO_PIN_8);
  static lhal::stm32::Gpio fault_confirmed(GPIOE, GPIO_PIN_9);
  static lhal::stm32::Gpio latch_state(GPIOE, GPIO_PIN_10);
  static lhal::stm32::Gpio linelock_out(GPIOD, GPIO_PIN_10);
  static lhal::stm32::Gpio motor_power(GPIOD, GPIO_PIN_12);
  static lhal::stm32::Gpio motor_disconnect(GPIOE, GPIO_PIN_6);
  static lhal::stm32::Gpio rtd_switch(GPIOE, GPIO_PIN_11);
  static lhal::stm32::Gpio linelock_ctrl(GPIOE, GPIO_PIN_15);
  static lhal::stm32::Gpio amp_shutdown(GPIOE, GPIO_PIN_14);
  static lhal::stm32::Gpio bms_error(GPIOE, GPIO_PIN_12);
  static lhal::stm32::Gpio imd_error(GPIOE, GPIO_PIN_13);
  static lhal::stm32::Gpio crit_stb(GPIOD, GPIO_PIN_9);
  static lhal::stm32::Gpio data_stb(GPIOD, GPIO_PIN_8);

  vcu::Peripherals p;
  p.clock = &clock;
  p.critical_can = &critical_can;
  p.data_can = &data_can;
  p.debug_uart = &debug_uart;
  p.usb_uart = &usb;
  p.adc1 = &adc1;
  p.adc2 = &adc2;
  p.audio_dac = &audio_dac;
  p.watchdog = &watchdog;
  p.led_r = &led_r;
  p.led_g = &led_g;
  p.led_b = &led_b;
  p.inputs.b1_disconnect = &b1_disconnect;
  p.inputs.b1_overvoltage = &b1_overvoltage;
  p.inputs.b2_disconnect = &b2_disconnect;
  p.inputs.b2_overvoltage = &b2_overvoltage;
  p.inputs.hard_braking = &hard_braking;
  p.inputs.combined_condition = &combined_condition;
  p.inputs.fault_confirmed = &fault_confirmed;
  p.inputs.latch_state = &latch_state;
  p.inputs.linelock_out = &linelock_out;
  p.inputs.motor_power = &motor_power;
  p.inputs.motor_disconnect = &motor_disconnect;
  p.inputs.ready_to_drive_switch = &rtd_switch;
  p.outputs.linelock_ctrl = &linelock_ctrl;
  p.outputs.amp_shutdown = &amp_shutdown;
  p.outputs.bms_error_led = &bms_error;
  p.outputs.imd_error_led = &imd_error;
  p.outputs.crit_stb = &crit_stb;
  p.outputs.data_stb = &data_stb;
  p.board = &board;
  p.reset_cause = reset_cause;
  p.vrefint_cal = *kVrefintCal;

#ifdef VCU_TEST_IMAGE
  static vcu::TestApp app(p);
#else
  static vcu::App app(p);
#endif
  app.StartTasks();
  vTaskStartScheduler();

  Error_Handler();  // the scheduler only returns if it failed to start
}

// Error_Handler() and assert_failed() come from the CubeMX-generated
// Core/Src/main.c; customize them there inside USER CODE sections.
