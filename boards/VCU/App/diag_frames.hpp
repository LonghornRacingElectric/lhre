#pragma once

#include <cstdint>

#include "analog.hpp"
#include "digital_io.hpp"
#include "lhal/can.hpp"
#include "lhre_can_types.hpp"

namespace vcu {

struct CalibratedValues {
  float apps1_travel = 0.0f;  // unclamped fractions / pressures
  float apps2_travel = 0.0f;
  float bse1_psi = 0.0f;
  float bse2_psi = 0.0f;
  float steering_angle_deg = 0.0f;
  float steering_travel = 0.0f;
};

// Data-bus diagnostics shared by the vehicle and bring-up images. Each
// returns the number of frames queued.
int SendAnalogDiag(lhal::CanBus* bus, const AnalogReadings& a,
                   const CalibratedValues& cal);
int SendDigitalDiag(lhal::CanBus* bus, const DigitalInputs& in,
                    const DigitalOutputs& out,
                    lhre::can::ShutdownLoopState loop, bool firmware_hard_brake,
                    bool hard_brake_mismatch);
int SendHealthDiag(lhal::CanBus* bus, lhal::ResetCause reset_cause,
                   uint32_t uptime_ms, const lhal::CanStats& critical,
                   const lhal::CanStats& data);

// Saturating narrowing for counters going into 8-bit fields.
constexpr uint8_t Saturate8(uint32_t v) {
  return v > 0xFFu ? 0xFFu : static_cast<uint8_t>(v);
}

// Clamped physical -> raw for the generated setters, which don't clamp.
constexpr float ClampPhys(float v, float lo, float hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

}  // namespace vcu
