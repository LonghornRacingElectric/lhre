"""Module extension to fetch STM32 G4 driver dependencies from GitHub."""

load("@bazel_tools//tools/build_defs/repo:git.bzl", "git_repository")

def _stm32g4_deps_impl(_ctx):
    git_repository(
        name = "stm32g4xx_hal_driver",
        remote = "https://github.com/STMicroelectronics/stm32g4xx-hal-driver.git",
        commit = "a6001282dfacfff57e9710250f15e4333b578865",
        build_file = "//drivers/stm32/stm32g4:hal_driver.BUILD",
    )

    git_repository(
        name = "cmsis_device_stm32g4",
        remote = "https://github.com/STMicroelectronics/cmsis-device-g4.git",
        commit = "626ee412334a5ed2e5b320af5a8d77d69f03a558",
        build_file = "//drivers/stm32/stm32g4:cmsis_device.BUILD",
    )

    git_repository(
        name = "cmsis_core_stm32g4",
        remote = "https://github.com/ARM-software/CMSIS_5.git",
        commit = "2b7495b8535bdcb306dac29b9ded4cfb679d7e5c",
        build_file = "//drivers/stm32/stm32g4:cmsis_core.BUILD",
    )

    git_repository(
        name = "freertos_kernel_stm32g4",
        remote = "https://github.com/FreeRTOS/FreeRTOS-Kernel.git",
        tag = "V11.1.0",
        build_file = "//drivers/stm32/stm32g4:freertos_kernel.BUILD",
    )

    git_repository(
        name = "cmsis_freertos_stm32g4",
        remote = "https://github.com/ARM-software/CMSIS-FreeRTOS.git",
        tag = "v11.1.0",
        build_file = "//drivers/stm32/stm32g4:cmsis_freertos.BUILD",
    )

stm32g4_deps = module_extension(
    implementation = _stm32g4_deps_impl,
)
