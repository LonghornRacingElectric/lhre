"""Stamped generation of the build-provenance header (lhre/build_info.hpp).

A genrule can't do this portably: its `cmd` needs bash, which Windows clients
don't have (that's why elf_out has a cmd_bat variant), and stable-status.txt
can't be wrangled from cmd_bat without shell parsing. This rule instead runs
the gen_build_info py_binary directly — no shell — so it behaves identically
on Linux/macOS/Windows clients and on the remote Linux executors.

The header depends only on stable-status.txt (not volatile-status.txt), so it
regenerates exactly when the STABLE_GIT_* keys change — new commit, new tag,
or a clean/dirty transition — never on timestamps.
"""

def _build_info_header_impl(ctx):
    out = ctx.actions.declare_file(ctx.attr.out)
    ctx.actions.run(
        executable = ctx.executable._generator,
        arguments = [ctx.info_file.path, out.path],
        inputs = [ctx.info_file],
        outputs = [out],
        mnemonic = "BuildInfoHeader",
        progress_message = "Stamping %{output}",
    )
    return [DefaultInfo(files = depset([out]))]

build_info_header = rule(
    implementation = _build_info_header_impl,
    doc = "Generates a C++ header from the stable workspace status file.",
    attrs = {
        "out": attr.string(
            mandatory = True,
            doc = "Package-relative path of the generated header.",
        ),
        "_generator": attr.label(
            default = "//tools/firmware:gen_build_info",
            executable = True,
            cfg = "exec",
        ),
    },
)
