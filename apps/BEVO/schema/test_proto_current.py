"""can_packets.proto must be exactly what update_can_proto generates from the CSVs.

The proto is checked in, so a CSV edit without a regenerate goes unnoticed: can.json
(built from the CSVs) and the proto disagree, and BEVO's build.rs silently skips
every signal the proto lacks.
"""
import difflib
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProtoCurrentTest(unittest.TestCase):
    def test_proto_matches_csvs(self):
        gen = _load("generate_can_proto")
        gen_json = gen._load_generate_can_json_module()
        csv_path = os.path.join(HERE, "can_packets.csv")
        bitfield_path = os.path.join(HERE, "can_bitfields.csv")
        proto_path = os.path.join(HERE, "can_packets.proto")

        packets = gen_json.process_csv(
            csv_path, gen_json.load_bitfield_definitions(bitfield_path), bitfield_path
        )
        proto_text, _ = gen.generate_proto_text(
            gen.parse_can_model_to_partitions(packets),
            "Orion",
            gen.parse_existing_proto_ids(proto_path),
        )
        with open(proto_path, encoding="utf-8") as f:
            committed = f.read()
        diff = "".join(
            difflib.unified_diff(
                committed.splitlines(True),
                proto_text.splitlines(True),
                "can_packets.proto (committed)",
                "generated from the CSVs",
                n=1,
            )
        )
        if diff:
            self.fail(
                "can_packets.proto is stale. Run "
                "`bazel run --config=local //apps/BEVO/schema:update_can_proto` "
                "and commit the proto and CSVs together.\n" + diff
            )


if __name__ == "__main__":
    unittest.main()
