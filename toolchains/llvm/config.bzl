"""cc_toolchain config for bare-metal ARM firmware built with hermetic clang.

Clang analog of @toolchains_arm_gnu//toolchain:config.bzl, encoding what ST's
starm-clang toolchain does implicitly (see boards/VCU/cmake/clang-vanilla.cmake
for the deconstruction this is based on): clang compiles with an explicit
--target triple, and newlib-nano / libgcc / libstdc++ from the hermetic
arm-none-eabi GCC repos are linked in — clang's multilib resolution can't map
GCC's directory layout, so the -L paths and the libc/libgcc/libstdc++ archives
are spelled out here instead of coming from -specs=nano.specs.

Differences from the GCC config this mirrors:
  - tools are looked up by exact basename (clang, ld.lld, llvm-ar, ...)
  - the clang builtin resource dir is passed explicitly (-resource-dir) so
    header search is independent of how Bazel materializes the clang symlink
  - GCC's builtin include dirs (lib/gcc/...) are excluded from header search;
    clang's resource headers replace them
  - the C++ multilib config header dir (c++/<ver>/arm-none-eabi/<multilib>)
    is injected, which the GCC driver derives from -mcpu internally
  - crti.o/crtn.o are linked explicitly (they provide _init/_fini for
    newlib's __libc_init_array; the GCC driver injects them via startfiles)
"""

load("@bazel_skylib//rules/directory:providers.bzl", "DirectoryInfo")
load("@bazel_tools//tools/build_defs/cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:cc_toolchain_config_lib.bzl", "action_config", "feature", "flag_group", "flag_set")
load("@rules_cc//cc:defs.bzl", "CcToolchainConfigInfo")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")

def _directory_files_impl(ctx):
    return [DefaultInfo(files = ctx.attr.directory[DirectoryInfo].transitive_files)]

# Expands a skylib DirectoryInfo target (e.g. the clang builtin_resource_dir)
# into its individual files. Actions must declare the headers file-by-file:
# a source-directory artifact input doesn't satisfy Bazel's .d-file
# ("undeclared inclusion") validation.
directory_files = rule(
    implementation = _directory_files_impl,
    attrs = {
        "directory": attr.label(mandatory = True, providers = [DirectoryInfo]),
    },
)

_all_compile_actions = [
    ACTION_NAMES.assemble,
    ACTION_NAMES.preprocess_assemble,
    ACTION_NAMES.linkstamp_compile,
    ACTION_NAMES.c_compile,
    ACTION_NAMES.cpp_compile,
    ACTION_NAMES.cpp_header_parsing,
    ACTION_NAMES.cpp_module_compile,
    ACTION_NAMES.cpp_module_codegen,
    ACTION_NAMES.lto_backend,
    ACTION_NAMES.clif_match,
]

_cpp_compile_actions = [
    ACTION_NAMES.cpp_compile,
    ACTION_NAMES.cpp_header_parsing,
    ACTION_NAMES.cpp_module_compile,
    ACTION_NAMES.cpp_module_codegen,
]

_link_actions = [
    ACTION_NAMES.cpp_link_executable,
    ACTION_NAMES.cpp_link_dynamic_library,
    ACTION_NAMES.cpp_link_nodeps_dynamic_library,
]

def _tool(ctx, attr_name):
    files = getattr(ctx.files, attr_name)
    if len(files) != 1:
        fail("expected exactly one file for {}, got {}".format(attr_name, files))
    return files[0]

def _action_configs(tool_file, action_names, implies = []):
    return [
        action_config(
            action_name = action_name,
            tools = [struct(type_name = "tool", tool = tool_file)],
            implies = implies,
        )
        for action_name in action_names
    ]

def _find_dir(files, suffix, what):
    for f in files:
        if f.path.endswith(suffix):
            return f.path
    fail("could not find a directory ending in '{}' among {} ({})".format(
        suffix,
        [f.path for f in files],
        what,
    ))

def _flag_groups_if_not_empty(flags):
    if not flags:
        return []
    return [flag_group(flags = flags)]

def _impl(ctx):
    clang = _tool(ctx, "clang")
    resource_dir = ctx.attr.resource_dir[DirectoryInfo]

    action_configs = (
        _action_configs(clang, [
            ACTION_NAMES.assemble,
            ACTION_NAMES.preprocess_assemble,
            ACTION_NAMES.c_compile,
            ACTION_NAMES.cc_flags_make_variable,
            ACTION_NAMES.cpp_compile,
            ACTION_NAMES.cpp_header_parsing,
        ]) +
        _action_configs(clang, _link_actions, implies = ["linker_param_file"]) +
        _action_configs(_tool(ctx, "ar"), [ACTION_NAMES.cpp_link_static_library], implies = [
            "archiver_flags",
            "linker_param_file",
        ]) +
        _action_configs(_tool(ctx, "objcopy"), [ACTION_NAMES.objcopy_embed_data]) +
        _action_configs(_tool(ctx, "strip"), [ACTION_NAMES.strip])
    )

    # ---- header search -----------------------------------------------------
    # C search order mirrors clang's own default (resource headers before the
    # libc so clang's stdint.h can include_next newlib's); C++ gets the
    # libstdc++ dirs in GCC's internal order, multilib config dir first.
    gnu_includes = ctx.files.gnu_include_path
    cxx_root = _find_dir(gnu_includes, "/c++/" + ctx.attr.gcc_version, "libstdc++ include root")
    newlib_include = _find_dir(gnu_includes, "arm-none-eabi/include", "newlib include dir")

    cxx_isystem = [
        cxx_root,
        cxx_root + "/arm-none-eabi/" + ctx.attr.multilib_dir,
        cxx_root + "/arm-none-eabi",
        cxx_root + "/backward",
    ]
    c_isystem = [
        resource_dir.path + "/include",
        newlib_include,
    ]

    # ---- library search ----------------------------------------------------
    gnu_libs = ctx.files.gnu_library_path
    newlib_lib = _find_dir(gnu_libs, "arm-none-eabi/lib", "newlib lib root") + "/" + ctx.attr.multilib_dir
    libgcc_lib = _find_dir(gnu_libs, "lib/gcc/arm-none-eabi/" + ctx.attr.gcc_version, "libgcc root") + "/" + ctx.attr.multilib_dir

    toolchain_compiler_flags = feature(
        name = "compiler_flags",
        enabled = True,
        flag_sets = [
            flag_set(
                actions = _cpp_compile_actions,
                flag_groups = [
                    flag_group(flags = ["-nostdinc++"] + [f for d in cxx_isystem for f in ["-isystem", d]]),
                ],
            ),
            flag_set(
                actions = _all_compile_actions,
                flag_groups = [
                    flag_group(flags = [
                        "-nostdinc",
                        "-resource-dir=" + resource_dir.path,
                    ] + [f for d in c_isystem for f in ["-isystem", d]]),
                    flag_group(flags = ctx.attr.copts + ["-no-canonical-prefixes"]),
                ],
            ),
        ],
    )

    cxx_flags = feature(
        name = "cxx_flags",
        enabled = True,
        flag_sets = [
            flag_set(
                actions = _cpp_compile_actions,
                flag_groups = _flag_groups_if_not_empty(ctx.attr.cxxopts),
            ),
        ],
    )

    conly_flags = feature(
        name = "conly_flags",
        enabled = True,
        flag_sets = [
            flag_set(
                actions = [ACTION_NAMES.c_compile],
                flag_groups = _flag_groups_if_not_empty(ctx.attr.conlyopts),
            ),
        ],
    )

    custom_linkopts = feature(
        name = "custom_linkopts",
        enabled = True,
        flag_sets = [
            flag_set(
                actions = [ACTION_NAMES.cpp_link_executable],
                flag_groups = [
                    flag_group(flags = ctx.attr.linkopts + [
                        "-fuse-ld=lld",
                        # No GNU-driver startfiles/default libs: entry is the
                        # startup script's Reset_Handler, and the libc/libgcc
                        # set is explicit (firmware_project appends the -l
                        # flags after the object files, where they resolve).
                        "-nostartfiles",
                        "-nodefaultlibs",
                        "-L" + newlib_lib,
                        "-L" + libgcc_lib,
                        libgcc_lib + "/crti.o",
                        libgcc_lib + "/crtn.o",
                        "-no-canonical-prefixes",
                    ]),
                ],
            ),
        ],
    )

    dbg_feature = feature(
        name = "dbg",
        flag_sets = [
            flag_set(
                actions = _all_compile_actions,
                flag_groups = _flag_groups_if_not_empty(ctx.attr.dbg_compile_flags),
            ),
        ],
        provides = ["compilation_mode"],
    )

    opt_feature = feature(
        name = "opt",
        flag_sets = [
            flag_set(
                actions = _all_compile_actions,
                flag_groups = _flag_groups_if_not_empty(ctx.attr.opt_compile_flags),
            ),
            flag_set(
                actions = [ACTION_NAMES.cpp_link_executable],
                flag_groups = _flag_groups_if_not_empty(ctx.attr.opt_link_flags),
            ),
        ],
        provides = ["compilation_mode"],
    )

    fastbuild_feature = feature(
        name = "fastbuild",
        flag_sets = [flag_set(actions = _all_compile_actions, flag_groups = [])],
        provides = ["compilation_mode"],
    )

    linker_param_file_feature = feature(
        name = "linker_param_file",
        flag_sets = [
            flag_set(
                actions = _link_actions + [ACTION_NAMES.cpp_link_static_library],
                flag_groups = [
                    flag_group(
                        flags = ["@%{linker_param_file}"],
                        expand_if_available = "linker_param_file",
                    ),
                ],
            ),
        ],
    )

    compiler_param_file_feature = feature(
        name = "compiler_param_file",
        flag_sets = [
            flag_set(
                actions = _all_compile_actions,
                flag_groups = [
                    flag_group(
                        flags = ["@%{compiler_param_file}"],
                        expand_if_available = "compiler_param_file",
                    ),
                ],
            ),
        ],
    )

    generate_linkmap_feature = feature(
        name = "generate_linkmap",
        flag_sets = [
            flag_set(
                actions = [ACTION_NAMES.cpp_link_executable],
                flag_groups = [
                    flag_group(
                        flags = ["-Wl,-Map=%{output_execpath}.map"],
                        expand_if_available = "output_execpath",
                    ),
                ],
            ),
        ],
    )

    return cc_common.create_cc_toolchain_config_info(
        ctx = ctx,
        toolchain_identifier = ctx.attr.toolchain_identifier,
        host_system_name = ctx.attr.host_system_name,
        target_system_name = "arm-none-eabi",
        target_cpu = "arm-none-eabi",
        target_libc = "newlib",
        compiler = "clang",
        abi_version = "eabi",
        abi_libc_version = ctx.attr.gcc_version,
        action_configs = action_configs,
        cxx_builtin_include_directories = cxx_isystem + c_isystem,
        features = [
            linker_param_file_feature,
            compiler_param_file_feature,
            dbg_feature,
            opt_feature,
            fastbuild_feature,
            generate_linkmap_feature,
            toolchain_compiler_flags,
            cxx_flags,
            conly_flags,
            custom_linkopts,
        ],
    )

cc_llvm_arm_toolchain_config = rule(
    implementation = _impl,
    attrs = {
        "toolchain_identifier": attr.string(mandatory = True),
        "host_system_name": attr.string(mandatory = True),
        "clang": attr.label(mandatory = True, allow_files = True),
        "ar": attr.label(mandatory = True, allow_files = True),
        "objcopy": attr.label(mandatory = True, allow_files = True),
        "strip": attr.label(mandatory = True, allow_files = True),
        "resource_dir": attr.label(mandatory = True, providers = [DirectoryInfo]),
        "gnu_include_path": attr.label_list(mandatory = True, allow_files = True),
        "gnu_library_path": attr.label_list(mandatory = True, allow_files = True),
        "gcc_version": attr.string(mandatory = True),
        "multilib_dir": attr.string(mandatory = True),
        "copts": attr.string_list(default = []),
        "cxxopts": attr.string_list(default = []),
        "conlyopts": attr.string_list(default = []),
        "linkopts": attr.string_list(default = []),
        "dbg_compile_flags": attr.string_list(default = ["-ggdb", "-Og"]),
        "opt_compile_flags": attr.string_list(default = ["-DNDEBUG", "-O2"]),
        "opt_link_flags": attr.string_list(default = ["-Wl,-S"]),
    },
    provides = [CcToolchainConfigInfo],
)
