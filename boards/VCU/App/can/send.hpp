#pragma once

#include "lhal/can.hpp"

namespace vcu {

// Returns 1 when the frame was queued, so callers can count frames sent.
inline int Send(lhal::CanBus* bus, const lhal::CanFrame& frame) {
  return bus != nullptr && lhal::IsOk(bus->Send(frame)) ? 1 : 0;
}

}  // namespace vcu
