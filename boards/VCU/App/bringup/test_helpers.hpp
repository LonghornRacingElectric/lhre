#pragma once

#include <cstddef>
#include <cstdlib>

#include "io/analog.hpp"
#include "io/digital_io.hpp"

namespace vcu::bringup {

// Output names for /out, in VcuDigitalOutputs bit order.
struct OutputName {
  const char* name;
  bool DigitalOutputs::* field;
};
inline constexpr OutputName kOutputs[] = {
    {"linelock", &DigitalOutputs::linelock_ctrl},
    {"amp", &DigitalOutputs::amp_enable},
    {"bms", &DigitalOutputs::bms_led},
    {"imd", &DigitalOutputs::imd_led},
    {"crit_stb", &DigitalOutputs::crit_stb},
    {"data_stb", &DigitalOutputs::data_stb},
};

inline const AnalogReadings::Channel& ChannelAt(const AnalogReadings& a,
                                                size_t i) {
  const AnalogReadings::Channel* channels[] = {
      &a.apps1,    &a.apps2,       &a.bse1,        &a.bse2,
      &a.steering, &a.shutdown_in, &a.shutdown_out};
  return *channels[i];
}
inline constexpr const char* kChannelNames[] = {
    "apps1", "apps2", "bse1", "bse2", "steer", "shdn_in", "shdn_out"};

inline int Mv(float v) { return static_cast<int>(v * 1000.0f); }

// Reads "<int> <int> <int>" (missing values stay as given).
inline int ParseInts(const char* args, long* values, int max_values) {
  int n = 0;
  const char* cursor = args;
  while (n < max_values && *cursor != '\0') {
    char* end = nullptr;
    const long v = std::strtol(cursor, &end, 10);
    if (end == cursor) {
      break;
    }
    values[n++] = v;
    cursor = end;
  }
  return n;
}

}  // namespace vcu::bringup
