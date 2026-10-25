#pragma once

#include "longhorn/console.hpp"
#include "vcu_app.hpp"

namespace vcu {

struct SerialHealth {
  uint32_t uptime_ms = 0;
  lhal::CanStats critical;
  lhal::CanStats data;
  uint32_t critical_inbox_drops = 0;
  uint32_t data_inbox_drops = 0;
  uint32_t control_beats = 0;
  uint32_t telemetry_beats = 0;
};

void PrintSerialReport(longhorn::Console& out, const ControlSnapshot& s,
                       const SerialHealth& health, lhal::ResetCause reset);

}  // namespace vcu
