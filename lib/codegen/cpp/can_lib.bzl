"""Runs the C generator over the spec files.

A genrule would need bash (its `cmd` doesn't run on Windows clients);
this rule runs the py_binary directly, same pattern as
tools/firmware/build_info.bzl.
"""

def _can_lib_sources_impl(ctx):
    args = ctx.actions.args()
    args.add("--header", ctx.outputs.header)
    args.add("--source", ctx.outputs.source)
    args.add_all(ctx.files.spec)
    ctx.actions.run(
        executable = ctx.executable._generator,
        arguments = [args],
        inputs = ctx.files.spec,
        outputs = [ctx.outputs.header, ctx.outputs.source],
        mnemonic = "CanLibGen",
        progress_message = "Generating CAN library from spec",
    )
    return [DefaultInfo(files = depset([ctx.outputs.header, ctx.outputs.source]))]

can_lib_sources = rule(
    implementation = _can_lib_sources_impl,
    doc = "Generates the C++ CAN pack/unpack library from //spec:files.",
    attrs = {
        "header": attr.output(mandatory = True),
        "source": attr.output(mandatory = True),
        "spec": attr.label(
            mandatory = True,
            allow_files = [".textproto"],
        ),
        "_generator": attr.label(
            default = "//lib/codegen/cpp:gen_can_lib",
            executable = True,
            cfg = "exec",
        ),
    },
)
