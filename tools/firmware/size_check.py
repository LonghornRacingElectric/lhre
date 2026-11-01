"""Fails when a firmware image fills any memory region past its budget.

Usage: size_check.py <image.elf> <linker_script.ld> <budget_percent>
                     [REGION=PERCENT ...]

Regions are whatever the linker script's MEMORY block declares, so the same
check fits every board without knowing what FLASH, RAM, CCMRAM or RAM_D1
mean. Each allocated section counts against the region holding its run
address (VMA) and, when it's loaded from somewhere else (LMA), against that
region too: .data costs RAM and the flash its initial values live in.

Anything this script can't account for exactly is an error, not a guess: an
ORIGIN or LENGTH expression like `512K - 16K`, or a section that doesn't sit
wholly inside one region. Guessing would let the budget pass on an
undercount.
"""

import re
import struct
import sys

SHF_ALLOC = 0x2
SHT_NOBITS = 8
PT_LOAD = 1
_UNITS = {"": 1, "K": 1024, "M": 1024 * 1024}
# GNU ld allows omitting the attributes and abbreviating ORIGIN and LENGTH.
# LENGTH runs to the end of the line, since linker scripts put one region
# per line.
_REGION = re.compile(r"(\w+)\s*(?:\([^)]*\))?\s*:\s*(?:ORIGIN|org|o)\s*=\s*"
                     r"([^,]+),\s*(?:LENGTH|len|l)\s*=\s*([^\n]+)")
_NUMBER = re.compile(r"(0[xX][0-9A-Fa-f]+|\d+)\s*([KM]?)")


def _number(region, field, text):
    m = _NUMBER.fullmatch(text.strip())
    if not m:
        raise ValueError(f"{region}: unsupported {field} {text.strip()!r} "
                         f"(expected a number with an optional K or M)")
    return int(m.group(1), 0) * _UNITS[m.group(2)]


def memory_regions(ld_text):
    """Returns {name: (origin, length)} from the MEMORY block, in order."""
    # Strip comments first so a brace inside one cannot end the block.
    text = re.sub(r"/\*.*?\*/", "", ld_text, flags=re.S)
    block = re.search(r"MEMORY\s*\{(.*?)\}", text, re.S)
    if not block:
        raise ValueError("no MEMORY block in the linker script")
    regions = {
        name: (_number(name, "ORIGIN", origin),
               _number(name, "LENGTH", length))
        for name, origin, length in _REGION.findall(block.group(1))
    }
    if not regions:
        raise ValueError("no regions in the linker script's MEMORY block")
    return regions


def alloc_sections(elf):
    """Yields (name, vma, lma, size) for each section that takes memory.

    lma is None for sections with no load image (.bss and the like).
    """
    if elf[:4] != b"\x7fELF" or elf[4] != 1 or elf[5] != 1:
        raise ValueError("expected a 32-bit little-endian ELF")
    phoff, shoff = struct.unpack_from("<II", elf, 0x1C)
    phentsize, phnum, shentsize, shnum, shstrndx = struct.unpack_from(
        "<HHHHH", elf, 0x2A)
    # (p_offset, p_paddr, p_filesz) of each segment the loader copies in.
    loads = []
    for i in range(phnum):
        p_type, offset, _, paddr, filesz = struct.unpack_from(
            "<5I", elf, phoff + i * phentsize)
        if p_type == PT_LOAD:
            loads.append((offset, paddr, filesz))

    def header(i):
        return struct.unpack_from("<6I", elf, shoff + i * shentsize)

    names = header(shstrndx)[4]  # file offset of the section-name table
    for i in range(shnum):
        name, sh_type, flags, vma, offset, size = header(i)
        if not flags & SHF_ALLOC or not size:
            continue
        start = names + name
        name = elf[start:elf.index(b"\0", start)].decode()
        lma = None
        if sh_type != SHT_NOBITS:
            # The section's load address is where its bytes sit in the
            # segment that carries them.
            lma = vma
            for seg_offset, paddr, filesz in loads:
                if seg_offset <= offset < seg_offset + filesz:
                    lma = paddr + offset - seg_offset
                    break
        yield name, vma, lma, size


def usage(regions, sections):
    """Returns {region: bytes used}, raising on a section no region holds."""
    used = dict.fromkeys(regions, 0)

    def charge(section, addr, size):
        for region, (origin, length) in regions.items():
            if origin <= addr and addr + size <= origin + length:
                used[region] += size
                return
        raise ValueError(
            f"section {section} ({size} bytes at {addr:#x}) doesn't fit "
            f"inside any MEMORY region")

    for name, vma, lma, size in sections:
        charge(name, vma, size)
        if lma is not None and lma != vma:
            charge(name, lma, size)
    return used


def main(elf_path, ld_path, budget_pct, overrides=()):
    with open(elf_path, "rb") as f:
        elf = f.read()
    with open(ld_path, encoding="utf-8") as f:
        regions = memory_regions(f.read())
    budgets = dict.fromkeys(regions, budget_pct)
    for override in overrides:
        region, _, pct = override.partition("=")
        if region not in regions:
            raise ValueError(f"budget for {region}, which {ld_path} doesn't "
                             f"declare (regions: {', '.join(regions)})")
        budgets[region] = float(pct)
    used = usage(regions, alloc_sections(elf))
    # A markdown table: readable in the test log, and CI copies it as-is
    # into the run's summary page.
    print("| Region | Used (bytes) | Size (bytes) | Use | Budget | |")
    print("| :-- | --: | --: | --: | --: | :-- |")
    over = []
    for region, (_, length) in regions.items():
        pct = 100.0 * used[region] / length if length else 0.0
        if pct > budgets[region]:
            over.append(region)
        print(f"| {region} | {used[region]:,} | {length:,} | {pct:.1f}% | "
              f"{budgets[region]:g}% | {'over' if region in over else 'ok'} |")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], float(sys.argv[3]),
                  sys.argv[4:]))
