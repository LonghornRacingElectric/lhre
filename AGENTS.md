# Agent instructions

Instructions for AI coding agents working in this repo. Humans should read
[README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md) first; agents
should too.

## Documentation is part of every change

Docs live **next to the code** — every package directory with non-trivial
code has a `README.md`, and CI publishes the whole tree as a site (MkDocs,
see `mkdocs.yml`). Treat docs as a required output of your work, not an
afterthought:

- **If you change behavior, update the adjacent `README.md` in the same
  change.** That includes new/renamed Bazel targets, new macro options,
  changed flags or defaults, new conventions, and changed workflows.
- **If you create a directory with code in it, create its `README.md`**:
  what it is, why it exists, how to use its targets, and any gotchas.
  Follow the existing voice — terse, explains *why*, doesn't narrate code
  line-by-line. `drivers/lhal/README.md` and `tools/firmware/README.md` are
  the style reference.
- **If you find undocumented or wrongly documented code, fix the docs** as
  part of whatever you're doing there.
- Verify docs before finishing: `uv run --group docs mkdocs build --strict`
  must pass. Broken links between `.md` files fail the build.
- Linking rules: between markdown files use relative links
  (`../tools/firmware/README.md`). To *code or config files* use full
  GitHub URLs (`https://github.com/LonghornRacingElectric/lhre/blob/main/...`)
  — relative links to non-markdown files 404 on the published site.
- Docs can also be embedded in source files: a comment block opening with
  `/** md` (until `**/`) or `// md` (until `// end md`) is extracted as its
  own page. Prefer the adjacent README for anything structural; use
  embedded blocks for docs that would go stale if separated from the code.

## Build, test, verify

```bash
bazel test //...                        # build + test everything (remote by default)
bazel test --config=local //...         # same, entirely on this machine
bazel run //tools/format                # clang-format everything (CI enforces :check)
uv run --group docs mkdocs build --strict   # docs must build cleanly
```

Firmware for a board: `bazel build //boards/VCU:vcu`. Flashing targets
(`:openocd`, `:dfu`) need hardware attached — don't run them.

## Repo rules that bite

- `boards/*/Core/` is CubeMX-generated. Never hand-edit, format, or "fix"
  anything in it; changes go through the board's `.ioc` + regeneration
  (see [CONTRIBUTING.md](CONTRIBUTING.md#regenerating-cubemx-code-safely)).
- Application code depends on `//drivers/lhal` interfaces only — never on
  ST HAL directly. That's what keeps it host-testable.
- Comments explain *why*, not *what*. Match the surrounding density.
- `main` is always the current car; no per-year directories
  (see [CONTRIBUTING.md](CONTRIBUTING.md#season-policy)).
