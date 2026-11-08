"""Hermetic telemetry artifacts generated from the CAN spec."""


def _telemetry_artifact_impl(ctx):
    args = ctx.actions.args()
    args.add("--kind", ctx.attr.kind)
    args.add("--out", ctx.outputs.out)
    if ctx.file.common_sql:
        args.add("--common-sql", ctx.file.common_sql)
    args.add_all(ctx.files.spec)
    inputs = list(ctx.files.spec)
    if ctx.file.common_sql:
        inputs.append(ctx.file.common_sql)
    ctx.actions.run(
        executable = ctx.executable._generator,
        arguments = [args],
        inputs = inputs,
        outputs = [ctx.outputs.out],
        mnemonic = "CanTelemetryGen",
        progress_message = "Generating telemetry %s from CAN spec" % ctx.attr.kind,
    )
    return [DefaultInfo(files = depset([ctx.outputs.out]))]


telemetry_artifact = rule(
    implementation = _telemetry_artifact_impl,
    attrs = {
        "common_sql": attr.label(allow_single_file = [".sql"]),
        "kind": attr.string(mandatory = True, values = ["json", "proto", "sql", "contract", "orm", "prisma"]),
        "out": attr.output(mandatory = True),
        "spec": attr.label(
            mandatory = True,
            allow_files = [".textproto"],
        ),
        "_generator": attr.label(
            default = "//lib/codegen/telemetry:gen_telemetry",
            executable = True,
            cfg = "exec",
        ),
    },
)
