# Tools

Build and dev tooling. Each package documents itself:

| Package | What it is |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| [firmware/](firmware/README.md) | `firmware_project` — the macro that turns board sources into flashable images — and build provenance. |
| [openocd/](openocd/README.md) | Hermetic OpenOCD + the `bazel run …:openocd` flash flow (ST-Link). |
| [dfu/](dfu/README.md) | Hermetic dfu-util + the `bazel run …:dfu` flash flow (USB only, no probe). |
| [format/](format/README.md) | clang-format for the whole repo: `bazel run //tools/format` (or `:check`). |
| [monitor/](monitor/README.md) | Serial console for the longhorn debug shell: `bazel run //tools/monitor` connects, checks the flashed sha against the checkout, and goes interactive. |
| [ide/](ide/README.md) | `compile_commands.json` regeneration for clangd/IDEs: `bazel run //:refresh_ide` (or the fast `//:refresh_compile_commands`). |

`new_board.py` (`bazel run //tools:new_board -- boards/<Name>/<Name>.ioc`)
scaffolds a whole working board from its CubeMX `.ioc`, so new boards
start from the convention instead of a copy of VCU:

- the minimal `firmware_project` call in `BUILD.bazel` (MCU and middleware
  read from the `.ioc`);
- compiling starter files for everything the macro synthesizes targets
  from — app library, host test, host sim (`App/`, templates in
  `new_board_templates.py`) and the firmware entry point (`Board/main.cpp`)
  — a scaffolded board blinks, host-tests, and simulates before any code
  is written; existing files are never overwritten, and `--no-test` /
  `--no-sim` skip those starters for boards that won't maintain them;
- the board's VS Code debugging (see [debug/](debug/README.md)): the SVD
  pin in `tools/debug/svd_lock.bzl` plus Cortex-Debug launch configs and
  build/flash tasks in `.vscode/`. `--vscode-only` redoes just this part
  for an existing board.

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
