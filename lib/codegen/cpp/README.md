# Generated CAN library (firmware)

`//lib/codegen/cpp:can_lib` — C++ pack/unpack for every message in the spec,
regenerated on every build from `//spec:files`. Builds for MCU targets
and the host alike; add it to a `firmware_project`'s `extra_deps` (or
any cc_target's `deps`) and `#include "lhre_can.hpp"`.

Only the spec's wire and logical layers reach this code. Telemetry
attributes are invisible to firmware by design.

## API shape

Everything lives in `namespace lhre::can`: one `enum class` per spec
enum (with a `ToString` for console logging) and one struct per message
(see the generated `lhre_can.hpp` under `bazel-bin/lib/codegen/cpp/` for the
real thing):

```cpp
struct VcuStatus {
  static constexpr uint32_t kFrameId = 0x300;
  static constexpr uint8_t kQuantity = 1;  // consecutive IDs from kFrameId
  static constexpr uint8_t kDlc = 8;
  static constexpr float kFrequencyHz = 100.0f;

  VcuState state{};
  bool faults_overtemp{};
  int16_t torque_request_raw{};  // phys = raw * 0.1 [Nm]
  ...

  float torque_request() const;          // physical value
  void set_torque_request(float value);  // rounds to nearest raw

  static constexpr bool Matches(uint32_t frame_id);
  void Pack(uint8_t* dst) const;   // writes kDlc bytes
  void Unpack(const uint8_t* src); // reads kDlc bytes, in place

  lhal::CanFrame ToFrame() const;  // ToFrame(index) when kQuantity > 1
  static VcuStatus FromFrame(const lhal::CanFrame& frame);
};
```

Typical use against [LHAL](../../../drivers/lhal/README.md):

```cpp
VcuStatus status;
status.state = VcuState::kDrive;
status.set_torque_request(-123.4f);
bus.Send(status.ToFrame());

lhal::CanFrame frame;
if (bus.Receive(&frame) && HvcPackStatus::Matches(frame.id)) {
  auto pack = HvcPackStatus::FromFrame(frame);
}
```

`kMessageMeta[]` / `kMessageCount` give a frame-ID-sorted constexpr
table of (id, dlc, quantity, frequency) for building RTOS TX/RX task
tables.

## Raw values, not floats

Struct fields hold **raw wire integers** (the smallest `intN_t`/`uintN_t`
that fits; enums as `enum class`, single bits and bitfield bits as
`bool`). `Pack`/`Unpack` are pure integer bit operations — no float
math, no FPU, safe on Cortex-M0.

Signals whose raw value isn't already physical (scale ≠ 1 or offset ≠ 0)
get a `_raw` field plus bare-named float accessors, so float cost is
opt-in per call site; the field comment states the conversion and unit.
Unscaled signals keep the bare name with no accessor — raw *is*
physical.

## Semantics worth knowing

- `Pack` zeroes the whole frame first, then ORs signals in. Unwritten
  bits are 0 on the wire.
- **Mux**: flat struct; the selector field decides which muxed signals
  `Pack` writes and `Unpack` reads. Non-selected fields are ignored by
  `Pack` and left untouched by `Unpack` (so a struct accumulates state
  across differently-selected frames).
- **`FromFrame` does not check the frame** — guard with `Matches(id)`
  (and `len >= kDlc` if the source isn't trusted) first.
- **Byte order**: little-endian signals use LSB start bits; big-endian
  uses DBC/Motorola MSB numbering. Both round-trip-tested against
  hand-computed byte patterns in `test/can_lib_test.cpp`.
- **Not yet supported** (generator errors out, schema allows them for
  later): FD frames (dlc > 8), big-endian bitfield containers.

## Migration note (lhre-2026)

Call sites of the old `generate_can_lib.py` output map mechanically:
per-message struct + pack/unpack pairs exist as before, as
`lhre::can::<Message>` methods; scaled fields that used to be floats in
the struct are now `_raw` integers plus explicit accessor pairs.
