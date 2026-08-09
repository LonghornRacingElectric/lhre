"""Macro for application libraries that build for both host and MCU."""

load("@rules_cc//cc:cc_library.bzl", "cc_library")
load("//tools/firmware:firmware_project.bzl", "FAMILY_FLAGS")

def lhal_cc_library(name, family = "stm32g4", copts = [], **kwargs):
    """A cc_library usable from host tests/sims and firmware_project alike.

    Firmware binaries are compiled with MCU-specific flags (float ABI, CPU);
    a plain cc_library dependency would compile without them and fail to
    link ("uses VFP register arguments" errors). This macro adds the family's
    MCU flags when cross-compiling for bare metal and none on the host.

    Args:
        name: target name.
        family: STM32 family whose flags to use when building for the MCU.
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
