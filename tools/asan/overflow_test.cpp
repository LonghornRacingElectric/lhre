// Canary for --config=asan (see README.md): if the sanitizer flags ever stop
// reaching the compiler or linker, the ASan CI step would still pass on clean
// code. This test fails instead.

#include <gtest/gtest.h>

#include <memory>

namespace {

int ReadPastTheEnd() {
  auto buffer = std::make_unique<int[]>(4);
  // Both volatile: the index so the compiler can't prove it out of range,
  // and the load so optimization (-c opt) can't drop it as dead.
  volatile int index = 4;
  return static_cast<const volatile int*>(buffer.get())[index];
}

TEST(AsanOverflowTest, HeapOverflowIsCaught) {
  EXPECT_DEATH(static_cast<void>(ReadPastTheEnd()), "heap-buffer-overflow");
}

}  // namespace
