"""Module extension to fetch ST's USB Device middleware from GitHub."""

load("@bazel_tools//tools/build_defs/repo:git.bzl", "git_repository")

def _usb_device_deps_impl(ctx):
    git_repository(
        name = "stm32_usb_device_library",
        remote = "https://github.com/STMicroelectronics/stm32-mw-usb-device.git",
        # v2.11.6
        commit = "2df324bd60d4b0bb27404fd70b1c089b467f0e09",
        build_file = "//drivers/stm32/usb_device:usb_device.BUILD",
    )

    # Commit-pinned, so keep it out of MODULE.bazel.lock (lock churn).
    return ctx.extension_metadata(reproducible = True)

usb_device_deps = module_extension(
    implementation = _usb_device_deps_impl,
)
