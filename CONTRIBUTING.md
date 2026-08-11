# Contributing

## Workflow

- Branch from `main`: `<your-name>/<short-description>` (e.g.
  `dhairya/vcu-can-heartbeat`).
- Open a PR; every change needs a review and a green presubmit before merge.
  No direct pushes to `main`.
- Presubmit runs `bazel test //...` (builds all firmware, runs host tests)
  and `bazel run //tools/format:check`. Run both locally before pushing.

## Formatting

clang-formatted code is a **requirement** for every push and PR — presubmit
runs `bazel run //tools/format:check` and fails on any unformatted file.
Before pushing, run:

```bash
bazel run //tools/format
```

which rewrites every tracked C/C++ file with the hermetic clang-format.
Generated code under `boards/*/Core/` is excluded — never hand-format (or
hand-edit) it.

## IDE setup (fixing red squiggles)

C++ code intelligence (clangd, VS Code, CLion) is driven by the checked-out
`compile_commands.json`. If highlighting is broken — red squiggles, errors
like "Unknown type name 'uint32_t'", missing headers — regenerate it:

```bash
bazel run //:refresh_ide
```

This is the reliable option: it builds `//...` first so every generated
header the compile commands reference actually exists on disk, then extracts.
It's slow. When you know the build outputs are already present (you've been
building locally) there's a fast path that only re-extracts, without building
intermediate files:

```bash
bazel run //:refresh_compile_commands
```

See [tools/ide](tools/ide/README.md) for what each one does and why.

## Debugging on hardware (VS Code)

Breakpoints, stepping, live variable watch, FreeRTOS thread views, and the
peripheral-register viewer — over an ST-Link, from VS Code, on any OS:

1. Open the repo in VS Code and install the recommended **Cortex-Debug**
   extension when prompted (it pulls in the RTOS/peripheral/memory viewers).
2. Plug in the board over ST-Link.
3. Run and Debug panel → **Debug \<board\> (ST-Link)** → F5.

That builds the firmware, flashes it, resets the MCU, and stops at
`main()`. The **Attach** variant inspects a board that's already running,
without reflashing or resetting it.

There is nothing else to install: the gdb client, the OpenOCD gdb server,
and the SVD register definitions are all fetched hermetically by Bazel and
staged under `bazel-bin/` before every launch. How that works — and
troubleshooting (Linux udev rules, dangling paths) — is in
[tools/debug](tools/debug/README.md). The launch configs and tasks in
`.vscode/` are generated per board by the `new_board` scaffolder (see
[Adding a new board](#adding-a-new-board)); don't hand-edit the generated
entries — rerun the scaffolder with `--vscode-only` instead.

To flash without starting a debug session:

```bash
bazel run //boards/VCU:openocd
```

## Documentation

Docs live **next to the code they describe**: drop a `README.md` (or any
`.md`) in the directory it documents. CI builds the whole tree into a site
with MkDocs ([mkdocs.yml](https://github.com/LonghornRacingElectric/lhre/blob/main/mkdocs.yml)) and publishes it to GitHub Pages on
every merge to `main`; `README.md` renders as that directory's index page.

You can also embed docs in source files — a comment block starting with
`/** md` (until `**/`) or lines starting with `// md` (until `// end md`)
is extracted as a page next to the file.

Preview locally:

```bash
uv run --group docs mkdocs serve
```

PRs that touch docs run `mkdocs build --strict`, so broken links between
pages fail presubmit. Links to *code* files (BUILD files, configs) don't
exist on the site — use a full GitHub URL for those.

## Adding a new board

Copy the layout of [boards/VCU](boards/VCU/README.md):

1. Create the CubeMX project (`<name>.ioc`) at the board root with
   **"Generate peripheral initialization as a pair of .c/.h files per
   peripheral"** enabled, targeting a Makefile toolchain. Generated code
   lands in `Core/`.
2. Hand-written bring-up (`main.cpp`, clock config, LHAL adapter wiring)
   goes in `Board/`. Application logic goes in `App/`, depending only on
   `//drivers/lhal`, so it runs in host tests and sims too —
   `firmware_project` turns `App/` files into the app library, tests, and
   sims by naming convention (see
   [tools/firmware](tools/firmware/README.md)).
3. Scaffold the `BUILD.bazel` from the `.ioc`:

   ```bash
   bazel run //tools:new_board -- boards/<Name>/<Name>.ioc
   ```

   (The `--` matters: it separates Bazel's own options from the script's
   arguments. `--force` overwrites an existing `BUILD.bazel`;
   `--vscode-only` regenerates just the debug setup for an existing
   board.) Follow its printed next steps (app library,
   `//boards:all_firmware`, README). The macro derives the device define
   and finds the linker script and startup file by ST's naming
   convention; just keep the chip's `.ld` and `startup_*.s` at the board
   root under their ST names. The scaffolder also sets up
   [VS Code debugging](#debugging-on-hardware-vs-code) for the board: it
   pins the device's SVD in `tools/debug/svd_lock.bzl` (needs network
   once) and adds the board's launch configs and build/flash tasks to
   `.vscode/`.
4. Add a `post_cubemx.sh` like the VCU's.

## Regenerating CubeMX code safely

`Core/` is 100% CubeMX-owned; `Board/` is 100% ours. That split is what makes
"Generate Code" safe:

1. Edit the `.ioc` in CubeMX and generate.
2. Run the board's `post_cubemx.sh` (deletes the generated `main.c`; our
   entry point is `Board/main.cpp`).
3. If you changed the clock tree in CubeMX, mirror it by hand in
   `Board/main.cpp` (`ConfigureSystemClock()`) — clock config is the one
   thing owned in both places.
4. Build and diff: only `Core/` files should have changed.

## Season policy

`main` is always the current car. Years live in git, not in directory names:

- Tag milestones: `season/2027/comp-michigan`, `season/2027/final`.
- When work on the next car starts, cut a `season/<year>` maintenance branch
  from the final tag. Fixes for the running old car land there and are
  cherry-picked to `main` if still relevant.
- When hardware is scrapped, delete its `boards/` directory on `main` —
  history and the season branch keep it.
