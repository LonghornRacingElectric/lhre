load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("@rules_cc//cc:cc_test.bzl", "cc_test")
load("//drivers/2027/lhal:lhal_library.bzl", "lhal_cc_library")
load("//tools/firmware:firmware_project.bzl", "firmware_project")

# Application logic: LHAL interfaces only, no ST HAL. Compiles for both the
# MCU (linked into :vcu below) and the host (:vcu_app_test, :vcu_sim).
lhal_cc_library(
    name = "vcu_app",
    srcs = ["App/vcu_app.cpp"],
    hdrs = ["App/vcu_app.hpp"],
    family = "stm32g4",
    includes = ["App"],
    deps = ["//drivers/2027/lhal"],
)

cc_test(
    name = "vcu_app_test",
    size = "small",
    srcs = ["App/vcu_app_test.cpp"],
    deps = [
        ":vcu_app",
        "//drivers/2027/lhal:host",
        "@googletest//:gtest_main",
    ],
)

cc_binary(
    name = "vcu_sim",
    srcs = ["App/vcu_sim.cpp"],
    deps = [
        ":vcu_app",
        "//drivers/2027/lhal:host",
    ],
)

firmware_project(
    name = "vcu",
    srcs = glob(
        [
            "Core/Src/**/*.c",
            "Core/Src/**/*.cpp",
        ],
        allow_empty = True,
    ) + glob(
        [
            "Core/Inc/**/*.h",
            "Core/Inc/**/*.hpp",
        ],
        allow_empty = True,
    ),
    defines = ["STM32G474xx"],
    enable_printf_float = True,
    extra_deps = [
        ":vcu_app",
        "//drivers/2027/lhal:stm32_headers",
    ],
    extra_srcs = ["//drivers/2027/lhal:stm32_srcs"],
    family = "stm32g4",
    linker_script = "STM32G474XX_FLASH.ld",
    startup_script = "startup_stm32g474xx.s",
)
