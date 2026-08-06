load("//tools/firmware:firmare_project.bzl", "firmware_project")

firmware_project(
    name = "vcu",
    family = "stm32g4",
    linker_script = "STM32G474XX_FLASH.ld",
    startup_script = "startup_stm32g474xx.s",
    defines = ["STM32G474xx"],
)
