"""Load CAN spec textproto files into the proto-backed IR.

The generated classes from spec/proto/can_spec.proto ARE the IR — this
module only parses files and provides a merged view with per-file
provenance for error messages. Keep it small and free of semantics
(those live in validator.py) so a future swap of the spec syntax touches
only this file.
"""

from google.protobuf import text_format

from lib.spec.proto import can_spec_pb2


class Spec:
    """All spec files, parsed. `files` maps filename -> SpecFile."""

    def __init__(self, files):
        self.files = dict(files)

    def messages(self):
        """Yields (filename, CanMessage) across all files."""
        for name, spec_file in self.files.items():
            for msg in spec_file.message:
                yield name, msg

    def buses(self):
        for name, spec_file in self.files.items():
            for bus in spec_file.bus:
                yield name, bus

    def enum_types(self):
        for name, spec_file in self.files.items():
            for enum_type in spec_file.enum_type:
                yield name, enum_type

    def bitfield_types(self):
        for name, spec_file in self.files.items():
            for bitfield_type in spec_file.bitfield_type:
                yield name, bitfield_type

    def groups(self):
        """Yields (filename, Group) from every GroupRegistry."""
        for name, spec_file in self.files.items():
            for group in spec_file.groups.group:
                yield name, group


def parse_file(text, filename="<string>"):
    """Parses one spec file's contents. Raises ParseError with the
    offending line on malformed input."""
    spec_file = can_spec_pb2.SpecFile()
    try:
        text_format.Parse(text, spec_file)
    except text_format.ParseError as err:
        raise text_format.ParseError(f"{filename}: {err}") from err
    return spec_file


def load(paths):
    """Loads the given .textproto paths into a Spec. Keys are the paths
    as given (use workspace-relative paths so errors are clickable)."""
    files = {}
    for path in paths:
        with open(path, encoding="utf-8") as f:
            files[str(path)] = parse_file(f.read(), filename=str(path))
    return Spec(files)
