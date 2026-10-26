"""Fails when a firmware image uses more flash or RAM than its budget.

Usage: size_check.py <image.elf> <linker_script.ld> <budget_percent>

Regions come from the linker script's MEMORY block. Initialized data counts
against both RAM and flash, because its initial values are stored in flash.
A LENGTH this script can't read exactly (an expression like `512K - 16K`)
is an error, not a guess: guessing high would under-report usage. So is a
section outside both FLASH and RAM, which would otherwise go uncounted.
"""

import re
import struct
import sys

SHF_ALLOC = 0x2
SHT_NOBITS = 8
_UNITS = {"": 1, "K": 1024, "M": 1024 * 1024}
# Attributes are optional in GNU ld. LENGTH runs to the end of the line,
# since linker scripts put one region per line.
_REGION = re.compile(r"(\w+)\s*(?:\([^)]*\))?\s*:\s*ORIGIN\s*=\s*(\w+)\s*,"
                     r"\s*LENGTH\s*=\s*([^\n]+)")
_LENGTH = re.compile(r"(0[xX][0-9A-Fa-f]+|\d+)\s*([KM]?)")


def memory_regions(ld_text):
    # Strip comments first so a brace inside one cannot end the block.
    text = re.sub(r"/\*.*?\*/", "", ld_text, flags=re.S)
    block = re.search(r"MEMORY\s*\{(.*?)\}", text, re.S).group(1)
    regions = {}
    for name, origin, length in _REGION.findall(block):
        m = _LENGTH.fullmatch(length.strip())
        if not m:
            raise ValueError(
                f"{name}: unsupported LENGTH {length.strip()!r} "
                f"(expected a number with an optional K or M)")
        regions[name] = (int(origin, 0), int(m.group(1), 0) * _UNITS[m.group(2)])
    return regions


def alloc_sections(elf):
    """Yields (name, sh_type, addr, size) for each section that takes memory."""
    if elf[:4] != b"\x7fELF" or elf[4] != 1 or elf[5] != 1:
        raise ValueError("expected a 32-bit little-endian ELF")
    shoff, = struct.unpack_from("<I", elf, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", elf, 0x2E)

    def header(i):
        return struct.unpack_from("<IIIIII", elf, shoff + i * shentsize)

    names = header(shstrndx)[4]  # file offset of the section-name table
    for i in range(shnum):
        name, sh_type, flags, addr, _, size = header(i)
        if flags & SHF_ALLOC and size:
            start = names + name
            name = elf[start:elf.index(b"\0", start)].decode()
            yield name, sh_type, addr, size


def main(elf_path, ld_path, budget_pct):
    with open(elf_path, "rb") as f:
        elf = f.read()
    with open(ld_path, encoding="utf-8") as f:
        regions = memory_regions(f.read())
    if "FLASH" not in regions or "RAM" not in regions:
        raise ValueError(
            f"{ld_path}: MEMORY needs regions named FLASH and RAM "
            f"(found {sorted(regions)})")
    flash_origin, flash_len = regions["FLASH"]
    ram_origin, ram_len = regions["RAM"]
    if not flash_len or not ram_len:
        raise ValueError(f"{ld_path}: FLASH or RAM has zero length")
    flash = ram = 0
    for name, sh_type, addr, size in alloc_sections(elf):
        if ram_origin <= addr < ram_origin + ram_len:
            ram += size
            if sh_type != SHT_NOBITS:
                flash += size  # initial values of .data
        elif flash_origin <= addr < flash_origin + flash_len:
            flash += size
        else:
            # CCM SRAM, a second RAM bank, and the like. Skipping it would
            # let the budget pass on an undercount.
            raise ValueError(
                f"{elf_path}: section {name} at {addr:#x} is outside FLASH "
                f"and RAM, so it can't be counted against the budget")
    failed = False
    for name, used, total in (("flash", flash, flash_len), ("RAM", ram, ram_len)):
        pct = 100.0 * used / total
        print(f"{name}: {used} / {total} bytes ({pct:.1f}%, budget {budget_pct}%)")
        failed |= pct > budget_pct
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], float(sys.argv[3])))
