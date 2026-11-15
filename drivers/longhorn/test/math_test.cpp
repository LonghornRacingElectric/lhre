#include <gtest/gtest.h>

#include "longhorn/math.hpp"

namespace {

TEST(Lookup, InterpolatesAndClamps) {
  const float y[] = {0.0f, 10.0f, 20.0f};
  EXPECT_FLOAT_EQ(longhorn::Lookup1D(y, 3, 0.0f, 2.0f, -1.0f), 0.0f);
  EXPECT_FLOAT_EQ(longhorn::Lookup1D(y, 3, 0.0f, 2.0f, 0.5f), 5.0f);
  EXPECT_FLOAT_EQ(longhorn::Lookup1D(y, 3, 0.0f, 2.0f, 1.5f), 15.0f);
  EXPECT_FLOAT_EQ(longhorn::Lookup1D(y, 3, 0.0f, 2.0f, 9.0f), 20.0f);
}

}  // namespace
