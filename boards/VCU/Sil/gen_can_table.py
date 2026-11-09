"""Writes the SIL's CAN decode table: every message in lib/spec as JSON.

The Python host decodes captured frames with it (host/vcusil/candecode.py),
so a SIL run log and the firmware always agree on the spec. Big-endian and
multiplexed signals are not decoded: none exist yet, and the host's decoder
would need the generated C++'s handling of both first. Such a message is
listed under "unsupported" and its frames stay raw in the run log, so a spec
change never breaks the build.
"""

import argparse
import hashlib
import json
import sys

from lib.spec.ir import loader, validator
from lib.spec.proto import can_spec_pb2


def _die(message):
    sys.stderr.write(f"gen_can_table: {message}\n")
    sys.exit(1)


def unsupported(signal):
    """Why the host's decoder cannot read this signal, or None."""
    if signal.encoding.byte_order == can_spec_pb2.BIG_ENDIAN:
        return f"{signal.name} is big-endian"
    if signal.encoding.muxed or signal.logical.mux_selector:
        return f"{signal.name} is multiplexed"
    return None


def signal_entry(signal, bitfields):
    enc = signal.encoding
    kind = signal.logical.WhichOneof("kind")
    entry = {
        "name": signal.name,
        "start_bit": enc.start_bit,
        "bit_length": enc.bit_length,
        "signed": enc.sign == can_spec_pb2.SIGNED,
        "scale": validator.effective_scale(enc),
        "offset": enc.offset,
        "kind": kind,
        "unit": signal.logical.physical.unit if kind == "physical" else "",
    }
    if kind == "enum_type":
        entry["enum"] = signal.logical.enum_type
    if kind == "bitfield":
        entry["bits"] = [b.name for b in bitfields[signal.logical.bitfield.bitfield_type].bit]
    return entry


def build(contents):
    """The table for spec files given as path -> text."""
    spec = loader.Spec({p: loader.parse_file(t, p) for p, t in contents.items()})
    errors, _ = validator.validate(spec)
    if errors:
        _die("spec is invalid:\n" + "\n".join(errors))

    bitfields = {b.name: b for _, b in spec.bitfield_types()}
    messages, skipped = [], {}
    for _, m in sorted(spec.messages(), key=lambda x: (x[1].bus, x[1].can_id)):
        reasons = [r for r in map(unsupported, m.signal) if r]
        if reasons:
            skipped[m.name] = reasons
            continue
        messages.append({
            "name": m.name,
            "can_id": m.can_id,
            "quantity": validator.effective_quantity(m),
            "bus": m.bus,
            "from_board": m.from_board,
            "dlc": m.dlc,
            "frequency_hz": m.frequency_hz,
            "signals": [signal_entry(s, bitfields) for s in m.signal],
        })
    digest = hashlib.sha256()
    for path in sorted(contents):
        digest.update(contents[path].encode())
    return {
        "spec_sha256": digest.hexdigest(),
        "messages": messages,
        "unsupported": skipped,
        "enums": {e.name: {str(v.number): v.name for v in e.value}
                  for _, e in spec.enum_types()},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("spec_files", nargs="+")
    args = parser.parse_args()

    contents = {}
    for path in sorted(args.spec_files):
        with open(path, encoding="utf-8") as f:
            contents[path] = f.read()
    table = build(contents)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=1, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
