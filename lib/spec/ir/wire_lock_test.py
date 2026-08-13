"""Golden test for the committed wire contract.

lib/spec/wire.lock records every telemetry field's number and derived
proto type. Regenerating must reproduce it byte-for-byte — so any change
to the off-vehicle contract (new field, changed scale flipping a type,
changed array bound) shows up as a reviewable diff instead of silently
breaking deployed consumers and archived data.
"""

import sys

from runfiles import Runfiles

from lib.spec.ir import loader, wire


def main(argv):
    r = Runfiles.Create()
    lock_arg = [a for a in argv if a.endswith("wire.lock")]
    spec_args = [a for a in argv if a.endswith(".textproto")]
    if not lock_arg:
        print("ERROR: wire.lock not passed to the test")
        return 1
    with open(r.Rlocation(lock_arg[0]), encoding="utf-8") as f:
        committed = f.read()
    spec = loader.load([r.Rlocation(a) for a in spec_args])
    expected = wire.manifest(spec)
    if committed == expected:
        return 0
    print("lib/spec/wire.lock is out of date.\n")
    committed_lines = committed.splitlines()
    expected_lines = expected.splitlines()
    for line in expected_lines:
        if line not in committed_lines and line.strip():
            print(f"  + {line}")
    for line in committed_lines:
        if line not in expected_lines and line.strip():
            print(f"  - {line}")
    print("\nrun: bazel run //tools/spec:lock")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
