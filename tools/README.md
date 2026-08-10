# Tools

Build and dev tooling. Each package documents itself:

| Package | What it is |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| [firmware/](firmware/README.md) | `firmware_project` — the macro that turns board sources into flashable images — and build provenance. |
| [openocd/](openocd/README.md) | Hermetic OpenOCD + the `bazel run …:openocd` flash flow (ST-Link). |
| [dfu/](dfu/README.md) | Hermetic dfu-util + the `bazel run …:dfu` flash flow (USB only, no probe). |
| [format/](format/README.md) | clang-format for the whole repo: `bazel run //tools/format` (or `:check`). |

`workspace_status.sh` / `workspace_status.bat` are the Bazel workspace-status
scripts (wired up in `.bazelrc`). They emit the `STABLE_GIT_*` keys that
[firmware/](firmware/README.md) stamps into every firmware binary — when
changing the emitted keys, keep both scripts and the consumer
(`firmware/gen_build_info.py`) in sync.
