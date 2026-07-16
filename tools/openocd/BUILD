"""OpenOCD for Flashing"""

load("@aspect_rules_py//py:defs.bzl", "py_binary")

filegroup(
    name = "openocd_flashing_script",
    srcs = ["flash.py"],
    visibility = ["//visibility:public"],
)

py_binary(
    name = "flash",
    srcs = ["flash.py"],
    main = "flash.py",
    python_version = "3.12",
    visibility = ["//visibility:public"],
    deps = [
        "@pypi//bazel_runfiles",
    ],
)
