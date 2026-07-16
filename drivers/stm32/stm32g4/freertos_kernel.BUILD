"""BUILD overlay for FreeRTOS/FreeRTOS-Kernel."""

load("@rules_cc//cc:defs.bzl", "cc_library")

# Core kernel headers (include/)
cc_library(
    name = "freertos_kernel_headers",
    hdrs = glob(["include/**/*.h"]),
    includes = ["include"],
    visibility = ["//visibility:public"],
)

# Core kernel source files
filegroup(
    name = "freertos_kernel_srcs",
    srcs = glob(
        ["*.c"],
        exclude = ["portable/**"],
    ),
    visibility = ["//visibility:public"],
)

# ARM Cortex-M4F port source files (GCC)
filegroup(
    name = "freertos_port_srcs",
    srcs = glob(["portable/GCC/ARM_CM4F/*.c"]),
    visibility = ["//visibility:public"],
)

# ARM Cortex-M4F port headers
cc_library(
    name = "freertos_port_headers",
    hdrs = glob(["portable/GCC/ARM_CM4F/*.h"]),
    includes = ["portable/GCC/ARM_CM4F"],
    visibility = ["//visibility:public"],
)

# Heap implementation (heap_4: thread-safe malloc/free with coalescence)
filegroup(
    name = "freertos_heap",
    srcs = ["portable/MemMang/heap_4.c"],
    visibility = ["//visibility:public"],
)
