#pragma once

#include <cstddef>
#include <cstdint>

namespace vcu {

// Same byte-stream token as lhre-2026 dfu_base: no newline is required and
// partial commands survive packet boundaries. One matcher per console.
class DfuCommand {
 public:
  bool Feed(uint8_t byte) {
    static constexpr char kToken[] = "update.";
    if (byte == static_cast<uint8_t>(kToken[matched_])) {
      if (++matched_ == sizeof(kToken) - 1) {
        matched_ = 0;
        return true;
      }
    } else {
      matched_ = byte == 'u' ? 1 : 0;
    }
    return false;
  }

 private:
  size_t matched_ = 0;
};

}  // namespace vcu
