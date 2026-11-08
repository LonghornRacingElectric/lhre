#include "shutdown_sense.hpp"

namespace hvc {

ChainBreak Diagnose(const Legs& legs) {
  if (!legs.leg2) {
    return ChainBreak::kNoInput;
  }
  if (!legs.leg3) {
    return ChainBreak::kBmsRelay;
  }
  if (!legs.leg4) {
    return ChainBreak::kImdRelay;
  }
  if (!legs.leg5) {
    return ChainBreak::kInterlock;
  }
  if (!legs.leg15) {
    return ChainBreak::kCar;
  }
  return ChainBreak::kNone;
}

const char* ChainBreakName(ChainBreak b) {
  switch (b) {
    case ChainBreak::kNone:
      return "none (chain complete)";
    case ChainBreak::kNoInput:
      return "no input (leg 2 dead)";
    case ChainBreak::kBmsRelay:
      return "BMS relay (leg 2-3)";
    case ChainBreak::kImdRelay:
      return "IMD relay (leg 3-4)";
    case ChainBreak::kInterlock:
      return "interlock (leg 4-5)";
    case ChainBreak::kCar:
      return "rest of car (leg 5-15)";
  }
  return "?";
}

bool Inconsistent(const Legs& legs) {
  const bool chain[] = {legs.leg2, legs.leg3, legs.leg4, legs.leg5,
                        legs.leg15};
  bool seen_dead = false;
  for (bool live : chain) {
    if (!live) {
      seen_dead = true;
    } else if (seen_dead) {
      return true;
    }
  }
  return false;
}

}  // namespace hvc
