# lib

Code shared across boards and hosts, plus the CAN spec it's generated
from:

- [spec/](spec/README.md) — the CAN spec (textproto source of truth,
  loader/validator, canonical serializer). Data and Python, not C++.
- [codegen/](codegen/README.md) — generators over the spec and their
  generated libraries, one subdirectory per output
  ([codegen/cpp](codegen/cpp/README.md) is the firmware pack/unpack
  library).
- Everything else: platform-agnostic C++ that builds on host **and**
  target — ring buffers, CRC, filters, … plain `cc_library` +
  colocated `cc_test`, one subdirectory per library, with a `README.md`
  saying what it's for.

Hand-written C++ here must not include ST headers or LHAL — pure logic
only, so it compiles anywhere and tests run on the host. The one
deliberate exception is generated code: `//lib/codegen/cpp:can_lib`
depends on the LHAL *interface* headers (for `lhal::CanFrame` glue),
which are themselves platform-independent, so everything under lib
still builds and tests everywhere.
