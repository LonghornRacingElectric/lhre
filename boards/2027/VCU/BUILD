load("//tools/firmware:firmware_project.bzl", "firmware_project")

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
    family = "stm32g4",
    linker_script = "STM32G474XX_FLASH.ld",
    startup_script = "startup_stm32g474xx.s",
)
