"""Compare reviewed wire locks without reading Git from build actions."""

import argparse
from pathlib import Path


def parse_lock(contents):
    fields, reserved_ids, reserved_names, high_water = {}, set(), set(), {}
    for line in contents.splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            if "next_free_id=" in line:
                high_water[line.split("]")[0][1:]] = int(line.split("=")[1])
            continue
        parts = line.split()
        if len(parts) < 3:
            raise ValueError(f"invalid wire lock entry: {line}")
        if parts[1] == "RESERVED_ID":
            reserved_ids.add((parts[0], int(parts[2])))
        elif parts[1] == "RESERVED_NAME":
            reserved_names.add((parts[0], parts[2]))
        else:
            group, name = parts[0].rsplit(".", 1)
            fields[(group, name)] = (int(parts[1]), " ".join(parts[2:]))
    return fields, reserved_ids, reserved_names, high_water


def compatibility_errors(previous, current):
    old, old_ids, old_names, old_water = parse_lock(previous)
    new, new_ids, new_names, new_water = parse_lock(current)
    retired_groups = {name for (group, name), (number, _) in old.items()
                      if group == "Root" and (group, name) not in new
                      and (group, number) in new_ids and (group, name) in new_names}
    errors = []
    for (group, name), value in old.items():
        if group in retired_groups:
            continue
        if (group, name) in new:
            new_number, new_type = new[(group, name)]
            old_number, old_type = value
            newly_documented_bound = old_type.startswith("repeated ") and "[" not in old_type and new_type.startswith(old_type + "[")
            if new_number != old_number or (new_type != old_type and not newly_documented_bound):
                errors.append(f"{group}.{name}: deployed number/type/layout changed; allocate a new field")
        elif (group, value[0]) not in new_ids or (group, name) not in new_names:
            errors.append(f"{group}.{name}: deletion must reserve both number and name")
    if any(item not in new_ids for item in old_ids if item[0] not in retired_groups) or any(item not in new_names for item in old_names if item[0] not in retired_groups):
        errors.append("deployed tombstones were removed")
    for (group, name), (number, _) in new.items():
        previous_name = next((key for key, value in old.items() if key[0] == group and value[0] == number), None)
        if previous_name is not None and previous_name != (group, name):
            errors.append(f"{group}.{name}: deployed number {number} was reused")
    for group, high_water in old_water.items():
        if group in new_water and new_water[group] < high_water:
            errors.append(f"{group}: next_free_id decreased")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    args = parser.parse_args()
    errors = compatibility_errors(args.baseline.read_text(), args.current.read_text())
    for error in errors:
        print(error)
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
