#pragma once

#include <cstdint>

#include "app/control_snapshot.hpp"
#include "lhal/can.hpp"
#include "lhal/reset.hpp"
#include "longhorn/console.hpp"
#include "safety/faults.hpp"

namespace vcu {

// Set true and rebuild to restore the full diagnostic report.
inline constexpr bool kDetailedSerialReport = false;

struct SerialHealth {
  uint32_t uptime_ms = 0;
  lhal::CanStats critical;
  lhal::CanStats data;
  uint32_t critical_inbox_drops = 0;
  uint32_t data_inbox_drops = 0;
  uint32_t control_beats = 0;
  uint32_t telemetry_beats = 0;
};

void PrintCompactSerialReport(longhorn::Console& out, const ControlSnapshot& s);

void PrintSerialReport(longhorn::Console& out, const ControlSnapshot& s,
                       const SerialHealth& health, lhal::ResetCause reset);

}  // namespace vcu
