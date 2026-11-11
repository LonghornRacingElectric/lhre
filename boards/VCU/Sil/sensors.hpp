#pragma once

// What the car's sensors would report for a physical quantity, written as
// the inverse of the firmware's own calibration so the firmware reads the
// quantity back. Functions with a `clamped` argument fit the value into what
// the sensor or CAN field can carry and set *clamped to whether they had to.

#include <cstdint>

#include "config/params.hpp"

namespace vcu::sil {

// Sensor-side volts for a brake pressure, from the BSE calibration. The
// caller keeps `psi` inside 0..full_scale_psi.
float BseVolts(float psi, const BseParams& p);

// Sensor-side volts that ComputeSteering() reads back as `angle_deg`. The
// sensor wraps (pct above wrap_above reads as pct - 1), so angles below the
// one at pct 0 (about -115 deg) sit at the top of its span. Angles too close
// to the wrap for a 12-bit ADC to resolve the branch are clamped.
float SteeringVolts(float angle_deg, const SteeringParams& p, bool* clamped);

// INVERTER_SPEED motor_speed: signed motor rpm.
int16_t MotorRpm(float rad_s, bool* clamped);

// USM wheel_speed. A toothed-wheel sensor reports speed without direction,
// and the firmware rejects negative wheel speeds, so this is |rad_s|.
float WheelSpeed(float rad_s, bool* clamped);

// Fits `value` into a CAN field of raw_min..raw_max counts of `scale`. The
// generated setters round to nearest without clamping, and a value outside
// the raw type is undefined behaviour.
float FitField(float value, int32_t raw_min, int32_t raw_max, float scale,
               bool* clamped);

}  // namespace vcu::sil
