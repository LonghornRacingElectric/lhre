"""Macro for creating firmware projects, binary/hex outputs, and flash targets."""

load("@aspect_bazel_lib//lib:transitions.bzl", "platform_transition_filegroup")
load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("@rules_cc//cc:cc_library.bzl", "cc_library")
load("@rules_cc//cc:cc_test.bzl", "cc_test")

# ---------------------------------------------------------------------------
# Target platform per family. Each platform's CPU-core constraint selects the
# //toolchains variant with the family's -mcpu/-mfpu/-mfloat-abi baked in, so
# no target here needs MCU codegen flags. Add new families in //platforms and
# map them here.
# ---------------------------------------------------------------------------
FAMILY_PLATFORMS = {
    "stm32f0": "//platforms:stm32f0",
    "stm32f4": "//platforms:stm32f4",
    "stm32g4": "//platforms:stm32g4",
    "stm32h7": "//platforms:stm32h7",
}

# ---------------------------------------------------------------------------
# Helpers – runfiles path resolution
# ---------------------------------------------------------------------------

def _runfiles_path(f):
    if f.owner.workspace_name:
        prefix = "../" + f.owner.workspace_name + "/"
        if f.short_path.startswith(prefix):
            return f.owner.workspace_name + "/" + f.short_path[len(prefix):]
        return f.short_path
    else:
        return "_main/" + f.short_path

# ---------------------------------------------------------------------------
# Flash rules  (OpenOCD + DFU)
# ---------------------------------------------------------------------------

def _openocd_flash_impl(ctx):
    is_windows = ctx.target_platform_has_constraint(ctx.attr._windows_constraint[platform_common.ConstraintValueInfo])

    if is_windows:
        executable = ctx.actions.declare_file(ctx.label.name + ".cmd")
        content = """@echo off
set RUNFILES_DIR=%~dp0{exe_name}.runfiles
"%RUNFILES_DIR%\\{tool}" "{openocd}" "{elf}" "{cfg}" %*
""".format(
            exe_name = executable.basename,
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
set RUNFILES_DIR=%~dp0{exe_name}.runfiles
"%RUNFILES_DIR%\\{tool}" "{dfu_util}" "{bin_file}" %*
""".format(
            exe_name = executable.basename,
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

# ---------------------------------------------------------------------------
# Objcopy output helpers
# ---------------------------------------------------------------------------

def binary_out(name, src, visibility = None, **kwargs):
    """Runs objcopy to convert a source file (e.g., an ELF) into a raw binary.

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
    """Runs objcopy to convert a source file (e.g., an ELF) into a hex binary.

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
        tools = ["@arm_none_eabi//:objcopy"],
        visibility = visibility,
        **kwargs
    )

def elf_out(name, src, visibility = None, **kwargs):
    """Copies input elf to an elf output.

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
        visibility = visibility,
        **kwargs
    )

# ---------------------------------------------------------------------------
# Firmware binary — symbolic macro
#
# firmware_project (below) is deliberately split in two:
#
#   - firmware_project, a legacy macro, is the board-facing API. It runs in
#     the board's BUILD thread, so it can glob() the board's CubeMX output
#     and derive per-MCU defaults whose relative labels (linker script,
#     startup file) must resolve in the board package.
#   - _firmware_binary, a symbolic macro, declares the cc_binary and wires
#     in the driver targets. Labels written here are visibility-checked
#     against //tools/firmware — the macro's home — not the board package.
#     That's what lets //drivers/stm32/... and the //drivers/freertos
#     internals stay invisible to boards: app code adding a direct ST HAL
#     dep is a build error, while the macro's own wiring still resolves.
# ---------------------------------------------------------------------------

def _firmware_binary_impl(
        name,
        visibility,
        srcs,
        deps,
        defines,
        includes,
        family,
        linker_script,
        startup_script,
        driver_headers,
        driver_srcs,
        enable_freertos,
        enable_usb,
        enable_printf_float):
    if driver_headers == None:
        driver_headers = "//drivers/stm32/{}:headers".format(family)
    if driver_srcs == None:
        driver_srcs = "//drivers/stm32/{}:srcs".format(family)

    # The LHAL STM32 backend is part of the platform layer, so every firmware
    # binary compiles it (same pattern as the ST HAL sources: inside the
    # binary, where the board's hal_conf.h and device define apply). Sources
    # that need optional middleware guard themselves — usb_cdc.cpp compiles
    # to nothing unless usbd_cdc.h is on the include path — and with
    # enable_usb it takes over usbd_cdc_if.c's job (see the USB carve-out in
    # firmware_project below).
    final_srcs = list(srcs) + [
        driver_srcs,
        "//drivers/lhal:stm32_srcs",
    ]

    # Every firmware binary gets build provenance (git describe/SHA/dirty)
    # via the stamped header in //tools/firmware:build_info.
    final_deps = list(deps) + [
        driver_headers,
        "//drivers/lhal:stm32_headers",
        "//tools/firmware:build_info",
    ]

    if enable_freertos:
        final_srcs.append("//drivers/stm32/{}:freertos_srcs".format(family))
        final_deps.append("//drivers/stm32/{}:freertos_headers".format(family))

        # CubeMX contract, handled centrally so boards don't copy-paste it:
        # the SysTick→kernel forwarding handler, and a stub cmsis_os.h for
        # the #include in CubeMX-generated main.c (the CMSIS-RTOS2 wrapper
        # itself is never compiled — boards use the raw FreeRTOS API).
        final_srcs.append("//drivers/freertos:cubemx_glue")
        final_deps.append("//drivers/freertos:cmsis_os_stub")

    if enable_usb:
        final_srcs.append("//drivers/stm32/usb_device:srcs")
        final_deps.append("//drivers/stm32/usb_device:headers")

    # Linker flags that are always present. MCU codegen/multilib flags come
    # from the toolchain selected by the family's platform; optimization
    # level follows --compilation_mode (see //toolchains).
    linkopts = [
        "-Wl,-Map={}.map,--cref".format(name),
        "-Wl,--gc-sections",
        "-T $(location {})".format(linker_script),
        "$(location {})".format(startup_script),
        "-specs=nano.specs",
        "-lnosys",
        "-lc",
        "-lm",
        "-lstdc++",
    ]
    if enable_printf_float:
        linkopts.append("-u _printf_float")

    cc_binary(
        name = "{}_project".format(name),
        srcs = final_srcs,
        includes = includes,
        deps = final_deps,
        linkopts = linkopts,
        defines = list(defines) + ["USE_HAL_DRIVER"],
        additional_linker_inputs = [
            linker_script,
            startup_script,
        ],
        target_compatible_with = [
            "@platforms//cpu:arm",
            "@platforms//os:none",
        ],
        copts = [
            "-fdiagnostics-color",
            "-ffunction-sections",
            "-fdata-sections",
        ],
        features = ["generate_linkmap"],
        tags = ["stm32_firmware"],
    )

    native.filegroup(
        name = "{}.out.map".format(name),
        srcs = [":{}_project".format(name)],
        output_group = "linkmap",
        tags = ["stm32_firmware"],
        visibility = visibility,
    )

    platform_transition_filegroup(
        name = name,
        srcs = ["{}_project".format(name)],
        target_platform = FAMILY_PLATFORMS[family],
        visibility = visibility,
        tags = ["stm32_firmware"],
    )

_firmware_binary = macro(
    implementation = _firmware_binary_impl,
    attrs = {
        "srcs": attr.label_list(configurable = False, default = []),
        "deps": attr.label_list(configurable = False, default = []),
        "defines": attr.string_list(configurable = False, default = []),
        "includes": attr.string_list(configurable = False, default = []),
        "family": attr.string(configurable = False, mandatory = True),
        "linker_script": attr.label(configurable = False, mandatory = True),
        "startup_script": attr.label(configurable = False, mandatory = True),
        "driver_headers": attr.label(configurable = False),
        "driver_srcs": attr.label(configurable = False),
        "enable_freertos": attr.bool(configurable = False, default = False),
        "enable_usb": attr.bool(configurable = False, default = False),
        "enable_printf_float": attr.bool(configurable = False, default = False),
    },
    doc = "One linked firmware image: the cc_binary, its linkmap, and the " +
          "platform-transitioned filegroup. Boards call firmware_project.",
)

# ---------------------------------------------------------------------------
# Main macro
# ---------------------------------------------------------------------------

def firmware_project(
        name,
        mcu,
        srcs = [],
        defines = [],
        extra_deps = [],
        extra_includes = [],
        app_deps = [],
        enable_app = True,
        enable_tests = True,
        enable_sims = True,
        enable_freertos = False,
        enable_usb = False,
        enable_dfu = False,
        locations = [],
        enable_printf_float = False,
        linker_script = None,
        startup_script = None,
        driver_headers = None,
        driver_srcs = None):
    """Creates a firmware project for STM32 microcontrollers.

    The board package is expected to follow the CubeMX reference layout
    (boards/VCU is the worked example): the macro compiles Core/ (CubeMX's
    output) and Board/ (hand-written bring-up) itself, and derives the
    family, device define, linker script, and startup file from `mcu` by
    ST's naming convention. State a fact explicitly only when a board
    genuinely deviates.

    App/ is synthesized into targets by file-naming convention (skipped
    per-file when absent, per-category with enable_tests / enable_sims,
    wholesale with enable_app = False):

        App/**/*.cpp|hpp    → cc_library {name}_app (LHAL + raw FreeRTOS
                              API), linked into the firmware and built for
                              the host
        App/*_test.cpp      → one small cc_test each, named by file stem,
                              against {name}_app + the LHAL host fakes
        App/*_sim.cpp       → one host cc_binary each, named by file stem

    Args:
        name (string): name of the project
        mcu (string): the MCU device in ST's lowercase header spelling, e.g.
            "stm32g474xx". Everything coupled to the part is derived from it:
            family "stm32g4" (first 7 chars), device define "STM32G474xx",
            linker script "STM32G474XX_FLASH.ld", startup file
            "startup_stm32g474xx.s".
        srcs (list, optional): extra sources/headers beyond the Core/ and
            Board/ trees the macro already globs. Defaults to [].
        defines (list, optional): defines to pass to the compiler. Defaults to [].
        extra_deps (list, optional): extra dependencies to compile with
            (beyond the {name}_app library, which is wired in itself).
            Defaults to [].
        extra_includes (list, optional): extra include paths to compile with. Defaults to [].
        app_deps (list, optional): extra dependencies for the synthesized
            {name}_app library (host-buildable ones — the app rule is LHAL
            interfaces only, no ST HAL). Defaults to [].
        enable_app (bool, optional): Synthesize the App/ targets. Set False
            for a board that outgrows the convention and hand-writes its app
            library, tests, and sims. Defaults to True.
        enable_tests (bool, optional): Synthesize a cc_test per
            App/*_test.cpp. Set False to keep the app library but skip the
            host tests — the escape hatch for boards that don't maintain
            them (the files, if present, simply stop being compiled).
            Defaults to True.
        enable_sims (bool, optional): Synthesize a host cc_binary per
            App/*_sim.cpp. Same escape hatch as enable_tests, for the
            sims. Defaults to True.
        enable_freertos (bool, optional): Whether or not to use FreeRTOS. Defaults to False.
        enable_usb (bool, optional): Wire in USB CDC (virtual COM port): ST's USB Device
            middleware plus the board's CubeMX-generated USB_DEVICE/ files — except
            usbd_cdc_if.c, whose job lhal/stm32/usb_cdc.cpp takes over (see
            lhal::stm32::UsbCdc). Requires USB enabled in the board's .ioc so the
            USB_DEVICE/ directory exists. Defaults to False.
        enable_dfu (bool, optional): Whether or not to accept strings to go into DFU. Defaults to False.
        locations (list, optional): A list of location identifiers (e.g., ["FR", "FL"]).
        enable_printf_float (bool, optional): Whether to link -u _printf_float (adds ~10KB). Defaults to False.
        linker_script (path, optional): Override for the mcu-derived linker script.
        startup_script (path, optional): Override for the mcu-derived startup file.
        driver_headers (label, optional): Override for the driver headers target. Defaults to //drivers/stm32/{family}:headers.
        driver_srcs (label, optional): Override for the driver srcs target. Defaults to //drivers/stm32/{family}:srcs.
    """
    if mcu != mcu.lower() or not mcu.startswith("stm32") or len(mcu) < 11:
        fail("{}: mcu must be ST's lowercase device spelling, e.g. \"stm32g474xx\" (got \"{}\").".format(name, mcu))
    family = mcu[:7]
    if family not in FAMILY_PLATFORMS:
        fail("Unknown MCU family '{}'. Add a platform for it in //platforms and map it in FAMILY_PLATFORMS.".format(family))

    # ST's device define keeps the trailing die-suffix lowercase:
    # stm32g474xx → STM32G474xx, stm32f051x8 → STM32F051x8.
    mcu_define = mcu[:9].upper() + mcu[9:]
    if linker_script == None:
        linker_script = "{}_FLASH.ld".format(mcu.upper())
    if startup_script == None:
        startup_script = "startup_{}.s".format(mcu)

    # ------------------------------------------------------------------
    # App/ target synthesis. Every board's app plumbing is the same shape —
    # the app library, its FreeRTOS host/MCU split, one cc_test per test
    # file, one host binary per sim — so the macro derives it from the
    # App/ file names instead of boards restating it.
    # ------------------------------------------------------------------
    app_name = "{}_app".format(name)
    app_srcs = native.glob(
        ["App/**/*.cpp"],
        exclude = [
            "App/**/*_test.cpp",
            "App/**/*_sim.cpp",
        ],
        allow_empty = True,
    )
    generate_app = enable_app and app_srcs

    if generate_app:
        final_app_deps = ["//drivers/lhal"] + app_deps
        if enable_freertos:
            # The board's CubeMX-generated FreeRTOS config, exposed on its
            # own so app code can compile against FreeRTOS headers when
            # cross-compiling (the kernel requires FreeRTOSConfig.h on the
            # include path everywhere FreeRTOS.h is included, not just
            # where the kernel sources are compiled).
            cc_library(
                name = "{}_freertos_config".format(name),
                hdrs = ["Core/Inc/FreeRTOSConfig.h"],
                strip_include_prefix = "Core/Inc",
            )

            # FreeRTOS comes from the same kernel checkout on both sides —
            # Cortex-M port + board config when cross-compiling, simulator
            # port + host config otherwise — so task code runs unmodified
            # on the host.
            final_app_deps = final_app_deps + select({
                "@platforms//os:none": [
                    ":{}_freertos_config".format(name),
                    "//drivers/stm32/{}:freertos_headers".format(family),
                ],
                "//conditions:default": ["//drivers/freertos:host"],
            })

        # Application logic: LHAL interfaces + raw FreeRTOS API, no ST HAL
        # (a HAL dep here is a visibility error by design). Compiles for
        # both the MCU (linked into the firmware below) and the host (the
        # tests and sims).
        cc_library(
            name = app_name,
            srcs = app_srcs,
            hdrs = native.glob(["App/**/*.hpp"], allow_empty = True),
            includes = ["App"],
            deps = final_app_deps,
        )

        for test_src in native.glob(["App/*_test.cpp"], allow_empty = True) if enable_tests else []:
            cc_test(
                name = test_src[len("App/"):-len(".cpp")],
                size = "small",
                srcs = [test_src],
                deps = [
                    ":" + app_name,
                    "//drivers/lhal:host",
                    "@googletest//:gtest_main",
                ],
            )

        for sim_src in native.glob(["App/*_sim.cpp"], allow_empty = True) if enable_sims else []:
            cc_binary(
                name = sim_src[len("App/"):-len(".cpp")],
                srcs = [sim_src],
                deps = [
                    ":" + app_name,
                    "//drivers/lhal:host",
                ],
            )

    binary_deps = extra_deps + ([":" + app_name] if generate_app else [])

    # CubeMX contract, handled centrally so boards don't copy-paste it:
    # Core/ is wholly CubeMX-generated, so the macro owns compiling it.
    # With FreeRTOS, app_freertos.c is excluded — it's CubeMX's CMSIS-RTOS2
    # glue (defaultTask and friends) and boards use the raw FreeRTOS API
    # instead (see drivers/freertos/README.md). Board/ holds the
    # hand-written bring-up code (boards/README.md has the ownership split).
    board_srcs = native.glob(
        [
            "Core/Src/**/*.c",
            "Board/**/*.cpp",
            "Board/**/*.hpp",
        ],
        exclude = ["Core/Src/app_freertos.c"] if enable_freertos else [],
        allow_empty = True,
    ) + native.glob(
        [
            "Core/Inc/**/*.h",
            "Core/Inc/**/*.hpp",
        ],
        allow_empty = True,
    ) + srcs

    final_defines = defines + [mcu_define]

    usb_includes = []
    if enable_usb:
        # CubeMX contract, handled centrally: compile the generated USB_DEVICE
        # glue (usb_device.c, usbd_conf.c, usbd_desc.c) and ST's middleware,
        # but NOT usbd_cdc_if.c — its only content is the CDC interface
        # struct, which lhal/stm32/usb_cdc.cpp defines instead so reception
        # routes into lhal::stm32::UsbCdc.
        # CubeMX has generated both spellings of the directory over the
        # years. Try them one at a time — a single glob with both patterns
        # would match the same files twice on case-insensitive filesystems
        # (macOS) and double-compile them.
        usb_glue = []
        usb_dir = None
        for candidate in ["USB_Device", "USB_DEVICE"]:
            usb_glue = native.glob(
                [
                    candidate + "/**/*.c",
                    candidate + "/**/*.h",
                ],
                exclude = [candidate + "/App/usbd_cdc_if.c"],
                allow_empty = True,
            )
            if usb_glue:
                usb_dir = candidate
                break
        if not usb_glue:
            fail(("{}: enable_usb = True but no USB_Device/ directory in this " +
                  "package. Enable USB_Device (CDC) in the board's .ioc and " +
                  "regenerate with CubeMX first.").format(name))
        board_srcs = board_srcs + usb_glue
        usb_includes = [
            usb_dir + "/App",
            usb_dir + "/Target",
        ]

    if enable_dfu:
        final_defines = final_defines + ["ENABLE_DFU"]

    release_srcs = []

    if not locations:
        locations_to_build = [None]
    else:
        locations_to_build = locations

    for location in locations_to_build:
        target_name = name
        location_defines = []

        if location:
            target_name = "{}_{}".format(name, location)
            location_defines.append("BOARD_{}".format(location))

        _firmware_binary(
            name = target_name,
            srcs = board_srcs,
            deps = binary_deps,
            defines = final_defines + location_defines,
            includes = ["Core/Inc"] + usb_includes + extra_includes,
            family = family,
            linker_script = linker_script,
            startup_script = startup_script,
            driver_headers = driver_headers,
            driver_srcs = driver_srcs,
            enable_freertos = enable_freertos,
            enable_usb = enable_usb,
            enable_printf_float = enable_printf_float,
            visibility = ["//visibility:public"],
        )

        elf_out(
            name = target_name,
            src = target_name,
            visibility = ["//visibility:public"],
        )
        hex_out(
            name = target_name,
            src = target_name,
            visibility = ["//visibility:public"],
        )
        binary_out(
            name = target_name,
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
