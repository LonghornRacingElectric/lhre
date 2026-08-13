"""Runs the C++ generator over the spec files.

A genrule would need bash (its `cmd` doesn't run on Windows clients);
this rule runs the py_binary directly, same pattern as
tools/firmware/build_info.bzl. The macro predeclares one .hpp/.cpp pair
per board (plus the shared types pair and the umbrella header) so
cc_library targets can reference the files by name.
"""

def _can_lib_sources_impl(ctx):
    args = ctx.actions.args()
    args.add("--out-dir", ctx.outputs.outs[0].dirname)
    args.add("--boards", ",".join(ctx.attr.boards))
    args.add_all(ctx.files.spec)
    ctx.actions.run(
        executable = ctx.executable._generator,
        arguments = [args],
        inputs = ctx.files.spec,
        outputs = ctx.outputs.outs,
        mnemonic = "CanLibGen",
        progress_message = "Generating CAN library from spec",
    )
    return [DefaultInfo(files = depset(ctx.outputs.outs))]

_can_lib_sources = rule(
    implementation = _can_lib_sources_impl,
    attrs = {
        "boards": attr.string_list(mandatory = True),
        "outs": attr.output_list(mandatory = True),
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

def can_lib_sources(name, spec, boards):
    """Generates lhre_can_types.{hpp,cpp}, lhre_can_<board>.{hpp,cpp} per
    board, and the lhre_can.hpp umbrella. `boards` must match the
    lib/spec/messages/*.textproto file stems — the generator errors with
    instructions if it drifts."""
    outs = ["lhre_can.hpp", "lhre_can_types.hpp", "lhre_can_types.cpp"]
    for board in boards:
        outs += ["lhre_can_{}.hpp".format(board), "lhre_can_{}.cpp".format(board)]
    _can_lib_sources(
        name = name,
        spec = spec,
        boards = boards,
        outs = outs,
    )
