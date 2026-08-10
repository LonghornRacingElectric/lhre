# lib

Platform-agnostic C++ that builds on host **and** target: ring buffers, CRC,
CAN pack/unpack, filters, … Nothing here may include ST headers or LHAL —
pure logic only, so it compiles anywhere and tests run on the host.

Empty so far. When adding the first library: plain `cc_library` +
colocated `cc_test`, one subdirectory per library, with a `README.md`
saying what it's for.
