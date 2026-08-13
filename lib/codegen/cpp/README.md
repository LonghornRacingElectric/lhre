# Generated CAN libraries (firmware)

C++ pack/unpack generated from `//lib/spec:files` on every build — one
library per source board, mirroring the one-file-per-board spec layout:

| Target | Contents |
| ------ | -------- |
| `:can_<board>` (e.g. `:can_vcu`, `:can_hvc`) | That board's messages, in `namespace lhre::can::<board>` (`lhre_can_<board>.hpp`) |
| `:can_types` | Shared enums + `ToString` + the `kMessageMeta` table (`lhre_can_types.hpp`) |
| `:can_lib` | Umbrella: the whole bus (`lhre_can.hpp`) — for tests, sims, the future gateway |

Firmware should depend on the per-board targets for exactly what it
sends and listens to (see `boards/VCU/BUILD.bazel`: `app_deps` lists
`:can_vcu` + `:can_hvc`), not the umbrella. The board list is
`CAN_BOARDS` in this package's BUILD file; the generator errors with
instructions when `lib/spec/messages/` gains or loses a board file.

Only the spec's wire and logical layers reach this code. Telemetry
attributes are invisible to firmware by design.

## API shape

One `enum class` per spec enum in `lhre::can` (with `ToString` for
console logging) and one struct per message in the sender's namespace
(see the generated headers under `bazel-bin/lib/codegen/cpp/` for the
real thing):

```cpp
namespace lhre::can::vcu {

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

}  // namespace lhre::can::vcu
```

Typical use against [LHAL](../../../drivers/lhal/README.md):

```cpp
using lhre::can::hvc::HvcPackStatus;
using lhre::can::vcu::VcuStatus;

VcuStatus status;
status.state = lhre::can::VcuState::kDrive;
status.set_torque_request(-123.4f);
bus.Send(status.ToFrame());

lhal::CanFrame frame;
if (bus.Receive(&frame) && HvcPackStatus::Matches(frame.id)) {
  auto pack = HvcPackStatus::FromFrame(frame);
}
```

Indexed array blocks (`quantity` > 1 with repeated telemetry slots, e.g.
`HVC_CELL_TEMPS`) are plain structs here too: fill the per-frame slots,
`ToFrame(i)` for each frame in the block; element index math lives with
the spec (see [lib/spec](../../spec/README.md)) and the gateway.

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
