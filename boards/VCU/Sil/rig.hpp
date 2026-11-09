#pragma once

// The unmodified vcu::App on the host bench, played by a car model: physical
// quantities in, the inverter torque command out. Sil/README.md has the
// timing and signal contract. Host only.

#include <cstdint>
#include <memory>
#include <string>

#include "app/control_snapshot.hpp"
#include "lhre_can_types.hpp"

namespace vcu {

class App;
namespace bench {
struct Bench;
}

namespace sil {

// Held constant for a whole Advance(). Driver and rest-of-car values must be
// in range (vcu_sil.h documents the ranges); car-model values are clamped to
// what their sensor or CAN field carries.
struct RigInputs {
  // Driver
  float accelerator_travel = 0.0f;  // 0..1, both APPS
  float brake_front_psi = 0.0f;     // BSE1
  float brake_rear_psi = 0.0f;      // BSE2, and the PDU's BSE3
  float steering_angle_deg = 0.0f;  // steering column
  bool rtd_pressed = false;
  // Rest of the car
  lhre::can::HvcState hvc_state = lhre::can::HvcState::kNotEnergized;
  bool contactors_closed = false;
  float shutdown_loop_v = 0.0f;
  // Car model
  float wheel_speed_rad_s[4] = {};  // FL, FR, RL, RR, signed
  float motor_speed_rad_s = 0.0f;
  float motor_torque_nm = 0.0f;
  float dc_bus_voltage_v = 0.0f;
  float dc_bus_current_a = 0.0f;  // positive discharging
  float min_cell_v = 0.0f;
  float max_cell_v = 0.0f;
};

// Bits of RigOutputs::saturated, equal to vcu_sil.h's VCU_SIL_SAT_*.
enum Saturation : uint32_t {
  kSatWheelSpeed = 1u << 0,
  kSatMotorSpeed = 1u << 1,
  kSatMotorTorque = 1u << 2,
  kSatBusVoltage = 1u << 3,
  kSatBusCurrent = 1u << 4,
  kSatCellVoltage = 1u << 5,
  kSatSteering = 1u << 6,
};

// INVERTER_TORQUE_COMMAND as the inverter decodes it.
struct TorqueCommand {
  bool enable = false;
  bool direction = false;
  float torque_request_nm = 0.0f;
  float torque_limit_nm = 0.0f;
};

struct RigOutputs {
  uint32_t now_ms = 0;
  uint32_t torque_frames = 0;  // frames received during this advance
  TorqueCommand command;       // the last of them
  uint32_t saturated = 0;
  ControlSnapshot snapshot;  // as published by the last control tick
};

class Rig {
 public:
  explicit Rig(lhre::can::EventMode mode = kDefaultEventMode);
  ~Rig();
  Rig(const Rig&) = delete;
  Rig& operator=(const Rig&) = delete;

  // Runs `ms` 1 ms passes with `in` held: ADC scans complete, frames due at
  // that ms go out, App::Step() runs, then every host buffer is drained.
  RigOutputs Advance(const RigInputs& in, uint32_t ms);

  // Firmware console (debug UART) text since the last call. At most
  // kConsoleLimit bytes are kept; older text is dropped first.
  static constexpr size_t kConsoleLimit = 1u << 20;
  std::string TakeConsole();
  uint64_t console_dropped() const { return console_dropped_; }

  // For tests: the fakes and the app under test.
  bench::Bench& bench() { return *bench_; }
  App& app() { return *app_; }

 private:
  // Reads the VCU's frames and empties every host buffer.
  void Drain(RigOutputs* out);

  // Declared before app_: the app holds pointers into the bench.
  std::unique_ptr<bench::Bench> bench_;
  std::unique_ptr<App> app_;
  std::string console_;
  uint64_t console_dropped_ = 0;
};

}  // namespace sil
}  // namespace vcu
