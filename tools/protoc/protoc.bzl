"""Hermetic prebuilt protoc, one repo per exec platform.

Why not protobuf's own prebuilt-protoc machinery: in every protobuf version
currently on the BCR it is broken — 36.0-rc1 pins stale artifact hashes
(the release zips were re-uploaded after tagging) and 36.0-rc2 disables the
prebuilt toolchains outright behind a `-dev` version guard. Until a stable
36.x fixes it, we pin the official release binaries ourselves, same pattern
as tools/dfu and tools/openocd.

Keep _TAG in lockstep with the protobuf bazel_dep in MODULE.bazel when
bumping, and refresh _SHAS from the release artifacts.
"""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

_TAG = "v36.0-rc1"

# Artifact naming turns the tag's "rc1" into "rc-1".
_ARTIFACT_VERSION = "36.0-rc-1"

_SHAS = {
    "linux-aarch_64": "27f570d90141a7b0fd2657a2752b536a430cc829c4d7f50df27c93e2ee053941",
    "linux-x86_64": "10e0e691050217a35d65ab993caeb11b2060a463f2dbb46416c589be97ca1b93",
    "osx-aarch_64": "395787dbb6bf99511a994553968e12148bd123ac96542885aaeeb627331acee5",
    "osx-x86_64": "076bc376d895066cd96da04bf3931f18bb58654fd5cdb505b3eb9e2c77fa576d",
    "win64": "77c0fa6a500a26615d7a587f824dd23b571630ec46b03f18d633b94479fcfe85",
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
