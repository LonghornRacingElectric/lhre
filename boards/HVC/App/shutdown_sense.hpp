#pragma once

namespace hvc {

// The shutdown circuit as the HVC senses it, upstream to downstream:
//
//   leg 2 -[BMS relay]- leg 3 -[IMD relay]- leg 4 - interlock - leg 5
//         - rest of car - leg 15 -> AIR+ / AIR- / precharge relay
//
// Each sense input reads high while its leg is live (HVC-013). The HVC
// drives the BMS relay (via the BMS error output); the IMD latch drives
// the IMD relay; legs 4 -> 15 are only sensed.
struct Legs {
  bool leg2 = false;
  bool leg3 = false;
  bool leg4 = false;
  bool leg5 = false;
  bool leg15 = false;
};

// Where the chain is broken: the segment just before the first dead leg.
enum class ChainBreak {
  kNone,       // every leg live: precharge relay is closed
  kNoInput,    // leg 2 dead: nothing coming into the board
  kBmsRelay,   // leg 2 live, leg 3 dead
  kImdRelay,   // leg 3 live, leg 4 dead
  kInterlock,  // leg 4 live, leg 5 dead
  kCar,        // leg 5 live, leg 15 dead
};

ChainBreak Diagnose(const Legs& legs);
const char* ChainBreakName(ChainBreak b);

// True when a leg reads live downstream of a dead one. That can't happen
// in a series chain, so it points at a sense fault or cross-talk.
bool Inconsistent(const Legs& legs);

}  // namespace hvc
