#include "config/params.hpp"

namespace vcu {
namespace {

using lhre::can::EventMode;

Params MakeDefaults() {
  Params p;
  p.event_mode = EventMode::kUnassigned;
  return p;
}

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

// Cap skidpad torque at 70% above 40% pedal.
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

// Last year's endurance enabled regen. Keep the new board's pressure and
// valve commissioning opt-in; see RegenParams before enabling it here.
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
