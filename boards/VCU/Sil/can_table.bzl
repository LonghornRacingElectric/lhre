"""Runs the SIL's CAN decode-table generator over the spec.

Same no-shell pattern as lib/codegen/cpp/can_lib.bzl: the action runs the
py_binary directly, so it needs no bash.
"""

def _vcu_sil_can_table_impl(ctx):
    out = ctx.actions.declare_file(ctx.attr.out)
    args = ctx.actions.args()
    args.add("--out", out)
    args.add_all(ctx.files.spec)
    ctx.actions.run(
        executable = ctx.executable._generator,
        arguments = [args],
        inputs = ctx.files.spec,
        outputs = [out],
        mnemonic = "SilCanTable",
        progress_message = "Generating the SIL CAN decode table",
    )
    return [DefaultInfo(files = depset([out]))]

vcu_sil_can_table = rule(
    implementation = _vcu_sil_can_table_impl,
    doc = "JSON decode table of every message in the CAN spec, for the SIL host.",
    attrs = {
        "out": attr.string(mandatory = True),
        "spec": attr.label(mandatory = True, allow_files = [".textproto"]),
        "_generator": attr.label(
            default = "//boards/VCU:vcu_sil_gen_can_table",
            executable = True,
            cfg = "exec",
        ),
    },
)
