#pragma once

namespace vcu {

// Largest physical value each Critical and Data bus field can carry (spec
// bit width x scale). The generated setters do not clamp.
inline constexpr float kU16Volts = 6.5535f;      // u16 x 0.0001 V
inline constexpr float kU16LoopVolts = 655.35f;  // u16 x 0.01 V
inline constexpr float kU16Fraction = 6.5535f;   // u16 x 0.0001
inline constexpr float kS16Fraction = 3.2767f;   // s16 x 0.0001
inline constexpr float kU16Psi = 3276.75f;       // u16 x 0.05 psi
inline constexpr float kS16Psi = 3276.7f;        // s16 x 0.1 psi
inline constexpr float kU16Nm = 6553.5f;         // u16 x 0.1 Nm
inline constexpr float kS16Nm = 3276.7f;         // s16 x 0.1 Nm
inline constexpr float kS16Deg = 131.068f;       // s16 x 0.004 deg
inline constexpr float kU32Wh = 4294967.0f;      // u32 x 0.001 Wh

}  // namespace vcu
