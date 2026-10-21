#pragma once

#include <cstdint>

#include "lhal/system.hpp"

namespace longhorn {

// Reports a new level only after the raw input has held it for
// `debounce_ms`. Starts at `initial` until the first stable reading.
class Debouncer {
 public:
  explicit Debouncer(bool initial = false)
      : value_(initial), candidate_(initial) {}

  bool Update(bool raw, uint32_t now_ms, uint32_t debounce_ms) {
    if (!started_ || raw != candidate_) {
      started_ = true;
      candidate_ = raw;
      since_ms_ = now_ms;
    }
    if (candidate_ != value_ &&
        lhal::ElapsedMs(now_ms, since_ms_, debounce_ms)) {
      value_ = candidate_;
    }
    return value_;
  }

  bool value() const { return value_; }

 private:
  bool value_;
  bool candidate_;
  bool started_ = false;
  uint32_t since_ms_ = 0;
};

// A debounced push input that reports press edges only after it has been
// seen released, so a switch held through boot never registers as a press.
class EdgeButton {
 public:
  // Returns true on the tick a debounced press begins.
  bool Update(bool pressed_raw, uint32_t now_ms, uint32_t debounce_ms) {
    const bool pressed = debouncer_.Update(pressed_raw, now_ms, debounce_ms);
    bool edge = false;
    if (!pressed) {
      armed_ = true;
    } else if (!last_ && armed_) {
      edge = true;
    }
    last_ = pressed;
    return edge;
  }

  bool pressed() const { return last_; }

 private:
  // Starts "pressed" so an input already held at boot needs a release first.
  Debouncer debouncer_{true};
  bool last_ = true;
  bool armed_ = false;
};

// Holds `condition` true only once it has persisted for `hold_ms`; drops
// immediately when it clears.
class PersistenceTimer {
 public:
  bool Update(bool condition, uint32_t now_ms, uint32_t hold_ms) {
    if (!condition) {
      active_ = false;
      return false;
    }
    if (!active_) {
      active_ = true;
      since_ms_ = now_ms;
    }
    return lhal::ElapsedMs(now_ms, since_ms_, hold_ms);
  }

 private:
  bool active_ = false;
  uint32_t since_ms_ = 0;
};

// First-order low-pass, seeded by the first sample (lhre-2026 low_pass.c).
class Ema {
 public:
  float Update(float x, float alpha) {
    if (!seeded_) {
      seeded_ = true;
      y_ = x;
    } else {
      y_ = alpha * x + (1.0f - alpha) * y_;
    }
    return y_;
  }
  float value() const { return y_; }
  void Reset() { seeded_ = false; }

 private:
  bool seeded_ = false;
  float y_ = 0.0f;
};

}  // namespace longhorn
