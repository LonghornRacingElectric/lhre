"""Generates the firmware CAN library (C++) from the spec.

Consumes only the wire and logical layers (design doc §4). One output
pair per source board: messages/<board>.textproto becomes
lhre_can_<board>.{hpp,cpp} with everything inside namespace
lhre::can::<board>, so a board's firmware depends on exactly the message
sets it sends or listens to instead of the whole bus. Shared enums and
the message metadata table live in lhre_can_types.{hpp,cpp}; lhre_can.hpp
is the include-everything umbrella.

Emitted API, per message: a struct with constexpr frame constants, raw
integer fields, integer-only Pack/Unpack (no FPU), inline float
accessors for scaled signals, and lhal::CanFrame conversions. Telemetry
attributes are ignored here — they never reach firmware.

Usage: gen_can_lib.py --out-dir DIR --boards vcu,hvc spec1.textproto ...
"""

import argparse
import hashlib
import pathlib
import re
import sys

from lib.spec import loader, validator
from lib.spec.proto import can_spec_pb2

_MESSAGES_FILE = re.compile(r"(?:^|/)messages/([a-z][a-z0-9_]*)\.textproto$")


def _die(msg):
    print(f"gen_can_lib: {msg}", file=sys.stderr)
    raise SystemExit(1)


def camel(upper_snake):
    """VCU_STATUS -> VcuStatus; also INIT -> Init for enum values."""
    return "".join(part.capitalize() for part in upper_snake.lower().split("_"))


def raw_ctype(encoding):
    width = next(w for w in (8, 16, 32, 64) if encoding.bit_length <= w)
    return f"int{width}_t" if encoding.sign == can_spec_pb2.SIGNED else f"uint{width}_t"


def is_scaled(encoding):
    return validator.effective_scale(encoding) != 1.0 or encoding.offset != 0.0


def float_lit(value):
    return f"{value!r}f"  # repr keeps full precision


class Generator:
    def __init__(self, spec):
        self.spec = spec
        self.enums = {e.name: e for _, e in spec.enum_types()}
        self.bitfields = {b.name: b for _, b in spec.bitfield_types()}
        # board stem -> its messages, sorted by CAN ID; boards sorted.
        self.boards = {}
        for filename, message in spec.messages():
            match = _MESSAGES_FILE.search(filename)
            if not match:
                _die(f"{filename}: messages must live in messages/<board>.textproto")
            self.boards.setdefault(match.group(1), []).append(message)
        self.boards = {b: sorted(msgs, key=lambda m: m.can_id)
                       for b, msgs in sorted(self.boards.items())}

    def all_messages(self):
        return sorted((m for msgs in self.boards.values() for m in msgs),
                      key=lambda m: m.can_id)

    # ---- shared pieces ---------------------------------------------------

    def struct_name(self, message):
        return camel(message.name)

    def selector(self, message):
        sel = [s for s in message.signal if s.logical.mux_selector]
        return sel[0] if sel else None

    def fields(self, message):
        """Yields (field_name, cpp_type, signal, bitfield_bit_index).
        Scaled physical signals get a _raw suffix; their float accessors
        carry the bare name."""
        for signal in message.signal:
            kind = signal.logical.WhichOneof("kind")
            if kind == "bitfield":
                bitfield = self.bitfields[signal.logical.bitfield.bitfield_type]
                if signal.encoding.byte_order != can_spec_pb2.LITTLE_ENDIAN:
                    _die(f"{message.name}.{signal.name}: big-endian bitfields are not supported")
                for i, bit in enumerate(bitfield.bit):
                    yield f"{signal.name}_{bit.name}", "bool", signal, i
            elif kind == "boolean":
                yield signal.name, "bool", signal, None
            elif kind == "enum_type":
                yield signal.name, signal.logical.enum_type, signal, None
            elif is_scaled(signal.encoding):
                yield f"{signal.name}_raw", raw_ctype(signal.encoding), signal, None
            else:
                yield signal.name, raw_ctype(signal.encoding), signal, None

    # ---- types header/source (shared enums + metadata) -------------------

    def types_header(self, provenance):
        h = [provenance]
        h.append("""#pragma once

#include <cstdint>

namespace lhre::can {
""")
        for name in sorted(self.enums):
            enum_type = self.enums[name]
            h.append(f"// {enum_type.description}" if enum_type.description else f"// {name}")
            width = max((v.number for v in enum_type.value), default=0)
            underlying = next(w for w in (8, 16, 32) if width < (1 << w))
            h.append(f"enum class {name} : uint{underlying}_t {{")
            for value in enum_type.value:
                comment = f"  // {value.description}" if value.description else ""
                h.append(f"  k{camel(value.name)} = {value.number},{comment}")
            h.append("};")
            h.append(f"// Wire name of the value, \"?\" if out of range.")
            h.append(f"const char* ToString({name} value);")
            h.append("")
        h.append("""// Per-message metadata for RTOS CAN task tables, sorted by frame ID.
struct MessageMeta {
  uint32_t frame_id;
  uint8_t dlc;
  uint8_t quantity;   // consecutive frame IDs from frame_id
  float frequency_hz; // 0 = aperiodic
};
""")
        h.append("inline constexpr MessageMeta kMessageMeta[] = {")
        for message in self.all_messages():
            quantity = validator.effective_quantity(message)
            h.append(f"    {{0x{message.can_id:03X}, {message.dlc}, {quantity}, "
                     f"{message.frequency_hz}f}},  // {message.name}")
        h.append("};")
        h.append("inline constexpr uint32_t kMessageCount = sizeof(kMessageMeta) / sizeof(kMessageMeta[0]);")
        h.append("")
        h.append("}  // namespace lhre::can")
        return "\n".join(h) + "\n"

    def types_source(self, provenance, header_name):
        s = [provenance, f'#include "{header_name}"\n', "namespace lhre::can {", ""]
        for name in sorted(self.enums):
            enum_type = self.enums[name]
            s.append(f"const char* ToString({name} value) {{")
            s.append("  switch (value) {")
            for value in enum_type.value:
                s.append(f'    case {name}::k{camel(value.name)}: return "{value.name}";')
            s.append("  }")
            s.append('  return "?";')
            s.append("}")
            s.append("")
        s.append("}  // namespace lhre::can")
        return "\n".join(s) + "\n"

    # ---- per-board header ------------------------------------------------

    def field_comment(self, signal, bit_index):
        if bit_index is not None:
            return ""
        enc = signal.encoding
        parts = []
        if signal.logical.WhichOneof("kind") == "physical":
            if is_scaled(enc):
                conv = f"phys = raw * {validator.effective_scale(enc):g}"
                if enc.offset:
                    conv += f" + {enc.offset:g}"
                parts.append(conv)
            if signal.logical.physical.unit:
                parts.append(f"[{signal.logical.physical.unit}]")
        if signal.description:
            parts.append(signal.description)
        if enc.muxed:
            parts.append(f"valid when selector == {enc.mux_value}")
        return f"  // {'; '.join(parts)}" if parts else ""

    def accessors(self, message, signal):
        """Inline float accessors for a scaled physical signal. Kept
        separate from Pack/Unpack so integer-only call sites never touch
        the FPU."""
        enc = signal.encoding
        scale = validator.effective_scale(enc)
        ctype = raw_ctype(enc)
        unit = f" [{signal.logical.physical.unit}]" if signal.logical.physical.unit else ""
        offset_add = f" + {float_lit(enc.offset)}" if enc.offset else ""
        out = [f"  // Physical value{unit}."]
        out.append(f"  float {signal.name}() const {{")
        out.append(f"    return static_cast<float>({signal.name}_raw) * {float_lit(scale)}{offset_add};")
        out.append("  }")
        out.append(f"  // Sets the raw field from a physical value{unit}, rounding to nearest.")
        out.append(f"  void set_{signal.name}(float value) {{")
        if enc.offset:
            out.append(f"    float scaled = (value - {float_lit(enc.offset)}) / {float_lit(scale)};")
        else:
            out.append(f"    float scaled = value / {float_lit(scale)};")
        out.append(f"    {signal.name}_raw = static_cast<{ctype}>(scaled >= 0.0f ? scaled + 0.5f : scaled - 0.5f);")
        out.append("  }")
        return out

    def message_decl(self, message):
        name = self.struct_name(message)
        quantity = validator.effective_quantity(message)
        out = []
        if message.description:
            out.append(f"// {message.description}")
        out.append(f"struct {name} {{")
        out.append(f"  static constexpr uint32_t kFrameId = 0x{message.can_id:03X};")
        if quantity > 1:
            out.append(f"  // Occupies frame IDs [kFrameId, kFrameId + kQuantity).")
        out.append(f"  static constexpr uint8_t kQuantity = {quantity};")
        out.append(f"  static constexpr uint8_t kDlc = {message.dlc};")
        out.append(f"  static constexpr float kFrequencyHz = {message.frequency_hz}f;  // 0 = aperiodic")
        out.append("")
        for field, ctype, signal, bit in self.fields(message):
            out.append(f"  {ctype} {field}{{}};{self.field_comment(signal, bit)}")
        out.append("")
        scaled = [s for s in message.signal
                  if s.logical.WhichOneof("kind") == "physical" and is_scaled(s.encoding)]
        for signal in scaled:
            out.extend(self.accessors(message, signal))
        if scaled:
            out.append("")
        out.append("  // True if a received frame ID belongs to this message.")
        if quantity > 1:
            out.append("  static constexpr bool Matches(uint32_t frame_id) {")
            out.append("    return frame_id >= kFrameId && frame_id < kFrameId + kQuantity;")
            out.append("  }")
        else:
            out.append("  static constexpr bool Matches(uint32_t frame_id) { return frame_id == kFrameId; }")
        out.append("")
        out.append("  // Integer-only (no FPU). Pack writes kDlc bytes; Unpack reads kDlc")
        out.append("  // bytes, updating fields in place")
        if self.selector(message) is not None:
            out.append(f"  // (signals not selected by '{self.selector(message).name}' are left untouched).")
        else:
            out[-1] += "."
        out.append("  void Pack(uint8_t* dst) const;")
        out.append("  void Unpack(const uint8_t* src);")
        out.append("")
        index_param = "uint8_t index = 0" if quantity > 1 else ""
        index_doc = " index selects the frame within [0, kQuantity)." if quantity > 1 else ""
        out.append(f"  // lhal glue: a ready-to-send frame / a decoded received frame.{index_doc}")
        out.append(f"  lhal::CanFrame ToFrame({index_param}) const;")
        out.append(f"  // Precondition: Matches(frame.id) && frame.len >= kDlc.")
        out.append(f"  static {name} FromFrame(const lhal::CanFrame& frame);")
        out.append("};")
        out.append("")
        return out

    def board_header(self, provenance, board, types_header_name):
        h = [provenance]
        h.append(f"""#pragma once

#include <cstdint>

#include "lhal/can.hpp"
#include "{types_header_name}"

// Messages originating from the {board.upper()} board.
namespace lhre::can::{board} {{
""")
        for message in self.boards[board]:
            h.extend(self.message_decl(message))
        h.append(f"}}  // namespace lhre::can::{board}")
        return "\n".join(h) + "\n"

    # ---- per-board source ------------------------------------------------

    _HELPERS = """namespace {

// Bit insertion/extraction over a byte buffer. start_bit addresses are
// linear little-endian (bit n = byte n/8, bit n%8); big-endian signals
// use DBC/Motorola numbering (start bit = MSB, descending, then bit 7
// of the next byte). Values are masked to bit_length, so callers can
// pass sign-extended integers unchanged.
void Insert(uint8_t* dst, uint32_t start_bit, uint32_t bit_length,
            uint64_t value, bool big_endian) {
  uint32_t byte = start_bit / 8u;
  uint32_t bit = start_bit % 8u;
  for (uint32_t i = 0; i < bit_length; ++i) {
    // Walk from the value's MSB for big-endian, LSB for little-endian.
    uint32_t value_bit = big_endian ? (bit_length - 1u - i) : i;
    if ((value >> value_bit) & 1u) {
      dst[byte] |= static_cast<uint8_t>(1u << bit);
    }
    if (big_endian) {
      if (bit == 0u) { ++byte; bit = 7u; } else { --bit; }
    } else {
      if (bit == 7u) { ++byte; bit = 0u; } else { ++bit; }
    }
  }
}

uint64_t Extract(const uint8_t* src, uint32_t start_bit, uint32_t bit_length,
                 bool big_endian) {
  uint64_t value = 0;
  uint32_t byte = start_bit / 8u;
  uint32_t bit = start_bit % 8u;
  for (uint32_t i = 0; i < bit_length; ++i) {
    uint32_t value_bit = big_endian ? (bit_length - 1u - i) : i;
    if ((src[byte] >> bit) & 1u) {
      value |= static_cast<uint64_t>(1u) << value_bit;
    }
    if (big_endian) {
      if (bit == 0u) { ++byte; bit = 7u; } else { --bit; }
    } else {
      if (bit == 7u) { ++byte; bit = 0u; } else { ++bit; }
    }
  }
  return value;
}

int64_t SignExtend(uint64_t value, uint32_t bit_length) {
  uint64_t sign_bit = static_cast<uint64_t>(1u) << (bit_length - 1u);
  return static_cast<int64_t>((value ^ sign_bit) - sign_bit);
}

}  // namespace
"""

    def board_source(self, provenance, board, header_name):
        s = [provenance, f'#include "{header_name}"\n', "#include <cstring>",
             "", f"namespace lhre::can::{board} {{", "", self._HELPERS]
        for message in self.boards[board]:
            s.extend(self.pack_fn(message))
            s.append("")
            s.extend(self.unpack_fn(message))
            s.append("")
            s.extend(self.frame_fns(message))
            s.append("")
        s.append(f"}}  // namespace lhre::can::{board}")
        return "\n".join(s) + "\n"

    def _mux_guard(self, message, signal, body_lines, this):
        if not signal.encoding.muxed:
            return body_lines
        sel = self.selector(message)
        guard = f"  if ({this}{sel.name} == {signal.encoding.mux_value}u) {{"
        return [guard] + ["  " + line for line in body_lines] + ["  }"]

    def _field_expr(self, signal):
        kind = signal.logical.WhichOneof("kind")
        if kind == "physical" and is_scaled(signal.encoding):
            return f"{signal.name}_raw"
        return signal.name

    def pack_fn(self, message):
        name = self.struct_name(message)
        out = [f"void {name}::Pack(uint8_t* dst) const {{",
               f"  std::memset(dst, 0, kDlc);"]
        for signal in message.signal:
            enc = signal.encoding
            big = "true" if enc.byte_order == can_spec_pb2.BIG_ENDIAN else "false"
            kind = signal.logical.WhichOneof("kind")
            if kind == "bitfield":
                bitfield = self.bitfields[signal.logical.bitfield.bitfield_type]
                lines = [
                    f"  Insert(dst, {enc.start_bit + i}u, 1u, {signal.name}_{bit.name} ? 1u : 0u, false);"
                    for i, bit in enumerate(bitfield.bit)]
            elif kind == "boolean":
                lines = [f"  Insert(dst, {enc.start_bit}u, 1u, {signal.name} ? 1u : 0u, false);"]
            else:
                lines = [f"  Insert(dst, {enc.start_bit}u, {enc.bit_length}u, "
                         f"static_cast<uint64_t>({self._field_expr(signal)}), {big});"]
            out.extend(self._mux_guard(message, signal, lines, this=""))
        out.append("}")
        return out

    def unpack_fn(self, message):
        name = self.struct_name(message)
        out = [f"void {name}::Unpack(const uint8_t* src) {{"]
        # The selector must be read before any muxed signal.
        ordered = sorted(message.signal, key=lambda s: s.encoding.muxed)
        for signal in ordered:
            enc = signal.encoding
            big = "true" if enc.byte_order == can_spec_pb2.BIG_ENDIAN else "false"
            kind = signal.logical.WhichOneof("kind")
            extract = f"Extract(src, {enc.start_bit}u, {enc.bit_length}u, {big})"
            if kind == "bitfield":
                bitfield = self.bitfields[signal.logical.bitfield.bitfield_type]
                lines = [
                    f"  {signal.name}_{bit.name} = Extract(src, {enc.start_bit + i}u, 1u, false) != 0u;"
                    for i, bit in enumerate(bitfield.bit)]
            elif kind == "boolean":
                lines = [f"  {signal.name} = {extract} != 0u;"]
            elif kind == "enum_type":
                lines = [f"  {signal.name} = static_cast<{signal.logical.enum_type}>({extract});"]
            elif enc.sign == can_spec_pb2.SIGNED:
                lines = [f"  {self._field_expr(signal)} = "
                         f"static_cast<{raw_ctype(enc)}>(SignExtend({extract}, {enc.bit_length}u));"]
            else:
                lines = [f"  {self._field_expr(signal)} = static_cast<{raw_ctype(enc)}>({extract});"]
            out.extend(self._mux_guard(message, signal, lines, this=""))
        out.append("}")
        return out

    def frame_fns(self, message):
        name = self.struct_name(message)
        quantity = validator.effective_quantity(message)
        index_param = "uint8_t index" if quantity > 1 else ""
        id_expr = "kFrameId + index" if quantity > 1 else "kFrameId"
        return [
            f"lhal::CanFrame {name}::ToFrame({index_param}) const {{",
            "  lhal::CanFrame frame;",
            f"  frame.id = {id_expr};",
            "  frame.len = kDlc;",
            "  Pack(frame.data);",
            "  return frame;",
            "}",
            "",
            f"{name} {name}::FromFrame(const lhal::CanFrame& frame) {{",
            f"  {name} msg;",
            "  msg.Unpack(frame.data);",
            "  return msg;",
            "}",
        ]


def provenance(paths, contents):
    digest = hashlib.sha256()
    for path, text in sorted(zip(paths, contents)):
        digest.update(text.encode("utf-8"))
    files = ", ".join(sorted(p.rsplit("/spec/", 1)[-1] for p in paths))
    return (f"// Generated by //lib/codegen/cpp:gen_can_lib — DO NOT EDIT.\n"
            f"// Spec: {files}\n"
            f"// Spec content sha256: {digest.hexdigest()[:16]}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--boards", required=True, help="comma-separated board stems")
    parser.add_argument("spec_files", nargs="+")
    args = parser.parse_args()

    contents = []
    for path in args.spec_files:
        with open(path, encoding="utf-8") as f:
            contents.append(f.read())
    spec = loader.Spec({p: loader.parse_file(t, p) for p, t in zip(args.spec_files, contents)})
    errors, _ = validator.validate(spec)
    if errors:
        _die("spec is invalid:\n" + "\n".join(errors))

    gen = Generator(spec)
    expected = sorted(args.boards.split(","))
    if expected != list(gen.boards):
        _die(f"BUILD lists boards {expected} but the spec has message files for "
             f"{list(gen.boards)} — update CAN_BOARDS in lib/codegen/cpp/BUILD.bazel")

    stamp = provenance(args.spec_files, contents)
    out = pathlib.Path(args.out_dir)
    out.joinpath("lhre_can_types.hpp").write_text(gen.types_header(stamp), encoding="utf-8")
    out.joinpath("lhre_can_types.cpp").write_text(
        gen.types_source(stamp, "lhre_can_types.hpp"), encoding="utf-8")
    umbrella = [stamp, "#pragma once", ""]
    umbrella.append("// Everything on the bus. Prefer depending on the per-board libraries")
    umbrella.append("// (//lib/codegen/cpp:can_<board>) so firmware pulls in only what it uses.")
    umbrella.append('#include "lhre_can_types.hpp"  // IWYU pragma: export')
    for board in gen.boards:
        gen_header = f"lhre_can_{board}.hpp"
        umbrella.append(f'#include "{gen_header}"  // IWYU pragma: export')
        out.joinpath(gen_header).write_text(
            gen.board_header(stamp, board, "lhre_can_types.hpp"), encoding="utf-8")
        out.joinpath(f"lhre_can_{board}.cpp").write_text(
            gen.board_source(stamp, board, gen_header), encoding="utf-8")
    out.joinpath("lhre_can.hpp").write_text("\n".join(umbrella) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
