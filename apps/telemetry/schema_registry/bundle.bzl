"""Bind CI provenance to generated release artifacts as explicit action inputs."""

def _bundle(ctx):
    args = ctx.actions.args()
    args.add("--out", ctx.outputs.out)
    args.add("--revision", ctx.var.get("TELEMETRY_REVISION", "local"))
    args.add("--branch", ctx.var.get("TELEMETRY_BRANCH", "vms/telemetry-pdr"))
    args.add("--image", ctx.file.image.path)
    for target, name in ctx.attr.assets.items():
        args.add("--asset", name + "=" + target.files.to_list()[0].path)
    ctx.actions.run(
        executable = ctx.executable._tool,
        arguments = [args],
        inputs = depset(ctx.files.assets + ctx.files.image),
        outputs = [ctx.outputs.out],
    )

release_bundle = rule(
    implementation = _bundle,
    attrs = {
        "assets": attr.label_keyed_string_dict(allow_files = True),
        "image": attr.label(allow_single_file = True),
        "out": attr.output(mandatory = True),
        "_tool": attr.label(default = ":bundle_tool", executable = True, cfg = "exec"),
    },
)
