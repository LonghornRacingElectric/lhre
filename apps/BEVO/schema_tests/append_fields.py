"""Make an append-only fixture without editing the working tree's CAN spec."""

from pathlib import Path
import sys

from lib.spec.ir import canonical, loader


def main():
    baseline, addition, output = map(Path, sys.argv[1:])
    spec = loader.parse_file(baseline.read_text())
    spec.MergeFrom(loader.parse_file(addition.read_text()))
    spec.groups.group[0].next_free_id = 4
    output.write_text(canonical.canonicalize(spec))


if __name__ == "__main__":
    main()
