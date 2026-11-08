#pragma once

#include <cstddef>
#include <cstdint>

#include "asm330lhb.hpp"

namespace hvc {

// N-tap moving-average FIR: the mean of the last N inputs. Integer-only,
// O(1) per sample via a running sum. Until N samples have arrived it
// averages what it has, so the first outputs aren't dragged toward zero.
template <size_t N>
class MovingAverage {
 public:
  static_assert(N > 0, "need at least one tap");

  int32_t Push(int32_t x) {
    sum_ += static_cast<int64_t>(x) - history_[next_];
    history_[next_] = x;
    next_ = (next_ + 1) % N;
    if (count_ < N) {
      ++count_;
    }
    return static_cast<int32_t>(sum_ / static_cast<int64_t>(count_));
  }

 private:
  int32_t history_[N] = {};
  int64_t sum_ = 0;
  size_t next_ = 0;
  size_t count_ = 0;
};

// Low-pass filter for IMU samples: an independent moving average on each
// accel axis, gyro axis, and temperature. With the IMU sampled at ~100 Hz,
// 8 taps put the -3 dB point near 5.5 Hz and the first null at 12.5 Hz —
// enough to calm vibration and sensor noise on a bench readout while
// still following the board when it's moved.
class ImuFilter {
 public:
  static constexpr size_t kTaps = 8;

  ImuSample Push(const ImuSample& in) {
    ImuSample out;
    for (int axis = 0; axis < 3; ++axis) {
      out.accel_mg[axis] = accel_[axis].Push(in.accel_mg[axis]);
      out.gyro_mdps[axis] = gyro_[axis].Push(in.gyro_mdps[axis]);
    }
    out.temp_centi_c = temp_.Push(in.temp_centi_c);
    return out;
  }

 private:
  MovingAverage<kTaps> accel_[3];
  MovingAverage<kTaps> gyro_[3];
  MovingAverage<kTaps> temp_;
};

}  // namespace hvc
