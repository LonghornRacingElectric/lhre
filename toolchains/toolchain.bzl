"""arm-none-eabi toolchain variants with per-CPU-core flags baked in.

Local mirror of arm_none_eabi_toolchain from
@toolchains_arm_gnu//toolchain:toolchain.bzl with one difference: the
generated cc_toolchain/config targets are tagged "manual". Upstream's macro
can't take tags, and without them `bazel build //...` matches the
cc_toolchains for all five host OS/arch combinations and fetching their file
groups downloads every host's gcc archive (~150 MB each). Toolchain
resolution ignores tags — the toolchain() declarations below stay visible to
register_toolchains, and only the variant actually selected for a build
fetches its host repo.
"""

load("@rules_cc//cc:defs.bzl", "cc_toolchain")
load("@toolchains_arm_gnu//toolchain:config.bzl", "cc_arm_gnu_toolchain_config")

# Host OS/arch → exec constraints, mirroring hosts["arm-none-eabi"] in the
# fork's toolchain.bzl. The @arm_none_eabi_<host> repos are brought into
# scope by use_repo in MODULE.bazel.
_HOSTS = {
    "darwin_arm64": ["@platforms//os:macos", "@platforms//cpu:arm64"],
    "darwin_x86_64": ["@platforms//os:macos", "@platforms//cpu:x86_64"],
    "linux_aarch64": ["@platforms//os:linux", "@platforms//cpu:arm64"],
    "linux_x86_64": ["@platforms//os:linux", "@platforms//cpu:x86_64"],
    "windows_x86_64": ["@platforms//os:windows", "@platforms//cpu:x86_64"],
}

def arm_none_eabi_toolchain(
        name,
        version,
        target_compatible_with,
        copts = [],
        linkopts = []):
    """Declares an arm-none-eabi (config, cc_toolchain, toolchain) per host.

    Args:
        name: toolchain variant name, e.g. "cortex_m4f".
        version: gcc version of the @arm_none_eabi_<host> repos.
        target_compatible_with: constraints a target platform must satisfy
            for this variant to be selected.
        copts: compiler flags baked into every compile action.
        linkopts: linker flags baked into every link action.
    """
    for host, exec_compatible_with in _HOSTS.items():
        repo = "arm_none_eabi_{}".format(host)
        fix_linkopts = []

        # macOS on apple rejects the relative path LTO plugin
        if version == "13.2.1" and "darwin" in host:
            fix_linkopts.append("-fno-lto")

        cc_arm_gnu_toolchain_config(
            name = "config_{}_{}".format(host, name),
            gcc_repo = repo,
            gcc_version = version,
            abi_version = "eabi",
            host_system_name = host,
            toolchain_prefix = "arm-none-eabi",
            toolchain_identifier = "{}_{}".format(repo, name),
            toolchain_bins = "@{}//:compiler_components".format(repo),
            include_path = ["@{}//:include_path".format(repo)],
            library_path = ["@{}//:library_path".format(repo)],
            copts = copts,
            linkopts = linkopts + fix_linkopts,
            tags = ["manual"],
        )

        cc_toolchain(
            name = "cc_toolchain_{}_{}".format(host, name),
            all_files = "@{}//:compiler_pieces".format(repo),
            ar_files = "@{}//:ar_files".format(repo),
            as_files = "@{}//:as_files".format(repo),
            compiler_files = "@{}//:compiler_files".format(repo),
            dwp_files = ":empty",
            linker_files = "@{}//:linker_files".format(repo),
            objcopy_files = "@{}//:objcopy".format(repo),
            strip_files = "@{}//:strip".format(repo),
            supports_param_files = 1,
            toolchain_config = ":config_{}_{}".format(host, name),
            toolchain_identifier = "{}_{}".format(repo, name),
            tags = ["manual"],
        )

        native.toolchain(
            name = "{}_{}".format(name, host),
            exec_compatible_with = exec_compatible_with,
            target_compatible_with = target_compatible_with,
            toolchain = ":cc_toolchain_{}_{}".format(host, name),
            toolchain_type = "@bazel_tools//tools/cpp:toolchain_type",
        )
