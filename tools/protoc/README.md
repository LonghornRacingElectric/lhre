# tools/protoc

`//tools/protoc` is the official prebuilt `protoc` binary, select()ed per
exec platform. Use it wherever a rule needs protoc as a plain tool — e.g.
`cargo_build_script`'s `PROTOC` env for prost codegen in
[apps/BEVO](../../apps/BEVO/README.md).

Why not `@protobuf//:protoc`? That label builds the compiler from source,
which drags all of abseil into the build (~800 C++ actions of pure wait).
And protobuf's own prebuilt-protoc machinery (the
`prefer_prebuilt_protoc` flag in `.bazelrc`) is broken in every protobuf
version on the BCR today — stale artifact hashes in 36.0-rc1, disabled
outright in 36.0-rc2 — so
[protoc.bzl](https://github.com/LonghornRacingElectric/lhre/blob/main/tools/protoc/protoc.bzl)
pins the release binaries ourselves, same pattern as `tools/dfu` and
`tools/openocd`. When bumping the `protobuf` bazel_dep, bump the tag and
hashes there in the same change.
