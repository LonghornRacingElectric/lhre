"""Macro for creating binary and hex files using objcopy."""

load("@aspect_bazel_lib//lib:transitions.bzl", "platform_transition_filegroup")
load("@rules_cc//cc:cc_binary.bzl", "cc_binary")

def _runfiles_path(f):
    if f.owner.workspace_name:
        prefix = "../" + f.owner.workspace_name + "/"
        if f.short_path.startswith(prefix):
            return f.owner.workspace_name + "/" + f.short_path[len(prefix):]
        return f.short_path
    else:
        return "_main/" + f.short_path

def _openocd_flash_impl(ctx):
    is_windows = ctx.target_platform_has_constraint(ctx.attr._windows_constraint[platform_common.ConstraintValueInfo])
    
    if is_windows:
        executable = ctx.actions.declare_file(ctx.label.name + ".cmd")
        content = """@echo off
set RUNFILES_DIR=%~dp0{target_name}.runfiles
"%RUNFILES_DIR%\\{tool}" "{openocd}" "{elf}" "{cfg}" %*
""".format(
            target_name = ctx.label.name,
            tool = _runfiles_path(ctx.executable.flash_tool).replace("/", "\\"),
            openocd = _runfiles_path(ctx.executable.openocd),
            elf = _runfiles_path(ctx.file.elf),
            cfg = _runfiles_path(ctx.file.cfg),
        )
    else:
        executable = ctx.actions.declare_file(ctx.label.name)
        content = """#!/bin/bash
export RUNFILES_DIR="$0.runfiles"
exec "$0.runfiles/{tool}" "{openocd}" "{elf}" "{cfg}" "$@"
""".format(
            tool = _runfiles_path(ctx.executable.flash_tool),
            openocd = _runfiles_path(ctx.executable.openocd),
            elf = _runfiles_path(ctx.file.elf),
            cfg = _runfiles_path(ctx.file.cfg),
        )

    ctx.actions.write(
        output = executable,
        content = content,
        is_executable = True,
    )

    runfiles = ctx.runfiles(files = [
        ctx.file.elf,
        ctx.file.cfg,
        ctx.executable.flash_tool,
        ctx.executable.openocd,
    ])
    runfiles = runfiles.merge(ctx.attr.flash_tool[DefaultInfo].default_runfiles)
    runfiles = runfiles.merge(ctx.attr.openocd[DefaultInfo].default_runfiles)

    return [
        DefaultInfo(
            executable = executable,
            runfiles = runfiles,
        ),
    ]

openocd_flash_target = rule(
    implementation = _openocd_flash_impl,
    executable = True,
    attrs = {
        "flash_tool": attr.label(
            default = "//tools/openocd:flash",
            executable = True,
            cfg = "target",
        ),
        "openocd": attr.label(
            default = "@openocd//:openocd",
            executable = True,
            cfg = "target",
        ),
        "elf": attr.label(
            allow_single_file = True,
            mandatory = True,
        ),
        "cfg": attr.label(
            allow_single_file = True,
            mandatory = True,
        ),
        "_windows_constraint": attr.label(
            default = "@platforms//os:windows",
        ),
    },
)

def _dfu_flash_impl(ctx):
    is_windows = ctx.target_platform_has_constraint(ctx.attr._windows_constraint[platform_common.ConstraintValueInfo])
    
    if is_windows:
        executable = ctx.actions.declare_file(ctx.label.name + ".cmd")
        content = """@echo off
set RUNFILES_DIR=%~dp0{target_name}.runfiles
"%RUNFILES_DIR%\\{tool}" "{dfu_util}" "{bin_file}" %*
""".format(
            target_name = ctx.label.name,
            tool = _runfiles_path(ctx.executable.flash_tool).replace("/", "\\"),
            dfu_util = _runfiles_path(ctx.executable.dfu_util),
            bin_file = _runfiles_path(ctx.file.bin_file),
        )
    else:
        executable = ctx.actions.declare_file(ctx.label.name)
        content = """#!/bin/bash
export RUNFILES_DIR="$0.runfiles"
exec "$0.runfiles/{tool}" "{dfu_util}" "{bin_file}" "$@"
""".format(
            tool = _runfiles_path(ctx.executable.flash_tool),
            dfu_util = _runfiles_path(ctx.executable.dfu_util),
            bin_file = _runfiles_path(ctx.file.bin_file),
        )

    ctx.actions.write(
        output = executable,
        content = content,
        is_executable = True,
    )

    runfiles = ctx.runfiles(files = [
        ctx.file.bin_file,
        ctx.executable.flash_tool,
        ctx.executable.dfu_util,
    ])
    runfiles = runfiles.merge(ctx.attr.flash_tool[DefaultInfo].default_runfiles)
    runfiles = runfiles.merge(ctx.attr.dfu_util[DefaultInfo].default_runfiles)

    return [
        DefaultInfo(
            executable = executable,
            runfiles = runfiles,
        ),
    ]

dfu_flash_target = rule(
    implementation = _dfu_flash_impl,
    executable = True,
    attrs = {
        "flash_tool": attr.label(
            default = "//tools/dfu:flash",
            executable = True,
            cfg = "target",
        ),
        "dfu_util": attr.label(
            default = "@dfu//:dfu",
            executable = True,
            cfg = "target",
        ),
        "bin_file": attr.label(
            allow_single_file = True,
            mandatory = True,
        ),
        "_windows_constraint": attr.label(
            default = "@platforms//os:windows",
        ),
    },
)



def firmware_outputs(name, src, project_name, visibility = None, **kwargs):
    """
    Runs objcopy to convert a source file into both .bin and .hex files.

    Args:
      name: The name of the output target.
      src: The label of the single source file to convert.
      project_name: The name of the project.
      visibility: visibility of the target,
      **kwargs: extra args to pass to genrule.
    """

    # Define the output filenames based on the rule's name
    bin_out = "{}.bin".format(project_name)
    hex_out = "{}.hex".format(project_name)
    elf_out = "{}.elf".format(project_name)

    command = (
        "$(execpath @arm_none_eabi//:objcopy) -O binary $< $(location {bin}) && " +
        "$(execpath @arm_none_eabi//:objcopy) -O ihex $< $(location {hex}) && " +
        "cp $< $(location {elf})"
    ).format(bin = bin_out, hex = hex_out, elf = elf_out)

    native.genrule(
        name = name,
        srcs = [src],
        outs = [
            bin_out,
            hex_out,
            elf_out,
        ],
        cmd = command,
        tools = ["@arm_none_eabi//:objcopy"],
        visibility = visibility,
        **kwargs
    )

def binary_out(name, src, visibility = None, **kwargs):
    """
    Runs objcopy to convert a source file (e.g., an ELF) into a raw binary.

    Args:
      name: The name of the output target. The output filename will be `name + ".bin"`.
      src: The label of the single source file to convert.
      visibility: The visibility of the generated rule.
      **kwargs: Additional arguments to pass to the underlying genrule.
    """
    native.genrule(
        name = "{}_bin".format(name),
        srcs = [src],
        outs = ["{}.bin".format(name)],
        cmd = "$(execpath @arm_none_eabi//:objcopy) -O binary $< $@",
        tools = ["@arm_none_eabi//:objcopy"],
        visibility = visibility,
        **kwargs
    )

def hex_out(name, src, visibility = None, **kwargs):
    """
    Runs objcopy to convert a source file (e.g., an ELF) into a hex binary.

    Args:
      name: The name of the output target. The output filename will be `name + ".hex"`.
      src: The label of the single source file to convert.
      visibility: The visibility of the generated rule.
      **kwargs: Additional arguments to pass to the underlying genrule.
    """
    native.genrule(
        name = "{}_hex".format(name),
        srcs = [src],
        outs = ["{}.hex".format(name)],
        cmd = "$(execpath @arm_none_eabi//:objcopy) -O ihex $< $@",
        # cmd_bat = "copy \"$(location @arm_none_eabi//:objcopy)\" objcopy.exe && objcopy.exe -O ihex $< $@",
        tools = ["@arm_none_eabi//:objcopy"],
        visibility = visibility,
        **kwargs
    )

def elf_out(name, src, visibility = None, **kwargs):
    """
    Copies input elf to an elf output.

    Args:
      name: The name of the output target. The output filename will be `name + ".elf"`.
      src: The label of the single source file to convert.
      visibility: The visibility of the generated rule.
      **kwargs: Additional arguments to pass to the underlying genrule.
    """
    native.genrule(
        name = "{}_elf".format(name),
        srcs = [src],
        outs = ["{}.elf".format(name)],
        cmd = "cp $< $@",
        cmd_bat = "copy $< $@",
        tools = ["@arm_none_eabi//:objcopy"],
        visibility = visibility,
        **kwargs
    )

MCU_FLAGS = [
    "-mcpu=cortex-m4",
    "-mthumb",
    "-mfpu=fpv4-sp-d16",
    "-mfloat-abi=hard",
    "-fdiagnostics-color",
]

def firmware_project(
        name,
        linker_script,
        startup_script,
        family,
        enable_usb = False,
        defines = [],
        extra_srcs = [],
        extra_deps = [],
        usb_device_name = None,
        extra_includes = [],
        enable_freertos = False,
        enable_dfu = False,
        locations = [],
        use_longhorn_lib = False,
        enable_ota = False,
        **kwargs):
    """Creates a firmware project for STM32 microcontrollers.

    Args:
        name (string): name of the project
        linker_script (path): the location of the linker script being used (.ld file)
        startup_script (path): the location of the startup script being used (.s file)
        family (string): the STM32 family, e.g. "stm32g4", etc.
        enable_usb (bool, optional): Whether or not to use USB drivers. Defaults to False.
        defines (list, optional): defines to pass to the compiler. Defaults to [].
        extra_srcs (list, optional): extra sources to compile with. Defaults to [].
        extra_deps (list, optional): extra dependencies to compile with. Defaults to [].
        usb_device_name (_type_, optional): name you want the USB driver to have. Defaults to None.
        extra_includes (list, optional): extra include paths to compile with. Defaults to [].
        enable_freertos (bool, optional): Whether or not to use FreeRTOS. Defaults to False.
        enable_dfu (bool, optional): Whether or not to accept strings to go into DFU. Defaults to False.
        locations (list, optional): A list of location identifiers (e.g., ["FR", "FL"]).
        use_longhorn_lib (bool, optional): Whether to depend on drivers/longhorn-lib. Defaults to False.
        enable_ota (bool, optional): Whether or not to use OTA flash drivers. Defaults to False.
        **kwargs: extra args to pass to cc_binary.
    """
    if usb_device_name == None:
        usb_device_name = name

    final_extra_srcs = extra_srcs[:]
    final_extra_deps = extra_deps[:]
    final_defines = defines[:]

    if enable_usb:
        final_extra_srcs.append("//drivers/stm32/{}:usb_device_srcs".format(family))
        final_extra_deps.append("//drivers/stm32/{}:usb_device_headers".format(family))
        final_defines.append('USB_DEVICE_NAME_TOKEN="ELC {}"'.format(usb_device_name))

    if enable_freertos:
        final_extra_srcs.append("//drivers/stm32/{}:freertos_srcs".format(family))
        final_extra_deps.append("//drivers/stm32/{}:freertos_headers".format(family))

    if enable_ota:
        final_extra_srcs.append("//drivers/ota:ota_flash_srcs")
        final_extra_deps.append("//drivers/ota:ota_flash_headers")

    if enable_dfu:
        final_defines.append("ENABLE_DFU")

    release_srcs = []

    if not locations:
        locations_to_build = [None]
    else:
        locations_to_build = locations

    for location in locations_to_build:
        target_name = name
        project_name = name
        location_defines = []

        if location:
            target_name = "{}_{}".format(name, location)
            project_name = "{}_{}".format(name, location)
            location_defines.append("BOARD_{}".format(location))

        deps_list = final_extra_deps + [
            "//drivers/stm32/{}:headers".format(family),
        ]
        if use_longhorn_lib:
            ll_version = "//drivers/longhorn-lib:longhorn_lib_{family}".format(family = family) if enable_freertos else "//drivers/longhorn-lib:longhorn_lib_base_{family}".format(family = family)
            deps_list.append(ll_version)

        cc_binary(
            name = "{}_project".format(target_name),
            srcs = native.glob([
                       "Core/Src/**/*.c",
                       "Core/Inc/**/*.h",
                       "Core/Src/**/*.cpp",
                       "Core/Inc/**/*.hpp",
                   ], allow_empty = True) +
                   [
                       "//drivers/stm32/{}:srcs".format(family),
                   ] + final_extra_srcs,
            includes = [
                "Core/Inc",
            ] + extra_includes,
            deps = deps_list,
            linkopts = MCU_FLAGS + [
                "-Wl,-Map={}.map,--cref".format(target_name),
                "-Wl,--gc-sections",
                "-T $(location {})".format(linker_script),
                "$(location {})".format(startup_script),
                "-specs=nano.specs",
                "-lnosys",
                "-lc",
                "-lm",
                "-lstdc++",
                "-u _printf_float",
            ],
            defines = final_defines + location_defines + ["USE_HAL_DRIVER"],
            additional_linker_inputs = [
                linker_script,
                startup_script,
            ],
            target_compatible_with = [
                "@platforms//cpu:arm",
                "@platforms//os:none",
            ],
            copts = MCU_FLAGS + [
                "-mthumb-interwork",
                "-ffunction-sections",
                "-fdata-sections",
                "-Og",
                "-g3",
            ],
            visibility = ["//visibility:private"],
            features = ["generate_linkmap"],
            tags = ["stm32_firmware"],
            **kwargs
        )

        native.filegroup(
            name = "{}.out.map".format(target_name),
            srcs = [":{}_project".format(target_name)],
            output_group = "linkmap",
            tags = ["stm32_firmware"],
        )

        platform_transition_filegroup(
            name = target_name,
            srcs = ["{}_project".format(target_name)],
            target_platform = "//:arm_none_eabi",
            visibility = ["//visibility:public"],
            tags = ["stm32_firmware"],
        )

        elf_out(
            name = project_name,
            src = target_name,
            visibility = ["//visibility:public"],
        )
        hex_out(
            name = project_name,
            src = target_name,
            visibility = ["//visibility:public"],
        )
        binary_out(
            name = project_name,
            src = target_name,
            visibility = ["//visibility:public"],
        )

        release_srcs.append("{}.elf".format(target_name))
        release_srcs.append("{}.bin".format(target_name))
        release_srcs.append("{}.hex".format(target_name))

        openocd_flash_target(
            name = "openocd_{}".format(location) if location else "openocd",
            elf = ":{}.elf".format(target_name),
            cfg = "//drivers/stm32/{}:openocd_cfg".format(family),
            tags = ["local", "flasher"],
        )

        dfu_flash_target(
            name = "dfu_{}".format(location) if location else "dfu",
            bin_file = ":{}.bin".format(target_name),
            tags = ["local", "flasher"],
        )

    native.filegroup(
        name = "release",
        srcs = release_srcs,
        visibility = ["//visibility:public"],
        tags = ["stm32_firmware"],
    )
