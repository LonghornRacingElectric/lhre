#pragma once

#include <cstddef>

namespace vcu {

constexpr float Clamp(float x, float lo, float hi) {
  return x < lo ? lo : (x > hi ? hi : x);
}

// Linear interpolation over `n` evenly spaced points spanning
// [x_min, x_max], clamped at the ends (lhre-2026 Lookup1D.c).
inline float Lookup1D(const float* y, size_t n, float x_min, float x_max,
                      float x) {
  if (n == 0) {
    return 0.0f;
  }
  if (n == 1 || x <= x_min || x_max <= x_min) {
    return y[0];
  }
  if (x >= x_max) {
    return y[n - 1];
  }
  const float pos = (x - x_min) / (x_max - x_min) * static_cast<float>(n - 1);
  const size_t i = static_cast<size_t>(pos);
  if (i >= n - 1) {
    return y[n - 1];
  }
  const float frac = pos - static_cast<float>(i);
  return y[i] + (y[i + 1] - y[i]) * frac;
}

// (x - lo) / (hi - lo), unclamped. Works for decreasing sensors (hi < lo).
inline float Normalize(float x, float lo, float hi) {
  const float span = hi - lo;
  return span == 0.0f ? 0.0f : (x - lo) / span;
}

}  // namespace vcu
