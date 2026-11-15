"""Stand-in for the flash scripts, for flash_launcher_test.

tools/openocd/flash.py and tools/dfu/flash.py need a board on the bench. This
takes the arguments the flash rules pass them (rlocation paths, then whatever
the user appended), resolves the paths through the runfiles library the same
way they do, and reports what it found instead of flashing.
"""

import json
import os
import sys

from runfiles import Runfiles

# The test passes this ahead of its own arguments, so the probe can tell the
# rule's embedded paths from forwarded ones without knowing which rule ran it.
SEPARATOR = "--"


def main():
    args = sys.argv[1:]
    split = args.index(SEPARATOR) if SEPARATOR in args else len(args)
    embedded, forwarded = args[:split], args[split + 1 :]

    r = Runfiles.Create()
    resolved = []
    for arg in embedded:
        path = r.Rlocation(arg)
        resolved.append(bool(path) and os.path.exists(path))

    print(
        json.dumps({"embedded": embedded, "resolved": resolved, "forwarded": forwarded})
    )

    for arg in forwarded:
        if arg.startswith("--exit="):
            sys.exit(int(arg.split("=", 1)[1]))


if __name__ == "__main__":
    main()
