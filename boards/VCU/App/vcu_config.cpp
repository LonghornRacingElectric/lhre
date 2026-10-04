#include "vcu_config.hpp"

namespace vcu {
namespace {

using lhre::can::EventMode;

Params MakeDefaults() {
  Params p;
  p.event_mode = EventMode::kUnassigned;
  return p;
}

// lhre-2026 acceleration_params.h:6-48 (traction control present there but
// disabled).
Params MakeAcceleration() {
  Params p = MakeDefaults();
  p.event_mode = EventMode::kAcceleration;
  const float table[kLookupPoints] = {220, 220, 220, 220, 220, 200,
                                      175, 155, 135, 115, 105};
  for (size_t i = 0; i < kLookupPoints; ++i) {
    p.torque.power_limit_torque[i] = table[i];
  }
  return p;
}

// lhre-2026 skidpad_params.h (final values from #297): torque saturates at
// 70% of available above 40% pedal, linear curve.
Params MakeSkidpad() {
  Params p = MakeDefaults();
  p.event_mode = EventMode::kSkidpad;
  const float map[kLookupPoints] = {0.0f,  0.20f, 0.40f, 0.60f, 0.70f, 0.70f,
                                    0.70f, 0.70f, 0.70f, 0.70f, 0.70f};
  for (size_t i = 0; i < kLookupPoints; ++i) {
    p.torque.pedal_map[i] = map[i];
  }
  p.torque.pedal_curve_exponent = 1.0f;
  return p;
}

Params MakeAutocross() {
  Params p = MakeDefaults();
  p.event_mode = EventMode::kAutocross;
  return p;
}

// lhre-2026 endurance_params.h enabled regen + line lock. Rev A keeps the
// line lock request at 0 until regen blending exists, so endurance runs on
// the defaults.
Params MakeEndurance() {
  Params p = MakeDefaults();
  p.event_mode = EventMode::kEndurance;
  return p;
}

const Params kDefaults = MakeDefaults();
const Params kAcceleration = MakeAcceleration();
const Params kSkidpad = MakeSkidpad();
const Params kAutocross = MakeAutocross();
const Params kEndurance = MakeEndurance();

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
    case EventMode::kUnassigned:
      break;
  }
  return kDefaults;
}

}  // namespace vcu
