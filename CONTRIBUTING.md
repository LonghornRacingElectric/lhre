# Contributing

## Workflow

- Branch from `main`: `<your-name>/<short-description>` (e.g.
  `dhairya/vcu-can-heartbeat`).
- Open a PR; every change needs a review and a green presubmit before merge.
  No direct pushes to `main`.
- Presubmit runs `bazel test //...` (builds all firmware, runs host tests)
  and `bazel run //tools/format:check`. Run both locally before pushing.

## Formatting

```bash
bazel run //tools/format
```

rewrites every tracked C/C++ file with the hermetic clang-format. Generated
code under `boards/*/Core/` is excluded — never hand-format (or hand-edit) it.

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

Copy the layout of [boards/VCU](boards/VCU):

1. Create the CubeMX project (`<name>.ioc`) at the board root with
   **"Generate peripheral initialization as a pair of .c/.h files per
   peripheral"** enabled, targeting a Makefile toolchain. Generated code
   lands in `Core/`.
2. Hand-written bring-up (`main.cpp`, clock config, LHAL adapter wiring)
   goes in `Board/`. Application logic goes in `App/` as a plain
   `cc_library` depending only on `//drivers/lhal`, so it runs in host
   tests and sims too (the per-family platform in `//platforms` selects a
   toolchain with the MCU flags baked in when cross-compiling).
3. Add a `BUILD.bazel` calling `firmware_project` (see
   [boards/VCU/BUILD.bazel](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/BUILD.bazel)) plus the linker script
   and startup file for your chip.
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
