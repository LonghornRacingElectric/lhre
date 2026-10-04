#pragma once

#include "vcu_io.hpp"

namespace vcu {

// Digital inputs with polarity applied: true = asserted.
struct DigitalInputs {
  bool b1_disconnect = false;
  bool b1_overvoltage = false;
  bool b2_disconnect = false;
  bool b2_overvoltage = false;
  bool hard_braking = false;
  bool combined_condition = false;
  bool fault_confirmed = false;
  bool latch_state = false;
  bool linelock_out = false;
  bool motor_power = false;
  bool motor_disconnect = false;
  bool rtd_pressed = false;
};

// Output levels: true = pin high (see DigitalOutputPins for meaning).
struct DigitalOutputs {
  bool linelock_ctrl = false;
  bool amp_enable = false;
  bool bms_led = true;
  bool imd_led = true;
  bool crit_stb = false;
  bool data_stb = false;
};

// No line lock request, amp off, BMS/IMD LEDs on (the board's own default
// when the MCU is dead), both transceivers in normal mode.
constexpr DigitalOutputs SafeOutputs() { return DigitalOutputs{}; }

inline bool ReadPin(const lhal::Gpio* pin) {
  return pin != nullptr && pin->Read();
}

inline DigitalInputs ReadDigitalInputs(const DigitalInputPins& p) {
  DigitalInputs in;
  in.b1_disconnect = ReadPin(p.b1_disconnect);
  in.b1_overvoltage = ReadPin(p.b1_overvoltage);
  in.b2_disconnect = ReadPin(p.b2_disconnect);
  in.b2_overvoltage = ReadPin(p.b2_overvoltage);
  in.hard_braking = ReadPin(p.hard_braking);
  in.combined_condition = ReadPin(p.combined_condition);
  in.fault_confirmed = ReadPin(p.fault_confirmed);
  in.latch_state = ReadPin(p.latch_state);
  in.linelock_out = ReadPin(p.linelock_out);
  in.motor_power = ReadPin(p.motor_power);
  in.motor_disconnect = ReadPin(p.motor_disconnect);
  // RTDS_Top has a pull-up and the switch shorts it to RTDS_Bottom (GND).
  // A missing pin reads as released.
  in.rtd_pressed =
      p.ready_to_drive_switch != nullptr && !p.ready_to_drive_switch->Read();
  return in;
}

inline void WritePin(lhal::Gpio* pin, bool level) {
  if (pin != nullptr) {
    pin->Write(level);
  }
}

inline void WriteDigitalOutputs(const DigitalOutputPins& p,
                                const DigitalOutputs& out) {
  WritePin(p.linelock_ctrl, out.linelock_ctrl);
  WritePin(p.amp_shutdown, out.amp_enable);
  WritePin(p.bms_error_led, out.bms_led);
  WritePin(p.imd_error_led, out.imd_led);
  WritePin(p.crit_stb, out.crit_stb);
  WritePin(p.data_stb, out.data_stb);
}

}  // namespace vcu
