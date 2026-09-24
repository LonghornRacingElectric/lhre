#!/usr/bin/env python3
import importlib.util
import os
import re
import sys

_FIELD_RE = re.compile(r"^\s*((?:repeated\s+)?\w+)\s+(\w+)\s*=\s*\d+\s*;")


def _field_types(proto_text: str) -> dict:
    """{(message, field): type} for a generated proto (flat messages only)."""
    out, msg = {}, None
    for line in proto_text.splitlines():
        m = re.match(r"\s*message\s+(\w+)", line)
        if m:
            msg = m.group(1)
            continue
        f = _FIELD_RE.match(line)
        if f and msg:
            out[(msg, f.group(2))] = f.group(1)
    return out


def _load_generate_can_proto_module():
    here = os.path.dirname(__file__)
    path = os.path.join(here, "generate_can_proto.py")
    spec = importlib.util.spec_from_file_location("generate_can_proto", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    workspace_dir = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if not workspace_dir:
        print(
            "Error: BUILD_WORKSPACE_DIRECTORY is not set. Run via `bazel run //apps/BEVO/schema:update_can_proto`.",
            file=sys.stderr,
        )
        return 2

    schema_dir = os.path.join(workspace_dir, "apps/BEVO/schema")
    csv_path = os.path.join(schema_dir, "can_packets.csv")
    bitfield_csv_path = os.path.join(schema_dir, "can_bitfields.csv")
    out_dir = schema_dir
    out_path = os.path.join(out_dir, "can_packets.proto")

    os.makedirs(out_dir, exist_ok=True)

    gen = _load_generate_can_proto_module()
    gen_json = gen._load_generate_can_json_module()
    bitfield_defs = gen_json.load_bitfield_definitions(bitfield_csv_path)
    packets = gen_json.process_csv(csv_path, bitfield_defs, bitfield_csv_path)

    # Seed from the previously generated proto so existing field numbers are preserved.
    existing_ids = gen.parse_existing_proto_ids(out_path)

    partitions = gen.parse_can_model_to_partitions(packets)
    try:
        proto_text, id_map = gen.generate_proto_text(partitions, "Orion", existing_ids)
    except ValueError as e:  # e.g. a hand-typed `#N` that collides; a traceback buries it
        print(f"Error: {e}. Nothing was written.", file=sys.stderr)
        return 1

    # A field's number is permanent, so its type is too: data already recorded under
    # that number would decode as garbage. Reusing an existing name with a new type
    # (e.g. `stomp_fault (float)`) is the usual way to hit this.
    old_types = {}
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            old_types = _field_types(f.read())
    changed = [
        f"{m}.{name}: {old_types[(m, name)]} -> {t}"
        for (m, name), t in _field_types(proto_text).items()
        if old_types.get((m, name), t) != t
    ]
    if changed:
        print(
            "Error: this would change the type of existing proto field(s):\n  "
            + "\n  ".join(changed)
            + "\nUse a new signal name instead. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(proto_text)

    annotated = gen.write_back_proto_ids(csv_path, id_map)
    annotated += gen.write_back_bitfield_ids(bitfield_csv_path, id_map)

    print(f"Wrote {out_path}" + (f"; annotated {annotated} CSV cell(s) with proto ids." if annotated else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
