"""gen_can_table on the real spec, and on specs it cannot fully decode.

A message the host cannot decode must never fail the build: CI builds and
tests //... on Linux, so a refusal here would block every spec change. For
the same reason nothing below assumes what the spec holds today. The cases
that need a particular message add their own probe message to a copy."""

import json
import os
import sys
import tempfile
import unittest

from google.protobuf import text_format
from runfiles import Runfiles

from lib.spec.ir import loader
from lib.spec.proto import can_spec_pb2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_can_table

SPEC = {}
PROBE = "SIL_TABLE_PROBE"


def _parsed():
    return {path: loader.parse_file(text, path) for path, text in SPEC.items()}


def _spec_messages():
    return [m for parsed in _parsed().values() for m in parsed.message]


def _with_probe(change=None):
    """The real spec plus PROBE: two 8-bit unsigned signals, `selector` and
    `value`, on a free ID, sent like the first message in the spec.
    change(probe) edits it first."""
    parsed = _parsed()
    path, template = next((p, f.message[0]) for p, f in sorted(parsed.items()) if f.message)
    used = {m.can_id + i for f in parsed.values() for m in f.message
            for i in range(max(m.quantity, 1))}
    probe = parsed[path].message.add(
        name=PROBE, can_id=next(i for i in range(0x7FF, 0, -1) if i not in used),
        from_board=template.from_board, bus=template.bus, dlc=8,
        frequency_hz=template.frequency_hz, description="gen_can_table_test probe")
    probe.to_boards.extend(template.to_boards)
    for name, start in (("selector", 0), ("value", 8)):
        s = probe.signal.add(name=name, description=name)
        s.encoding.start_bit, s.encoding.bit_length = start, 8
        s.logical.physical.unit = ""
    if change:
        change(probe)
    contents = dict(SPEC)
    contents[path] = text_format.MessageToString(parsed[path])
    return contents


def _names(table):
    return [m["name"] for m in table["messages"]]


class GenCanTableTest(unittest.TestCase):
    def setUp(self):
        self.base = gen_can_table.build(SPEC)

    def test_every_spec_message_is_decoded_or_listed(self):
        names = _names(self.base)
        self.assertEqual(len(names), len(set(names)))
        self.assertFalse(set(names) & set(self.base["unsupported"]))
        self.assertEqual(set(names) | set(self.base["unsupported"]),
                         {m.name for m in _spec_messages()})
        self.assertEqual(len(self.base["spec_sha256"]), 64)

    def test_bitfield_bits_are_in_wire_order(self):
        """Bit i of a bitfield signal is entry i of its type, as on the wire."""
        types = {b.name: [bit.name for bit in b.bit]
                 for parsed in _parsed().values() for b in parsed.bitfield_type}
        spec = {(m.name, s.name): s for m in _spec_messages() for s in m.signal}
        checked = 0
        for m in self.base["messages"]:
            for s in m["signals"]:
                if s["kind"] == "bitfield":
                    ref = spec[(m["name"], s["name"])].logical.bitfield.bitfield_type
                    self.assertEqual(s["bits"], types[ref], (m["name"], s["name"]))
                    checked += 1
        self.assertGreater(checked, 0)

    def test_the_digest_does_not_depend_on_argument_order(self):
        reordered = dict(reversed(list(SPEC.items())))
        self.assertEqual(gen_can_table.build(reordered)["spec_sha256"],
                         self.base["spec_sha256"])

    def test_a_plain_probe_decodes(self):
        table = gen_can_table.build(_with_probe())
        self.assertEqual(table["unsupported"], self.base["unsupported"])
        probe = next(m for m in table["messages"] if m["name"] == PROBE)
        self.assertEqual([s["name"] for s in probe["signals"]], ["selector", "value"])

    def test_a_big_endian_message_is_listed_and_left_raw(self):
        def flip(m):
            m.signal[1].encoding.byte_order = can_spec_pb2.BIG_ENDIAN
            m.signal[1].encoding.start_bit = 15  # byte 1, MSB first
        table = gen_can_table.build(_with_probe(flip))
        self.assertEqual(table["unsupported"],
                         {**self.base["unsupported"], PROBE: ["value is big-endian"]})
        self.assertEqual(_names(table), _names(self.base))

    def test_a_multiplexed_message_is_listed_and_left_raw(self):
        def mux(m):
            m.signal[0].logical.mux_selector = True
            m.signal[1].encoding.muxed = True
            m.signal[1].encoding.mux_value = 1
        table = gen_can_table.build(_with_probe(mux))
        self.assertEqual(table["unsupported"], {
            **self.base["unsupported"],
            PROBE: ["selector is multiplexed", "value is multiplexed"]})
        self.assertEqual(_names(table), _names(self.base))

    def test_an_invalid_spec_still_fails(self):
        def overlap(m):
            m.signal[1].encoding.start_bit = 0  # on top of `selector`
        with self.assertRaises(SystemExit):
            gen_can_table.build(_with_probe(overlap))


    def test_every_field_is_the_spec_s(self):
        """The decoder reads these as-is, so each must be the spec's own."""
        spec = {m.name: m for m in _spec_messages()}
        for m in self.base["messages"]:
            s = spec[m["name"]]
            self.assertEqual(
                (m["can_id"], m["quantity"], m["bus"], m["from_board"], m["dlc"], m["frequency_hz"]),
                (s.can_id, max(s.quantity, 1), s.bus, s.from_board, s.dlc, s.frequency_hz), m["name"])
            for got, sig in zip(m["signals"], s.signal, strict=True):
                e, kind = sig.encoding, sig.logical.WhichOneof("kind")
                self.assertEqual(
                    (got["name"], got["start_bit"], got["bit_length"], got["signed"], got["scale"],
                     got["offset"], got["kind"], got["unit"], got.get("enum")),
                    (sig.name, e.start_bit, e.bit_length, e.sign == can_spec_pb2.SIGNED,
                     e.scale or 1.0, e.offset, kind,
                     sig.logical.physical.unit if kind == "physical" else "",
                     sig.logical.enum_type if kind == "enum_type" else None), (m["name"], sig.name))
        enums = {e.name: {str(v.number): v.name for v in e.value}
                 for parsed in _parsed().values() for e in parsed.enum_type}
        self.assertEqual(self.base["enums"], enums)

    def test_a_signed_offset_probe_keeps_its_encoding(self):
        def shape(m):
            m.signal[0].encoding.sign = can_spec_pb2.SIGNED
            m.signal[1].encoding.scale, m.signal[1].encoding.offset = 0.5, -40.0
            m.signal[1].logical.physical.unit = "degC"
        probe = next(m for m in gen_can_table.build(_with_probe(shape))["messages"]
                     if m["name"] == PROBE)
        self.assertEqual([(s["signed"], s["scale"], s["offset"], s["unit"]) for s in probe["signals"]],
                         [(True, 1.0, 0.0, ""), (False, 0.5, -40.0, "degC")])

    def test_main_writes_what_build_returns(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for name, text in SPEC.items():
                path = os.path.join(d, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                paths.append(path)
            out = os.path.join(d, "table.json")
            argv, sys.argv = sys.argv, ["gen_can_table", "--out", out, *paths]
            try:
                gen_can_table.main()
            finally:
                sys.argv = argv
            with open(out, encoding="utf-8") as f:
                self.assertEqual(json.load(f), json.loads(json.dumps(self.base)))


if __name__ == "__main__":
    r = Runfiles.Create()
    for arg in sys.argv[1:]:
        with open(r.Rlocation(arg), encoding="utf-8") as f:
            SPEC[arg] = f.read()
    unittest.main(argv=sys.argv[:1])
