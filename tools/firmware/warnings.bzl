"""Compiler warnings for hand-written firmware code. See README.md#warnings."""

# Every flag is an error: the code was clean when each went on, so a new
# warning is a new bug, not a backlog. GCC (ARM) and clang (host) both
# take these; a flag one of them lacks would break that build.
FIRMWARE_WARNINGS = [
    "-Wall",
    "-Wextra",
    "-Werror",
    # A local hiding a member or outer variable: the classic wrong-variable
    # bug in control code.
    "-Wshadow",
    # float silently widened to double. The M4F has a single-precision FPU,
    # so double math is software-emulated and slow in a control loop.
    "-Wdouble-promotion",
    # A float or double narrowed to an integer or to float without a cast.
    "-Wfloat-conversion",
    # A switch case that runs into the next without [[fallthrough]].
    "-Wimplicit-fallthrough",
    "-Wnull-dereference",
    # Deleting through a base pointer without a virtual destructor.
    "-Wnon-virtual-dtor",
    # printf-family format strings checked against their arguments, and
    # format strings that aren't literals.
    "-Wformat=2",
    # #if on an undefined macro, which silently reads as 0.
    "-Wundef",
]
