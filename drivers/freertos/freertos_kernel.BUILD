"""BUILD overlay for FreeRTOS/FreeRTOS-Kernel.

Kernel sources are exposed as filegroups (not cc_libraries) because they must
be compiled *inside* the consuming binary, where that build's FreeRTOSConfig.h
is on the include path — the same pattern as the ST HAL sources.
"""

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

# Heap implementation for firmware (heap_4: thread-safe malloc/free with
# coalescence inside a fixed configTOTAL_HEAP_SIZE arena)
filegroup(
    name = "freertos_heap",
    srcs = ["portable/MemMang/heap_4.c"],
    visibility = ["//visibility:public"],
)

# Heap implementation for the host simulator ports (heap_3: wraps the real
# malloc/free, so configTOTAL_HEAP_SIZE doesn't apply)
filegroup(
    name = "heap_3",
    srcs = ["portable/MemMang/heap_3.c"],
    visibility = ["//visibility:public"],
)

# POSIX simulator port (Linux/macOS): each task is a pthread, the scheduler
# runs one at a time. Supports vTaskEndScheduler().
filegroup(
    name = "posix_port_srcs",
    srcs = [
        "portable/ThirdParty/GCC/Posix/port.c",
        "portable/ThirdParty/GCC/Posix/utils/wait_for_event.c",
    ],
    visibility = ["//visibility:public"],
)

cc_library(
    name = "posix_port_headers",
    hdrs = glob(["portable/ThirdParty/GCC/Posix/**/*.h"]),
    includes = [
        "portable/ThirdParty/GCC/Posix",
        "portable/ThirdParty/GCC/Posix/utils",
    ],
    visibility = ["//visibility:public"],
)

# Windows simulator port (MSVC/MinGW/clang): tasks are Win32 threads; the
# tick comes from a winmm multimedia timer (link winmm).
filegroup(
    name = "windows_port_srcs",
    srcs = ["portable/MSVC-MingW/port.c"],
    visibility = ["//visibility:public"],
)

cc_library(
    name = "windows_port_headers",
    hdrs = glob(["portable/MSVC-MingW/*.h"]),
    includes = ["portable/MSVC-MingW"],
    visibility = ["//visibility:public"],
)
