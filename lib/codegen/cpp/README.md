# Generated CAN libraries (firmware)

C++ pack/unpack generated from `//lib/spec:files` on every build, one
library per source board:

| Target | Contents |
| ------ | -------- |
| `:can_<board>` | That board's messages, in `namespace lhre::can::<board>` |
| `:can_types` | Shared enums, `ToString`, and the `kMessageMeta` table |
| `:can_lib` | Umbrella over the whole bus, for tests, sims, and the gateway |

Firmware depends on the per-board targets for what it sends and listens
to. `boards/VCU/BUILD.bazel` lists `:can_vcu` and `:can_hvc`. The board
list is `CAN_BOARDS` in this package's BUILD file; the generator errors
with instructions when `lib/spec/messages/` gains or loses a file.

Only the wire and logical layers reach this code. Telemetry attributes
stay invisible to firmware.

## API

```cpp
namespace lhre::can::vcu {

struct VcuStatus {
  static constexpr uint32_t kFrameId = 0x300;
  static constexpr uint8_t kQuantity = 1;
  static constexpr uint8_t kDlc = 8;
  static constexpr float kFrequencyHz = 100.0f;

  VcuState state{};
  bool faults_overtemp{};
  int16_t torque_request_raw{};  // phys = raw * 0.1 [Nm]

  float torque_request() const;
  void set_torque_request(float value);  // rounds to nearest

  static constexpr bool Matches(uint32_t frame_id);
  void Pack(uint8_t* dst) const;
  void Unpack(const uint8_t* src);

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

`kMessageMeta[]` and `kMessageCount` give a frame-ID-sorted constexpr
table of (id, dlc, quantity, frequency) for RTOS task tables.

## Raw values

Struct fields hold raw wire integers: the smallest `intN_t`/`uintN_t`
that fits, enums as `enum class`, single bits as `bool`. `Pack` and
`Unpack` are integer-only, so they need no FPU and run on Cortex-M0.

Signals with a scale or offset get a `_raw` field plus float accessors,
so float cost is opt-in per call site. Unscaled signals keep the bare
name.

## Behavior worth knowing

- `Pack` zeroes the frame first, so unwritten bits are 0 on the wire.
- Mux: the selector field decides which signals `Pack` writes and
  `Unpack` reads. Other fields are left alone, so a struct accumulates
  state across differently-selected frames.
- `FromFrame` does not check the frame. Guard with `Matches(id)` first.
- Little-endian signals use LSB start bits, big-endian uses DBC/Motorola
  MSB numbering. Both are tested against hand-computed bytes in
  `test/can_lib_test.cpp`.
- FD frames (dlc > 8) and big-endian bitfields are rejected by the
  generator for now.

Indexed array blocks (`quantity` > 1, e.g. `HVC_CELL_TEMPS`) are plain
structs: fill the slots and call `ToFrame(i)` per frame. Element index
math lives with [lib/spec](../../spec/README.md) and the gateway.
