import contextlib
import io
import os
import struct
import tempfile
import unittest

from tools.firmware import size_check

PROGBITS = 1
STRTAB = 3
NOBITS = size_check.SHT_NOBITS
ALLOC = 0x2

# Small regions so percentages are easy to read off.
G4 = """MEMORY
{
RAM (xrw)   : ORIGIN = 0x20000000, LENGTH = 1K
FLASH (rx)  : ORIGIN = 0x8000000, LENGTH = 2K
}
"""
F4 = G4.replace("}", "CCMRAM (xrw) : ORIGIN = 0x10000000, LENGTH = 1K\n}")
H7 = """MEMORY
{
  FLASH   (rx)  : ORIGIN = 0x08000000, LENGTH = 4K
  DTCMRAM (xrw) : ORIGIN = 0x20000000, LENGTH = 1K
  RAM_D1  (xrw) : ORIGIN = 0x24000000, LENGTH = 2K
  RAM_D2  (xrw) : ORIGIN = 0x30000000, LENGTH = 1K
  RAM_D3  (xrw) : ORIGIN = 0x38000000, LENGTH = 1K
  ITCMRAM (xrw) : ORIGIN = 0x00000000, LENGTH = 1K
}
"""


def _section(name, vma, size, lma=None, sh_type=PROGBITS, flags=ALLOC):
    return name, sh_type, flags, vma, size, vma if lma is None else lma


def _elf(sections):
    """A 32-bit little-endian ELF with only headers and section names.

    Each PROGBITS section gets its own PT_LOAD segment carrying it from its
    LMA, which is how the linker describes `> RAM AT> FLASH`. Contents are
    never read, so the file offsets point at nothing.
    """
    names = b"\0"
    shdrs = [(0, 0, 0, 0, 0, 0)]
    phdrs = []
    for i, (name, sh_type, flags, vma, size, lma) in enumerate(sections):
        offset = 0x10000 * (i + 1)
        shdrs.append((len(names), sh_type, flags, vma, offset, size))
        names += name.encode() + b"\0"
        if sh_type != NOBITS and flags & ALLOC:
            phdrs.append((size_check.PT_LOAD, offset, vma, lma, size, size))
    phoff = 52
    names_offset = phoff + 32 * len(phdrs)
    shoff = names_offset + len(names)
    shdrs.append((0, STRTAB, 0, 0, names_offset, len(names)))

    header = bytearray(52)
    header[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<II", header, 0x1C, phoff, shoff)
    struct.pack_into("<HHHHH", header, 0x2A, 32, len(phdrs), 40, len(shdrs),
                     len(shdrs) - 1)
    return (bytes(header) +
            b"".join(struct.pack("<6I8x", *p) for p in phdrs) + names +
            b"".join(struct.pack("<6I16x", *s) for s in shdrs))


TEXT = _section(".text", 0x8000000, 1000)
DATA = _section(".data", 0x20000000, 100, lma=0x80003E8)
BSS = _section(".bss", 0x20000064, 500, sh_type=NOBITS)
DEBUG = _section(".debug_info", 0x0, 4000, flags=0)


class MemoryRegionsTest(unittest.TestCase):
    def test_decimal_units(self):
        regions = size_check.memory_regions(
            "MEMORY\n{\nRAM (xrw) : ORIGIN = 0x20000000, LENGTH = 128K\n"
            "FLASH (rx) : ORIGIN = 0x8000000, LENGTH = 510K\n}\n")
        self.assertEqual(regions["RAM"], (0x20000000, 128 * 1024))
        self.assertEqual(regions["FLASH"], (0x8000000, 510 * 1024))

    def test_hex_length(self):
        regions = size_check.memory_regions(
            "MEMORY { RAM (xrw) : ORIGIN = 0x20000000, LENGTH = 0x20000 }")
        self.assertEqual(regions["RAM"], (0x20000000, 0x20000))

    def test_brace_in_comment(self):
        regions = size_check.memory_regions(
            "MEMORY {\n/* last page {reserved} */\n"
            "FLASH (rx) : ORIGIN = 0x8000000, LENGTH = 512K\n}")
        self.assertEqual(regions["FLASH"], (0x8000000, 512 * 1024))

    def test_no_attributes_and_abbreviations(self):
        regions = size_check.memory_regions(
            "MEMORY {\nFLASH : org = 0x8000000, l = 512K\n}")
        self.assertEqual(regions["FLASH"], (0x8000000, 512 * 1024))

    def test_every_region_in_order(self):
        self.assertEqual(
            list(size_check.memory_regions(H7)),
            ["FLASH", "DTCMRAM", "RAM_D1", "RAM_D2", "RAM_D3", "ITCMRAM"])

    def test_expressions_are_errors(self):
        for line in ("FLASH (rx) : ORIGIN = 0x8004000, LENGTH = 512K - 16K",
                     "FLASH (rx) : ORIGIN = 0x8000000 + 16K, LENGTH = 496K"):
            with self.subTest(line=line):
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    size_check.memory_regions(f"MEMORY {{\n{line}\n}}")

    def test_no_memory_block(self):
        with self.assertRaisesRegex(ValueError, "no MEMORY block"):
            size_check.memory_regions("SECTIONS { }")
        with self.assertRaisesRegex(ValueError, "no regions"):
            size_check.memory_regions("MEMORY { }")


class AllocSectionsTest(unittest.TestCase):
    def test_reads_names_and_load_addresses(self):
        self.assertEqual(
            list(size_check.alloc_sections(_elf([TEXT, DATA, BSS, DEBUG]))),
            [(".text", 0x8000000, 0x8000000, 1000),
             (".data", 0x20000000, 0x80003E8, 100),
             (".bss", 0x20000064, None, 500)])

    def test_rejects_64_bit_elf(self):
        with self.assertRaisesRegex(ValueError, "32-bit"):
            list(size_check.alloc_sections(b"\x7fELF\x02\x01" + bytes(58)))


class UsageTest(unittest.TestCase):
    def usage(self, ld, sections):
        return size_check.usage(size_check.memory_regions(ld),
                                size_check.alloc_sections(_elf(sections)))

    def test_load_copy_counts_in_both_regions(self):
        self.assertEqual(self.usage(G4, [TEXT, DATA, BSS, DEBUG]),
                         {"RAM": 600, "FLASH": 1100})

    def test_f4_ccmram(self):
        ccm = _section(".ccmram", 0x10000000, 64, lma=0x800044C)
        self.assertEqual(self.usage(F4, [TEXT, DATA, ccm]),
                         {"RAM": 100, "FLASH": 1164, "CCMRAM": 64})

    def test_h7_regions(self):
        sections = [
            _section(".isr_vector", 0x08000000, 0x298),
            _section(".data", 0x24000000, 64, lma=0x08000298),
            _section(".bss", 0x24000040, 512, sh_type=NOBITS),
            _section(".dma_buffers", 0x30000000, 256, sh_type=NOBITS),
            _section(".stack", 0x20000000, 1024, sh_type=NOBITS),
            _section(".itcm_text", 0x0, 128, lma=0x080002D8),
        ]
        self.assertEqual(self.usage(H7, sections), {
            "FLASH": 0x298 + 64 + 128,
            "DTCMRAM": 1024,
            "RAM_D1": 576,
            "RAM_D2": 256,
            "RAM_D3": 0,
            "ITCMRAM": 128,
        })

    def test_section_ending_at_region_end_fits(self):
        self.assertEqual(
            self.usage(G4, [_section(".text", 0x8000000, 2048)])["FLASH"],
            2048)

    def test_section_past_region_end_is_an_error(self):
        for section in (_section(".text", 0x8000000, 2049),
                        _section(".text", 0x8000800, 1)):
            with self.subTest(section=section):
                with self.assertRaisesRegex(ValueError, "doesn't fit"):
                    self.usage(G4, [section])

    def test_section_outside_every_region_is_an_error(self):
        ccm = _section(".ccmram", 0x10000000, 64)
        with self.assertRaisesRegex(ValueError, r"\.ccmram .* 0x10000000"):
            self.usage(G4, [ccm])

    def test_load_address_outside_every_region_is_an_error(self):
        data = _section(".data", 0x20000000, 64, lma=0x9000000)
        with self.assertRaisesRegex(ValueError, "0x9000000"):
            self.usage(G4, [data])


class MainTest(unittest.TestCase):
    def check(self, ld, sections, budget_pct, overrides=()):
        self.output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            elf_path = os.path.join(tmp, "image.elf")
            ld_path = os.path.join(tmp, "image.ld")
            with open(elf_path, "wb") as f:
                f.write(_elf(sections))
            with open(ld_path, "w", encoding="utf-8") as f:
                f.write(ld)
            with contextlib.redirect_stdout(self.output):
                return size_check.main(elf_path, ld_path, budget_pct,
                                       overrides)

    def test_budget_applies_to_every_region(self):
        # FLASH 1100 / 2048 (53.7%), RAM 600 / 1024 (58.6%).
        self.assertEqual(self.check(G4, [TEXT, DATA, BSS], 60), 0)
        self.assertEqual(self.check(G4, [TEXT, DATA, BSS], 58), 1)

    def test_non_primary_region_over_budget(self):
        dma = _section(".dma_buffers", 0x30000000, 1000, sh_type=NOBITS)
        self.assertEqual(self.check(H7, [dma], 85), 1)
        self.assertIn("| RAM_D2 | 1,000 | 1,024 | 97.7% | 85% | over |",
                      self.output.getvalue())
        self.assertIn("| RAM_D1 | 0 | 2,048 | 0.0% | 85% | ok |",
                      self.output.getvalue())

    def test_region_override(self):
        stack = _section(".stack", 0x20000000, 1024, sh_type=NOBITS)
        self.assertEqual(self.check(H7, [stack], 85), 1)
        self.assertEqual(self.check(H7, [stack], 85, ["DTCMRAM=100"]), 0)

    def test_override_for_unknown_region_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "CCMRAM"):
            self.check(G4, [TEXT], 85, ["CCMRAM=100"])


if __name__ == "__main__":
    unittest.main()
