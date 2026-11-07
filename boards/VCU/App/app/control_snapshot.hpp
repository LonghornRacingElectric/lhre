#pragma once

#include <cstdint>

#include "config/params.hpp"
#include "control/battery.hpp"
#include "control/pedals.hpp"
#include "control/steering.hpp"
#include "control/torque.hpp"
#include "control/wheel_slip.hpp"
#include "io/analog.hpp"
#include "io/digital_io.hpp"
#include "lhre_can_types.hpp"
#include "safety/state_machine.hpp"

namespace vcu {

// Control owns live state; telemetry and consoles read a mutex-protected copy.
struct ControlSnapshot {
  uint32_t now_ms = 0;
  VcuState state = VcuState::kInit;
  const char* last_transition = "boot";
  uint32_t faults = 0;
  lhre::can::VcuFaultCode primary_fault = lhre::can::VcuFaultCode::kNone;
  lhre::can::EventMode event_mode = kDefaultEventMode;
  AnalogReadings analog;
  PedalOutputs pedals;
  SteeringOutputs steering;
  float brake_bias = 0.0f;
  lhre::can::ShutdownLoopState loop = lhre::can::ShutdownLoopState::kUnknown;
  DigitalInputs inputs;
  DigitalOutputs outputs;
  float motor_rpm = 0.0f;
  bool motor_speed_valid = false;
  bool adc_fresh = false;
  TorqueOutputs torque;
  float torque_command = 0.0f;
  bool inverter_enable = false;
  BatteryOutputs battery;
  bool buzzer = false;
  bool linelock_request = false;
  bool brake_flags_latched = false;
  uint8_t fault_resets = 0;
  float bse3_psi = 0.0f;
  bool bse3_available = false;
  bool launch_armed = false;
  bool regen_current_cut = false;
  WheelControlResult wheel_control;
};

}  // namespace vcu
