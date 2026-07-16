"""Hermetic OpenOCD Toolchain."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

def _openocd_repo_impl(ctx):
    # This implementation is now much simpler because we delegate the work.
    # We select the correct URL and SHA256 based on the OS and architecture.
    os_name = ctx.os.name
    arch = ctx.os.arch

    if os_name.startswith("linux"):
        sha256 = "7e762af5f9a4f21ac03130ddce36acf996f3e67c4ee1526ed3d4f8ad89e65c77"
        url = "https://github.com/xpack-dev-tools/openocd-xpack/releases/download/v0.12.0-6/xpack-openocd-0.12.0-6-linux-x64.tar.gz"
        bin_path = "bin/openocd"
    elif os_name.startswith("mac os x"):
        if arch == "aarch64": # Apple Silicon
            sha256 = "4f458dea82af3529d4dc71544abfd8c8c96ebe6c521868f87c6697b0866c9e99"
            url = "https://github.com/xpack-dev-tools/openocd-xpack/releases/download/v0.12.0-6/xpack-openocd-0.12.0-6-darwin-arm64.tar.gz"
        else: # Intel
            sha256 = "aa5052790b003f18e64691a3b8b7a650d4ce54f72fbbb23f1aed2d26a5795b76"
            url = "https://github.com/xpack-dev-tools/openocd-xpack/releases/download/v0.12.0-6/xpack-openocd-0.12.0-6-darwin-x64.tar.gz"
        bin_path = "bin/openocd"
    elif os_name.startswith("windows"):
        sha256 = "c57724f87219bafde78f61b54fb7f303f456fed9826a3d0ee2d54d461ad58020"
        url = "https://github.com/xpack-dev-tools/openocd-xpack/releases/download/v0.12.0-6/xpack-openocd-0.12.0-6-win32-x64.zip"
        bin_path = "bin/openocd.exe"
    else:
        fail("Unsupported OS: {}".format(os_name))

    build_file_content = """
package(default_visibility = ["//visibility:public"])

filegroup(
    name = "openocd",
    srcs = ["{bin_path}"],
)

filegroup(
    name = "scripts",
    srcs = glob(["openocd/scripts/**/*"]),
)
""".format(bin_path = bin_path)

    http_archive(
        name = "openocd",
        sha256 = sha256,
        strip_prefix = "xpack-openocd-0.12.0-6",
        build_file_content = build_file_content,
        urls = [url],
    )

openocd = module_extension(
    implementation = _openocd_repo_impl,
)