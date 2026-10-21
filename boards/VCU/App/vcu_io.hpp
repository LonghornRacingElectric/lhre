#pragma once

// The contract between Board/ (which wires pins and ADC ranks) and App/
// (which interprets them). Pin names follow the Rev A netlist.

#include <cstddef>
#include <cstdint>

#include "lhal/lhal.hpp"
#include "rgb_led.hpp"
#include "vcu_adc.hpp"
#include "lhal/uart.hpp"

namespace vcu {

// ADC1 scan ranks (timer-triggered, 16x oversampled, circular DMA).
enum Adc1Rank : size_t {
  kAdc1Apps1 = 0,        // PC0 APPS1_Filtered, ADC1_IN6
  kAdc1Apps2 = 1,        // PC1 APPS2_Filtered, ADC1_IN7
  kAdc1Bse1 = 2,         // PC2 BSE1_Filtered, ADC1_IN8
  kAdc1Bse2 = 3,         // PC3 BSE2_Filtered, ADC1_IN9
  kAdc1ShutdownOut = 4,  // PA3 Shutdown_out_S, ADC1_IN4
  kAdc1Vrefint = 5,      // VREFINT
  kAdc1Channels = 6,
};

// ADC2 scan ranks.
enum Adc2Rank : size_t {
  kAdc2Steering = 0,    // PC4 Steering_Filtered, ADC2_IN5
  kAdc2ShutdownIn = 1,  // PC5 Shutdown_in_S, ADC2_IN11
  kAdc2Channels = 2,
};

// Digital inputs as raw pin levels; App applies polarity.
struct DigitalInputPins {
  lhal::Gpio* b1_disconnect = nullptr;          // PD13, high = asserted
  lhal::Gpio* b1_overvoltage = nullptr;         // PD11
  lhal::Gpio* b2_disconnect = nullptr;          // PD14
  lhal::Gpio* b2_overvoltage = nullptr;         // PD15
  lhal::Gpio* hard_braking = nullptr;           // PE7 Hard_braking_mcu
  lhal::Gpio* combined_condition = nullptr;     // PE8
  lhal::Gpio* fault_confirmed = nullptr;        // PE9
  lhal::Gpio* latch_state = nullptr;            // PE10, high = BSPD latched
  lhal::Gpio* linelock_out = nullptr;           // PD10
  lhal::Gpio* motor_power = nullptr;            // PD12
  lhal::Gpio* motor_disconnect = nullptr;       // PE6
  lhal::Gpio* ready_to_drive_switch = nullptr;  // PE11, LOW = pressed
};

struct DigitalOutputPins {
  lhal::Gpio* linelock_ctrl = nullptr;  // PE15, high = request
  lhal::Gpio* amp_shutdown = nullptr;   // PE14, high = amp enabled
  lhal::Gpio* bms_error_led = nullptr;  // PE12, high = LED on
  lhal::Gpio* imd_error_led = nullptr;  // PE13, high = LED on
  lhal::Gpio* crit_stb = nullptr;       // PD9, high = standby
  lhal::Gpio* data_stb = nullptr;       // PD8, high = standby
};

// Board hooks that don't fit a generic LHAL peripheral.
class BoardServices {
 public:
  virtual ~BoardServices() = default;

  // Drives BOOT0trig and resets into the ST ROM bootloader. Does not return
  // on target.
  virtual void EnterBootloader() = 0;

  // Flash writes may block; save from telemetry, never control.
  virtual uint32_t LoadFaultLatch() = 0;
  virtual void SaveFaultLatch(uint32_t value) = 0;
};

// Board-provided peripherals; clock is required, optional devices may be null.
struct Peripherals {
  lhal::Clock* clock = nullptr;
  lhal::CanBus* critical_can = nullptr;   // FDCAN2 (PB12/PB13)
  lhal::CanBus* data_can = nullptr;       // FDCAN1 (PD0/PD1)
  lhal::Uart* debug_uart = nullptr;       // STDC14 VCP (USART3, PC10/PC11)
  lhal::Uart* usb_uart = nullptr;  // USB CDC
  vcu::AdcScan* adc1 = nullptr;           // ranks per Adc1Rank
  vcu::AdcScan* adc2 = nullptr;           // ranks per Adc2Rank
  lhal::WaveformDac* audio_dac = nullptr;
  lhal::Watchdog* watchdog = nullptr;
  lhal::Pwm* led_r = nullptr;  // status RGB, duty = brightness
  lhal::Pwm* led_g = nullptr;
  lhal::Pwm* led_b = nullptr;
  vcu::RgbLed* status_led = nullptr;
  DigitalInputPins inputs;
  DigitalOutputPins outputs;
  BoardServices* board = nullptr;
  lhal::ResetCause reset_cause = lhal::ResetCause::kUnknown;
  // Factory VREFINT calibration count (12-bit at 3.0 V); 0 = unknown.
  uint16_t vrefint_cal = 0;
};

}  // namespace vcu
