"""DFU Util Toolchain for Flashing"""

load("@aspect_rules_py//py:defs.bzl", "py_binary")

filegroup(
    name = "dfu_flashing_script",
    srcs = ["flash.py"],
    visibility = ["//visibility:public"],
)

py_binary(
    name = "flash",
    srcs = ["flash.py"],
    main = "flash.py",
    visibility = ["//visibility:public"],
    deps = [
        "@pypi//bazel_runfiles",
        "@pypi//pyserial",
    ],
)
