"""OpenOCD for Flashing"""

load("@rules_python//python:defs.bzl", "py_binary")

filegroup(
    name = "openocd_flashing_script",
    srcs = ["flash.py"],
    visibility = ["//visibility:public"],
)

py_binary(
    name = "flash",
    srcs = ["flash.py"],
    main = "flash.py",
    legacy_create_init = 0,
    visibility = ["//visibility:public"],
    deps = [
        "@lhre_pypi//bazel_runfiles",
    ],
)
