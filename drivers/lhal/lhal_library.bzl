"""Macro for application libraries that build for both host and MCU."""

load("@rules_cc//cc:cc_library.bzl", "cc_library")
load("//tools/firmware:firmware_project.bzl", "FAMILY_FLAGS")

def lhal_cc_library(name, family, copts = [], **kwargs):
    """A cc_library usable from host tests/sims and firmware_project alike.

    Firmware binaries are compiled with MCU-specific flags (float ABI, CPU);
    a plain cc_library dependency would compile without them and fail to
    link ("uses VFP register arguments" errors). This macro adds the family's
    MCU flags when cross-compiling for bare metal and none on the host.

    `family` is deliberately mandatory: a default would silently give every
    library one family's float ABI and break the first board on a different
    chip. The real fix is per-family platforms + toolchains with these flags
    baked in, at which point this macro disappears.

    Args:
        name: target name.
        family: STM32 family whose flags to use when building for the MCU
            (a key of FAMILY_FLAGS in firmware_project.bzl).
        copts: extra copts, appended on all platforms.
        **kwargs: forwarded to cc_library.
    """
    cc_library(
        name = name,
        copts = select({
            "@platforms//os:none": FAMILY_FLAGS[family],
            "//conditions:default": [],
        }) + copts,
        **kwargs
    )
