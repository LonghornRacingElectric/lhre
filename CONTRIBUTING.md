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
3. Scaffold the `BUILD.bazel` from the `.ioc` —
   `bazel run //tools:new_board -- boards/<Name>/<Name>.ioc` — and follow
   its printed next steps (app library, `//boards:all_firmware`, README).
   The macro derives the device define and finds the linker script and
   startup file by ST's naming convention; just keep the chip's `.ld` and
   `startup_*.s` at the board root under their ST names.
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
