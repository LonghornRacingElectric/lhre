"""BUILD overlay for STMicroelectronics/stm32-mw-usb-device.

Only the Core and the CDC (virtual COM port) class are exposed; add globs
for other classes (MSC, HID, DFU, ...) if a board ever needs them.
"""

load("@rules_cc//cc:defs.bzl", "cc_library")

cc_library(
    name = "usb_device_headers",
    hdrs = glob(
        [
            "Core/Inc/*.h",
            "Class/CDC/Inc/*.h",
        ],
        exclude = ["**/*_template.h"],
    ),
    includes = [
        "Class/CDC/Inc",
        "Core/Inc",
    ],
    visibility = ["//visibility:public"],
)

filegroup(
    name = "usb_device_srcs",
    srcs = glob(
        [
            "Core/Src/*.c",
            "Class/CDC/Src/*.c",
        ],
        exclude = ["**/*_template.c"],
    ),
    visibility = ["//visibility:public"],
)
