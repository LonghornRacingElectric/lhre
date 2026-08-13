"""Entry point for //spec:format_check — every spec file must match its
canonical serialization byte-for-byte. Fix with `bazel run //tools/spec:fmt`."""

import sys

from runfiles import Runfiles

from lib.spec.ir import canonical, loader


def main(argv):
    r = Runfiles.Create()
    bad = []
    for arg in argv:
        path = r.Rlocation(arg)
        with open(path, encoding="utf-8") as f:
            actual = f.read()
        expected = canonical.canonicalize(loader.parse_file(actual, filename=arg))
        if actual != expected:
            bad.append(arg)
    for name in bad:
        print(f"not canonically formatted: {name}")
    if bad:
        print("\nrun: bazel run //tools/spec:fmt")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
