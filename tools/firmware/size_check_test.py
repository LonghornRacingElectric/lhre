import os
import struct
import tempfile
import unittest

from tools.firmware import size_check

_LD = ("MEMORY\n{\nRAM (xrw) : ORIGIN = 0x20000000, LENGTH = 1K\n"
       "FLASH (rx) : ORIGIN = 0x8000000, LENGTH = 2K\n}\n")


def _elf(sections):
    """A 32-bit little-endian ELF holding only section headers.

    sections: (sh_type, sh_flags, sh_addr, sh_size) per section, after the
    null section every ELF starts with.
    """
    header = bytearray(52)
    header[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<I", header, 0x20, len(header))  # e_shoff
    struct.pack_into("<HH", header, 0x2E, 40, len(sections) + 1)
    entries = [(0, 0, 0, 0)] + list(sections)
    return bytes(header) + b"".join(
        struct.pack("<10I", 0, sh_type, flags, addr, 0, size, 0, 0, 0, 0)
        for sh_type, flags, addr, size in entries)


PROGBITS = 1
TEXT = (PROGBITS, 0x6, 0x8000000, 1000)
DATA = (PROGBITS, 0x3, 0x20000000, 100)
BSS = (size_check.SHT_NOBITS, 0x3, 0x20000064, 500)
DEBUG = (PROGBITS, 0x0, 0x0, 4000)  # not SHF_ALLOC: takes no memory


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

    def test_no_attributes(self):
        regions = size_check.memory_regions(
            "MEMORY {\nFLASH : ORIGIN = 0x8000000, LENGTH = 512K\n}")
        self.assertEqual(regions["FLASH"], (0x8000000, 512 * 1024))

    def test_length_expression_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "512K - 16K"):
            size_check.memory_regions(
                "MEMORY {\nFLASH (rx) : ORIGIN = 0x8004000, "
                "LENGTH = 512K - 16K\n}")


class MainTest(unittest.TestCase):
    def check(self, sections, budget_pct):
        with tempfile.TemporaryDirectory() as tmp:
            elf_path = os.path.join(tmp, "image.elf")
            ld_path = os.path.join(tmp, "image.ld")
            with open(elf_path, "wb") as f:
                f.write(_elf(sections))
            with open(ld_path, "w", encoding="utf-8") as f:
                f.write(_LD)
            return size_check.main(elf_path, ld_path, budget_pct)

    def test_counts_data_against_flash_and_ram(self):
        sections = [TEXT, DATA, BSS, DEBUG]
        # flash: .text + .data's initial values = 1100 / 2048 (53.7%).
        # RAM: .data + .bss = 600 / 1024 (58.6%).
        self.assertEqual(self.check(sections, 60), 0)
        self.assertEqual(self.check(sections, 58), 1)
        self.assertEqual(self.check([TEXT, BSS], 50), 0)  # 48.8% / 48.8%

    def test_rejects_64_bit_elf(self):
        with self.assertRaisesRegex(ValueError, "32-bit"):
            list(size_check.alloc_sections(b"\x7fELF\x02\x01" + bytes(58)))


if __name__ == "__main__":
    unittest.main()
