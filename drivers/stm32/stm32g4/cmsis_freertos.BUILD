"""BUILD overlay for ARM-software/CMSIS-FreeRTOS — CMSIS-RTOS2 FreeRTOS wrapper."""

load("@rules_cc//cc:defs.bzl", "cc_library")

# CMSIS-RTOS2 FreeRTOS wrapper headers (freertos_os2.h, etc.)
cc_library(
    name = "cmsis_rtos2_freertos_headers",
    hdrs = glob(["CMSIS/RTOS2/FreeRTOS/Include/**/*.h"]),
    includes = ["CMSIS/RTOS2/FreeRTOS/Include"],
    visibility = ["//visibility:public"],
)

# CMSIS-RTOS2 FreeRTOS wrapper source files (cmsis_os2.c)
filegroup(
    name = "cmsis_rtos2_freertos_srcs",
    srcs = glob(["CMSIS/RTOS2/FreeRTOS/Source/**/*.c"]),
    visibility = ["//visibility:public"],
)
