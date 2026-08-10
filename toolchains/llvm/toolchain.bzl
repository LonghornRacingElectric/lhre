"""LLVM arm-none-eabi toolchain variants, one per CPU core, per host.

Same shape as //toolchains:toolchain.bzl (the GCC variants): each call
instantiates a (config, cc_toolchain, toolchain) triple per host OS/arch.
Compilation uses the hermetic clang prebuilts shared with @llvm's host
toolchains; newlib-nano, libgcc, and libstdc++ come from the same
@arm_none_eabi_<host> repos the GCC variants use, so the two firmware
toolchains link byte-identical C library code and differ only in compiler.

Every toolchain() here carries target_settings on
//toolchains:llvm_firmware, so these variants are only eligible when
--//toolchains:firmware_compiler=llvm is set — GCC stays the default.
"""

load("@rules_cc//cc:defs.bzl", "cc_toolchain")
load("//toolchains/llvm:config.bzl", "cc_llvm_arm_toolchain_config", "directory_files")

# Host OS/arch → (exec constraints, hermetic clang repo, binary suffix).
# Repo apparent names are mapped in MODULE.bazel from @llvm's prebuilt
# extension; the @arm_none_eabi_<host> repos follow //toolchains:_HOSTS.
_HOSTS = {
    "darwin_arm64": (["@platforms//os:macos", "@platforms//cpu:arm64"], "llvm_minimal_darwin_arm64", ""),
    "darwin_x86_64": (["@platforms//os:macos", "@platforms//cpu:x86_64"], "llvm_minimal_darwin_x86_64", ""),
    "linux_aarch64": (["@platforms//os:linux", "@platforms//cpu:arm64"], "llvm_minimal_linux_aarch64", ""),
    "linux_x86_64": (["@platforms//os:linux", "@platforms//cpu:x86_64"], "llvm_minimal_linux_x86_64", ""),
    "windows_x86_64": (["@platforms//os:windows", "@platforms//cpu:x86_64"], "llvm_minimal_windows_x86_64", ".exe"),
}

ARM_GNU_GCC_VERSION = "13.2.1"

def llvm_arm_none_eabi_toolchain(
        name,
        multilib_dir,
        target_compatible_with,
        copts = [],
        linkopts = []):
    """Declares an LLVM arm-none-eabi (config, cc_toolchain, toolchain) per host.

    Args:
        name: toolchain variant name, e.g. "cortex_m4f".
        multilib_dir: the GNU multilib directory the variant's codegen flags
            select, e.g. "thumb/v7e-m+fp/hard" (arm-none-eabi-gcc <flags>
            -print-multi-directory). Clang cannot derive this from -mcpu.
        target_compatible_with: constraints a target platform must satisfy
            for this variant to be selected.
        copts: compiler flags baked into every compile action.
        linkopts: linker flags baked into every link action.
    """
    for host, (exec_compatible_with, llvm_repo, exe) in _HOSTS.items():
        gnu_repo = "arm_none_eabi_{}".format(host)

        cc_llvm_arm_toolchain_config(
            name = "config_{}_{}".format(host, name),
            toolchain_identifier = "llvm_{}_{}".format(host, name),
            host_system_name = host,
            clang = "@{}//:bin/clang{}".format(llvm_repo, exe),
            ar = "@{}//:bin/llvm-ar{}".format(llvm_repo, exe),
            objcopy = "@{}//:bin/llvm-objcopy{}".format(llvm_repo, exe),
            strip = "@{}//:bin/llvm-strip{}".format(llvm_repo, exe),
            resource_dir = "@{}//:builtin_resource_dir".format(llvm_repo),
            gnu_include_path = ["@{}//:include_path".format(gnu_repo)],
            gnu_library_path = ["@{}//:library_path".format(gnu_repo)],
            gcc_version = ARM_GNU_GCC_VERSION,
            multilib_dir = multilib_dir,
            copts = ["--target=arm-none-eabi"] + copts,
            linkopts = ["--target=arm-none-eabi"] + linkopts,
            tags = ["manual"],
        )

        # clang/ld.lld are symlinks to clang-22/lld in the prebuilt archive;
        # ship both ends so the driver resolves inside the sandbox. GNU-side
        # inputs use :compiler_pieces (a flat file glob) — the :include_path /
        # :library_path directory artifacts overlap each other (nested dirs),
        # which the sandbox rejects; they are only used as path anchors in the
        # toolchain config, never as action inputs.
        directory_files(
            name = "resource_headers_{}_{}".format(host, name),
            directory = "@{}//:builtin_resource_dir".format(llvm_repo),
            tags = ["manual"],
        )

        native.filegroup(
            name = "compiler_files_{}_{}".format(host, name),
            srcs = [
                "@{}//:bin/clang{}".format(llvm_repo, exe),
                "@{}//:bin/clang-22{}".format(llvm_repo, exe),
                ":resource_headers_{}_{}".format(host, name),
                "@{}//:compiler_pieces".format(gnu_repo),
            ],
            tags = ["manual"],
        )

        native.filegroup(
            name = "linker_files_{}_{}".format(host, name),
            srcs = [
                "@{}//:bin/clang{}".format(llvm_repo, exe),
                "@{}//:bin/clang-22{}".format(llvm_repo, exe),
                "@{}//:bin/ld.lld{}".format(llvm_repo, exe),
                "@{}//:bin/lld{}".format(llvm_repo, exe),
                "@{}//:compiler_pieces".format(gnu_repo),
            ],
            tags = ["manual"],
        )

        native.filegroup(
            name = "ar_files_{}_{}".format(host, name),
            srcs = ["@{}//:bin/llvm-ar{}".format(llvm_repo, exe)],
            tags = ["manual"],
        )

        native.filegroup(
            name = "all_files_{}_{}".format(host, name),
            srcs = [
                ":compiler_files_{}_{}".format(host, name),
                ":linker_files_{}_{}".format(host, name),
                ":ar_files_{}_{}".format(host, name),
                "@{}//:bin/llvm-objcopy{}".format(llvm_repo, exe),
                "@{}//:bin/llvm-strip{}".format(llvm_repo, exe),
            ],
            tags = ["manual"],
        )

        cc_toolchain(
            name = "cc_toolchain_{}_{}".format(host, name),
            all_files = ":all_files_{}_{}".format(host, name),
            ar_files = ":ar_files_{}_{}".format(host, name),
            as_files = ":compiler_files_{}_{}".format(host, name),
            compiler_files = ":compiler_files_{}_{}".format(host, name),
            dwp_files = ":empty",
            linker_files = ":linker_files_{}_{}".format(host, name),
            objcopy_files = "@{}//:bin/llvm-objcopy{}".format(llvm_repo, exe),
            strip_files = "@{}//:bin/llvm-strip{}".format(llvm_repo, exe),
            supports_param_files = 1,
            toolchain_config = ":config_{}_{}".format(host, name),
            toolchain_identifier = "llvm_{}_{}".format(host, name),
            tags = ["manual"],
        )

        native.toolchain(
            name = "{}_{}".format(name, host),
            exec_compatible_with = exec_compatible_with,
            target_compatible_with = target_compatible_with,
            target_settings = ["//toolchains:llvm_firmware"],
            toolchain = ":cc_toolchain_{}_{}".format(host, name),
            toolchain_type = "@bazel_tools//tools/cpp:toolchain_type",
        )
