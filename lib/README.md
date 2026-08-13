# lib

Code shared across boards and hosts, plus the CAN spec it is generated
from:

- [spec/](spec/README.md): the CAN spec. Data and Python, no C++.
- [codegen/](codegen/README.md): generators over the spec and their
  output. [codegen/cpp](codegen/cpp/README.md) is the firmware
  pack/unpack library.
- Everything else: platform-agnostic C++ that builds on host and target.
  Ring buffers, CRC, filters. Plain `cc_library` with a colocated
  `cc_test`, one subdirectory each, with a `README.md`.

Hand-written C++ here must not include ST headers or LHAL, so it compiles
anywhere and tests run on the host. Generated code is the exception:
`//lib/codegen/cpp:can_lib` depends on the LHAL interface headers for
`lhal::CanFrame` glue. Those are platform-independent, so everything
under lib still builds everywhere.
