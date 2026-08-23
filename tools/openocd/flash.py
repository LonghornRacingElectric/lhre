import argparse
import subprocess
import sys
import os

from runfiles import Runfiles


def main():
    parser = argparse.ArgumentParser(
        description="Flash a firmware file to a target using OpenOCD."
    )
    # receive the canonical paths from the BUILD file arguments
    parser.add_argument("openocd_canonical_path")
    parser.add_argument("firmware_canonical_path")
    parser.add_argument("config_canonical_path")
    args = parser.parse_args()

    r = Runfiles.Create()

    openocd_exe_actual_path = os.path.realpath(r.Rlocation(args.openocd_canonical_path))
    firmware_elf_actual_path = os.path.realpath(r.Rlocation(args.firmware_canonical_path))
    openocd_cfg_actual_path = os.path.realpath(r.Rlocation(args.config_canonical_path))

    # OpenOCD's TCL script library, laid out as <repo>/openocd/scripts next
    # to <repo>/bin/openocd.
    openocd_scripts_dir = os.path.join(
        os.path.dirname(os.path.dirname(openocd_exe_actual_path)),
        "openocd",
        "scripts",
    )

    # OpenOCD's embedded Jim TCL interpreter treats backslashes as escape
    # sequences (e.g. \b as backspace, \v as vertical tab, \x.. as hex), so
    # all paths passed to OpenOCD options and commands must use forward slashes.
    scripts_dir_arg = openocd_scripts_dir.replace("\\", "/")
    cfg_arg = openocd_cfg_actual_path.replace("\\", "/")
    firmware_elf_arg = firmware_elf_actual_path.replace("\\", "/")

    print("--- Flashing Firmware (Paths resolved via Runfiles Library) ---")
    print(f"Working Directory:      {os.getcwd()}")
    print(f"Resolved OpenOCD Path:  {openocd_exe_actual_path}")
    print(f"Resolved Scripts Dir:   {openocd_scripts_dir}")
    print(f"Resolved Firmware Path: {firmware_elf_actual_path}")
    print(f"Resolved Config Path:   {openocd_cfg_actual_path}")
    print("-----------------------------------------------------------------")

    command = [
        openocd_exe_actual_path,
        "-s",
        scripts_dir_arg,
        "-f",
        cfg_arg,
        "-c",
        f'program "{firmware_elf_arg}" verify reset exit',
    ]

    try:
        subprocess.run(command, check=True)
        print("--- Flash Complete ---")
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
