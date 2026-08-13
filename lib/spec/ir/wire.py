"""The off-vehicle wire contract: proto types and the lock manifest.

The type a telemetry field takes in the snapshot proto is *derived*, not
declared — encoding sign/width/scale/offset plus the logical kind
determine it completely (see lib/spec/README.md). This module is the one
place that rule lives: the lock file, the editor's type display, and the
future proto/ingest generators all call it, so they cannot disagree.

The derived type is part of the wire contract, which is why it lands in
wire.lock: changing a signal's `scale` flips its proto type without
changing any id, and the lock diff is what makes that visible in review.
"""

from lib.spec.ir import validator
from lib.spec.proto import can_spec_pb2

# Proto type for a repeated numeric field's elements. One shared message
# for every indexed array (cell temps, cell voltages, ...) rather than a
# generated per-field entry type.
INDEXED_ELEMENT = "IndexedFloat"


def proto_type(signal):
    """Snapshot-proto type for one non-bitfield signal.

    Bitfield signals have no type of their own — each of their bits is a
    separate bool field; call bit_proto_type() for those.
    """
    logical, encoding = signal.logical, signal.encoding
    kind = logical.WhichOneof("kind")
    if kind == "boolean":
        return "bool"
    if kind == "enum_type":
        return logical.enum_type
    if kind == "bitfield":
        raise ValueError("bitfield signals bind types per bit")

    scale = validator.effective_scale(encoding)
    offset = encoding.offset
    if scale == 1.0 and float(offset).is_integer():
        # Unscaled: the raw value *is* the physical value, so an integer
        # is both truthful and lossless. Pick the smallest that holds the
        # shifted raw range.
        lo, hi = validator.raw_range(encoding)
        lo, hi = lo + int(offset), hi + int(offset)
        if lo >= 0:
            return "uint32" if hi <= 0xFFFFFFFF else "uint64"
        if lo >= -(1 << 31) and hi <= (1 << 31) - 1:
            return "int32"
        return "int64"
    # Scaled: a real-valued measurement. float32 carries 24 mantissa bits,
    # so raw fields up to 24 bits survive the conversion without distinct
    # values collapsing; wider needs double (GPS at 1e-7 is the classic).
    return "float" if encoding.bit_length <= 24 else "double"


def bit_proto_type():
    return "bool"


def _binding_key(telemetry):
    return (telemetry.group, telemetry.field)


def field_types(spec):
    """Every telemetry-bound field's wire type.

    Returns {(group, field): (type, element_count)} where element_count
    is None for scalars and the array bound for repeated fields.
    """
    out = {}
    slots = {}
    for _, message, signal, bit, telemetry in validator.telemetry_bindings(spec):
        key = _binding_key(telemetry)
        if telemetry.repeated:
            # Each binding is one slot per frame; the block repeats over
            # the message's quantity-expanded ID range.
            slots[key] = slots.get(key, 0) + validator.effective_quantity(message)
            out[key] = (f"repeated {INDEXED_ELEMENT}", slots[key])
        elif bit is not None:
            out[key] = (bit_proto_type(), None)
        else:
            out[key] = (proto_type(signal), None)
    return out


def signal_types(spec):
    """Derived types keyed by "MESSAGE.signal" (and "MESSAGE.signal.bit"
    for bitfield bits) — what the editor displays. Covers every signal,
    telemetry-bound or not, since the type is a property of the encoding."""
    out = {}
    for _, message in spec.messages():
        for signal in message.signal:
            path = f"{message.name}.{signal.name}"
            if signal.logical.WhichOneof("kind") == "bitfield":
                out[path] = "bool per bit"
            else:
                try:
                    out[path] = proto_type(signal)
                except ValueError:
                    continue
    return out


HEADER = """# Generated wire contract — DO NOT EDIT.
# Update with: bazel run //tools/spec:lock
#
# One line per telemetry field: <group>.<field> <id> <proto type>, plus
# each group's tombstones. This is what the gateway publishes and the
# telemetry server decodes: changing any existing line breaks deployed
# consumers and archived data, which is why it is committed and reviewed
# rather than silently regenerated. Types are derived from the spec's
# encodings (lib/spec/wire.py) — note that editing a signal's `scale` can
# change its type here without changing its id.
"""


def manifest(spec):
    """The wire.lock contents for a spec."""
    types = field_types(spec)
    ids = {}
    for _, _, _, _, telemetry in validator.telemetry_bindings(spec):
        ids[_binding_key(telemetry)] = telemetry.id

    by_group = {}
    for (group, field), (ptype, count) in types.items():
        rendered = f"{ptype}[{count}]" if count is not None else ptype
        by_group.setdefault(group, []).append((ids[(group, field)], field, rendered))

    lines = [HEADER]
    for _, group in sorted(spec.groups(), key=lambda g: g[1].name):
        entries = sorted(by_group.get(group.name, []))
        lines.append(f"[{group.name}] next_free_id={group.next_free_id}")
        for field_id, field, rendered in entries:
            lines.append(f"{group.name}.{field} {field_id} {rendered}")
        for reserved in sorted(group.reserved_ids):
            lines.append(f"{group.name} RESERVED_ID {reserved}")
        for reserved in sorted(group.reserved_names):
            lines.append(f"{group.name} RESERVED_NAME {reserved}")
        lines.append("")
    return "\n".join(lines)
