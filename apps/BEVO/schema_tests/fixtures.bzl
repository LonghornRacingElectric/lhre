"""Run both fixture versions through the production telemetry artifact rule."""

load("//lib/codegen/telemetry:telemetry_artifact.bzl", "telemetry_artifact")

def schema_fixture(name, spec):
    for kind, filename in {
        "proto": "can_packets.proto",
        "json": "can.json",
        "sql": "orion_db_init.sql",
        "orm": "orion_models.py",
        "prisma": "orion.prisma",
        "contract": "schema_contract.json",
    }.items():
        telemetry_artifact(
            name = name + "_" + kind,
            testonly = True,
            spec = spec,
            kind = kind,
            out = name + "/" + filename,
            common_sql = "//apps/telemetry/stack/ingest:common_schema.sql" if kind in ("sql", "prisma") else None,
        )
    native.genrule(
        name = name + "_descriptor",
        testonly = True,
        srcs = [":" + name + "_proto"],
        outs = [name + "/schema_descriptor.pb"],
        cmd = "$(location //tools/protoc) --descriptor_set_out=$@ --proto_path=$$(dirname $(location :%s_proto)) $(location :%s_proto)" % (name, name),
        tools = ["//tools/protoc"],
    )
    native.filegroup(
        name = name + "_artifacts",
        testonly = True,
        srcs = [":" + name + "_" + kind for kind in ["proto", "json", "sql", "orm", "prisma", "contract", "descriptor"]],
    )
