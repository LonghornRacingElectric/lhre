#pragma once

// Board-level safe output state, shared by bring-up, the fault hooks, and
// Error_Handler. Pin assignments come from the Rev A netlist (see
// boards/VCU/README.md, "Pin map").

#include "main.h"

namespace vcu_board {

// Safe levels:
//   Linelock_Ctrl   PE15 low   (no line lock request)
//   Amp_Shutdown_SW PE14 low   (PAM8302 ~SD asserted: amp off)
//   BMS_Error_SW    PE12 high  (LED on: the board default if the MCU dies)
//   IMD_Error_SW    PE13 high  (LED on)
//   Crit_STB / Data_STB PD9/PD8 low (TJA1462 normal mode, the pull-down
//   default) BOOT0trig       PB7  low
// Register writes only, so it is safe from any context, including hard
// fault and hooks with interrupts disabled.
void ForceSafeOutputs();

}  // namespace vcu_board
