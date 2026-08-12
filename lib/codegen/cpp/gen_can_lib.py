"""Generates the firmware CAN library (C++) from the spec.

Consumes only the wire and logical layers (design doc §4). Emitted API,
per message: a struct in lhre::can with constexpr frame constants, raw
integer fields, integer-only Pack/Unpack (no FPU), inline float
accessors for scaled signals, and lhal::CanFrame conversions. Telemetry
attributes are ignored here — they never reach firmware.

Usage: gen_can_lib.py --header out.hpp --source out.cpp spec1.textproto ...
"""

import argparse
import hashlib
import re
import sys

from lib.spec import loader, validator
from lib.spec.proto import can_spec_pb2


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
        self.messages = sorted((m for _, m in spec.messages()), key=lambda m: m.can_id)

    # ---- naming ----------------------------------------------------------

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

    # ---- header ----------------------------------------------------------

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

    def enum_decl(self, enum_type):
        out = [f"// {enum_type.description}" if enum_type.description else f"// {enum_type.name}"]
        width = max((v.number for v in enum_type.value), default=0)
        underlying = next(w for w in (8, 16, 32) if width < (1 << w))
        out.append(f"enum class {enum_type.name} : uint{underlying}_t {{")
        for value in enum_type.value:
            comment = f"  // {value.description}" if value.description else ""
            out.append(f"  k{camel(value.name)} = {value.number},{comment}")
        out.append("};")
        out.append(f"// Wire name of the value (\"{enum_type.value[0].name}\"...), \"?\" if out of range.")
        out.append(f"const char* ToString({enum_type.name} value);")
        out.append("")
        return out

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

    def header(self, provenance):
        h = [provenance]
        h.append("""#pragma once

#include <cstdint>

#include "lhal/can.hpp"

namespace lhre::can {
""")
        for enum_name in sorted(self.enums):
            h.extend(self.enum_decl(self.enums[enum_name]))
        for message in self.messages:
            h.extend(self.message_decl(message))
        h.append("""// Per-message metadata for RTOS CAN task tables, sorted by frame ID.
struct MessageMeta {
  uint32_t frame_id;
  uint8_t dlc;
  uint8_t quantity;   // consecutive frame IDs from frame_id
  float frequency_hz; // 0 = aperiodic
};
""")
        h.append("inline constexpr MessageMeta kMessageMeta[] = {")
        for message in self.messages:
            n = self.struct_name(message)
            h.append(f"    {{{n}::kFrameId, {n}::kDlc, {n}::kQuantity, {n}::kFrequencyHz}},")
        h.append("};")
        h.append("inline constexpr uint32_t kMessageCount = sizeof(kMessageMeta) / sizeof(kMessageMeta[0]);")
        h.append("")
        h.append("}  // namespace lhre::can")
        return "\n".join(h) + "\n"

    # ---- source ----------------------------------------------------------

    def source(self, provenance, header_name):
        s = [provenance]
        s.append(f'#include "{header_name}"\n')
        s.append("""#include <cstring>

namespace lhre::can {
namespace {

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
""")
        for enum_name in sorted(self.enums):
            s.extend(self.tostring_fn(self.enums[enum_name]))
            s.append("")
        for message in self.messages:
            s.extend(self.pack_fn(message))
            s.append("")
            s.extend(self.unpack_fn(message))
            s.append("")
            s.extend(self.frame_fns(message))
            s.append("")
        s.append("}  // namespace lhre::can")
        return "\n".join(s) + "\n"

    def tostring_fn(self, enum_type):
        out = [f"const char* ToString({enum_type.name} value) {{", "  switch (value) {"]
        for value in enum_type.value:
            out.append(f'    case {enum_type.name}::k{camel(value.name)}: return "{value.name}";')
        out.append("  }")
        out.append('  return "?";')
        out.append("}")
        return out

    def _mux_guard(self, message, signal, body_lines, this):
        if not signal.encoding.muxed:
            return body_lines
        sel = self.selector(message)
        guard = f"  if ({this}{sel.name} == {signal.encoding.mux_value}u) {{"
        return [guard] + ["  " + line for line in body_lines] + ["  }"]

    def _field_expr(self, signal):
        """Struct member holding the signal's raw value, in pack casts."""
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
    parser.add_argument("--header", required=True)
    parser.add_argument("--source", required=True)
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
    stamp = provenance(args.spec_files, contents)
    header_name = args.header.rsplit("/", 1)[-1]
    with open(args.header, "w", encoding="utf-8") as f:
        f.write(gen.header(stamp))
    with open(args.source, "w", encoding="utf-8") as f:
        f.write(gen.source(stamp, header_name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
