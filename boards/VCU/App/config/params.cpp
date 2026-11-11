#include "config/params.hpp"

namespace vcu {
namespace {

using lhre::can::EventMode;

Params MakeDefaults() {
  Params p;
  p.event_mode = EventMode::kUnassigned;
  return p;
}

// 2026's acceleration table peaked 10 Nm higher, then fell with speed;
// the power cap now does the falling.
Params MakeAcceleration() {
  Params p = MakeDefaults();
  p.event_mode = EventMode::kAcceleration;
  p.torque.peak_torque_nm = 220.0f;
  return p;
}

// 70% of peak, linear, full at 32% travel. The 2026 skidpad map saturated
// at 70% from 40% of the deadzoned travel (35% of raw travel); this stays
// within 10 Nm of it everywhere and matches it from 35% up.
Params MakeSkidpad() {
  Params p = MakeDefaults();
  p.event_mode = EventMode::kSkidpad;
  p.torque.peak_torque_nm = 0.70f * 210.0f;
  p.torque.touchiness = 1.0f;
  p.apps.throttle_full = 0.32f;
  return p;
}

Params MakeAutocross() {
  Params p = MakeDefaults();
  p.event_mode = EventMode::kAutocross;
  return p;
}

// Last year's endurance enabled regen. Keep the new board's pressure and
// valve commissioning opt-in; see RegenParams before enabling it here.
// pedal_power_w keeps the 2026 rpm table's torque per pedal position, so
// endurance energy use stays where it was.
Params MakeEndurance() {
  Params p = MakeDefaults();
  p.event_mode = EventMode::kEndurance;
  p.torque.pedal_power_w = 65000.0f;
  return p;
}

// Bench motor spin with the motor unloaded. Nothing but the rotor's own
// inertia resists the torque, so the car's 210 Nm would overspeed it in a
// fraction of a second. Cap torque low and fade it to zero below a speed
// ceiling; the motor then coasts, since nothing commands negative torque.
// TODO(verify): 20 Nm and 1500 rpm are first-spin values, not the motor's
// limits. Raise them on the bench once the first spins are clean.
Params MakeMotorSpin() {
  Params p = MakeDefaults();
  p.event_mode = EventMode::kMotorSpin;
  p.torque.peak_torque_nm = 20.0f;
  p.torque.fade_start_rpm = 1200.0f;
  p.torque.zero_torque_rpm = 1500.0f;
  // The inverter enforces the same cap on its side of the command.
  p.inverter.torque_limit_nm = p.torque.peak_torque_nm;
  return p;
}

const Params kDefaults = MakeDefaults();
const Params kAcceleration = MakeAcceleration();
const Params kSkidpad = MakeSkidpad();
const Params kAutocross = MakeAutocross();
const Params kEndurance = MakeEndurance();
const Params kMotorSpin = MakeMotorSpin();

}  // namespace

const Params& ParamsFor(EventMode mode) {
  switch (mode) {
    case EventMode::kAcceleration:
      return kAcceleration;
    case EventMode::kSkidpad:
      return kSkidpad;
    case EventMode::kAutocross:
      return kAutocross;
    case EventMode::kEndurance:
      return kEndurance;
    case EventMode::kMotorSpin:
      return kMotorSpin;
    case EventMode::kUnassigned:
      break;
  }
  return kDefaults;
}

}  // namespace vcu
