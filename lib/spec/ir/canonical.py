"""Canonical serialization of spec files.

One definition of "correctly formatted" shared by the formatter
(//tools/spec:fmt), the format check (//spec:format_check), and any
future tooling that writes spec files (CSV migrator, editor UI) — hand
edits and tool edits must converge to identical bytes.

Canonical form: the fixed header below, then the file printed by
protobuf's text_format with messages sorted by CAN ID and registries
sorted by name. Field order inside an entry is proto field-number order
(text_format's default). Textproto comments do not survive a parse, so
spec files must not carry hand-written comments — prose belongs in the
schema's `description` fields.
"""

from google.protobuf import text_format

from lib.spec.proto import can_spec_pb2

# Recognized by protobuf-aware editors and the textproto LSP.
HEADER = (
    "# proto-file: lib/spec/proto/can_spec.proto\n"
    "# proto-message: lhre.canspec.SpecFile\n"
    "\n"
)


def _sorted_copy(spec_file):
    out = can_spec_pb2.SpecFile()
    out.CopyFrom(spec_file)
    for field, key in (
        ("message", lambda m: (m.can_id, m.name)),
        ("bus", lambda b: b.name),
        ("enum_type", lambda e: e.name),
        ("bitfield_type", lambda b: b.name),
    ):
        entries = sorted(getattr(out, field), key=key)
        del getattr(out, field)[:]
        getattr(out, field).extend(entries)
    if out.HasField("groups"):  # touching the submessage would mark it set
        groups = sorted(out.groups.group, key=lambda g: g.name)
        del out.groups.group[:]
        out.groups.group.extend(groups)
        for group in out.groups.group:
            group.reserved_ids[:] = sorted(group.reserved_ids)
            group.reserved_names[:] = sorted(group.reserved_names)
    return out


def canonicalize(spec_file):
    """Returns the canonical file contents for a parsed SpecFile."""
    return HEADER + text_format.MessageToString(_sorted_copy(spec_file))
