#pragma once

#include <cstddef>
#include <cstdint>

namespace hvc {

// BMBs (ADBMS6830) on the isoSPI daisy chain behind SPI2. The one number
// to change as BMBs are added on the bench; the full pack is 12. Everything
// that talks to the chain (buffers, the wake-up sequence, /bms) sizes
// itself from this, like TOTAL_IC in the 2026 HVC.
inline constexpr size_t kNumBmbs = 10;

// Per BMB: 14 cells (C1-C14), and 9 thermistors on GPIO2-10.
inline constexpr size_t kCellsPerBmb = 14;
inline constexpr size_t kThermistorsPerBmb = 9;

// One BMB's answer to RDSID (read serial ID). A correct PEC is what proves
// the BMB is really talking: with nothing on the chain MISO idles high and
// every "answer" is 0xFF bytes with a failing PEC.
struct BmbId {
  bool pec_ok = false;
  uint8_t command_counter = 0;  // increments on each command the BMB accepts
  uint8_t sid[6] = {};          // factory-unique serial ID
};

// The BMB chain as the app sees it. LHAL has no SPI-device interface, so
// the app defines this narrow one; the board implements it with Analog
// Devices' ADBMS6830 library (Board/adbms_chain.*) and tests with a fake.
class BmsChain {
 public:
  virtual ~BmsChain() = default;

  // Wakes the chain and reads every BMB's serial ID, in chain order
  // (nearest the HVC first). Fills up to `max` entries; returns how many.
  virtual size_t ReadSerialIds(BmbId* out, size_t max) = 0;

  // The ADBMS library's own functions (Board/adbms_chain.cpp); the
  // library prints the results itself over printf.
  virtual void Configure() = 0;         // adBms6830_init_config
  virtual void ReadCellVoltages() = 0;  // adBms6830_read_cell_voltages
};

}  // namespace hvc
