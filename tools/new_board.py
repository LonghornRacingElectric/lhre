"""Scaffold a board's BUILD.bazel from its CubeMX .ioc.

Usage:  bazel run //tools:new_board -- boards/<Name>/<Name>.ioc

Boards should never be created by copying another board's BUILD file — that
copies whatever is incidental to that board along with what's essential.
Everything a BUILD file needs to state is derivable from the .ioc: the MCU
(everything part-coupled — family, device define, linker/startup scripts —
is derived from it by firmware_project) and which middleware CubeMX has
enabled. This script reads exactly that and emits the minimal call.
"""

import argparse
import os
import re
import sys


def ioc_values(path):
    values = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            key, sep, value = line.strip().partition("=")
            if sep:
                values[key] = value
    return values


def mcu_from_device_id(device_id):
    # ProjectManager.DeviceId is the orderable part (STM32G474VETx: pin
    # count + flash size + package); firmware_project wants the die-level
    # header spelling (stm32g474xx). Chars 0-8 name the die; the tail is
    # replaced by the "xx" wildcard, keeping an explicit flash-size letter
    # for families that encode it in the define (e.g. STM32F051x8).
    m = re.match(r"STM32([A-Z]\d{3})", device_id)
    if not m:
        sys.exit(f"error: can't parse an STM32 device from DeviceId '{device_id}'")
    return f"stm32{m.group(1).lower()}xx"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ioc", help="path to the board's .ioc file (in boards/<Name>/)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing BUILD.bazel")
    args = parser.parse_args()

    # `bazel run` starts in the runfiles tree; resolve the user's path from
    # where they invoked us.
    workdir = os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd())
    ioc_path = os.path.join(workdir, args.ioc) if not os.path.isabs(args.ioc) else args.ioc
    if not os.path.isfile(ioc_path):
        sys.exit(f"error: {args.ioc} not found")

    board_dir = os.path.dirname(ioc_path)
    board = os.path.basename(board_dir)
    build_path = os.path.join(board_dir, "BUILD.bazel")
    if os.path.exists(build_path) and not args.force:
        sys.exit(f"error: {build_path} already exists (pass --force to overwrite)")

    values = ioc_values(ioc_path)
    mcu = mcu_from_device_id(values.get("ProjectManager.DeviceId", ""))
    ips = {v for k, v in values.items() if re.fullmatch(r"Mcu\.IP\d+", k)}
    freertos = "FREERTOS" in ips
    usb = "USB_DEVICE" in ips

    name = board.lower()
    opts = [f'    name = "{name}",\n']
    if freertos:
        opts.append("    enable_freertos = True,\n")
    if usb:
        opts.append("    enable_usb = True,\n")
    opts.append(f'    mcu = "{mcu}",\n')

    with open(build_path, "w", encoding="utf-8") as f:
        f.write(
            'load("//tools/firmware:firmware_project.bzl", "firmware_project")\n'
            "\n"
            "# The whole board: firmware image, flash targets, and — synthesized\n"
            "# from the App/ file names — the app library, host tests, and sims.\n"
            "# Everything else is derived from mcu or the CubeMX layout; see\n"
            "# tools/firmware/README.md. State only what this board adds.\n"
            "firmware_project(\n" + "".join(opts) + ")\n"
        )

    print(f"wrote {os.path.relpath(build_path, workdir)}  (mcu={mcu}, freertos={freertos}, usb={usb})")
    print("next steps:")
    print(f"  - write App/{name}_app.cpp/.hpp, App/{name}_app_test.cpp, and Board/main.cpp")
    print(f"    (boards/VCU is the reference; App/ file names become targets)")
    print(f"  - add //boards/{board}:release to //boards:all_firmware")
    print(f"  - write boards/{board}/README.md (see AGENTS.md — docs are part of the change)")


if __name__ == "__main__":
    main()
