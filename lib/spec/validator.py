"""Semantic validation of the CAN spec (design doc §5).

Pure function of the parsed spec files — no git history, no previously
generated outputs. `validate()` returns (errors, warnings); errors block
the merge (//spec:validate), warnings are advisory. Every message names
the file, message, signal, and violated rule.

Rule numbers in parentheses refer to the design doc:
  1. ledger partition invariant per group (IDs and names)
  2. global field-name uniqueness across groups
  3. bit-level frame validity (overlap, fit, mux selector)
  4. CAN ID uniqueness per bus, quantity-expanded
  5. referential integrity (bus / enum_type / bitfield / group)
  6. encoding sanity
  7. tier totality
  8. bus-load advisory (warning only)
"""

import re

from lib.spec.proto import can_spec_pb2

# Tier derivation thresholds (design doc §7). Live here so every
# generator sees the same resolved tier.
FAST_MIN_HZ = 50.0
MEDIUM_MIN_HZ = 5.0

BUS_LOAD_WARN_FRACTION = 0.8

# Valid CAN FD DLC byte counts beyond classic 8.
FD_DLCS = (12, 16, 20, 24, 32, 48, 64)

_UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_LOWER_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
_MESSAGES_FILE = re.compile(r"(?:^|/)messages/([a-z][a-z0-9_]*)\.textproto$")


def effective_scale(encoding):
    """Scale with the textproto "0 means unset" rule applied."""
    return encoding.scale if encoding.scale != 0 else 1.0


def effective_quantity(message):
    return message.quantity if message.quantity != 0 else 1


def raw_range(encoding):
    """(min, max) raw integer values representable by the encoding."""
    n = encoding.bit_length
    if encoding.sign == can_spec_pb2.SIGNED:
        return -(1 << (n - 1)), (1 << (n - 1)) - 1
    return 0, (1 << n) - 1


def occupied_bits(encoding):
    """Linear bit positions (byte*8 + bit, LSB-0) covered by a signal.

    LITTLE_ENDIAN: start_bit is the LSB, positions ascend. BIG_ENDIAN:
    start_bit is the MSB in DBC/Motorola order — descending within a
    byte, then bit 7 of the next byte.
    """
    if encoding.byte_order == can_spec_pb2.LITTLE_ENDIAN:
        return list(range(encoding.start_bit, encoding.start_bit + encoding.bit_length))
    bits = []
    byte, bit = divmod(encoding.start_bit, 8)
    for _ in range(encoding.bit_length):
        bits.append(byte * 8 + bit)
        byte, bit = (byte + 1, 7) if bit == 0 else (byte, bit - 1)
    return bits


def resolve_tier(message, telemetry):
    """Concrete RateTier for a telemetry binding (§7 resolution order)."""
    if telemetry.rate_tier != can_spec_pb2.TIER_UNSPECIFIED:
        return telemetry.rate_tier
    if message.default_rate_tier != can_spec_pb2.TIER_UNSPECIFIED:
        return message.default_rate_tier
    if message.frequency_hz >= FAST_MIN_HZ:
        return can_spec_pb2.FAST
    if message.frequency_hz >= MEDIUM_MIN_HZ:
        return can_spec_pb2.MEDIUM
    return can_spec_pb2.SLOW


def telemetry_bindings(spec):
    """Every telemetry binding in the spec, bitfields expanded.

    Yields (filename, message, signal, bit_or_None, Telemetry). Bitfield
    signals carry no binding themselves; each telemetry-bound bit of the
    referenced type binds one bool field.
    """
    bitfields = {b.name: b for _, b in spec.bitfield_types()}
    for filename, message in spec.messages():
        for signal in message.signal:
            if signal.logical.WhichOneof("kind") == "bitfield":
                bitfield = bitfields.get(signal.logical.bitfield.bitfield_type)
                if bitfield is None:
                    continue  # reported by referential integrity
                for bit in bitfield.bit:
                    if bit.HasField("telemetry"):
                        yield filename, message, signal, bit, bit.telemetry
            elif signal.HasField("telemetry"):
                yield filename, message, signal, None, signal.telemetry


def classic_frame_bits(dlc):
    """Worst-case bits on the wire for a classic standard-ID data frame:
    47 bits of overhead + data, plus maximal bit stuffing."""
    data_bits = 8 * dlc
    return 47 + data_bits + (34 + data_bits - 1) // 4


class _Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, where, rule, text):
        self.errors.append(f"{where}: {text} (rule {rule})")

    def warning(self, where, rule, text):
        self.warnings.append(f"{where}: {text} (rule {rule})")


def _check_registries(spec, report):
    """Duplicate names in registries and messages (rule 5 groundwork)."""
    for kind, entries in (
        ("bus", spec.buses()),
        ("enum_type", spec.enum_types()),
        ("bitfield_type", spec.bitfield_types()),
        ("group", spec.groups()),
    ):
        seen = {}
        for filename, entry in entries:
            if entry.name in seen:
                report.error(filename, 5, f"duplicate {kind} '{entry.name}' (also in {seen[entry.name]})")
            seen[entry.name] = filename
    seen = {}
    for filename, message in spec.messages():
        where = f"{filename}: {message.name}"
        if not _UPPER_SNAKE.match(message.name):
            report.error(where, 5, "message name must be UPPER_SNAKE")
        if message.name in seen:
            report.error(where, 5, f"duplicate message name (also in {seen[message.name]})")
        seen[message.name] = filename
        if not message.from_board:
            report.error(where, 5, "from_board is required")
        # One file per source board: messages/<board>.textproto may only
        # hold messages that board sends. Codegen leans on this to emit
        # one namespace/library per board.
        stem = _MESSAGES_FILE.search(filename)
        if stem and message.from_board and message.from_board.lower() != stem.group(1):
            report.error(where, 5,
                         f"from_board '{message.from_board}' does not match its file "
                         f"(messages/{stem.group(1)}.textproto holds {stem.group(1).upper()}'s messages)")

    for filename, enum_type in spec.enum_types():
        seen_names, seen_numbers = {}, {}
        for value in enum_type.value:
            where = f"{filename}: {enum_type.name}.{value.name}"
            if value.name in seen_names:
                report.error(where, 5, "duplicate enum value name")
            if value.number in seen_numbers:
                report.error(where, 5, f"duplicate enum value number {value.number} (also {seen_numbers[value.number]})")
            seen_names[value.name] = value
            seen_numbers[value.number] = value.name

    for filename, bitfield_type in spec.bitfield_types():
        seen_bits = set()
        for bit in bitfield_type.bit:
            if bit.name in seen_bits:
                report.error(f"{filename}: {bitfield_type.name}.{bit.name}", 5, "duplicate bitfield bit name")
            seen_bits.add(bit.name)


def _check_signal_encoding(where, message, signal, enums, bitfields, report):
    """Rules 5 and 6 for one signal. Returns False if the encoding is too
    broken for overlap analysis."""
    if not signal.HasField("encoding") or not signal.HasField("logical"):
        report.error(where, 6, "signal must have both encoding and logical")
        return False
    enc = signal.encoding
    kind = signal.logical.WhichOneof("kind")
    if kind is None:
        report.error(where, 5, "logical.kind must be set")
        return False

    if enc.bit_length < 1:
        report.error(where, 6, "bit_length must be >= 1")
        return False
    if enc.bit_length > 64:
        report.error(where, 6, "bit_length must be <= 64")
        return False
    if enc.sign == can_spec_pb2.SIGNED and enc.bit_length < 2:
        report.error(where, 6, "signed signals need bit_length >= 2")

    if kind == "enum_type":
        enum_type = enums.get(signal.logical.enum_type)
        if enum_type is None:
            report.error(where, 5, f"unknown enum_type '{signal.logical.enum_type}'")
        else:
            if enc.sign != can_spec_pb2.UNSIGNED:
                report.error(where, 6, "enum signals must be UNSIGNED")
            worst = max((v.number for v in enum_type.value), default=0)
            if worst > raw_range(enc)[1]:
                report.error(where, 6, f"enum value {worst} does not fit in {enc.bit_length} bits")
    elif kind == "boolean":
        if enc.bit_length != 1:
            report.error(where, 6, "boolean signals must have bit_length 1")
    elif kind == "bitfield":
        bitfield = bitfields.get(signal.logical.bitfield.bitfield_type)
        if bitfield is None:
            report.error(where, 5, f"unknown bitfield '{signal.logical.bitfield.bitfield_type}'")
        elif len(bitfield.bit) > enc.bit_length:
            report.error(where, 6, f"bitfield {bitfield.name} has {len(bitfield.bit)} bits, encoding only {enc.bit_length}")
        if signal.HasField("telemetry"):
            report.error(where, 6, "bitfield signals bind telemetry on their bits in types.textproto, not on the signal")

    if signal.logical.mux_selector:
        if kind not in ("physical", "enum_type"):
            report.error(where, 3, "mux selector must be a physical integer or enum signal")
        if enc.sign != can_spec_pb2.UNSIGNED or effective_scale(enc) != 1.0 or enc.offset != 0:
            report.error(where, 3, "mux selector must be raw unsigned (scale 1, offset 0)")
        if enc.muxed:
            report.error(where, 3, "mux selector cannot itself be muxed")

    scale = effective_scale(enc)
    if enc.min > enc.max:
        report.error(where, 6, f"min {enc.min} > max {enc.max}")
    elif enc.min != 0 or enc.max != 0:
        raw_lo, raw_hi = raw_range(enc)
        physical = sorted((raw_lo * scale + enc.offset, raw_hi * scale + enc.offset))
        if enc.min < physical[0] - 1e-9 or enc.max > physical[1] + 1e-9:
            report.error(
                where, 6,
                f"declared range [{enc.min}, {enc.max}] exceeds representable [{physical[0]}, {physical[1]}]")
    return True


def _check_frames(spec, enums, bitfields, buses, report):
    """Rules 3, 4, 5 (bus refs), 6 per message; returns nothing."""
    ids_per_bus = {}
    for filename, message in spec.messages():
        where = f"{filename}: {message.name}"
        if message.bus not in buses:
            report.error(where, 5, f"unknown bus '{message.bus}'")
        bus_fd = buses.get(message.bus).fd if message.bus in buses else False
        if message.dlc > 8 and (not bus_fd or message.dlc not in FD_DLCS):
            report.error(where, 3, f"dlc {message.dlc} invalid for {'FD' if bus_fd else 'classic'} bus '{message.bus}'")

        quantity = effective_quantity(message)
        id_range = range(message.can_id, message.can_id + quantity)
        if id_range.stop - 1 > 0x7FF:
            report.error(where, 4, f"IDs [{id_range.start:#x}, {id_range.stop - 1:#x}] exceed the 11-bit standard range")
        for can_id in id_range:
            claim = ids_per_bus.setdefault(message.bus, {})
            if can_id in claim:
                report.error(where, 4, f"CAN ID {can_id:#x} already used by {claim[can_id]}")
            claim[can_id] = message.name

        selectors = [s for s in message.signal if s.HasField("logical") and s.logical.mux_selector]
        muxed = [s for s in message.signal if s.HasField("encoding") and s.encoding.muxed]
        if len(selectors) > 1:
            report.error(where, 3, f"multiple mux selectors: {', '.join(s.name for s in selectors)}")
        if muxed and not selectors:
            report.error(where, 3, "muxed signals but no signal marked logical.mux_selector")
        if selectors and not muxed:
            report.error(where, 3, f"mux selector '{selectors[0].name}' but no muxed signals")

        seen_signals = set()
        occupied = {}  # linear bit -> (signal name, mux key)
        for signal in message.signal:
            sig_where = f"{where}.{signal.name}"
            if not _LOWER_SNAKE.match(signal.name):
                report.error(sig_where, 5, "signal name must be lower_snake")
            if signal.name in seen_signals:
                report.error(sig_where, 3, "duplicate signal name")
            seen_signals.add(signal.name)
            if not _check_signal_encoding(sig_where, message, signal, enums, bitfields, report):
                continue
            enc = signal.encoding
            bits = occupied_bits(enc)
            if max(bits) >= message.dlc * 8:
                report.error(sig_where, 3, f"signal spans bit {max(bits)}, frame has {message.dlc * 8} bits")
                continue
            # Signals with different mux values may share bits; the
            # selector and plain signals may not overlap anything.
            mux_key = enc.mux_value if enc.muxed else None
            for bit in bits:
                if bit in occupied:
                    other_name, other_key = occupied[bit]
                    if mux_key is None or other_key is None or mux_key == other_key:
                        report.error(sig_where, 3, f"bit {bit} overlaps signal '{other_name}'")
                        break
                occupied[bit] = (signal.name, mux_key)


def _check_ledgers(spec, report):
    """Rules 1, 2, 5 (group refs), 7."""
    groups = {g.name: (f, g) for f, g in spec.groups()}
    live = {name: {} for name in groups}  # group -> id -> (where, field)
    field_names = {}  # bare field name -> where (rule 2)

    for filename, message, signal, bit, telemetry in telemetry_bindings(spec):
        suffix = f".{bit.name}" if bit is not None else ""
        where = f"{filename}: {message.name}.{signal.name}{suffix}"
        if telemetry.group not in groups:
            report.error(where, 5, f"unknown telemetry group '{telemetry.group}'")
            continue
        if not telemetry.field or not _LOWER_SNAKE.match(telemetry.field):
            report.error(where, 1, f"telemetry field '{telemetry.field}' must be lower_snake")
            continue
        if telemetry.id == 0:
            report.error(where, 1, "telemetry id 0 is not a valid proto field number")
            continue
        if resolve_tier(message, telemetry) == can_spec_pb2.TIER_UNSPECIFIED:
            report.error(where, 7, "signal does not resolve to a concrete rate tier")

        # One ledger entry per field name. Repeated fields share one entry
        # (and one id) across array_index bindings; anything else that
        # reuses a name or an id is a collision.
        group_live = live[telemetry.group]
        entry = group_live.get(telemetry.field)
        if entry is None:
            for other_field, other in group_live.items():
                if other["id"] == telemetry.id:
                    report.error(where, 1, f"telemetry id {telemetry.id} already used by '{other_field}' ({other['where']})")
            group_live[telemetry.field] = {
                "id": telemetry.id, "repeated": telemetry.repeated,
                "indexes": {telemetry.array_index}, "where": where,
            }
        else:
            if entry["id"] != telemetry.id:
                report.error(where, 1, f"field '{telemetry.field}' bound with id {telemetry.id} but {entry['where']} uses id {entry['id']}")
            if not (entry["repeated"] and telemetry.repeated):
                report.error(where, 1, f"field '{telemetry.field}' already bound by {entry['where']}")
            elif telemetry.array_index in entry["indexes"]:
                report.error(where, 1, f"field '{telemetry.field}' array_index {telemetry.array_index} already bound by {entry['where']}")
            entry["indexes"].add(telemetry.array_index)

        prior = field_names.setdefault(telemetry.field, (telemetry.group, where))
        if prior[0] != telemetry.group:
            report.error(where, 2, f"field name '{telemetry.field}' already used by {prior[1]}")

    for group_name, (filename, group) in groups.items():
        where = f"{filename}: group {group_name}"
        live_ids = {entry["id"] for entry in live[group_name].values()}
        reserved = set(group.reserved_ids)
        expected = set(range(1, group.next_free_id)) if group.next_free_id else set()
        if overlap := live_ids & reserved:
            report.error(where, 1, f"ids {sorted(overlap)} are both live and reserved")
        if missing := expected - live_ids - reserved:
            report.error(where, 1, f"ids {sorted(missing)} below next_free_id {group.next_free_id} are neither live nor reserved — tombstone them")
        if stray := (live_ids | reserved) - expected:
            report.error(where, 1, f"ids {sorted(stray)} at or above next_free_id {group.next_free_id} — bump next_free_id")
        reserved_names = set(group.reserved_names)
        for field, entry in live[group_name].items():
            if field in reserved_names:
                report.error(entry["where"], 1, f"field name '{field}' is tombstoned in group {group_name}")


def _check_bus_load(spec, buses, report):
    """Rule 8, advisory. Classic frames only; FD accounting can come
    with FD support."""
    load_bps = {}
    for _, message in spec.messages():
        if message.bus in buses and message.dlc <= 8:
            bits = classic_frame_bits(message.dlc)
            load_bps[message.bus] = load_bps.get(message.bus, 0.0) + (
                bits * message.frequency_hz * effective_quantity(message))
    for bus_name, bps in load_bps.items():
        bitrate = buses[bus_name].bitrate_bps
        if bitrate and bps / bitrate > BUS_LOAD_WARN_FRACTION:
            report.warning(
                f"bus {bus_name}", 8,
                f"estimated worst-case load {bps / bitrate:.0%} exceeds {BUS_LOAD_WARN_FRACTION:.0%} of {bitrate} bps")


def _check_bitfield_references(spec, report):
    """A bitfield type with telemetry-bound bits maps its bits to
    specific snapshot fields, so at most one signal may reference it."""
    telemetry_bitfields = {
        b.name for _, b in spec.bitfield_types() if any(bit.HasField("telemetry") for bit in b.bit)
    }
    users = {}
    for filename, message in spec.messages():
        for signal in message.signal:
            if signal.logical.WhichOneof("kind") != "bitfield":
                continue
            name = signal.logical.bitfield.bitfield_type
            where = f"{filename}: {message.name}.{signal.name}"
            if name in telemetry_bitfields and name in users:
                report.error(where, 1, f"bitfield '{name}' has telemetry-bound bits and is already used by {users[name]}")
            users.setdefault(name, where)


def validate(spec):
    """Runs every invariant. Returns (errors, warnings)."""
    report = _Report()
    enums = {e.name: e for _, e in spec.enum_types()}
    bitfields = {b.name: b for _, b in spec.bitfield_types()}
    buses = {b.name: b for _, b in spec.buses()}
    _check_registries(spec, report)
    _check_frames(spec, enums, bitfields, buses, report)
    _check_bitfield_references(spec, report)
    _check_ledgers(spec, report)
    _check_bus_load(spec, buses, report)
    return report.errors, report.warnings
