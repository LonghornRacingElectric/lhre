#include <gtest/gtest.h>

#include "longhorn/dfu.hpp"

namespace {

TEST(Dfu, MatchesRawLegacyTokenAcrossPacketsWithoutNewline) {
  longhorn::DfuCommand command;
  for (uint8_t byte : {'u', 'p', 'x', 'u', 'u', 'p', 'd', 'a', 't', 'e'}) {
    EXPECT_FALSE(command.Feed(byte));
  }
  EXPECT_TRUE(command.Feed('.'));
  EXPECT_FALSE(command.Feed('\n'));
}

}  // namespace
