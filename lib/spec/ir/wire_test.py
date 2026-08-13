"""Unit tests for proto type derivation — the rule that decides what a
telemetry field looks like on the wire, and therefore what every
downstream consumer compiles against."""

import unittest

from lib.spec.ir import loader, wire
from lib.spec.proto import can_spec_pb2


def signal(bit_length, sign=can_spec_pb2.UNSIGNED, scale=0.0, offset=0.0, kind="physical",
           enum_type="Mode"):
    sig = can_spec_pb2.Signal(name="s")
    sig.encoding.bit_length = bit_length
    sig.encoding.sign = sign
    sig.encoding.scale = scale
    sig.encoding.offset = offset
    if kind == "physical":
        sig.logical.physical.unit = "x"
    elif kind == "boolean":
        sig.logical.boolean = False
    elif kind == "enum_type":
        sig.logical.enum_type = enum_type
    return sig


class ProtoTypeTest(unittest.TestCase):
    def test_logical_kinds(self):
        self.assertEqual(wire.proto_type(signal(1, kind="boolean")), "bool")
        self.assertEqual(wire.proto_type(signal(8, kind="enum_type")), "Mode")

    def test_unscaled_is_an_integer(self):
        # The raw value *is* the physical value; float would be a lie.
        self.assertEqual(wire.proto_type(signal(8)), "uint32")
        self.assertEqual(wire.proto_type(signal(8, sign=can_spec_pb2.SIGNED)), "int32")
        self.assertEqual(wire.proto_type(signal(32)), "uint32")
        self.assertEqual(wire.proto_type(signal(33)), "uint64")
        self.assertEqual(wire.proto_type(signal(64, sign=can_spec_pb2.SIGNED)), "int64")

    def test_integral_offset_can_flip_signedness(self):
        # e.g. raw uint8 with offset -40 spans [-40, 215]
        self.assertEqual(wire.proto_type(signal(8, offset=-40.0)), "int32")

    def test_scaled_is_float_until_precision_runs_out(self):
        self.assertEqual(wire.proto_type(signal(16, scale=0.1)), "float")
        self.assertEqual(wire.proto_type(signal(24, scale=0.1)), "float")
        # float32 has 24 mantissa bits; wider raw fields need double.
        self.assertEqual(wire.proto_type(signal(25, scale=0.1)), "double")
        # GPS: int32 at 1e-7 is the case that silently loses digits as float.
        self.assertEqual(
            wire.proto_type(signal(32, sign=can_spec_pb2.SIGNED, scale=1e-7)), "double")

    def test_scale_change_changes_the_wire_type(self):
        # The hazard wire.lock exists to surface: same id, different type.
        self.assertEqual(wire.proto_type(signal(16)), "uint32")
        self.assertEqual(wire.proto_type(signal(16, scale=0.1)), "float")

    def test_bitfield_signals_have_no_type_of_their_own(self):
        sig = can_spec_pb2.Signal(name="s")
        sig.encoding.bit_length = 8
        sig.logical.bitfield.bitfield_type = "Flags"
        with self.assertRaises(ValueError):
            wire.proto_type(sig)


REGISTRY = """
bus { name: "Critical" bitrate_bps: 1000000 }
groups { group { name: "Battery" next_free_id: 3 } }
"""

ARRAY_MESSAGE = """
message {
  name: "CELLS"
  can_id: 0x200
  quantity: 4
  from_board: "HVC"
  bus: "Critical"
  dlc: 4
  signal {
    name: "slot0"
    encoding { bit_length: 16 sign: SIGNED scale: 0.1 }
    logical { physical { unit: "degC" } }
    telemetry { group: "Battery" field: "cell_temps" id: 1 repeated: true }
  }
  signal {
    name: "slot1"
    encoding { start_bit: 16 bit_length: 16 sign: SIGNED scale: 0.1 }
    logical { physical { unit: "degC" } }
    telemetry { group: "Battery" field: "cell_temps" id: 1 repeated: true array_index: 1 }
  }
  signal {
    name: "count"
    encoding { start_bit: 32 bit_length: 8 }
    logical { physical { unit: "" } }
    telemetry { group: "Battery" field: "cell_count" id: 2 }
  }
}
"""


class ManifestTest(unittest.TestCase):
    def spec(self):
        return loader.Spec({
            "registry.textproto": loader.parse_file(REGISTRY),
            "lib/spec/messages/hvc.textproto": loader.parse_file(ARRAY_MESSAGE),
        })

    def test_repeated_bound_spans_the_id_block(self):
        # 2 slots per frame x quantity 4 = 8 elements, one field, one id.
        types = wire.field_types(self.spec())
        self.assertEqual(types[("Battery", "cell_temps")], ("repeated IndexedFloat", 8))
        self.assertEqual(types[("Battery", "cell_count")], ("uint32", None))

    def test_manifest_lists_fields_and_tombstones(self):
        text = wire.manifest(self.spec())
        self.assertIn("Battery.cell_temps 1 repeated IndexedFloat[8]", text)
        self.assertIn("Battery.cell_count 2 uint32", text)
        self.assertIn("[Battery] next_free_id=3", text)

    def test_manifest_is_stable(self):
        self.assertEqual(wire.manifest(self.spec()), wire.manifest(self.spec()))


if __name__ == "__main__":
    unittest.main()
