# Generated CAN libraries (firmware)

C++ pack/unpack generated from `//lib/spec:files` on every build, one
library per source board:

| Target | Contents |
| ------ | -------- |
| `:can_<board>` | That board's messages, in `namespace lhre::can::<board>` |
| `:can_types` | Shared enums, `ToString`, and the `kMessageMeta` table |
| `:can_lib` | Umbrella over the whole bus, for tests, sims, and the gateway |

Firmware depends on the per-source targets for what it sends and listens
to. `boards/VCU/BUILD.bazel` is the worked example. The source
list is `CAN_BOARDS` in this package's BUILD file; the generator errors
with instructions when `lib/spec/messages/` gains or loses a file.

Only the wire and logical layers reach this code. Telemetry attributes
stay invisible to firmware.

## API

```cpp
namespace lhre::can::vcu {

struct VcuState {
  static constexpr uint32_t kFrameId = 0x1C7;
  static constexpr uint8_t kQuantity = 1;
  static constexpr uint8_t kDlc = 7;
  static constexpr float kFrequencyHz = 333.0f;

  uint8_t prndl_state{};
  bool stomp_fault{};
  uint16_t state_of_charge_estimate_raw{};  // phys = raw * 0.1 [%]

  float state_of_charge_estimate() const;
  void set_state_of_charge_estimate(float value);  // rounds to nearest

  static constexpr bool Matches(uint32_t frame_id);
  void Pack(uint8_t* dst) const;
  void Unpack(const uint8_t* src);

  lhal::CanFrame ToFrame() const;  // ToFrame(index) when kQuantity > 1
  static VcuState FromFrame(const lhal::CanFrame& frame);
};

}  // namespace lhre::can::vcu
```

Typical use against [LHAL](../../../drivers/lhal/README.md):

```cpp
using lhre::can::hvc::ContactorStatus;
using lhre::can::vcu::VcuState;

VcuState status;
status.prndl_state = 2;
bus.Send(status.ToFrame());

lhal::CanFrame frame;
if (bus.Receive(&frame) && ContactorStatus::Matches(frame.id)) {
  auto contactors = ContactorStatus::FromFrame(frame);
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

Indexed array blocks (`quantity` > 1, e.g. `CELL_TEMPERATURES`) are plain
structs: fill the slots and call `ToFrame(i)` per frame. Element index
math lives with [lib/spec](../../spec/README.md) and the gateway.

## Recipient dispatch

`:can_rx_<board>` exports `lhre_can_rx_<board>.hpp`. For each bus subscribed
through `CanMessage.to_boards` (including `*`), it generates
`lhre::can::rx::<Board><Bus>Rx`, containing one longhorn `RxSlot` per message
(named with the lower-case spec name) and `Dispatch(frame, now_ms)`.
Separate bus structs prevent cross-bus decoding. Board policy can derive
from them to add freshness predicates without maintaining a decode chain.
The RX target depends on source libraries and longhorn; existing packer
libraries keep their original dependencies. No generated files are committed.
