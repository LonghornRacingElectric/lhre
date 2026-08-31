"""Hermetic prebuilt protoc as a plain executable, one repo per exec platform.

proto_library rules get protoc through protobuf's own prebuilt toolchain
(docs/build-system.md, "Protobuf without compiling protobuf"). This exists
for tools that need protoc as an executable *path* rather than a toolchain:
the prost codegen build script in apps/BEVO. Same pattern as tools/dfu and
tools/openocd.

Keep _TAG in lockstep with the protobuf bazel_dep in MODULE.bazel when
bumping, and refresh _SHAS from the release artifacts (the GitHub release
API reports each asset's sha256 digest).
"""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

_TAG = "v35.1"

_ARTIFACT_VERSION = "35.1"

_SHAS = {
    "linux-aarch_64": "01bf9d08808c7f96678b63f4bd8efa559bb4f83d5a7a270d5edaf507f9d5d9cf",
    "linux-x86_64": "6930ebf62bd4ea607b98fff052596c6ee564b9835b4ce172c75a3f53ae9d91b7",
    "osx-aarch_64": "193289af0470c6a1aada357d4fba0bbf8d78bfaac8b5e42ca30af2ef75583de2",
    "osx-x86_64": "537d73604a344ded6fc94e98e07e529d4fe3e4a0b09e59905353950fafc2a1f7",
    "win64": "5d3ff218d7d91eea95f7569bcb5a98f3030f8996d44151279d9772edcff76082",
}

_BUILD = """\
package(default_visibility = ["//visibility:public"])

filegroup(
    name = "protoc",
    srcs = ["bin/protoc{exe}"],
)
"""

def _protoc_repos_impl(ctx):
    # One repo per platform; //tools/protoc select()s between them per exec
    # platform, and Bazel only fetches the branch the select resolves to.
    for platform, sha256 in _SHAS.items():
        exe = ".exe" if platform.startswith("win") else ""
        http_archive(
            name = "protoc_" + platform.replace("-", "_"),
            build_file_content = _BUILD.format(exe = exe),
            sha256 = sha256,
            urls = [
                "https://github.com/protocolbuffers/protobuf/releases/download/{}/protoc-{}-{}.zip".format(
                    _TAG,
                    _ARTIFACT_VERSION,
                    platform,
                ),
            ],
        )
    return ctx.extension_metadata(reproducible = True)

protoc = module_extension(
    implementation = _protoc_repos_impl,
)
