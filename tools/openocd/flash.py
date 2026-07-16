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

    openocd_exe_actual_path = r.Rlocation(args.openocd_canonical_path)
    firmware_elf_actual_path = r.Rlocation(args.firmware_canonical_path)
    openocd_cfg_actual_path = r.Rlocation(args.config_canonical_path)

    print("--- Flashing Firmware (Paths resolved via Runfiles Library) ---")
    print(f"Working Directory:      {os.getcwd()}")
    print(f"Resolved OpenOCD Path:  {openocd_exe_actual_path}")
    print(f"Resolved Firmware Path: {firmware_elf_actual_path}")
    print(f"Resolved Config Path:   {openocd_cfg_actual_path}")
    print("-----------------------------------------------------------------")

    # Normalize the firmware path for the OpenOCD command string
    firmware_elf_arg = firmware_elf_actual_path.replace("\\", "/")

    command = [
        openocd_exe_actual_path,
        "-f",
        openocd_cfg_actual_path,
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
