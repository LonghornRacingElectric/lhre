"""Entry point for //spec:validate — runs every §5 invariant on the real
spec files. Arguments are runfiles paths of the .textproto files."""

import sys

from runfiles import Runfiles

from lib.spec.ir import loader, validator


def main(argv):
    r = Runfiles.Create()
    paths = [r.Rlocation(arg) for arg in argv]
    spec = loader.load(paths)
    errors, warnings = validator.validate(spec)
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"\nspec validation failed: {len(errors)} error(s)")
        return 1
    print(f"spec OK: {len(list(spec.messages()))} messages validated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
