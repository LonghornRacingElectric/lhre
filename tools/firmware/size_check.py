"""Fails when a firmware image uses more flash or RAM than its budget.

Usage: size_check.py <image.elf> <linker_script.ld> <budget_percent>

Regions come from the linker script's MEMORY block. Initialized data counts
against both RAM and flash, because its initial values are stored in flash.
"""

import re
import struct
import sys

SHF_ALLOC = 0x2
SHT_NOBITS = 8
_UNITS = {"": 1, "K": 1024, "M": 1024 * 1024}


def memory_regions(ld_text):
    block = re.search(r"MEMORY\s*\{(.*?)\}", ld_text, re.S).group(1)
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    regions = {}
    for name, origin, length, unit in re.findall(
        r"(\w+)\s*\([^)]*\)\s*:\s*ORIGIN\s*=\s*(\w+)\s*,\s*LENGTH\s*=\s*(\d+)\s*([KM]?)",
        block,
    ):
        regions[name] = (int(origin, 0), int(length) * _UNITS[unit])
    return regions


def alloc_sections(elf):
    if elf[:4] != b"\x7fELF" or elf[4] != 1 or elf[5] != 1:
        raise ValueError("expected a 32-bit little-endian ELF")
    shoff, = struct.unpack_from("<I", elf, 0x20)
    shentsize, shnum = struct.unpack_from("<HH", elf, 0x2E)
    for i in range(shnum):
        _, sh_type, flags, addr, _, size = struct.unpack_from(
            "<IIIIII", elf, shoff + i * shentsize)
        if flags & SHF_ALLOC and size:
            yield sh_type, addr, size


def main(elf_path, ld_path, budget_pct):
    with open(elf_path, "rb") as f:
        elf = f.read()
    with open(ld_path, encoding="utf-8") as f:
        regions = memory_regions(f.read())
    flash_origin, flash_len = regions["FLASH"]
    ram_origin, ram_len = regions["RAM"]
    flash = ram = 0
    for sh_type, addr, size in alloc_sections(elf):
        if ram_origin <= addr < ram_origin + ram_len:
            ram += size
            if sh_type != SHT_NOBITS:
                flash += size  # initial values of .data
        elif flash_origin <= addr < flash_origin + flash_len:
            flash += size
    failed = False
    for name, used, total in (("flash", flash, flash_len), ("RAM", ram, ram_len)):
        pct = 100.0 * used / total
        print(f"{name}: {used} / {total} bytes ({pct:.1f}%, budget {budget_pct}%)")
        failed |= pct > budget_pct
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], float(sys.argv[3])))
