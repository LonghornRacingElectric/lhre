"""The off-vehicle wire contract: proto types and the lock manifest.

The type a telemetry field takes in the snapshot proto is derived from its
encoding and logical kind unless an existing deployed field carries an
explicit compatibility override. This module is the one place that rule
lives: the lock file, editor, proto generator, and ingest generator all call
it, so they cannot disagree.

The resulting type is part of the wire contract, which is why it lands in
wire.lock: changing an encoding can flip a non-overridden proto type without
changing any id, and the lock diff makes that visible in review.
"""

from lib.spec.ir import validator
from lib.spec.proto import can_spec_pb2

PROTO_SCALAR_TYPES = {
    can_spec_pb2.PROTO_FLOAT: "float",
    can_spec_pb2.PROTO_DOUBLE: "double",
    can_spec_pb2.PROTO_INT32: "int32",
    can_spec_pb2.PROTO_UINT32: "uint32",
    can_spec_pb2.PROTO_INT64: "int64",
    can_spec_pb2.PROTO_UINT64: "uint64",
    can_spec_pb2.PROTO_BOOL: "bool",
    can_spec_pb2.PROTO_STRING: "string",
    can_spec_pb2.PROTO_BYTES: "bytes",
}


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


def scalar_type(value):
    try:
        return PROTO_SCALAR_TYPES[value]
    except KeyError as error:
        raise ValueError(f"unsupported protobuf scalar type {value}") from error


def field_types(spec):
    """Every telemetry-bound field's wire type.

    Returns {(group, field): (type, element_count)} where element_count
    is None for scalars and the array bound for repeated fields.
    """
    out = {}
    slots = {}
    for _, message, signal, bit, telemetry in validator.telemetry_bindings(spec):
        key = _binding_key(telemetry)
        override = telemetry.wire_type
        element_type = scalar_type(override) if override else (
            bit_proto_type() if bit is not None else proto_type(signal)
        )
        if telemetry.repeated:
            # Each binding is one slot per frame; the block repeats over
            # the message's quantity-expanded ID range.
            slots[key] = slots.get(key, 0) + validator.effective_quantity(message)
            out[key] = (f"repeated {element_type}", slots[key])
        else:
            out[key] = (element_type, None)
    for _, group in spec.groups():
        for field in group.external_field:
            ptype = scalar_type(field.type)
            out[(group.name, field.name)] = (
                f"repeated {ptype}" if field.repeated else ptype,
                len(field.element_names) if field.element_names else None,
            )
    return out


def field_ids(spec):
    """Stable protobuf field numbers keyed by (group, field)."""
    ids = {}
    for _, _, _, _, telemetry in validator.telemetry_bindings(spec):
        ids[_binding_key(telemetry)] = telemetry.id
    for _, group in spec.groups():
        for field in group.external_field:
            ids[(group.name, field.name)] = field.id
    return ids


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
# each group's root snapshot id and tombstones. This is what the gateway
# publishes and the telemetry server decodes: changing an existing line breaks
# deployed consumers and archived data, so it is committed and reviewed
# rather than silently regenerated. Types are derived from the spec's
# encodings unless a deployed field has a compatibility override
# (lib/spec/ir/wire.py). Encoding edits can still change a non-overridden type.
"""


def manifest(spec):
    """The wire.lock contents for a spec."""
    types = field_types(spec)
    ids = field_ids(spec)

    by_group = {}
    for (group, field), (ptype, count) in types.items():
        rendered = f"{ptype}[{count}]" if count is not None else ptype
        by_group.setdefault(group, []).append((ids[(group, field)], field, rendered))

    lines = [HEADER]
    lines.extend(["[Root]", "Root.time 1 int64", "Root.packet_id 2 int64"])
    for _, group in sorted(spec.groups(), key=lambda item: item[1].snapshot_id):
        lines.append(f"Root.{group.name} {group.snapshot_id} message {group.name}")
    for spec_file in spec.files.values():
        for reserved in sorted(spec_file.groups.reserved_snapshot_ids):
            lines.append(f"Root RESERVED_ID {reserved}")
        for reserved in sorted(spec_file.groups.reserved_snapshot_names):
            lines.append(f"Root RESERVED_NAME {reserved}")
    lines.append("")
    for _, group in sorted(spec.groups(), key=lambda g: g[1].name):
        entries = sorted(by_group.get(group.name, []))
        lines.append(f"[{group.name}] snapshot_id={group.snapshot_id} next_free_id={group.next_free_id}")
        for field_id, field, rendered in entries:
            lines.append(f"{group.name}.{field} {field_id} {rendered}")
        for reserved in sorted(group.reserved_ids):
            lines.append(f"{group.name} RESERVED_ID {reserved}")
        for reserved in sorted(group.reserved_names):
            lines.append(f"{group.name} RESERVED_NAME {reserved}")
        lines.append("")
    for _, enum_type in sorted(spec.enum_types(), key=lambda item: item[1].name):
        prefix = f"Enum:{enum_type.name}"
        lines.append(f"[{prefix}]")
        for value in sorted(enum_type.value, key=lambda item: item.number):
            lines.append(f"{prefix}.{value.name} {value.number} enum")
        for reserved in sorted(enum_type.reserved_numbers):
            lines.append(f"{prefix} RESERVED_ID {reserved}")
        for reserved in sorted(enum_type.reserved_names):
            lines.append(f"{prefix} RESERVED_NAME {reserved}")
        lines.append("")
    return "\n".join(lines)
