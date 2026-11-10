"""Compile schema-derived BEVO bindings and tables using declared Bazel inputs."""

load("@rules_rust//cargo:defs.bzl", "cargo_build_script")
load("@rules_rust//rust:defs.bzl", "rust_library")

def sensor_proto_library(name, proto, can_json, contract, policy = None, testonly = False):
    compatible = select({
        "@platforms//os:windows": ["@platforms//:incompatible"],
        "//conditions:default": [],
    })
    policy_env = {"NATIVE_POLICY": "$(execpath %s)" % policy} if policy else {}
    cargo_build_script(
        name = name + "_build",
        testonly = testonly,
        srcs = ["//apps/BEVO:build.rs"],
        build_script_env = dict({
            "CAN_JSON": "$(execpath %s)" % can_json,
            "PROTO_FILE": "$(execpath %s)" % proto,
            "PROTOC": "$(execpath //tools/protoc)",
            "SCHEMA_CONTRACT": "$(execpath %s)" % contract,
        }, **policy_env),
        data = [can_json, proto, contract] + ([policy] if policy else []),
        edition = "2021",
        target_compatible_with = compatible,
        tools = ["//tools/protoc"],
        deps = [
            "@crates//:prost",
            "@crates//:prost-build",
            "@crates//:prost-types",
            "@crates//:serde_json",
        ],
    )
    rust_library(
        name = name,
        testonly = testonly,
        srcs = ["//apps/BEVO:config.rs", "//apps/BEVO:lib.rs", "//apps/BEVO:projection.rs", "//apps/BEVO:native.rs"],
        crate_root = "//apps/BEVO:lib.rs",
        edition = "2021",
        target_compatible_with = compatible,
        deps = [
            ":" + name + "_build",
            "@crates//:prost",
            "@crates//:serde",
            "@crates//:serde_json",
        ],
    )
