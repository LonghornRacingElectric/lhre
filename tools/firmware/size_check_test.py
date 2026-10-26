import unittest

from tools.firmware import size_check


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


if __name__ == "__main__":
    unittest.main()
