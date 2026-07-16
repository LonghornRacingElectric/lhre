"""BUILD overlay for ARM-software/CMSIS_5 — Core headers and RTOS2 API."""

load("@rules_cc//cc:defs.bzl", "cc_library")

cc_library(
    name = "cmsis_core_headers",
    hdrs = glob(["CMSIS/Core/Include/**/*.h"]),
    includes = ["CMSIS/Core/Include"],
    visibility = ["//visibility:public"],
)

# CMSIS-RTOS2 API headers (cmsis_os2.h, os_tick.h)
cc_library(
    name = "cmsis_rtos2_headers",
    hdrs = glob(["CMSIS/RTOS2/Include/**/*.h"]),
    includes = ["CMSIS/RTOS2/Include"],
    visibility = ["//visibility:public"],
)

