# Tools

Build and dev tooling. Each package documents itself:

| Package | What it is |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| [firmware/](firmware/README.md) | `firmware_project` — the macro that turns board sources into flashable images — and build provenance. |
| [openocd/](openocd/README.md) | Hermetic OpenOCD + the `bazel run …:openocd` flash flow (ST-Link). |
| [dfu/](dfu/README.md) | Hermetic dfu-util + the `bazel run …:dfu` flash flow (USB only, no probe). |
| [format/](format/README.md) | clang-format for the whole repo: `bazel run //tools/format` (or `:check`). |
| [ide/](ide/README.md) | `compile_commands.json` regeneration for clangd/IDEs: `bazel run //:refresh_ide` (or the fast `//:refresh_compile_commands`). |

`new_board.py` (`bazel run //tools:new_board -- boards/<Name>/<Name>.ioc`)
scaffolds a new board's `BUILD.bazel` from its CubeMX `.ioc` — it reads the
MCU and enabled middleware and emits the minimal `firmware_project` call,
so new boards start from the convention instead of a copy of VCU. It also
sets up the board's VS Code debugging (see [debug/](debug/README.md)):
pins the device's SVD in `tools/debug/svd_lock.bzl` for Bazel to fetch and
adds the board's Cortex-Debug launch configs and build/flash tasks to
`.vscode/`. `--vscode-only` redoes just that part for an existing board.

When adding a hermetic tool repo (the openocd/, dfu/, debug/ pattern —
a module extension that fetches a pinned binary or file set): pin every
download (sha256 or git commit) and end the extension implementation with
`return ctx.extension_metadata(reproducible = True)`. Otherwise Bazel
records the extension in `MODULE.bazel.lock`, and OS-dependent results make
every platform rewrite the lock on every build — see "Module extensions
stay out of the lockfile" in [docs/build-system.md](../docs/build-system.md).

`workspace_status.sh` / `workspace_status.bat` are the Bazel workspace-status
scripts (wired up in `.bazelrc`). They emit the `STABLE_GIT_*` keys that
[firmware/](firmware/README.md) stamps into every firmware binary — when
changing the emitted keys, keep both scripts and the consumer
(`firmware/gen_build_info.py`) in sync.
