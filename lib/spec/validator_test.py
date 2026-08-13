"""Unit tests for the §5 invariants, on synthetic specs. The real spec
files are checked by //spec:validate; these tests pin down that each rule
actually fires (and that valid constructs don't)."""

import unittest

from lib.spec import canonical, loader, validator
from lib.spec.proto import can_spec_pb2

REGISTRY = """
bus { name: "Critical" bitrate_bps: 1000000 }
enum_type {
  name: "Mode"
  value { name: "OFF" number: 0 }
  value { name: "ON" number: 1 }
}
groups {
  group { name: "Vehicle" next_free_id: 2 }
}
"""

# A minimal valid message binding Vehicle id 1.
MESSAGE = """
message {
  name: "STATUS"
  can_id: 0x100
  from_board: "VCU"
  bus: "Critical"
  dlc: 8
  frequency_hz: 10
  signal {
    name: "speed"
    encoding { start_bit: 0 bit_length: 16 scale: 0.01 }
    logical { physical { unit: "kph" } }
    telemetry { group: "Vehicle" field: "speed" id: 1 }
  }
}
"""


def _validate(*files):
    parsed = {f"file{i}.textproto": loader.parse_file(text) for i, text in enumerate(files)}
    return validator.validate(loader.Spec(parsed))


class ValidatorTest(unittest.TestCase):
    def assert_error(self, errors, fragment):
        self.assertTrue(any(fragment in e for e in errors),
                        f"no error contains {fragment!r}; got: {errors}")

    def test_valid_spec_passes(self):
        errors, warnings = _validate(REGISTRY, MESSAGE)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    # Rule 1: ledger partition.
    def test_missing_tombstone(self):
        registry = REGISTRY.replace("next_free_id: 2", "next_free_id: 3")
        errors, _ = _validate(registry, MESSAGE)
        self.assert_error(errors, "neither live nor reserved")

    def test_id_above_next_free(self):
        errors, _ = _validate(REGISTRY, MESSAGE.replace("id: 1", "id: 7"))
        self.assert_error(errors, "bump next_free_id")

    def test_id_both_live_and_reserved(self):
        registry = REGISTRY.replace('next_free_id: 2', "next_free_id: 2 reserved_ids: 1")
        errors, _ = _validate(registry, MESSAGE)
        self.assert_error(errors, "both live and reserved")

    def test_tombstoned_name_reused(self):
        registry = REGISTRY.replace('next_free_id: 2', 'next_free_id: 2 reserved_names: "speed"')
        errors, _ = _validate(registry, MESSAGE)
        self.assert_error(errors, "tombstoned")

    def test_duplicate_id_in_group(self):
        other = MESSAGE.replace("STATUS", "STATUS2").replace("0x100", "0x101").replace(
            'field: "speed"', 'field: "speed2"')
        errors, _ = _validate(REGISTRY, MESSAGE, other)
        self.assert_error(errors, "telemetry id 1 already used")

    # Rule 2: global field-name uniqueness.
    def test_same_name_in_two_groups(self):
        registry = REGISTRY.replace(
            'group { name: "Vehicle" next_free_id: 2 }',
            'group { name: "Vehicle" next_free_id: 2 } group { name: "Other" next_free_id: 2 }')
        other = MESSAGE.replace("STATUS", "STATUS2").replace("0x100", "0x101").replace(
            '"Vehicle"', '"Other"')
        errors, _ = _validate(registry, MESSAGE, other)
        self.assert_error(errors, "already used by")

    # Rule 3: frame validity.
    def test_bit_overlap(self):
        message = MESSAGE.replace(
            "logical { physical { unit: \"kph\" } }\n    telemetry { group: \"Vehicle\" field: \"speed\" id: 1 }",
            "logical { physical { unit: \"kph\" } }")
        message = message.replace(
            "}\n}",
            """}
  signal {
    name: "overlapping"
    encoding { start_bit: 8 bit_length: 8 }
    logical { boolean: false }
  }
}""")
        # bit_length 8 boolean also errors; check the overlap error fires.
        errors, _ = _validate(REGISTRY, message)
        self.assert_error(errors, "overlaps signal")

    def test_mux_overlap_allowed(self):
        message = """
message {
  name: "DEBUG"
  can_id: 0x200
  from_board: "VCU"
  bus: "Critical"
  dlc: 8
  signal {
    name: "channel"
    encoding { start_bit: 0 bit_length: 8 }
    logical { physical { unit: "" } mux_selector: true }
  }
  signal {
    name: "a"
    encoding { start_bit: 8 bit_length: 32 muxed: true mux_value: 0 }
    logical { physical { unit: "" } }
  }
  signal {
    name: "b"
    encoding { start_bit: 8 bit_length: 16 muxed: true mux_value: 1 }
    logical { physical { unit: "" } }
  }
}
"""
        errors, _ = _validate(REGISTRY, MESSAGE, message)
        self.assertEqual(errors, [])

    def test_muxed_without_selector(self):
        message = MESSAGE.replace("start_bit: 0 bit_length: 16", "start_bit: 0 bit_length: 16 muxed: true")
        errors, _ = _validate(REGISTRY, message)
        self.assert_error(errors, "no signal marked logical.mux_selector")

    def test_signal_past_dlc(self):
        errors, _ = _validate(REGISTRY, MESSAGE.replace("dlc: 8", "dlc: 1"))
        self.assert_error(errors, "frame has 8 bits")

    def test_big_endian_occupancy(self):
        enc = can_spec_pb2.Encoding(start_bit=55, bit_length=16, byte_order=can_spec_pb2.BIG_ENDIAN)
        self.assertEqual(validator.occupied_bits(enc), list(range(55, 47, -1)) + list(range(63, 55, -1)))

    # Rule 4: CAN ID uniqueness with quantity expansion.
    def test_quantity_range_collision(self):
        first = MESSAGE.replace("can_id: 0x100", "can_id: 0x100 quantity: 4")
        second = MESSAGE.replace("STATUS", "STATUS2").replace("can_id: 0x100", "can_id: 0x103").replace(
            'field: "speed" id: 1', 'field: "speed2" id: 1')
        errors, _ = _validate(REGISTRY, first, second)
        self.assert_error(errors, "CAN ID 0x103 already used")

    # Rule 5: referential integrity.
    def test_unknown_references(self):
        errors, _ = _validate(REGISTRY, MESSAGE.replace('bus: "Critical"', 'bus: "Nope"'))
        self.assert_error(errors, "unknown bus")
        errors, _ = _validate(REGISTRY, MESSAGE.replace('group: "Vehicle"', 'group: "Nope"'))
        self.assert_error(errors, "unknown telemetry group")
        message = MESSAGE.replace('logical { physical { unit: "kph" } }', 'logical { enum_type: "Nope" }')
        errors, _ = _validate(REGISTRY, message)
        self.assert_error(errors, "unknown enum_type")

    # Rule 6: encoding sanity.
    def test_signed_needs_two_bits(self):
        message = MESSAGE.replace("bit_length: 16", "bit_length: 1 sign: SIGNED")
        errors, _ = _validate(REGISTRY, message)
        self.assert_error(errors, "signed signals need bit_length >= 2")

    def test_declared_range_exceeds_representable(self):
        message = MESSAGE.replace("scale: 0.01", "scale: 0.01 min: 0 max: 1000")
        errors, _ = _validate(REGISTRY, message)
        self.assert_error(errors, "exceeds representable")

    def test_enum_must_fit(self):
        registry = REGISTRY.replace('value { name: "ON" number: 1 }', 'value { name: "ON" number: 9 }')
        message = MESSAGE.replace(
            'encoding { start_bit: 0 bit_length: 16 scale: 0.01 }\n    logical { physical { unit: "kph" } }',
            'encoding { start_bit: 0 bit_length: 3 }\n    logical { enum_type: "Mode" }')
        errors, _ = _validate(registry, message)
        self.assert_error(errors, "does not fit in 3 bits")

    # Rule 7: tier resolution (totality + precedence).
    def test_tier_resolution(self):
        message = can_spec_pb2.CanMessage(frequency_hz=100)
        self.assertEqual(validator.resolve_tier(message, can_spec_pb2.Telemetry()), can_spec_pb2.FAST)
        message.frequency_hz = 10
        self.assertEqual(validator.resolve_tier(message, can_spec_pb2.Telemetry()), can_spec_pb2.MEDIUM)
        message.frequency_hz = 0
        self.assertEqual(validator.resolve_tier(message, can_spec_pb2.Telemetry()), can_spec_pb2.SLOW)
        message.default_rate_tier = can_spec_pb2.FAST
        self.assertEqual(validator.resolve_tier(message, can_spec_pb2.Telemetry()), can_spec_pb2.FAST)
        explicit = can_spec_pb2.Telemetry(rate_tier=can_spec_pb2.SLOW)
        self.assertEqual(validator.resolve_tier(message, explicit), can_spec_pb2.SLOW)

    # Rule 8: bus-load advisory.
    def test_bus_load_warning(self):
        message = MESSAGE.replace("frequency_hz: 10", "frequency_hz: 8000")
        errors, warnings = _validate(REGISTRY, message)
        self.assertEqual(errors, [])
        self.assert_error(warnings, "estimated worst-case load")

    # Bitfield expansion sanity.
    def test_bitfield_signal_cannot_bind_telemetry_directly(self):
        registry = REGISTRY + """
bitfield_type {
  name: "Flags"
  bit { name: "a" telemetry { group: "Vehicle" field: "flag_a" id: 1 } }
}
"""
        message = MESSAGE.replace(
            'logical { physical { unit: "kph" } }\n    telemetry { group: "Vehicle" field: "speed" id: 1 }',
            'logical { bitfield { bitfield_type: "Flags" } }\n    telemetry { group: "Vehicle" field: "speed" id: 1 }')
        errors, _ = _validate(registry, message)
        self.assert_error(errors, "bind telemetry on their bits")

    # Rule 5: one file per source board.
    def test_from_board_must_match_messages_file(self):
        parsed = {
            "registry.textproto": loader.parse_file(REGISTRY),
            "lib/spec/messages/hvc.textproto": loader.parse_file(MESSAGE),  # from VCU
        }
        errors, _ = validator.validate(loader.Spec(parsed))
        self.assert_error(errors, "does not match its file")
        parsed["lib/spec/messages/vcu.textproto"] = parsed.pop("lib/spec/messages/hvc.textproto")
        errors, _ = validator.validate(loader.Spec(parsed))
        self.assertEqual(errors, [])

    def test_from_board_required(self):
        errors, _ = _validate(REGISTRY, MESSAGE.replace('from_board: "VCU"\n  ', ""))
        self.assert_error(errors, "from_board is required")

    # Indexed arrays: repeated bindings + quantity express e.g. cell temps.
    def test_repeated_array_block(self):
        message = """
message {
  name: "CELLS"
  can_id: 0x200
  quantity: 4
  from_board: "HVC"
  bus: "Critical"
  dlc: 4
  signal {
    name: "slot0"
    encoding { bit_length: 16 }
    logical { physical { unit: "V" } }
    telemetry { group: "Vehicle" field: "cells" id: 1 repeated: true }
  }
  signal {
    name: "slot1"
    encoding { start_bit: 16 bit_length: 16 }
    logical { physical { unit: "V" } }
    telemetry { group: "Vehicle" field: "cells" id: 1 repeated: true array_index: 1 }
  }
}
"""
        errors, _ = _validate(REGISTRY, message)
        self.assertEqual(errors, [])
        # Same slot twice is a collision.
        errors, _ = _validate(REGISTRY, message.replace("array_index: 1", ""))
        self.assert_error(errors, "array_index 0 already bound")

    def test_canonical_roundtrip_stable(self):
        spec_file = loader.parse_file(REGISTRY + MESSAGE)
        once = canonical.canonicalize(spec_file)
        twice = canonical.canonicalize(loader.parse_file(once))
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
