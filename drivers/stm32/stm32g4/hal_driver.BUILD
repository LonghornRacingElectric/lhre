"""BUILD overlay for STMicroelectronics/stm32g4xx-hal-driver."""

load("@rules_cc//cc:defs.bzl", "cc_library")

cc_library(
    name = "hal_headers",
    hdrs = glob([
        "Inc/**/*.h",
        "Inc/Legacy/**/*.h",
    ]),
    includes = [
        "Inc",
        "Inc/Legacy",
    ],
    visibility = ["//visibility:public"],
    deps = [
        "@cmsis_core_stm32g4//:cmsis_core_headers",
        "@cmsis_device_stm32g4//:cmsis_device_headers",
    ],
)

filegroup(
    name = "hal_srcs",
    srcs = glob(
        ["Src/**/*.c"],
        exclude = ["Src/*template.c"],
    ),
    visibility = ["//visibility:public"],
)
