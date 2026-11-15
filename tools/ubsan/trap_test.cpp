// Canary for --config=ubsan (see README.md): if the sanitizer flags ever stop
// reaching the compiler, the UBSan CI step would still pass on clean code.
// This test fails instead.

#include <gtest/gtest.h>

#include <climits>

namespace {

int OverflowSigned() {
  // volatile, so the compiler can't fold the overflow away at compile time.
  volatile int x = INT_MAX;
  return x + 1;
}

TEST(UbsanTrapTest, SignedOverflowTraps) {
  EXPECT_DEATH(static_cast<void>(OverflowSigned()), "");
}

}  // namespace
