#include "adbms_chain.hpp"

#include <cstring>

// Not adBms6830CmdList.h: it defines the command tables as globals, so
// only adBms_Application.c may include it.
extern "C" {
#include "adBms_Application.h"
}

// From adbms6830/src/adBms6830GenericType.c; not in any header.
extern "C" void spiReadData(uint8_t tIC, uint8_t tx_cmd[2], uint8_t* rx_data,
                            uint8_t* pec_error, uint8_t* cmd_cntr,
                            uint8_t regData_size);

namespace hvc::board {
namespace {

// RDSID: read serial ID group (ADBMS6830 command list).
uint8_t kRdsid[2] = {0x00, 0x2C};
// Each BMB answers with 6 data bytes + 2 bytes of command counter / PEC.
constexpr uint8_t kBytesPerBmb = 8;

// The library's per-BMB register mirror, in chain order.
cell_asic g_ic[kNumBmbs];

}  // namespace

size_t AdbmsChain::ReadSerialIds(BmbId* out, size_t max) {
  uint8_t rx[kNumBmbs * kBytesPerBmb] = {};
  uint8_t pec_error[kNumBmbs] = {};
  uint8_t cmd_counter[kNumBmbs] = {};
  // Wakes the isoSPI chain, sends RDSID + its PEC, and clocks back every
  // BMB's response, checking each one's PEC.
  spiReadData(static_cast<uint8_t>(kNumBmbs), kRdsid, rx, pec_error,
              cmd_counter, kBytesPerBmb);

  const size_t n = max < kNumBmbs ? max : kNumBmbs;
  for (size_t i = 0; i < n; ++i) {
    out[i].pec_ok = pec_error[i] == 0;
    out[i].command_counter = cmd_counter[i];
    std::memcpy(out[i].sid, &rx[i * kBytesPerBmb], sizeof(out[i].sid));
  }
  return n;
}

void AdbmsChain::Configure() {
  adBms6830_init_config(static_cast<uint8_t>(kNumBmbs), g_ic);
}

void AdbmsChain::ReadCellVoltages() {
  adBms6830_read_cell_voltages(static_cast<uint8_t>(kNumBmbs), g_ic);
}

}  // namespace hvc::board
