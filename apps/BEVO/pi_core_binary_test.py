import struct
import sys
import unittest
from pathlib import Path


class PiCoreBinaryTest(unittest.TestCase):
    def test_core_daemons_are_static_aarch64_elfs(self):
        paths = [Path(path) for argument in sys.argv[1:] for path in argument.split()]
        self.assertEqual({path.name for path in paths}, {"cand", "loggerd", "publishd"})

        for path in paths:
            data = path.read_bytes()
            self.assertEqual(data[:4], b"\x7fELF", path)
            self.assertEqual(data[4], 2, f"{path} is not ELF64")
            self.assertEqual(data[5], 1, f"{path} is not little-endian")
            self.assertEqual(struct.unpack_from("<H", data, 18)[0], 183, f"{path} is not AArch64")

            program_offset = struct.unpack_from("<Q", data, 32)[0]
            entry_size = struct.unpack_from("<H", data, 54)[0]
            entry_count = struct.unpack_from("<H", data, 56)[0]
            program_types = {
                struct.unpack_from("<I", data, program_offset + index * entry_size)[0]
                for index in range(entry_count)
            }
            self.assertNotIn(3, program_types, f"{path} contains a dynamic interpreter")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
